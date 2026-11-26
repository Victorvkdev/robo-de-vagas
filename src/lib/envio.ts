// Envio automático (janela de veto): às 11h as vagas 🤖 da lista que o Vk não pulou viram 'enviando' e o
// workflow envio (Actions) gera o CV adaptado, preenche o ATS e manda o comprovante. /envio controla o modo.

import { type ListJob, esc, localDate, localHour } from "./lista";
import { dispatchWorkflow } from "./mail";
import { getSetting, setSetting } from "./stats";

interface EnvioEnv {
  DB: D1Database;
  TELEGRAM_BOT_TOKEN: string;
  ALLOWED_CHAT_ID: string;
  GH_REPO?: string;
  GH_DISPATCH_TOKEN?: string;
}

const num = async (db: D1Database, k: string, d: number) => {
  const raw = await getSetting(db, k);
  const v = Number(raw);
  return raw === null || raw === undefined || raw === "" || !Number.isFinite(v) ? d : v;
};

/** Nota mínima do 🤖. Com /envio off devolve 999 (nenhuma vaga vira 🤖). */
export async function autoMin(db: D1Database): Promise<number> {
  if ((await getSetting(db, "envio_modo")) === "off") return 999;
  return num(db, "auto_min_fit", 75);
}

/** Vagas de hoje que vão sozinhas: 🤖, não puladas (listada ou aprovada), por nota. */
export async function autoCandidates(db: D1Database, date: string): Promise<ListJob[]> {
  const min = await autoMin(db);
  const max = await num(db, "auto_max_day", 10);
  const r = await db
    .prepare(
      `SELECT id, list_pos, title, company, location, url, salary, fit, summary, reason, risk, contact, apply_type, status
         FROM jobs WHERE list_date = ? AND status IN ('listada','aprovada') AND apply_type = 'auto' AND fit >= ? AND url NOT LIKE '%lever.co%'
        ORDER BY fit DESC LIMIT ?`
    )
    .bind(date, min, max)
    .all<ListJob>();
  return r.results;
}

/** Às 11h (1x por dia, só depois que a lista saiu): marca 'enviando' e dispara o workflow. force = /envio agora. */
export async function kickAuto(env: EnvioEnv, send: (t: string) => Promise<unknown>, now = Date.now(), force = false) {
  const db = env.DB;
  const today = localDate(now);
  const modo = (await getSetting(db, "envio_modo")) || "ensaio";
  if (modo === "off" || (await getSetting(db, "paused")) === "1") return force ? "Envio automático desligado (/envio real ou /envio ensaio para ligar)." : null;
  if (!force) {
    if (localHour(now) < (await num(db, "auto_hour", 11))) return null;
    if ((await getSetting(db, "envio_kick")) === today) return null;
    if ((await getSetting(db, "list_sent")) !== today) return null;
  }
  await setSetting(db, "envio_kick", today);
  const jobs = await autoCandidates(db, today);
  if (!jobs.length) return force ? "Nenhuma vaga 🤖 pendente na lista de hoje." : null;
  const ts = Date.now() / 1000;
  await db.batch(jobs.map((j) => db.prepare(`UPDATE jobs SET status = 'enviando', status_ts = ? WHERE id = ?`).bind(ts, j.id)));
  const ok = await dispatchWorkflow(env, "envio.yml", "auto").catch(() => false);
  if (!ok) {
    await db.batch(jobs.map((j) => db.prepare(`UPDATE jobs SET status = 'aprovada', status_ts = ? WHERE id = ?`).bind(ts, j.id)));
    await send("⚠️ Não consegui disparar o envio automático (GitHub). As vagas 🤖 foram para o pacote: /pacote");
    return null;
  }
  const lines = jobs.map((j) => `${j.list_pos}. ${esc(j.company || "?")} — ${esc(j.title)} · ⭐ ${Math.round(j.fit)}`);
  await send(
    [
      `🤖 <b>${modo === "real" ? "Enviando" : "Ensaio de envio"}</b>: ${jobs.length} vagas com CV adaptado (comprovante chega aqui).`,
      ...lines,
      modo === "real" ? "" : "\nModo ensaio: preencho e mando o print, sem enviar. Conferiu? /envio real",
    ]
      .filter(Boolean)
      .join("\n")
  );
  return null;
}

