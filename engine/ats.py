"""Preenche e envia formulários de ATS abertos (Greenhouse, Lever, Ashby, Workable, Recruitee, SmartRecruiters)
com Playwright. Regras: nunca resolve captcha; pergunta obrigatória sem resposta aprovada = kit; LinkedIn nunca.
Modo ensaio: preenche tudo e tira print, mas não clica em enviar."""
import re
import time

from .respostas import KIT, SKIP, Respostas, pick_option
from .textnorm import norm

SUCCESS = re.compile(r"thank(s| you)[^.]{0,60}(appl|interest|submit)|application (has been |was )?(submitted|received|sent)|"
                     r"we.?ve received your application|successfully (submitted|applied)|obrigad[oa][^.]{0,60}candidat|"
                     r"candidatura (foi )?(enviada|recebida|realizada)|recebemos (a )?sua candidatura|"
                     r"your application has been", re.I)
CLOSED = re.compile(r"no longer (accepting|available|open)|position (has been )?(filled|closed)|job (is )?closed|"
                    r"vaga (encerrada|fechada|expirada)|page not found|404", re.I)
CAPTCHA_SEL = ("iframe[src*='hcaptcha.com'][src*='checkbox'], iframe[title*='hCaptcha'], iframe[src*='recaptcha/api2/bframe'],"
               " iframe[src*='recaptcha'][src*='anchor'], iframe[src*='challenges.cloudflare.com'], .cf-turnstile")
SUBMIT_SEL = ("button[type=submit], input[type=submit], button:has-text('Submit application'), button:has-text('Submit'),"
              " button:has-text('Apply'), button:has-text('Enviar candidatura'), button:has-text('Enviar'),"
              " button:has-text('Candidatar')")
NEXT_SEL = "button:has-text('Next'), button:has-text('Continue'), button:has-text('Próximo'), button:has-text('Continuar')"
ATS_HOST = re.compile(r"greenhouse\.io|lever\.co|ashbyhq\.com|workable\.com|smartrecruiters\.com|recruitee\.com")


class Abort(Exception):
    """Vaga não pode ir sozinha: vira kit com o motivo."""

    def __init__(self, reason, questions=None):
        super().__init__(reason)
        self.reason, self.questions = reason, questions or []


def apply_url(url):
    """URL do formulário a partir do link da vaga."""
    u = url.split("#")[0]
    if "jobs.lever.co" in u and not re.search(r"/apply/?$", u):
        return u.rstrip("/") + "/apply"
    if "jobs.ashbyhq.com" in u and "/application" not in u:
        return u.split("?")[0].rstrip("/") + "/application"
    if "apply.workable.com" in u and not re.search(r"/apply/?$", u):
        return u.split("?")[0].rstrip("/") + "/apply/"
    return u


# JS: lista os campos do formulário com rótulo, tipo, opções e se é obrigatório
FIELDS_JS = r"""
() => {
  const vis = (el) => { const r = el.getBoundingClientRect(); const s = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none'; };
  const txt = (el) => (el ? (el.innerText || el.textContent || '') : '').replace(/\s+/g, ' ').trim();
  const labelOf = (el) => {
    if (el.id) { const l = document.querySelector(`label[for="${CSS.escape(el.id)}"]`); if (l && txt(l)) return txt(l); }
    if (el.getAttribute('aria-labelledby')) {
      const t = el.getAttribute('aria-labelledby').split(/\s+/).map(i => txt(document.getElementById(i))).join(' ');
      if (t.trim()) return t; }
    if (el.getAttribute('aria-label')) return el.getAttribute('aria-label');
    const l = el.closest('label'); if (l && txt(l)) return txt(l);
    let p = el.parentElement;
    for (let i = 0; i < 5 && p; i++, p = p.parentElement) {
      const c = p.querySelector('label, legend, .application-label, [class*="label"], [class*="question"]');
      if (c && !c.contains(el) && txt(c)) return txt(c);
    }
    return el.getAttribute('placeholder') || el.name || '';
  };
  const out = []; const seen = new Set(); let i = 0;
  for (const el of document.querySelectorAll('input, textarea, select, [role=combobox]')) {
    const type = (el.getAttribute('type') || el.tagName).toLowerCase();
    if (['hidden', 'submit', 'button', 'search', 'image', 'reset'].includes(type)) continue;
    if (type !== 'file' && !vis(el) && !['radio', 'checkbox'].includes(type)) continue;
    const key = 'f' + (i++); el.setAttribute('data-robo', key);
    let label = labelOf(el);
    let kind = el.tagName === 'TEXTAREA' ? 'textarea' : el.tagName === 'SELECT' ? 'select' : type;
    if (el.getAttribute('role') === 'combobox' && el.tagName === 'INPUT') kind = 'combobox';
    const req = el.required || el.getAttribute('aria-required') === 'true' || /\*\s*$|\(required\)|obrigat/i.test(label);
    const item = { key, kind, label: label.replace(/\s*\*\s*$/, '').slice(0, 300), required: req, name: el.name || '',
                   value: el.value || '', options: [] };
    if (kind === 'select') item.options = [...el.options].map(o => o.textContent.trim());
    if (kind === 'radio' || kind === 'checkbox') {
      const g = el.name || key; if (seen.has(g) && el.name) continue; seen.add(g);
      const group = el.name ? [...document.querySelectorAll(`input[name="${CSS.escape(el.name)}"]`)] : [el];
      group.forEach((r, j) => r.setAttribute('data-robo-opt', key + '_' + j));
      item.options = group.map(r => { const l = r.id && document.querySelector(`label[for="${CSS.escape(r.id)}"]`);
        return txt(l) || txt(r.closest('label')) || r.value; });
      const fs = el.closest('fieldset'); const lg = fs && fs.querySelector('legend');
      if (group.length > 1) { item.label = (lg && txt(lg)) || txt(el.closest('[class*="question"], .field, li, div')?.querySelector('label, legend, [class*="label"]')) || item.label; }
      item.required = item.required || group.some(r => r.required);
    }
    out.push(item);
  }
  return out;
}
"""


