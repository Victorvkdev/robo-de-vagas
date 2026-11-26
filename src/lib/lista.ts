// Lista diária no Telegram: cards, botões ✅/❌, "ok" por texto e aprovações.
// Envio de verdade chega na entrega 5; até lá a aprovação só muda o status (e /aprovadas mostra o que falta enviar).

export const TZ = "America/Fortaleza";
export const APPROVED = ["aprovada", "precisa_voce"];

export interface ListJob {
  id: string;
  list_pos: number;
  title: string;
  company: string | null;
  location: string | null;
  url: string;
  salary: string | null;
  fit: number;
  summary: string | null;
  reason: string | null;
  risk: string | null;
  contact: string | null;
  apply_type: string | null;
  status: string;
}

export interface Button {
  text: string;
  callback_data: string;
}
export type Keyboard = Button[][];

export const esc = (s: unknown) =>
  String(s ?? "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");

const cut = (s: string | null | undefined, n: number) => {
  const t = String(s ?? "").replace(/\s+/g, " ").trim();
  return t.length > n ? t.slice(0, n - 1) + "…" : t;
};

/** AAAA-MM-DD no fuso do Vk. */
export function localDate(now = Date.now(), tz = TZ): string {
  return new Intl.DateTimeFormat("en-CA", { timeZone: tz, year: "numeric", month: "2-digit", day: "2-digit" }).format(
    new Date(now)
  );
}

export function localHour(now = Date.now(), tz = TZ): number {
  return Number(new Intl.DateTimeFormat("en-US", { timeZone: tz, hour: "2-digit", hourCycle: "h23" }).format(new Date(now)));
}

/** Início do dia local em epoch (s). Fortaleza não tem horário de verão: UTC-3 fixo. */
export function dayStart(date: string): number {
  return Date.parse(`${date}T00:00:00-03:00`) / 1000;
}

export const fmtDay = (date: string) => `${date.slice(8, 10)}/${date.slice(5, 7)}`;

// ---------------------------------------------------------------- "ok"
export interface OkCmd {
  mode: "all" | "only" | "except";
  nums: number[];
}

/** "ok" · "ok 1 3 5" · "ok 1,3-5" · "ok menos 2 4" · "ok exceto 2". null se não for um ok. */
export function parseOk(text: string): OkCmd | null {
  const t = text
    .trim()
    .toLowerCase()
    .normalize("NFD")
    .replace(/[̀-ͯ]/g, "");
  const m = t.match(/^(?:ok|okay|aprovo|aprova)\b(.*)$/s);
  if (!m) return null;
  const rest = m[1];
  const nums: number[] = [];
  for (const part of rest.matchAll(/(\d{1,3})(?:\s*-\s*(\d{1,3}))?/g)) {
    const a = Number(part[1]);
    const b = part[2] ? Number(part[2]) : a;
    for (let i = Math.min(a, b); i <= Math.max(a, b) && i - Math.min(a, b) < 60; i++) nums.push(i);
  }
  const except = /\b(menos|exceto|sem|but|except)\b/.test(rest);
  if (except) return { mode: "except", nums };
  if (nums.length) return { mode: "only", nums };
  if (rest.trim() === "" || /^(todas|tudo|all)\W*$/.test(rest.trim())) return { mode: "all", nums: [] };
  return null;
}

/** Decide quem aprovar. LinkedIn nunca é enviado pelo robô: vira "precisa_voce" e não conta no teto. */
export function pickApprovals(
  jobs: Pick<ListJob, "id" | "list_pos" | "apply_type" | "status">[],
  cmd: OkCmd,
  remaining: number
): { approve: string[]; linkedin: string[]; overCap: number[] } {
  const open = jobs.filter((j) => j.status === "listada").sort((a, b) => a.list_pos - b.list_pos);
  let chosen = open;
  if (cmd.mode === "only") chosen = open.filter((j) => cmd.nums.includes(j.list_pos));
  if (cmd.mode === "except") chosen = open.filter((j) => !cmd.nums.includes(j.list_pos));
  const approve: string[] = [];
  const linkedin: string[] = [];
  const overCap: number[] = [];
  for (const j of chosen) {
    if (j.apply_type === "linkedin") linkedin.push(j.id);
    else if (approve.length < remaining) approve.push(j.id);
    else overCap.push(j.list_pos);
  }
  return { approve, linkedin, overCap };
}

// ---------------------------------------------------------------- cards
const ENVIO: Record<string, string> = {
  auto: "🤖 envio automático",
  kit: "🧰 kit (formulário com login ou e-mail)",
  linkedin: "✋ LinkedIn: você aplica",
  email: "✉️ por e-mail",
};

/** 🤖 da janela de veto: formulário aberto (ATS) e nota alta. Sai sozinha às 11h se o Vk não pular. */
export const isAuto = (j: Pick<ListJob, "apply_type" | "fit">, minFit: number) => j.apply_type === "auto" && j.fit >= minFit;

export function card(j: ListJob, autoMin = 75): string {
  const head = `<b>${j.list_pos}.</b> <a href="${esc(j.url)}">${esc(cut(j.title, 80))}</a>`;
  const meta = [cut(j.company, 40) || "?", cut(j.location, 36), `⭐ ${Math.round(j.fit)}`, j.salary ? `💰 ${cut(j.salary, 30)}` : ""]
    .filter(Boolean)
    .map(esc)
    .join(" · ");
  const lines = [head, meta];
  if (j.summary) lines.push(esc(cut(j.summary, 140)));
  if (j.reason) lines.push("💡 " + esc(cut(j.reason, 80)));
  if (j.risk) lines.push("⚠️ " + esc(cut(j.risk, 90)));
  if (j.contact) lines.push("👥 você conhece: " + esc(cut(j.contact, 60)));
  lines.push(
    isAuto(j, autoMin) ? "🤖 vai sozinha às 11h com CV adaptado (❌ para vetar)" : j.apply_type === "auto" ? "📝 formulário aberto (pacote)" : ENVIO[j.apply_type ?? "kit"] ?? ENVIO.kit
  );
  return lines.join("\n");
}

