// Pacote para o chat de candidaturas: um arquivo .md com o contexto fixo (settings.sonnet_context, fora do repo)
// e todas as vagas aprovadas, numeradas. No fim o chat devolve "enviei 1 3 5", que o Vk cola aqui no bot.

import { type ListJob, esc, localDate, markSent } from "./lista";
import { getSetting, setSetting } from "./stats";

interface PacoteEnv {
  DB: D1Database;
  TELEGRAM_BOT_TOKEN: string;
  ALLOWED_CHAT_ID: string;
}

interface PacoteJob extends ListJob {
  lang: string | null;
  source: string | null;
}

const TIPO: Record<string, string> = {
  auto: "formulário aberto (Greenhouse/Lever/Ashby/Workable…)",
  kit: "site da empresa ou Gupy (pode pedir login)",
  linkedin: "LinkedIn: clicar em Candidatar-se e seguir para o site da empresa; se for Easy Apply, pular e me avisar",
  email: "candidatura por e-mail: só montar o texto, não enviar",
};

function cvOf(j: PacoteJob): string {
  const pt = j.lang === "pt" || j.source === "gupy" || /gupy\.io/.test(j.url);
  return pt ? "adaptar a partir do PT (CV_Victor_Cavalcante_BR.docx)" : "adaptar a partir do EN (CV_Victor_Cavalcante.docx)";
}

const CV_RULES = [
  "## CV adaptado por vaga (obrigatório, nunca o genérico)",
  "Antes de preencher cada vaga:",
  "1. Leia a vaga e liste as 8 a 12 palavras-chave que importam (ferramentas, responsabilidades, área, senioridade, idioma).",
  "2. Parta do CV Master no idioma da vaga, em VK_JobSearch/01_Curriculos/01_Master (CV_Victor_Cavalcante.docx em inglês,",
  "   CV_Victor_Cavalcante_BR.docx em português).",
  "3. Ajuste para o fit: título alinhado ao cargo da vaga, resumo de 3 linhas com as palavras-chave, ordem e escolha dos",
  "   bullets (banco de bullets aprovados, seção 5 do Super_Contexto.md), skills e ferramentas na ordem que a vaga pede.",
  "4. Use só experiências, empresas, números e ferramentas reais (seções 4 a 7 do Super_Contexto.md). Nunca invente.",
  "   Se a vaga pede algo que eu talvez tenha e não está no contexto, me pergunte antes de omitir e grave a resposta na seção 14.",
  "5. Formato ATS: 1 coluna, sem tabela, sem foto, 1 a 2 páginas, \"mais de 6 anos\" / \"6+ years\".",
  "6. Salve em VK_JobSearch/01_Curriculos/02_Por_Vaga/AAAA-MM-DD_Empresa/CV_Victor_Cavalcante_Empresa.pdf e anexe ESSE arquivo.",
  "7. Na carta de apresentação (se o formulário tiver o campo), use as mesmas palavras-chave.",
];

export async function pacoteJobs(db: D1Database): Promise<PacoteJob[]> {
  const r = await db
    .prepare(
      `SELECT id, list_pos, title, company, location, url, salary, fit, summary, reason, risk, contact, apply_type, status, lang, source
         FROM jobs WHERE status IN ('aprovada','precisa_voce') ORDER BY fit DESC, status_ts DESC LIMIT 40`
    )
    .all<PacoteJob>();
  return r.results;
}

export function buildPacote(context: string, jobs: PacoteJob[], date: string, learned: { q: string; a: string; ts: number }[] = []): string {
  const head = [
    `# Candidaturas aprovadas (${date.slice(8, 10)}/${date.slice(5, 7)}) — ${jobs.length} vagas`,
    "",
    "Aplique em todas as vagas abaixo, uma por vez, na ordem, seguindo o contexto fixo. Não clique em enviar: eu reviso e envio.",
    "Quando eu disser que enviei, siga para a próxima. Se uma vaga não der (login que eu preciso criar, captcha, teste longo,",
    "presencial, Easy Apply do LinkedIn, vaga fechada), anote o motivo em 1 linha e siga.",
    "",
    "Respostas novas: antes de me perguntar algo, procure no Super_Contexto.md (VK_JobSearch/05_Contexto_e_Referencia),",
    "inclusive na seção \"## 14. RESPOSTAS APRENDIDAS\". Se não estiver lá e eu te responder, use a resposta e, na hora,",
    "acrescente no fim dessa seção (crie a seção se não existir) uma linha: `- [AAAA-MM-DD] Pergunta (PT/EN) → resposta`.",
    "Vale também para perguntas parecidas: a mesma resposta serve. Se não conseguir gravar no arquivo, me devolva as linhas no final.",
    "",
    "No final, me responda só com:",
    "1. a linha exata para eu colar no bot: `enviei` seguido dos números das que eu enviei (ex.: `enviei 1 3 5`);",
    "2. a lista das que ficaram pendentes, com o motivo;",
    "3. as respostas novas que entraram no Super_Contexto.md (ou que eu preciso colar lá).",
    "",
    "---",
    "",
    ...CV_RULES,
    "",
    "---",
    "",
    context.trim(),
    ...(learned.length
      ? ["", "## Respostas que eu passei pelo bot (valem como as da seção 14)", ...learned.map((l) => `- ${l.q} → ${l.a}`)]
      : []),
    "",
    "---",
    "",
    "## Vagas",
    "",
  ];
  const body = jobs.map((j, i) =>
    [
      `### ${i + 1}. ${j.title} — ${j.company || "?"}`,
      `- Link: ${j.url}`,
      `- Como aplicar: ${TIPO[j.apply_type ?? "kit"] ?? TIPO.kit}`,
      `- CV: ${cvOf(j)}`,
      j.location ? `- Local: ${j.location}` : "",
      j.salary ? `- Salário informado: ${j.salary}` : "",
      j.summary ? `- Resumo: ${j.summary}` : "",
      j.risk ? `- Atenção: ${j.risk}` : "",
      j.contact ? `- Conheço alguém lá: ${j.contact}` : "",
    ]
      .filter(Boolean)
      .join("\n")
  );
  return [...head, ...body.join("\n\n").split("\n"), ""].join("\n");
}

