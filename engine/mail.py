"""E-mail (entrega 4): lê o Zoho por IMAP, classifica, atualiza status, agenda entrevistas, avisa no Telegram,
rascunha respostas (sem enviar) e envia por SMTP só o que o Vk aprovou com o toque em Enviar.

Regras primeiro (sem custo); o Gemini só entra na dúvida e para escrever rascunhos.
Logs públicos do Actions: só contagens. Nunca imprime remetente, assunto ou texto."""
import datetime as dt
import email
import email.header
import email.policy
import email.utils
import hashlib
import html
import imaplib
import json
import os
import re
import smtplib
import ssl
import time
import unicodedata
from email.message import EmailMessage
from zoneinfo import ZoneInfo

from . import ai
from .d1 import in_list, q
from .notify import TZ, Notifier, ics
from .textnorm import company_key, norm

HOSTS_IMAP = ("imappro.zoho.com", "imap.zoho.com")
HOSTS_SMTP = ("smtppro.zoho.com", "smtp.zoho.com")
DAY = 86400
LOCAL = ZoneInfo("America/Fortaleza")
FIRST_DAYS = 14          # primeira leitura: últimos 14 dias (status), sem avisar o que é velho
FETCH_CAP = 300
MAX_AI = 6               # chamadas ao Gemini por rodada
FOLLOWUP_CAP = 5

# ---------------------------------------------------------------- padrões (vêm da v2, ajustados)
PAD_ENTREVISTA = (r"interview|entrevista|bate-?papo|conversa (inicial|com)|schedule (a|your) (call|time|chat)|"
                  r"agendar|agendamento|book a time|meet (with|the team)|next step.{0,40}call|screening call|chamada|"
                  r"invitation:|convite:|video call|videochamada|phone screen|intro call|chat with")
PAD_AGENDAR = (r"calendly\.com|cal\.com/|availability|disponibilidade|quando (voce|vc) (pode|tem)|select a time|"
               r"book a (time|slot)|schedule a (time|call)|pick a time|horarios? disponive|melhor horario|"
               r"what times? work|let me know (when|what time)|share (your|a few) (times|slots)|escolha um horario")
PAD_TESTE = (r"challenge|desafio|take-?home|assessment|teste tecnico|teste pratico|case (tecnico|pratico)|coderbyte|"
             r"hackerrank|testgorilla|codility|design exercise|exercicio")
PAD_RECUSA = (r"unfortunately|infelizmente|not (be )?moving forward|decided to (move|proceed) (forward )?with other|"
              r"nao (vamos|iremos) (seguir|prosseguir|avancar)|nao seguiremos|outros? candidat|position has been filled|"
              r"vaga (foi )?(encerrada|preenchida)|not selected|nao foi selecionad|regret to inform|we will not be|"
              r"optamos por|decidimos (seguir|prosseguir) com|will not be progressing|pursue other candidates")
PAD_RECEBIDA = (r"thank you for (applying|your application|your interest)|application (received|submitted)|"
                r"recebemos (a |sua )?candidatura|candidatura (recebida|enviada|realizada)|"
                r"obrigad[oa] por (se candidatar|sua candidatura|aplicar)|we.ve received your application|"
                r"thanks for applying")
PAD_PENDENCIA = (r"complete (your|the) (application|profile|candidacy|questionnaire|form|registration)|finish your application|"
                 r"incomplete application|application (is )?incomplete|candidatura (incompleta|pendente)|"
                 r"(complete|finalize|conclua|termine) (a |sua )?(candidatura|inscricao|cadastro)|questionnaire|questionario|"
                 r"survey to complete|formulario (de|para)|video (introdut|de apresenta|interview|introduction|pitch)|"
                 r"record (a|your) (short )?(video|introduction)|grav(e|ar) (um )?video|one-?way (video|interview)|"
                 r"hirevue|willo|spark ?hire|async(hronous)? (video|interview)|"
                 r"next step.{0,60}(complete|fill|answer|record|upload)|proxima etapa.{0,80}(responder|preencher|gravar|completar|enviar)|"
                 r"responda (o|ao|as|aos) (questionario|perguntas|formulario)|action required|acao necessaria")
PAD_PRAZO = r"until|by|before|no later than|deadline|due|expires?|ate o dia|ate dia|ate|prazo|vence"
PAD_OFERTA = r"offer letter|job offer|pleased to offer|happy to offer|proposta (de emprego|de trabalho|formal)|carta oferta"
PAD_ORCAMENTO = (r"orcamento|freela|freelance|quote (for|request)|preciso de um (site|app|layout)|"
                 r"budget for (a|the|my) (project|website)|project inquiry|hire you for")
PAD_VAGA = (r"vaga|position|role|opportunit|oportunidade|application|candidatura|recruit|recrut|hiring|"
            r"processo seletivo|talent|interview|entrevista")
LIXO = (r"newsletter|unsubscribe.{0,40}(digest|weekly)|job alert|alerta de vaga|vagas recomendadas|jobs you may|"
        r"recommended jobs|new jobs for you|linkedin news|webinar|join (our|the) community|comunidade de")
NOREPLY = re.compile(r"no-?reply|noreply|nao-?responda|donotreply|notifications?@|mailer|alerts?@|news(letter)?@|"
                     r"marketing@|digest@|updates?@|info@")
LINK_REUNIAO = re.compile(
    r"https?://(?:[\w-]+\.)?(?:meet\.google\.com|zoom\.us|teams\.microsoft\.com|teams\.live\.com|calendly\.com|cal\.com|"
    r"whereby\.com|app\.gem\.com|goodtime\.io|meet\.jit\.si|webex\.com)/[^\s\"'<>)\]]+")
GENERIC_DOMAINS = {"gmail", "outlook", "hotmail", "yahoo", "icloud", "zoho", "greenhouse", "greenhouse-mail", "lever",
                   "hire", "ashbyhq", "ashby", "workable", "workablemail", "smartrecruiters", "recruitee", "gupy",
                   "linkedin", "jobsoid", "bamboohr", "breezy", "teamtailor", "pinpointhq", "rippling", "calendly",
                   "google", "mail", "email", "notifications", "talent", "careers", "jobs", "recruiting", "us", "eu"}
STATUS_OF = {"entrevista": "entrevista", "agendar": "entrevista", "teste": "teste", "recusa": "recusada",
             "pendencia": "em_analise",
             "recebida": "em_analise", "oferta": "oferta"}
RANK = {"aprovada": 0, "precisa_voce": 0, "listada": 0, "nova": 0, "enviada": 1, "em_analise": 2, "teste": 3,
        "entrevista": 4, "oferta": 5}
APPLIED_JOB = ("aprovada", "precisa_voce", "enviada", "em_analise", "teste", "entrevista", "oferta", "recusada")

# ---------------------------------------------------------------- texto


def dec(h):
    try:
        return str(email.header.make_header(email.header.decode_header(h or "")))
    except Exception:  # noqa: BLE001
        return h or ""


def html_to_text(s):
    s = re.sub(r"(?is)<(script|style).*?</\1>", " ", s)
    s = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</li>|</tr>", "\n", s)
    s = re.sub(r"(?i)<a [^>]*href=\"([^\"]+)\"[^>]*>", r" \1 ", s)
    s = re.sub(r"<[^>]+>", " ", s)
    return re.sub(r"[ \t]+", " ", html.unescape(s))


def body_of(msg):
    texts, htmls, cal = [], [], ""
    for p in msg.walk():
        if p.is_multipart():
            continue
        ct = p.get_content_type()
        try:
            raw = p.get_payload(decode=True) or b""
            s = raw.decode(p.get_content_charset() or "utf-8", "replace")
        except Exception:  # noqa: BLE001
            continue
        if ct == "text/calendar" or (p.get_filename() or "").lower().endswith(".ics"):
            cal = cal or s
        elif ct == "text/plain":
            texts.append(s)
        elif ct == "text/html":
            htmls.append(html_to_text(s))
    return ("\n".join(texts) if texts else "\n".join(htmls))[:30000], cal


