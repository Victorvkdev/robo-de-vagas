"""Coletores (APIs e feeds públicos, sem login, sem burlar nada). Base: jobhunter v4 + Gupy, Workable global e
LinkedIn público do Robô v2. Cada fonte é isolada: se uma cair, as outras seguem. Nunca tocam no banco."""
import hashlib
import html
import re
import threading
import time
import datetime as dt
import xml.etree.ElementTree as ET
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed, TimeoutError as FutTimeout
from email.utils import parsedate_to_datetime
from urllib.parse import urlparse, urlencode, parse_qs

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from .score import keep_title
from .textnorm import company_key

UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130 Safari/537.36"}
TIMEOUT = (5, 20)
MAX_WORKERS = 8
COLLECT_TIMEOUT = 600


def _session():
    s = requests.Session()
    retry = Retry(total=2, backoff_factor=0.7, status_forcelist=[429, 500, 502, 503, 504],
                  allowed_methods=frozenset(["GET", "POST"]))
    ad = HTTPAdapter(pool_connections=32, pool_maxsize=16, max_retries=retry)
    s.mount("https://", ad)
    s.headers.update(UA)
    return s


_SESSION = _session()
_HOSTSEM = defaultdict(lambda: threading.Semaphore(2))
_LOCK = threading.Lock()


def _req(method, url, **kw):
    kw.setdefault("timeout", TIMEOUT)
    with _LOCK:
        sem = _HOSTSEM[urlparse(url).hostname or ""]
    with sem:
        r = _SESSION.request(method, url, **kw)
    r.raise_for_status()
    return r


def _get(url, **kw):
    return _req("GET", url, **kw).json()


def _raw(url, **kw):
    return _req("GET", url, **kw).content


