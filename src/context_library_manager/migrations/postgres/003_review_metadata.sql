CREATE TABLE IF NOT EXISTS review_metadata (
  review_id TEXT PRIMARY KEY, urgency TEXT NOT NULL DEFAULT 'normal',
  reason TEXT NOT NULL DEFAULT 'evidence-conflict', source TEXT, owner TEXT
);
CREATE INDEX IF NOT EXISTS idx_review_metadata_filters
  ON review_metadata(urgency, reason, source, owner);
CREATE INDEX IF NOT EXISTS idx_reviews_project_status_created
  ON reviews(project, status, created_at, id);
INSERT INTO review_metadata(review_id,urgency,reason,source,owner)
  SELECT r.id,
         COALESCE(w.payload::jsonb ->> 'urgency', 'normal'),
         COALESCE(w.payload::jsonb ->> 'review_reason', 'evidence-conflict'),
         w.payload::jsonb ->> 'source_type',
         w.payload::jsonb ->> 'owner'
    FROM reviews r JOIN work_items w ON w.id = r.work_id
  ON CONFLICT(review_id) DO NOTHING;
