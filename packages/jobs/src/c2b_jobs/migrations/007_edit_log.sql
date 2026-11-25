-- 007_edit_log.sql: every user edit, answer and suggestion review, with before/after (docs/05 §2).

CREATE TABLE IF NOT EXISTS edit_log (
    id          bigserial PRIMARY KEY,
    job_id      uuid NOT NULL REFERENCES job (id) ON DELETE CASCADE,
    line_id     text,
    field       text NOT NULL,
    before      jsonb,
    after       jsonb,
    actor       text NOT NULL DEFAULT 'user',
    kind        text NOT NULL CHECK (kind IN ('edit', 'answer', 'suggestion')),
    ref         text,
    created_at  timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS edit_log_job_line_idx ON edit_log (job_id, line_id);
