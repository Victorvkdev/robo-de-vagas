// Worker do Robô de Vagas: recebe o webhook do Telegram e responde aos comandos.
// Só responde ao chat_id configurado; qualquer outro chat é ignorado em silêncio.

import { fmtDate, getSetting, getStats, setSetting, topJobs } from "./lib/stats";

export interface Env {
  DB: D1Database;
  TELEGRAM_BOT_TOKEN: string;
  TELEGRAM_WEBHOOK_SECRET: string;
  ALLOWED_CHAT_ID: string;
  PANEL_URL: string;
}

interface TgMessage {
  message_id: number;
  chat: { id: number };
  text?: string;
}
interface TgUpdate {
  update_id: number;
  message?: TgMessage;
  callback_query?: { id: string; data?: string; message?: TgMessage; from: { id: number } };
}

const HELP = [
  "<b>Robô de Vagas</b>",
  "/status — os 4 números e a próxima entrevista",
  "/lista — prévia das melhores vagas abertas",
  "/painel — link do painel",
  "/pausar e /voltar — pausa ou retoma o robô",
].join("\n");

export const esc = (s: unknown) =>
  String(s ?? "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");

async function tg(env: Env, method: string, body: unknown): Promise<Response> {
  return fetch(`https://api.telegram.org/bot${env.TELEGRAM_BOT_TOKEN}/${method}`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
  });
}

export async function send(env: Env, text: string, extra: Record<string, unknown> = {}) {
  return tg(env, "sendMessage", {
    chat_id: env.ALLOWED_CHAT_ID,
    text,
    parse_mode: "HTML",
    link_preview_options: { is_disabled: true },
    ...extra,
  });
}

export async function statusText(env: Env): Promise<string> {
  const s = await getStats(env.DB);
  const paused = (await getSetting(env.DB, "paused")) === "1";
  const prox = s.proxima
    ? `Próxima entrevista: <b>${esc(s.proxima.empresa ?? "?")}</b> em ${fmtDate(s.proxima.quando)}` +
      (s.proxima.link ? `\n${esc(s.proxima.link)}` : "")
    : "Próxima entrevista: nenhuma marcada";
  return [
    `<b>Aplicadas</b> ${s.aplicadas} · <b>Em análise</b> ${s.em_analise} · <b>Entrevistas</b> ${s.entrevistas} · <b>Recusadas</b> ${s.recusadas}`,
    prox,
    `Vagas novas no banco: ${s.novas}`,
    paused ? "⏸ Robô pausado (/voltar para retomar)" : "",
  ]
    .filter(Boolean)
    .join("\n");
}

export async function listaText(env: Env): Promise<string> {
  const jobs = await topJobs(env.DB, 10);
  if (!jobs.length) return "Nenhuma vaga aberta no banco agora.";
  const lines = jobs.map(
    (j, i) =>
      `${i + 1}. <a href="${esc(j.url)}">${esc(j.title)}</a> — ${esc(j.company || "?")} · nota ${Math.round(j.fit)}`
  );
  return ["<b>Prévia: 10 melhores vagas abertas</b>", ...lines, "", "A lista diária com botões começa na entrega 3."].join(
    "\n"
  );
}

export async function handleCommand(env: Env, text: string): Promise<string> {
  const cmd = text.trim().split(/\s+/)[0].toLowerCase().replace(/@.*$/, "");
  switch (cmd) {
    case "/start":
    case "/ajuda":
    case "/help":
      return HELP;
    case "/status":
      return statusText(env);
    case "/lista":
      return listaText(env);
    case "/painel":
      return env.PANEL_URL ? `Painel: ${esc(env.PANEL_URL)}` : "Painel ainda não configurado.";
    case "/pausar":
      await setSetting(env.DB, "paused", "1");
      return "⏸ Pausado. Nada de lista nem envio até você mandar /voltar. Entrevistas continuam avisando.";
    case "/voltar":
      await setSetting(env.DB, "paused", "0");
      return "▶️ Voltei. Lista amanhã às 8h.";
    default:
      return "Não entendi. " + HELP;
  }
}

export default {
  async fetch(req: Request, env: Env): Promise<Response> {
    const url = new URL(req.url);

    if (req.method === "GET" && url.pathname === "/health") {
      return Response.json({ ok: true });
    }

    if (req.method === "POST" && url.pathname === "/tg") {
      if (!env.TELEGRAM_WEBHOOK_SECRET || req.headers.get("x-telegram-bot-api-secret-token") !== env.TELEGRAM_WEBHOOK_SECRET) {
        return new Response("forbidden", { status: 403 });
      }
      const update = (await req.json()) as TgUpdate;
      const chatId = update.message?.chat.id ?? update.callback_query?.message?.chat.id;
      if (!env.ALLOWED_CHAT_ID || String(chatId) !== String(env.ALLOWED_CHAT_ID)) {
        return new Response("ok"); // ignora qualquer outro chat
      }
      try {
        if (update.callback_query) {
          await tg(env, "answerCallbackQuery", { callback_query_id: update.callback_query.id, text: "Em breve" });
        } else if (update.message?.text) {
          await send(env, await handleCommand(env, update.message.text));
        }
      } catch (e) {
        await send(env, "⚠️ Erro: " + esc((e as Error).message)).catch(() => undefined);
      }
      return new Response("ok");
    }

    return new Response("not found", { status: 404 });
  },
};
