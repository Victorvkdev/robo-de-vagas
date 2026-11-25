#!/usr/bin/env bash
# Robô de Vagas v3 — instalação da fundação (entrega 2). Rode no Mac, na raiz do repo:
#   ./scripts/setup.sh          → tudo (D1, migração, bot, painel, segredos)
#   ./scripts/setup.sh access   → liga o painel ao Cloudflare Access (depois de criar o app no Zero Trust)
# Pode rodar de novo sem medo: cada passo confere o que já existe.
# Segredos são digitados aqui (entrada oculta) e vão direto para Cloudflare/GitHub. Nunca cole no chat.

set -euo pipefail
cd "$(dirname "$0")/.."

DB_NAME="robo-de-vagas"
PAGES="robo-de-vagas-painel"
JOBHUNTER_DB="${JOBHUNTER_DB:-$HOME/jobhunter/jobs.db}"
ROBO_DIR="${ROBO_DIR:-$HOME/robo-vagas}"
CONTEXTO="${CONTEXTO:-$HOME/Documents/VK_JobSearch/05_Contexto_e_Referencia/Super_Contexto.md}"
export CI=true            # wrangler não pede confirmação
WR="npx --no-install wrangler"

ok()   { printf "\033[32m✅ %s\033[0m\n" "$*"; }
bad()  { printf "\033[31m❌ %s\033[0m\n" "$*"; }
step() { printf "\n\033[1m▶ %s\033[0m\n" "$*"; }
ask_secret() { local v; read -r -s -p "$1: " v; echo >&2; printf '%s' "$v"; }
json() { python3 -c "import json,sys; d=json.load(sys.stdin); print($1)"; }

pages_secret() { printf '%s' "$2" | $WR pages secret put "$1" --project-name "$PAGES" >/dev/null && ok "Painel: $1"; }

if [[ "${1:-}" == "access" ]]; then
  step "Cloudflare Access no painel"
  read -r -p "Team domain (ex.: vk.cloudflareaccess.com): " TEAM
  read -r -p "Application Audience (AUD) tag: " AUD
  read -r -p "E-mail autorizado [hello@victorvk.com]: " MAIL; MAIL="${MAIL:-hello@victorvk.com}"
  pages_secret ACCESS_TEAM_DOMAIN "${TEAM#https://}"
  pages_secret ACCESS_AUD "$AUD"
  pages_secret ALLOWED_EMAIL "$MAIL"
  npm run -s deploy:panel >/dev/null && ok "Painel publicado: https://$PAGES.pages.dev"
  exit 0
fi

step "1/9 Ferramentas"
for c in node npm python3 gh curl openssl; do command -v "$c" >/dev/null || { bad "falta $c"; exit 1; }; done
gh auth status >/dev/null 2>&1 || gh auth login
REPO="$(gh repo view --json nameWithOwner -q .nameWithOwner)"
ok "GitHub: $REPO"
npm install --silent
$WR whoami 2>/dev/null | grep -qi "associated with" || $WR login
ok "Cloudflare logado"

step "2/9 Token da API do Cloudflare (para o GitHub Actions)"
CF_TOKEN="$(ask_secret "Cole o Cloudflare API token")"
cf() { curl -s -H "Authorization: Bearer $CF_TOKEN" "https://api.cloudflare.com/client/v4$1"; }
[[ "$(cf /user/tokens/verify | json 'd["success"]')" == "True" ]] && ok "token válido" || { bad "token inválido"; exit 1; }
ACCOUNT_ID="$(cf /accounts | json 'd["result"][0]["id"] if d.get("result") else ""')"
[[ -z "$ACCOUNT_ID" ]] && read -r -p "Account ID (painel do Cloudflare, lado direito da home): " ACCOUNT_ID
ok "Account ID: $ACCOUNT_ID"
for p in "d1/database:D1" "workers/scripts:Workers" "pages/projects:Pages"; do
  [[ "$(cf "/accounts/$ACCOUNT_ID/${p%%:*}" | json 'd["success"]')" == "True" ]] && ok "permissão ${p##*:}" || bad "token sem permissão ${p##*:} (edite o token e rode de novo)"
done

step "3/9 Banco D1"
d1_id() { $WR d1 list --json 2>/dev/null | python3 -c "import json,sys; print(next((d['uuid'] for d in json.load(sys.stdin) if d['name']=='$DB_NAME'),''))"; }
D1_ID="$(d1_id)"
[[ -z "$D1_ID" ]] && { $WR d1 create "$DB_NAME" >/dev/null; D1_ID="$(d1_id)"; }
perl -pi -e "s/database_id = \".*\"/database_id = \"$D1_ID\"/" wrangler.toml panel/wrangler.toml
ok "D1 $DB_NAME ($D1_ID)"
$WR d1 migrations apply "$DB_NAME" --remote >/dev/null && ok "tabelas criadas"

step "4/9 Migração (jobhunter + Robô v2 + super contexto)"
N_JOBS="$($WR d1 execute "$DB_NAME" --remote --json --command "SELECT COUNT(*) AS n FROM jobs" | json 'd[0]["results"][0]["n"]')"
if [[ "$N_JOBS" -gt 0 ]]; then
  ok "já migrado ($N_JOBS vagas) — pulando"
