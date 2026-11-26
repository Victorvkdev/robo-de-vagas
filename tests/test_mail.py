"""Testes da entrega 4 (e-mail, agenda, rascunhos, follow-up) e dos ajustes da entrega 3. Sem rede."""
import datetime as dt
import os
import sys
import unittest
from email.message import EmailMessage
from zoneinfo import ZoneInfo

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from engine import mail, notify, pipeline, score  # noqa: E402
from engine.__main__ import load_cfg  # noqa: E402
from engine.d1 import LocalDB  # noqa: E402
from engine.textnorm import company_key  # noqa: E402

CFG = load_cfg()
# quarta, 25/11/2026 15h em Teresina
NOW = dt.datetime(2026, 11, 25, 15, 0, tzinfo=ZoneInfo("America/Fortaleza")).timestamp()
NIGHT = dt.datetime(2026, 11, 25, 23, 30, tzinfo=ZoneInfo("America/Fortaleza")).timestamp()


def raw(frm, subject, body, ics=None, msgid=None, date_ts=NOW - 600, html=False):
    m = EmailMessage()
    m["From"] = frm
    m["To"] = "hello@victorvk.com"
    m["Subject"] = subject
    m["Message-ID"] = msgid or "<%s@test>" % abs(hash(subject + frm))
    m["Date"] = dt.datetime.fromtimestamp(date_ts, dt.timezone.utc).strftime("%a, %d %b %Y %H:%M:%S +0000")
    if html:
        m.set_content("<p>%s</p>" % body, subtype="html")
    else:
        m.set_content(body)
    if ics:
        m.add_attachment(ics.encode(), maintype="text", subtype="calendar", filename="invite.ics")
    return m.as_bytes()


ICS_NY = "BEGIN:VCALENDAR\nBEGIN:VEVENT\nDTSTART;TZID=America/New_York:20261202T140000\n" \
         "LOCATION:https://meet.google.com/abc-defg-hij\nEND:VEVENT\nEND:VCALENDAR"


class FakeTG:
    def __init__(self):
        self.sent = []

    def send(self, text, keyboard=None, doc=None, doc_name=None):
        self.sent.append(dict(text=text, keyboard=keyboard, doc=doc))
        return True


class FakeSMTP:
    def __init__(self):
        self.msgs = []

    def send_message(self, msg):
        self.msgs.append(msg)


def db_with_jobs():
    db = LocalDB(":memory:", os.path.join(ROOT, "migrations"))
    for jid, comp, title, st in (("w1", "Wise", "Product Designer", "enviada"),
                                 ("p1", "Puzzle", "UX Designer", "recusada"),
                                 ("a1", "Acme Corp", "UI Engineer", "aprovada")):
        db.query("INSERT INTO jobs (id, company, company_key, title, status, status_ts, origin) VALUES (?,?,?,?,?,?,'v3')",
                 [jid, comp, company_key(comp), title, st, NOW - 5 * 86400])
    db.query("INSERT INTO applications (job_id, ts, channel, state, state_ts, origin) VALUES ('w1', ?, 'manual', "
             "'enviada', ?, 'v3')", [NOW - 5 * 86400, NOW - 5 * 86400])
    db.query("INSERT INTO profile (key, body) VALUES ('super_contexto', 'Product Designer, 6+ anos. 303K -> 3,4M.')")
    return db


def parsed(*a, **k):
    return mail.parse_message(raw(*a, **k))


