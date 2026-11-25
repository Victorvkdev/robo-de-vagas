# robo-de-vagas

Personal job-search automation that runs on free tiers: GitHub Actions does the heavy lifting, Cloudflare (Workers + D1 + Pages) keeps the data and the dashboard, and a Telegram bot is the only interface.

This repo holds **code only**. All personal data lives in a private D1 database and in encrypted secrets.

## Layout

```
src/worker.ts          Telegram webhook (commands: /status /lista /painel /pausar /voltar)
src/lib/stats.ts       Queries shared by the bot and the dashboard
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

## Security

- The bot only answers the configured `chat_id`; the webhook checks Telegram's secret header.
- The dashboard API validates the Cloudflare Access JWT (issuer, audience, email) and fails closed.
- `data/`, `.env*` and `.dev.vars` are git-ignored.

## Roadmap

1. Context document ✅
2. Foundation: repo, D1, migration, bot, dashboard ✅
3. Job search + daily list on Telegram
4. Email, calendar, drafts, follow-up
5. Auto-apply on open ATS forms, network, freelance, talent pools
