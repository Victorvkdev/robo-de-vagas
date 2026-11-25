"""Ciclo diário: coleta -> nota -> grava no D1 -> expira/deduplica -> monta a lista do dia (top 40) -> Gemini.
Os logs (públicos no Actions) só mostram contagens, nunca vagas, nomes ou textos."""
import datetime as dt
import json
import time

from . import ai, score, sources
from .d1 import in_list, q
from .textnorm import company_key, dedupe_key

TZ = dt.timezone(dt.timedelta(hours=-3))  # America/Fortaleza
DAY = 86400
APPLIED = ("aprovada", "precisa_voce", "enviada", "em_analise", "teste", "entrevista", "oferta", "recusada")
LIVE_DAYS = 1.5  # vaga precisa ter sido vista na última coleta (com folga) para entrar na lista


def local_date(now=None):
    return dt.datetime.fromtimestamp(now or time.time(), TZ).strftime("%Y-%m-%d")


def _chunks(seq, n):
    seq = list(seq)
    for i in range(0, len(seq), n):
        yield seq[i:i + n]


UPSERT = """INSERT INTO jobs (id, source, company, company_key, title, location, url, description, salary, regime, lang,
 area, level, years, yrs_phrase, elig, geo_ok, fit, ghost, priority, reason, risk, apply_type, status, status_ts,
 posted_ts, first_seen, last_seen, seen_count, dkey, desc_partial, origin)
VALUES ({vals})
ON CONFLICT(id) DO UPDATE SET
 title=excluded.title, location=excluded.location, company=excluded.company, company_key=excluded.company_key,
 description=CASE WHEN length(COALESCE(excluded.description,'')) > length(COALESCE(jobs.description,''))
                  THEN excluded.description ELSE jobs.description END,
 salary=COALESCE(NULLIF(excluded.salary,''), jobs.salary), regime=COALESCE(NULLIF(excluded.regime,''), jobs.regime),
 lang=excluded.lang, area=excluded.area, level=excluded.level, years=excluded.years, yrs_phrase=excluded.yrs_phrase,
 elig=excluded.elig, geo_ok=excluded.geo_ok, fit=excluded.fit, ghost=excluded.ghost, priority=excluded.priority,
 reason=CASE WHEN jobs.status='listada' THEN jobs.reason ELSE excluded.reason END,
 risk=CASE WHEN jobs.status='listada' THEN jobs.risk ELSE excluded.risk END,
 apply_type=excluded.apply_type, posted_ts=COALESCE(jobs.posted_ts, excluded.posted_ts),
 last_seen=excluded.last_seen, seen_count=COALESCE(jobs.seen_count,0)+1, dkey=excluded.dkey, gone=0,
 desc_partial=excluded.desc_partial,
 status=CASE WHEN jobs.status='expirada' THEN 'nova' ELSE jobs.status END,
 origin=CASE WHEN COALESCE(jobs.origin,'') LIKE '%v3%' THEN jobs.origin ELSE COALESCE(jobs.origin||'+','')||'v3' END"""

COLS = ("id", "source", "company", "company_key", "title", "location", "url", "description", "salary", "regime", "lang",
        "area", "level", "years", "yrs_phrase", "elig", "geo_ok", "fit", "ghost", "priority", "reason", "risk",
        "apply_type", "status", "status_ts", "posted_ts", "first_seen", "last_seen", "seen_count", "dkey",
        "desc_partial", "origin")


def _lang(j):
    if j.get("lang"):
        return j["lang"]
    t = " ".join((j.get("title") or "", (j.get("description") or "")[:400])).lower()
    pt = sum(w in t for w in (" de ", " para ", " com ", " você ", " vaga", "desenvolvedor", "experiência"))
    return "pt" if pt >= 3 else "en"


def load_contacts(db):
    out = {}
    for r in db.query("SELECT company_key, name, position FROM contacts WHERE COALESCE(company_key,'') != ''"):
        out.setdefault(r["company_key"], " · ".join(x for x in (r["name"], r["position"]) if x)[:80])
    return out


def score_jobs(jobs, cfg, now, weights, pretensao, contacts):
    rows, by_source = [], {}
    for j in jobs:
        ck = j.get("company_key") or company_key(j.get("company"))
        s = score.score(j, cfg, now, weights, pretensao, warm=ck in contacts)
        if not s:
            continue
        row = dict(j, **s)
        row.update(company_key=ck, lang=_lang(j), status="nova", status_ts=now, first_seen=now, last_seen=now,
                   seen_count=1, dkey=dedupe_key(j.get("company"), j.get("title")), origin="v3",
                   description=(j.get("description") or "")[:8000], desc_partial=int(bool(j.get("desc_partial"))))
        rows.append(row)
        by_source[j["source"]] = by_source.get(j["source"], 0) + 1
    return rows, by_source


