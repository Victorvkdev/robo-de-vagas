// API do painel (Cloudflare Pages Functions). Só responde com um JWT válido do Cloudflare Access
// emitido para o e-mail autorizado. Sem Access configurado, tudo dá 401 (fecha por padrão).

import { getStats, topJobs } from "../../../src/lib/stats";

interface Env {
  DB: D1Database;
  ACCESS_TEAM_DOMAIN: string; // ex.: vk.cloudflareaccess.com
  ACCESS_AUD: string; // Application Audience (AUD) tag
  ALLOWED_EMAIL: string;
}

const b64u = (s: string) => Uint8Array.from(atob(s.replace(/-/g, "+").replace(/_/g, "/")), (c) => c.charCodeAt(0));
let certCache: { at: number; keys: JsonWebKey[] } | null = null;

async function verifyAccess(req: Request, env: Env): Promise<boolean> {
  const token = req.headers.get("cf-access-jwt-assertion");
  if (!token || !env.ACCESS_TEAM_DOMAIN || !env.ACCESS_AUD || !env.ALLOWED_EMAIL) return false;
  const [h, p, sig] = token.split(".");
  if (!h || !p || !sig) return false;
  const header = JSON.parse(new TextDecoder().decode(b64u(h)));
  const payload = JSON.parse(new TextDecoder().decode(b64u(p)));

  if (!certCache || Date.now() - certCache.at > 3600_000) {
    const r = await fetch(`https://${env.ACCESS_TEAM_DOMAIN}/cdn-cgi/access/certs`);
    certCache = { at: Date.now(), keys: ((await r.json()) as { keys: JsonWebKey[] }).keys };
  }
  const jwk = certCache.keys.find((k) => (k as JsonWebKey & { kid?: string }).kid === header.kid);
  if (!jwk) return false;
  const key = await crypto.subtle.importKey("jwk", jwk, { name: "RSASSA-PKCS1-v1_5", hash: "SHA-256" }, false, ["verify"]);
  const ok = await crypto.subtle.verify("RSASSA-PKCS1-v1_5", key, b64u(sig), new TextEncoder().encode(`${h}.${p}`));
  if (!ok) return false;

  const now = Date.now() / 1000;
  const aud = Array.isArray(payload.aud) ? payload.aud : [payload.aud];
  return (
    payload.exp > now &&
    aud.includes(env.ACCESS_AUD) &&
    payload.iss === `https://${env.ACCESS_TEAM_DOMAIN}` &&
    String(payload.email).toLowerCase() === env.ALLOWED_EMAIL.toLowerCase()
  );
}

export const onRequestGet: PagesFunction<Env> = async ({ request, env, params }) => {
  if (!(await verifyAccess(request, env))) return new Response("unauthorized", { status: 401 });
  const route = (params.route as string[] | undefined)?.join("/") ?? "";
  const db = env.DB;

  switch (route) {
    case "stats":
      return Response.json(await getStats(db));
    case "jobs":
      return Response.json(await topJobs(db, 40));
    case "applications": {
      const r = await db
        .prepare(
          `SELECT a.id, a.ts, a.state, a.state_ts, a.channel, a.via, a.notes, j.title, j.company, j.url
             FROM applications a LEFT JOIN jobs j ON j.id = a.job_id
            ORDER BY COALESCE(a.state_ts, a.ts) DESC LIMIT 200`
        )
        .all();
      return Response.json(r.results);
    }
    case "network": {
      // vagas abertas em empresas onde há contato
      const r = await db
        .prepare(
          `SELECT j.company, j.title, j.url, c.name AS contato, c.position, c.url AS contato_url
             FROM jobs j JOIN contacts c ON c.company_key = j.company_key AND c.company_key <> ''
            WHERE j.status IN ('nova','listada')
            ORDER BY j.fit DESC LIMIT 100`
        )
        .all();
      const total = await db.prepare(`SELECT COUNT(*) AS n FROM contacts`).first<{ n: number }>();
      return Response.json({ contatos: total?.n ?? 0, matches: r.results });
    }
    case "pools":
      return Response.json((await db.prepare(`SELECT * FROM talent_pools ORDER BY name`).all()).results);
    default:
      return new Response("not found", { status: 404 });
  }
};
