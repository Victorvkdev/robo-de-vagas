"""Envio automático (janela de veto): o Worker marca às 11h as vagas 🤖 não puladas como 'enviando' e dispara
o workflow envio. Aqui: CV adaptado (PDF) -> formulário do ATS -> comprovante -> D1, R2 e Telegram.
Modos: real (envia) | ensaio (preenche e tira print, não envia; a vaga volta para o pacote) | cv (só gera o CV)."""
import gzip
import html
import json
import os
import time

import requests

from . import ats, cv, pipeline
from .perfil import Perfil

SRC_NAME = {"greenhouse": "Company website", "lever": "Company website", "ashby": "Company website",
            "workable": "Company website", "smartrecruiters": "Company website", "recruitee": "Company website"}
COLS = ("id, title, company, url, description, area, level, lang, source, apply_type, fit, status, list_pos, contact, "
        "list_date")


def tg_file(path, caption, mime, keyboard=None, name=None):
    tok, chat = os.environ.get("TELEGRAM_BOT_TOKEN"), os.environ.get("TELEGRAM_CHAT_ID")
    if not tok or not chat or not path or not os.path.exists(path):
        return False
    data = {"chat_id": chat, "caption": caption[:1024], "parse_mode": "HTML"}
    if keyboard:
        data["reply_markup"] = json.dumps({"inline_keyboard": keyboard})
    with open(path, "rb") as f:
        try:
            r = requests.post("https://api.telegram.org/bot%s/sendDocument" % tok, data=data, timeout=60,
                              files={"document": (name or os.path.basename(path), f, mime)})
            return r.ok
        except requests.RequestException:
            return False


def day_start(date):
    """Meia-noite de Fortaleza (UTC-3 fixo) em epoch."""
    import datetime as dt
    return dt.datetime.strptime(date, "%Y-%m-%d").replace(tzinfo=dt.timezone(dt.timedelta(hours=-3))).timestamp()


def load_perfil(db):
    body = (db.query("SELECT body FROM profile WHERE key='super_contexto'") or [{}])[0].get("body") or ""
    return Perfil(body)


def cover_pdf(resp, path, browser):
    txt = resp.cover()
    if not txt:
        return None
    doc = ("<!DOCTYPE html><html><head><meta charset=utf-8><style>@page{size:A4;margin:22mm}body{font-family:Arial;"
           "font-size:11pt;line-height:1.5}</style></head><body>%s<p>Victor Cavalcante</p></body></html>"
           % "".join("<p>%s</p>" % html.escape(p) for p in txt.split("\n\n")))
    return cv.pdf(doc, path, browser)


def _esc(s):
    return html.escape(str(s or ""))


