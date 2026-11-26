// Entrega 4 no Worker: botões dos rascunhos (Enviar/Editar/Ignorar), "Classifiquei errado",
// lembretes de entrevista (24h e 1h antes), fila do silêncio (22h–7h) e os comandos /agenda e /rascunhos.
// O envio de verdade (SMTP do Zoho) roda no Actions: o Worker só aprova e dispara o workflow.

import { type Keyboard, esc, localHour } from "./lista";
import { fmtDate, getSetting, setSetting } from "./stats";

export interface MailEnv {
  DB: D1Database;
  TELEGRAM_BOT_TOKEN: string;
  ALLOWED_CHAT_ID: string;
  GH_REPO?: string;
  GH_DISPATCH_TOKEN?: string;
}

type Send = (text: string, extra?: Record<string, unknown>) => Promise<Response>;

export interface Outbox {
  id: number;
  kind: string;
  to_addr: string;
  subject: string;
  body: string;
  state: string;
  note: string | null;
}

export const CLASSES: [string, string][] = [
  ["entrevista", "📅 Entrevista"],
  ["agendar", "🗓 Marcar horário"],
  ["teste", "🧪 Teste"],
  ["recrutador", "💬 Recrutador"],
  ["recusa", "❌ Recusa"],
  ["recebida", "📥 Recebida"],
  ["oferta", "🎉 Proposta"],
  ["orcamento", "💼 Orçamento"],
  ["spam", "🗑 Spam/nada"],
];
const STATUS_OF: Record<string, string> = {
  entrevista: "entrevista",
  agendar: "entrevista",
  teste: "teste",
  recusa: "recusada",
  recebida: "em_analise",
  oferta: "oferta",
};

/** Igual ao norm_subject do Python (mail.py): minúsculas, sem acento, sem "Re:/Fwd:". */
export function normSubject(s: string): string {
  return (s || "")
    .toLowerCase()
    .normalize("NFD")
    .replace(/[̀-ͯ]/g, "")
    .replace(/^\s*((re|fwd?|enc|res)\s*:\s*)+/, "")
    .replace(/\s+/g, " ")
    .trim();
}

export function isQuiet(hour: number, start = 22, end = 7): boolean {
  return start > end ? hour >= start || hour < end : hour >= start && hour < end;
}

export async function quietNow(db: D1Database, now = Date.now()): Promise<boolean> {
  const s = Number((await getSetting(db, "quiet_start")) ?? 22);
  const e = Number((await getSetting(db, "quiet_end")) ?? 7);
  return isQuiet(localHour(now), s, e);
}

/** Dispara um workflow do Actions (workflow_dispatch). Sem token: false. */
export async function dispatchWorkflow(env: MailEnv, workflow: string, modo: string): Promise<boolean> {
  if (!env.GH_DISPATCH_TOKEN || !env.GH_REPO) return false;
  const r = await fetch(`https://api.github.com/repos/${env.GH_REPO}/actions/workflows/${workflow}/dispatches`, {
    method: "POST",
    headers: {
      authorization: `Bearer ${env.GH_DISPATCH_TOKEN}`,
      accept: "application/vnd.github+json",
      "user-agent": "robo-de-vagas-worker",
      "content-type": "application/json",
    },
    body: JSON.stringify({ ref: "main", inputs: { modo } }),
  });
  return r.status === 204;
}

/** Dispara o workflow "email" no modo enviar. Sem token: o ciclo de 15 min envia. */
export const dispatchSend = (env: MailEnv) => dispatchWorkflow(env, "email.yml", "enviar");

export function draftKeyboard(id: number): Keyboard {
  return [
    [
      { text: "✉️ Enviar", callback_data: `ob:s:${id}` },
      { text: "✏️ Editar", callback_data: `ob:e:${id}` },
      { text: "🗑 Ignorar", callback_data: `ob:i:${id}` },
    ],
  ];
}

const getOutbox = (db: D1Database, id: number) => db.prepare(`SELECT * FROM outbox WHERE id = ?`).bind(id).first<Outbox>();

