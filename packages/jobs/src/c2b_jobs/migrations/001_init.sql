-- P0 schema: job, upload, job_event (docs/05 §4 plus an event log for SSE replay).
-- pgvector is created now so the image choice is proven; it is first used by library search (P1+).
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE job (
    id                         uuid PRIMARY KEY,
    token_hash                 bytea NOT NULL,          -- SHA-256 of the access token; the token itself is never stored
    status                     text NOT NULL
        CHECK (status IN ('uploading', 'extracting', 'building', 'review', 'approved', 'failed')),
    product                    jsonb NOT NULL,
    library_version            text NOT NULL,
    consent_native_conversion  boolean NOT NULL DEFAULT false,
    created_at                 timestamptz NOT NULL DEFAULT now(),
    updated_at                 timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE upload (
    id             uuid PRIMARY KEY,
    job_id         uuid NOT NULL REFERENCES job (id) ON DELETE CASCADE,
    filename       text NOT NULL,                       -- original name, display only
    kind           text NOT NULL CHECK (kind IN ('cad', 'photo', 'bom_file', 'datasheet')),
    detected_type  text NOT NULL,                       -- from magic bytes, e.g. 'step', 'png'
    size_bytes     bigint NOT NULL,
    sha256         text NOT NULL,
    stored_path    text NOT NULL,                       -- relative to the data dir: jobs/<id>/in/<file_id><ext>
    created_at     timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX upload_job_idx ON upload (job_id);

CREATE TABLE job_event (
    id          bigserial PRIMARY KEY,
    job_id      uuid NOT NULL REFERENCES job (id) ON DELETE CASCADE,
    kind        text NOT NULL,
    message     text NOT NULL,
    data        jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX job_event_job_idx ON job_event (job_id, id);
