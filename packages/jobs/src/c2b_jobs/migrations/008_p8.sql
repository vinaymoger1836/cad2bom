-- 008_p8.sql: native conversion (APS), retention, admin audit (ADR-018, ADR-020, ADR-021).

-- New job status while a native file is converted by Autodesk.
ALTER TABLE job DROP CONSTRAINT IF EXISTS job_status_check;
ALTER TABLE job ADD CONSTRAINT job_status_check
    CHECK (status IN ('uploading', 'converting', 'extracting', 'building', 'review',
                      'approved', 'failed'));

-- Retention: when the job and its files are deleted. Backfilled for existing jobs.
ALTER TABLE job ADD COLUMN IF NOT EXISTS expires_at timestamptz;
UPDATE job SET expires_at = created_at + interval '30 days' WHERE expires_at IS NULL;
CREATE INDEX IF NOT EXISTS job_expires_idx ON job (expires_at);

-- Consent is recorded with the time and the version of the text the person saw.
ALTER TABLE job ADD COLUMN IF NOT EXISTS consent_native_at timestamptz;
ALTER TABLE job ADD COLUMN IF NOT EXISTS consent_native_text_ver text;

-- Files made by the system (converted STEP, OBJ) are tied to the upload they came from.
ALTER TABLE upload ADD COLUMN IF NOT EXISTS origin text NOT NULL DEFAULT 'user'
    CHECK (origin IN ('user', 'converted'));
ALTER TABLE upload ADD COLUMN IF NOT EXISTS derived_from uuid REFERENCES upload (id) ON DELETE CASCADE;
-- Native CAD formats are accepted as `cad` uploads; their `detected_type` is f3d / sldprt / sldasm.

-- What APS returned for one native upload (SolidWorks properties, OBJ files, usage).
CREATE TABLE IF NOT EXISTS job_native (
    job_id         uuid NOT NULL REFERENCES job (id) ON DELETE CASCADE,
    upload_id      uuid NOT NULL REFERENCES upload (id) ON DELETE CASCADE,
    source_format  text NOT NULL,
    status         text NOT NULL,
    message        text NOT NULL DEFAULT '',
    usage_jobs     integer NOT NULL DEFAULT 0,
    properties     jsonb NOT NULL DEFAULT '[]'::jsonb,
    obj_relpaths   jsonb NOT NULL DEFAULT '[]'::jsonb,
    created_at     timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (job_id, upload_id)
);

-- Monthly APS usage for the free-tier cap. One row per calendar month (UTC).
CREATE TABLE IF NOT EXISTS aps_usage (
    month       text PRIMARY KEY,            -- 'YYYY-MM'
    jobs        integer NOT NULL DEFAULT 0,
    updated_at  timestamptz NOT NULL DEFAULT now()
);

-- Admin actions (logins, drafts, publishes). No secrets, no file contents.
CREATE TABLE IF NOT EXISTS admin_audit (
    id          bigserial PRIMARY KEY,
    action      text NOT NULL,
    detail      jsonb NOT NULL DEFAULT '{}'::jsonb,
    ip          text,
    created_at  timestamptz NOT NULL DEFAULT now()
);