class ParseTest(unittest.TestCase):
    def test_quotes_puzzle(self):
        body = ("Hi Victor, we'd love to invite you to an interview with the team. What times work for you next week?\n\n"
                "On Mon, Nov 10, 2026 at 9:00 AM Iveth <iveth@puzzle.tech> wrote:\n"
                "> Unfortunately we decided to move forward with other candidates.")
        m = parsed("Iveth Torres <iveth.torres@puzzle.tech>", "Re: Update on Your Application", body)
        self.assertNotIn("Unfortunately", m["text"])
        self.assertEqual(mail.classify(m, [], now=NOW)[0], "agendar")
        self.assertEqual(mail.new_status("recusada", "entrevista"), "entrevista")
        self.assertIsNone(mail.new_status("entrevista", "em_analise"))
        self.assertEqual(mail.new_status("entrevista", "recusada"), "recusada")

    def test_classes(self):
        c = lambda *a, **k: mail.classify(parsed(*a, **k), [], now=NOW)[0]  # noqa: E731
        self.assertEqual(c("Wise <no-reply@ashbyhq.com>", "Thanks for applying to Wise",
                           "Thank you for applying! We've received your application."), "recebida")
        self.assertEqual(c("Wise <no-reply@ashbyhq.com>", "Your application", "Unfortunately we will not be moving "
                                                                              "forward with your application."), "recusa")
        self.assertEqual(c("Ana <ana@wise.com>", "Interview invitation", "Hi! Interview", ics=ICS_NY), "entrevista")
        self.assertEqual(c("Ana <ana@wise.com>", "Next steps", "Please book a time for an interview: "
                                                              "https://calendly.com/wise/30min"), "agendar")
        self.assertEqual(c("Rita <rita@acme.com>", "Sua candidatura", "Olá Victor! Qual a sua pretensão salarial para a "
                                                                     "vaga? Você tem disponibilidade imediata?"), "recrutador")
        self.assertEqual(c("Coderbyte <no-reply@coderbyte.com>", "Assessment for Product Designer position",
                           "Complete the take-home assessment by December 5."), "teste")
        self.assertEqual(c("LinkedIn <jobs-noreply@linkedin.com>", "New jobs for you", "Recommended jobs ... job alert"),
                         "spam")
        self.assertEqual(c("João <joao@gmail.com>", "Orçamento de site", "Oi! Preciso de um site para minha loja, "
                                                                        "pode me passar um orçamento?"), "orcamento")
        self.assertEqual(c("Promo <news@shop.com>", "Black Friday", "50% off everything"), "")

    def test_rule_override(self):
        m = parsed("Iveth <iveth@puzzle.tech>", "Re: Update", "Unfortunately...")
        rules = [dict(kind="from_subject", pattern="iveth@puzzle.tech|update", **{"class": "entrevista"})]
        self.assertEqual(mail.classify(m, rules, now=NOW)[0], "entrevista")

    def test_dates(self):
        ts, tz, ok = mail.parse_when("", ICS_NY, NOW)
        self.assertEqual(mail.fmt_local(ts), "qua, 02/12 às 16h")  # 14h NY = 16h Teresina
        ts, tz, ok = mail.parse_when("Let's talk on December 2nd at 2:00 PM EST via Zoom", "", NOW)
        self.assertEqual(mail.fmt_local(ts), "qua, 02/12 às 16h")
        self.assertTrue(ok)
        ts, tz, ok = mail.parse_when("Entrevista marcada para 03/12 às 14h30 (horário de Brasília)", "", NOW)
        self.assertEqual(mail.fmt_local(ts), "qui, 03/12 às 14h30")
        ts, _, _ = mail.parse_when("Temos 3 vagas abertas desde 02/12", "", NOW)
        self.assertIsNone(ts)
        ts, _, _ = mail.parse_when("Call on 5 January at 10am GMT", "", NOW)
        self.assertEqual(dt.datetime.fromtimestamp(ts, dt.timezone.utc).strftime("%Y-%m-%d %H"), "2027-01-05 10")

    def test_slots_skip_weekend(self):
        s = mail.slots(dt.datetime(2026, 11, 27, 15, tzinfo=ZoneInfo("America/Fortaleza")).timestamp())  # sexta
        self.assertEqual([x.weekday() for x in s], [0, 1, 2])
        self.assertIn("seg, 30/11 às 10h", mail.fmt_slots(s, "pt"))

    def test_quiet(self):
        db = LocalDB(":memory:", os.path.join(ROOT, "migrations"))
        self.assertTrue(notify.is_quiet(db, NIGHT))
        self.assertFalse(notify.is_quiet(db, NOW))


