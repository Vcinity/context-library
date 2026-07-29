ALTER TABLE notifications ADD COLUMN claim_owner TEXT;
ALTER TABLE notifications ADD COLUMN claimed_at TEXT;
ALTER TABLE notifications ADD COLUMN claim_expires TEXT;
CREATE INDEX IF NOT EXISTS idx_notifications_claimable
  ON notifications(status, next_attempt, claim_expires, created_at);
CREATE TABLE IF NOT EXISTS contribution_work_links (
  contribution_id TEXT PRIMARY KEY, work_id TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_contribution_work_links_work
  ON contribution_work_links(work_id);
