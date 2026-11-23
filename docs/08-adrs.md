# 08 — Architecture decision records

Status: **Accepted** (owner, 2026-11-20) unless marked *Proposed*. New decisions during implementation are added as *Proposed*.

### ADR-001 — Rules first, LLM only as a constrained fallback
**Context:** A BoM must be reproducible and explainable; LLMs invent part numbers, quantities and standards; free-tier quotas are small.
**Decision:** Deterministic rules from the library produce every line. The LLM is called only at six ladder points (doc 04 §9) with closed-enum structured output, never for numbers, and its contributions are capped in confidence and always labelled.
**Alternatives:** LLM drafts the BoM and rules check it (fast, but hard to explain and to keep stable); pure rules with no LLM (stalls on unknown parts and messy files).
**Consequences:** A larger library to build and maintain; very low token use; explainable lines; unknowns become questions.

### ADR-002 — Standalone repo and library; platform-compatible model
**Decision:** New repo with its own library. `Prov` and `BomLine` shapes stay compatible with the Factory Layout Platform's S2 so the platform can later call this app as a service.
**Consequences:** Some duplicated infrastructure (compose, worker, router) copied from the platform's P0 patterns.

### ADR-003 — STEP via OpenCascade in a sandboxed worker
**Decision:** OCP/CadQuery with XCAF in a separate container: no network, CPU/RAM/time limits, read-only root.
**Alternatives:** Browser-side OpenCascade WASM (keeps files off the server, but a 10 MB download and no server-side rules on geometry); commercial SDKs (cost).
**Consequences:** Heavy image (multi-arch build); untrusted files isolated.

### ADR-004 — Native SolidWorks and Fusion through an optional APS adapter
**Context:** No reliable free library reads SLDPRT/SLDASM/F3D. APS Model Derivative translates F3D to STEP and SolidWorks to SVF2/OBJ with model properties.
**Decision:** Optional adapter behind an interface, off by default, per-job user consent, usage cap, transient storage. Recommend STEP AP242 export from SolidWorks for best geometry.
**Alternatives:** Require STEP only (simplest, but friction for SolidWorks users); CAD Exchanger or HOOPS SDK (paid licences, better coverage; can be added behind the same interface).
**Consequences:** Files leave the server for conversion; APS pricing and free-tier limits must be watched.

### ADR-005 — Standard-part recognition from geometry and tables
**Decision:** Recognisers measure features and match standard tables with tolerances; grades/materials of standard parts come from the organisation profile, never from geometry.
**Consequences:** Good results on catalogue parts with bad names; tables must be entered with sources; inch series later.

### ADR-006 — Noun-first descriptions and organisation profiles
**Decision:** Descriptions built from class templates (`NOUN, MODIFIERS, SIZE, STANDARD, GRADE`); numbering, description style, defaults and export columns per organisation profile.
**Consequences:** Consistent BoMs across jobs; profiles to maintain.

### ADR-007 — Suggestions, not silent changes; approval gate
**Decision:** User BoM lines are never rewritten; all additions and changes are suggestions; approval blocked while questions or suggestions are open.

### ADR-008 — Plain deterministic pipeline; LangGraph only for the chat agent
**Decision:** The BoM pipeline is a plain step runner with checkpoints in Postgres (procrastinate jobs). LangGraph is used only for the conversational agent, whose proposals return as suggestions.
**Alternatives:** LangGraph for the whole pipeline (as in the platform): useful there for many human approvals across stages, unnecessary here.

### ADR-009 — LiteLLM router on Groq and Gemini free tiers
**Decision:** Roles `reasoner`, `extractor`, `vision`, `agent` with budgets, fallbacks and cassettes; paid models by config later.

### ADR-010 — ECAD import later
**Decision:** v1 takes PCB BoMs as files (CSV/XLSX with refdes). IPC-2581/ODB++/KiCad import is a later phase.

## Proposed during implementation

### ADR-011 — Shared `packages/jobs` for settings, database and the queue app (*Proposed*, P0)
**Context:** The API must defer procrastinate tasks and read/write job rows; the worker needs the same settings, schema and queue names. `packages/domain` must stay pure Pydantic (mypy strict, no I/O), and the API must not import worker code.
**Decision:** Add `packages/jobs` (`c2b_jobs`): environment settings, plain-psycopg repository, SQL migrations (advisory-lock protected), the procrastinate `App`, and the queue and task names. The API defers tasks by name (`configure_task`); the worker registers the implementations.
**Alternatives:** Put this in `packages/domain` (breaks its purity); in `apps/worker` and import from the API (couples two apps); duplicate it (drift).
**Consequences:** One more package than the layout in docs/10 §2. Migrations live in `packages/jobs/src/c2b_jobs/migrations/`.

