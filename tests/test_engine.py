"""Testes do motor (entrega 3) com dados de exemplo e um SQLite com o schema do D1. Sem rede."""
import os
import sys
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from engine import pipeline, score  # noqa: E402
from engine.__main__ import load_cfg  # noqa: E402
from engine.d1 import LocalDB, q  # noqa: E402
from engine.textnorm import company_key  # noqa: E402

NOW = 1795600000.0  # 2026-11-25
CFG = load_cfg()
LONG = "We build a design system in Figma and React. " * 20


def mk(i, title, company="Acme", location="Remote - Worldwide", source="greenhouse", age=1, desc=LONG, salary=""):
    return dict(id="j%d" % i, source=source, company=company, company_key=company_key(company), title=title,
                location=location, url="https://x/%d" % i, description=desc, salary=salary,
                posted_ts=NOW - age * 86400, desc_partial=False, regime="", lang=None)


class ScoreTest(unittest.TestCase):
    def test_areas(self):
        self.assertEqual(score.area_of("Senior Product Designer")[0], "product_design")
        self.assertEqual(score.area_of("Design Engineer")[0], "design_eng")
        self.assertEqual(score.area_of("Desenvolvedor Front-end Pleno")[0], "frontend")
        self.assertEqual(score.area_of("CRM Specialist")[0], "crm")
        self.assertEqual(score.area_of("Analista de QA")[0], "suporte_qa")
        self.assertEqual(score.area_of("Webflow Developer")[0], "web")
        self.assertEqual(score.area_of("UX/UI Designer")[0], "uxui")
        self.assertIsNone(score.area_of("Backend Engineer")[0])

    def test_exclusions(self):
        for t in ("Staff Product Designer", "Design Intern", "Graphic Designer", "Head of Design",
                  "Vaga afirmativa: UX Designer", "Java Front-end Developer", "Design Manager"):
            self.assertTrue(score.excluded(t), t)
        self.assertFalse(score.excluded("CRM Manager"))
        self.assertFalse(score.excluded("Junior UX Designer"))  # júnior liberado (25/11)

    def test_drops(self):
        self.assertIsNone(score.score(mk(1, "Product Designer", location="New York (Hybrid)"), CFG, NOW))
        self.assertIsNone(score.score(mk(2, "Product Designer", location="US only"), CFG, NOW))
        self.assertIsNone(score.score(mk(3, "Product Designer", age=40), CFG, NOW))
        self.assertIsNone(score.score(mk(4, "Account Executive"), CFG, NOW))

    def test_ranking_and_risks(self):
        a = score.score(mk(1, "Product Designer", location="LATAM", age=1), CFG, NOW)
        b = score.score(mk(2, "QA Analyst", location="Remote - Worldwide", age=10), CFG, NOW)
        c = score.score(mk(3, "Product Designer", location="Remote - Europe", age=1), CFG, NOW)
        self.assertGreater(a["fit"], b["fit"])
        self.assertGreater(a["fit"], c["fit"])
        self.assertEqual(c["elig"], "regiao")
        self.assertIn("Europa", c["risk"])
        d = score.score(mk(4, "Product Designer", desc=LONG + " 8+ years of experience in product design."), CFG, NOW)
        self.assertIn("8+ anos", d["risk"])
        j = score.score(mk(5, "Junior UX Designer"), CFG, NOW)
        self.assertIn("júnior", j["risk"])

    def test_salary_only_warns(self):
        p = {"pleno": {"clt_brl": 4500, "usd_mes": 2000, "usd_h": 20}}
        r = score.score(mk(1, "UX Designer", source="gupy", location="Brasil (remoto)", salary="R$ 3.000"), CFG, NOW,
                        pretensao=p)
        self.assertIsNotNone(r)
        self.assertIn("abaixo da pretensão", r["risk"])
        self.assertIsNone(score.salary_warning("USD 60000-80000/ano", "mid", p))
        self.assertIsNotNone(score.salary_warning("USD 15000-18000", "mid", p))

    def test_apply_type(self):
        self.assertEqual(score.apply_type({"source": "lever", "url": ""}), "auto")
        self.assertEqual(score.apply_type({"source": "himalayas", "url": "https://jobs.ashbyhq.com/x/1"}), "auto")
        self.assertEqual(score.apply_type({"source": "gupy", "url": "https://x.gupy.io/1"}), "kit")
        self.assertEqual(score.apply_type({"source": "linkedin", "url": "https://www.linkedin.com/jobs/1"}), "linkedin")