class RunTest(unittest.TestCase):
    def mailbox(self, msgs):
        return lambda state, now: ([("INBOX", i + 1, r, False) for i, r in enumerate(msgs)],
                                   {"INBOX": {"v": 1, "u": len(msgs)}})

    def test_full_cycle(self):
        db = db_with_jobs()
        db.set_setting("mail_state", '{"INBOX": {"v": 1, "u": 0}}')
        tg = FakeTG()
        msgs = [
            raw("Ana <ana@wise.com>", "Interview: Product Designer", "Confirming our interview.", ics=ICS_NY),
            raw("Rita <rita@acme.com>", "Sua candidatura na Acme Corp",
                "Olá Victor! Qual a sua pretensão salarial para a vaga? Obrigada"),
            raw("Iveth <iveth.torres@puzzle.tech>", "Re: Update on Your Application",
                "Hi Victor! We'd like to schedule an interview. What times work for you?\n\n"
                "On Mon, Nov 10, 2026 Iveth wrote:\n> Unfortunately we decided to move forward"),
            raw("News <newsletter@medium.com>", "Weekly digest", "newsletter unsubscribe weekly digest"),
        ]
        out = mail.run(db, tg, None, now=NOW, mailbox=self.mailbox(msgs), smtp=FakeSMTP())
        self.assertEqual(out["emails"]["lidos"], 4)
        st = {r["id"]: r["status"] for r in db.query("SELECT id, status FROM jobs")}
        self.assertEqual(st["w1"], "entrevista")
        self.assertEqual(st["p1"], "entrevista")  # recusa antiga não segura a entrevista nova
        ev = db.query("SELECT * FROM events WHERE kind='entrevista'")
        self.assertEqual(len(ev), 1)
        self.assertEqual(mail.fmt_local(ev[0]["due_ts"]), "qua, 02/12 às 16h")
        drafts = db.query("SELECT * FROM outbox ORDER BY id")
        self.assertEqual([d["kind"] for d in drafts], ["resposta", "agendar"])
        self.assertTrue(drafts[1]["in_reply_to"])
        self.assertIn("Victor Cavalcante", drafts[0]["body"])
        self.assertEqual(drafts[0]["lang"], "pt")
        # aviso de entrevista com .ics e rascunhos com botões
        self.assertTrue(any(s["doc"] and "BEGIN:VCALENDAR" in s["doc"] for s in tg.sent))
        self.assertTrue(any(s["keyboard"] and s["keyboard"][0][0]["callback_data"].startswith("ob:s:") for s in tg.sent))
        self.assertEqual(db.query("SELECT COUNT(*) AS n FROM emails")[0]["n"], 3)  # spam não é gravado
        # a candidatura da Acme (aprovada, sem application) ganha linha em applications? recrutador não muda status
        self.assertEqual(st["a1"], "aprovada")
        # segunda rodada sem e-mail novo: nada gravado
        before = db.rows_written
        mail.run(db, tg, None, now=NOW + 900, mailbox=lambda s, n: ([], s), smtp=FakeSMTP())
        self.assertLessEqual(db.rows_written - before, 1)

    def test_quiet_queue_and_send(self):
        db = db_with_jobs()
        db.set_setting("mail_state", '{"INBOX": {"v": 1, "u": 0}}')
        tg = FakeTG()
        msgs = [raw("Rita <rita@acme.com>", "Pergunta", "Olá Victor, você tem disponibilidade para a vaga? Obrigada",
                    date_ts=NIGHT - 60),
                raw("Ana <ana@wise.com>", "Interview", "Confirming our interview", ics=ICS_NY, date_ts=NIGHT - 60)]
        mail.run(db, tg, None, now=NIGHT, mailbox=self.mailbox(msgs), smtp=FakeSMTP())
        self.assertEqual(len(tg.sent), 1)  # só a entrevista fura o silêncio
        self.assertEqual(db.query("SELECT COUNT(*) AS n FROM notices WHERE sent_ts IS NULL")[0]["n"], 1)
        # Vk aprova o rascunho -> envio por SMTP na mesma thread
        oid = db.query("SELECT id FROM outbox")[0]["id"]
        db.query("UPDATE outbox SET state='aprovado' WHERE id=?", [oid])
        smtp = FakeSMTP()
        out = mail.run(db, tg, None, mode="enviar", now=NIGHT + 60, smtp=smtp)
        self.assertEqual(out["envio"]["enviados"], 1)
        self.assertEqual(smtp.msgs[0]["To"], "rita@acme.com")
        self.assertTrue(smtp.msgs[0]["In-Reply-To"])
        self.assertEqual(db.query("SELECT state FROM outbox WHERE id=?", [oid])[0]["state"], "enviado")
        # não envia duas vezes
        out = mail.run(db, tg, None, mode="enviar", now=NIGHT + 120, smtp=smtp)
        self.assertEqual(out["envio"]["enviados"], 0)

    def test_first_run_no_old_notices(self):
        db = db_with_jobs()
        tg = FakeTG()
        msgs = [raw("Rita <rita@acme.com>", "Pergunta", "Olá Victor, você tem disponibilidade para a vaga?")]
        mail.run(db, tg, None, now=NOW, mailbox=lambda s, n: ([("INBOX", 1, msgs[0], True)], {"INBOX": {"v": 1, "u": 1}}),
                 smtp=FakeSMTP())
        self.assertEqual(tg.sent, [])
        self.assertEqual(db.query("SELECT COUNT(*) AS n FROM outbox")[0]["n"], 0)

    def test_followup(self):
        db = db_with_jobs()
        db.query("UPDATE applications SET ts=? WHERE job_id='w1'", [NOW - 11 * 86400])
        db.query("INSERT INTO emails (id, ts, from_addr, subject, class, job_id, msgid, reply_to, lang) VALUES "
                 "('e1', ?, 'ana@wise.com', 'Your application', 'recebida', 'w1', '<m1@x>', 'ana@wise.com', 'en')",
                 [NOW - 10 * 86400])
        db.query("INSERT INTO jobs (id, company, company_key, title, status, status_ts) VALUES ('o1','Old','old','PD',"
                 "'enviada',?)", [NOW - 30 * 86400])
        db.query("INSERT INTO applications (job_id, ts, state) VALUES ('o1', ?, 'enviada')", [NOW - 25 * 86400])
        tg = FakeTG()
        db.set_setting("mail_state", '{"INBOX": {"v": 1, "u": 0}}')
        out = mail.run(db, tg, None, now=NOW, mailbox=lambda s, n: ([], s), smtp=FakeSMTP())
        self.assertEqual(out["followups"], 1)
        self.assertEqual(out["sem_retorno"], 1)
        d = db.query("SELECT * FROM outbox WHERE kind='followup'")[0]
        self.assertIn("Product Designer", d["body"])
        self.assertEqual(d["to_addr"], "ana@wise.com")
        out = mail.run(db, tg, None, now=NOW + 900, mailbox=lambda s, n: ([], s), smtp=FakeSMTP())
        self.assertEqual(out["followups"], 0)  # 1x por dia

    def test_redo_after_correction(self):
        db = db_with_jobs()
        db.set_setting("mail_state", '{"INBOX": {"v": 1, "u": 0}}')
        db.query("INSERT INTO emails (id, ts, from_addr, subject, snippet, class, company, msgid, reply_to, lang, redo) "
                 "VALUES ('e9', ?, 'bob@acme.com', 'Hello', 'Can we talk?', 'agendar', 'Acme', '<z@x>', 'bob@acme.com', "
                 "'en', 1)", [NOW])
        tg = FakeTG()
        out = mail.run(db, tg, None, now=NOW, mailbox=lambda s, n: ([], s), smtp=FakeSMTP())
        self.assertEqual(out["refeitos"], 1)
        self.assertIn("UTC−3", db.query("SELECT body FROM outbox")[0]["body"])


