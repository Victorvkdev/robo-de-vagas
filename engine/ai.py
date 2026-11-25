"""Gemini (plano grátis) para o card da vaga: resumo de 1 linha, motivo da nota e riscos.
Preso ao super contexto e à descrição da vaga. Se falhar, a lista sai mesmo assim com as regras locais."""
import json
import os
import re
import time

import requests

BASE = "https://generativelanguage.googleapis.com/v1beta"
BATCH = 20

PROMPT = """Você prepara cards de vagas para o Vk, em português do Brasil, curtos e factuais.
Use SÓ a descrição de cada vaga e o perfil abaixo. Nunca invente empresa, número, salário ou requisito.
Se a descrição não diz, não afirme.

PERFIL (resumo do super contexto):
{perfil}

Para cada vaga devolva um objeto JSON:
- "n": o número da vaga
- "resumo": 1 frase de até 110 caracteres dizendo o que a pessoa vai fazer e o produto/empresa
- "motivo": até 70 caracteres, por que combina com o perfil
- "riscos": lista com até 2 avisos curtos (até 40 caracteres cada) que o Vk precisa saber antes de aplicar,
  ex.: "pede 7 anos", "só EUA", "teste longo", "inglês fluente", "presencial 1x/mês". Lista vazia se não houver.

Responda só com um array JSON.

VAGAS:
{vagas}
"""


# ordem de preferência; chaves novas não acessam modelos antigos e alguns 'preview' têm cota zero no plano grátis
PREFERRED = ("gemini-flash-latest", "gemini-3-flash", "gemini-2.5-flash", "gemini-flash-lite-latest",
             "gemini-3.1-flash-lite", "gemini-2.5-flash-lite")


class Gemini:
    def __init__(self, key, model=None):
        self.key = key
        self.forced = model or os.environ.get("GEMINI_MODEL")
        self.model = None
        self.queue = None
        self.calls = 0

    @classmethod
    def from_env(cls):
        k = os.environ.get("GEMINI_API_KEY")
        return cls(k) if k else None

    def _candidates(self):
        if self.forced:
            return [self.forced]
        try:
            r = requests.get(BASE + "/models", params={"key": self.key}, timeout=30)
            r.raise_for_status()
            names = {m["name"].split("/", 1)[-1] for m in r.json().get("models", [])
                     if "generateContent" in (m.get("supportedGenerationMethods") or [])}
        except Exception:  # noqa: BLE001
            return list(PREFERRED)
        ok = [m for m in PREFERRED if m in names]
        return ok or sorted(n for n in names if "flash" in n and not re.search(r"preview|image|tts|omni|live", n))

    def _post(self, model, body):
        return requests.post("%s/models/%s:generateContent" % (BASE, model), params={"key": self.key}, json=body,
                             timeout=120)

    def generate(self, prompt):
        body = {"contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": {"temperature": 0.2, "responseMimeType": "application/json"}}
        if self.queue is None:
            self.queue = self._candidates()
        last = "sem modelo disponível"
        while self.queue:
            model = self.queue[0]
            for attempt in range(2):
                r = self._post(model, body)
                self.calls += 1
                if r.status_code in (500, 503) or (r.status_code == 429 and attempt == 0):
                    time.sleep(20)
                    continue
                break
            if r.ok:
                self.model = model
                parts = ((r.json().get("candidates") or [{}])[0].get("content") or {}).get("parts") or []
                return "".join(p.get("text", "") for p in parts)
            try:
                msg = r.json().get("error", {}).get("message", "")
            except ValueError:
                msg = r.text
            last = "HTTP %s em %s: %s" % (r.status_code, model, msg[:160])
            if r.status_code in (404, 429, 403, 400, 500, 503) and len(self.queue) > 1:
                self.queue.pop(0)  # tenta o próximo modelo
                continue
            break
        raise RuntimeError(last)


def _parse(text):
    text = text.strip()
    text = re.sub(r"^```(?:json)?|```$", "", text).strip()
    try:
        d = json.loads(text)
    except ValueError:
        m = re.search(r"\[.*\]", text, re.S)
        d = json.loads(m.group(0)) if m else []
    return d if isinstance(d, list) else []


def perfil_curto(body, limit=5000):
    """Só os blocos úteis pro card (sem telefone/e-mail)."""
    if not body:
        return "Product Designer e Front-end Developer, 6+ anos, iGaming/CRM/gamificação, mora no Brasil, só remoto."
    body = re.sub(r"[\w.+-]+@[\w-]+\.[\w.]+", "[e-mail]", body)
    body = re.sub(r"\+?\d[\d\s().-]{8,}\d", "[telefone]", body)
    return body[:limit]


