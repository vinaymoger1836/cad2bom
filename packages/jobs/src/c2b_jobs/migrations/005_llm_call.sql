-- 005_llm_call.sql: Audited log of LLM calls across roles and jobs (docs/05 §2).

CREATE TABLE IF NOT EXISTS llm_call (
    id                bigserial PRIMARY KEY,
    job_id            uuid NOT NULL REFERENCES job (id) ON DELETE CASCADE,
    role              text NOT NULL,
    model             text NOT NULL,
    prompt_tokens     int NOT NULL,
    completion_tokens int NOT NULL,
    total_tokens      int NOT NULL,
    latency_ms        int NOT NULL,
    fallback_reason   text NOT NULL,
    line_id           text,
    cassette_key      text,
    created_at        timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS llm_call_job_idx ON llm_call (job_id, id);
