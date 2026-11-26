"""Resposta para cada campo de formulário, só com dados do Super Contexto (seções 1, 2, 3, 8, 11 e 14).
Campo que não tem resposta aprovada volta None: se for obrigatório, a vaga vira kit (nunca inventa)."""
import json
import re

from .textnorm import norm

KIT = "__KIT__"     # pergunta que, pela regra, sempre vira kit (antecedentes, CPF, RG...)
SKIP = "__SKIP__"   # deixa em branco (empresa atual, campo opcional sem dado)

# (regex no rótulo normalizado, chave DADOS/IDI/especial). Ordem importa: o primeiro que casar vale.
RULES = [
    (r"criminal|antecedente|convicted|felony", KIT),
    (r"\bcpf\b|\brg\b|identidade|social security|\bssn\b|passport|passaporte|date of birth|birth ?date|"
     r"data de nascimento|nascimento|home address|street address|endereco|\bcep\b|zip|postal code|bank", KIT),
    (r"first name|primeiro nome|^nome$|given name", "DADOS.primeiro_nome"),
    (r"last name|sobrenome|surname|family name", "DADOS.sobrenome"),
    (r"preferred name|nome preferido|nickname", "DADOS.nome_preferido"),
    (r"full name|nome completo|^name$|^your name|legal name|^nome\b", "DADOS.nome"),
    (r"e-?mail", "DADOS.email"),
    (r"phone|telefone|celular|mobile|whatsapp", "DADOS.telefone"),
    (r"linkedin", "DADOS.linkedin"),
    (r"github", "DADOS.github"),
    (r"behance|dribbble", "DADOS.behance"),
    (r"portfolio password|senha do portfolio", "DADOS.senha_portfolio"),
    (r"portfolio|portifolio|website|personal site|site pessoal|\burl\b|other link|link", "DADOS.portfolio"),
    (r"current (company|employer)|company where you (currently )?work|empresa (em |onde )?(que )?(voce )?trabalha|empresa atual|most recent (company|employer)|^company$|^organization$", SKIP),
    (r"current (title|role|position)|cargo atual", "DADOS.cargo_atual"),
    (r"currently employed|empregado atualmente|esta trabalhando", "DADOS.empregado_agora"),
    (r"years of (relevant |professional )?experience|anos de experiencia|how many years", "DADOS.anos_experiencia_numero"),
    (r"salary|compensation|pretensao|remuneracao|expected (pay|rate)|rate expectation|desired pay|hourly rate", "PRET"),
    (r"notice period|aviso previo", "DADOS.aviso_previo"),
    (r"start date|when can you start|earliest start|disponibilidade|availability|data de inicio|quando pode comecar",
     "DADOS.disponibilidade"),
    (r"sponsor", "DADOS.precisa_sponsorship_remoto"),
    (r"(authori[sz]ed|eligible|legally|right) to work.*(united states|u\.s|\bus\b|usa|america)", "DADOS.autorizacao_eua"),
    (r"(authori[sz]ed|eligible|legally|right) to work.*(uk|united kingdom|europe|\beu\b|european)", "DADOS.autorizacao_ue_uk"),
    (r"(authori[sz]ed|eligible|legally|right) to work.*(brazil|brasil)", "DADOS.autorizacao_brasil"),
    (r"(authori[sz]ed|eligible|legally|right) to work|work authori[sz]ation|work permit", "AUTH_GENERIC"),
    (r"relocat|mudar de cidade|mudanca", "DADOS.relocation"),
    (r"travel|viagem|viajar", "DADOS.viagem"),
    (r"overlap|work .*(hours|time ?zone)|any time ?zone|qualquer fuso", "DADOS.overlap_eua_europa"),
    (r"time ?zone|fuso", "DADOS.fuso"),
    (r"remote|remoto|hybrid|hibrido|on-?site|presencial|work model|modelo de trabalho", "DADOS.modelo_trabalho"),
    (r"^current location|^localizacao atual", "DADOS.local_completo"),
    (r"located in|where are you (based|located)|country of residence|pais de residencia|^country|^pais",
     "DADOS.pais"),
    (r"^location|^city|cidade|localizacao|where do you live|onde voce mora", "DADOS.local_completo"),
    (r"^state|^estado|province", "DADOS.estado"),
    (r"how did you (hear|find|learn)|como (soube|conheceu|ficou sabendo)|referral source|^source$", "SOURCE"),
    (r"referred|referral|indicad|who referred", "DADOS.indicado_por"),
    (r"previously (worked|employed)|worked (for|at) .* before|ja trabalhou", "DADOS.ja_trabalhou_na_empresa"),
    (r"(applied|apply) .* before|ja se candidatou|previously applied", "DADOS.ja_aplicou_antes"),
    (r"18 years|over 18|maior de (idade|18)|legal age", "DADOS.maior_18"),
    (r"english|ingles", "IDI.ingles_dropdown"),
    (r"spanish|espanhol", "IDI.espanhol"),
    (r"portuguese|portugues", "IDI.portugues"),
    (r"background check|verificacao de antecedentes", "SENS.consentimento_bg"),
    (r"non-?compete|nao concorrencia|exclusividade", "SENS.nao_concorrencia"),
    (r"relative|family member|parente|familiar", "SENS.parente_empresa"),
    (r"conflict of interest|conflito de interesse", "SENS.conflito_interesse"),
    (r"privacy|privacidade|terms|termos|consent|consentimento|gdpr|data processing|acknowledge|i agree|concordo|"
     r"lgpd|retain my|keep my (data|information)|future (opportunities|roles)", "SENS.privacidade"),
    (r"gender|genero|sexo|pronoun|pronome", "DADOS.genero"),
    (r"race|ethnic|raca|etnia|hispanic|latino", "DADOS.raca_etnia"),
    (r"veteran|veterano", "DADOS.veterano"),
    (r"disabilit|deficiencia|pcd", "DADOS.deficiencia"),
    (r"sexual orientation|orientacao sexual|lgbt", "DADOS.orientacao"),
    (r"degree|bachelor|bacharel|graduacao|education level|escolaridade", "FORM.nivel_maior"),
    (r"equipment|equipamento|computer|internet", "DADOS.equipamento"),
    (r"take-?home|design challenge|teste pratico|assessment", "DADOS.teste_pratico"),
    (r"contract type|regime|employment type|tipo de contrato|cnpj|contractor", "DADOS.regime"),
    (r"current salary|salario atual|ultimo salario", "SENS.salario_atual"),
]
_RULES = [(re.compile(p), k) for p, k in RULES]
COVER = re.compile(r"cover letter|carta de apresenta|motivation letter|why .* (us|company|role|position)|"
                   r"additional information|anything else|algo mais|mensagem|message to|tell us|conte")