def _visible_captcha(page):
    """Só conta desafio que a pessoa veria (o hCaptcha invisível do Lever fica escondido e não bloqueia)."""
    for fr in page.locator(CAPTCHA_SEL).all():
        try:
            if not fr.is_visible():
                continue
            box = fr.bounding_box()
        except Exception:  # noqa: BLE001
            continue
        if box and box["width"] > 60 and box["height"] > 60 and box["x"] >= 0 and box["y"] >= 0:
            return True
    return False


def _close_cookies(page):
    for sel in ("button:has-text('Deny')", "button:has-text('Reject')", "button:has-text('Recusar')",
                "button:has-text('Accept')", "button:has-text('Aceitar')"):
        loc = page.locator(sel).first
        try:
            if loc.count() and loc.is_visible():
                loc.click(timeout=3000)
                page.wait_for_timeout(500)
                return
        except Exception:  # noqa: BLE001
            pass


def _resolve_form_page(page, url):
    page.goto(url, wait_until="domcontentloaded", timeout=45000)
    page.wait_for_timeout(2500)
    if CLOSED.search((page.title() or "") + " " + (page.inner_text("body")[:3000] if page.locator("body").count() else "")):
        raise Abort("vaga fechada")
    # página de carreira com o formulário em iframe do ATS (ex.: Greenhouse embed): abre o iframe direto
    if not ATS_HOST.search(page.url):
        for f in page.frames:
            if ATS_HOST.search(f.url or "") and ("embed" in f.url or "apply" in f.url or "job_app" in f.url):
                page.goto(f.url, wait_until="domcontentloaded", timeout=45000)
                page.wait_for_timeout(2000)
                break
    # botão "Apply" que abre o formulário na mesma página
    if page.locator("input[type=file]").count() == 0:
        for sel in ("a:has-text('Apply for this job')", "button:has-text('Apply for this job')", "a:has-text('Apply now')",
                    "button:has-text('Apply now')", "button:has-text('Apply')", "a:has-text('Candidatar-se')",
                    "button:has-text('Candidatar-se')", "a:has-text('I\\'m interested')"):
            loc = page.locator(sel).first
            if loc.count() and loc.is_visible():
                try:
                    loc.click(timeout=5000)
                    page.wait_for_timeout(2500)
                except Exception:  # noqa: BLE001
                    pass
                if page.locator("input[type=file]").count():
                    break
    if "linkedin.com" in page.url:
        raise Abort("formulário leva ao LinkedIn")
    if page.locator("input[type=password]").count() and page.locator("input[type=file]").count() == 0:
        raise Abort("pede login/conta")


def _fill_combobox(page, loc, value):
    loc.click(timeout=5000)
    loc.fill("")
    loc.type(value[:40], delay=30)
    page.wait_for_timeout(1200)
    opt = page.locator("[role=option]").first
    if opt.count() and opt.is_visible():
        opt.click(timeout=4000)
    else:
        page.keyboard.press("Enter")
    page.wait_for_timeout(300)