export function row(j: Pick<ListJob, "id" | "list_pos" | "status">): Button[] {
  if (j.status === "aprovada") return [{ text: `✅ ${j.list_pos} aprovada · desfazer`, callback_data: `un:${j.id}` }];
  if (j.status === "precisa_voce") return [{ text: `✋ ${j.list_pos} com você · desfazer`, callback_data: `un:${j.id}` }];
  if (j.status === "pulada") return [{ text: `❌ ${j.list_pos} pulada · desfazer`, callback_data: `un:${j.id}` }];
  if (j.status === "enviada") return [{ text: `📨 ${j.list_pos} enviada`, callback_data: "noop" }];
  if (j.status === "enviando") return [{ text: `🤖 ${j.list_pos} enviando…`, callback_data: "noop" }];
  return [
    { text: `✅ ${j.list_pos}`, callback_data: `ap:${j.id}` },
    { text: `❌ ${j.list_pos}`, callback_data: `sk:${j.id}` },
  ];
}

/** Tamanho que o Telegram conta: texto visível, sem tags nem links. */
const visible = (html: string) => html.replace(/<[^>]+>/g, "").replace(/&(amp|lt|gt|quot);/g, "x").length;

/** Blocos de até 10 vagas, sem passar do limite de 4096 caracteres do Telegram. */
export function blocks(jobs: ListJob[], date: string, cap: number, autoMin = 75): { text: string; keyboard: Keyboard }[] {
  const out: { text: string; keyboard: Keyboard }[] = [];
  const header =
    `<b>📋 Lista de ${fmtDay(date)}</b> · ${jobs.length} vagas\n` +
    `Toque ✅/❌ ou responda <b>ok</b> (as ${cap} primeiras), <b>ok 1 3 5</b> ou <b>ok menos 2 4</b>.` +
    (jobs.some((j) => isAuto(j, autoMin) && j.status === "listada")
      ? `\n🤖 = sai sozinha às 11h se você não tocar ❌.`
      : "");
  let cur: ListJob[] = [];
  let size = visible(header);
  const flush = () => {
    if (!cur.length) return;
    const first = cur[0].list_pos;
    const last = cur[cur.length - 1].list_pos;
    const top = out.length === 0 ? header + "\n\n" : `<b>${first}–${last}</b>\n\n`;
    out.push({ text: top + cur.map((j) => card(j, autoMin)).join("\n\n"), keyboard: cur.map(row) });
    cur = [];
    size = 40;
  };
  for (const j of jobs) {
    const c = visible(card(j, autoMin));
    if (cur.length >= 10 || size + c + 2 > 3900) flush();
    cur.push(j);
    size += c + 2;
  }
  flush();
  return out;
}

/** Troca a linha de botões de uma vaga dentro do teclado de uma mensagem já enviada. */
export function replaceRow(kb: Keyboard, jobId: string, newRow: Button[]): Keyboard {
  return kb.map((r) => (r.some((b) => b.callback_data.endsWith(":" + jobId)) ? newRow : r));
}

// ---------------------------------------------------------------- banco
const LIST_COLS = `id, list_pos, title, company, location, url, salary, fit, summary, reason, risk, contact, apply_type, status`;

export async function todayList(db: D1Database, date: string): Promise<ListJob[]> {
  const r = await db
    .prepare(
      `SELECT ${LIST_COLS} FROM jobs WHERE list_date = ? AND list_pos IS NOT NULL
        AND status IN ('listada','aprovada','precisa_voce','pulada','enviada','enviando') ORDER BY list_pos`
    )
    .bind(date)
    .all<ListJob>();
  return r.results;
}

export async function getJob(db: D1Database, id: string): Promise<ListJob | null> {
  return db.prepare(`SELECT ${LIST_COLS} FROM jobs WHERE id = ?`).bind(id).first<ListJob>();
}

export async function approvedToday(db: D1Database, date: string): Promise<number> {
  const r = await db
    .prepare(`SELECT COUNT(*) AS n FROM jobs WHERE status IN ('aprovada','enviada') AND status_ts >= ? AND list_date = ?`)
    .bind(dayStart(date), date)
    .first<{ n: number }>();
  return r?.n ?? 0;
}

export async function setStatus(db: D1Database, ids: string[], status: string, now = Date.now() / 1000) {
  if (!ids.length) return;
  await db.batch(ids.map((id) => db.prepare(`UPDATE jobs SET status = ?, status_ts = ? WHERE id = ?`).bind(status, now, id)));
}

export async function markSent(db: D1Database, j: ListJob, now = Date.now() / 1000) {
  await db.batch([
    db.prepare(`UPDATE jobs SET status = 'enviada', status_ts = ? WHERE id = ?`).bind(now, j.id),
    db
      .prepare(
        `INSERT INTO applications (job_id, ts, channel, via, state, state_ts, origin) VALUES (?, ?, ?, ?, 'enviada', ?, 'v3')`
      )
      .bind(j.id, now, j.apply_type === "linkedin" ? "linkedin" : "manual", j.contact ? "contato" : "cold", now),
  ]);
}

export async function pendingApproved(db: D1Database): Promise<ListJob[]> {
  const r = await db
    .prepare(`SELECT ${LIST_COLS} FROM jobs WHERE status IN ('aprovada','precisa_voce') ORDER BY status_ts DESC LIMIT 30`)
    .all<ListJob>();
  return r.results;
}