def yes_no(text):
    t = norm(text)
    if re.match(r"^(sim|yes)\b", t):
        return "yes"
    if re.match(r"^(nao|no|none|nenhum)\b", t) or t.startswith("i am not") or t.startswith("not "):
        return "no"
    return None


class Respostas:
    def __init__(self, perfil, job, learned_db=(), gem=None, source_name=""):
        self.p, self.job, self.gem = perfil, job, gem
        self.lang = "pt" if (job.get("lang") == "pt") else "en"
        self.learned = list(perfil.learned) + [(q, a) for q, a in learned_db]
        self.source_name = source_name
        self.log = []  # (rótulo, resposta, origem)

    # ------------------------------------------------------------ valores
    def dado(self, key):
        if key.startswith("SENS."):
            v = self.p.sens.get(key)
            if not v:
                return None
            ans = v[1] if len(v) > 1 else v[0]
            if "Vira kit" in ans:
                return KIT
            if " / " in ans:
                pt, en = ans.split(" / ", 1)
                return en if self.lang == "en" else pt
            return ans
        if key == "FORM.nivel_maior":
            for line in self.p.md.splitlines():
                m = re.match(r"^- FORM\.nivel_maior: (.+)$", line)
                if m:
                    pt, _, en = m.group(1).partition(" / ")
                    return en if self.lang == "en" and en else pt
            return None
        v = self.p.d(key, self.lang)
        return v or None

    def pret(self, label=""):
        lab = norm(label)
        brl = re.search(r"r\$|\bclt\b|\bpj\b|\bbrl\b|reais", lab)
        only_num = re.search(r"numeric|numerico|somente (o )?numero|only (the )?number|apenas (o )?valor", lab)
        v = self._pret(brl, lab)
        if v and only_num:
            v = re.sub(r"[^\d]", "", v.split(",")[0] if "R$" in v else v.replace(",", ""))
        return v

    def _pret(self, brl, lab):
        level = (self.job.get("level") or "").lower()
        k = "PRET.jr" if level in ("junior", "jr", "entry") else "PRET.sr" if level in ("senior", "sr", "lead") else "PRET.pl"
        row = self.p.pret.get(k)
        if not row:
            return None
        # colunas: nível | CLT | PJ | US$/mês | US$/ano | US$/h
        if self.lang == "pt" or brl:
            pj = re.search(r"\bpj\b|cnpj", lab) or (not re.search(r"\bclt\b", lab)
                                                   and re.search(r"\bpj\b|cnpj", norm(self.job.get("description") or "")))
            return (row[2] if pj else row[1]).strip()
        return row[3].strip() + "/month"

    def learned_for(self, label):
        t = set(re.findall(r"[a-z]{3,}", norm(label)))
        best, score = None, 0.0
        for q, a in self.learned:
            qt = set(re.findall(r"[a-z]{3,}", norm(q)))
            if not qt or not t:
                continue
            j = len(t & qt) / len(t | qt)
            if j > score:
                best, score = a, j
        return best if score >= 0.6 else None

    def cover(self):
        r01 = self.p.resp.get("R01", {}).get(self.lang, "")
        r29 = self.p.resp.get("R29", {}).get(self.lang, "")
        return (r01 + ("\n\n" + r29 if r29 else "")).strip() or None

    def open_text(self, label):
        """Pergunta aberta: a IA só escolhe QUAL resposta padrão (R01..R30) serve; o texto é o aprovado."""
        if not self.gem or not self.p.resp:
            return None
        opts = "\n".join("%s: %s" % (k, v["title"]) for k, v in self.p.resp.items() if v.get(self.lang))
        try:
            out = self.gem.generate(
                "Pergunta de formulário de candidatura: \"%s\"\nVaga: %s na %s.\nRespostas padrão disponíveis (ID: tema):\n%s\n"
                "Qual ID responde bem a pergunta? Se nenhuma responde de verdade, use null. Responda só JSON {\"id\": \"R01\"}."
                % (label[:300], self.job.get("title"), self.job.get("company"), opts))
            rid = (json.loads(re.sub(r"^```(?:json)?|```$", "", out.strip())) or {}).get("id")
        except Exception:  # noqa: BLE001
            return None
        r = self.p.resp.get(rid or "")
        if not r or "{" in r.get(self.lang, ""):  # modelos com {empresa}/{ponto do anúncio} não saem sozinhos
            return None
        return r[self.lang]

    # ------------------------------------------------------------ principal
    def answer(self, label, kind="text", options=None):
        """Texto da resposta, KIT, SKIP ou None (sem resposta aprovada)."""
        lab = norm(label)
        if not lab:
            return None
        if kind == "textarea" and COVER.search(lab):
            v = self.cover()
            self.log.append((label, "carta/R01+R29", "padrao"))
            return v
        key = None
        for rx, k in _RULES:
            if rx.search(lab):
                key = k
                break
        v = None
        if key in (KIT, SKIP):
            v = key
        elif key == "PRET":
            v = self.pret(label)
        elif key == "SOURCE":
            v = self.source_name or ("Company website" if self.lang == "en" else "Site da empresa")
        elif key == "AUTH_GENERIC":
            v = self.dado("DADOS.autorizacao_brasil")
            if self.lang == "en":
                v = "Yes, in Brazil (I work remotely from Brazil as a contractor)" if kind in ("text", "textarea") else "Yes"
        elif key:
            v = self.dado(key)
        if v is None:
            v = self.learned_for(label)
            if v:
                self.log.append((label, v, "aprendida"))
                return v
        if v is None and kind == "textarea":
            v = self.open_text(label)
            if v:
                self.log.append((label, v[:60], "padrao-ia"))
                return v
        if v is not None:
            self.log.append((label, v if len(str(v)) < 80 else str(v)[:77] + "...", key or "?"))
        return v


