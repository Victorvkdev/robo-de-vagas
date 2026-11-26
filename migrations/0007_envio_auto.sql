-- Entrega 5b: envio automático com janela de veto + backup no R2
-- envio_modo: ensaio (preenche e manda print, não envia) até o Vk mandar /envio real
INSERT OR IGNORE INTO settings(key, value) VALUES
  ('envio_modo', 'ensaio'),
  ('auto_min_fit', '75'),
  ('auto_max_day', '10'),
  ('auto_hour', '11'),
  ('envio_kick', '');
CREATE INDEX IF NOT EXISTS ix_app_channel_ts ON applications(channel, ts);
