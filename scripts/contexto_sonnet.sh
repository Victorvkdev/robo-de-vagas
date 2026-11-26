#!/usr/bin/env bash
# Carrega no D1 o contexto fixo do chat de candidaturas (APLICACAO_SONNET.md até "## Status").
# Rode de novo sempre que mudar o arquivo. O texto fica só no banco, nunca no repo.
set -euo pipefail
cd "$(dirname "$0")/.."
SRC="${1:-$HOME/Documents/VK_JobSearch/08_Robo_Vagas/APLICACAO_SONNET.md}"
python3 - "$SRC" > /tmp/sonnet_ctx.sql <<'PY'
import re, sys
t = open(sys.argv[1], encoding="utf-8").read()
t = re.split(r"(?m)^## Status", t)[0].strip()
t = re.sub(r"(?m)^Cole este arquivo.*\n?", "", t).strip()
print("INSERT INTO settings(key, value) VALUES ('sonnet_context', '%s') "
      "ON CONFLICT(key) DO UPDATE SET value = excluded.value;" % t.replace("'", "''"))
PY
CI=true npx --no-install wrangler d1 execute robo-de-vagas --remote --file /tmp/sonnet_ctx.sql >/dev/null
rm -f /tmp/sonnet_ctx.sql
printf "\033[32m✅ contexto do Sonnet carregado no banco\033[0m\n"