### ADR-012 — cad-worker reached over a Unix socket, not the database (*Proposed*, P0)
**Context:** ADR-003 requires the cad-worker to have no network, but docs/02 shows it consuming the `cad` queue, which lives in Postgres. A parser for untrusted files should not hold database credentials or a route to Postgres.
**Decision:** The `worker` service consumes the `cad` queue and forwards each request to the cad-worker as one JSON line over a Unix socket on a shared volume. The cad-worker runs with `network_mode: none`, a read-only root filesystem, no capabilities, CPU 2 / RAM 3 GB / PIDs 256, input data mounted read-only. The 120 s per-file limit is enforced by running each extraction in a child process that is killed on timeout (compose cannot enforce wall time). One extraction at a time (procrastinate `lock`).
**Alternatives:** cad-worker joins an `internal: true` network with Postgres only (matches the diagram, but puts a database credential inside the sandbox); a TCP service on an internal network (still a network).
**Consequences:** The diagram edge `Q → CW` becomes `Q → worker → socket → CW`. The `worker` must be healthy-gated on the cad-worker socket. Real extraction output (GLB, features) must be written to a separate writable mount in P2.

### ADR-013 — Job link and access token (*Proposed*, P0)
**Context:** Jobs are reachable only by an unguessable link (docs/02 §6); there are no accounts.
**Decision:** `POST /jobs` returns `link = /j/<job_id>#<token>`. The token is 32 random bytes (urlsafe) shown once; only its SHA-256 is stored and compared in constant time. The token travels in the URL fragment (never sent to servers or logged) and in an `Authorization: Bearer` header. SSE is read with `fetch` streaming so the token never appears in a query string. Unknown job and wrong token give the same 401. The job id is a UUID4 used as a directory name, not as a secret.
**Alternatives:** Token in the path or query (leaks into logs and referrers); slow password hash (no benefit for 256-bit random tokens); cookie sessions (need CSRF handling and cannot be shared by link).
**Consequences:** Losing the link loses access; the UI says so. Rotating or revoking a token needs a later endpoint.

### ADR-014 — cad-worker writes results to a separate output tree (*Proposed*, P0–P6 review)
**Context:** ADR-012 mounts job data read-only into the cad-worker, but P2 made it write `model.glb`. Under the compose file the GLB export would have failed on every upload, and the API served the file from `jobs/<id>/out/`.
**Decision:** Input stays `data/` mounted read-only at `/data`. Results go to `data/out/<job id>/`, mounted read-write only into the cad-worker at `/out` (`C2B_CAD_OUT_DIR`). The API serves `data/out/<job id>/model.glb`. The one-shot `init` service creates and chowns `data/out`.
**Alternatives:** Mount all of `data/` read-write into the sandbox (weakens ADR-012); return the GLB bytes over the socket (large messages, extra copies).
**Consequences:** `e2e` checks that the input is read-only and `/out` is writable. A hostile file can still only write inside `/out`.

### ADR-015 — Confirmed column mapping kept per job; the LLM never confirms (*Proposed*, P0–P6 review)
**Context:** The mapping screen is reachable before a draft exists, and `POST /mapping/confirm` required a draft. Separately, the header mapper marked the B4 column "Item" as `extractor_L3` and `confirmed_by_user` by name, inside the rules engine, and `resolve_draft_with_ladder` defaulted to confirming L3 answers itself. Both broke CLAUDE.md rules 1, 5 and 6.
**Decision:** (1) The intake maps by data only: a column that loses a field to another gets its best remaining candidate from header patterns or value kinds, stays in `ambiguous_columns`, `by="rules"`, unconfirmed, and the engine raises a blocking `Question` with `mapping_column` set. (2) L3 may propose a field (`by="extractor_L3"`, unconfirmed); only a person confirms (`by="user"`). (3) The user's choices are saved as `data/jobs/<id>/mapping_overrides.json` (written by the API, read by the worker) and applied at intake, so they survive re-runs and work before the first draft. The golden `mapping.json` keeps the end state.
**Alternatives:** A database column (needs a migration and a re-run endpoint, no benefit yet); keeping the draft-only confirm (unusable from the upload screen).
**Consequences:** Approval stays blocked until the column is confirmed. A confirm that changes a field after the draft exists reports `rebuild_needed`; a re-run endpoint arrives with the review screen (P7).