def upsert(db, rows):
    db.exec_many(UPSERT.format(vals=", ".join(q(r.get(c)) for c in COLS)) for r in rows)


def update_health(db, res, by_source, now):
    st = []
    for key in res["ok_boards"]:
        st.append("INSERT INTO sources_health(key,last_ok_ts,fail_streak,useful_count,disabled) VALUES (%s,%s,0,0,0) "
                  "ON CONFLICT(key) DO UPDATE SET last_ok_ts=excluded.last_ok_ts, fail_streak=0, last_error=NULL"
                  % (q(key), q(now)))
    for name, n in res["counts"].items():
        if name in res["errors"]:
            continue
        st.append("INSERT INTO sources_health(key,last_ok_ts,fail_streak,useful_count,disabled) VALUES (%s,%s,0,%s,0) "
                  "ON CONFLICT(key) DO UPDATE SET last_ok_ts=excluded.last_ok_ts, fail_streak=0, "
                  "useful_count=excluded.useful_count, last_error=NULL" % (q(name), q(now), q(by_source.get(name, 0))))
    for key, err in res["errors"].items():
        slug_dead = ":" in key and str(err) in ("404", "410")
        st.append(
            "INSERT INTO sources_health(key,fail_streak,useful_count,disabled,last_error) VALUES (%s,%s,0,0,%s) "
            "ON CONFLICT(key) DO UPDATE SET last_error=excluded.last_error%s"
            % (q(key), 1 if slug_dead else 0, q(str(err)[:120]),
               ", fail_streak=sources_health.fail_streak+1, disabled=CASE WHEN sources_health.fail_streak+1>=3 "
               "THEN 1 ELSE 0 END" if slug_dead else ""))
    if st:
        db.exec_many(st)


def expire(db, res, rows, now):
    """Vaga de ATS cujo board respondeu OK hoje e que não apareceu há mais de 1 dia: expirada."""
    keys = {}
    for k in res["ok_boards"]:
        kind, slug = k.split(":", 1)
        keys.setdefault(kind, set()).add(company_key(sources.pretty(slug)))
    for r in rows:
        if r["source"] in keys:
            keys[r["source"]].add(r["company_key"])
    st = ["UPDATE jobs SET status='expirada', status_ts=%s WHERE status='nova' AND source=%s AND company_key IN %s "
          "AND last_seen < %s" % (q(now), q(kind), in_list(sorted(cks)), q(now - DAY)) for kind, cks in keys.items() if cks]
    if st:
        db.exec_many(st)


def dedupe(db, rows):
    """Mesma empresa+cargo em várias fontes: fica 1 (ATS direto ou descrição mais longa). Já aplicada: as outras somem."""
    dkeys = sorted({r["dkey"] for r in rows if r.get("dkey")})
    found = []
    for ch in _chunks(dkeys, 300):
        found += db.query("SELECT id, dkey, source, status, length(COALESCE(description,'')) AS l FROM jobs "
                          "WHERE dkey IN %s" % in_list(ch))
    groups = {}
    for r in found:
        groups.setdefault(r["dkey"], []).append(r)
    dup = []
    for g in groups.values():
        if len(g) < 2:
            continue
        if any(x["status"] in APPLIED or x["status"] == "listada" for x in g):
            dup += [x["id"] for x in g if x["status"] == "nova"]
            continue
        live = sorted([x for x in g if x["status"] == "nova"],
                      key=lambda x: (x["source"] not in score.ATS, -(x["l"] or 0)))
        dup += [x["id"] for x in live[1:]]
    for ch in _chunks(dup, 400):
        db.exec_many(["UPDATE jobs SET status='duplicada' WHERE id IN %s" % in_list(ch)])
    return len(dup)


def _merge_risk(rule, extra):
    out, seen = [], set()
    for r in [x.strip() for x in (rule or "").split("·")] + list(extra or []):
        k = r.lower()
        if r and k not in seen:
            seen.add(k)
            out.append(r)
    return " · ".join(out[:3])


