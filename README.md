# robo-de-vagas

Personal job-search automation that runs on free tiers: GitHub Actions does the heavy lifting, Cloudflare (Workers + D1 + Pages) keeps the data and the dashboard, and a Telegram bot is the only interface.

This repo holds **code only**. All personal data lives in a private D1 database and in encrypted secrets.

## Layout

```
engine/                Job search engine (Python, runs on GitHub Actions): 20+ sources, scoring, D1 writes, daily list
src/worker.ts          Telegram webhook + daily cron (list at 8am, ✅/❌ buttons, "ok 1 3 5")
src/lib/lista.ts       List cards, approval parsing and queries
src/lib/mail.ts        Draft buttons (Send/Edit/Ignore), "wrong class" fixes, interview reminders, quiet-hours queue
src/lib/stats.ts       Queries shared by the bot and the dashboard
engine/mail.py         Email (Zoho IMAP/SMTP): classify, status, calendar (.ics), drafts, follow-up
panel/                 Dashboard (Cloudflare Pages + Functions, behind Cloudflare Access)
migrations/            D1 schema
scripts/setup.sh       One-shot setup: D1, data migration, bot, webhook, dashboard, secrets
scripts/migrate_local.py  Builds a local seed from the previous tools (never committed)
tests/                 Migration test with sample data
```

## Setup

```bash
npm install
./scripts/setup.sh          # asks for tokens with hidden input
./scripts/setup.sh access   # after creating the Access app for the dashboard
```

## Daily flow

| When (UTC−3) | Where | What |
|---|---|---|
| 06:00 | Actions `busca` | Collect, score, dedupe, expire, pick the top 40, Gemini one-line summaries |
| 08:00 | Worker cron | Sends the list to Telegram in blocks with ✅/❌ buttons |
| any time | Telegram | `ok`, `ok 1 3 5`, `ok menos 2 4`, `/lista`, `/aprovadas`, `/agenda`, `/rascunhos` |
| every 15 min | Actions `email` | Read the inbox, update statuses, schedule interviews, draft replies, send approved ones |
| every 15 min | Worker cron | Interview reminders (24h and 1h before), notices held during quiet hours (22h–7h) |
| Mon 08:17 | Actions `saude` | Weekly summary + source health, keepalive commit |

Manual run: Actions → busca → Run workflow (`dry-run` only prints counts, `lista` rebuilds today's list).

## Security

- The bot only answers the configured `chat_id`; the webhook checks Telegram's secret header.
- The dashboard API validates the Cloudflare Access JWT (issuer, audience, email) and fails closed.
- `data/`, `.env*` and `.dev.vars` are git-ignored.
- No email leaves the inbox without a tap on Send in Telegram; logs only print counts.

## Roadmap

1. Context document ✅
2. Foundation: repo, D1, migration, bot, dashboard ✅
3. Job search + daily list on Telegram ✅
4. Email, calendar, drafts, follow-up ✅
5. Auto-apply on open ATS forms, network, freelance, talent pools

## Envio automático (entrega 5b)
Lista das 8h marca 🤖 as vagas de formulário aberto (Greenhouse, Lever, Ashby, Workable, Recruitee, SmartRecruiters)
com nota ≥ 75. O que não for pulado até 11h vai para o workflow `envio`: CV adaptado (só blocos aprovados do
Super Contexto, HTML → PDF), formulário preenchido com Playwright, comprovante no Telegram e no R2 privado.
Captcha, login ou pergunta obrigatória sem resposta aprovada = a vaga vai para o pacote. Começa em modo ensaio
(preenche e manda print, sem enviar); `/envio real` liga o envio de verdade.

