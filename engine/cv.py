"""CV adaptado por vaga: escolhe e ordena só blocos aprovados do Super Contexto (bullets da seção 5, resumo,
headline, skills da seção 7). Não escreve fato novo: a IA, quando entra, só devolve IDs e ordem.
Formato ATS: 1 coluna, sem tabela, sem foto. HTML -> PDF com o Chromium do Playwright."""
import html
import json
import re

from .textnorm import norm

AREA_TAG = {"product_design": "PD", "uxui": "UX", "frontend": "FE", "design_eng": "DE", "crm": "CRM", "web": "WEB",
            "suporte_qa": "QA"}
AREA_HL = {"product_design": "HL.PD", "uxui": "HL.PD", "design_eng": "HL.DE", "frontend": "HL.FE", "crm": "HL.CRM",
           "web": "HL.PD", "suporte_qa": "HL.QA"}
GAME = re.compile(r"\bgam(e|es|ing)\b|jogo|steam|unity|unreal|igaming|casino|cassino")
# quantos bullets por experiência (ordem do CV = ordem do Super Contexto)
LIMITS = {"Lucky Gaming": 6, "Deadpixel Studios": 2, "Infatec": 4, "Freelance": 1, "Projetos": 2}
MESES = {"pt": "jan fev mar abr mai jun jul ago set out nov dez".split(),
         "en": "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split()}
STOP = set("the and for with you your our are will from that this have has into about over their they them".split()
           + "para com uma que dos das nos nas por como mais sua seu ser são está".split())

L = {
    "pt": dict(resumo="Resumo", exp="Experiência", proj="Projetos", form="Formação", cert="Certificações",
               idi="Idiomas", skills="Competências e ferramentas", atual="atual"),
    "en": dict(resumo="Summary", exp="Experience", proj="Projects", form="Education", cert="Certifications",
               idi="Languages", skills="Skills & tools", atual="present"),
}


def lang_of(job):
    if (job.get("lang") or "") == "pt" or job.get("source") == "gupy" or "gupy.io" in (job.get("url") or ""):
        return "pt"
    return "en"


def keywords(job, n=40):
    words = re.findall(r"[a-z][a-z0-9+#.\-]{2,}", norm((job.get("title") or "") + " " + (job.get("description") or "")))
    freq = {}
    for w in words:
        w = w.strip(".-")
        if w not in STOP and len(w) > 2:
            freq[w] = freq.get(w, 0) + 1
    return [w for w, _ in sorted(freq.items(), key=lambda x: -x[1])[:n]]


def _tags_for(job):
    tags = {AREA_TAG.get(job.get("area") or "", "PD")}
    if GAME.search(norm((job.get("title") or "") + " " + (job.get("description") or "")[:3000])):
        tags.add("GAME")
    if job.get("area") in ("design_eng", "frontend"):
        tags |= {"FE", "DE"}
    if job.get("area") in ("product_design", "uxui"):
        tags |= {"PD", "UX"}
    return tags


def bullet_score(b, tags, kws):
    t = norm(b["en"] + " " + b["pt"])
    s = 3 * len(tags & set(b["tags"]))
    s += sum(1 for k in kws[:30] if k in t)
    return s


def select(perfil, job, gem=None):
    """Plano do CV: headline, resumo, bullets por grupo, skills. Só IDs do banco aprovado."""
    lang = lang_of(job)
    tags, kws = _tags_for(job), keywords(job)
    groups = {}
    for b in perfil.bullets:
        if not b["cv_ok"]:
            continue
        g = "Infatec" if b["group"].startswith("Infatec") else b["group"]
        groups.setdefault(g, []).append(b)
    plan = {}
    for g, bs in groups.items():
        lim = LIMITS.get(g, 2)
        ranked = sorted(bs, key=lambda b: -bullet_score(b, tags, kws))
        if g == "Projetos" and not (tags & {"FE", "DE", "WEB", "CRM"}):
            ranked = []
        plan[g] = [b["id"] for b in ranked[:lim] if g != "Projetos" or bullet_score(b, tags, kws) >= 3]
    hl = AREA_HL.get(job.get("area") or "", "HL.PD")
    if gem:
        try:
            plan, hl = _ai_order(gem, perfil, job, plan, hl, lang)
        except Exception:  # noqa: BLE001  (sem IA o plano por regra já serve)
            pass
    skills = list(perfil.skills.get(lang) or perfil.skills.get("en") or [])
    # skills extras do banco (seção 7) que a vaga cita: entram na frente
    desc = norm((job.get("title") or "") + " " + (job.get("description") or ""))
    extra = []
    for row in perfil.skills.get("all", []):
        for item in re.split(r"[,;/()]", row):
            it = item.strip()
            if 2 < len(it) < 30 and norm(it) in desc and it.lower() not in (s.lower() for s in skills + extra):
                extra.append(it)
    hits = [s for s in skills if norm(s) in desc]
    rest = [s for s in skills if s not in hits]
    return dict(lang=lang, headline=hl, bullets=plan, skills=(hits + extra[:8] + rest)[:26], keywords=kws[:12])


def _ai_order(gem, perfil, job, plan, hl, lang):
    ids = {b["id"]: b for b in perfil.bullets if b["cv_ok"]}
    lista = "\n".join("%s [%s] %s" % (b["id"], " ".join(b["tags"]), b["en"][:220]) for b in ids.values())
    prompt = (
        "Você monta um CV para ATS. NÃO escreva texto: só escolha IDs. Vaga:\n"
        "Título: %s\nEmpresa: %s\nDescrição: %s\n\nBullets aprovados (ID [tags] texto):\n%s\n\n"
        "Headlines possíveis: %s\n\n"
        "Responda só JSON: {\"headline\": \"HL.x\", \"bullets\": {\"Lucky Gaming\": [ids até 6], \"Deadpixel Studios\": "
        "[até 2], \"Infatec\": [até 4], \"Freelance\": [até 1], \"Projetos\": [0 a 2]}} com os mais aderentes à vaga "
        "primeiro, cada ID no grupo de onde veio (prefixo B-LG Lucky, B-DP Deadpixel, B-IN Infatec, B-FL Freelance, B-PJ Projetos)."
    ) % (job.get("title"), job.get("company"), (job.get("description") or "")[:3500], lista, ", ".join(perfil.hl))
    out = json.loads(re.sub(r"^```(?:json)?|```$", "", gem.generate(prompt).strip()))
    pref = {"Lucky Gaming": "B-LG", "Deadpixel Studios": "B-DP", "Infatec": "B-IN", "Freelance": "B-FL", "Projetos": "B-PJ"}
    new = {}
    for g, cur in plan.items():
        got = [i for i in (out.get("bullets") or {}).get(g, []) if i in ids and i.startswith(pref.get(g, "?"))]
        got = list(dict.fromkeys(got))[: LIMITS.get(g, 2)]
        new[g] = got if (got or g == "Projetos") else cur
    h = out.get("headline")
    return new, (h if h in perfil.hl else hl)


# ---------------------------------------------------------------- render
def _period(e, lang):
    def f(v):
        m = re.match(r"(\d{4})-(\d{2})", v or "")
        return "%s %s" % (MESES[lang][int(m.group(2)) - 1], m.group(1)) if m else (v or "")
    return "%s – %s" % (f(e.get("inicio")), f(e.get("fim")))


def _exp_for_group(perfil, g):
    for e in perfil.exps:
        if e.get("nome", "").startswith(g.split()[0]) or (g == "Freelance" and e.get("nome") == "Freelance"):
            return e
    return None


def render_html(perfil, plan, job):
    lang = plan["lang"]
    t = L[lang]
    e_ = html.escape
    by_id = {b["id"]: b for b in perfil.bullets}
    hl = perfil.hl.get(plan["headline"], "")
    if lang == "pt":
        hl = ("Front-end Developer com background em Product Design" if plan["headline"] in ("HL.FE", "HL.DE")
              else "Product Designer (UX/UI) | Designer de Produto")
    resumo = perfil.res.get("RES.PD_PT" if lang == "pt" else "RES.PD_EN", "")
    contato = [perfil.d("DADOS.email"), perfil.d("DADOS.telefone"), perfil.d("DADOS.local_completo", lang),
               perfil.d("DADOS.portfolio"), perfil.d("DADOS.linkedin"), perfil.d("DADOS.github")]
    contato = [c.replace("https://", "") for c in contato if c]
    parts = ["<h1>%s</h1>" % e_(perfil.d("DADOS.nome") or "Victor Cavalcante"), "<p class=hl>%s</p>" % e_(hl),
             "<p class=ct>%s</p>" % " · ".join(e_(c) for c in contato),
             "<h2>%s</h2><p>%s</p>" % (t["resumo"], e_(resumo)), "<h2>%s</h2>" % t["exp"]]
    for g in ("Lucky Gaming", "Deadpixel Studios", "Infatec", "Freelance"):
        ids = plan["bullets"].get(g) or []
        e = _exp_for_group(perfil, g)
        if not e or not ids:
            continue
        cargo = e.get("cargo_%s" % lang) or e.get("cargo_en", "")
        if g == "Lucky Gaming" and lang == "en" and plan["headline"] in ("HL.DE", "HL.FE") and e.get("cargo_en_alt"):
            cargo = e["cargo_en_alt"]
        emp = e.get("empresa", g)
        local = e.get("local", "")
        if "/" in local:
            local = local.split("/")[1 if lang == "en" else 0].strip()
        if lang == "en":
            local = local.replace("Brasil", "Brazil")
        parts.append("<h3>%s — %s</h3><p class=meta>%s · %s</p><ul>%s</ul>" % (
            e_(cargo), e_(emp), e_(_period(e, lang)), e_(local),
            "".join("<li>%s</li>" % e_(by_id[i][lang]) for i in ids if i in by_id)))
    pj = [i for i in plan["bullets"].get("Projetos") or [] if i in by_id]
    if pj:
        parts.append("<h2>%s</h2><ul>%s</ul>" % (t["proj"], "".join("<li>%s</li>" % e_(by_id[i][lang]) for i in pj)))
    parts.append("<h2>%s</h2><p>%s</p>" % (t["skills"], e_(", ".join(plan["skills"]))))
    if perfil.form:
        parts.append("<h2>%s</h2><ul>%s</ul>" % (t["form"], "".join(
            "<li>%s</li>" % e_((f["en"] if lang == "en" and f["en"] else f["pt"])) for f in perfil.form)))
    if perfil.certs:
        parts.append("<h2>%s</h2><p>%s</p>" % (t["cert"], e_(" · ".join(perfil.certs))))
    idi = [perfil.d(k, lang) for k in ("IDI.portugues", "IDI.ingles", "IDI.espanhol")]
    nomes = ("Português", "Inglês", "Espanhol") if lang == "pt" else ("Portuguese", "English", "Spanish")
    parts.append("<h2>%s</h2><p>%s</p>" % (t["idi"], e_(" · ".join("%s: %s" % (n, v) for n, v in zip(nomes, idi) if v))))
    css = ("body{font-family:Arial,Helvetica,sans-serif;font-size:10.2pt;line-height:1.38;color:#111;margin:0}"
           "h1{font-size:19pt;margin:0 0 2pt}h2{font-size:11pt;text-transform:uppercase;letter-spacing:.04em;"
           "border-bottom:1px solid #999;margin:11pt 0 4pt;padding-bottom:2pt}h3{font-size:10.5pt;margin:7pt 0 0}"
           "p{margin:2pt 0}ul{margin:2pt 0 0 14pt;padding:0}li{margin:0 0 2.5pt}.hl{font-weight:bold}"
           ".ct,.meta{color:#333;font-size:9.4pt}")
    return ("<!DOCTYPE html><html lang=%s><head><meta charset=utf-8><title>CV Victor Cavalcante</title>"
            "<style>@page{size:A4;margin:14mm 15mm}%s</style></head><body>%s</body></html>") % (lang, css, "".join(parts))


def pdf(html_doc, path, browser=None):
    """Gera o PDF. Usa um browser aberto (Playwright) se vier; senão abre um."""
    if browser is None:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            b = p.chromium.launch()
            try:
                return pdf(html_doc, path, b)
            finally:
                b.close()
    pg = browser.new_page()
    try:
        pg.set_content(html_doc, wait_until="load")
        pg.pdf(path=path, format="A4", print_background=False)
    finally:
        pg.close()
    return path


def file_name(job, lang):
    emp = re.sub(r"[^A-Za-z0-9]+", "_", job.get("company") or "Empresa").strip("_")[:30] or "Empresa"
    return ("CV_Victor_Cavalcante_%s.pdf" % emp) if lang == "en" else ("Curriculo_Victor_Cavalcante_%s.pdf" % emp)
