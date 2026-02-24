CREATE EXTENSION IF NOT EXISTS "pgcrypto";

CREATE TABLE IF NOT EXISTS jobs (
  id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  repo_url       TEXT NOT NULL,
  branch         TEXT NOT NULL DEFAULT 'main',
  prd_path       TEXT NOT NULL DEFAULT 'docs/PRD.md',
  status         TEXT NOT NULL DEFAULT 'pending',
  metadata       JSONB,
  callback_url   TEXT,
  cloud_run_execution_id TEXT,
  created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs (status);

CREATE TABLE IF NOT EXISTS job_events (
  id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  job_id         UUID NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
  event          TEXT NOT NULL,
  detail         JSONB,
  created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_job_events_job_id ON job_events (job_id);

-- Deploy phase columns (added Phase 6)
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS pr_url TEXT;
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS live_url TEXT;
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS netlify_site_id TEXT;
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS neon_project_id TEXT;
