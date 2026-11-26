"""Nota da vaga (zero tokens): área + peso, palavras-chave, frescor, elegibilidade, nível, ghost.
Ranking em vez de corte duro: só descarta o que nunca serve (título fora, presencial, restrição de país, velha demais).
Salário nunca descarta, só vira aviso."""
import re
import time

from . import levels
from .textnorm import norm

ATS = ("greenhouse", "lever", "ashby", "workable", "smartrecruiters", "recruitee")
ATS_HOSTS = re.compile(r"greenhouse\.io|lever\.co|ashbyhq\.com|workable\.com|smartrecruiters\.com|recruitee\.com")

# área -> padrão no título (ordem importa: o primeiro que bate vence)
AREAS = [
    ("design_eng", r"design engineer|ui engineer|ux engineer|design technologist|creative (technologist|developer|front)"),
    ("product_design", r"product design|designer de produto|disenador(a)? de producto|product designer"),
    ("crm", r"\bcrm\b|lifecycle|gamifica|loyalty|retention|e-?mail (designer|developer|marketing)|email marketing|marketing automation|\bbraze\b"),
    ("uxui", r"\bux\b|\bui\b|user experience|experiencia do usuario|interaction design|design system|visual designer|digital designer|web designer ui|ux ?/ ?ui|ui ?/ ?ux"),
    ("frontend", r"front[ -]?end|desenvolvedor(a)? front|web developer|react (developer|engineer)|ui developer|javascript developer|desenvolvedor(a)? web"),
    ("web", r"web designer|webflow|framer|no[- ]?code|low[- ]?code|wordpress|landing page|shopify designer"),
    ("suporte_qa", r"\bqa\b|quality assurance|tester|test analyst|analista de (testes|qualidade)|technical support|suporte tecnico|support (engineer|specialist|analyst)|product op(s|erations)|implementation specialist|customer success engineer"),
]
_AREAS = [(k, re.compile(p)) for k, p in AREAS]
GENERIC_DESIGNER = re.compile(r"\bdesigner\b|\bdisenador|\bpessoa designer")
DEFAULT_WEIGHTS = {"product_design": 1.0, "uxui": 1.0, "design_eng": 0.95, "frontend": 0.85, "crm": 0.8, "web": 0.7,
                   "suporte_qa": 0.6}

EXCLUDE = re.compile(
    r"afirmativa|exclusiv[ao] para|\bpcd\b|pessoas? com deficiencia|"
    r"\b(staff|principal|director|diretor|head|vp|vice|gerente|coordenador|intern|internship|estagi\w*|trainee|apprentice|"
    r"backend|back-end|data (scientist|engineer|analyst)|devops|sre|android|ios|java|\.net|php|ruby|golang|salesforce|sap|"
    r"architect|arquitet\w*|instructional|interior|fashion|industrial|mechanical|civil|hardware|teacher|professor|tutor|"
    r"sales|vendedor|recruiter|account executive|graphic designer|designer grafico|3d|illustrator|motion designer|"
    r"packaging|textile|jewelry|landscape|artist|instrucional|learning experience|shopify developer|"
    r"talent pool|talent community|banco de talentos|general application|open application|candidatura espontanea|"
    r"evergreen)\b")
# engenharia de software sem front/UI (ex.: "Software Engineer, CRM" é backend)
SWE = re.compile(r"software (engineer|developer)|engenheir[oa] de software|desenvolvedor(a)? de software|full[- ]?stack")
SWE_OK = re.compile(r"front|\bui\b|\bux\b|design|web|react")
MANAGER = re.compile(r"\bmanager\b")
HYBRID = re.compile(r"\b(hibrid\w*|hybrid|presencial|on-?site|in[- ]office)\b")
REMOTE = re.compile(r"\b(remote|remoto|anywhere|work from home|wfh|distributed|telecommut\w*|fully remote|home based|trabalho remoto)\b")
RESTRICT = re.compile(
    r"(must|only|required|need to|candidates)\W.{0,40}(be )?(located|based|resid\w*|authori[sz]ed)\W.{0,20}"
    r"\b(us|usa|united states|eu|uk|canada)\b"
    r"|\bus (citizens|residents) only\b"
    r"|\b(usa|united states|u\.s\.|uk|eu|canada)[ -]only\b")