class ListaDuplicadaTest(unittest.TestCase):
    def test_no_second_list_same_day(self):
        db = LocalDB(":memory:", os.path.join(ROOT, "migrations"))
        jobs = [dict(id="g%d" % i, source="greenhouse", company="Co%d" % i, title="Product Designer",
                     location="Remote - Worldwide", url="https://x/%d" % i, description="Figma React. " * 60,
                     salary="", posted_ts=NOW - 86400, desc_partial=False, regime="", lang=None) for i in range(12)]
        fake = lambda cfg, skip=(): dict(jobs=jobs[:6], errors={}, ok_boards=set(), counts={}, raw_count=6)  # noqa
        pipeline.run(db, CFG, now=NOW, collector=fake)
        db.query("UPDATE jobs SET status='pulada' WHERE status='listada'")  # Vk analisou tudo
        fake2 = lambda cfg, skip=(): dict(jobs=jobs, errors={}, ok_boards=set(), counts={}, raw_count=12)  # noqa
        s = pipeline.run(db, CFG, now=NOW + 3600, collector=fake2)
        self.assertEqual(s["lista"].get("skipped"), "lista de hoje já existe")
        s = pipeline.run(db, CFG, now=NOW + 7200, collector=fake2, force_list=True)
        pos = [r["list_pos"] for r in db.query("SELECT list_pos FROM jobs WHERE status='listada' ORDER BY list_pos")]
        self.assertEqual(pos, list(range(7, 13)))


