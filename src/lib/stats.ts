// Consultas compartilhadas entre o Worker (bot) e o painel (Pages Functions).

export const ACTIVE_STATES = ["enviada", "em_analise", "teste"];

export interface Stats {
  aplicadas: number;
  em_analise: number;
  entrevistas: number;
  recusadas: number;
  proxima: { quando: number; empresa: string | null; cargo: string | null; link: string | null } | null;
  novas: number;
}

export async function getStats(db: D1Database, now = Date.now() / 1000): Promise<Stats> {
  const [counts, next, novas] = await db.batch([
    db.prepare(
      `SELECT COUNT(*) AS aplicadas,
              SUM(CASE WHEN state IN ('enviada','em_analise','teste') THEN 1 ELSE 0 END) AS em_analise,
              SUM(CASE WHEN state IN ('entrevista','oferta') THEN 1 ELSE 0 END) AS entrevistas,
              SUM(CASE WHEN state = 'recusada' THEN 1 ELSE 0 END) AS recusadas
         FROM applications`
    ),
    db
      .prepare(
        `SELECT e.due_ts AS quando, j.company AS empresa, j.title AS cargo, e.link AS link
           FROM events e LEFT JOIN jobs j ON j.id = e.job_id
          WHERE e.kind = 'entrevista' AND e.due_ts >= ?
          ORDER BY e.due_ts ASC LIMIT 1`
      )
      .bind(now - 3600),
    db.prepare(`SELECT COUNT(*) AS n FROM jobs WHERE status = 'nova'`),
  ]);
  const c = (counts.results[0] ?? {}) as Record<string, number | null>;
  return {
    aplicadas: c.aplicadas ?? 0,
    em_analise: c.em_analise ?? 0,
    entrevistas: c.entrevistas ?? 0,
    recusadas: c.recusadas ?? 0,
    proxima: (next.results[0] as Stats["proxima"]) ?? null,
    novas: ((novas.results[0] as { n: number }) ?? { n: 0 }).n,
  };
}

export interface JobRow {
  id: string;
  title: string;
  company: string;
  url: string;
  fit: number;
  source: string;
  location: string | null;
  area: string | null;
  status: string;
  first_seen: number | null;
}

/** Lista do dia mais recente (ordem da lista); sem lista, as melhores vagas abertas do banco. */
export async function topJobs(db: D1Database, limit = 10): Promise<JobRow[]> {
  const listed = await db
    .prepare(
      `SELECT id, title, company, url, fit, source, location, area, status, first_seen
         FROM jobs
        WHERE list_date = (SELECT MAX(list_date) FROM jobs WHERE list_date IS NOT NULL) AND list_pos IS NOT NULL
        ORDER BY list_pos
        LIMIT ?`
    )
    .bind(limit)
    .all<JobRow>();
  if (listed.results.length) return listed.results;
  const r = await db
    .prepare(
      `SELECT id, title, company, url, fit, source, location, area, status, first_seen
         FROM jobs
        WHERE status IN ('nova','listada') AND COALESCE(geo_ok,1) = 1
          AND COALESCE(elig,'') NOT IN ('presencial','bloqueada') AND COALESCE(gone,0) = 0
        ORDER BY fit DESC, first_seen DESC
        LIMIT ?`
    )
    .bind(limit)
    .all<JobRow>();
  return r.results;
}

export async function getSetting(db: D1Database, key: string): Promise<string | null> {
  const r = await db.prepare(`SELECT value FROM settings WHERE key = ?`).bind(key).first<{ value: string }>();
  return r?.value ?? null;
}

export async function setSetting(db: D1Database, key: string, value: string): Promise<void> {
  await db
    .prepare(`INSERT INTO settings(key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value`)
    .bind(key, value)
    .run();
}

export function fmtDate(ts: number, tz = "America/Fortaleza"): string {
  return new Intl.DateTimeFormat("pt-BR", {
    timeZone: tz,
    day: "2-digit",
    month: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(ts * 1000));
}
