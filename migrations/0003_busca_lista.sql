-- Entrega 3: busca + lista no Telegram
ALTER TABLE jobs ADD COLUMN summary TEXT;   -- resumo de 1 linha (Gemini ou regra)
ALTER TABLE jobs ADD COLUMN contact TEXT;   -- contato conhecido na empresa (nome · cargo)
CREATE INDEX IF NOT EXISTS ix_jobs_seen ON jobs(status, last_seen);
CREATE INDEX IF NOT EXISTS ix_jobs_status_ts ON jobs(status, status_ts);
INSERT OR IGNORE INTO settings(key, value) VALUES ('list_ready', ''), ('list_sent', '');