class Entrega3AjustesTest(unittest.TestCase):
    def test_filters(self):
        for t in ("Learning Experience Designer", "Talent Pool - Product Designer", "Software Engineer, CRM",
                  "Shopify Developer", "UI Artist", "Designer Instrucional"):
            self.assertFalse(score.keep_title(t), t)
        for t in ("Software Engineer, Frontend", "Product Designer", "CRM Specialist", "Shopify Designer"):
            self.assertTrue(score.keep_title(t), t)

    def test_linkedin_city(self):
        j = dict(source="linkedin", title="Product Designer", location="Campinas, São Paulo, Brazil (remoto)",
                 description="")
        self.assertEqual(score.eligibility(j, CFG), "presencial")
        j["location"] = "Brazil (remoto)"
        self.assertEqual(score.eligibility(j, CFG), "aberta")
        j.update(location="Campinas, São Paulo, Brazil (remoto)", title="Product Designer (Remote)")
        self.assertNotEqual(score.eligibility(j, CFG), "presencial")

    def test_upsert_writes_only_changes(self):
        db = LocalDB(":memory:", os.path.join(ROOT, "migrations"))
        now = NOW
        jobs = [dict(id="g%d" % i, source="greenhouse", company="Acme", title="Product Designer %d" % i,
                     location="Remote - Worldwide", url="https://x/%d" % i, description="Figma React. " * 60,
                     salary="", posted_ts=now - 86400, desc_partial=False, regime="", lang=None) for i in range(50)]
        rows, _ = pipeline.score_jobs(jobs, CFG, now, score.DEFAULT_WEIGHTS, {}, {})
        r1 = pipeline.upsert(db, rows, now)
        self.assertEqual(r1["novas"], 50)
        before = db.rows_written
        rows, _ = pipeline.score_jobs(jobs, CFG, now + 3600, score.DEFAULT_WEIGHTS, {}, {})
        r2 = pipeline.upsert(db, rows, now + 3600)
        self.assertEqual(db.rows_written - before, 0)
        self.assertEqual(r2["iguais"], 50)
        rows, _ = pipeline.score_jobs(jobs, CFG, now + 21 * 3600, score.DEFAULT_WEIGHTS, {}, {})
        rows[0]["salary"] = "US$ 3,000/mo"
        r3 = pipeline.upsert(db, rows, now + 21 * 3600)
        self.assertEqual(r3["mudaram"], 1)
        self.assertEqual(r3["vistas"], 49)


if __name__ == "__main__":
    unittest.main()


class LinkedInExternoTest(unittest.TestCase):
    def test_detail(self):
        from engine import sources
        page = ('<div class="show-more-less-html__markup relative"><p>Fully remote role. Figma.</p></div>'
                '<code id="applyUrl" style="display: none"><!--"https://www.linkedin.com/jobs/view/externalApply/123?'
                'url=https%3A%2F%2Fjobs%2Elever%2Eco%2Facme%2Fabc%3Fsource%3DLinkedIn&amp;urlHash=xx"--></code>')
        desc, ext = sources.linkedin_detail(page)
        self.assertIn("Fully remote", desc)
        self.assertEqual(ext, "https://jobs.lever.co/acme/abc?source=LinkedIn")
        self.assertEqual(sources.linkedin_detail('<div class="show-more-less-html__markup">x</div>')[1], None)
        j = dict(source="linkedin", url=ext)
        self.assertEqual(score.apply_type(j), "auto")
        self.assertEqual(score.apply_type(dict(source="linkedin", url="https://br.linkedin.com/jobs/view/x-1")),
                         "linkedin")
        j = dict(source="linkedin", title="Product Designer", location="Curitiba, Paraná, Brazil (remoto)",
                 description="This is a fully remote position.")
        self.assertEqual(score.eligibility(j, CFG), "aberta")


def raw_sent(to, subject, body, in_reply_to=None, date_ts=NOW - 300):
    m = EmailMessage()
    m["From"] = "Victor Cavalcante <hello@victorvk.com>"
    m["To"] = to
    m["Subject"] = subject
    m["Message-ID"] = "<sent%s@victorvk.com>" % abs(hash(subject + to))
    m["Date"] = dt.datetime.fromtimestamp(date_ts, dt.timezone.utc).strftime("%a, %d %b %Y %H:%M:%S +0000")
    if in_reply_to:
        m["In-Reply-To"] = in_reply_to
        m["References"] = in_reply_to
    m.set_content(body)
    return m.as_bytes()


