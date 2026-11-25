"""Testa a migração com dados de exemplo: gera o seed e aplica no SQLite com o schema do D1."""
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

JH_SCHEMA = """
CREATE TABLE jobs(id TEXT PRIMARY KEY, source TEXT, company TEXT, title TEXT, location TEXT, url TEXT,
 description TEXT, salary TEXT, posted_ts REAL, first_seen REAL, last_seen REAL, seen_count INTEGER DEFAULT 1,
 fit REAL DEFAULT 0, ghost REAL DEFAULT 0, geo_ok INTEGER DEFAULT 1, status TEXT DEFAULT 'new', follow_up_ts REAL,
 level TEXT DEFAULT 'unknown', years INTEGER, via TEXT DEFAULT 'cold', applied_ts REAL, notes TEXT DEFAULT '',
 app_state TEXT DEFAULT '', region TEXT DEFAULT '', elig TEXT DEFAULT '', company_key TEXT, dkey TEXT,
 repost_count INTEGER DEFAULT 0, reject TEXT, near INTEGER DEFAULT 0, priority INTEGER, yrs_phrase TEXT,
 desc_partial INTEGER DEFAULT 0, gone INTEGER DEFAULT 0, app_state_ts REAL);
CREATE TABLE contacts(id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, url TEXT, email TEXT, company TEXT,
 company_key TEXT, position TEXT, connected_on TEXT, source TEXT DEFAULT 'manual', relation TEXT DEFAULT 'connection',
 stage TEXT DEFAULT 'new', notes TEXT DEFAULT '', last_touch_ts REAL, next_touch_ts REAL, step INTEGER DEFAULT 0, UNIQUE(url));
CREATE TABLE interactions(id INTEGER PRIMARY KEY AUTOINCREMENT, contact_id INTEGER, ts REAL, kind TEXT, note TEXT DEFAULT '');
CREATE TABLE templates(key TEXT PRIMARY KEY, name TEXT, lang TEXT, body TEXT);
CREATE TABLE targets(key TEXT PRIMARY KEY, name TEXT, priority INTEGER DEFAULT 2, notes TEXT DEFAULT '');
CREATE TABLE leads(id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, source TEXT DEFAULT '', stage TEXT DEFAULT 'new',
 value_brl REAL DEFAULT 0, notes TEXT DEFAULT '', created_ts REAL, updated_ts REAL);
CREATE TABLE invoices(id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, amount_brl REAL, lead_id INTEGER, note TEXT DEFAULT '');
CREATE TABLE settings(key TEXT PRIMARY KEY, value TEXT);
"""

CSV = """id,achada_em,fonte,empresa,cargo,local,regime,salario,link,nota,motivo,status,status_em,idioma,envio,obs
r1,2026-11-20,lever,Acme,Product Designer,remoto,,,https://jobs.lever.co/acme/1,70,ok,enviada,2026-11-21,en,auto,
r2,2026-11-20,gupy,Beta's,UX/UI Designer,remoto,CLT,,https://beta.gupy.io/2,60,ok,recusada,2026-11-22,pt,manual,
r3,2026-11-20,ashby,Gama,Front-end Developer,remoto,,,https://jobs.ashbyhq.com/gama/3,55,ok,nova,2026-11-20,en,auto,
dup,2026-11-20,greenhouse,Delta,Product Designer,remoto,,,https://boards.greenhouse.io/delta/9?x=1,50,ok,em análise,2026-11-23,en,auto,
"""


class MigrateTest(unittest.TestCase):
    def test_seed_applies_on_d1_schema(self):
        with tempfile.TemporaryDirectory() as t:
            jh = os.path.join(t, "jobs.db")
            c = sqlite3.connect(jh)
            c.executescript(JH_SCHEMA)
            c.execute("INSERT INTO jobs(id,source,company,title,url,description,status,fit) VALUES"
                      "('j1','greenhouse','Delta','Product Designer','https://boards.greenhouse.io/delta/9','x'||hex(randomblob(500)),'expired',40),"
                      "('j2','jobicy','Fueled','Product Designer','https://jobicy.com/2','desc','applied',64)")
            c.execute("INSERT INTO contacts(name,url,company,company_key) VALUES ('Ana','https://linkedin.com/in/ana','Delta','delta')")
            c.execute("INSERT INTO settings VALUES ('source_fail', ?)", (json.dumps({"lever:foo": 3}),))
            c.commit(); c.close()
            robo = os.path.join(t, "robo"); os.makedirs(os.path.join(robo, "dados"))
            open(os.path.join(robo, "dados", "vagas.csv"), "w").write(CSV)
            json.dump([{"msgid": "<m1>", "tipo": "recusada", "assunto": "No", "de": "x", "quando": "2026-11-22 10:00",
                        "empresa": "Beta", "vaga_id": "r2", "link": "", "data_entrevista": ""}],
                      open(os.path.join(robo, "dados", "eventos.json"), "w"))
            json.dump({"greenhouse": ["a"]}, open(os.path.join(robo, "config.json"), "w"))
            out = os.path.join(t, "seed.sql")
            subprocess.run([sys.executable, os.path.join(ROOT, "scripts", "migrate_local.py"),
                            "--jobhunter", jh, "--robo", robo, "--out", out], check=True, capture_output=True)

            d1 = sqlite3.connect(":memory:")
            for f in sorted(os.listdir(os.path.join(ROOT, "migrations"))):
                d1.executescript(open(os.path.join(ROOT, "migrations", f)).read())
            seed = open(out).read()
            d1.executescript(seed)
            d1.executescript(seed)  # rodar duas vezes não duplica

            q = lambda s: d1.execute(s).fetchone()[0]
            self.assertEqual(q("SELECT COUNT(*) FROM jobs"), 5)  # j1 recebe o status de 'dup' (mesma URL)
            self.assertEqual(q("SELECT status FROM jobs WHERE id='j1'"), "em_analise")
            self.assertEqual(q("SELECT COUNT(*) FROM applications"), 4)  # j2, r1, r2, dup
            self.assertEqual(q("SELECT COUNT(*) FROM applications WHERE state='recusada'"), 1)
            self.assertEqual(q("SELECT COUNT(*) FROM events WHERE kind='recusa'"), 1)
            self.assertEqual(q("SELECT company FROM jobs WHERE id='r2'"), "Beta's")
            self.assertEqual(q("SELECT area FROM jobs WHERE id='r3'"), "frontend")
            self.assertEqual(q("SELECT COUNT(*) FROM contacts"), 1)
            self.assertEqual(q("SELECT disabled FROM sources_health WHERE key='lever:foo'"), 1)
            self.assertEqual(q("SELECT value FROM settings WHERE key='list_size'"), "40")
            self.assertIsNotNone(q("SELECT value FROM settings WHERE key='pretensao'"))


if __name__ == "__main__":
    unittest.main()
