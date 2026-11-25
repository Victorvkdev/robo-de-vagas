"""Normalização de texto, chave de empresa e chave de duplicata (vem do jobhunter)."""
import hashlib
import re
import unicodedata

SUFFIX = r"\b(inc|incorporated|llc|ltd|limited|gmbh|corp|corporation|co|company|sa|s\.a|ltda|plc|ag|bv|pte|group|labs)\b"
_WS = re.compile(r"\s+")


def norm(s):
    """minúsculas, sem acentos, espaços colapsados"""
    s = s or ""
    if not s.isascii():
        s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode("ascii")
    return _WS.sub(" ", s.lower()).strip()


def sha16(s):
    return hashlib.sha1((s or "").encode("utf-8", "ignore")).hexdigest()[:16]


def company_key(name):
    n = norm(name)
    n = re.sub(r"[^a-z0-9 ]+", " ", n)
    n = re.sub(SUFFIX, " ", n)
    return re.sub(r"\s+", "", n).strip()


def dedupe_key(company, title):
    ck = company_key(company)
    if not ck:
        return None
    t = re.sub(r"\(.*?\)", " ", norm(title))
    t = re.sub(r"[^a-z0-9 ]+", " ", t)
    return ck + "|" + re.sub(r"\s+", " ", t).strip()
