"""Testes da entrega 5b: Super Contexto -> CV adaptado, respostas de formulário, ATS (formulário falso) e envio.
Dados fictícios (tests/fixtures/contexto_exemplo.md). Sem rede. Testes com navegador pulam se o Chromium não abrir."""
import os
import sys
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from engine import ats, cv, envio  # noqa: E402
from engine.d1 import LocalDB  # noqa: E402
from engine.perfil import Perfil  # noqa: E402
from engine.respostas import KIT, SKIP, Respostas, pick_option  # noqa: E402

FIX = os.path.join(ROOT, "tests", "fixtures")
MD = open(os.path.join(FIX, "contexto_exemplo.md"), encoding="utf-8").read()
JOB = dict(id="j1", title="Product Designer", company="Acme Inc", url="file://" + os.path.join(FIX, "form.html"),
           description="Figma, React, accessibility and Liquid", area="product_design", lang="en", level="pleno",
           source="greenhouse", apply_type="auto", fit=80, status="enviando", list_pos=1, contact=None,
           list_date="2026-11-26")


def browser_or_skip(tc):
    try:
        from playwright.sync_api import sync_playwright
        pw = sync_playwright().start()
        b = pw.chromium.launch()
    except Exception as e:  # noqa: BLE001
        tc.skipTest("sem Chromium: %s" % type(e).__name__)
    tc.addCleanup(pw.stop)
    tc.addCleanup(b.close)
    return b


class PerfilTest(unittest.TestCase):
    def test_parse(self):
        p = Perfil(MD)
        self.assertTrue(p.ok())
        self.assertEqual(p.d("DADOS.primeiro_nome"), "Ana")
        self.assertEqual([b["id"] for b in p.bullets], ["B-LG-01", "B-LG-02", "B-PJ-03"])
        self.assertFalse(p.bullets[2]["cv_ok"])
        self.assertEqual(p.exps[0]["inicio"], "2025-10")
        self.assertIn("HL.FE", p.hl)
        self.assertEqual(p.learned[0][1], "No, I open one on the first PJ offer")
        self.assertEqual(p.pret["PRET.pl"][3], "US$ 2,000")


class CvTest(unittest.TestCase):
    def test_select_and_render(self):
        p = Perfil(MD)
        plan = cv.select(p, JOB)
        self.assertEqual(plan["lang"], "en")
        self.assertEqual(plan["bullets"]["Lucky Gaming"][0], "B-LG-01")
        self.assertNotIn("B-PJ-03", sum(plan["bullets"].values(), []))  # "nunca no CV"
        self.assertIn("Liquid", plan["skills"])  # skill do banco citada na vaga sobe
        h = cv.render_html(p, plan, JOB)
        self.assertIn("Did product design in Figma.", h)
        self.assertIn("Oct 2025", h)
        self.assertNotIn("<table", h)
        pt = cv.render_html(p, cv.select(p, dict(JOB, lang="pt")), JOB)
        self.assertIn("Experiência", pt)

    def test_ai_only_ids(self):
        class G:
            def generate(self, _):
                return '{"headline":"HL.FE","bullets":{"Lucky Gaming":["B-LG-02","B-XX-99","B-LG-01"]}}'
        plan = cv.select(Perfil(MD), JOB, G())
        self.assertEqual(plan["bullets"]["Lucky Gaming"], ["B-LG-02", "B-LG-01"])  # ID inventado sai
        self.assertEqual(plan["headline"], "HL.FE")


class RespostasTest(unittest.TestCase):
    def setUp(self):
        self.r = Respostas(Perfil(MD), JOB)

    def test_rules(self):
        a = self.r.answer
        self.assertEqual(a("First Name"), "Ana")
        self.assertEqual(a("Email address"), "ana@example.com")
        self.assertEqual(a("LinkedIn Profile"), "https://linkedin.com/in/exemplo")
        self.assertEqual(a("Current company"), SKIP)
        self.assertEqual(a("Have you ever been convicted of a felony?"), KIT)
        self.assertEqual(a("Date of birth"), KIT)
        self.assertEqual(a("Are you authorized to work in the United States?"), "No")
        self.assertEqual(a("Salary expectations"), "US$ 2,000/month")
        self.assertEqual(a("Do you have a CNPJ?"), "No, I open one on the first PJ offer")  # aprendida
        self.assertIn("example designer", a("Cover letter", "textarea"))
        self.assertIsNone(a("Describe your biggest weakness", "textarea"))  # sem IA: não inventa

    def test_pick(self):
        self.assertEqual(pick_option("No", ["Select...", "Yes", "No"]), "No")
        self.assertEqual(pick_option("Sim (checkbox obrigatório)", ["Yes", "No"]), "Yes")
        self.assertEqual(pick_option("Decline to self-identify", ["Male", "Female", "I don't wish to answer"]),
                         "I don't wish to answer")
        self.assertEqual(pick_option("6", ["0-2 years", "3-5 years", "6-8 years", "9+"]), "6-8 years")
        self.assertIsNone(pick_option("Brazil", ["Red", "Blue"]))

    def test_apply_url(self):
        self.assertEqual(ats.apply_url("https://jobs.lever.co/acme/123"), "https://jobs.lever.co/acme/123/apply")
        self.assertEqual(ats.apply_url("https://jobs.ashbyhq.com/acme/abc?x=1"), "https://jobs.ashbyhq.com/acme/abc/application")
        self.assertEqual(ats.apply_url("https://apply.workable.com/acme/j/ABC/"), "https://apply.workable.com/acme/j/ABC/apply/")


