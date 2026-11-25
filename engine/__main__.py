"""CLI do motor.

  python -m engine busca                 coleta, grava no D1 e monta a lista do dia
  python -m engine busca --dry-run       só coleta e pontua (sem D1, sem segredos); mostra contagens
  python -m engine busca --no-collect    só (re)monta a lista com o que já está no banco
  python -m engine busca --force-list    refaz a lista de hoje (e o Worker manda de novo)
  python -m engine saude                 resumo da semana + saúde das fontes no Telegram
  python -m engine email                 lê o Zoho, classifica, avisa, rascunha, follow-up e envia o aprovado
  python -m engine email --modo enviar   só envia os rascunhos aprovados no Telegram
  python -m engine email --modo teste    só confere o login IMAP/SMTP
  --local caminho.db                     usa um SQLite local em vez do D1
"""
import argparse
import html
import json
import os
import sys
import time

import requests
import yaml

from . import ai, mail, notify, pipeline, score, sources
from .d1 import D1, LocalDB

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load_cfg():
    with open(os.path.join(os.path.dirname(__file__), "config.yaml"), encoding="utf-8") as f:
        return yaml.safe_load(f)


def get_db(local):
    if local:
        return LocalDB(local, os.path.join(ROOT, "migrations") if not os.path.exists(local) else None)
    return D1.from_env()


def tg_send(text):
    tok, chat = os.environ.get("TELEGRAM_BOT_TOKEN"), os.environ.get("TELEGRAM_CHAT_ID")
    if not tok or not chat:
        print("telegram: sem token/chat, mensagem não enviada")
        return
    r = requests.post("https://api.telegram.org/bot%s/sendMessage" % tok, timeout=30,
                      json={"chat_id": chat, "text": text, "parse_mode": "HTML",
                            "link_preview_options": {"is_disabled": True}})
    print("telegram:", "ok" if r.ok else r.status_code)


def cmd_busca(a):
    cfg = load_cfg()
    if a.dry_run:
        now = time.time()
        res = sources.collect(cfg, only=set(a.only.split(",")) if a.only else None)
        rows, by_source = pipeline.score_jobs(res["jobs"], cfg, now, score.DEFAULT_WEIGHTS, {}, {})
        areas = {}
        for r in rows:
            areas[r["area"]] = areas.get(r["area"], 0) + 1
        print(json.dumps(dict(coletadas=res["raw_count"], nas_areas=len(res["jobs"]), aproveitadas=len(rows),
                              por_area=areas, aproveitadas_por_fonte=by_source, por_fonte=res["counts"],
                              erros={k: v for k, v in res["errors"].items()}), ensure_ascii=False, indent=1))
        return 0
    db = get_db(a.local)
    gem = None if a.no_ai else ai.Gemini.from_env()
    s = pipeline.run(db, cfg, do_collect=not a.no_collect, gem=gem, force_list=a.force_list)
    print(json.dumps(s, ensure_ascii=False, indent=1))
    if a.do_collect_check and not a.no_collect and s.get("aproveitadas", 0) == 0:
        tg_send("⚠️ Busca de hoje não achou nenhuma vaga aproveitável. Confira o Actions.")
        return 1
    return 0


def cmd_saude(a):
    db = get_db(a.local)
    now = time.time()
    wk = now - 7 * 86400
    one = lambda sql, p=(): (db.query(sql, list(p)) or [{"n": 0}])[0]["n"] or 0  # noqa: E731
    novas = one("SELECT COUNT(*) AS n FROM jobs WHERE first_seen >= ? AND origin LIKE '%v3%'", [wk])
    listadas = one("SELECT COUNT(*) AS n FROM jobs WHERE list_date >= ?", [pipeline.local_date(wk)])
    aprovadas = one("SELECT COUNT(*) AS n FROM jobs WHERE status IN ('aprovada','precisa_voce') AND status_ts >= ?", [wk])
    enviadas = one("SELECT COUNT(*) AS n FROM applications WHERE ts >= ?", [wk])
    entrevistas = one("SELECT COUNT(*) AS n FROM events WHERE kind='entrevista' AND ts >= ?", [wk])
    recusas = one("SELECT COUNT(*) AS n FROM events WHERE kind='recusa' AND ts >= ?", [wk])
    rascunhos = one("SELECT COUNT(*) AS n FROM outbox WHERE state IN ('aguardando','erro')")
    sem_retorno = one("SELECT COUNT(*) AS n FROM applications WHERE state='sem_retorno' AND state_ts >= ?", [wk])
    proximas = db.query("SELECT COALESCE(e.company, j.company) AS c, e.due_ts AS d FROM events e LEFT JOIN jobs j "
                        "ON j.id = e.job_id WHERE e.kind='entrevista' AND e.due_ts BETWEEN ? AND ? ORDER BY e.due_ts",
                        [now, now + 7 * 86400])
    mortas = db.query("SELECT key FROM sources_health WHERE disabled = 1 ORDER BY key")
    quebradas = db.query("SELECT key, last_error FROM sources_health WHERE disabled = 0 AND last_error IS NOT NULL "
                         "AND instr(key, ':') = 0 ORDER BY key")
    paradas = db.query("SELECT key FROM sources_health WHERE instr(key, ':') = 0 AND last_ok_ts < ? ORDER BY key",
                       [now - 3 * 86400])
    linhas = ["<b>Resumo da semana</b>",
              "Vagas novas: %d · Listadas: %d · Aprovadas: %d · Enviadas: %d · Entrevistas: %d"
              % (novas, listadas, aprovadas, enviadas, entrevistas),
              "Recusas: %d · Sem retorno (21 dias): %d · Rascunhos esperando: %d" % (recusas, sem_retorno, rascunhos)]
    if proximas:
        linhas.append("Entrevistas da semana: " + ", ".join("%s (%s)" % (html.escape(r["c"] or "?"), mail.fmt_local(r["d"]))
                                                           for r in proximas))
    linhas += ["",
              "<b>Saúde das fontes</b>",
              "Boards desligados (404 três vezes): %d" % len(mortas)]
    if quebradas:
        linhas.append("Com erro na última busca: " + ", ".join(r["key"] for r in quebradas))
    if paradas:
        linhas.append("Sem resposta há 3+ dias: " + ", ".join(r["key"] for r in paradas))
    if not quebradas and not paradas:
        linhas.append("Todas as fontes responderam ✅")
    tg_send("\n".join(linhas))
    db.query("INSERT INTO runs(kind, started_ts, finished_ts, ok, summary) VALUES ('saude', ?, ?, 1, ?)",
             [now, time.time(), json.dumps(dict(mortas=len(mortas), quebradas=len(quebradas)))])
    print(json.dumps(dict(mortas=len(mortas), quebradas=len(quebradas), paradas=len(paradas))))
    return 0


