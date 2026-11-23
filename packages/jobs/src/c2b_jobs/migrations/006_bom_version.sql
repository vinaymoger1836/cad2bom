-- 006_bom_version.sql: Immutable frozen BoM versions per job (docs/05 §2).

CREATE TABLE IF NOT EXISTS bom_version (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    job_id       uuid NOT NULL REFERENCES job (id) ON DELETE CASCADE,
    version      integer NOT NULL,
    data         jsonb NOT NULL,
    created_at   timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT bom_version_job_version_unique UNIQUE (job_id, version)
);

CREATE INDEX IF NOT EXISTS bom_version_job_idx ON bom_version (job_id);
