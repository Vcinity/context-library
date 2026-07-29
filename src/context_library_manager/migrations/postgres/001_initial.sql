CREATE TABLE IF NOT EXISTS work_items (
  id TEXT PRIMARY KEY, project TEXT NOT NULL, item_type TEXT NOT NULL,
  idempotency_key TEXT NOT NULL UNIQUE, state TEXT NOT NULL,
  payload TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0,
  lease_owner TEXT, lease_expires TEXT, last_error TEXT,
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS events (
  id BIGSERIAL PRIMARY KEY, work_id TEXT, actor TEXT NOT NULL,
  event_type TEXT NOT NULL, payload TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS reviews (
  id TEXT PRIMARY KEY, project TEXT NOT NULL, work_id TEXT NOT NULL UNIQUE,
  question TEXT NOT NULL, choices TEXT NOT NULL, status TEXT NOT NULL,
  evidence TEXT NOT NULL, resolution TEXT, created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS notifications (
  id TEXT PRIMARY KEY, review_id TEXT NOT NULL UNIQUE, status TEXT NOT NULL,
  created_at TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0,
  next_attempt TEXT, last_error TEXT, delivered_at TEXT
);
CREATE TABLE IF NOT EXISTS review_evidence (
  id TEXT PRIMARY KEY, review_id TEXT NOT NULL, kind TEXT NOT NULL,
  payload TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS contributions (
  id TEXT PRIMARY KEY, project TEXT NOT NULL, actor TEXT NOT NULL,
  payload TEXT NOT NULL, idempotency_key TEXT NOT NULL UNIQUE,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS agent_runs (
  id TEXT PRIMARY KEY, work_id TEXT NOT NULL, profile TEXT NOT NULL,
  status TEXT NOT NULL, input_tokens INTEGER NOT NULL DEFAULT 0,
  output_tokens INTEGER NOT NULL DEFAULT 0,
  cache_hit INTEGER NOT NULL DEFAULT 0, cost DOUBLE PRECISION NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL, actor TEXT NOT NULL DEFAULT '',
  prompt_revision TEXT NOT NULL DEFAULT '1', cache_key TEXT,
  provider TEXT NOT NULL DEFAULT 'local-command', parent_run TEXT,
  started_at TEXT, finished_at TEXT
);
CREATE TABLE IF NOT EXISTS leases (
  work_id TEXT PRIMARY KEY, owner TEXT NOT NULL, expires_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS agent_cache (
  cache_key TEXT PRIMARY KEY, project TEXT NOT NULL, payload TEXT NOT NULL,
  input_tokens INTEGER NOT NULL, output_tokens INTEGER NOT NULL,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS project_budgets (
  project TEXT PRIMARY KEY, day TEXT NOT NULL,
  reserved_tokens INTEGER NOT NULL DEFAULT 0,
  spent_tokens INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS user_budgets (
  actor TEXT PRIMARY KEY, day TEXT NOT NULL,
  reserved_tokens INTEGER NOT NULL DEFAULT 0,
  spent_tokens INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS projects (
  id TEXT PRIMARY KEY, name TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS policy_revisions (
  id TEXT PRIMARY KEY, project TEXT NOT NULL, revision TEXT NOT NULL,
  payload TEXT NOT NULL, created_at TEXT NOT NULL, UNIQUE(project, revision)
);
CREATE TABLE IF NOT EXISTS publication_history (
  id TEXT PRIMARY KEY, project TEXT NOT NULL, work_id TEXT, status TEXT NOT NULL,
  digest TEXT, git_revision TEXT, error TEXT, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS autonomy_metrics (
  id TEXT PRIMARY KEY, project TEXT NOT NULL, window_start TEXT NOT NULL,
  window_days INTEGER NOT NULL, payload TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS audit_events (
  id TEXT PRIMARY KEY, work_id TEXT, project TEXT, actor TEXT NOT NULL,
  event_type TEXT NOT NULL, payload TEXT NOT NULL, created_at TEXT NOT NULL
);