def cmd_teste_ia(a):
    """Testa a chave e o modelo do Gemini com um prompt bobo (não toca no banco)."""
    gem = ai.Gemini.from_env()
    if not gem:
        print("teste-ia: GEMINI_API_KEY ausente")
        return 1
    try:
        out = gem.generate('Responda só com o JSON ["ok"]')
        print("teste-ia: ok (%s): %s" % (gem.model, out[:80]))
        return 0
    except Exception as e:  # noqa: BLE001
        print("teste-ia: falhou: %s" % str(e)[:300])
        try:
            r = requests.get(ai.BASE + "/models", params={"key": gem.key}, timeout=30)
            names = [m["name"] for m in r.json().get("models", [])] if r.ok else []
            print("teste-ia: /models HTTP %s, %d modelos; flash: %s" % (
                r.status_code, len(names), ", ".join(n for n in names if "flash" in n)[:400]))
        except Exception as e2:  # noqa: BLE001
            print("teste-ia: /models falhou: %s" % type(e2).__name__)
        return 1


def cmd_email(a):
    if a.modo == "teste":
        try:
            print("email teste:", json.dumps(mail.selftest()))
            return 0
        except Exception as e:  # noqa: BLE001
            print("email teste: falhou:", str(e)[:200])
            return 1
    if not a.local and not (os.environ.get("ZOHO_USER") and os.environ.get("ZOHO_APP_PASSWORD")):
        print("email: secrets do Zoho ainda não configurados (rode ./scripts/entrega4.sh)")
        return 0
    db = get_db(a.local)
    gem = None if a.no_ai else ai.Gemini.from_env()
    t0 = time.time()
    try:
        out = mail.run(db, notify.Telegram.from_env(), gem, mode=a.modo)
    except Exception as e:  # noqa: BLE001
        msg = str(e)[:200]
        print("email: falhou:", msg)
        # avisa no máximo 1x por dia, para uma senha errada não virar 96 mensagens
        today = pipeline.local_date()
        if db.setting("mail_error_day") != today:
            db.set_setting("mail_error_day", today)
            tg_send("⚠️ Leitura do e-mail falhou: %s\nConfira o Actions (workflow email)." % msg)
        return 1
    out["segundos"] = int(time.time() - t0)
    out["d1_gravacoes"] = getattr(db, "rows_written", None)
    print(json.dumps(out, ensure_ascii=False))
    if any(out.get(k) for k in ("emails",)) and (out["emails"].get("lidos") or out["envio"].get("enviados")):
        db.query("INSERT INTO runs(kind, started_ts, finished_ts, ok, summary) VALUES ('email', ?, ?, 1, ?)",
                 [t0, time.time(), json.dumps(out, ensure_ascii=False)])
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(prog="engine")
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("busca")
    b.add_argument("--dry-run", action="store_true")
    b.add_argument("--only", default="", help="fontes separadas por vírgula (só no --dry-run)")
    b.add_argument("--no-collect", action="store_true")
    b.add_argument("--no-ai", action="store_true")
    b.add_argument("--force-list", action="store_true")
    b.add_argument("--local", default=None)
    b.set_defaults(do_collect_check=True)
    s = sub.add_parser("saude")
    s.add_argument("--local", default=None)
    sub.add_parser("teste-ia")
    e = sub.add_parser("email")
    e.add_argument("--modo", default="ciclo", choices=["ciclo", "enviar", "teste"])
    e.add_argument("--no-ai", action="store_true")
    e.add_argument("--local", default=None)
    a = ap.parse_args(argv)
    return {"busca": cmd_busca, "saude": cmd_saude, "teste-ia": cmd_teste_ia, "email": cmd_email}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
