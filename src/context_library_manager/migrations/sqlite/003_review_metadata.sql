CREATE TABLE IF NOT EXISTS review_metadata (
  review_id TEXT PRIMARY KEY, urgency TEXT NOT NULL DEFAULT 'normal',
  reason TEXT NOT NULL DEFAULT 'evidence-conflict', source TEXT, owner TEXT
);
CREATE INDEX IF NOT EXISTS idx_review_metadata_filters
  ON review_metadata(urgency, reason, source, owner);
CREATE INDEX IF NOT EXISTS idx_reviews_project_status_created
  ON reviews(project, status, created_at, id);
INSERT OR IGNORE INTO review_metadata(review_id,urgency,reason,source,owner)
  SELECT r.id,
         COALESCE(json_extract(w.payload, '$.urgency'), 'normal'),
         COALESCE(json_extract(w.payload, '$.review_reason'), 'evidence-conflict'),
         json_extract(w.payload, '$.source_type'),
         json_extract(w.payload, '$.owner')
    FROM reviews r JOIN work_items w ON w.id = r.work_id;
