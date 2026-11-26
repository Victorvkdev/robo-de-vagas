-- O que o Vk enviou pelo Zoho (pasta Enviados): só o necessário para saber se um e-mail já foi respondido.
CREATE TABLE IF NOT EXISTS sent_mail (
  msgid TEXT PRIMARY KEY,
  ts REAL,
  to_addrs TEXT,
  subject_norm TEXT,
  refs TEXT
);
CREATE INDEX IF NOT EXISTS ix_sent_ts ON sent_mail(ts);
-- Pendências e testes marcados como feitos (botão ✅ Feito).
ALTER TABLE events ADD COLUMN done INTEGER DEFAULT 0;
