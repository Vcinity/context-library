CREATE TABLE IF NOT EXISTS browser_sessions (
  id TEXT PRIMARY KEY, subject TEXT NOT NULL, display_name TEXT NOT NULL,
  capabilities TEXT NOT NULL, allowed_projects TEXT NOT NULL,
  selected_project TEXT, csrf_digest TEXT NOT NULL, oidc_session_reference TEXT,
  issued_at TEXT NOT NULL, expires_at TEXT NOT NULL, invalidated_at TEXT
);
CREATE TABLE IF NOT EXISTS idempotency_records (
  id TEXT PRIMARY KEY, actor TEXT NOT NULL, project TEXT, route TEXT NOT NULL,
  idempotency_key TEXT NOT NULL, request_digest TEXT NOT NULL,
  response_status INTEGER NOT NULL, response TEXT NOT NULL,
  created_at TEXT NOT NULL, UNIQUE(actor, project, route, idempotency_key)
);
CREATE TABLE IF NOT EXISTS agent_service_state (
  singleton INTEGER PRIMARY KEY CHECK(singleton = 1), state TEXT NOT NULL,
  version INTEGER NOT NULL, reason TEXT NOT NULL, actor TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
INSERT INTO agent_service_state
  (singleton, state, version, reason, actor, updated_at)
  VALUES (1, 'running', 1, 'initial state', 'runtime:migration',
          '1970-01-01T00:00:00Z')
  ON CONFLICT(singleton) DO NOTHING;
CREATE TABLE IF NOT EXISTS process_heartbeats (
  process TEXT NOT NULL, instance_id TEXT NOT NULL, state TEXT NOT NULL,
  details TEXT NOT NULL, observed_at TEXT NOT NULL,
  PRIMARY KEY(process, instance_id)
);
CREATE TABLE IF NOT EXISTS work_cancellations (
  id TEXT PRIMARY KEY, work_id TEXT NOT NULL, agent_run_id TEXT NOT NULL,
  state TEXT NOT NULL, actor TEXT NOT NULL, reason TEXT NOT NULL,
  idempotency_key TEXT NOT NULL UNIQUE, requested_at TEXT NOT NULL,
  acknowledged_at TEXT
);
CREATE TABLE IF NOT EXISTS configuration_revisions (
  id TEXT PRIMARY KEY, project TEXT NOT NULL, revision INTEGER NOT NULL,
  actor TEXT NOT NULL, reason TEXT NOT NULL, values_json TEXT NOT NULL,
  rolled_back_from INTEGER, created_at TEXT NOT NULL,
  UNIQUE(project, revision)
);
CREATE TABLE IF NOT EXISTS configuration_changes (
  id TEXT PRIMARY KEY, revision_id TEXT NOT NULL, field_name TEXT NOT NULL,
  before_json TEXT, after_json TEXT, source TEXT NOT NULL,
  restart_required INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS library_proposals (
  id TEXT PRIMARY KEY, project TEXT NOT NULL, decision_id TEXT,
  operation TEXT NOT NULL, status TEXT NOT NULL, actor TEXT NOT NULL,
  work_id TEXT, contribution_id TEXT, library_digest TEXT NOT NULL,
  payload TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_work_project_state_time
  ON work_items(project, state, updated_at);
CREATE INDEX IF NOT EXISTS idx_reviews_project_status_time
  ON reviews(project, status, updated_at);
CREATE INDEX IF NOT EXISTS idx_audit_project_time
  ON audit_events(project, created_at);
CREATE INDEX IF NOT EXISTS idx_runs_work_status_time
  ON agent_runs(work_id, status, created_at);
CREATE INDEX IF NOT EXISTS idx_notifications_status_time
  ON notifications(status, created_at);
CREATE INDEX IF NOT EXISTS idx_proposals_project_status_time
  ON library_proposals(project, status, updated_at);
CREATE INDEX IF NOT EXISTS idx_config_project_revision
  ON configuration_revisions(project, revision);