async function approve(env: MailEnv, id: number): Promise<string> {
  const now = Date.now() / 1000;
  const r = await env.DB.prepare(
    `UPDATE outbox SET state = 'aprovado', updated_ts = ? WHERE id = ? AND state IN ('aguardando','erro','ignorado')`
  )
    .bind(now, id)
    .run();
  if (!r.meta.changes) return "Esse rascunho já saiu ou está saindo";
  const ok = await dispatchSend(env).catch(() => false);
  return ok ? "Enviando…" : "Aprovado: sai na próxima leitura (até 15 min)";
}

/** Botões ob:s|e|i|u:<id>. Devolve o texto do answerCallbackQuery e, se mudou, a nova linha de botões. */
export async function outboxCallback(
  env: MailEnv,
  send: Send,
  op: string,
  id: number
): Promise<{ answer: string; keyboard?: Keyboard }> {
  const o = await getOutbox(env.DB, id);
  if (!o) return { answer: "Rascunho não encontrado" };
  const now = Date.now() / 1000;
  if (op === "s") {
    const answer = await approve(env, id);
    return { answer, keyboard: [[{ text: "⏳ enviando…", callback_data: "noop" }]] };
  }
  if (op === "i") {
    await env.DB.prepare(`UPDATE outbox SET state = 'ignorado', updated_ts = ? WHERE id = ? AND state IN ('aguardando','erro')`)
      .bind(now, id)
      .run();
    return { answer: "Ignorado", keyboard: [[{ text: "🗑 ignorado · desfazer", callback_data: `ob:u:${id}` }]] };
  }
  if (op === "u") {
    await env.DB.prepare(`UPDATE outbox SET state = 'aguardando', updated_ts = ? WHERE id = ? AND state = 'ignorado'`)
      .bind(now, id)
      .run();
    return { answer: "Desfeito", keyboard: draftKeyboard(id) };
  }
  if (op === "e") {
    if (!["aguardando", "erro", "ignorado"].includes(o.state)) return { answer: "Esse rascunho já saiu" };
    await send(o.body, { parse_mode: undefined }); // texto puro, fácil de copiar e editar
    const r = await send(
      `✏️ Responda <b>esta mensagem</b> com o texto final do e-mail para ${esc(o.to_addr)}. Ele sai assim que você mandar.\n` +
        `Para cancelar: /cancelar`,
      { reply_markup: { force_reply: true, input_field_placeholder: "Texto final do e-mail" } }
    );
    const sent = (await r.json()) as { result?: { message_id: number } };
    await setSetting(env.DB, "edit_outbox", `${id}:${sent.result?.message_id ?? 0}`);
    return { answer: "Mande o texto novo" };
  }
  return { answer: "" };
}

/** Texto do Vk enquanto há uma edição aberta: vira o corpo do e-mail e é enviado. null = não era edição. */
export async function maybeEdit(
  env: MailEnv,
  text: string,
  replyTo: number | undefined
): Promise<string | null> {
  const pending = await getSetting(env.DB, "edit_outbox");
  if (!pending) return null;
  const [idS, promptS] = pending.split(":");
  const id = Number(idS);
  const isReply = replyTo !== undefined && String(replyTo) === promptS;
  if (!isReply && (text.startsWith("/") || /^(ok|okay|aprovo|aprova)\b/i.test(text.trim()))) return null;
  if (text.trim().length < 10) return "Texto muito curto. Mande o e-mail completo ou /cancelar.";
  await setSetting(env.DB, "edit_outbox", "");
  const r = await env.DB.prepare(
    `UPDATE outbox SET body = ?, state = 'aguardando', updated_ts = ? WHERE id = ? AND state IN ('aguardando','erro','ignorado')`
  )
    .bind(text.trim(), Date.now() / 1000, id)
    .run();
  if (!r.meta.changes) return "Esse rascunho já tinha saído. Nada mudou.";
  const msg = await approve(env, id);
  return `✉️ Texto novo salvo. ${msg}`;
}

