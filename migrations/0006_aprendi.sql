-- Respostas que o Vk ensina pelo bot (/aprendi pergunta → resposta). synced = 1 depois de ir para o Super_Contexto.md.
CREATE TABLE IF NOT EXISTS learned (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts REAL,
  q TEXT,
  a TEXT,
  synced INTEGER DEFAULT 0
);
