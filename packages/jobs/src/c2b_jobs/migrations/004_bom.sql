-- 004_bom.sql: Persist draft EBOM per job (docs/05 §2).

CREATE TABLE IF NOT EXISTS job_bom (
    job_id       uuid PRIMARY KEY REFERENCES job (id) ON DELETE CASCADE,
    data         jsonb NOT NULL,
    updated_at   timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS job_bom_job_idx ON job_bom (job_id);