def enrich(gem, jobs, perfil):
    """jobs: lista de dict(n, title, company, location, salary, description). Devolve {n: dict(resumo, motivo, riscos)}."""
    out = {}
    for i in range(0, len(jobs), BATCH):
        part = jobs[i:i + BATCH]
        vagas = "\n\n".join(
            "#%d %s — %s | local: %s | salário: %s\n%s" % (
                j["n"], j["title"], j.get("company") or "?", j.get("location") or "?", j.get("salary") or "não informado",
                re.sub(r"\s+", " ", (j.get("description") or ""))[:1800])
            for j in part)
        try:
            items = _parse(gem.generate(PROMPT.format(perfil=perfil, vagas=vagas)))
        except Exception as e:  # noqa: BLE001
            print("gemini: falhou no lote %d (%s)" % (i // BATCH + 1, str(e)[:260]))
            continue
        for it in items:
            try:
                n = int(it.get("n"))
            except (TypeError, ValueError):
                continue
            riscos = [str(x)[:45] for x in (it.get("riscos") or []) if x][:2]
            out[n] = dict(resumo=str(it.get("resumo") or "")[:140], motivo=str(it.get("motivo") or "")[:90], riscos=riscos)
    return out


def resumo_regra(job):
    """Resumo sem IA: primeira frase útil da descrição."""
    d = re.sub(r"\s+", " ", job.get("description") or "").strip()
    for s in re.split(r"(?<=[.!?])\s+", d):
        if 40 <= len(s) <= 160 and not re.search(r"cookie|equal opportunity|privacy", s, re.I):
            return s
    return d[:120] + ("…" if len(d) > 120 else "")


# ---------------------------------------------------------------- e-mail (entrega 4)
MAIL_CLASSES = ("entrevista", "agendar", "teste", "recrutador", "recusa", "recebida", "oferta", "orcamento", "spam")

CLASSIFY_PROMPT = """Classifique o e-mail que o Vk (candidato a vagas de Product Design / UX / Front-end) recebeu.
Classes:
- entrevista: entrevista JÁ marcada, com data/hora
- agendar: querem marcar entrevista/conversa e pedem horário ou mandam link de agenda
- teste: teste técnico, desafio, case ou assessment
- recrutador: recrutador/empresa pergunta algo ou espera resposta (pretensão, disponibilidade, interesse)
- recusa: não seguiram com a candidatura
- recebida: confirmação automática de candidatura recebida
- oferta: proposta de emprego
- orcamento: alguém pede orçamento/projeto freelance
- spam: newsletter, alerta de vagas, marketing, comunidade, qualquer outra coisa
Hoje é {hoje} (fuso do Vk: America/Fortaleza, UTC-3).
Responda só com JSON: {{"classe": "...", "quando": "data/hora da entrevista em ISO 8601 COM fuso (ex.: 2026-12-02T14:00:00-05:00) ou vazio", "prazo": "prazo do teste em ISO 8601 ou vazio"}}
Se o e-mail não diz o fuso, use o do remetente se for óbvio; senão -03:00.

DE: {de}
ASSUNTO: {assunto}
TEXTO:
{texto}
"""

DRAFT_PROMPT = """Você escreve a resposta de e-mail do Vk (Victor Cavalcante) para um recrutador.
Regras:
- Idioma: {idioma}, escrito de forma natural e nativa. Tom educado, direto, curto (até 120 palavras).
- Use SÓ fatos do PERFIL abaixo. Nunca invente número, empresa, ferramenta, prazo ou salário.
- Se perguntarem algo que o PERFIL não responde, diga que confirma e responde em seguida.
- Pretensão salarial: use os valores do PERFIL para o regime/nível da vaga; se não der pra saber, diga que está aberto a conversar dentro da faixa da vaga.
- Comece com saudação ({saudacao}). Não escreva assinatura nem assunto.
{extra}
Responda só com JSON: {{"resposta": "texto do e-mail"}}

PERFIL:
{perfil}

E-MAIL RECEBIDO (de {de}, assunto "{assunto}"):
{texto}
"""


def mail_classify(gem, de, assunto, texto, hoje):
    try:
        out = gem.generate(CLASSIFY_PROMPT.format(hoje=hoje, de=de, assunto=assunto, texto=texto[:3500]))
        d = json.loads(re.sub(r"^```(?:json)?|```$", "", out.strip()).strip())
    except Exception as e:  # noqa: BLE001
        print("gemini e-mail: classificação falhou (%s)" % type(e).__name__)
        return None
    if not isinstance(d, dict) or d.get("classe") not in MAIL_CLASSES:
        return None
    return d


def mail_draft(gem, perfil, de, assunto, texto, lang, saudacao, extra=""):
    idioma = "português do Brasil" if lang == "pt" else ("espanhol" if lang == "es" else "inglês")
    try:
        out = gem.generate(DRAFT_PROMPT.format(idioma=idioma, saudacao=saudacao, extra=extra, perfil=perfil, de=de,
                                               assunto=assunto, texto=texto[:3000]))
        d = json.loads(re.sub(r"^```(?:json)?|```$", "", out.strip()).strip())
        r = str(d.get("resposta") or "").strip() if isinstance(d, dict) else ""
        return r or None
    except Exception as e:  # noqa: BLE001
        print("gemini e-mail: rascunho falhou (%s)" % type(e).__name__)
        return None


_NUMTOK = re.compile(r"\d[\d.,]*%?")


def unverified_numbers(text, *allowed_sources):
    """Números do rascunho que não aparecem no perfil nem no e-mail (checagem de fatos)."""
    pool = " ".join(s or "" for s in allowed_sources)
    pool_n = {re.sub(r"[.,%]", "", m) for m in _NUMTOK.findall(pool)}
    bad = []
    for m in _NUMTOK.findall(text or ""):
        k = re.sub(r"[.,%]", "", m)
        if k and k not in pool_n and m not in bad:
            bad.append(m.rstrip(".,"))
    return bad