export async function cancelEdit(env: MailEnv): Promise<string> {
  const had = await getSetting(env.DB, "edit_outbox");
  await setSetting(env.DB, "edit_outbox", "");
  return had ? "Edição cancelada. O rascunho continua em /rascunhos." : "Nada para cancelar.";
}

// ---------------------------------------------------------------- "Classifiquei errado"
interface EmailRow {
  id: string;
  from_addr: string;
  subject: string;
  class: string;
  job_id: string | null;
  due_ts: number | null;
  link: string | null;
  company: string | null;
  ts: number;
}

export function classKeyboard(eid: string): Keyboard {
  const rows: Keyboard = [];
  for (let i = 0; i < CLASSES.length; i += 3)
    rows.push(CLASSES.slice(i, i + 3).map(([k, label]) => ({ text: label, callback_data: `mf:${eid}:${k}` })));
  return rows;
}

export async function askClass(env: MailEnv, send: Send, eid: string): Promise<string> {
  const e = await env.DB.prepare(`SELECT id, subject, class FROM emails WHERE id = ?`).bind(eid).first<EmailRow>();
  if (!e) return "E-mail não encontrado";
  await send(`Qual é a classe certa de “${esc((e.subject || "").slice(0, 80))}”? (hoje: ${esc(e.class)})`, {
    reply_markup: { inline_keyboard: classKeyboard(eid) },
  });
  return "Escolha a classe";
}

export async function fixClass(env: MailEnv, eid: string, cls: string): Promise<string> {
  const db = env.DB;
  const e = await db.prepare(`SELECT * FROM emails WHERE id = ?`).bind(eid).first<EmailRow>();
  if (!e) return "E-mail não encontrado";
  if (!CLASSES.some(([k]) => k === cls)) return "Classe inválida";
  const now = Date.now() / 1000;
  const domain = (e.from_addr || "").split("@").pop() || "";
  const rule =
    cls === "spam" && !/gmail|outlook|hotmail|yahoo|icloud|greenhouse|lever|ashby|workable|gupy|linkedin/.test(domain)
      ? { kind: "domain", pattern: domain }
      : { kind: "from_subject", pattern: `${(e.from_addr || "").toLowerCase()}|${normSubject(e.subject)}` };
  const st: D1PreparedStatement[] = [
    db
      .prepare(
        `UPDATE emails SET corrected_class = ?, class = ?, redo = CASE WHEN ? IN ('recrutador','agendar') THEN 1 ELSE 0 END WHERE id = ?`
      )
      .bind(cls, cls, cls, eid),
    db.prepare(`INSERT INTO mail_rules (kind, pattern, class, created_ts) VALUES (?, ?, ?, ?)`).bind(rule.kind, rule.pattern, cls, now),
    // evento que o e-mail criou deixa de valer
    db.prepare(`DELETE FROM events WHERE email_id = ? AND origin = 'v3-email'`).bind(eid),
  ];
  const status = STATUS_OF[cls];
  if (["entrevista", "teste", "recusa", "recebida", "oferta"].includes(cls)) {
    st.push(
      db
        .prepare(
          `INSERT INTO events (job_id, kind, ts, due_ts, link, note, email_id, origin, company) VALUES (?, ?, ?, ?, ?, ?, ?, 'v3-email', ?)`
        )
        .bind(e.job_id, cls, e.ts, cls === "entrevista" || cls === "teste" ? e.due_ts : null, e.link, e.subject, eid, e.company)
    );
  }
  if (e.job_id && status) {
    st.push(db.prepare(`UPDATE jobs SET status = ?, status_ts = ? WHERE id = ?`).bind(status, now, e.job_id));
    st.push(db.prepare(`UPDATE applications SET state = ?, state_ts = ? WHERE job_id = ?`).bind(status, now, e.job_id));
  }
  await db.batch(st);
  const label = CLASSES.find(([k]) => k === cls)?.[1] ?? cls;
  const extra = cls === "recrutador" || cls === "agendar" ? " O rascunho chega na próxima leitura." : "";
  return `Corrigido para ${label}. Próximos e-mails iguais já saem assim.${extra}`;
}

