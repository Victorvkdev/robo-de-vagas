// Worker do Robô de Vagas: webhook do Telegram (comandos, botões, "ok") e cron da lista diária.
// Só responde ao chat_id configurado; qualquer outro chat é ignorado em silêncio.

import {
  type Keyboard,
  type ListJob,
  approvedToday,
  blocks,
  esc,
  fmtDay,
  getJob,
  localDate,
  localHour,
  markSent,
  parseOk,
  pendingApproved,
  pickApprovals,
  replaceRow,
  row,
  setStatus,
  todayList,
} from "./lib/lista";
import {
  askClass,
  cancelEdit,
  cmdAgenda,
  cmdRascunhos,
  cmdRetornos,
  dispatchWorkflow,
  fixClass,
  flushNotices,
  maybeEdit,
  outboxCallback,
  reminders,
} from "./lib/mail";
import { fmtDate, getSetting, getStats, setSetting, topJobs } from "./lib/stats";

export interface Env {
  DB: D1Database;
  TELEGRAM_BOT_TOKEN: string;
  TELEGRAM_WEBHOOK_SECRET: string;
  ALLOWED_CHAT_ID: string;
  PANEL_URL: string;
  GH_REPO?: string;
  GH_DISPATCH_TOKEN?: string;
}

interface TgMessage {
  message_id: number;
  chat: { id: number };
  text?: string;
  reply_markup?: { inline_keyboard?: Keyboard };
  reply_to_message?: { message_id: number };
}
interface TgUpdate {
  update_id: number;
  message?: TgMessage;
  callback_query?: { id: string; data?: string; message?: TgMessage; from: { id: number } };
}

const COMMANDS = [
  { command: "lista", description: "Lista de hoje com botões" },
  { command: "aprovadas", description: "Aprovadas que faltam enviar" },
  { command: "agenda", description: "Entrevistas e prazos de teste" },
  { command: "rascunhos", description: "Respostas esperando seu Enviar" },
  { command: "retornos", description: "Retornos das candidaturas (e-mail)" },
  { command: "status", description: "Os 4 números e a próxima entrevista" },
  { command: "painel", description: "Link do painel" },
  { command: "pausar", description: "Pausar o robô" },
  { command: "voltar", description: "Retomar o robô" },
];
const COMMANDS_VERSION = "5";

const HELP = [
  "<b>Robô de Vagas</b>",
  "/lista — lista de hoje com botões",
  "/aprovadas — aprovadas que faltam enviar (toque 📨 quando enviar)",
  "/agenda — entrevistas e prazos de teste",
  "/rascunhos — respostas de e-mail esperando ✉️ Enviar",
  "/retornos — recusas, testes, entrevistas e confirmações dos últimos 30 dias",
  "/status — os 4 números e a próxima entrevista",
  "/painel — link do painel",
  "/pausar e /voltar — pausa ou retoma o robô",
  "",
  "Na lista: <b>ok</b> · <b>ok 1 3 5</b> · <b>ok menos 2 4</b>",
].join("\n");

async function tg(env: Env, method: string, body: unknown): Promise<Response> {
  return fetch(`https://api.telegram.org/bot${env.TELEGRAM_BOT_TOKEN}/${method}`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
  });
}

export async function send(env: Env, text: string, extra: Record<string, unknown> = {}) {
  const r = await tg(env, "sendMessage", {
    chat_id: env.ALLOWED_CHAT_ID,
    text,
    parse_mode: "HTML",
    link_preview_options: { is_disabled: true },
    ...extra,
  });
  if (!r.ok) throw new Error(`Telegram ${r.status}: ${(await r.text()).slice(0, 200)}`);
  return r;
}

const maxSend = async (env: Env) => Number((await getSetting(env.DB, "max_send_day")) ?? 25);

