CREATE TABLE IF NOT EXISTS telemetry_project_counters (
  project TEXT PRIMARY KEY, next_sequence INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS telemetry_producer_counters (
  project TEXT NOT NULL, producer TEXT NOT NULL, next_sequence INTEGER NOT NULL,
  PRIMARY KEY(project, producer)
);
CREATE TABLE IF NOT EXISTS telemetry_events (
  id TEXT PRIMARY KEY, project TEXT NOT NULL, project_sequence INTEGER NOT NULL,
  producer TEXT NOT NULL, producer_sequence INTEGER NOT NULL, item_id TEXT,
  event_type TEXT NOT NULL, actor_class TEXT NOT NULL, payload TEXT NOT NULL,
  occurred_at TEXT NOT NULL,
  UNIQUE(project, project_sequence),
  UNIQUE(project, producer, producer_sequence)
);
CREATE TABLE IF NOT EXISTS telemetry_manifests (
  id TEXT PRIMARY KEY, project TEXT NOT NULL, revision TEXT NOT NULL,
  required_producers TEXT NOT NULL, effective_at TEXT NOT NULL,
  UNIQUE(project, revision)
);
CREATE TABLE IF NOT EXISTS telemetry_watermarks (
  project TEXT NOT NULL, producer TEXT NOT NULL, producer_sequence INTEGER NOT NULL,
  occurred_at TEXT NOT NULL, PRIMARY KEY(project, producer)
);
CREATE TABLE IF NOT EXISTS telemetry_collector_errors (
  id TEXT PRIMARY KEY, project TEXT NOT NULL, producer TEXT,
  gap_start TEXT NOT NULL, gap_end TEXT NOT NULL, reason TEXT NOT NULL,
  reconciled INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS telemetry_item_state (
  item_id TEXT PRIMARY KEY, project TEXT NOT NULL, intake_at TEXT,
  policy_revision TEXT, eligibility TEXT, current_state TEXT NOT NULL,
  last_event_sequence INTEGER NOT NULL, updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_telemetry_project_time
  ON telemetry_events(project, occurred_at, project_sequence);
CREATE INDEX IF NOT EXISTS idx_telemetry_item_time
  ON telemetry_events(project, item_id, occurred_at, project_sequence);
CREATE INDEX IF NOT EXISTS idx_telemetry_producer_time
  ON telemetry_events(project, producer, occurred_at, producer_sequence);