else
  ARGS=(--jobhunter "$JOBHUNTER_DB" --robo "$ROBO_DIR" --out data/seed.sql)
  [[ -f "$CONTEXTO" ]] && ARGS+=(--contexto "$CONTEXTO")
  python3 scripts/migrate_local.py "${ARGS[@]}"
  $WR d1 execute "$DB_NAME" --remote --file data/seed.sql >/dev/null
  $WR d1 execute "$DB_NAME" --remote --json --command \
    "SELECT (SELECT COUNT(*) FROM jobs) j, (SELECT COUNT(*) FROM contacts) c, (SELECT COUNT(*) FROM applications) a" \
    | json '"vagas %(j)s · contatos %(c)s · candidaturas %(a)s" % d[0]["results"][0]' | while read -r l; do ok "$l"; done
fi

step "5/9 Bot do Telegram"
TG_TOKEN="$(ask_secret "Cole o token do bot (@BotFather)")"
tgapi() { curl -s "https://api.telegram.org/bot$TG_TOKEN/$1" "${@:2}"; }
BOT="$(tgapi getMe | json 'd["result"]["username"] if d.get("ok") else ""')"
[[ -z "$BOT" ]] && { bad "token do bot inválido"; exit 1; }
ok "bot @$BOT"
tgapi deleteWebhook >/dev/null
read -r -p "Abra o Telegram, mande /start para @$BOT e aperte Enter aqui… " _
CHAT_ID="$(tgapi getUpdates | json 'next((u["message"]["chat"]["id"] for u in reversed(d["result"]) if "message" in u and u["message"]["chat"]["type"]=="private"),"")')"
[[ -z "$CHAT_ID" ]] && { bad "não achei sua mensagem; mande /start de novo e rode o script outra vez"; exit 1; }
ok "chat_id $CHAT_ID"

step "6/9 Worker"
get_sub() { cf "/accounts/$ACCOUNT_ID/workers/subdomain" | json 'd["result"]["subdomain"] if d.get("result") else ""'; }
SUB="$(get_sub)"
for s in victorvk vk-robo "vk-$(openssl rand -hex 3)"; do
  [[ -n "$SUB" ]] && break
  curl -s -X PUT -H "Authorization: Bearer $CF_TOKEN" -H "content-type: application/json" \
    -d "{\"subdomain\":\"$s\"}" "https://api.cloudflare.com/client/v4/accounts/$ACCOUNT_ID/workers/subdomain" >/dev/null
  SUB="$(get_sub)"
done
[[ -z "$SUB" ]] && read -r -p "Subdomínio workers.dev (Workers & Pages > lado direito > Subdomain): " SUB
$WR deploy >/dev/null
WORKER_URL="https://robo-de-vagas.$SUB.workers.dev"
WH_SECRET="$(openssl rand -hex 24)"
printf '%s' "$TG_TOKEN"  | $WR secret put TELEGRAM_BOT_TOKEN >/dev/null
printf '%s' "$WH_SECRET" | $WR secret put TELEGRAM_WEBHOOK_SECRET >/dev/null
printf '%s' "$CHAT_ID"   | $WR secret put ALLOWED_CHAT_ID >/dev/null
curl -s "$WORKER_URL/health" | grep -q '"ok":true' && ok "Worker no ar: $WORKER_URL" || bad "Worker não respondeu em $WORKER_URL"

step "7/9 Webhook"
tgapi setWebhook --data-urlencode "url=$WORKER_URL/tg" -d "secret_token=$WH_SECRET" \
  --data-urlencode 'allowed_updates=["message","callback_query"]' -d drop_pending_updates=true | grep -q '"ok":true' \
  && ok "webhook ligado" || { bad "falha no setWebhook"; exit 1; }
tgapi setMyCommands --data-urlencode 'commands=[{"command":"status","description":"Os 4 números e a próxima entrevista"},{"command":"lista","description":"Prévia das melhores vagas"},{"command":"painel","description":"Link do painel"},{"command":"pausar","description":"Pausar o robô"},{"command":"voltar","description":"Retomar o robô"}]' >/dev/null
tgapi sendMessage -d "chat_id=$CHAT_ID" --data-urlencode "text=✅ Robô de Vagas v3 conectado. Mande /status." >/dev/null

step "8/9 Painel (Pages)"
$WR pages project create "$PAGES" --production-branch main >/dev/null 2>&1 || true
npm run -s deploy:panel >/dev/null && ok "Painel publicado: https://$PAGES.pages.dev (fechado até ligar o Access)"

step "9/9 Segredos no GitHub ($REPO)"
GEMINI="$(ask_secret "Cole a chave do Gemini")"
code="$(curl -s -o /dev/null -w '%{http_code}' "https://generativelanguage.googleapis.com/v1beta/models?key=$GEMINI")"
[[ "$code" == "200" ]] && ok "chave Gemini válida" || bad "chave Gemini respondeu $code (confira depois)"
gsec() { printf '%s' "$2" | gh secret set "$1" --repo "$REPO" >/dev/null && ok "GitHub: $1"; }
gsec CLOUDFLARE_API_TOKEN "$CF_TOKEN"
gsec CLOUDFLARE_ACCOUNT_ID "$ACCOUNT_ID"
gsec D1_DATABASE_ID "$D1_ID"
gsec TELEGRAM_BOT_TOKEN "$TG_TOKEN"
gsec TELEGRAM_CHAT_ID "$CHAT_ID"
gsec GEMINI_API_KEY "$GEMINI"

printf "\n\033[1mPronto.\033[0m Mande /status para @%s. Falta só ligar o Access no painel.\n" "$BOT"