/** Manda o pacote como arquivo .md e guarda a ordem (para o "enviei 1 3 5"). */
export async function sendPacote(env: PacoteEnv, send: (t: string) => Promise<Response>): Promise<void> {
  const jobs = await pacoteJobs(env.DB);
  if (!jobs.length) {
    await send("Nenhuma vaga aprovada esperando envio.");
    return;
  }
  const context = (await getSetting(env.DB, "sonnet_context")) ?? "";
  const date = localDate();
  const learned = (
    await env.DB.prepare(`SELECT q, a, ts FROM learned WHERE synced = 0 ORDER BY id`).all<{ q: string; a: string; ts: number }>()
  ).results;
  const md = buildPacote(context || "(contexto fixo ainda não carregado no banco)", jobs, date, learned);
  await setSetting(env.DB, "pacote_map", JSON.stringify(jobs.map((j) => j.id)));
  const fd = new FormData();
  fd.set("chat_id", env.ALLOWED_CHAT_ID);
  fd.set(
    "caption",
    `📦 Pacote com ${jobs.length} vagas aprovadas. Cole o arquivo num chat novo do Sonnet. ` +
      `No fim ele te dá a linha "enviei …": cole aqui que eu marco as enviadas.`
  );
  fd.set("document", new File([md], `candidaturas_${date}.md`, { type: "text/markdown" }));
  const r = await fetch(`https://api.telegram.org/bot${env.TELEGRAM_BOT_TOKEN}/sendDocument`, { method: "POST", body: fd });
  if (!r.ok) throw new Error(`Telegram ${r.status}: ${(await r.text()).slice(0, 200)}`);
}

/** "enviei 1 3 5" · "enviei 1-4" · "enviei todas": marca como enviadas pela numeração do último pacote. */
export async function handleEnviei(env: PacoteEnv, text: string): Promise<string | null> {
  const t = text.trim().toLowerCase().normalize("NFD").replace(/[̀-ͯ]/g, "");
  const m = t.match(/^enviei\b(.*)$/s);
  if (!m) return null;
  const map = JSON.parse((await getSetting(env.DB, "pacote_map")) || "[]") as string[];
  if (!map.length) return "Não tenho pacote recente. Mande /pacote primeiro.";
  let nums: number[] = [];
  if (/\b(todas|tudo|all)\b/.test(m[1])) nums = map.map((_, i) => i + 1);
  for (const p of m[1].matchAll(/(\d{1,2})(?:\s*-\s*(\d{1,2}))?/g)) {
    const a = Number(p[1]);
    const b = p[2] ? Number(p[2]) : a;
    for (let i = Math.min(a, b); i <= Math.max(a, b); i++) nums.push(i);
  }
  nums = [...new Set(nums)].filter((n) => n >= 1 && n <= map.length);
  if (!nums.length) return "Diga quais: enviei 1 3 5 (números do pacote) ou enviei todas.";
  const done: string[] = [];
  for (const n of nums) {
    const j = await env.DB.prepare(
      `SELECT id, list_pos, title, company, location, url, salary, fit, summary, reason, risk, contact, apply_type, status FROM jobs WHERE id = ?`
    )
      .bind(map[n - 1])
      .first<ListJob>();
    if (!j || !["aprovada", "precisa_voce"].includes(j.status)) continue;
    await markSent(env.DB, j);
    done.push(`${n}. ${esc(j.company || "?")}`);
  }
  return done.length ? `📨 Marquei ${done.length} como enviadas:\n${done.join("\n")}` : "Essas já estavam marcadas.";
}

/** "/aprendi pergunta → resposta": guarda no banco; o contexto_sonnet.sh leva para a seção 14 do Super_Contexto.md. */
export async function handleAprendi(env: PacoteEnv, text: string): Promise<string> {
  const body = text.replace(/^\/aprendi(@\S+)?\s*/i, "").trim();
  const sep = ["→", "->", "=>", "=", ":"].find((x) => body.includes(x));
  const i = sep ? body.indexOf(sep) : -1;
  const m = i > 0 ? [body, body.slice(0, i), body.slice(i + sep!.length)] : null;
  if (!m || !m[1].trim() || !m[2].trim()) return "Formato: /aprendi pergunta → resposta\nEx.: /aprendi Tem CNPJ? → Sim, MEI ativo";
  await env.DB.prepare(`INSERT INTO learned (ts, q, a, synced) VALUES (?, ?, ?, 0)`)
    .bind(Date.now() / 1000, m[1].trim().slice(0, 300), m[2].trim().slice(0, 600))
    .run();
  return "📚 Guardei. Já vale no próximo pacote e nos rascunhos; entra no Super Contexto quando você rodar ./scripts/contexto_sonnet.sh.";
}
