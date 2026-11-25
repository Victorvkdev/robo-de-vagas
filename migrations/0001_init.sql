-- Robô de Vagas v3 — schema inicial (D1 / SQLite)
-- Datas: epoch em segundos (REAL). Status unificado das vagas:
-- nova, listada, aprovada, enviada, em_analise, teste, entrevista, oferta,
-- recusada, pulada, precisa_voce, expirada, duplicada

CREATE TABLE IF NOT EXISTS jobs (
  id TEXT PRIMARY KEY,
  source TEXT,
  company TEXT,
  company_key TEXT,
  title TEXT,
  location TEXT,
  url TEXT,
  description TEXT,
  salary TEXT,
  regime TEXT,            -- CLT, PJ, Full-time, Contract...
  lang TEXT,              -- pt | en | es
  area TEXT,              -- product_design, uxui, frontend, design_eng, crm, web, suporte_qa
  level TEXT DEFAULT 'unknown',
  years INTEGER,
  yrs_phrase TEXT,
  region TEXT DEFAULT '',
  elig TEXT DEFAULT '',   -- aberta, regiao, bloqueada, presencial
  geo_ok INTEGER DEFAULT 1,
  fit REAL DEFAULT 0,
  ghost REAL DEFAULT 0,
  priority INTEGER,
  near INTEGER DEFAULT 0,
  reason TEXT,            -- motivo da nota
  risk TEXT,              -- avisos (pede 7 anos, só EUA...)
  apply_type TEXT,        -- auto, kit, linkedin, email
  status TEXT DEFAULT 'nova',
  status_ts REAL,
  posted_ts REAL,
  first_seen REAL,
  last_seen REAL,
  seen_count INTEGER DEFAULT 1,
  repost_count INTEGER DEFAULT 0,
  dkey TEXT,
  gone INTEGER DEFAULT 0,
  desc_partial INTEGER DEFAULT 0,
  list_date TEXT,         -- AAAA-MM-DD da lista em que entrou
  list_pos INTEGER,       -- posição na lista do dia (1..40)
  origin TEXT,            -- jobhunter | robo_v2 | v3
  notes TEXT DEFAULT ''
);
CREATE INDEX IF NOT EXISTS ix_jobs_status ON jobs(status, fit);
CREATE INDEX IF NOT EXISTS ix_jobs_ck ON jobs(company_key);
CREATE INDEX IF NOT EXISTS ix_jobs_dkey ON jobs(dkey);
CREATE INDEX IF NOT EXISTS ix_jobs_url ON jobs(url);
CREATE INDEX IF NOT EXISTS ix_jobs_list ON jobs(list_date, list_pos);

CREATE TABLE IF NOT EXISTS applications (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  job_id TEXT,
  ts REAL,
  channel TEXT,           -- auto, kit, linkedin, claude, email, manual
  via TEXT DEFAULT 'cold',-- cold, contato, indicacao
  cv_key TEXT,            -- arquivo no R2
  answers TEXT,           -- JSON das respostas enviadas
  proof_key TEXT,         -- print no R2
  state TEXT,             -- enviada, em_analise, teste, entrevista, oferta, recusada, sem_retorno
  state_ts REAL,
  origin TEXT,
  notes TEXT DEFAULT ''
);
CREATE INDEX IF NOT EXISTS ix_app_job ON applications(job_id);
CREATE INDEX IF NOT EXISTS ix_app_state ON applications(state, state_ts);

CREATE TABLE IF NOT EXISTS events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  job_id TEXT,
  application_id INTEGER,
  kind TEXT,              -- recebida, teste, entrevista, recusa, oferta, nota
  ts REAL,                -- quando aconteceu
  due_ts REAL,            -- data da entrevista / prazo do teste
  link TEXT,
  note TEXT DEFAULT '',
  email_id TEXT,
  origin TEXT
);
CREATE INDEX IF NOT EXISTS ix_events_due ON events(kind, due_ts);
CREATE INDEX IF NOT EXISTS ix_events_job ON events(job_id);

CREATE TABLE IF NOT EXISTS emails (
  id TEXT PRIMARY KEY,    -- Message-ID
  ts REAL,
  from_addr TEXT,
  subject TEXT,
  snippet TEXT,
  class TEXT,             -- entrevista, agendar, teste, recrutador, recusa, recebida, orcamento, spam, precisa_voce
  company TEXT,
  job_id TEXT,
  corrected_class TEXT,   -- botão "Classifiquei errado"
  thread_ref TEXT
);
CREATE INDEX IF NOT EXISTS ix_emails_ts ON emails(ts);

CREATE TABLE IF NOT EXISTS outbox (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  kind TEXT,              -- followup, resposta, agendar, indicacao, candidatura_email
  job_id TEXT,
  contact_id INTEGER,
  email_id TEXT,
  to_addr TEXT,
  subject TEXT,
  body TEXT,
  state TEXT DEFAULT 'aguardando', -- aguardando, aprovado, enviado, ignorado, erro
  created_ts REAL,
  updated_ts REAL,
  tg_message_id INTEGER
);
CREATE INDEX IF NOT EXISTS ix_outbox_state ON outbox(state);

CREATE TABLE IF NOT EXISTS contacts (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT, url TEXT UNIQUE, email TEXT, company TEXT, company_key TEXT,
  position TEXT, connected_on TEXT, source TEXT DEFAULT 'manual', relation TEXT DEFAULT 'connection',
  stage TEXT DEFAULT 'new', notes TEXT DEFAULT '', last_touch_ts REAL, next_touch_ts REAL, step INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS ix_contacts_ck ON contacts(company_key);
CREATE INDEX IF NOT EXISTS ix_contacts_next ON contacts(next_touch_ts);

CREATE TABLE IF NOT EXISTS interactions (
  id INTEGER PRIMARY KEY AUTOINCREMENT, contact_id INTEGER, ts REAL, kind TEXT, note TEXT DEFAULT ''
);
CREATE INDEX IF NOT EXISTS ix_inter_ts ON interactions(ts, kind);

CREATE TABLE IF NOT EXISTS templates (key TEXT PRIMARY KEY, name TEXT, lang TEXT, body TEXT);
CREATE TABLE IF NOT EXISTS targets (key TEXT PRIMARY KEY, name TEXT, priority INTEGER DEFAULT 2, notes TEXT DEFAULT '');

CREATE TABLE IF NOT EXISTS leads (
  id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, source TEXT DEFAULT '', stage TEXT DEFAULT 'new',
  value_brl REAL DEFAULT 0, notes TEXT DEFAULT '', created_ts REAL, updated_ts REAL
);
CREATE TABLE IF NOT EXISTS invoices (
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, amount_brl REAL, lead_id INTEGER, note TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS talent_pools (
  key TEXT PRIMARY KEY, name TEXT, url TEXT, status TEXT DEFAULT 'a_fazer', last_update_ts REAL, notes TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS profile (
  key TEXT PRIMARY KEY,   -- id do bloco do super contexto (DADOS.nome, B-PD-01...) ou 'super_contexto'
  version TEXT,
  lang TEXT,
  body TEXT,
  updated_ts REAL
);

CREATE TABLE IF NOT EXISTS sources_health (
  key TEXT PRIMARY KEY,   -- fonte:slug
  last_ok_ts REAL,
  fail_streak INTEGER DEFAULT 0,
  useful_count INTEGER DEFAULT 0,
  disabled INTEGER DEFAULT 0,
  last_error TEXT
);

CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT);

CREATE TABLE IF NOT EXISTS runs (
  id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT, started_ts REAL, finished_ts REAL, ok INTEGER, summary TEXT
);