QUOTE_HEAD = re.compile(
    r"(?im)^\s*(on .{0,250}?wrote:|em .{0,250}?escreveu:|el .{0,250}?escribi[oó]:|-{2,}\s*original message\s*-{2,}|"
    r"-{2,}\s*mensagem original\s*-{2,}|_{8,}|from:\s.+\n\s*(sent|date|enviado|data):)")


def strip_quotes(text):
    """Tira a parte citada (respostas antigas), que confundia a classificação (caso Puzzle)."""
    m = QUOTE_HEAD.search(text or "")
    if m:
        text = text[:m.start()]
    return "\n".join(l for l in (text or "").splitlines() if not l.lstrip().startswith(">")).strip()


def norm_subject(s):
    s = unicodedata.normalize("NFD", (s or "").lower())
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = re.sub(r"^\s*((re|fwd?|enc|res)\s*:\s*)+", "", s)
    return re.sub(r"\s+", " ", s).strip()


def lang_of(text):
    t = " " + norm(text[:1500]) + " "
    pt = sum(w in t for w in (" voce ", " obrigad", " vaga ", " para ", " com ", " nao ", " entrevista", " ola ", " seu "))
    es = sum(w in t for w in (" usted ", " gracias", " puesto ", " para ", " con ", " entrevista", " hola "))
    en = sum(w in t for w in (" you ", " the ", " thank", " your ", " we ", " interview", " and "))
    if pt >= 3 and pt >= en:
        return "pt"
    if es >= 3 and es > pt and es >= en:
        return "es"
    return "en"


def domain_of(addr):
    return (addr or "").rsplit("@", 1)[-1].lower()


def domain_root(addr):
    """acme.com.br -> acme; mail.acme.io -> acme (ignora domínios genéricos e de ATS)."""
    parts = [p for p in domain_of(addr).split(".") if p]
    labels = [p for p in parts[:-1] if p not in ("com", "co", "org", "net", "gov", "edu", "mail", "email", "e", "m")]
    for p in reversed(labels):
        if p not in GENERIC_DOMAINS and len(p) >= 3:
            return p
    return ""


def first_name(display, addr):
    n = (display or "").strip().strip('"')
    if not n or re.search(r"team|time|careers|recruit|talent|hiring|jobs|rh|people|no.?reply|@", n, re.I):
        return ""
    w = n.split()[0]
    return w if w.isalpha() and len(w) > 1 else ""


def parse_message(raw, folder="INBOX"):
    msg = email.message_from_bytes(raw, policy=email.policy.compat32)
    msgid = (msg.get("Message-ID") or "").strip() or "<%s@sem-id>" % hashlib.sha1(raw[:2000]).hexdigest()
    disp, addr = email.utils.parseaddr(dec(msg.get("From")))
    rt = email.utils.parseaddr(dec(msg.get("Reply-To") or ""))[1]
    try:
        ts = email.utils.parsedate_to_datetime(msg.get("Date")).timestamp()
    except Exception:  # noqa: BLE001
        ts = time.time()
    text, cal = body_of(msg)
    clean = strip_quotes(text)
    subject = dec(msg.get("Subject"))
    tos = " ".join(a.lower() for _, a in email.utils.getaddresses(
        [dec(msg.get(h) or "") for h in ("To", "Cc")]) if a)
    irt = (msg.get("In-Reply-To") or "").strip()
    return dict(msgid=msgid, eid=hashlib.sha1(msgid.encode()).hexdigest()[:16], subject=subject.strip(),
                from_name=disp, from_addr=addr.lower(), reply_to=(rt or addr).lower(), ts=ts, text=clean,
                full=text, ics=cal, refs=" ".join((msg.get("References") or "").split()[-10:]),
                in_reply_to=irt, to=tos, lang=lang_of(subject + "\n" + clean), folder=folder)


# ---------------------------------------------------------------- datas e fusos
TZ_ABBR = {"est": "America/New_York", "edt": "America/New_York", "et": "America/New_York",
           "eastern": "America/New_York", "pst": "America/Los_Angeles", "pdt": "America/Los_Angeles",
           "pt": "America/Los_Angeles", "pacific": "America/Los_Angeles", "cst": "America/Chicago",
           "cdt": "America/Chicago", "ct": "America/Chicago", "central": "America/Chicago",
           "mst": "America/Denver", "mdt": "America/Denver", "gmt": "UTC", "utc": "UTC", "bst": "Europe/London",
           "cet": "Europe/Berlin", "cest": "Europe/Berlin", "wet": "Europe/Lisbon", "west": "Europe/Lisbon",
           "brt": "America/Sao_Paulo", "brasilia": "America/Sao_Paulo", "art": "America/Argentina/Buenos_Aires",
           "ist": "Asia/Kolkata", "sgt": "Asia/Singapore", "aest": "Australia/Sydney"}
MESES = {"jan": 1, "feb": 2, "fev": 2, "mar": 3, "apr": 4, "abr": 4, "may": 5, "mai": 5, "jun": 6, "jul": 7,
         "aug": 8, "ago": 8, "sep": 9, "set": 9, "oct": 10, "out": 10, "nov": 11, "dec": 12, "dez": 12}
_MON = r"(jan|feb|fev|mar|apr|abr|may|mai|jun|jul|aug|ago|sep|set|oct|out|nov|dec|dez)[a-z]*\.?"
_TZ = (r"(?:\s*\(?\s*(?P<tz>gmt\s*[+-]\s*\d{1,2}(?::?\d{2})?|utc\s*[+-]\s*\d{1,2}(?::?\d{2})?|"
       r"horario de brasilia|brasilia|est|edt|et|pst|pdt|pt|cst|cdt|ct|mst|mdt|gmt|utc|bst|cet|cest|wet|west|brt|art|"
       r"ist|sgt|aest|eastern|pacific|central)\b)?")
_TIME = r"(?P<h>\d{1,2})(?:[:h](?P<mi>\d{2}))?\s*(?P<ap>am|pm|a\.m\.|p\.m\.)?\s*(?:h|hs|horas)?"
DATE_RX = [
    # 02/12 [/2026] ... 14h / 14:00 / 2pm
    re.compile(r"\b(?P<d>\d{1,2})/(?P<m>\d{1,2})(?:/(?P<y>\d{2,4}))?\b[^\n]{0,40}?\b" + _TIME + _TZ),
    # December 2(nd)[, 2026] [at] 2:00 pm
    re.compile(r"\b(?P<mon>" + _MON + r")\s+(?P<d>\d{1,2})(?:st|nd|rd|th)?,?(?:\s+(?P<y>\d{4}))?[^\n]{0,30}?\b" + _TIME + _TZ),
    # 2 December / 2 de dezembro [de 2026] [às] 14h
    re.compile(r"\b(?P<d>\d{1,2})(?:st|nd|rd|th)?\s+(?:de\s+)?(?P<mon>" + _MON + r")(?:\s+(?:de\s+)?(?P<y>\d{4}))?[^\n]{0,30}?\b"
               + _TIME + _TZ),
]


def _tzinfo(tok):
    if not tok:
        return None
    t = re.sub(r"\s+", "", tok.lower())
    m = re.match(r"(gmt|utc)([+-])(\d{1,2}):?(\d{2})?", t)
    if m:
        off = int(m.group(3)) * 60 + int(m.group(4) or 0)
        return dt.timezone(dt.timedelta(minutes=off if m.group(2) == "+" else -off))
    name = TZ_ABBR.get(t.replace("horariode", ""))
    return ZoneInfo(name) if name else None


