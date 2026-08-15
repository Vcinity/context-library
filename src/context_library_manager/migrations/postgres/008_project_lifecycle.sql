ALTER TABLE projects ADD COLUMN IF NOT EXISTS lifecycle TEXT NOT NULL DEFAULT 'enabled';
ALTER TABLE projects ADD COLUMN IF NOT EXISTS lifecycle_version INTEGER NOT NULL DEFAULT 1;
CREATE INDEX IF NOT EXISTS idx_projects_lifecycle ON projects(lifecycle, active);