def run(db, gem=None, mode="real", limit=10, r2=None, now=None, job_ids=None, launch=None, notify=True):
    now = now or time.time()
    today = pipeline.local_date(now)
    perfil = load_perfil(db)
    if not perfil.ok():
        raise RuntimeError("super contexto não está no D1 (rode ./scripts/contexto_sonnet.sh)")
    if job_ids:
        jobs = db.query("SELECT %s FROM jobs WHERE id IN (%s)" % (COLS, ",".join("?" * len(job_ids))), list(job_ids))
    else:
        jobs = db.query("SELECT %s FROM jobs WHERE status='enviando' ORDER BY fit DESC LIMIT ?" % COLS, [limit * 2])
    sent_today = (db.query("SELECT COUNT(*) AS n FROM applications WHERE channel='auto' AND ts >= ?",
                           [day_start(today)])
                  or [{"n": 0}])[0]["n"] or 0
    learned = [(r["q"], r["a"]) for r in db.query("SELECT q, a FROM learned ORDER BY id")]
    out = dict(modo=mode, vagas=len(jobs), enviadas=0, conferir=0, ensaio=0, kit=0, cv=0, motivos=[])
    if not jobs:
        return out
    if launch is None:
        from playwright.sync_api import sync_playwright
        pw = sync_playwright().start()
        browser = pw.chromium.launch()
    else:
        pw, browser = None, launch()
    tmp = os.environ.get("RUNNER_TEMP") or "/tmp"
    try:
        for j in jobs:
            if mode == "real" and sent_today >= limit:
                db.query("UPDATE jobs SET status='aprovada', status_ts=? WHERE id=? AND status='enviando'", [now, j["id"]])
                out["motivos"].append("%s: teto de %d envios automáticos/dia" % (j["company"], limit))
                continue
            plan = cv.select(perfil, j, gem)
            lang = plan["lang"]
            cv_path = os.path.join(tmp, cv.file_name(j, lang))
            cv.pdf(cv.render_html(perfil, plan, j), cv_path, browser)
            cv_key = r2.put_file("cv/%s/%s.pdf" % (today, j["id"]), cv_path, "application/pdf") if r2 else None
            head = "<b>%s</b> — %s" % (_esc(j["title"]), _esc(j["company"]))
            if mode == "cv":
                out["cv"] += 1
                if notify:
                    tg_file(cv_path, "📄 CV adaptado: " + head, "application/pdf")
                continue
            from .respostas import Respostas
            cover = cover_pdf(Respostas(perfil, j), os.path.join(tmp, "cover_%s.pdf" % j["id"]), browser)
            shot = os.path.join(tmp, "comprovante_%s.png" % j["id"])
            res = ats.run(browser, j, perfil, cv_path, mode=mode, cover_path=cover, learned=learned, gem=gem,
                          shot_path=shot, source_name=SRC_NAME.get(j.get("source") or "", ""))
            st = res["status"]
            ded = [(a, b) for a, b, c in res["answers"] if c == "deduzida"]
            ded_txt = ("\n🧠 Deduzi (confira; corrija com /aprendi):" + "".join(
                "\n• %s → %s" % (_esc(a[:70]), _esc(b[:60])) for a, b in ded[:8])) if ded else ""
            proof_key = (r2.put_file("proof/%s/%s.png" % (today, j["id"]), res["shot"], "image/png")
                         if (r2 and res.get("shot") and st in ("enviada", "conferir")) else None)
            answers = json.dumps([dict(campo=a, resposta=b, origem=c) for a, b, c in res["answers"]], ensure_ascii=False)
            if st in ("enviada", "conferir"):
                sent_today += 1
                out["enviadas" if st == "enviada" else "conferir"] += 1
                db.query("UPDATE jobs SET status='enviada', status_ts=? WHERE id=?", [now, j["id"]])
                db.query("INSERT INTO applications (job_id, ts, channel, via, cv_key, answers, proof_key, state, state_ts, "
                         "origin, notes) VALUES (?, ?, 'auto', ?, ?, ?, ?, 'enviada', ?, 'v3', ?)",
                         [j["id"], now, "contato" if j.get("contact") else "cold", cv_key, answers, proof_key, now,
                          "" if st == "enviada" else "sem_confirmacao"])
                if notify:
                    kb = None
                    cap = "🤖 <b>Enviada sozinha</b>: %s\n<a href=\"%s\">vaga</a> · CV adaptado abaixo" % (head, _esc(j["url"]))
                    if st == "conferir":
                        cap = ("🤖❓ <b>Cliquei em enviar e não vi confirmação</b>: %s\nConfira o print. Se não saiu, toque abaixo "
                               "que ela volta para o pacote." % head)
                        kb = [[{"text": "↩️ Não saiu: vai pro pacote", "callback_data": "nr:%s" % j["id"]}]]
                    tg_file(res["shot"], cap + ded_txt, "image/png", kb, "comprovante_%s.png" % (j["company"] or "vaga")[:30])
                    tg_file(cv_path, "📄 CV enviado: " + head, "application/pdf")
            else:
                out["ensaio" if st == "ensaio" else "kit"] += 1
                note = ("auto: " + res["reason"])[:300]
                # teste manual (--job) em ensaio não mexe no status da vaga
                if not (job_ids and st == "ensaio"):
                    db.query("UPDATE jobs SET status='aprovada', status_ts=?, notes=? WHERE id=? AND status IN "
                             "('enviando','listada','aprovada')", [now, note, j["id"]])
                if st == "kit":
                    out["motivos"].append("%s: %s" % (j["company"], res["reason"]))
                if notify:
                    if st == "ensaio":
                        cap = ("🧪 <b>Ensaio</b> (não enviei): %s\nPreenchi assim. Se estiver certo, mande <b>/envio real</b>. "
                               "A vaga continua no pacote." % head)
                    else:
                        qs = "".join("\n• %s" % _esc(q[:120]) for q in res.get("questions", [])[:6])
                        cap = ("🧰 <b>Não deu para enviar sozinha</b>: %s\nMotivo: %s%s\nFoi para o pacote do Sonnet."
                               % (head, _esc(res["reason"]), qs))
                        if res.get("questions"):
                            cap += "\nEnsine com /aprendi pergunta → resposta para a próxima ir sozinha."
                    if res.get("shot"):
                        tg_file(res["shot"], cap + ded_txt, "image/png", None, "formulario_%s.png" % (j["company"] or "vaga")[:30])
                    else:
                        tg_file(cv_path, cap, "application/pdf")
    finally:
        browser.close()
        if pw:
            pw.stop()
    return out


# ---------------------------------------------------------------- backup semanal do D1 no R2
def backup(db, r2, keep=8, now=None):
    now = now or time.time()
    tables = [r["name"] for r in db.query("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE '\\_%' "
                                           "ESCAPE '\\' AND name NOT LIKE 'sqlite%' AND name NOT LIKE 'd1_%'")]
    dump, total = {}, 0
    for t in tables:
        rows, off = [], 0
        while True:
            page = db.query('SELECT * FROM "%s" LIMIT 500 OFFSET %d' % (t, off))
            rows += page
            off += len(page)
            if len(page) < 500:
                break
        dump[t] = rows
        total += len(rows)
    data = gzip.compress(json.dumps(dict(ts=now, tables=dump), ensure_ascii=False, default=str).encode("utf-8"))
    key = "backup/d1_%s.json.gz" % pipeline.local_date(now)
    ok = r2.put(key, data, "application/gzip")
    removed = 0
    if ok:
        olds = sorted(k for k in r2.list("backup/") if k.startswith("backup/d1_"))
        for k in olds[:-keep]:
            removed += 1 if r2.delete(k) else 0
    return dict(key=ok, tabelas=len(tables), linhas=total, mb=round(len(data) / 1e6, 2), removidos=removed,
                erro=(r2.errors[-1] if r2.errors else None))
