-- 003_cad_extract.sql: Persist raw CAD extraction results per upload.

CREATE TABLE IF NOT EXISTS cad_extract (
    job_id       uuid NOT NULL REFERENCES job (id) ON DELETE CASCADE,
    upload_id    uuid NOT NULL REFERENCES upload (id) ON DELETE CASCADE,
    data         jsonb NOT NULL,
    leaf_count   integer NOT NULL DEFAULT 0,
    created_at   timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (job_id, upload_id)
);

CREATE INDEX IF NOT EXISTS cad_extract_job_idx ON cad_extract (job_id);
