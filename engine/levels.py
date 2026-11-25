"""Senioridade e anos pedidos (vem do jobhunter, sem tokens)."""
import re

from .textnorm import norm

JUNIOR = r"\b(junior|jr\.?|entry[- ]level|j[uú]nior)\b"
STAFF = r"\b(staff|principal|head of|director|vp|vice president)\b"
LEAD = r"\b(lead|lider|líder)\b"
SENIOR = r"\b(senior|sr\.?|s[eê]nior|especialista)\b"
MID = r"\b(mid|mid[- ]level|pleno|semi[- ]?senior|ssr|intermediate|ii)\b"

YEARS = re.compile(r"(?<![\d.])(\d{1,2})\s*(?:\+|plus)?\s*(?:(?:-|–|to|a|ate)\s*(\d{1,2})\s*\+?)?\s*(?:years?|yrs?|anos?)\b", re.I)
EXP_CTX = re.compile(r"experience|\bexp\b|experiencia|working as|in (?:product|ux|ui|design)|de atuacao|na area", re.I)
IGNORE_AFTER = re.compile(r"^\W{0,3}(ago|old|de mercado|of history|in business|de historia|no mercado)\b", re.I)
IGNORE_BEFORE = re.compile(r"(founded|fundad\w*|since|over the (?:last|past)|for (?:the )?(?:last|past)|\bha)\W*$", re.I)


def years_required(text):
    """MAIOR número de anos de experiência pedido (faixa '3-5' conta como 5). (anos, frase) ou (None, None)."""
    t = norm(text)[:8000]
    best, phrase = None, None
    for m in YEARS.finditer(t):
        a = int(m.group(1))
        b = int(m.group(2)) if m.group(2) else None
        n = max(a, b) if b else a
        if not (0 < n <= 20):
            continue
        before, after = t[max(0, m.start() - 40):m.start()], t[m.end():m.end() + 40]
        if IGNORE_AFTER.search(after) or IGNORE_BEFORE.search(before):
            continue
        if not (EXP_CTX.search(after) or EXP_CTX.search(before)):
            continue
        if best is None or n > best:
            best, phrase = n, m.group(0).strip()
    return best, phrase


def detect(title, description):
    """dict(lvl, yrs, phrase, lead) — lvl: junior, mid, senior, staff, unknown"""
    t = norm(title)
    yrs, phrase = years_required(description or "")
    lead = bool(re.search(LEAD, t))
    if re.search(JUNIOR, t):
        lvl = "junior"
    elif re.search(STAFF, t):
        lvl = "staff"
    elif re.search(SENIOR, t) or lead:
        lvl = "senior"
    elif re.search(MID, t):
        lvl = "mid"
    elif yrs is not None:
        lvl = "junior" if yrs <= 2 else "mid" if yrs <= 5 else "senior"
    else:
        lvl = "unknown"
    return dict(lvl=lvl, yrs=yrs, phrase=phrase, lead=lead)