// ---------------------------------------------------------------- textos
export async function statusText(env: Env): Promise<string> {
  const s = await getStats(env.DB);
  const paused = (await getSetting(env.DB, "paused")) === "1";
  const prox = s.proxima
    ? `Próxima entrevista: <b>${esc(s.proxima.empresa ?? "?")}</b> em ${fmtDate(s.proxima.quando)}` +
      (s.proxima.link ? `\n${esc(s.proxima.link)}` : "")
    : "Próxima entrevista: nenhuma marcada";
  const today = localDate();
  const list = await todayList(env.DB, today);
  const ap = list.filter((j) => j.status !== "listada" && j.status !== "pulada").length;
  return [
    `<b>Aplicadas</b> ${s.aplicadas} · <b>Em análise</b> ${s.em_analise} · <b>Entrevistas</b> ${s.entrevistas} · <b>Recusadas</b> ${s.recusadas}`,
    prox,
    list.length ? `Lista de hoje: ${list.length} vagas, ${ap} aprovadas` : "Lista de hoje: ainda não saiu",
    paused ? "⏸ Robô pausado (/voltar para retomar)" : "",
  ]
    .filter(Boolean)
    .join("\n");
}

async function sendList(env: Env, list: ListJob[], date: string) {
  const cap = await maxSend(env);
  for (const b of blocks(list, date, cap)) {
    await send(env, b.text, { reply_markup: { inline_keyboard: b.keyboard } });
  }
}

async function cmdLista(env: Env) {
  const today = localDate();
  const list = await todayList(env.DB, today);
  if (list.length) return sendList(env, list, today);
  const jobs = await topJobs(env.DB, 10);
  const lines = jobs.map(
    (j, i) => `${i + 1}. <a href="${esc(j.url)}">${esc(j.title)}</a> — ${esc(j.company || "?")} · ⭐ ${Math.round(j.fit)}`
  );
  await send(env, ["A lista de hoje ainda não saiu (a busca roda às 6h). Prévia do banco:", ...lines].join("\n"));
}

async function cmdAprovadas(env: Env) {
  const jobs = await pendingApproved(env.DB);
  if (!jobs.length) return send(env, "Nenhuma aprovada esperando envio.");
  const lines = jobs.map(
    (j) =>
      `<b>${j.list_pos ?? "•"}.</b> <a href="${esc(j.url)}">${esc(j.title)}</a> — ${esc(j.company || "?")}` +
      (j.apply_type === "linkedin" ? " · ✋ LinkedIn" : "")
  );
  const kb: Keyboard = jobs.map((j) => [{ text: `📨 Enviei ${j.list_pos ?? ""} ${(j.company || "").slice(0, 20)}`.trim(), callback_data: `ev:${j.id}` }]);
  await send(
    env,
    [`<b>Aprovadas que faltam enviar (${jobs.length})</b>`, "O envio automático chega na entrega 5. Enviou? Toque 📨.", "", ...lines].join("\n"),
    { reply_markup: { inline_keyboard: kb } }
  );
}

async function handleOk(env: Env, text: string): Promise<string | null> {
  const cmd = parseOk(text);
  if (!cmd) return null;
  const today = localDate();
  const list = await todayList(env.DB, today);
  if (!list.length) return "Não tem lista hoje ainda. Ela chega às 8h.";
  const remaining = Math.max(0, (await maxSend(env)) - (await approvedToday(env.DB, today)));
  const { approve, linkedin, overCap } = pickApprovals(list, cmd, remaining);
  await setStatus(env.DB, approve, "aprovada");
  await setStatus(env.DB, linkedin, "precisa_voce");
  const pos = (ids: string[]) => list.filter((j) => ids.includes(j.id)).map((j) => j.list_pos);
  const out = [];
  out.push(approve.length ? `✅ Aprovei ${approve.length}: ${pos(approve).join(", ")}` : "Nenhuma vaga nova aprovada.");
  if (linkedin.length) {
    out.push(`✋ LinkedIn, você aplica (${linkedin.length}):`);
    for (const j of list.filter((x) => linkedin.includes(x.id)))
      out.push(`${j.list_pos}. <a href="${esc(j.url)}">${esc(j.title)}</a> — ${esc(j.company || "?")}`);
  }
  if (overCap.length) out.push(`⏸ Teto de envios do dia atingido. Ficaram de fora: ${overCap.join(", ")}`);
  out.push("", "Até a entrega 5, o envio é com você ou com o Claude: /aprovadas mostra os links.");
  return out.join("\n");
}