HARD_BLOCK = re.compile(r"green card|security clearance|us citizens? only|must be a us citizen")
OPEN_DESC =re.compile(r"\b(brazil|brasil|latam|latin america|america latina|work from anywhere|anywhere in the world)\b")
TEST_RX = re.compile(r"take[- ]home|case (tecnico|pratico)|teste (tecnico|pratico)|design (challenge|exercise)|desafio")

REGIONS = [
    ("Europa", r"europe|emea|european|\buk\b|united kingdom|germany|france|spain|portugal|netherlands|poland|ireland|italy"),
    ("EUA/Canadá", r"united states|\busa\b|\bus\b|canada|north america|new york|san francisco|california|toronto"),
    ("Ásia/Oceania", r"asia|apac|india|singapore|japan|australia|new zealand|philippines"),
]


def _rx_terms(terms):
    parts = [r"(?<![a-z])" + re.escape(norm(t)).replace(r"\ ", r"\s+") + r"(?![a-z])" for t in terms or [] if t]
    return re.compile("|".join(parts)) if parts else None


def area_of(title):
    """(área, multiplicador) ou (None, 0) se o título não é de nenhuma área."""
    t = norm(title)
    for key, rx in _AREAS:
        if rx.search(t):
            return key, 1.0
    if GENERIC_DESIGNER.search(t):
        return "uxui", 0.6
    return None, 0.0


def excluded(title):
    t = norm(title)
    if EXCLUDE.search(t):
        return True
    if SWE.search(t) and not SWE_OK.search(t):
        return True
    # "manager" só vale em CRM/lifecycle (CRM Manager, Lifecycle Manager)
    return bool(MANAGER.search(t)) and not re.search(r"crm|lifecycle|retention|e-?mail", t)


def keep_title(title):
    """filtro barato usado na coleta"""
    return area_of(title)[0] is not None and not excluded(title)


def eligibility(job, cfg):
    """aberta = dá pra pegar do Brasil; regiao = remota restrita a uma região; bloqueada; presencial"""
    g = cfg.get("geo") or {}
    loc = norm(job.get("location"))
    title = norm(job.get("title"))
    desc = norm((job.get("description") or "")[:3000])
    if HYBRID.search(loc + " " + title):
        return "presencial"
    if job.get("source") == "linkedin":
        # LinkedIn público: "Cidade, Estado, Brazil (remoto)" sem confirmação de remoto no título = presencial
        raw = re.sub(r"\(remoto\)", "", loc).strip()
        if HYBRID.search(desc[:3000]) and not REMOTE.search(title):
            return "presencial"
        if "," in raw and not REMOTE.search(title) and not REMOTE.search(desc[:3000]):
            return "presencial"
    ok, blk = _rx_terms(g.get("ok_terms")), _rx_terms(g.get("block_terms"))
    if blk and blk.search(loc):
        return "bloqueada"
    if ok and ok.search(loc):
        return "aberta"
    if RESTRICT.search(desc) or HARD_BLOCK.search(desc):
        return "bloqueada"
    if job.get("source") in ("gupy", "programathor") or OPEN_DESC.search(desc):
        return "aberta"
    if loc in ("", "remote", "remoto", "remote work", "anywhere", "fully remote", "brasil (remoto)"):
        return "aberta"
    if REMOTE.search(" ".join((loc, title, desc[:300]))):
        return "regiao"
    return "presencial"


def region_label(job):
    loc = norm(job.get("location"))
    for label, rx in REGIONS:
        if re.search(rx, loc):
            return label
    return "uma região"


def ghost(job, now):
    g = 0.0
    posted = job.get("posted_ts")
    age = (now - posted) / 86400 if posted else None
    if age is not None:
        g += 35 if age > 45 else 20 if age > 30 else 0
    else:
        g += 10
    if (job.get("repost_count") or 0) >= 2:
        g += 15
    if len(job.get("description") or "") < 400 and not job.get("desc_partial"):
        g += 20
    if not job.get("salary"):
        g += 5
    return min(g, 100.0), age


_NUM = re.compile(r"(\d[\d.,]*)\s*(k)?", re.I)


def _nums(s):
    out = []
    for m in _NUM.finditer(s):
        raw = m.group(1).replace(",", "")
        if raw.count(".") > 1 or re.search(r"\.\d{3}$", raw):
            raw = raw.replace(".", "")
        try:
            v = float(raw)
        except ValueError:
            continue
        if m.group(2):
            v *= 1000
        out.append(v)
    return out


