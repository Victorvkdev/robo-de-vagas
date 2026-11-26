"""Lê o Super Contexto (markdown guardado no D1 em profile.super_contexto) e devolve os blocos aprovados.
Nada de dado pessoal fica no repo: tudo vem do banco em tempo de execução."""
import re

ROW = re.compile(r"^\|\s*([A-Z][A-Za-z_.0-9]+)\s*\|(.*)\|\s*$")


def _cells(rest):
    return [c.strip() for c in rest.split("|")]


def _sections(md):
    """{'1': texto, '5': texto, ...} pelo número do '## N.'"""
    out, cur, buf = {}, None, []
    for line in md.splitlines():
        m = re.match(r"^## (\d+)\.", line)
        if m or line.startswith("## Histórico"):
            if cur is not None:
                out[cur] = "\n".join(buf)
            cur, buf = (m.group(1) if m else "hist"), []
            continue
        buf.append(line)
    if cur is not None:
        out[cur] = "\n".join(buf)
    return out


class Perfil:
    def __init__(self, md):
        self.md = md or ""
        s = _sections(self.md)
        self.dados = self._table(s.get("1", "") + "\n" + s.get("2", ""))  # DADOS.* e IDI.* -> (pt, en)
        self.sens = {k: v for k, v in self._table(s.get("11", ""), cols=3).items()}
        self.bullets = self._bullets(s.get("5", ""))
        self.res, self.hl = self._resumos(s.get("5", ""))
        self.skills = self._skills(s.get("7", ""))
        self.exps, self.form, self.certs = self._exps(s.get("4", ""))
        self.resp = self._resp(s.get("8", ""))
        self.pret = self._table(s.get("3", ""), cols=7)
        self.learned = self._learned(s.get("14", ""))
        self.nums = self._table(s.get("6", ""), cols=4)

    # ------------------------------------------------------------ parsers
    @staticmethod
    def _table(txt, cols=2):
        out = {}
        for line in txt.splitlines():
            m = ROW.match(line.strip())
            if not m or m.group(1) == "ID":
                continue
            c = _cells(m.group(2))
            out[m.group(1)] = tuple(c[:cols]) if cols > 2 else (c[0], c[1] if len(c) > 1 else c[0])
        return out

    @staticmethod
    def _bullets(txt):
        out, cur, group = [], None, ""
        for line in txt.splitlines():
            g = re.match(r"^### (.+)", line)
            if g:
                group = g.group(1).strip()
                continue
            m = re.match(r"^- (B-[A-Z]+-\d+) \[([A-Z ]+)\](.*)$", line)
            if m:
                cur = dict(id=m.group(1), tags=m.group(2).split(), group=group, pt="", en="",
                           cv_ok="nunca no CV" not in m.group(3))
                out.append(cur)
                continue
            m = re.match(r"^\s+- (PT|EN): (.+)$", line)
            if m and cur:
                cur[m.group(1).lower()] = m.group(2).strip()
        return out

    @staticmethod
    def _resumos(txt):
        res, hl = {}, {}
        for line in txt.splitlines():
            m = re.match(r"^- (RES\.[A-Z_]+): (.+)$", line)
            if m:
                res[m.group(1)] = m.group(2).strip()
            m = re.match(r"^\s+- (HL\.[A-Z]+): (.+)$", line)
            if m:
                hl[m.group(1)] = m.group(2).strip()
        return res, hl

    @staticmethod
    def _skills(txt):
        out = {}
        for line in txt.splitlines():
            m = re.match(r"^- (SK\.lista_curta_(?:en|pt)): (.+)$", line)
            if m:
                out[m.group(1)[-2:]] = [x.strip() for x in m.group(2).split(",") if x.strip()]
            m = ROW.match(line.strip())
            if m and m.group(1).startswith("SK."):
                out.setdefault("all", []).append(_cells(m.group(2))[0])
        return out

    @staticmethod
    def _exps(txt):
        exps, form, certs, cur = [], [], [], None
        for line in txt.splitlines():
            m = re.match(r"^### EXP\.(\d+) (.+)$", line)
            if m:
                cur = dict(n=int(m.group(1)), nome=m.group(2).strip())
                exps.append(cur)
                continue
            if line.startswith("### "):
                cur = None
                continue
            m = re.match(r"^- (FORM\.\d+): (.+)$", line)
            if m:
                pt, _, en = m.group(2).partition("| EN:")
                form.append(dict(pt=" · ".join(x.strip() for x in pt.split("|")[:3] if x.strip()), en=en.strip()))
                continue
            m = re.match(r"^- (CERT\.\d+): (.+)$", line)
            if m:
                certs.append(m.group(2).strip())
                continue
            m = re.match(r"^- ([a-z_]+(?: \([^)]*\))?): (.+)$", line)
            if m and cur is not None:
                cur[m.group(1).split(" ")[0]] = m.group(2).strip()
        return exps, form, certs

    @staticmethod
    def _resp(txt):
        out, cur = {}, None
        for line in txt.splitlines():
            m = re.match(r"^### (R\d+) (.+)$", line)
            if m:
                cur = out.setdefault(m.group(1), dict(title=m.group(2).strip(), pt="", en=""))
                continue
            m = re.match(r"^- (PT|EN): (.+)$", line)
            if m and cur is not None:
                cur[m.group(1).lower()] = m.group(2).strip()
        return out

    @staticmethod
    def _learned(txt):
        out = []
        for line in txt.splitlines():
            m = re.match(r"^- (?:\[[^\]]*\] )?(.+?)\s*(?:→|->|=>)\s*(.+)$", line)
            if m and not line.startswith("`"):
                out.append((m.group(1).strip(), m.group(2).strip()))
        return out

    # ------------------------------------------------------------ helpers
    def d(self, key, lang="en"):
        v = self.dados.get(key)
        return (v[1] if lang == "en" else v[0]) if v else ""

    def ok(self):
        return bool(self.bullets and self.exps and self.dados.get("DADOS.email"))

    def allowed_numbers(self):
        """Números que podem aparecer em texto gerado (tabela da seção 6 + datas das experiências)."""
        txt = " ".join(" ".join(v) for v in self.nums.values())
        txt += " " + " ".join("%s %s" % (e.get("inicio", ""), e.get("fim", "")) for e in self.exps)
        return set(re.findall(r"\d+(?:[.,]\d+)?", txt))