class PipelineTest(unittest.TestCase):
    def setUp(self):
        self.db = LocalDB(":memory:", os.path.join(ROOT, "migrations"))
        self.db.set_setting("list_size", "5")
        self.db.query("INSERT INTO contacts(name, position, company, company_key, url) VALUES "
                      "('Ana', 'Designer', 'Beta', 'beta', 'u1')")
        self.db.query("INSERT INTO profile(key, body) VALUES ('super_contexto', 'Vk, designer. hello@x.com')")

    def fake(self, jobs, ok=("greenhouse:acme",)):
        return lambda cfg, skip=(): dict(jobs=jobs, errors={"lever:morta": "404", "remotive": "timeout"},
                                         ok_boards=set(ok), counts={"greenhouse": len(jobs), "remotive": 0},
                                         raw_count=len(jobs))

    def test_full_cycle(self):
        jobs = [mk(i, "Product Designer %d" % i, company="Co%d" % i) for i in range(8)]
        jobs += [mk(20, "UX Designer", company="Beta", source="lever")]
        jobs += [mk(21, "UX Designer", company="Beta", source="himalayas")]  # duplicada da 20
        jobs += [mk(22, "Product Designer 1", company="Co1", source="remotive")]  # duplicada da 1
        jobs += [mk(23, "Backend Engineer")]  # nunca entra
        s = pipeline.run(self.db, CFG, gem=None, now=NOW, collector=self.fake(jobs))
        self.assertEqual(s["aproveitadas"], 11)
        self.assertEqual(s["duplicadas"], 2)
        self.assertEqual(s["lista"]["listed"], 5)
        rows = self.db.query("SELECT id, list_pos, summary, contact, apply_type FROM jobs WHERE status='listada' "
                             "ORDER BY list_pos")
        self.assertEqual([r["list_pos"] for r in rows], [1, 2, 3, 4, 5])
        self.assertTrue(all(r["summary"] for r in rows))
        warm = [r for r in rows if r["id"] == "j20"]
        self.assertTrue(warm and warm[0]["contact"].startswith("Ana"))  # contato conhecido sobe no ranking
        self.assertEqual(self.db.query("SELECT status FROM jobs WHERE id='j21'")[0]["status"], "duplicada")
        h = {r["key"]: r for r in self.db.query("SELECT * FROM sources_health")}
        self.assertEqual(h["lever:morta"]["fail_streak"], 1)
        self.assertEqual(h["remotive"]["fail_streak"], 0)
        self.assertEqual(self.db.setting("list_ready"), pipeline.local_date(NOW))

        # mesma busca no mesmo dia não refaz a lista
        s2 = pipeline.run(self.db, CFG, now=NOW + 600, collector=self.fake(jobs))
        self.assertEqual(s2["lista"].get("skipped"), "lista de hoje já existe")
        self.assertEqual(s2["banco"]["iguais"], 11)  # nada mudou: nada regravado (ajuste b)

        # dia seguinte: quem ficou listada vira pulada; vaga de board OK que sumiu expira; slug 404 3x desliga
        tomorrow = NOW + 86400 * 1.2
        self.db.query("UPDATE jobs SET status='aprovada', status_ts=? WHERE id='j20'", [NOW])
        left = [j for j in jobs if j["id"] not in ("j7",)]
        pipeline.run(self.db, CFG, now=tomorrow, collector=self.fake(left, ok=("greenhouse:co7",)))
        pipeline.run(self.db, CFG, now=tomorrow + 60, collector=self.fake(left, ok=("greenhouse:co7",)), force_list=True)
        st = dict((r["id"], r["status"]) for r in self.db.query("SELECT id, status FROM jobs"))
        self.assertEqual(st["j20"], "aprovada")
        self.assertIn(st["j7"], ("expirada",))
        self.assertNotIn("listada", [st[k] for k in ("j0",) if k in st and st[k] == "pulada"])
        self.assertEqual(self.db.query("SELECT disabled FROM sources_health WHERE key='lever:morta'")[0]["disabled"], 1)
        listed_today = self.db.query("SELECT company_key FROM jobs WHERE status='listada'")
        self.assertNotIn("beta", [r["company_key"] for r in listed_today])  # já aprovada na Beta: não repete cargo

    def test_paused(self):
        self.db.set_setting("paused", "1")
        s = pipeline.run(self.db, CFG, now=NOW, collector=self.fake([mk(1, "Product Designer")]))
        self.assertEqual(s["lista"]["skipped"], "pausado")

    def test_quote(self):
        self.assertEqual(q("it's"), "'it''s'")
        self.assertEqual(q(None), "NULL")


if __name__ == "__main__":
    unittest.main()
