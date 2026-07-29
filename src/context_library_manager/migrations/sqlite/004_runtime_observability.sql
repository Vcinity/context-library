ALTER TABLE audit_events ADD COLUMN capability TEXT;
ALTER TABLE audit_events ADD COLUMN run_id TEXT;
ALTER TABLE audit_events ADD COLUMN policy_revision INTEGER;
ALTER TABLE audit_events ADD COLUMN before_reference TEXT;
ALTER TABLE audit_events ADD COLUMN after_reference TEXT;
CREATE INDEX IF NOT EXISTS idx_audit_project_actor_time
  ON audit_events(project, actor, created_at, id);
CREATE INDEX IF NOT EXISTS idx_audit_project_action_time
  ON audit_events(project, event_type, created_at, id);
CREATE INDEX IF NOT EXISTS idx_audit_project_work_time
  ON audit_events(project, work_id, created_at, id);
CREATE INDEX IF NOT EXISTS idx_audit_project_run_time
  ON audit_events(project, run_id, created_at, id);