async function handleMailCallback(env: Env, cq: NonNullable<TgUpdate["callback_query"]>, parts: string[]) {
  const answer = (text: string) => tg(env, "answerCallbackQuery", { callback_query_id: cq.id, text });
  const sendFn = (t: string, extra: Record<string, unknown> = {}) => send(env, t, extra);
  const [action, a, b] = parts;
  if (action === "ob") {
    const res = await outboxCallback(env, sendFn, a, Number(b));
    if (res.keyboard && cq.message) {
      const kb = cq.message.reply_markup?.inline_keyboard ?? [];
      // troca só a linha dos botões do rascunho; mantém "Classifiquei errado"
      const rest = kb.filter((r) => !r.some((x) => x.callback_data.startsWith("ob:") || x.callback_data === "noop"));
      await tg(env, "editMessageReplyMarkup", {
        chat_id: cq.message.chat.id,
        message_id: cq.message.message_id,
        reply_markup: { inline_keyboard: [...res.keyboard, ...rest] },
      });
    }
    return answer(res.answer);
  }
  if (action === "mc") return answer(await askClass(env, sendFn, a));
  if (action === "mf") {
    const msg = await fixClass(env, a, b);
    if (cq.message) {
      await tg(env, "editMessageText", {
        chat_id: cq.message.chat.id,
        message_id: cq.message.message_id,
        text: "✅ " + msg,
      });
    }
    return answer("Corrigido");
  }
  return answer("");
}

async function handleCallback(env: Env, cq: NonNullable<TgUpdate["callback_query"]>) {
  const parts = (cq.data ?? "").split(":");
  if (["ob", "mc", "mf"].includes(parts[0])) return handleMailCallback(env, cq, parts);
  const [action, id] = parts;
  const answer = (text: string) => tg(env, "answerCallbackQuery", { callback_query_id: cq.id, text });
  if (action === "noop" || !id) return answer("");
  const j = await getJob(env.DB, id);
  if (!j) return answer("Vaga não encontrada");
  let msg = "";
  if (action === "ap") {
    if (j.apply_type === "linkedin") {
      await setStatus(env.DB, [id], "precisa_voce");
      j.status = "precisa_voce";
      msg = "LinkedIn: você aplica";
    } else {
      const today = localDate();
      if ((await approvedToday(env.DB, today)) >= (await maxSend(env))) return answer("Teto de envios do dia atingido");
      await setStatus(env.DB, [id], "aprovada");
      j.status = "aprovada";
      msg = "Aprovada";
    }
  } else if (action === "sk") {
    await setStatus(env.DB, [id], "pulada");
    j.status = "pulada";
    msg = "Pulada";
  } else if (action === "un") {
    if (j.status === "enviada") return answer("Já enviada");
    await setStatus(env.DB, [id], "listada");
    j.status = "listada";
    msg = "Desfeito";
  } else if (action === "ev") {
    if (j.status !== "enviada") await markSent(env.DB, j);
    j.status = "enviada";
    msg = "Marcada como enviada";
  } else {
    return answer("");
  }
  const kb = cq.message?.reply_markup?.inline_keyboard;
  if (cq.message && kb) {
    const newRow =
      action === "ev"
        ? [{ text: `📨 ${j.list_pos ?? ""} enviada`.replace("  ", " "), callback_data: "noop" }]
        : row({ ...j, list_pos: j.list_pos });
    await tg(env, "editMessageReplyMarkup", {
      chat_id: cq.message.chat.id,
      message_id: cq.message.message_id,
      reply_markup: { inline_keyboard: replaceRow(kb, id, newRow) },
    });
  }
  return answer(msg);
}