def salary_warning(salary, lvl, pretensao):
    """Aviso se o salário informado está abaixo da pretensão do nível. Nunca descarta."""
    if not salary or not pretensao:
        return None
    key = {"junior": "junior", "senior": "senior", "staff": "senior"}.get(lvl, "pleno")
    p = pretensao.get(key) or {}
    s = salary.lower()
    nums = [n for n in _nums(s) if n >= 100]
    if not nums:
        return None
    top = max(nums)
    if "r$" in s or "brl" in s:
        floor = p.get("clt_brl")
        if floor and top < floor:
            return "salário abaixo da pretensão (R$ %d)" % floor
        return None
    usd_mes = p.get("usd_mes")
    if not usd_mes:
        return None
    if "/h" in s or "hour" in s or "hora" in s:
        h = p.get("usd_h")
        return ("abaixo de US$ %d/h" % h) if h and top < h else None
    monthly = top / 12 if top > 12000 else top
    return ("salário abaixo de US$ %d/mês" % usd_mes) if monthly < usd_mes else None


def apply_type(job):
    src, url = job.get("source") or "", job.get("url") or ""
    if "linkedin.com" in url:  # Easy Apply: só pelo LinkedIn, com você
        return "linkedin"
    if src in ATS or ATS_HOSTS.search(url):
        return "auto"
    return "kit"


def score(job, cfg, now=None, weights=None, pretensao=None, warm=False):
    """dict com tudo que vai pro banco, ou None se a vaga nunca serve."""
    now = now or time.time()
    title = job.get("title") or ""
    area, mult = area_of(title)
    if not area or excluded(title):
        return None
    elig = eligibility(job, cfg)
    if elig in ("presencial", "bloqueada"):
        return None
    gh, age = ghost(job, now)
    if age is not None and age > cfg.get("max_age_days", 30):
        return None
    if gh >= cfg.get("ghost_drop", 80):
        return None

    w = (weights or DEFAULT_WEIGHTS).get(area, 0.6)
    lv = levels.detect(title, job.get("description"))
    if lv["lvl"] == "staff":
        return None
    s = 50.0 * w * mult
    why, risk = [], []
    why.append({"product_design": "Product Design", "uxui": "UX/UI", "design_eng": "Design Engineer",
                "frontend": "Front-end", "crm": "CRM/Gamificação", "web": "Web/No-code",
                "suporte_qa": "Suporte/QA"}[area])

    blob = norm(" ".join((title, job.get("description") or "", job.get("location") or "")))
    kw = cfg.get("keywords") or {}
    hits = [k for k in kw if re.search(r"(?<![a-z])" + re.escape(norm(k)), blob)]
    s += min(sum(kw[k] for k in hits), 22)
    if hits:
        why.append(", ".join(hits[:3]))

    if age is not None:
        if age <= 2:
            s += 15; why.append("nova")
        elif age <= 7:
            s += 8
        elif age > 21:
            s -= 15
        elif age > 14:
            s -= 8
    if elig == "aberta":
        s += 8
        if re.search(r"brazil|brasil|latam|latin america|america latina", norm(job.get("location"))):
            s += 4; why.append("aceita Brasil/LATAM")
    else:
        s -= 12; risk.append("remota, mas pode exigir morar em " + region_label(job))

    if lv["lvl"] == "mid" or lv["lvl"] == "unknown":
        s += 5
    elif lv["lvl"] == "senior":
        s += 3
    elif lv["lvl"] == "junior":
        risk.append("júnior")
    if lv["lead"]:
        s -= 6
    yrs = lv["yrs"]
    if yrs and yrs >= 8:
        s -= 20; risk.append("pede %d+ anos" % yrs)
    elif yrs and yrs >= 7:
        s -= 8; risk.append("pede %d anos" % yrs)

    src = job.get("source") or ""
    if src in ATS or src == "gupy":
        s += 5
    if job.get("salary"):
        s += 3
    if warm:
        s += 10; why.append("você conhece alguém lá")
    s -= gh / 5
    if TEST_RX.search(norm(job.get("description") or "")):
        risk.append("tem teste/case")
    sw = salary_warning(job.get("salary"), lv["lvl"], pretensao)
    if sw:
        risk.append(sw)
    if age is None:
        risk.append("sem data de publicação")

    fit = max(0.0, min(100.0, s))
    return dict(area=area, level=lv["lvl"], years=yrs, yrs_phrase=lv["phrase"], elig=elig,
                geo_ok=1, fit=round(fit, 1), ghost=gh, priority=int(round(fit)), reason=" · ".join(why),
                risk=" · ".join(risk), apply_type=apply_type(job))