def clean(t):
    t = html.unescape(t or "")
    t = re.sub(r"<[^>]+>", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def ts(v):
    """epoch s/ms, ISO8601 ou RFC822 -> epoch s. Falhou -> None."""
    if v is None or v == "" or isinstance(v, bool):
        return None
    try:
        if isinstance(v, (int, float)) or re.fullmatch(r"\d+(\.\d+)?", str(v).strip()):
            f = float(v)
            return f / 1000 if f > 1e12 else f
        s = str(v).strip()
        iso = s.replace(" UTC", "").strip()
        if iso.endswith("Z"):
            iso = iso[:-1] + "+00:00"
        iso = re.sub(r"([+-]\d{2})(\d{2})$", r"\1:\2", iso)
        iso = re.sub(r"\.(\d{6})\d+", r".\1", iso)
        iso = re.sub(r"\.(\d{1,5})(?=[+-]|$)", lambda m: "." + m.group(1).ljust(6, "0"), iso)
        try:
            d = dt.datetime.fromisoformat(iso)
        except ValueError:
            d = parsedate_to_datetime(s)
        if d.tzinfo is None:
            d = d.replace(tzinfo=dt.timezone.utc)
        return d.timestamp()
    except Exception:
        return None


def pretty(slug):
    return re.sub(r"[-_]+", " ", slug or "").strip().title()


def job(source, company, title, location, url, desc, salary="", posted=None, partial=False, regime="", lang=None):
    url = (url or "").strip()
    jid = hashlib.sha1(("%s|%s" % (source, url)).encode()).hexdigest()[:16]
    return dict(id=jid, source=source, company=clean(company)[:100], title=clean(title)[:160],
                location=clean(location)[:160], url=url, description=clean(desc)[:12000], salary=(salary or "")[:80],
                posted_ts=ts(posted), desc_partial=bool(partial), regime=regime or "", lang=lang)


# ---------------- agregadores ----------------
def remoteok(_s):
    out = []
    for d in _get("https://remoteok.com/api")[1:]:
        if not isinstance(d, dict) or not d.get("position"):
            continue
        sal = "USD %s-%s/ano" % (d["salary_min"], d["salary_max"]) if d.get("salary_min") else ""
        out.append(job("remoteok", d.get("company"), d.get("position"), d.get("location") or "Worldwide",
                       d.get("url") or d.get("apply_url"), d.get("description"), sal, d.get("epoch")))
    return out


def remotive(_s):
    out = []
    for q in ("category=design", "category=software-dev&search=front", "search=designer",
              "category=customer-support&search=technical", "category=marketing&search=crm"):
        for d in _get("https://remotive.com/api/remote-jobs?" + q).get("jobs", []):
            out.append(job("remotive", d.get("company_name"), d.get("title"), d.get("candidate_required_location"),
                           d.get("url"), d.get("description"), d.get("salary"), d.get("publication_date"),
                           regime=d.get("job_type")))
    return out


JOBICY_TAGS = ("design", "ux", "frontend", "web design", "crm")


def jobicy(_s):
    out, errs = [], 0
    for tag in JOBICY_TAGS:
        for geo in ("", "latam", "brazil", "anywhere"):
            q = {"count": 50, "tag": tag}
            if geo:
                q["geo"] = geo
            try:
                d = _get("https://jobicy.com/api/v2/remote-jobs?" + urlencode(q))
            except requests.RequestException:
                errs += 1
                if errs >= 4:
                    raise
                continue
            for x in d.get("jobs", []):
                sal = "%s %s-%s" % (x.get("salaryCurrency", ""), x.get("annualSalaryMin", ""), x.get("annualSalaryMax", "")) \
                    if x.get("annualSalaryMin") else ""
                jt = x.get("jobType")
                out.append(job("jobicy", x.get("companyName"), x.get("jobTitle"), x.get("jobGeo") or geo,
                               x.get("url"), x.get("jobDescription") or x.get("jobExcerpt"), sal, x.get("pubDate"),
                               regime=", ".join(jt) if isinstance(jt, list) else (jt or "")))
    return out


def arbeitnow(_s):
    return [job("arbeitnow", x.get("company_name"), x.get("title"), "Remote" if x.get("remote") else x.get("location"),
                x.get("url"), x.get("description"), "", x.get("created_at"))
            for x in _get("https://www.arbeitnow.com/api/job-board-api").get("data", [])]


def himalayas(_s):
    out = []
    for off in range(0, 200, 20):
        for x in _get("https://himalayas.app/jobs/api?limit=20&offset=%d" % off).get("jobs", []):
            sal = "%s %s-%s" % (x.get("currency", ""), x.get("minSalary", ""), x.get("maxSalary", "")) if x.get("minSalary") else ""
            out.append(job("himalayas", x.get("companyName"), x.get("title"),
                           ", ".join(x.get("locationRestrictions") or ["Worldwide"]),
                           x.get("applicationLink") or x.get("guid"), x.get("description") or x.get("excerpt"),
                           sal, x.get("pubDate"), regime=x.get("employmentType")))
    return out


WWR_FEEDS = ("remote-design-jobs", "remote-product-jobs", "remote-front-end-programming-jobs",
             "remote-full-stack-programming-jobs", "remote-customer-support-jobs", "all-other-remote-jobs")


def weworkremotely(_s):
    out = []
    for f in WWR_FEEDS:
        for it in ET.fromstring(_raw("https://weworkremotely.com/categories/%s.rss" % f)).iter("item"):
            g = lambda t: (it.findtext(t) or "").strip()  # noqa: E731
            comp, _, title = g("title").partition(": ")
            if not title:
                comp, title = "", g("title")
            out.append(job("weworkremotely", comp, title, g("region") or "Anywhere", g("link"), g("description"),
                           "", g("pubDate")))
    return out


def workingnomads(_s):
    out = []
    for x in _get("https://www.workingnomads.com/api/exposed_jobs/"):
        cat = (x.get("category_name") or "").lower()
        if cat and not any(k in cat for k in ("design", "development", "product", "marketing", "support")):
            continue
        out.append(job("workingnomads", x.get("company_name"), x.get("title"), x.get("location") or "Worldwide",
                       x.get("url"), x.get("description"), "", x.get("pub_date")))
    return out


def getonboard(_s):
    out = []
    for cat in ("design-ux", "programming"):
        d = _get("https://www.getonbrd.com/api/v0/categories/%s/jobs?per_page=100&expand=%%5B%%22company%%22%%5D" % cat)
        comp = {c.get("id"): (c.get("attributes") or {}).get("name") for c in d.get("included", []) if isinstance(c, dict)}
        for x in d.get("data", []):
            a = x.get("attributes") or {}
            cid = (((x.get("relationships") or {}).get("company") or {}).get("data") or {}).get("id")
            ctr = a.get("countries") or []
            remote = a.get("remote") or "remote" in str(a.get("remote_modality", "")).lower()
            loc = ("LATAM Remote " if remote else "") + ", ".join(ctr if isinstance(ctr, list) else [str(ctr)])
            desc = " ".join(str(a.get(k) or "") for k in ("description", "functions", "benefits"))
            sal = "USD %s-%s/mês" % (a["min_salary"], a["max_salary"]) if a.get("min_salary") else ""
            out.append(job("getonboard", comp.get(cid) or "", a.get("title"), loc.strip() or "LATAM",
                           (x.get("links") or {}).get("public_url") or "https://www.getonbrd.com/jobs/%s" % x.get("id"),
                           desc, sal, a.get("published_at")))
    return out


def themuse(_s):
    out = []
    for pg in range(4):
        try:
            d = _get("https://www.themuse.com/api/public/jobs?category=Design%%20and%%20UX&location=Flexible%%20%%2F%%20Remote&page=%d" % pg)
        except requests.RequestException:
            if pg == 0:
                raise
            break
        for x in d.get("results", []):
            locs = ", ".join(loc.get("name", "") for loc in x.get("locations", []))
            out.append(job("themuse", (x.get("company") or {}).get("name"), x.get("name"), locs or "Flexible / Remote",
                           (x.get("refs") or {}).get("landing_page"), x.get("contents"), "", x.get("publication_date")))
    return out


def rss(s):
    """Feeds RSS/Atom pessoais (Google Alerts). Ficam no D1 (settings.rss_feeds), nunca no repo."""
    out = []
    A = "{http://www.w3.org/2005/Atom}"
    for url in s.get("rss") or []:
        try:
            root = ET.fromstring(_raw(url))
        except Exception:
            continue
        for it in list(root.iter("item")) + list(root.iter(A + "entry")):
            def g(*tags):
                for t in tags:
                    v = it.findtext(t) or it.findtext(A + t)
                    if v:
                        return v.strip()
                return ""
            link = g("link")
            if not link:
                el = it.find(A + "link")
                link = (el.get("href") if el is not None else "") or ""
            link = (parse_qs(urlparse(link).query).get("url") or [link])[0]
            title = clean(g("title"))
            out.append(job("rss", "", title, "Remote", link, title + ". " + g("description", "summary", "content"),
                           "", g("pubDate", "published", "updated"), partial=True))
    return out


# ---------------- Brasil e buscas globais (do Robô v2) ----------------
TERMOS_BR = ("product designer", "ux designer", "ui designer", "ux/ui", "designer de produto", "front-end", "frontend",
             "web designer", "design system", "crm", "gamificação", "webflow", "suporte técnico", "analista de qa",
             "designer")


def gupy(_s):
    out = []
    for termo in TERMOS_BR:
        url = "https://portal.gupy.io/api/job-search/jobs?" + urlencode(
            {"jobName": termo, "limit": 100, "offset": 0, "workplaceType": "remote"})
        for j in _get(url).get("data", []):
            if j.get("workplaceType") != "remote":
                continue
            reg = {"vacancy_legal_entity": "PJ", "vacancy_type_effective": "CLT"}.get(j.get("type", ""), "")
            out.append(job("gupy", j.get("careerPageName"), j.get("name"), "Brasil (remoto)", j.get("jobUrl"),
                           j.get("description"), "", j.get("publishedDate"), regime=reg, lang="pt"))
        time.sleep(0.3)
    return out


TERMOS_WORKABLE = ("product designer", "ux designer", "ui designer", "front-end", "web designer", "designer",
                   "design engineer", "crm")


def workable_search(_s):
    out = []
    for termo in TERMOS_WORKABLE:
        d = _get("https://jobs.workable.com/api/v1/jobs?" + urlencode(
            {"query": termo, "location": "Brazil", "workplace": "remote"}))
        for x in d.get("jobs", []):
            locs = x.get("locations") or []
            if not any(str(loc).upper() == "TELECOMMUTE" for loc in locs):
                continue
            pais = ((x.get("location") or {}).get("countryName") or "").strip()
            out.append(job("workable", (x.get("company") or {}).get("title"), x.get("title"),
                           (pais + " Remote").strip(), x.get("url"),
                           (x.get("description") or "") + " " + (x.get("requirementsSection") or ""), "",
                           x.get("created") or x.get("updated"), regime=x.get("employmentType"),
                           lang="pt" if x.get("language") == "pt" else "en"))
    return out


TERMOS_LINKEDIN = ("product designer", "ux ui designer", "ux designer", "design engineer", "front-end developer",
                   "web designer", "crm")


LINKEDIN_DETAIL_CAP = 80


def linkedin_detail(html_text):
    """Página pública da vaga: (descrição, link de candidatura no site da empresa ou None se for Easy Apply)."""
    d = re.search(r'show-more-less-html__markup[^>]*>(.*?)</div>', html_text or "", re.S)
    desc = clean(d.group(1)) if d else ""
    ext = None
    m = re.search(r'<code id="applyUrl"[^>]*><!--\s*"?(.*?)"?\s*--></code>', html_text or "", re.S)
    if m:
        raw = html.unescape(m.group(1)).strip()
        q = parse_qs(urlparse(raw).query)
        ext = (q.get("url") or [raw])[0]
        if "linkedin.com" in (urlparse(ext).hostname or ""):
            ext = None
    return desc, ext


def linkedin(_s):
    """Busca pública (sem login) das últimas 24h, remoto, Brasil. Nunca aplica pelo LinkedIn: quando a vaga manda
    para o site da empresa, o link do card passa a ser o do site (e o envio segue o ATS de lá)."""
    out, seen = [], set()
    for termo in TERMOS_LINKEDIN:
        for start in (0, 25):
            url = "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search?" + urlencode(
                {"keywords": termo, "location": "Brazil", "f_WT": 2, "f_TPR": "r86400", "start": start})
            try:
                h = _req("GET", url).text
            except requests.RequestException:
                break
            for card in h.split("<li")[1:]:
                link = re.search(r'class="base-card__full-link[^"]*"\s+href="([^"?]+)', card)
                cargo = re.search(r'base-search-card__title">\s*(.*?)\s*</h3>', card, re.S)
                emp = re.search(r'base-search-card__subtitle">(.*?)</h4>', card, re.S)
                loc = re.search(r'job-search-card__location">\s*(.*?)\s*</span>', card, re.S)
                quando = re.search(r'datetime="([\d-]+)"', card)
                if not (link and cargo) or link.group(1) in seen:
                    continue
                seen.add(link.group(1))
                out.append(job("linkedin", emp.group(1) if emp else "", cargo.group(1),
                               (clean(loc.group(1)) if loc else "Brazil") + " (remoto)", link.group(1), "", "",
                               quando.group(1) if quando else None, partial=True))
            time.sleep(1)
    # detalhe das que passam no título: descrição + candidatura no site da empresa
    n = 0
    for j in out:
        if n >= LINKEDIN_DETAIL_CAP or not keep_title(j["title"]):
            continue
        jid = re.search(r"(\d{6,})/?$", j["url"])
        if not jid:
            continue
        n += 1
        try:
            desc, ext = linkedin_detail(
                _req("GET", "https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/" + jid.group(1)).text)
        except requests.RequestException:
            continue
        if desc:
            j["description"], j["desc_partial"] = desc[:12000], False
        if ext:
            j["linkedin_url"], j["url"] = j["url"], ext
        time.sleep(0.5)
    return out


# ---------------- ATS por empresa ----------------
def _boards(kind, slugs, fetch, skip):
    jobs, ok, errors = [], set(), {}
    todo = [s for s in (slugs or []) if "%s:%s" % (kind, s) not in skip]

    def one(s):
        try:
            return s, fetch(s), None
        except requests.HTTPError as e:
            return s, None, str(e.response.status_code if e.response is not None else "http")
        except Exception as e:  # noqa: BLE001
            return s, None, (type(e).__name__ + ": " + str(e))[:80]

    with ThreadPoolExecutor(2) as ex:
        for s, got, err in ex.map(one, todo):
            key = "%s:%s" % (kind, s)
            if err is not None:
                errors[key] = err
                continue
            jobs += got
            ok.add(key)
    return jobs, ok, errors


def greenhouse(s, skip):
    def fetch(b):
        out = []
        for x in _get("https://boards-api.greenhouse.io/v1/boards/%s/jobs" % b).get("jobs", []):
            title = x.get("title") or ""
            if not keep_title(title):
                continue
            desc, partial = "", True
            try:
                desc = _get("https://boards-api.greenhouse.io/v1/boards/%s/jobs/%s" % (b, x.get("id"))).get("content") or ""
                partial = False
            except requests.RequestException:
                pass
            out.append(job("greenhouse", x.get("company_name") or pretty(b), title, (x.get("location") or {}).get("name"),
                           x.get("absolute_url"), desc, "", x.get("first_published") or x.get("updated_at"), partial))
        return out
    return _boards("greenhouse", s.get("greenhouse"), fetch, skip)


def lever(s, skip):
    def fetch(b):
        out = []
        for x in _get("https://api.lever.co/v0/postings/%s?mode=json" % b):
            cat = x.get("categories") or {}
            out.append(job("lever", pretty(b), x.get("text"), "%s %s" % (cat.get("location", ""), x.get("workplaceType", "")),
                           x.get("hostedUrl"), x.get("descriptionPlain") or x.get("description"), "", x.get("createdAt"),
                           regime=cat.get("commitment")))
        return out
    return _boards("lever", s.get("lever"), fetch, skip)


def ashby(s, skip):
    def fetch(b):
        out = []
        for x in _get("https://api.ashbyhq.com/posting-api/job-board/%s" % b).get("jobs", []):
            if x.get("isListed") is False:
                continue
            loc = (x.get("location") or "") + (" Remote" if x.get("isRemote") else "")
            out.append(job("ashby", pretty(b), x.get("title"), loc, x.get("jobUrl"),
                           x.get("descriptionPlain") or x.get("descriptionHtml"), "", x.get("publishedAt"),
                           regime=x.get("employmentType")))
        return out
    return _boards("ashby", s.get("ashby"), fetch, skip)


def workable(s, skip):
    def fetch(b):
        out = []
        for x in _get("https://apply.workable.com/api/v1/widget/accounts/%s" % b).get("jobs", []):
            loc = ", ".join(v for v in (x.get("city"), x.get("state"), x.get("country")) if v) + \
                (" Remote" if x.get("telecommuting") else "")
            desc = x.get("description") or ""
            out.append(job("workable", pretty(b), x.get("title"), loc, x.get("url") or x.get("shortlink"), desc, "",
                           x.get("published_on") or x.get("created_at"), partial=not desc))
        return out
    return _boards("workable", s.get("workable"), fetch, skip)


def smartrecruiters(s, skip):
    def fetch(b):
        out = []
        for x in _get("https://api.smartrecruiters.com/v1/companies/%s/postings?limit=100" % b).get("content", []):
            title = x.get("name") or ""
            if not keep_title(title):
                continue
            loc_ = x.get("location") or {}
            loc = ", ".join(v for v in (loc_.get("city"), (loc_.get("country") or "").upper()) if v) + \
                (" Remote" if loc_.get("remote") else "")
            desc, partial = "", True
            try:
                det = _get("https://api.smartrecruiters.com/v1/companies/%s/postings/%s" % (b, x.get("id")))
                secs = (det.get("jobAd") or {}).get("sections") or {}
                desc = " ".join((secs.get(k) or {}).get("text") or "" for k in
                                ("jobDescription", "qualifications", "additionalInformation"))
                partial = not desc.strip()
            except requests.RequestException:
                pass
            out.append(job("smartrecruiters", (x.get("company") or {}).get("name") or b, title, loc,
                           "https://jobs.smartrecruiters.com/%s/%s" % (b, x.get("id")), desc, "", x.get("releasedDate"),
                           partial))
        return out
    return _boards("smartrecruiters", s.get("smartrecruiters"), fetch, skip)


def recruitee(s, skip):
    def fetch(b):
        out = []
        for x in _get("https://%s.recruitee.com/api/offers/" % b).get("offers", []):
            loc = "%s %s" % (x.get("location", ""), x.get("country", "")) + (" Remote" if x.get("remote") else "")
            out.append(job("recruitee", x.get("company_name") or pretty(b), x.get("title"), loc, x.get("careers_url"),
                           x.get("description"), "", x.get("published_at") or x.get("created_at")))
        return out
    return _boards("recruitee", s.get("recruitee"), fetch, skip)


BOARDS = dict(greenhouse=greenhouse, lever=lever, ashby=ashby, workable=workable, smartrecruiters=smartrecruiters,
              recruitee=recruitee)
AGGREGATORS = dict(remoteok=remoteok, remotive=remotive, jobicy=jobicy, arbeitnow=arbeitnow, himalayas=himalayas,
                   weworkremotely=weworkremotely, workingnomads=workingnomads, getonboard=getonboard, themuse=themuse,
                   rss=rss, gupy=gupy, workable_search=workable_search, linkedin=linkedin)


def collect(cfg, skip=(), timeout=COLLECT_TIMEOUT, only=None):
    """Roda as fontes em paralelo. dict(jobs, errors, ok_boards, counts, raw_count). Só devolve títulos das áreas."""
    s = cfg.get("sources") or {}
    skip = set(skip or ())
    names = [n for n in list(AGGREGATORS) + list(BOARDS)
             if (s.get(n) if n != "rss" else s.get("rss")) and n not in skip and (not only or n in only)]
    raw, errors, ok_boards, counts = [], {}, set(), {}
    ex = ThreadPoolExecutor(max_workers=MAX_WORKERS)
    futs = {(ex.submit(BOARDS[n], s, skip) if n in BOARDS else ex.submit(AGGREGATORS[n], s)): n for n in names}
    try:
        for f in as_completed(futs, timeout=timeout):
            n = futs[f]
            try:
                got = f.result()
                if n in BOARDS:
                    got, ok, errs = got
                    ok_boards |= ok
                    errors.update(errs)
                    counts[n] = "%d (%d boards ok, %d erro)" % (len(got), len(ok), len(errs))
                else:
                    counts[n] = len(got)
                raw += got
            except Exception as e:  # noqa: BLE001
                errors[n] = (type(e).__name__ + ": " + str(e))[:120]
                counts[n] = 0
    except FutTimeout:
        for f, n in futs.items():
            if not f.done():
                errors[n] = "timeout"
    finally:
        ex.shutdown(wait=False, cancel_futures=True)

    jobs, seen = [], set()
    for j in raw:
        if not j.get("url") or j["id"] in seen or not keep_title(j["title"]):
            continue
        seen.add(j["id"])
        j["company_key"] = company_key(j["company"])
        jobs.append(j)
    return dict(jobs=jobs, errors=errors, ok_boards=ok_boards, counts=counts, raw_count=len(raw))