class EnviadosPendenciasTest(unittest.TestCase):
    def box(self, msgs):
        return lambda state, now: ([("INBOX", i + 1, r, False) for i, r in enumerate(msgs)], {"INBOX": {"v": 1, "u": 9}})

    def test_already_replied_no_draft(self):
        db = db_with_jobs()
        db.set_setting("mail_state", '{"INBOX": {"v": 1, "u": 0}}')
        q = raw("Iveth <iveth@puzzle.tech>", "Next steps", "Hi Victor, what times work for an interview?",
                msgid="<pz1@puzzle>", date_ts=NOW - 3600)
        ans = raw_sent("iveth@puzzle.tech", "Re: Next steps", "Tuesday 10am works.", in_reply_to="<pz1@puzzle>")
        tg = FakeTG()
        out = mail.run(db, tg, None, now=NOW, mailbox=self.box([q, ans]), smtp=FakeSMTP())
        self.assertEqual(db.query("SELECT COUNT(*) AS n FROM outbox")[0]["n"], 0)
        self.assertEqual(out["emails"].get("ja_respondidos"), 1)

    def test_close_pending_draft(self):
        db = db_with_jobs()
        db.set_setting("mail_state", '{"INBOX": {"v": 1, "u": 0}}')
        q = raw("Rita <rita@acme.com>", "Pergunta", "Olá Victor, qual sua pretensão para a vaga?", msgid="<r1@acme>",
                date_ts=NOW - 3600)
        mail.run(db, FakeTG(), None, now=NOW - 1800, mailbox=self.box([q]), smtp=FakeSMTP())
        self.assertEqual(db.query("SELECT state FROM outbox")[0]["state"], "aguardando")
        ans = raw_sent("rita@acme.com", "Re: Pergunta", "R$ 6.000 PJ.", in_reply_to="<r1@acme>", date_ts=NOW - 600)
        tg = FakeTG()
        out = mail.run(db, tg, None, now=NOW, mailbox=self.box([ans]), smtp=FakeSMTP())
        self.assertEqual(out["ja_respondidos"], 1)
        self.assertEqual(db.query("SELECT state FROM outbox")[0]["state"], "respondido")
        self.assertTrue(any("já respondeu" in s["text"] for s in tg.sent))

    def test_pendencia(self):
        db = db_with_jobs()
        db.set_setting("mail_state", '{"INBOX": {"v": 1, "u": 0}}')
        q = raw("Wise Recruiting <no-reply@hirevue.com>", "Next step: record your video interview",
                "Hi Victor, please record a short video introduction for the Product Designer role at Wise "
                "by December 4. Start here: https://app.hirevue.com/interviews/abc123 . Unsubscribe: https://x.com/unsubscribe")
        tg = FakeTG()
        mail.run(db, tg, None, now=NOW, mailbox=self.box([q]), smtp=FakeSMTP())
        ev = db.query("SELECT * FROM events WHERE kind='pendencia'")
        self.assertEqual(len(ev), 1)
        self.assertEqual(mail.fmt_local(ev[0]["due_ts"]), "sex, 04/12 às 23h59")
        self.assertIn("hirevue.com/interviews/abc123", ev[0]["link"])
        self.assertEqual(ev[0]["job_id"], "w1")
        msg = [s for s in tg.sent if "Pendência" in s["text"]][0]
        self.assertIn("gravar vídeo", msg["text"])
        self.assertEqual(msg["keyboard"][0][0]["callback_data"], "pd:" + ev[0]["email_id"])
        c = lambda *a: mail.classify(parsed(*a), [], now=NOW)[0]  # noqa: E731
        self.assertEqual(c("Gupy <noreply@gupy.io>", "Complete sua candidatura", "Falta responder o questionário da vaga."),
                         "pendencia")

    def test_folders_include_sent(self):
        class M:
            def list(self):
                return "OK", [b'(\\HasNoChildren) "/" "INBOX"', b'(\\HasNoChildren) "/" "Notification"',
                              b'(\\HasNoChildren \\Sent) "/" "Sent"', b'(\\HasNoChildren \\Drafts) "/" "Drafts"',
                              b'(\\HasNoChildren \\Trash) "/" "Trash"', b'(\\HasNoChildren) "/" "Spam"']
        self.assertEqual(mail._folders(M()), (["INBOX", "Notification", "Sent", "Spam"], "Sent"))