def _check(page, f, j=0, text=None):
    """Marca checkbox/radio: clique normal, depois clique no texto, depois JS. True se ficou marcado."""
    loc = page.locator('[data-robo-opt="%s_%d"]' % (f["key"], j)).first
    text = (text or f["label"])[:60]
    tries = (lambda: loc.check(timeout=4000, force=True),
             lambda: page.get_by_text(text, exact=False).first.click(timeout=4000),
             lambda: loc.evaluate("e => { e.checked = true; e.dispatchEvent(new Event('input', {bubbles: true}));"
                                  " e.dispatchEvent(new Event('change', {bubbles: true})); }"))
    for t in tries:
        try:
            if loc.is_checked():
                return True
            t()
            page.wait_for_timeout(300)
        except Exception:  # noqa: BLE001
            continue
    try:
        return loc.is_checked()
    except Exception:  # noqa: BLE001
        return False


def fill_form(page, resp, cv_path, cover_path=None):
    """Preenche o que estiver na tela. Devolve (preenchidos, faltando_obrigatorios)."""
    fields = page.evaluate(FIELDS_JS)
    filled, missing, uploaded = 0, [], False
    files = [f for f in fields if f["kind"] == "file"]
    for f in fields:
        loc = page.locator('[data-robo="%s"]' % f["key"])
        lab = norm(f["label"] + " " + f["name"])
        try:
            if f["kind"] == "file":
                if re.search(r"cover|carta|motivation", lab):
                    if cover_path and f["required"]:
                        loc.set_input_files(cover_path)
                    continue
                if re.search(r"resume|cv\b|curr|curriculo|attach", lab) or len(files) == 1 or not uploaded:
                    loc.set_input_files(cv_path)
                    uploaded = True
                    filled += 1
                    page.wait_for_timeout(1500)
                continue
            if f["kind"] in ("text", "email", "tel", "url", "number", "textarea", "input", "combobox", "date"):
                if f["value"].strip():
                    continue
                v = resp.answer(f["label"] or f["name"], "textarea" if f["kind"] == "textarea" else "text")
                if v == KIT:
                    raise Abort("pergunta que vira kit: %s" % f["label"][:80], [f["label"]])
                if v == SKIP and f["required"]:  # empresa atual obrigatória: "Nenhuma (último: ...)"
                    v = resp.dado("DADOS.empresa_atual")
                if v in (None, SKIP):
                    if f["required"]:
                        missing.append(f["label"] or f["name"])
                    continue
                if f["kind"] == "number":
                    m = re.search(r"\d+", v.replace(".", "").replace(",", ""))
                    v = m.group(0) if m else v
                if f["kind"] == "combobox":
                    _fill_combobox(page, loc, v)
                else:
                    loc.fill(v, timeout=5000)
                filled += 1
            elif f["kind"] == "select":
                v = resp.answer(f["label"] or f["name"], "select", f["options"])
                if v == KIT:
                    raise Abort("pergunta que vira kit: %s" % f["label"][:80], [f["label"]])
                opt = pick_option(v, f["options"]) if v not in (None, SKIP) else None
                if opt:
                    loc.select_option(label=opt, timeout=5000)
                    filled += 1
                elif f["required"]:
                    missing.append(f["label"])
            elif f["kind"] in ("radio", "checkbox"):
                v = resp.answer(f["label"] or f["name"], f["kind"], f["options"])
                if v == KIT:
                    raise Abort("pergunta que vira kit: %s" % f["label"][:80], [f["label"]])
                if f["kind"] == "checkbox" and len(f["options"]) == 1:
                    # checkbox único: só marca consentimento/termos (resposta "Sim")
                    if v and pick_option(v, ["Yes", "Sim"]):
                        if _check(page, f):
                            filled += 1
                        elif f["required"]:
                            missing.append(f["label"])
                    elif f["required"]:
                        missing.append(f["label"])
                    continue
                # grupo de checkboxes de consentimento (mesmo name): marca os que são "concordo/autorizo"
                if f["kind"] == "checkbox" and v and pick_option(v, ["Yes", "Sim"]):
                    idx = [i for i, o in enumerate(f["options"])
                           if re.search(r"concordo|agree|aceito|accept|autorizo|consinto|consent|li e|i have read", norm(o))]
                    if idx:
                        ok = [_check(page, f, i, f["options"][i]) for i in idx]
                        if all(ok):
                            filled += 1
                        elif f["required"]:
                            missing.append(f["label"])
                        continue
                opt = pick_option(v, f["options"]) if v not in (None, SKIP) else None
                if opt:
                    j = f["options"].index(opt)
                    if _check(page, f, j, opt):
                        filled += 1
                    elif f["required"]:
                        missing.append(f["label"])
                elif f["required"]:
                    missing.append(f["label"])
        except Abort:
            raise
        except Exception as e:  # noqa: BLE001
            if f["required"]:
                missing.append("%s (%s)" % (f["label"][:60], type(e).__name__))
    if not uploaded and files:
        missing.append("upload do CV")
    return filled, missing


