#!/usr/bin/env bash
# Entrega 4: e-mail, avisos, agenda, rascunhos e follow-up. Rode na raiz do repo, depois do git push.
# Pode rodar de novo sem medo. Senha do Zoho vem do ~/robo-vagas/.env (v2) ou é digitada aqui, oculta.
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

step "2/6 Migração 0004 (e-mail, agenda, fila de avisos, menos gravações)"
$WR d1 migrations apply "$DB" --remote >/dev/null && ok "migração aplicada"

step "3/6 Segredos do Zoho no GitHub"
ENVF="$HOME/robo-vagas/.env"
ZUSER="hello@victorvk.com"
if [[ -f "$ENVF" ]] && grep -q '^ZOHO_SENHA_APP=' "$ENVF"; then
  ZPASS="$(grep '^ZOHO_SENHA_APP=' "$ENVF" | head -1 | cut -d= -f2- | tr -d '"'"'")"
  ok "senha de app lida do ~/robo-vagas/.env"
else
  read -r -s -p "Cole a senha de app do Zoho (não aparece): " ZPASS; echo
fi
printf '%s' "$ZUSER" | gh secret set ZOHO_USER --repo "$REPO" >/dev/null && ok "GitHub: ZOHO_USER"
printf '%s' "$ZPASS" | gh secret set ZOHO_APP_PASSWORD --repo "$REPO" >/dev/null && ok "GitHub: ZOHO_APP_PASSWORD"
unset ZPASS

step "4/6 Worker (botões dos rascunhos, lembretes, silêncio) + disparo do envio"
gh auth token | $WR secret put GH_DISPATCH_TOKEN >/dev/null && ok "Worker: GH_DISPATCH_TOKEN (toque em Enviar dispara o envio na hora)"
$WR deploy >/dev/null && ok "Worker publicado (cron a cada 15 min)"

step "5/6 Conferir Google, Wise e BairesDev marcadas como enviadas (ajuste c)"
SQL="SELECT id, list_pos, company, title, status FROM jobs WHERE status='enviada' AND list_date >= '2026-11-25' AND (company LIKE '%Google%' OR company LIKE '%Wise%' OR company LIKE '%BairesDev%') ORDER BY list_pos"
ROWS="$($WR d1 execute "$DB" --remote --json --command "$SQL" 2>/dev/null | python3 -c 'import json,sys; d=json.load(sys.stdin); print(json.dumps(d[0]["results"]))')"
if [[ "$ROWS" == "[]" ]]; then
  ok "nenhuma delas está como enviada"
else
  cat > /tmp/robo_fix.py <<'PY'
import json, sys
rows = json.loads(sys.argv[1])
out = []
for r in rows:
    sys.stderr.write("%s. %s — %s\n   [a] voltar para aprovada  [p] pulada  [m] manter enviada: "
                     % (r["list_pos"], r["company"], r["title"]))
    sys.stderr.flush()
    a = sys.stdin.readline().strip().lower()
    q = "'" + r["id"].replace("'", "''") + "'"
    if a in ("a", "p"):
        out.append("UPDATE jobs SET status='%s' WHERE id=%s;" % ("aprovada" if a == "a" else "pulada", q))
        out.append("DELETE FROM applications WHERE job_id=%s AND origin='v3' AND channel IN ('manual','linkedin');" % q)
if out:
    print("\n".join(out))
PY
  python3 /tmp/robo_fix.py "$ROWS" < /dev/tty > /tmp/robo_fix.sql
  if [[ -s /tmp/robo_fix.sql ]]; then
    $WR d1 execute "$DB" --remote --file /tmp/robo_fix.sql >/dev/null && ok "status corrigidos"
  else
    ok "mantidas como estavam"
  fi
  rm -f /tmp/robo_fix.sql /tmp/robo_fix.py
fi

step "6/6 Teste do login no Zoho (IMAP e SMTP, sem ler nem enviar nada)"
gh workflow run email --repo "$REPO" -f modo=teste && ok "teste disparado no Actions"
sleep 8
gh run watch "$(gh run list --repo "$REPO" --workflow email --limit 1 --json databaseId -q '.[0].databaseId')" --exit-status \
  && ok "Zoho conectado. A leitura roda sozinha a cada 15 min." \
  || echo "Teste falhou: abra o run no GitHub (Actions → email) e me mande a linha 'email teste'."
