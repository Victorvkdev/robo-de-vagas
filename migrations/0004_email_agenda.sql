-- Entrega 4: e-mail, avisos, agenda, rascunhos, follow-up.
-- Também reduz gravações no D1 (ajuste b da entrega 3): hash por vaga e menos índices em jobs.

ALTER TABLE jobs ADD COLUMN h TEXT;            -- hash dos campos relevantes: igual = não regrava
DROP INDEX IF EXISTS ix_jobs_seen;
DROP INDEX IF EXISTS ix_jobs_url;
DROP INDEX IF EXISTS ix_jobs_status_ts;

ALTER TABLE emails ADD COLUMN msgid TEXT;       -- Message-ID original (emails.id é um hash curto, cabe no botão)
ALTER TABLE emails ADD COLUMN refs TEXT;        -- References, para responder na mesma thread
ALTER TABLE emails ADD COLUMN reply_to TEXT;
ALTER TABLE emails ADD COLUMN lang TEXT;
ALTER TABLE emails ADD COLUMN due_ts REAL;      -- data da entrevista / prazo do teste
ALTER TABLE emails ADD COLUMN link TEXT;
ALTER TABLE emails ADD COLUMN redo INTEGER DEFAULT 0; -- corrigido no Telegram: o robô refaz rascunho se precisar

ALTER TABLE events ADD COLUMN company TEXT;
ALTER TABLE events ADD COLUMN title TEXT;
ALTER TABLE events ADD COLUMN r24 INTEGER DEFAULT 0;  -- lembrete de 24h enviado
ALTER TABLE events ADD COLUMN r1 INTEGER DEFAULT 0;   -- lembrete de 1h enviado

ALTER TABLE outbox ADD COLUMN lang TEXT;
ALTER TABLE outbox ADD COLUMN in_reply_to TEXT;
ALTER TABLE outbox ADD COLUMN refs TEXT;
ALTER TABLE outbox ADD COLUMN note TEXT;        -- aviso da checagem de fatos
ALTER TABLE outbox ADD COLUMN sent_ts REAL;
ALTER TABLE outbox ADD COLUMN error TEXT;

-- Avisos do Telegram. O que cai no silêncio (22h–7h) espera aqui; o Worker manda às 7h.
CREATE TABLE IF NOT EXISTS notices (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts REAL,
  urgent INTEGER DEFAULT 0,
  text TEXT,
  keyboard TEXT,          -- JSON do inline_keyboard
  doc TEXT,               -- anexo (.ics)
  doc_name TEXT,
  sent_ts REAL
);
CREATE INDEX IF NOT EXISTS ix_notices_pending ON notices(sent_ts);

-- Correções do botão "Classifiquei errado": viram regra.
CREATE TABLE IF NOT EXISTS mail_rules (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  kind TEXT,              -- from | domain | from_subject
  pattern TEXT,
  class TEXT,
  created_ts REAL
);

INSERT OR IGNORE INTO settings(key, value) VALUES
  ('mail_state', ''),
  ('mail_signature', 'Victor Cavalcante
Product Designer & Front-end Developer
victorvk.com'),
  ('cal_link', ''),
  ('followup_date', '');