def pick_option(answer, options):
    """Escolhe a opção de um select/radio que corresponde à resposta. None se nenhuma bate."""
    if not options:
        return None
    a = norm(answer)
    yn = yes_no(answer)
    opts = [(o, norm(o)) for o in options if norm(o) and not re.match(r"^(select|selecione|choose|escolha|--)", norm(o))]
    for o, n in opts:
        if n == a:
            return o
    if yn:
        for o, n in opts:
            if yes_no(o) == yn:
                return o
    decline = re.compile(r"decline|prefer not|nao (desejo|quero)|prefiro nao|don.?t wish|not to (say|answer|disclose)")
    if decline.search(a):
        for o, n in opts:
            if decline.search(n):
                return o
    # número (anos): faixa que contém o valor
    m = re.match(r"^(\d+)", a)
    if m:
        x = int(m.group(1))
        for o, n in opts:
            r = re.findall(r"\d+", n)
            if len(r) >= 2 and int(r[0]) <= x <= int(r[1]):
                return o
            if len(r) == 1 and ("+" in n or "more" in n or "mais" in n) and x >= int(r[0]):
                return o
    best, sc = None, 0.0
    at = set(a.split())
    for o, n in opts:
        nt = set(n.split())
        if not nt:
            continue
        s = len(at & nt) / len(nt | at)
        if n in a or a in n:
            s += 0.5
        if s > sc:
            best, sc = o, s
    return best if sc >= 0.34 else None
