CREATE TABLE IF NOT EXISTS accounts(
  ig_id TEXT PRIMARY KEY, username TEXT, token_enc TEXT, token_expires INTEGER, updated INTEGER);
CREATE TABLE IF NOT EXISTS jobs(
  id TEXT PRIMARY KEY, ig_id TEXT NOT NULL, kind TEXT NOT NULL, state TEXT NOT NULL,
  run_at INTEGER, payload TEXT NOT NULL, approval_hash TEXT,
  container_id TEXT, children TEXT, media_id TEXT, intent TEXT,
  tries INTEGER DEFAULT 0, next_try_at INTEGER, resume_state TEXT, error TEXT,
  comment_state TEXT, comment_id TEXT, comment_tries INTEGER DEFAULT 0,
  created INTEGER, updated INTEGER, published_at INTEGER, cleaned INTEGER DEFAULT 0);
CREATE INDEX IF NOT EXISTS ix_jobs_state ON jobs(state, run_at);
CREATE TABLE IF NOT EXISTS attempts(
  id INTEGER PRIMARY KEY AUTOINCREMENT, job_id TEXT, op TEXT, status TEXT, ts INTEGER, detail TEXT);
CREATE TABLE IF NOT EXISTS oauth_handoff(
  state TEXT PRIMARY KEY, code TEXT, created INTEGER, claimed INTEGER DEFAULT 0);
