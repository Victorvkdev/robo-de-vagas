"""R2 (privado) pela API do Cloudflare, com o mesmo token do D1 (precisa de permissão R2 Edit).
Sem permissão: devolve None e o resto segue (o Telegram já tem cópia do CV e do comprovante)."""
import os
from urllib.parse import quote

import requests

API = "https://api.cloudflare.com/client/v4/accounts/%s/r2/buckets/%s/objects/%s"
LIST = "https://api.cloudflare.com/client/v4/accounts/%s/r2/buckets/%s/objects"


class R2:
    def __init__(self, account, token, bucket):
        self.account, self.bucket = account, bucket
        self.h = {"Authorization": "Bearer " + token}
        self.errors = []

    @classmethod
    def from_env(cls, bucket=None):
        # desligado por padrão: o R2 pede cartão. Só liga com o secret R2_ENABLED=1.
        if os.environ.get("R2_ENABLED") != "1":
            return None
        a, t = os.environ.get("CLOUDFLARE_ACCOUNT_ID"), os.environ.get("CLOUDFLARE_API_TOKEN")
        b = bucket or os.environ.get("R2_BUCKET") or "robo-de-vagas"
        return cls(a, t, b) if a and t else None

    def put(self, key, data, ctype="application/octet-stream"):
        try:
            r = requests.put(API % (self.account, self.bucket, quote(key, safe="/")), data=data, timeout=120,
                             headers=dict(self.h, **{"content-type": ctype}))
        except requests.RequestException as e:
            self.errors.append(type(e).__name__)
            return None
        if not r.ok:
            self.errors.append("HTTP %s" % r.status_code)
            return None
        return key

    def put_file(self, key, path, ctype):
        with open(path, "rb") as f:
            return self.put(key, f.read(), ctype)

    def list(self, prefix):
        try:
            r = requests.get(LIST % (self.account, self.bucket), params={"prefix": prefix, "per_page": 1000},
                             headers=self.h, timeout=60)
            return [o["key"] for o in (r.json().get("result") or [])] if r.ok else []
        except (requests.RequestException, ValueError):
            return []

    def delete(self, key):
        try:
            return requests.delete(API % (self.account, self.bucket, quote(key, safe="/")), headers=self.h, timeout=60).ok
        except requests.RequestException:
            return False