// ---------------------------------------------------------------- lembretes e fila do silêncio
interface EventRow {
  id: number;
  due_ts: number;
  link: string | null;
  company: string | null;
  title: string | null;
  r24: number;
  r1: number;
}

export async function reminders(env: MailEnv, send: Send, now = Date.now()): Promise<number> {
  const t = now / 1000;
  const quiet = await quietNow(env.DB, now);
  const r = await env.DB.prepare(
    `SELECT e.id, e.due_ts, e.link, COALESCE(e.company, j.company) AS company, COALESCE(e.title, j.title) AS title, e.r24, e.r1
       FROM events e LEFT JOIN jobs j ON j.id = e.job_id
      WHERE e.kind = 'entrevista' AND e.due_ts BETWEEN ? AND ? AND (COALESCE(e.r24,0) = 0 OR COALESCE(e.r1,0) = 0)`
  )
    .bind(t, t + 24 * 3600 + 900)
    .all<EventRow>();
  let n = 0;
  for (const e of r.results) {
    const mins = (e.due_ts - t) / 60;
    const who = `<b>${esc(e.company || "?")}</b>${e.title ? " · " + esc(e.title.slice(0, 60)) : ""}`;
    const link = e.link ? `\n${esc(e.link)}` : "";
    if (!e.r1 && mins <= 75) {
      await send(`⏰ Entrevista em ${Math.max(1, Math.round(mins))} min: ${who}\n${fmtDate(e.due_ts)}${link}`);
      await env.DB.prepare(`UPDATE events SET r1 = 1, r24 = 1 WHERE id = ?`).bind(e.id).run();
      n++;
    } else if (!e.r24 && mins > 75) {
      if (quiet && mins > 12 * 60) continue; // espera as 7h
      await send(`📅 Lembrete: entrevista ${fmtDate(e.due_ts)} (Teresina) com ${who}${link}`);
      await env.DB.prepare(`UPDATE events SET r24 = 1 WHERE id = ?`).bind(e.id).run();
      n++;
    }
  }
  return n;
}

interface Notice {
  id: number;
  urgent: number;
  text: string;
  keyboard: string | null;
  doc: string | null;
  doc_name: string | null;
}

/** Manda o que ficou na fila do silêncio. No silêncio, só urgente. */
export async function flushNotices(env: MailEnv, send: Send, now = Date.now()): Promise<number> {
  const quiet = await quietNow(env.DB, now);
  const r = await env.DB.prepare(
    `SELECT id, urgent, text, keyboard, doc, doc_name FROM notices WHERE sent_ts IS NULL ${quiet ? "AND urgent = 1" : ""} ORDER BY id LIMIT 20`
  ).all<Notice>();
  let n = 0;
  for (const x of r.results) {
    const kb = x.keyboard ? { reply_markup: { inline_keyboard: JSON.parse(x.keyboard) } } : {};
    if (x.doc) {
      const fd = new FormData();
      fd.set("chat_id", env.ALLOWED_CHAT_ID);
      fd.set("caption", x.text.slice(0, 1024));
      fd.set("parse_mode", "HTML");
      if (x.keyboard) fd.set("reply_markup", JSON.stringify({ inline_keyboard: JSON.parse(x.keyboard) }));
      fd.set("document", new File([x.doc], x.doc_name || "convite.ics", { type: "text/calendar" }));
      const res = await fetch(`https://api.telegram.org/bot${env.TELEGRAM_BOT_TOKEN}/sendDocument`, { method: "POST", body: fd });
      if (!res.ok) continue;
    } else {
      await send(x.text, kb);
    }
    await env.DB.prepare(`UPDATE notices SET sent_ts = ? WHERE id = ?`).bind(now / 1000, x.id).run();
    n++;
  }
  return n;
}