def _errors(page):
    txt = []
    for sel in ("[role=alert]", ".error", ".field-error", "[class*='error']", "[aria-invalid=true]"):
        for el in page.locator(sel).all()[:6]:
            try:
                if el.is_visible():
                    t = (el.inner_text() or el.get_attribute("aria-label") or "").strip()
                    if t and len(t) < 200:
                        txt.append(t)
            except Exception:  # noqa: BLE001
                pass
    return list(dict.fromkeys(txt))[:5]


def run(browser, job, perfil, cv_path, mode="real", cover_path=None, learned=(), gem=None, shot_path="/tmp/shot.png",
        source_name=""):
    """Aplica numa vaga. Devolve dict(status='enviada'|'conferir'|'ensaio'|'kit', reason, answers, shot)."""
    ctx = browser.new_context(locale="en-US", viewport={"width": 1280, "height": 1800},
                              user_agent=("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                                          "(KHTML, like Gecko) Chrome/131.0 Safari/537.36"))
    page = ctx.new_page()
    page.set_default_timeout(15000)
    resp = Respostas(perfil, job, learned, gem, source_name)
    out = dict(status="kit", reason="", answers=resp.log, shot=None, questions=[])
    try:
        if "linkedin.com" in (job.get("url") or ""):
            raise Abort("LinkedIn nunca é enviado pelo robô")
        _resolve_form_page(page, apply_url(job["url"]))
        _close_cookies(page)
        if _visible_captcha(page):
            raise Abort("captcha na página")
        total, missing = 0, []
        for step in range(5):  # formulários em etapas (SmartRecruiters, Recruitee)
            n, miss = fill_form(page, resp, cv_path, cover_path)
            total += n
            missing += miss
            if missing:
                break
            nxt = page.locator(NEXT_SEL).first
            if step < 4 and nxt.count() and nxt.is_visible() and not page.locator(SUBMIT_SEL).first.is_visible():
                nxt.click(timeout=5000)
                page.wait_for_timeout(2500)
                continue
            break
        if missing:
            raise Abort("pergunta obrigatória sem resposta aprovada", missing)
        if total < 3:
            raise Abort("não achei o formulário")
        if _visible_captcha(page):
            raise Abort("captcha no formulário")
        if mode != "real":
            page.screenshot(path=shot_path, full_page=True)
            out.update(status="ensaio", reason="preenchido, não enviado (modo ensaio)", shot=shot_path)
            return out
        btn = page.locator(SUBMIT_SEL).last
        if not btn.count():
            raise Abort("botão de enviar não encontrado")
        btn.scroll_into_view_if_needed()
        before = page.url
        btn.click(timeout=8000)
        ok = False
        for _ in range(20):
            page.wait_for_timeout(1000)
            body = page.inner_text("body")[:6000] if page.locator("body").count() else ""
            if SUCCESS.search(body) or (page.url != before and re.search(r"confirm|thank|success|obrigad", page.url, re.I)):
                ok = True
                break
            if _visible_captcha(page):
                raise Abort("captcha depois do enviar (não resolvo captcha)")
        page.screenshot(path=shot_path, full_page=True)
        out["shot"] = shot_path
        if not ok:
            errs = _errors(page)
            if errs:  # formulário recusou (campo inválido): não saiu
                raise Abort("o formulário recusou: " + " | ".join(errs), errs)
            out.update(status="conferir", reason="cliquei em enviar e não vi confirmação nem erro")
            return out
        out.update(status="enviada", reason="confirmação na tela")
        return out
    except Abort as e:
        out.update(status="kit", reason=e.reason, questions=e.questions)
        if not out["shot"]:
            try:
                page.screenshot(path=shot_path, full_page=True)
                out["shot"] = shot_path
            except Exception:  # noqa: BLE001
                pass
        return out
    except Exception as e:  # noqa: BLE001
        out.update(status="kit", reason="erro no navegador: %s" % type(e).__name__)
        return out
    finally:
        try:
            ctx.close()
        except Exception:  # noqa: BLE001
            pass
        time.sleep(0.5)
