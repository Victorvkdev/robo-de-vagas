#!/usr/bin/env bash
# Atualiza no D1 os dois textos que a IA usa (ficam só no banco, nunca no repo):
#  - contexto fixo do chat de candidaturas (APLICACAO_SONNET.md até "## Status") → pacote do Sonnet
#  - Super_Contexto.md inteiro (com as "Respostas aprendidas") → rascunhos de e-mail e cards do Gemini
# Rode depois de cada sessão de candidaturas em que o Sonnet aprendeu respostas novas.
set -euo pipefail
cd "$(dirname "$0")/.."
SRC="${1:-$HOME/Documents/VK_JobSearch/08_Robo_Vagas/APLICACAO_SONNET.md}"
SUPER="${2:-$HOME/Documents/VK_JobSearch/05_Contexto_e_Referencia/Super_Contexto.md}"
python3 - "$SRC" "$SUPER" > /tmp/sonnet_ctx.sql <<'PY'
import os, re, sys, time
t = open(sys.argv[1], encoding="utf-8").read()
t = re.split(r"(?m)^## Status", t)[0].strip()
t = re.sub(r"(?m)^Cole este arquivo.*\n?", "", t).strip()
print("INSERT INTO settings(key, value) VALUES ('sonnet_context', '%s') "
      "ON CONFLICT(key) DO UPDATE SET value = excluded.value;" % t.replace("'", "''"))
if os.path.exists(sys.argv[2]):
    sc = open(sys.argv[2], encoding="utf-8").read()
    m = re.search(r"v(\d+(?:\.\d+)*)", sc[:400])
    print("INSERT INTO profile(key, version, lang, body, updated_ts) VALUES ('super_contexto', %s, 'pt', '%s', %d) "
          "ON CONFLICT(key) DO UPDATE SET version = excluded.version, body = excluded.body, updated_ts = excluded.updated_ts;"
          % ("'%s'" % m.group(1) if m else "NULL", sc.replace("'", "''"), time.time()))
PY
CI=true npx --no-install wrangler d1 execute robo-de-vagas --remote --file /tmp/sonnet_ctx.sql >/dev/null
rm -f /tmp/sonnet_ctx.sql
printf "\033[32m✅ contexto do Sonnet e Super Contexto atualizados no banco\033[0m\n"