def prepare_list(db, now, gem=None, force=False, contacts=None):
    today = local_date(now)
    db.query("UPDATE jobs SET status='pulada', status_ts=? WHERE status='listada' AND COALESCE(list_date,'') < ?",
             [now, today])
    if db.setting("paused") == "1":
        return dict(listed=0, skipped="pausado")
    if force:
        db.query("UPDATE jobs SET status='nova', list_date=NULL, list_pos=NULL WHERE status='listada' AND list_date=?",
                 [today])
        db.set_setting("list_sent", "")
    elif db.query("SELECT 1 AS x FROM jobs WHERE status='listada' AND list_date=? LIMIT 1", [today]):
        return dict(listed=0, skipped="lista de hoje já existe")

    size = int(db.setting("list_size") or 40)
    max_co = int(db.setting("max_per_company_month") or 3)
    cands = db.query(
        "SELECT id, title, company, company_key, location, salary, substr(COALESCE(description,''),1,2000) AS description,"
        " source, priority, reason, risk, apply_type, dkey FROM jobs"
        " WHERE status='nova' AND COALESCE(gone,0)=0 AND COALESCE(geo_ok,1)=1 AND COALESCE(origin,'') LIKE '%v3%'"
        " AND last_seen >= ? AND (posted_ts IS NULL OR posted_ts >= ?)"
        " ORDER BY priority DESC, posted_ts DESC LIMIT 400", [now - LIVE_DAYS * DAY, now - 30 * DAY])

    applied_ck = {}
    for r in db.query("SELECT company_key AS ck, COUNT(*) AS n FROM jobs WHERE status IN %s AND status_ts >= ? "
                      "GROUP BY company_key" % in_list(APPLIED), [now - 30 * DAY]):
        applied_ck[r["ck"]] = r["n"]
    for r in db.query("SELECT j.company_key AS ck, COUNT(*) AS n FROM applications a JOIN jobs j ON j.id = a.job_id "
                      "WHERE a.ts >= ? GROUP BY j.company_key", [now - 30 * DAY]):
        applied_ck[r["ck"]] = max(applied_ck.get(r["ck"], 0), r["n"])
    applied_dk = {r["dkey"] for r in db.query("SELECT DISTINCT dkey FROM jobs WHERE status IN %s AND dkey IS NOT NULL"
                                              % in_list(APPLIED))}

    chosen, per_co = [], {}
    for c in cands:
        ck = c["company_key"] or ""
        if c["dkey"] in applied_dk or applied_ck.get(ck, 0) >= max_co or per_co.get(ck, 0) >= 2:
            continue
        per_co[ck] = per_co.get(ck, 0) + 1
        chosen.append(c)
        if len(chosen) >= size:
            break

    notes = {}
    if gem and chosen:
        perfil = ai.perfil_curto((db.query("SELECT body FROM profile WHERE key='super_contexto'") or [{}])[0].get("body"))
        notes = ai.enrich(gem, [dict(c, n=i) for i, c in enumerate(chosen, 1)], perfil)

    contacts = contacts if contacts is not None else load_contacts(db)
    st = []
    for i, c in enumerate(chosen, 1):
        n = notes.get(i) or {}
        st.append("UPDATE jobs SET status='listada', status_ts=%s, list_date=%s, list_pos=%d, summary=%s, reason=%s, "
                  "risk=%s, contact=%s WHERE id=%s" % (
                      q(now), q(today), i, q(n.get("resumo") or ai.resumo_regra(c)),
                      q(n.get("motivo") or c["reason"]), q(_merge_risk(c["risk"], n.get("riscos"))),
                      q(contacts.get(c["company_key"] or "")), q(c["id"])))
    if st:
        db.exec_many(st)
    db.set_setting("list_ready", today if chosen else "")
    return dict(listed=len(chosen), candidates=len(cands), ai=len(notes))


def run(db, cfg, do_collect=True, gem=None, force_list=False, now=None, collector=None):
    now = now or time.time()
    t0 = time.time()
    weights = db.setting_json("areas") or score.DEFAULT_WEIGHTS
    pretensao = db.setting_json("pretensao") or {}
    cfg = dict(cfg)
    cfg["sources"] = dict(cfg.get("sources") or {}, rss=db.setting_json("rss_feeds") or [])
    contacts = load_contacts(db)
    summary = {}
    if do_collect:
        skip = {r["key"] for r in db.query("SELECT key FROM sources_health WHERE disabled = 1")}
        res = (collector or sources.collect)(cfg, skip=skip)
        rows, by_source = score_jobs(res["jobs"], cfg, now, weights, pretensao, contacts)
        upsert(db, rows)
        update_health(db, res, by_source, now)
        expire(db, res, rows, now)
        dups = dedupe(db, rows)
        summary.update(coletadas=res["raw_count"], nas_areas=len(res["jobs"]), aproveitadas=len(rows), duplicadas=dups,
                       por_fonte=res["counts"], erros=len(res["errors"]),
                       fontes_com_erro=sorted(k for k in res["errors"] if ":" not in k))
    summary["lista"] = prepare_list(db, now, gem, force_list, contacts)
    summary["segundos"] = int(time.time() - t0)
    db.query("INSERT INTO runs(kind, started_ts, finished_ts, ok, summary) VALUES ('busca', ?, ?, 1, ?)",
             [now, time.time(), json.dumps(summary, ensure_ascii=False)])
    return summary