def _next_year(d, mo, h, mi, tz, now):
    base = dt.datetime.fromtimestamp(now, tz)
    for y in (base.year, base.year + 1):
        try:
            cand = dt.datetime(y, mo, d, h, mi, tzinfo=tz)
        except ValueError:
            return None
        if cand.timestamp() >= now - DAY:
            return cand
    return None


def parse_when(text, cal="", now=None):
    """(epoch, fuso_original_ou_None, confirmado). Converte tudo para epoch; quem mostra converte para Teresina."""
    now = now or time.time()
    m = re.search(r"DTSTART(?:;TZID=(?P<tz>[^:;]+))?(?:;VALUE=DATE-TIME)?:(?P<v>\d{8}T\d{4,6})(?P<z>Z)?", cal or "")
    if m:
        v = m.group("v")[:13]
        d = dt.datetime.strptime(v, "%Y%m%dT%H%M")
        if m.group("z"):
            tz = dt.timezone.utc
        else:
            try:
                tz = ZoneInfo(m.group("tz").strip('"')) if m.group("tz") else LOCAL
            except Exception:  # noqa: BLE001
                tz = LOCAL
        return d.replace(tzinfo=tz).timestamp(), getattr(tz, "key", None), True
    t = norm(text or "")[:8000]
    for rx in DATE_RX:
        for m in rx.finditer(t):
            g = m.groupdict()
            try:
                d = int(g["d"])
                mo = int(g["m"]) if g.get("m") else MESES[g["mon"][:3]]
                h = int(g["h"])
                mi = int(g.get("mi") or 0)
            except (KeyError, TypeError, ValueError):
                continue
            ap = (g.get("ap") or "").replace(".", "")
            if ap == "pm" and h < 12:
                h += 12
            elif ap == "am" and h == 12:
                h = 0
            if not (1 <= mo <= 12 and 1 <= d <= 31 and h <= 23 and mi <= 59):
                continue
            if not ap and not g.get("mi") and not re.search(r"\d\s*(h|hs|horas)\b", m.group(0)):
                continue  # "02/12 3 vagas" não é horário
            tz = _tzinfo(g.get("tz"))
            y = g.get("y")
            if y:
                y = int(y) + (2000 if len(y) == 2 else 0)
                try:
                    cand = dt.datetime(y, mo, d, h, mi, tzinfo=tz or LOCAL)
                except ValueError:
                    continue
            else:
                cand = _next_year(d, mo, h, mi, tz or LOCAL, now)
                if not cand:
                    continue
            return cand.timestamp(), (g.get("tz") or None), bool(tz)
    return None, None, False


_DL_RX = [
    re.compile(r"\b(?P<d>\d{1,2})/(?P<m>\d{1,2})(?:/(?P<y>\d{2,4}))?\b"),
    re.compile(r"\b(?P<mon>" + _MON + r")\s+(?P<d>\d{1,2})(?:st|nd|rd|th)?(?:,?\s+(?P<y>\d{4}))?\b"),
    re.compile(r"\b(?P<d>\d{1,2})(?:st|nd|rd|th)?\s+(?:de\s+)?(?P<mon>" + _MON + r")(?:\s+(?:de\s+)?(?P<y>\d{4}))?\b"),
]


def parse_deadline(text, now=None):
    """Prazo de teste/pendência: data (com ou sem hora) logo depois de 'até', 'by', 'deadline'... Sem hora = 23h59."""
    now = now or time.time()
    t = norm(text or "")[:8000]
    for m in re.finditer(r"\b(" + PAD_PRAZO + r")\b", t):
        win = t[m.end():m.end() + 70]
        ts, _, _ = parse_when(win, "", now)
        if ts:
            return ts
        for rx in _DL_RX:
            d = rx.search(win)
            if not d:
                continue
            g = d.groupdict()
            try:
                day = int(g["d"])
                mo = int(g["m"]) if g.get("m") else MESES[g["mon"][:3]]
            except (KeyError, TypeError, ValueError):
                continue
            if not (1 <= mo <= 12 and 1 <= day <= 31):
                continue
            if g.get("y"):
                y = int(g["y"]) + (2000 if len(g["y"]) == 2 else 0)
                try:
                    return dt.datetime(y, mo, day, 23, 59, tzinfo=LOCAL).timestamp()
                except ValueError:
                    continue
            c = _next_year(day, mo, 23, 59, LOCAL, now)
            if c:
                return c.timestamp()
    return None


URL_RX = re.compile(r"https?://[^\s\"'<>)\]]+")
ACTION_RX = re.compile(r"apply|application|candidat|form|video|assessment|questionn|questionar|survey|hirevue|willo|"
                       r"sparkhire|gupy|typeform|forms\.gle|jotform|complete|continue|portal|career|vaga", re.I)
NOT_ACTION_RX = re.compile(r"unsubscribe|descadastr|privacy|privacidade|preferences|terms|linkedin\.com/(in|company)|"
                           r"facebook|twitter|instagram|youtube|\.(png|jpg|gif)", re.I)


def action_link(text):
    urls = [u.rstrip(".,;") for u in URL_RX.findall(text or "")]
    for u in urls:
        if ACTION_RX.search(u) and not NOT_ACTION_RX.search(u):
            return u
    return ""


def pendencia_kind(text):
    t = norm(text or "")
    if re.search(r"video|hirevue|willo|spark ?hire|one-?way", t):
        return "gravar vídeo"
    if re.search(r"questionnaire|questionario|survey|formulario|perguntas", t):
        return "responder questionário"
    if re.search(r"complete|finalize|conclua|termine|incomplet|pendente", t):
        return "completar a candidatura"
    return "fazer a próxima etapa"