class AtsTest(unittest.TestCase):
    def test_form(self):
        b = browser_or_skip(self)
        p = Perfil(MD)
        pdf = cv.pdf(cv.render_html(p, cv.select(p, JOB), JOB), "/tmp/_t_cv.pdf", b)
        src = open(os.path.join(FIX, "form.html"), encoding="utf-8").read()
        open("/tmp/_f1.html", "w").write(src.replace("EXTRA", ""))
        open("/tmp/_f2.html", "w").write(src.replace(
            "EXTRA", '<label for="q9">Describe your biggest weakness *</label><textarea id="q9" required></textarea>'))
        open("/tmp/_f3.html", "w").write(src.replace(
            "EXTRA", '<iframe title="hCaptcha" src="https://hcaptcha.com/checkbox" width="300" height="80"></iframe>'))
        r = ats.run(b, dict(JOB, url="file:///tmp/_f1.html"), p, pdf, mode="ensaio", shot_path="/tmp/_s.png")
        self.assertEqual(r["status"], "ensaio", r["reason"])
        r = ats.run(b, dict(JOB, url="file:///tmp/_f1.html"), p, pdf, mode="real", shot_path="/tmp/_s.png")
        self.assertEqual(r["status"], "enviada", r["reason"])
        r = ats.run(b, dict(JOB, url="file:///tmp/_f2.html"), p, pdf, mode="real", shot_path="/tmp/_s.png")
        self.assertEqual(r["status"], "kit")
        self.assertIn("weakness", r["questions"][0])
        r = ats.run(b, dict(JOB, url="file:///tmp/_f3.html"), p, pdf, mode="real", shot_path="/tmp/_s.png")
        self.assertEqual((r["status"], r["reason"]), ("kit", "captcha na página"))
        r = ats.run(b, dict(JOB, url="https://www.linkedin.com/jobs/view/1"), p, pdf, mode="real")
        self.assertEqual(r["status"], "kit")


class FakeR2:
    def __init__(self):
        self.objs, self.errors = {}, []

    def put(self, k, data, ctype=""):
        self.objs[k] = data
        return k

    def put_file(self, k, path, ctype=""):
        return self.put(k, open(path, "rb").read())

    def list(self, prefix):
        return [k for k in self.objs if k.startswith(prefix)]

    def delete(self, k):
        return self.objs.pop(k, None) is not None


class EnvioTest(unittest.TestCase):
    def db(self):
        db = LocalDB(":memory:", os.path.join(ROOT, "migrations"))
        db.query("INSERT INTO profile(key, body) VALUES ('super_contexto', ?)", [MD])
        return db

    def test_run_and_backup(self):
        b = browser_or_skip(self)
        db = self.db()
        src = open(os.path.join(FIX, "form.html"), encoding="utf-8").read()
        open("/tmp/_f1.html", "w").write(src.replace("EXTRA", ""))
        for i, url in ((1, "file:///tmp/_f1.html"), (2, "https://www.linkedin.com/jobs/view/2")):
            db.query("INSERT INTO jobs(id, title, company, url, area, lang, source, apply_type, fit, status, list_pos, list_date) "
                     "VALUES (?, 'Product Designer', 'Acme', ?, 'product_design', 'en', 'greenhouse', 'auto', 80, 'enviando', ?, "
                     "'2026-11-26')", ["j%d" % i, url, i])
        r2 = FakeR2()
        out = envio.run(db, mode="real", limit=10, r2=r2, launch=lambda: b, notify=False)
        self.assertEqual((out["enviadas"], out["kit"]), (1, 1), out)
        st = {r["id"]: r["status"] for r in db.query("SELECT id, status FROM jobs")}
        self.assertEqual(st, {"j1": "enviada", "j2": "aprovada"})
        app = db.query("SELECT channel, cv_key, proof_key, answers FROM applications")[0]
        self.assertEqual(app["channel"], "auto")
        self.assertTrue(app["cv_key"].startswith("cv/") and app["proof_key"].startswith("proof/"))
        self.assertIn("ana@example.com", app["answers"])
        # backup: guarda e mantém só os 8 últimos
        for d in range(10):
            r2.objs["backup/d1_2026-01-%02d.json.gz" % (d + 1)] = b"x"
        res = envio.backup(db, r2, keep=8, now=time.time())
        self.assertTrue(res["key"])
        self.assertEqual(len(r2.list("backup/")), 8)
        self.assertGreater(res["linhas"], 2)


if __name__ == "__main__":
    unittest.main()