/** As aprovadas 🤖 de hoje ainda não enviadas ficam fora do pacote (vão às 11h). */
export async function autoPendingIds(db: D1Database, now = Date.now()): Promise<Set<string>> {
  const today = localDate(now);
  if ((await getSetting(db, "envio_kick")) === today) return new Set();
  return new Set((await autoCandidates(db, today)).map((j) => j.id));
}

/** "↩️ Não saiu": volta a vaga para o pacote e apaga a candidatura automática. */
export async function notSent(db: D1Database, id: string): Promise<string> {
  const ts = Date.now() / 1000;
  await db.batch([
    db.prepare(`UPDATE jobs SET status = 'aprovada', status_ts = ? WHERE id = ?`).bind(ts, id),
    db.prepare(`DELETE FROM applications WHERE job_id = ? AND channel = 'auto'`).bind(id),
  ]);
  return "Voltou para o pacote";
}

export async function cmdEnvio(env: EnvioEnv, text: string, send: (t: string) => Promise<unknown>): Promise<string | null> {
  const db = env.DB;
  const arg = text.trim().split(/\s+/)[1]?.toLowerCase() ?? "";
  if (["real", "ensaio", "off"].includes(arg)) {
    await setSetting(db, "envio_modo", arg);
    return {
      real: "🤖 Envio automático <b>real</b> ligado: às 11h as 🤖 que você não pular são enviadas de verdade (até " +
        (await num(db, "auto_max_day", 10)) + "/dia).",
      ensaio: "🧪 Modo <b>ensaio</b>: preencho e mando o print, sem enviar. As vagas seguem no pacote.",
      off: "⏹ Envio automático desligado. Tudo volta a ir pelo pacote do Sonnet.",
    }[arg]!;
  }
  if (arg === "agora") return kickAuto(env, send, Date.now(), true);
  if (/^\d+$/.test(arg)) {
    await setSetting(db, "auto_max_day", arg);
    return `Teto do envio automático: ${arg} por dia.`;
  }
  const modo = (await getSetting(db, "envio_modo")) || "ensaio";
  const today = localDate();
  const pend = await autoCandidates(db, today);
  const sent = await db
    .prepare(
      `SELECT j.company, j.title, a.notes FROM applications a JOIN jobs j ON j.id = a.job_id
        WHERE a.channel = 'auto' AND a.ts >= ? ORDER BY a.ts DESC LIMIT 15`
    )
    .bind(Date.now() / 1000 - 7 * 86400)
    .all<{ company: string; title: string; notes: string }>();
  return [
    `<b>Envio automático</b>: ${modo === "real" ? "🤖 real" : modo === "off" ? "⏹ desligado" : "🧪 ensaio"} · nota ≥ ${await num(db, "auto_min_fit", 75)} · até ${await num(db, "auto_max_day", 10)}/dia às ${await num(db, "auto_hour", 11)}h`,
    pend.length && (await getSetting(db, "envio_kick")) !== today
      ? `Hoje vão sozinhas: ${pend.map((j) => j.list_pos).join(", ")}`
      : "Hoje: nada pendente",
    sent.results.length
      ? "Enviadas sozinhas (7 dias):\n" + sent.results.map((r) => `• ${esc(r.company)} — ${esc(r.title)}${r.notes ? " ❓" : ""}`).join("\n")
      : "",
    "",
    "/envio real · /envio ensaio · /envio off · /envio agora · /envio 10 (teto/dia)",
  ]
    .filter((x) => x !== "")
    .join("\n");
}

/** Se o workflow não rodou: 'enviando' há mais de 2h volta para o pacote e avisa. */
export async function staleEnviando(db: D1Database, send: (t: string) => Promise<unknown>, now = Date.now()) {
  const r = await db
    .prepare(`UPDATE jobs SET status = 'aprovada', status_ts = ? WHERE status = 'enviando' AND status_ts < ? RETURNING list_pos`)
    .bind(now / 1000, now / 1000 - 2 * 3600)
    .all<{ list_pos: number }>();
  if (r.results.length)
    await send(`⚠️ O envio automático não terminou (Actions → envio). ${r.results.length} vagas voltaram para o pacote: /pacote`);
}
