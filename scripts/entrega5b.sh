#!/usr/bin/env bash
# Entrega 5b: envio automático com janela de veto (CV adaptado + ATS + comprovante), R2 privado e backup semanal.
# Rode na raiz do repo, depois do git push. Pode rodar de novo sem medo.
set -euo pipefail
cd "$(dirname "$0")/.."
export CI=true
WR="npx --no-install wrangler"
DB="robo-de-vagas"
REPO="Victorvkdev/robo-de-vagas"
ok()   { printf "\033[32m✅ %s\033[0m\n" "$*"; }
step() { printf "\n\033[1m▶ %s\033[0m\n" "$*"; }

step "1/6 Dependências"
npm install --silent && ok "npm ok"

step "2/6 Bucket R2 privado (CVs, comprovantes e backup)"
if $WR r2 bucket list 2>/dev/null | grep -q "robo-de-vagas"; then ok "bucket já existe"
else $WR r2 bucket create robo-de-vagas >/dev/null && ok "bucket criado"; fi

step "3/6 Migração 0007 (modo ensaio, nota mínima 75, até 10/dia às 11h)"
$WR d1 migrations apply "$DB" --remote >/dev/null && ok "migração aplicada"

step "4/6 Super Contexto atualizado no banco (o CV e as respostas saem dele)"
./scripts/contexto_sonnet.sh >/dev/null && ok "contexto no D1"

step "5/6 Worker (🤖 na lista, disparo às 11h, /envio)"
$WR deploy >/dev/null && ok "Worker publicado"

step "6/6 Teste: ensaio numa vaga 🤖 (preenche e manda print + CV no Telegram, não envia)"
SQL="SELECT id, company FROM jobs WHERE apply_type='auto' AND status IN ('listada','aprovada','nova') AND COALESCE(gone,0)=0 ORDER BY COALESCE(list_date,'') DESC, fit DESC LIMIT 1"
J="$($WR d1 execute "$DB" --remote --json --command "$SQL" 2>/dev/null | python3 -c 'import json,sys; r=json.load(sys.stdin)[0]["results"]; print(r[0]["id"]+" "+(r[0]["company"] or "") if r else "")')"
if [[ -n "$J" ]]; then
  gh workflow run envio.yml --repo "$REPO" -f modo=ensaio -f job="${J%% *}" >/dev/null && ok "ensaio disparado (${J#* }): o resultado chega no Telegram em ~3 min"
else
  ok "sem vaga 🤖 no banco agora; o primeiro ensaio roda amanhã às 11h"
fi
printf "\nPronto. Amanhã: lista às 8h com 🤖; às 11h ensaio das que você não pulou. Conferiu os prints? Mande /envio real no bot.\n"
