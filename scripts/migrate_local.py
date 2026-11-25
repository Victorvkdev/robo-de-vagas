#!/usr/bin/env python3
"""Gera data/seed.sql com os dados locais (jobhunter + Robô v2) para importar no D1.

Uso:
  python3 scripts/migrate_local.py \
    --jobhunter ~/jobhunter/jobs.db \
    --robo ~/robo-vagas \
    --contexto ~/Documents/VK_JobSearch/05_Contexto_e_Referencia/Super_Contexto.md \
    --out data/seed.sql

O arquivo gerado tem dados pessoais: fica em data/ (ignorado pelo git) e nunca vai pro repo.
"""
import argparse
import csv
import datetime as dt
import json
import os
import re
import sqlite3
import sys
import time

TZ = dt.timezone(dt.timedelta(hours=-3))  # America/Fortaleza

STATUS_JH = {"new": "nova", "expired": "expirada", "dup": "duplicada", "applied": "enviada"}
STATUS_ROBO = {
    "nova": "nova", "listada": "listada", "aprovada": "aprovada", "enviada": "enviada",
    "em análise": "em_analise", "em analise": "em_analise", "teste": "teste", "entrevista": "entrevista",
    "oferta": "oferta", "recusada": "recusada", "pulada": "pulada", "precisa de você": "precisa_voce",
    "expirada": "expirada", "duplicada": "duplicada",
}
APPLIED_STATES = {"enviada", "em_analise", "teste", "entrevista", "oferta", "recusada"}
EVENT_KIND = {"recusada": "recusa", "em análise": "recebida", "entrevista": "entrevista", "teste": "teste"}

AREA_RULES = [
    ("design_eng", r"design engineer|ui engineer|design technologist|creative (developer|technologist)"),
    ("crm", r"\bcrm\b|lifecycle|gamifica|retention|e-?mail (designer|developer|marketing)|braze|smartico"),
    ("suporte_qa", r"\bqa\b|quality assurance|tester|support|suporte|product ops|product operations"),
    ("frontend", r"front.?end|react|vue|angular|web developer|desenvolvedor web|javascript"),
    ("web", r"webflow|wordpress|no.?code|low.?code|framer|landing page"),
    ("uxui", r"\bux\b|\bui\b|user experience|interface|usabilidade|ux/ui|ui/ux"),
    ("product_design", r"product designer|designer de produto|product design"),
]
DESC_KEEP_FULL = {"nova", "listada", "aprovada", "enviada", "em_analise", "teste", "entrevista", "oferta"}

# Pretensão por nível (decisão de 25/11/2026). Fica só no seed local.
PRETENSAO = {
    "junior": {"clt_brl": 3000, "pj_brl": 4000, "usd_mes": 1200, "usd_h": 8},
    "pleno": {"clt_brl": 4500, "pj_brl": 6000, "usd_mes": 2000, "usd_h": 20},
    "senior": {"clt_brl": 6000, "pj_brl": 8000, "usd_mes": 2000, "usd_h": 20},
    "regra": "salario nunca descarta vaga, so avisa",
}


def q(v):
    """Literal SQL seguro para SQLite/D1."""
    if v is None:
        return "NULL"
    if isinstance(v, bool):
        return "1" if v else "0"
    if isinstance(v, (int, float)):
        return repr(v)
    s = str(v).replace("\x00", "")
    return "'" + s.replace("'", "''") + "'"


def ins(table, row, verb="INSERT OR REPLACE"):
    cols = ", ".join(row.keys())
    vals = ", ".join(q(v) for v in row.values())
    return f"{verb} INTO {table} ({cols}) VALUES ({vals});"


def day_ts(s):
    if not s:
        return None
    try:
        d = dt.datetime.strptime(s[:10], "%Y-%m-%d").replace(hour=12, tzinfo=TZ)
        return d.timestamp()
    except ValueError:
        return None


def dtm_ts(s):
    try:
        return dt.datetime.strptime(s, "%Y-%m-%d %H:%M").replace(tzinfo=TZ).timestamp()
    except (ValueError, TypeError):
        return day_ts(s)


def ckey(name):
    return re.sub(r"[^a-z0-9]", "", (name or "").lower())