def parse_iso(s):
    try:
        d = dt.datetime.fromisoformat(str(s).strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return (d if d.tzinfo else d.replace(tzinfo=LOCAL)).timestamp()


DIAS = ("seg", "ter", "qua", "qui", "sex", "sáb", "dom")


def fmt_local(ts):
    d = dt.datetime.fromtimestamp(ts, TZ)
    hh = "%dh%02d" % (d.hour, d.minute) if d.minute else "%dh" % d.hour
    return "%s, %02d/%02d às %s" % (DIAS[d.weekday()], d.day, d.month, hh)


def slots(now, n=3):
    """Próximos dias úteis às 10h, 14h e 10h (horário de Brasília)."""
    out, d, hours = [], dt.datetime.fromtimestamp(now, TZ), (10, 14, 10, 14)
    while len(out) < n:
        d = d + dt.timedelta(days=1)
        if d.weekday() >= 5:
            continue
        out.append(d.replace(hour=hours[len(out)], minute=0, second=0, microsecond=0))
    return out


def fmt_slots(sl, lang):
    if lang == "pt":
        return "\n".join("- %s, %02d/%02d às %dh" % (DIAS[s.weekday()], s.day, s.month, s.hour) for s in sl)
    if lang == "es":
        dias = ("lun", "mar", "mié", "jue", "vie", "sáb", "dom")
        return "\n".join("- %s %02d/%02d a las %d:00" % (dias[s.weekday()], s.day, s.month, s.hour) for s in sl)
    return "\n".join("- %s at %d:00 %s" % (s.strftime("%a, %b %-d"), s.hour % 12 or 12, "AM" if s.hour < 12 else "PM")
                     for s in sl)


# ---------------------------------------------------------------- classificação
def classify(m, rules, gem_budget=None, gem=None, now=None):
    """Classe do e-mail ('' = ignorar). Ordem: correções do Vk > regras > Gemini na dúvida."""
    frm, subj = m["from_addr"], norm_subject(m["subject"])
    for r in rules:
        if (r["kind"] == "from" and r["pattern"] == frm) or \
           (r["kind"] == "from_subject" and r["pattern"] == frm + "|" + subj) or \
           (r["kind"] == "domain" and r["pattern"] == domain_of(frm)):
            return r["class"], "regra"
    t = norm(m["subject"] + " \n " + m["text"][:6000])
    human = not NOREPLY.search(frm)
    if re.search(LIXO, t) and not m["ics"]:
        return "spam", "regra"
    if re.search(PAD_RECUSA, t):
        return "recusa", "regra"
    if re.search(PAD_OFERTA, t):
        return "oferta", "regra"
    if re.search(PAD_PENDENCIA, t) and not m["ics"] and not re.search(PAD_TESTE, norm(m["subject"])):
        return "pendencia", "regra"
    interview = re.search(PAD_ENTREVISTA, t)
    if m["ics"] and (interview or re.search(PAD_VAGA, t) or LINK_REUNIAO.search(m["ics"])):
        return "entrevista", "regra"
    if interview and re.search(PAD_VAGA + "|" + PAD_ENTREVISTA, t):
        when = parse_when(m["text"], "", now)[0]
        if when and (LINK_REUNIAO.search(m["text"]) or re.search(r"confirm|agendad|scheduled|marcad", t)) \
                and not re.search(PAD_AGENDAR, t.replace("calendly.com", "")):
            return "entrevista", "regra"
        if re.search(PAD_AGENDAR, t) or human:
            return "agendar", "regra"
    if re.search(PAD_TESTE, t) and re.search(PAD_VAGA, t):
        return "teste", "regra"
    if re.search(PAD_RECEBIDA, t):
        return "recebida", "regra"
    if human and re.search(PAD_ORCAMENTO, t) and not re.search(PAD_VAGA, t):
        return "orcamento", "regra"
    if human and re.search(PAD_VAGA, t):
        if gem and gem_budget is not None and gem_budget[0] > 0:
            gem_budget[0] -= 1
            d = ai.mail_classify(gem, m["from_addr"], m["subject"], m["text"],
                                 dt.datetime.fromtimestamp(now or time.time(), TZ).strftime("%Y-%m-%d"))
            if d:
                m["_ai"] = d
                return d["classe"], "ia"
        if "?" in m["text"][:3000]:
            return "recrutador", "regra"
    return "", ""


# ---------------------------------------------------------------- vaga do e-mail
def load_jobs(db, now):
    rows = db.query("SELECT id, company, company_key, title, status FROM jobs WHERE status IN %s AND status_ts >= ?"
                    % in_list(APPLIED_JOB), [now - 150 * DAY])
    ids = {r["id"] for r in rows}
    extra = db.query("SELECT j.id, j.company, j.company_key, j.title, j.status FROM applications a JOIN jobs j "
                     "ON j.id = a.job_id WHERE a.ts >= ?", [now - 150 * DAY])
    rows += [r for r in extra if r["id"] not in ids]
    apps = {r["job_id"]: r for r in db.query("SELECT id, job_id, state FROM applications WHERE ts >= ?",
                                             [now - 150 * DAY])}
    return rows, apps


def match_job(jobs, m):
    """Vaga mais provável: domínio do remetente, depois nome da empresa no remetente/assunto/texto."""
    root = domain_root(m["from_addr"])
    head = norm(" ".join((m["from_name"], m["from_addr"], m["subject"])))
    body = norm(m["text"][:3000])
    squash = lambda s: re.sub(r"[^a-z0-9]", "", s)  # noqa: E731
    head_s, body_s = squash(head), squash(body)
    best, score = None, 0
    for j in jobs:
        ck = j["company_key"] or company_key(j["company"])
        if len(ck) < 3:
            continue
        s = 0
        if root and (root == ck or (len(root) >= 4 and (ck.startswith(root) or root.startswith(ck)))):
            s = 100
        elif len(ck) >= 6 and ck in head_s:
            s = 80
        elif len(ck) >= 6 and ck in body_s:
            s = 50
        else:
            name = re.sub(r"[^a-z0-9 ]", " ", norm(j["company"]))
            name = re.sub(r"\s+", " ", name).strip()
            if len(name) >= 3 and re.search(r"\b" + re.escape(name) + r"\b", head):
                s = 70
            elif len(name) >= 4 and re.search(r"\b" + re.escape(name) + r"\b", body):
                s = 40
        if s and norm(j["title"] or "")[:25] and norm(j["title"])[:25] in norm(m["subject"] + " " + m["text"][:2000]):
            s += 10
        s += RANK.get(j["status"], 0)  # empata: a mais avançada no processo
        if s > score:
            best, score = j, s
    return best


def company_from(m):
    root = domain_root(m["from_addr"])
    if root:
        return root.capitalize()
    n = (m["from_name"] or "").strip()
    return re.sub(r"\s*(via|at|from)\s.*$", "", n, flags=re.I)[:60] or domain_of(m["from_addr"])


def new_status(cur, new):
    """Recusa sempre vale; depois de recusa, só entrevista/teste/oferta voltam (caso Puzzle); senão, só avança."""
    if not new or new == cur:
        return None
    if new == "recusada":
        return new
    if cur == "recusada":
        return new if new in ("entrevista", "teste", "oferta") else None
    return new if RANK.get(new, 0) > RANK.get(cur, 0) else None


# ---------------------------------------------------------------- rascunhos
TEMPLATES = {
    "agendar": {
        "pt": "{saud}\n\nObrigado pelo convite, fico feliz em seguir no processo. Tenho disponibilidade nestes horários "
              "(horário de Brasília, UTC−3):\n{slots}\n\nSe nenhum funcionar, me diga outros que eu me ajusto.",
        "en": "{saud}\n\nThank you for the invitation, I'm glad to move forward. I'm available at these times "
              "(Brasília time, UTC−3):\n{slots}\n\nIf none of these work, just let me know and I'll adjust.",
        "es": "{saud}\n\nGracias por la invitación, me alegra seguir en el proceso. Tengo disponibilidad en estos "
              "horarios (hora de Brasilia, UTC−3):\n{slots}\n\nSi ninguno funciona, avísame y me ajusto.",
    },
    "resposta": {
        "pt": "{saud}\n\nObrigado pelo contato! Recebi sua mensagem e te respondo com os detalhes em seguida.",
        "en": "{saud}\n\nThank you for reaching out! I got your message and will follow up with the details shortly.",
        "es": "{saud}\n\n¡Gracias por el contacto! Recibí tu mensaje y te respondo con los detalles en seguida.",
    },
    "followup": {
        "pt": "{saud}\n\nTudo bem? Me candidatei para a vaga de {cargo} há alguns dias e queria saber se há novidades "
              "sobre o processo. Sigo com muito interesse na oportunidade e à disposição para conversar.\n\nObrigado!",
        "en": "{saud}\n\nI hope you're doing well. I applied for the {cargo} role a little while ago and wanted to check "
              "if there are any updates on the process. I'm still very interested and happy to talk anytime.\n\nThank you!",
        "es": "{saud}\n\n¿Cómo estás? Me postulé al puesto de {cargo} hace algunos días y quería saber si hay novedades "
              "sobre el proceso. Sigo muy interesado y a disposición para conversar.\n\n¡Gracias!",
    },
}


def greeting(name, lang):
    if lang == "pt":
        return "Olá, %s!" % name if name else "Olá!"
    if lang == "es":
        return "Hola, %s:" % name if name else "Hola:"
    return "Hi %s," % name if name else "Hello,"


def build_draft(kind, m, lang, perfil, gem, budget, now, cal_link="", cargo=""):
    """(texto, aviso). aviso = números que não estão no super contexto nem no e-mail."""
    saud = greeting(first_name(m.get("from_name"), m.get("from_addr")), lang)
    sl = fmt_slots(slots(now), lang)
    extra = ""
    if kind == "agendar":
        extra = ("- Querem marcar uma conversa: proponha exatamente estes horários (horário de Brasília, UTC−3):\n%s\n" % sl)
        if cal_link:
            extra += "- Ofereça também este link para escolherem um horário: %s\n" % cal_link
        extra += "- Se o e-mail traz um link de agenda (Calendly etc.), agradeça e diga que vai escolher um horário pelo link.\n"
    body = None
    if gem and kind in ("agendar", "resposta") and budget[0] > 0:
        budget[0] -= 1
        body = ai.mail_draft(gem, perfil, m["from_addr"], m["subject"], m["text"], lang, saud, extra)
    if not body:
        tpl = TEMPLATES[kind].get(lang) or TEMPLATES[kind]["en"]
        body = tpl.format(saud=saud, slots=sl, cargo=cargo or ("a vaga" if lang == "pt" else "the"))
        if kind == "agendar" and cal_link:
            body += ("\n\nSe preferir, escolha um horário aqui: " if lang == "pt" else
                     "\n\nIf easier, you can pick a time here: ") + cal_link
    bad = ai.unverified_numbers(body, perfil, m.get("text"), m.get("subject"), sl, cal_link)
    return body.strip(), ("confira: " + ", ".join(bad[:4]) + " não está no super contexto") if bad else ""


def signature(db):
    return (db.setting("mail_signature") or "Victor Cavalcante").strip()


# ---------------------------------------------------------------- Telegram
E = html.escape


def kb_draft(oid, eid=None):
    rows = [[{"text": "✉️ Enviar", "callback_data": "ob:s:%d" % oid},
             {"text": "✏️ Editar", "callback_data": "ob:e:%d" % oid},
             {"text": "🗑 Ignorar", "callback_data": "ob:i:%d" % oid}]]
    if eid:
        rows.append([{"text": "Classifiquei errado", "callback_data": "mc:%s" % eid}])
    return rows


def kb_wrong(eid):
    return [[{"text": "Classifiquei errado", "callback_data": "mc:%s" % eid}]]


def draft_text(title, m, to, body, note):
    snip = re.sub(r"\s+", " ", m["text"])[:350]
    parts = [title, "<i>%s</i>" % E(m["subject"][:120]), "", "“%s”" % E(snip), "", "<b>Rascunho para %s:</b>" % E(to),
             "<blockquote>%s</blockquote>" % E(body[:2500])]
    if note:
        parts.append("⚠️ " + E(note))
    return "\n".join(parts)


# ---------------------------------------------------------------- processamento
class Ctx:
    def __init__(self, db, notifier, gem=None, now=None, perfil="", first=False):
        self.db, self.n, self.gem, self.now = db, notifier, gem, now or time.time()
        self.perfil = perfil
        self.first = first
        self.budget = [MAX_AI]
        self.rules = db.query("SELECT kind, pattern, class FROM mail_rules ORDER BY id DESC")
        self.jobs, self.apps = load_jobs(db, self.now)
        self.cal_link = db.setting("cal_link") or ""
        self.count = {}
        self.sent = db.query("SELECT msgid, ts, to_addrs, subject_norm, refs FROM sent_mail WHERE ts >= ?",
                             [self.now - 60 * DAY])


def already_replied(ctx, msgid, to_addr, subject, after_ts):
    """Você já respondeu pelo Zoho? (mesma thread ou mesmo destinatário + mesmo assunto depois do e-mail)"""
    to_addr, subj = (to_addr or "").lower(), norm_subject(subject)
    for s in ctx.sent:
        if (s["ts"] or 0) < (after_ts or 0) - 60:
            continue
        if msgid and msgid in (s["refs"] or ""):
            return True
        if to_addr and to_addr in (s["to_addrs"] or "") and subj and subj == s["subject_norm"]:
            return True
    return False


def wrote_to(ctx, to_addr, after_ts):
    to_addr = (to_addr or "").lower()
    return any(to_addr and to_addr in (s["to_addrs"] or "") and (s["ts"] or 0) >= after_ts for s in ctx.sent)


def record_sent(ctx, m):
    row = dict(msgid=m["msgid"], ts=m["ts"], to_addrs=m.get("to", ""), subject_norm=norm_subject(m["subject"]),
               refs=(m.get("refs", "") + " " + m.get("in_reply_to", "")).strip()[-1500:])
    if any(s["msgid"] == row["msgid"] for s in ctx.sent):
        return
    ctx.db.query("INSERT OR IGNORE INTO sent_mail (msgid, ts, to_addrs, subject_norm, refs) VALUES (?, ?, ?, ?, ?)",
                 [row["msgid"], row["ts"], row["to_addrs"], row["subject_norm"], row["refs"]])
    ctx.sent.append(row)
    ctx.count["enviados_lidos"] = ctx.count.get("enviados_lidos", 0) + 1


def close_replied(ctx):
    """Rascunho esperando que você já respondeu direto no Zoho: sai da fila sem enviar nada."""
    rows = ctx.db.query("SELECT o.id, o.to_addr, o.subject, o.in_reply_to, o.kind, o.created_ts, e.ts AS ets "
                        "FROM outbox o LEFT JOIN emails e ON e.id = o.email_id WHERE o.state IN ('aguardando','erro')")
    closed = []
    for r in rows:
        after = r["ets"] if r["kind"] != "followup" else r["created_ts"]
        if already_replied(ctx, r["in_reply_to"], r["to_addr"], r["subject"], after or 0) or \
                (r["kind"] == "followup" and wrote_to(ctx, r["to_addr"], r["created_ts"] or 0)):
            ctx.db.query("UPDATE outbox SET state='respondido', updated_ts=? WHERE id=?", [ctx.now, r["id"]])
            closed.append(r)
    if closed:
        ctx.n.add("✔️ Você já respondeu pelo Zoho, descartei %d rascunho(s): %s" % (
            len(closed), ", ".join("#%d %s" % (r["id"], E(r["to_addr"] or "")) for r in closed[:6])))
    return len(closed)


def _set_job_status(ctx, job, status, st):
    ns = new_status(job["status"], status)
    if not ns:
        return
    st.append("UPDATE jobs SET status=%s, status_ts=%s WHERE id=%s" % (q(ns), q(ctx.now), q(job["id"])))
    job["status"] = ns
    app = ctx.apps.get(job["id"])
    if app:
        st.append("UPDATE applications SET state=%s, state_ts=%s WHERE id=%s" % (q(ns), q(ctx.now), q(app["id"])))
        app["state"] = ns
    else:
        st.append("INSERT INTO applications (job_id, ts, channel, via, state, state_ts, origin) VALUES "
                  "(%s, %s, 'manual', 'cold', %s, %s, 'v3-email')" % (q(job["id"]), q(ctx.now), q(ns), q(ctx.now)))
        ctx.apps[job["id"]] = dict(id=None, job_id=job["id"], state=ns)


def _placeholder_job(ctx, m, cls, st):
    """E-mail de processo de uma empresa que não está no banco: cria a vaga para a linha do tempo."""
    jid = "mail:" + m["eid"]
    comp = company_from(m)
    status = STATUS_OF[cls]
    st.append("INSERT OR IGNORE INTO jobs (id, source, company, company_key, title, url, status, status_ts, first_seen, "
              "last_seen, origin) VALUES (%s, 'email', %s, %s, %s, '', %s, %s, %s, %s, 'v3-email')" % (
                  q(jid), q(comp), q(company_key(comp)), q(m["subject"][:120]), q(status), q(ctx.now), q(ctx.now),
                  q(ctx.now)))
    st.append("INSERT INTO applications (job_id, ts, channel, via, state, state_ts, origin) VALUES "
              "(%s, %s, 'manual', 'cold', %s, %s, 'v3-email')" % (q(jid), q(m["ts"]), q(status), q(ctx.now)))
    job = dict(id=jid, company=comp, company_key=company_key(comp), title=m["subject"][:120], status=status)
    ctx.jobs.append(job)
    ctx.apps[jid] = dict(id=None, job_id=jid, state=status)
    return job


def make_draft(ctx, kind, m, job, cls_title):
    if kind in ("resposta", "agendar") and already_replied(ctx, m["msgid"], m["reply_to"], m["subject"], m["ts"]):
        ctx.count["ja_respondidos"] = ctx.count.get("ja_respondidos", 0) + 1
        return None
    lang = m["lang"]
    body, note = build_draft(kind, m, lang, ctx.perfil, ctx.gem, ctx.budget, ctx.now, ctx.cal_link,
                             cargo=(job or {}).get("title") or "")
    full = body + "\n\n" + signature(ctx.db)
    subj = m["subject"] if re.match(r"(?i)^\s*re\s*:", m["subject"]) else "Re: " + m["subject"]
    refs = (m.get("refs", "") + " " + m["msgid"]).strip()
    r = ctx.db.query("INSERT INTO outbox (kind, job_id, email_id, to_addr, subject, body, state, created_ts, updated_ts, "
                     "lang, in_reply_to, refs, note) VALUES (?, ?, ?, ?, ?, ?, 'aguardando', ?, ?, ?, ?, ?, ?) RETURNING id",
                     [kind, (job or {}).get("id"), m["eid"], m["reply_to"], subj[:200], full, ctx.now, ctx.now, lang,
                      m["msgid"], refs[-900:], note or None])
    oid = r[0]["id"]
    ctx.n.add(draft_text(cls_title, m, m["reply_to"], full, note), kb_draft(oid, m["eid"]))
    ctx.count["rascunhos"] = ctx.count.get("rascunhos", 0) + 1
    return oid


def process(ctx, m, cls, how):
    """Grava o e-mail relevante, atualiza vaga/candidatura, agenda e monta o aviso."""
    st = []
    ctx.count[cls] = ctx.count.get(cls, 0) + 1
    if cls == "spam":
        return
    recent = m["ts"] >= ctx.now - 2 * DAY and not ctx.first and not m.get("_first")
    job = match_job(ctx.jobs, m) if cls in STATUS_OF or cls in ("recrutador",) else None
    if not job and cls in ("entrevista", "teste", "recusa", "oferta", "agendar", "recebida", "pendencia"):
        job = _placeholder_job(ctx, m, cls, st)
    comp = (job or {}).get("company") or company_from(m)
    due, tzo, confirmed, link = None, None, False, ""
    if cls in ("entrevista", "teste"):
        due, tzo, confirmed = parse_when(m["text"], m["ics"], ctx.now)
        ai_d = m.get("_ai") or {}
        if not due and cls == "entrevista" and ctx.gem and ctx.budget[0] > 0 and not ai_d:
            ctx.budget[0] -= 1
            ai_d = ai.mail_classify(ctx.gem, m["from_addr"], m["subject"], m["text"],
                                    dt.datetime.fromtimestamp(ctx.now, TZ).strftime("%Y-%m-%d")) or {}
        if not due:
            due = parse_iso(ai_d.get("quando" if cls == "entrevista" else "prazo") or "")
            confirmed = bool(due)
        lk = LINK_REUNIAO.search(m["ics"] + " " + m["text"])
        link = lk.group(0) if lk else ""
        if cls == "teste":
            if not due or due < ctx.now:
                due = parse_deadline(m["text"], ctx.now) or due
            link = link or action_link(m["full"])
    if cls == "pendencia":
        due = parse_deadline(m["text"], ctx.now)
        link = action_link(m["full"])
    st.append("INSERT OR IGNORE INTO emails (id, ts, from_addr, subject, snippet, class, company, job_id, thread_ref, "
              "msgid, refs, reply_to, lang, due_ts, link) VALUES (%s)" % ", ".join(q(v) for v in (
                  m["eid"], m["ts"], m["from_addr"], m["subject"][:200], re.sub(r"\s+", " ", m["text"])[:1200], cls,
                  comp, (job or {}).get("id"), m["msgid"], m["msgid"], m.get("refs", "")[-900:], m["reply_to"],
                  m["lang"], due, link)))
    if job and cls in STATUS_OF:
        _set_job_status(ctx, job, STATUS_OF[cls], st)
    if cls in ("entrevista", "teste", "recusa", "recebida", "oferta", "pendencia"):
        kind = {"recusa": "recusa", "recebida": "recebida"}.get(cls, cls)
        st.append("INSERT INTO events (job_id, kind, ts, due_ts, link, note, email_id, origin, company, title) VALUES "
                  "(%s, %s, %s, %s, %s, %s, %s, 'v3-email', %s, %s)" % (
                      q((job or {}).get("id")), q(kind), q(m["ts"]), q(due), q(link), q(m["subject"][:140]),
                      q(m["eid"]), q(comp), q((job or {}).get("title"))))
    if cls == "orcamento":
        st.append("INSERT INTO leads (name, source, stage, notes, created_ts, updated_ts) VALUES (%s, 'email', 'new', %s, "
                  "%s, %s)" % (q(m["from_name"] or m["from_addr"]), q(m["subject"][:200]), q(ctx.now), q(ctx.now)))
    ctx.db.exec_many(st)

    # avisos (primeira leitura: só entrevista futura)
    future = bool(due and due > ctx.now)
    title = E(comp) + ((" · " + E((job or {}).get("title") or "")[:60]) if job and job.get("title") and
                       not job["id"].startswith("mail:") else "")
    if cls == "entrevista" and (recent or future):
        when = fmt_local(due) + " (Teresina)" if due else "data no e-mail"
        if due and tzo and not str(tzo).lower().startswith(("bra", "horario", "america/sao", "america/fortaleza")):
            when += " · no e-mail: %s" % E(str(tzo).upper() if len(str(tzo)) < 8 else str(tzo))
        elif due and not confirmed and m["lang"] != "pt":
            when += " · confira o fuso"
        txt = "📅 <b>Entrevista: %s</b>\n%s%s\n<i>%s</i>" % (title, when, ("\n" + E(link)) if link else "",
                                                            E(m["subject"][:120]))
        doc = ics(m["eid"], due, "Entrevista: " + comp, link, desc=m["subject"]) if future else None
        ctx.n.add(txt, kb_wrong(m["eid"]), urgent=True, doc=doc, doc_name="entrevista.ics" if doc else None)
    elif not recent:
        return
    elif cls == "agendar" and NOREPLY.search(m["reply_to"]):
        lk = LINK_REUNIAO.search(m["text"])
        ctx.n.add("🗓 <b>%s quer marcar entrevista</b>\nEscolha o horário pelo link%s\n<i>%s</i>" % (
            title, (":\n" + E(lk.group(0))) if lk else " do e-mail.", E(m["subject"][:120])), kb_wrong(m["eid"]),
            urgent=False)
    elif cls == "agendar":
        make_draft(ctx, "agendar", m, job, "🗓 <b>%s quer marcar entrevista</b>" % title)
    elif cls == "recrutador":
        make_draft(ctx, "resposta", m, job, "💬 <b>%s escreveu e espera resposta</b>" % title)
    elif cls == "teste":
        ctx.n.add("🧪 <b>Teste: %s</b>\nPrazo: %s%s\n<i>%s</i>" % (
            title, fmt_local(due) if due else "veja o e-mail", ("\n" + E(link)) if link else "", E(m["subject"][:120])),
            [[{"text": "✅ Feito", "callback_data": "pd:%s" % m["eid"]}], kb_wrong(m["eid"])[0]])
    elif cls == "pendencia":
        ctx.n.add("📝 <b>Pendência: %s</b>\nO que fazer: %s\nPrazo: %s%s\n<i>%s</i>" % (
            title, pendencia_kind(m["subject"] + " " + m["text"]), fmt_local(due) if due else "veja o e-mail",
            ("\n" + E(link)) if link else "", E(m["subject"][:120])),
            [[{"text": "✅ Feito", "callback_data": "pd:%s" % m["eid"]}], kb_wrong(m["eid"])[0]])
    elif cls == "oferta":
        ctx.n.add("🎉 <b>Proposta: %s</b>\n<i>%s</i>" % (title, E(m["subject"][:120])), kb_wrong(m["eid"]), urgent=True)
    elif cls == "orcamento":
        ctx.n.add("💼 <b>Pedido de orçamento</b> de %s\n<i>%s</i>\nLead criado no Freelance. Responda o cliente." % (
            E(m["from_name"] or m["from_addr"]), E(m["subject"][:120])), kb_wrong(m["eid"]))


def handle_messages(ctx, msgs):
    seen = {r["id"] for r in ctx.db.query("SELECT id FROM emails WHERE id IN %s" % in_list([m["eid"] for m in msgs]))} \
        if msgs else set()
    me = (os.environ.get("ZOHO_USER") or "hello@victorvk.com").lower()
    for m in msgs:  # o que você enviou (pasta Enviados) entra antes, para não rascunhar o que já foi respondido
        if m["from_addr"] == me:
            record_sent(ctx, m)
    for m in sorted(msgs, key=lambda x: x["ts"]):
        if m["eid"] in seen or m["from_addr"] == me:
            continue
        seen.add(m["eid"])
        cls, how = classify(m, ctx.rules, ctx.budget, ctx.gem, ctx.now)
        if cls:
            process(ctx, m, cls, how)
    return ctx.count


def redo_corrected(ctx):
    """E-mail que o Vk corrigiu para recrutador/agendar no Telegram: monta o rascunho agora."""
    rows = ctx.db.query("SELECT * FROM emails WHERE redo = 1 LIMIT 5")
    for r in rows:
        m = dict(eid=r["id"], msgid=r["msgid"] or r["thread_ref"] or "", subject=r["subject"] or "",
                 from_name="", from_addr=r["from_addr"] or "", reply_to=r["reply_to"] or r["from_addr"] or "",
                 text=r["snippet"] or "", refs=r["refs"] or "", lang=r["lang"] or "en", ts=r["ts"], ics="")
        job = next((j for j in ctx.jobs if j["id"] == r["job_id"]), None)
        kind = "agendar" if r["class"] == "agendar" else "resposta"
        ctx.db.query("UPDATE emails SET redo = 0 WHERE id = ?", [r["id"]])
        make_draft(ctx, kind, m, job, ("🗓 <b>%s quer marcar entrevista</b>" if kind == "agendar"
                                       else "💬 <b>%s espera resposta</b>") % E(r["company"] or "?"))
    return len(rows)


def followups(ctx):
    """1x por dia: 10 dias sem resposta = rascunho de cobrança (se há e-mail de pessoa); 21 dias = sem retorno."""
    today = dt.datetime.fromtimestamp(ctx.now, TZ).strftime("%Y-%m-%d")
    if ctx.db.setting("followup_date") == today or dt.datetime.fromtimestamp(ctx.now, TZ).hour < 9:
        return dict(followups=0)
    fdays = int(ctx.db.setting("followup_days") or 10)
    ndays = int(ctx.db.setting("no_reply_days") or 21)
    apps = ctx.db.query(
        "SELECT a.id, a.job_id, a.ts, a.state, j.company, j.title FROM applications a JOIN jobs j ON j.id = a.job_id "
        "WHERE a.state IN ('enviada','em_analise') AND a.ts <= ? AND a.ts >= ?",
        [ctx.now - fdays * DAY, ctx.now - 120 * DAY])
    done = {r["job_id"] for r in ctx.db.query("SELECT DISTINCT job_id FROM outbox WHERE kind = 'followup'")}
    lastev = {r["job_id"]: r["t"] for r in ctx.db.query(
        "SELECT job_id, MAX(ts) AS t FROM events WHERE kind IN ('entrevista','teste','oferta') GROUP BY job_id")}
    made = stale = 0
    for a in apps:
        if lastev.get(a["job_id"], 0) > a["ts"]:
            continue
        if a["ts"] <= ctx.now - ndays * DAY:
            ctx.db.query("UPDATE applications SET state='sem_retorno', state_ts=? WHERE id=?", [ctx.now, a["id"]])
            stale += 1
            continue
        if a["job_id"] in done or made >= FOLLOWUP_CAP:
            continue
        em = [e for e in ctx.db.query("SELECT * FROM emails WHERE job_id = ? ORDER BY ts DESC LIMIT 5", [a["job_id"]])
              if not NOREPLY.search(e["reply_to"] or e["from_addr"] or "")]
        if not em:
            continue
        e = em[0]
        if wrote_to(ctx, e["reply_to"] or e["from_addr"], a["ts"]):
            continue
        m = dict(eid=e["id"], msgid=e["msgid"] or "", subject=e["subject"] or a["title"] or "", from_name="",
                 from_addr=e["from_addr"], reply_to=e["reply_to"] or e["from_addr"], text="", refs=e["refs"] or "",
                 lang=e["lang"] or "en", ts=e["ts"], ics="")
        make_draft(ctx, "followup", m, dict(id=a["job_id"], title=a["title"]),
                   "⏳ <b>%s: %d dias sem resposta</b>" % (E(a["company"] or "?"), int((ctx.now - a["ts"]) // DAY)))
        made += 1
    ctx.db.set_setting("followup_date", today)
    return dict(followups=made, sem_retorno=stale)


# ---------------------------------------------------------------- IMAP / SMTP
def creds():
    u, p = os.environ.get("ZOHO_USER"), os.environ.get("ZOHO_APP_PASSWORD")
    if not u or not p:
        raise RuntimeError("faltam os secrets ZOHO_USER / ZOHO_APP_PASSWORD")
    return u, p


def imap_login():
    u, p = creds()
    last = None
    for host in HOSTS_IMAP:
        try:
            M = imaplib.IMAP4_SSL(host, 993, ssl_context=ssl.create_default_context(), timeout=60)
            M.login(u, p)
            return M, host
        except Exception as e:  # noqa: BLE001
            last = e
    raise RuntimeError("IMAP: login falhou (%s)" % type(last).__name__)


SKIP_FOLDERS = re.compile(r"(?i)^(sent|enviados?|sent items|sent messages|drafts?|rascunhos?|trash|lixeira|deleted|"
                          r"outbox|caixa de sa[ií]da|templates?|modelos?)$")


def _folders(M):
    """Todas as pastas que recebem e-mail (Inbox, Notification, Newsletter, Spam e as que você criar).
    Enviados também (só para saber o que você já respondeu). Fica de fora Rascunhos, Lixeira, Saída e Modelos."""
    out, sent = ["INBOX"], None
    try:
        typ, lst = M.list()
        for l in lst or []:
            s = l.decode("utf-8", "replace")
            name = s.split(' "/" ')[-1].strip().strip('"')
            flags = s.split(")")[0]
            if not sent and (re.search(r"\\Sent", flags) or re.fullmatch(r"(?i)sent|enviados|sent items|sent messages",
                                                                          name)):
                sent = name
            if name == sent:  # Enviados: lido para saber o que você já respondeu
                out.append(name)
                continue
            if name.upper() == "INBOX" or re.search(r"\\(Sent|Drafts|Trash|Noselect)", flags, re.I):
                continue
            leaf = name.split("/")[-1]
            if SKIP_FOLDERS.match(leaf):
                continue
            out.append(name)
    except Exception:  # noqa: BLE001
        pass
    return out, sent


def fetch_new(M, state, now):
    """[(folder, uid, raw, first)] e o estado novo. Só UIDs novos desde a última rodada."""
    out, new_state = [], dict(state or {})
    folders, _ = _folders(M)
    for folder in folders:
        try:
            typ, _ = M.select('"%s"' % folder, readonly=True)
            if typ != "OK":
                continue
            uv = int((M.response("UIDVALIDITY")[1] or [b"0"])[0])
            st = (state or {}).get(folder)
            first = not st or st.get("v") != uv
            if first:
                since = (dt.datetime.utcfromtimestamp(now) - dt.timedelta(days=FIRST_DAYS)).strftime("%d-%b-%Y")
                typ, d = M.uid("search", None, "SINCE", since)
            else:
                typ, d = M.uid("search", None, "UID", "%d:*" % (st["u"] + 1))
            uids = sorted(int(x) for x in (d[0].split() if d and d[0] else []))
            if not first:
                uids = [u for u in uids if u > st["u"]]
            uids = uids[-FETCH_CAP:]
            for u in uids:
                typ, md = M.uid("fetch", str(u), "(BODY.PEEK[])")
                raw = next((x[1] for x in md or [] if isinstance(x, tuple)), None)
                if raw:
                    out.append((folder, u, raw, first))
            top = max(uids) if uids else (0 if first else st["u"])
            if first:
                try:
                    typ, sd = M.status('"%s"' % folder, "(UIDNEXT)")
                    nxt = int(re.search(rb"UIDNEXT (\d+)", sd[0]).group(1))
                    top = max(top, nxt - 1)
                except Exception:  # noqa: BLE001
                    pass
            new_state[folder] = {"v": uv, "u": top}
        except Exception as e:  # noqa: BLE001
            print("imap: pasta falhou (%s)" % type(e).__name__)
    return out, new_state


def smtp_login():
    u, p = creds()
    last = None
    for host in HOSTS_SMTP:
        try:
            S = smtplib.SMTP_SSL(host, 465, context=ssl.create_default_context(), timeout=60)
            S.login(u, p)
            return S, host
        except Exception as e:  # noqa: BLE001
            last = e
    raise RuntimeError("SMTP: login falhou (%s)" % type(last).__name__)


def build_mime(row, sender):
    msg = EmailMessage()
    msg["From"] = email.utils.formataddr(("Victor Cavalcante", sender))
    msg["To"] = row["to_addr"]
    msg["Subject"] = row["subject"] or ""
    msg["Date"] = email.utils.formatdate(localtime=False)
    msg["Message-ID"] = email.utils.make_msgid(domain=sender.split("@")[-1])
    if row.get("in_reply_to"):
        msg["In-Reply-To"] = row["in_reply_to"]
        msg["References"] = (row.get("refs") or row["in_reply_to"]).strip()
    msg.set_content(row["body"] or "")
    return msg


def send_approved(db, notifier, smtp=None, imap=None, now=None):
    """Manda o que o Vk aprovou (toque em Enviar ou texto editado). Nada sai sem isso."""
    now = now or time.time()
    db.query("UPDATE outbox SET state='erro', error='envio interrompido' WHERE state='enviando' AND updated_ts < ?",
             [now - 1800])
    rows = db.query("SELECT * FROM outbox WHERE state = 'aprovado' ORDER BY id LIMIT 10")
    if not rows:
        return dict(enviados=0)
    sender = (os.environ.get("ZOHO_USER") or "hello@victorvk.com")
    own = smtp is None
    sent = err = 0
    try:
        S = smtp or smtp_login()[0]
    except Exception as e:  # noqa: BLE001
        notifier.add("⚠️ Não consegui entrar no SMTP do Zoho (%s). Os %d e-mail(s) aprovados ficam esperando."
                     % (E(str(e)[:80]), len(rows)), urgent=True)
        return dict(enviados=0, erro=1)
    sent_folder = None
    for r in rows:
        # trava: só envia se ainda está 'aprovado' (evita envio duplo com duas rodadas ao mesmo tempo)
        lock = db.query("UPDATE outbox SET state='enviando', updated_ts=? WHERE id=? AND state='aprovado' RETURNING id",
                        [now, r["id"]])
        if not lock:
            continue
        msg = build_mime(r, sender)
        try:
            S.send_message(msg)
        except Exception as e:  # noqa: BLE001
            db.query("UPDATE outbox SET state='erro', error=?, updated_ts=? WHERE id=?", [str(e)[:200], now, r["id"]])
            notifier.add("⚠️ Falhou o envio para %s: %s\nToque ✉️ Enviar de novo em /rascunhos."
                         % (E(r["to_addr"]), E(str(e)[:120])), urgent=True)
            err += 1
            continue
        db.query("UPDATE outbox SET state='enviado', sent_ts=?, updated_ts=? WHERE id=?", [now, now, r["id"]])
        if r["job_id"]:
            db.query("INSERT INTO events (job_id, kind, ts, note, origin) VALUES (?, 'nota', ?, ?, 'v3-email')",
                     [r["job_id"], now, {"followup": "follow-up enviado", "agendar": "horários enviados"}.get(
                         r["kind"], "resposta enviada")])
        notifier.add("✉️ Enviado para %s" % E(r["to_addr"]), urgent=True)
        sent += 1
        try:  # cópia em Enviados
            if imap is not None or own:
                Mi = imap or imap_login()[0]
                imap = Mi
                if sent_folder is None:
                    sent_folder = _folders(Mi)[1] or "Sent"
                Mi.append('"%s"' % sent_folder, "\\Seen", imaplib.Time2Internaldate(time.time()), msg.as_bytes())
        except Exception:  # noqa: BLE001
            pass
    if own:
        try:
            S.quit()
        except Exception:  # noqa: BLE001
            pass
    return dict(enviados=sent, erros=err)


# ---------------------------------------------------------------- rodada
def run(db, tg, gem=None, mode="ciclo", now=None, mailbox=None, smtp=None):
    """ciclo: envia aprovados, lê e-mail, refaz corrigidos, follow-up. enviar: só envia aprovados."""
    now = now or time.time()
    notifier = Notifier(db, tg, now)
    out = {}
    out["envio"] = send_approved(db, notifier, smtp=smtp, now=now)
    if mode == "enviar":
        out["avisos"] = notifier.flush()
        return out
    body = (db.query("SELECT body FROM profile WHERE key='super_contexto'") or [{}])[0].get("body")
    perfil = ai.perfil_curto(body, 7000)
    state = db.setting_json("mail_state") or {}
    first_ever = not state
    if mailbox is None:
        M, _host = imap_login()
        raw, new_state = fetch_new(M, state, now)
        try:
            M.logout()
        except Exception:  # noqa: BLE001
            pass
    else:
        raw, new_state = mailbox(state, now)
    msgs = []
    for folder, _uid, r, first in raw:
        try:
            m = parse_message(r, folder)
        except Exception:  # noqa: BLE001
            continue
        m["_first"] = first
        msgs.append(m)
    ctx = Ctx(db, notifier, gem, now, perfil, first=first_ever)
    out["emails"] = dict(lidos=len(msgs), **handle_messages(ctx, msgs))
    out["ja_respondidos"] = close_replied(ctx)
    out["refeitos"] = redo_corrected(ctx)
    out.update(followups(ctx))
    if new_state != state:
        db.set_setting("mail_state", json.dumps(new_state))
    out["avisos"] = notifier.flush()
    out["ia"] = MAX_AI - ctx.budget[0]
    return out


def selftest():
    """Só confere login IMAP e SMTP (não lê conteúdo, não envia nada)."""
    M, h1 = imap_login()
    folders, sent = _folders(M)
    M.logout()
    S, h2 = smtp_login()
    S.quit()
    return dict(imap=h1, pastas=len(folders), enviados=bool(sent), smtp=h2)
