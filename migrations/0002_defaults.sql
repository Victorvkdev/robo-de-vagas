-- Ajustes padrão (sem dados pessoais). Valores pessoais entram pelo seed local, fora do repo.
INSERT OR IGNORE INTO settings(key, value) VALUES
  ('timezone', 'America/Fortaleza'),
  ('list_hour', '8'),
  ('list_size', '40'),
  ('max_send_day', '25'),
  ('max_per_company_month', '3'),
  ('quiet_start', '22'),
  ('quiet_end', '7'),
  ('paused', '0'),
  ('areas', '{"product_design":1.0,"uxui":1.0,"design_eng":0.95,"frontend":0.85,"crm":0.8,"web":0.7,"suporte_qa":0.6}'),
  ('followup_days', '10'),
  ('no_reply_days', '21');

INSERT OR IGNORE INTO talent_pools(key, name, url) VALUES
  ('revelo', 'Revelo', 'https://www.revelo.com'),
  ('strider', 'Strider', 'https://www.onstrider.com'),
  ('turing', 'Turing', 'https://www.turing.com'),
  ('beon', 'BEON.tech', 'https://beon.tech'),
  ('wellfound', 'Wellfound', 'https://wellfound.com'),
  ('toptal', 'Toptal', 'https://www.toptal.com'),
  ('andela', 'Andela', 'https://andela.com'),
  ('lemon', 'Lemon.io', 'https://lemon.io'),
  ('remotecom', 'Remote.com Talent', 'https://remote.com'),
  ('braintrust', 'Braintrust', 'https://www.usebraintrust.com');