export async function handleCommand(env: Env, text: string, replyTo?: number): Promise<string | null> {
  const cmd = text.trim().split(/\s+/)[0].toLowerCase().replace(/@.*$/, "");
  const sendFn = (t: string, extra: Record<string, unknown> = {}) => send(env, t, extra);
  if (cmd === "/cancelar") return cancelEdit(env);
  const edited = await maybeEdit(env, text, replyTo);
  if (edited) return edited;
  switch (cmd) {
    case "/agenda":
      await cmdAgenda(env, sendFn);
      return null;
    case "/rascunhos":
      await cmdRascunhos(env, sendFn);
      return null;
    case "/retornos":
      await cmdRetornos(env, sendFn);
      return null;
    case "/start":
    case "/ajuda":
    case "/help":
      return HELP;
    case "/status":
      return statusText(env);
    case "/lista":
      await cmdLista(env);
      return null;
    case "/aprovadas":
      await cmdAprovadas(env);
      return null;
    case "/painel":
      return env.PANEL_URL ? `Painel: ${esc(env.PANEL_URL)}` : "Painel ainda não configurado.";
    case "/pausar":
      await setSetting(env.DB, "paused", "1");
      return "⏸ Pausado. Nada de lista nem envio até você mandar /voltar. Entrevistas continuam avisando.";
    case "/voltar":
      await setSetting(env.DB, "paused", "0");
      return "▶️ Voltei. Lista amanhã às 8h (ou /lista se a de hoje já saiu).";
    default: {
      const ok = await handleOk(env, text);
      return ok ?? "Não entendi. " + HELP;
    }
  }
}

// ---------------------------------------------------------------- cron (a cada 15 min)
// Sempre: lembretes de entrevista e fila do silêncio. Das 8h às 11h59: lista do dia (só sai uma vez).
export async function tick(env: Env, now = Date.now()) {
  const sendFn = (t: string, extra: Record<string, unknown> = {}) => send(env, t, extra);
  await reminders(env, sendFn, now).catch((e) => console.error("reminders", (e as Error).message));
  await flushNotices(env, sendFn, now).catch((e) => console.error("notices", (e as Error).message));
  // o agendamento do GitHub (*/15) é descartado com frequência: o Worker dispara a leitura do e-mail a cada tick
  await dispatchWorkflow(env, "email.yml", "ciclo").catch((e) => console.error("email", (e as Error).message));
  const h = localHour(now);
  if (h >= 8 && h < 12) {
    await dailyList(env, now).catch(async (e) => {
      await setSetting(env.DB, "list_sent", "").catch(() => undefined);
      console.error("dailyList", (e as Error).message);
    });
  }
}

export async function dailyList(env: Env, now = Date.now()) {
  if ((await getSetting(env.DB, "commands_v")) !== COMMANDS_VERSION) {
    await tg(env, "setMyCommands", { commands: COMMANDS });
    await setSetting(env.DB, "commands_v", COMMANDS_VERSION);
  }
  if ((await getSetting(env.DB, "paused")) === "1") return;
  const today = localDate(now);
  if ((await getSetting(env.DB, "list_sent")) === today) return;
  const list = await todayList(env.DB, today);
  if (!list.length) {
    // o agendamento das 5h do GitHub às vezes não roda: às 8h o próprio Worker dispara a busca (1x por dia)
    if ((await getSetting(env.DB, "busca_kick")) !== today && (await getSetting(env.DB, "list_ready")) !== today) {
      await setSetting(env.DB, "busca_kick", today);
      await dispatchWorkflow(env, "busca.yml", "busca").catch(() => false);
      return;
    }
    if (localHour(now) >= 11 && (await getSetting(env.DB, "list_warn")) !== today) {
      await setSetting(env.DB, "list_warn", today);
      await send(env, `⚠️ A lista de ${fmtDay(today)} não ficou pronta. A busca das 6h pode ter falhado: confira o Actions no GitHub.`);
    }
    return;
  }
  await setSetting(env.DB, "list_sent", today);
  await sendList(env, list, today);
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
          await handleCallback(env, update.callback_query);
        } else if (update.message?.text) {
          const reply = await handleCommand(env, update.message.text, update.message.reply_to_message?.message_id);
          if (reply) await send(env, reply);
        }
      } catch (e) {
        await send(env, "⚠️ Erro: " + esc((e as Error).message)).catch(() => undefined);
      }
      return new Response("ok");
    }

    return new Response("not found", { status: 404 });
  },

  async scheduled(_ev: ScheduledController, env: Env, ctx: ExecutionContext) {
    ctx.waitUntil(tick(env));
  },
};