def area(title):
    t = (title or "").lower()
    for key, rx in AREA_RULES:
        if re.search(rx, t):
            return key
    return None


def trim_desc(desc, status):
    if not desc:
        return desc
    if status in DESC_KEEP_FULL:
        return desc[:20000]
    return desc[:400]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--jobhunter", required=True)
    ap.add_argument("--robo", required=True, help="pasta ~/robo-vagas")
    ap.add_argument("--contexto", default=None)
    ap.add_argument("--out", default="data/seed.sql")
    a = ap.parse_args()

    jh = os.path.expanduser(a.jobhunter)
    robo = os.path.expanduser(a.robo)
    out = []
    now = time.time()
    n = {}

    # ---------- jobhunter ----------
    c = sqlite3.connect(f"file:{jh}?mode=ro", uri=True)
    c.row_factory = sqlite3.Row
    url_to_id = {}
    for r in c.execute("SELECT * FROM jobs"):
        st = STATUS_JH.get(r["status"], "nova")
        url_to_id[(r["url"] or "").split("?")[0]] = r["id"]
        out.append(ins("jobs", {
            "id": r["id"], "source": r["source"], "company": r["company"], "company_key": r["company_key"],
            "title": r["title"], "location": r["location"], "url": r["url"],
            "description": trim_desc(r["description"], st), "salary": r["salary"],
            "area": area(r["title"]), "level": r["level"], "years": r["years"], "yrs_phrase": r["yrs_phrase"],
            "region": r["region"], "elig": r["elig"], "geo_ok": r["geo_ok"], "fit": r["fit"], "ghost": r["ghost"],
            "priority": r["priority"], "near": r["near"], "reason": r["reject"], "status": st,
            "status_ts": r["applied_ts"] or r["last_seen"], "posted_ts": r["posted_ts"],
            "first_seen": r["first_seen"], "last_seen": r["last_seen"], "seen_count": r["seen_count"],
            "repost_count": r["repost_count"], "dkey": r["dkey"], "gone": r["gone"],
            "desc_partial": r["desc_partial"], "origin": "jobhunter", "notes": r["notes"],
        }))
        n["jobs_jobhunter"] = n.get("jobs_jobhunter", 0) + 1
        if r["status"] == "applied":
            out.append(ins("applications", {
                "job_id": r["id"], "ts": r["applied_ts"] or r["last_seen"], "channel": "manual",
                "via": r["via"] or "cold", "state": "enviada", "state_ts": r["applied_ts"] or r["last_seen"],
                "origin": "jobhunter",
            }, "INSERT"))
            n["applications"] = n.get("applications", 0) + 1

    for r in c.execute("SELECT * FROM contacts"):
        out.append(ins("contacts", dict(r)))
        n["contacts"] = n.get("contacts", 0) + 1
    for table in ("interactions", "templates", "targets", "leads", "invoices"):
        for r in c.execute(f"SELECT * FROM {table}"):
            out.append(ins(table, dict(r)))
            n[table] = n.get(table, 0) + 1

    settings = dict(c.execute("SELECT key, value FROM settings").fetchall())
    for key, fails in json.loads(settings.get("source_fail") or "{}").items():
        out.append(ins("sources_health", {"key": key, "fail_streak": fails, "disabled": 1 if fails >= 3 else 0,
                                          "last_error": "404 no jobhunter"}))
        n["sources_health"] = n.get("sources_health", 0) + 1
    c.close()

    # ---------- Robô v2 ----------
    vagas_csv = os.path.join(robo, "dados", "vagas.csv")
    robo_ids = {}
    with open(vagas_csv, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            st = STATUS_ROBO.get((r["status"] or "").strip().lower(), "nova")
            link = (r["link"] or "").split("?")[0]
            matched = url_to_id.get(link) if link else None
            jid = matched or r["id"]
            robo_ids[r["id"]] = jid
            st_ts = day_ts(r["status_em"]) or day_ts(r["achada_em"])
            fit = float(r["nota"]) if (r["nota"] or "").strip() else 0
            if matched:  # vaga já veio do jobhunter: só atualiza o estado
                out.append(f"UPDATE jobs SET status={q(st)}, status_ts={q(st_ts)}, lang={q(r['idioma'] or None)}, "
                           f"regime={q(r['regime'])}, origin='jobhunter+robo_v2' WHERE id={q(jid)};")
            else:
                out.append(ins("jobs", {
                    "id": jid, "source": r["fonte"], "company": r["empresa"], "company_key": ckey(r["empresa"]),
                    "title": r["cargo"], "location": r["local"], "url": r["link"], "salary": r["salario"],
                    "regime": r["regime"], "lang": r["idioma"] or None, "area": area(r["cargo"]), "fit": fit,
                    "reason": r["motivo"], "apply_type": {"auto": "auto", "manual": "kit"}.get(r["envio"]),
                    "status": st, "status_ts": st_ts, "first_seen": day_ts(r["achada_em"]),
                    "last_seen": st_ts, "origin": "robo_v2", "notes": r["obs"],
                }))
            n["jobs_robo"] = n.get("jobs_robo", 0) + 1
            if st in APPLIED_STATES:
                out.append(ins("applications", {
                    "job_id": jid, "ts": day_ts(r["achada_em"]) if st != "enviada" else st_ts,
                    "channel": "manual" if r["envio"] != "auto" else "auto", "state": st, "state_ts": st_ts,
                    "origin": "robo_v2", "notes": r["obs"],
                }, "INSERT"))
                n["applications"] = n.get("applications", 0) + 1

    ev_path = os.path.join(robo, "dados", "eventos.json")
    if os.path.exists(ev_path):
        for e in json.load(open(ev_path, encoding="utf-8")):
            ts = dtm_ts(e.get("quando"))
            jid = robo_ids.get(e.get("vaga_id") or "")
            out.append(ins("emails", {
                "id": e["msgid"], "ts": ts, "from_addr": e.get("de"),
                "subject": re.sub(r"\s+", " ", e.get("assunto") or ""),
                "class": STATUS_ROBO.get(e.get("tipo"), e.get("tipo")), "company": e.get("empresa"), "job_id": jid,
            }))
            n["emails"] = n.get("emails", 0) + 1
            kind = EVENT_KIND.get(e.get("tipo"))
            if kind and jid:
                out.append(ins("events", {
                    "job_id": jid, "kind": kind, "ts": ts, "due_ts": dtm_ts(e.get("data_entrevista")),
                    "link": e.get("link") or None, "note": e.get("assunto"), "email_id": e["msgid"], "origin": "robo_v2",
                }, "INSERT"))
                n["events"] = n.get("events", 0) + 1

    cfg_path = os.path.join(robo, "config.json")
    if os.path.exists(cfg_path):
        cfg = json.load(open(cfg_path, encoding="utf-8"))
        ats = {k: cfg[k] for k in ("greenhouse", "lever", "ashby") if k in cfg}
        out.append(ins("settings", {"key": "ats_slugs", "value": json.dumps(ats)}))
    out.append(ins("settings", {"key": "pretensao", "value": json.dumps(PRETENSAO, ensure_ascii=False)}))

    # ---------- Super contexto ----------
    if a.contexto:
        p = os.path.expanduser(a.contexto)
        body = open(p, encoding="utf-8").read()
        m = re.search(r"(?:versao|versão|version)\s*:\s*v?(\d+(?:\.\d+)*)|\bv(\d+\.\d+)", body[:2000], re.I)
        out.append(ins("profile", {"key": "super_contexto", "version": (m.group(1) or m.group(2)) if m else None,
                                   "lang": "pt", "body": body, "updated_ts": now}))
        n["profile"] = 1

    out.append(ins("runs", {"kind": "migracao", "started_ts": now, "finished_ts": time.time(), "ok": 1,
                            "summary": json.dumps(n)}, "INSERT"))

    os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
    with open(a.out, "w", encoding="utf-8") as f:
        # zera tabelas migradas para o seed poder rodar de novo sem duplicar
        f.write("DELETE FROM applications WHERE origin IN ('jobhunter','robo_v2');\n")
        f.write("DELETE FROM events WHERE origin = 'robo_v2';\n")
        f.write("\n".join(out) + "\n")
    size = os.path.getsize(a.out) / 1e6
    print(json.dumps(n, ensure_ascii=False))
    print(f"ok: {a.out} ({size:.1f} MB)")


if __name__ == "__main__":
    sys.exit(main())
