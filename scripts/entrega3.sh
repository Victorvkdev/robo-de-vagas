#!/usr/bin/env bash
# Entrega 3: aplica a migração no D1, publica o Worker (com o cron da lista) e roda um teste de busca.
# Rode na raiz do repo, depois do git push.
set -euo pipefail
cd "$(dirname "$0")/.."
export CI=true
WR="npx --no-install wrangler"
ok() { printf "\033[32m✅ %s\033[0m\n" "$*"; }

npm install --silent
$WR d1 migrations apply robo-de-vagas --remote >/dev/null && ok "migração 0003 aplicada"
$WR deploy >/dev/null && ok "Worker publicado com o cron das 8h"
npm run -s deploy:panel >/dev/null && ok "painel atualizado"

# Feeds do Google Alerts (pessoais): vão só pro D1, nunca pro repo.
JH="$HOME/jobhunter/config.yaml"
if [[ -f "$JH" ]]; then
  FEEDS="$(python3 -c "import yaml,json; print(json.dumps((yaml.safe_load(open('$JH'))['sources'].get('rss') or [])))" 2>/dev/null || echo '[]')"
  $WR d1 execute robo-de-vagas --remote --command \
    "INSERT INTO settings(key,value) VALUES ('rss_feeds','$FEEDS') ON CONFLICT(key) DO UPDATE SET value=excluded.value" >/dev/null \
    && ok "feeds do Google Alerts no D1"
fi

gh workflow run busca -f modo=busca && ok "busca disparada no Actions (leva uns 10 min)"
echo "Acompanhe: gh run watch \$(gh run list --workflow busca --limit 1 --json databaseId -q '.[0].databaseId')"
echo "Quando terminar, mande /lista para o bot."