// ---------------------------------------------------------------- comandos
export async function cmdAgenda(env: MailEnv, send: Send) {
  const t = Date.now() / 1000;
  const r = await env.DB.prepare(
    `SELECT e.kind, e.due_ts, e.link, COALESCE(e.company, j.company) AS company, COALESCE(e.title, j.title) AS title
       FROM events e LEFT JOIN jobs j ON j.id = e.job_id
      WHERE e.kind IN ('entrevista','teste') AND e.due_ts >= ? ORDER BY e.due_ts LIMIT 10`
  )
    .bind(t - 3600)
    .all<{ kind: string; due_ts: number; link: string | null; company: string | null; title: string | null }>();
  if (!r.results.length) return send("Agenda vazia: nenhuma entrevista ou prazo de teste marcado.");
  const lines = r.results.map(
    (e) =>
      `${e.kind === "entrevista" ? "📅" : "🧪"} ${fmtDate(e.due_ts)} · <b>${esc(e.company || "?")}</b>` +
      (e.title ? ` · ${esc(e.title.slice(0, 50))}` : "") +
      (e.link ? `\n${esc(e.link)}` : "")
  );
  return send(["<b>Agenda</b> (horário de Teresina)", ...lines].join("\n"));
}

export async function cmdRascunhos(env: MailEnv, send: Send) {
  const r = await env.DB.prepare(
    `SELECT id, kind, to_addr, subject, body, state, note FROM outbox WHERE state IN ('aguardando','erro') ORDER BY id DESC LIMIT 8`
  ).all<Outbox>();
  if (!r.results.length) return send("Nenhum rascunho esperando.");
  for (const o of r.results.reverse()) {
    const head = `${o.state === "erro" ? "⚠️ falhou · " : ""}<b>#${o.id}</b> para ${esc(o.to_addr)}\n<i>${esc((o.subject || "").slice(0, 100))}</i>`;
    await send(`${head}\n<blockquote>${esc(o.body.slice(0, 2500))}</blockquote>${o.note ? "\n⚠️ " + esc(o.note) : ""}`, {
      reply_markup: { inline_keyboard: draftKeyboard(o.id) },
    });
  }
}

const ICON: Record<string, string> = {
  entrevista: "📅",
  agendar: "🗓",
  teste: "🧪",
  recrutador: "💬",
  recusa: "❌",
  recebida: "📥",
  oferta: "🎉",
  orcamento: "💼",
};

/** /retornos: últimos e-mails das candidaturas (sem spam), com a classe e a empresa. */
export async function cmdRetornos(env: MailEnv, send: Send) {
  const r = await env.DB.prepare(
    `SELECT e.id, e.ts, e.class, COALESCE(e.company, j.company) AS company, e.subject, COALESCE(j.title, '') AS title
       FROM emails e LEFT JOIN jobs j ON j.id = e.job_id
      WHERE e.class <> 'spam' AND e.ts >= ? ORDER BY e.ts DESC LIMIT 20`
  )
    .bind(Date.now() / 1000 - 30 * 86400)
    .all<{ id: string; ts: number; class: string; company: string | null; subject: string; title: string }>();
  if (!r.results.length) return send("Nenhum retorno de candidatura nos últimos 30 dias.");
  const lines = r.results.map(
    (e) =>
      `${ICON[e.class] ?? "•"} ${fmtDate(e.ts).slice(0, 5)} · <b>${esc(e.company || "?")}</b>` +
      (e.title && !e.title.startsWith("mail:") ? ` · ${esc(e.title.slice(0, 40))}` : "") +
      `\n   <i>${esc((e.subject || "").slice(0, 70))}</i>`
  );
  return send(
    ["<b>Retornos dos últimos 30 dias</b>", "📅 entrevista · 🗓 marcar horário · 🧪 teste · 💬 recrutador · ❌ recusa · 📥 recebida", "", ...lines].join("\n")
  );
}
