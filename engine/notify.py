"""Avisos no Telegram a partir do Actions, com silêncio de 22h a 7h.
Fora do silêncio (ou urgente) sai na hora; no silêncio fica na tabela notices e o Worker manda às 7h."""
import datetime as dt
import json
import os
import time

import requests

TZ = dt.timezone(dt.timedelta(hours=-3))  # America/Fortaleza (sem horário de verão)


def local_hour(now=None):
    return dt.datetime.fromtimestamp(now or time.time(), TZ).hour


def is_quiet(db, now=None):
    start = int(db.setting("quiet_start") or 22)
    end = int(db.setting("quiet_end") or 7)
    h = local_hour(now)
    return h >= start or h < end if start > end else start <= h < end


class Telegram:
    def __init__(self, token, chat):
        self.token, self.chat = token, chat

    @classmethod
    def from_env(cls):
        tok, chat = os.environ.get("TELEGRAM_BOT_TOKEN"), os.environ.get("TELEGRAM_CHAT_ID")
        return cls(tok, chat) if tok and chat else None

    def send(self, text, keyboard=None, doc=None, doc_name=None):
        """True se o Telegram aceitou."""
        base = "https://api.telegram.org/bot%s/" % self.token
        markup = {"inline_keyboard": keyboard} if keyboard else None
        try:
            if doc:
                data = {"chat_id": self.chat, "caption": text[:1024], "parse_mode": "HTML"}
                if markup:
                    data["reply_markup"] = json.dumps(markup)
                r = requests.post(base + "sendDocument", data=data, timeout=30,
                                  files={"document": (doc_name or "convite.ics", doc.encode("utf-8"), "text/calendar")})
            else:
                body = {"chat_id": self.chat, "text": text[:4096], "parse_mode": "HTML",
                        "link_preview_options": {"is_disabled": True}}
                if markup:
                    body["reply_markup"] = markup
                r = requests.post(base + "sendMessage", json=body, timeout=30)
            return r.ok
        except requests.RequestException:
            return False


class Notifier:
    """Junta avisos de uma rodada. flush() manda o que pode e guarda o resto no D1."""

    def __init__(self, db, tg, now=None, silent=False):
        self.db, self.tg, self.now, self.silent = db, tg, now or time.time(), silent
        self.sent = self.queued = 0
        self.items = []

    def add(self, text, keyboard=None, urgent=False, doc=None, doc_name=None):
        self.items.append(dict(text=text, keyboard=keyboard, urgent=urgent, doc=doc, doc_name=doc_name))

    def flush(self):
        quiet = is_quiet(self.db, self.now)
        for it in self.items:
            if self.silent:
                continue
            if (it["urgent"] or not quiet) and self.tg and self.tg.send(it["text"], it["keyboard"], it["doc"], it["doc_name"]):
                self.sent += 1
                continue
            self.db.query("INSERT INTO notices(ts, urgent, text, keyboard, doc, doc_name) VALUES (?, ?, ?, ?, ?, ?)",
                          [self.now, int(it["urgent"]), it["text"],
                           json.dumps(it["keyboard"]) if it["keyboard"] else None, it["doc"], it["doc_name"]])
            self.queued += 1
        self.items = []
        return dict(enviados=self.sent, na_fila=self.queued)


def ics(uid, start_ts, title, link="", minutes=60, desc=""):
    """Convite .ics (UTC) com alarme de 30 min."""
    f = lambda t: dt.datetime.fromtimestamp(t, dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")  # noqa: E731
    esc = lambda s: (s or "").replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")  # noqa: E731
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//robo-de-vagas//PT", "METHOD:PUBLISH", "BEGIN:VEVENT",
             "UID:%s@robo-de-vagas" % uid, "DTSTAMP:" + f(time.time()), "DTSTART:" + f(start_ts),
             "DTEND:" + f(start_ts + minutes * 60), "SUMMARY:" + esc(title)]
    if link:
        lines += ["LOCATION:" + esc(link), "URL:" + link]
    lines.append("DESCRIPTION:" + esc(((link + "\n") if link else "") + desc))
    lines += ["BEGIN:VALARM", "ACTION:DISPLAY", "DESCRIPTION:" + esc(title), "TRIGGER:-PT30M", "END:VALARM",
              "END:VEVENT", "END:VCALENDAR"]
    return "\r\n".join(lines) + "\r\n"
