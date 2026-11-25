"""Acesso ao D1 pela API REST do Cloudflare (no Actions) ou a um SQLite local (testes e --local).
Escritas em lote usam SQL literal escapado (sem parâmetros) para caber no limite de 100 parâmetros do D1."""
import json
import os
import sqlite3
import time

import requests

API = "https://api.cloudflare.com/client/v4/accounts/%s/d1/database/%s/query"
CHUNK_BYTES = 90_000


def q(v):
    """Literal SQL seguro para SQLite/D1."""
    if v is None:
        return "NULL"
    if isinstance(v, bool):
        return "1" if v else "0"
    if isinstance(v, (int, float)):
        return repr(v)
    s = str(v).replace("\x00", "")
    return "'" + s.replace("'", "''") + "'"


def in_list(values):
    return "(" + ",".join(q(v) for v in values) + ")" if values else "(NULL)"


class _Base:
    rows_written = 0
    rows_read = 0

    def exec_many(self, statements):
        """Roda muitos statements literais, em pedaços de ~90 KB."""
        buf, size = [], 0
        for st in statements:
            st = st.strip().rstrip(";") + ";"
            if buf and size + len(st) > CHUNK_BYTES:
                self._exec_script("\n".join(buf))
                buf, size = [], 0
            buf.append(st)
            size += len(st) + 1
        if buf:
            self._exec_script("\n".join(buf))

    def setting(self, key, default=None):
        r = self.query("SELECT value FROM settings WHERE key = ?", [key])
        return r[0]["value"] if r else default

    def setting_json(self, key, default=None):
        v = self.setting(key)
        try:
            return json.loads(v) if v else default
        except ValueError:
            return default

    def set_setting(self, key, value):
        if not isinstance(value, str):
            value = json.dumps(value, ensure_ascii=False)
        self.query("INSERT INTO settings(key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                   [key, value])


class D1(_Base):
    def __init__(self, account_id, database_id, token):
        self.url = API % (account_id, database_id)
        self.s = requests.Session()
        self.s.headers.update({"Authorization": "Bearer " + token, "content-type": "application/json"})

    @classmethod
    def from_env(cls):
        return cls(os.environ["CLOUDFLARE_ACCOUNT_ID"], os.environ["D1_DATABASE_ID"], os.environ["CLOUDFLARE_API_TOKEN"])

    def _post(self, body):
        for attempt in range(4):
            r = self.s.post(self.url, data=json.dumps(body), timeout=90)
            if r.status_code in (429, 500, 502, 503, 504) and attempt < 3:
                time.sleep(2 * (attempt + 1))
                continue
            try:
                d = r.json()
            except ValueError:
                raise RuntimeError("D1 HTTP %s" % r.status_code)
            if not d.get("success"):
                # nunca imprime o SQL (pode ter dado pessoal)
                errs = "; ".join(str(e.get("message", e))[:200] for e in d.get("errors") or [])
                raise RuntimeError("D1 erro: %s" % (errs or r.status_code))
            res = d.get("result") or []
            for x in res:
                m = x.get("meta") or {}
                self.rows_written += int(m.get("rows_written") or 0)
                self.rows_read += int(m.get("rows_read") or 0)
            return res
        raise RuntimeError("D1 indisponível")

    def query(self, sql, params=None):
        res = self._post({"sql": sql, "params": list(params or [])})
        return (res[-1].get("results") or []) if res else []

    def _exec_script(self, sql):
        self._post({"sql": sql})


class LocalDB(_Base):
    """SQLite com o mesmo schema do D1 (aplica migrations/). Para testes e para rodar sem Cloudflare."""

    def __init__(self, path=":memory:", migrations=None):
        self.c = sqlite3.connect(path)
        self.c.row_factory = sqlite3.Row
        if migrations:
            for f in sorted(os.listdir(migrations)):
                if f.endswith(".sql"):
                    self.c.executescript(open(os.path.join(migrations, f), encoding="utf-8").read())
            self.c.commit()

    def _count(self, before):
        self.rows_written += self.c.total_changes - before

    def query(self, sql, params=None):
        before = self.c.total_changes
        cur = self.c.execute(sql, list(params or []))
        self._count(before)
        rows = [dict(r) for r in cur.fetchall()] if cur.description else []
        self.c.commit()
        return rows

    def _exec_script(self, sql):
        before = self.c.total_changes
        self.c.executescript(sql)
        self.c.commit()
        self._count(before)
