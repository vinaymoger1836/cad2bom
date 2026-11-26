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

### ADR-016 — L1 adds evidence to a rule-made class; it replaces only a geometry guess (*Proposed*, P7)
**Context:** `resolve_draft_with_ladder` ran L1 on every CAD line under the class threshold (0.75) and replaced the rules' class with the LLM class (docs/04 §9 says L1 is evidence at weight 0.5, never above 0.7). On golden B3 six lines (Canopy 0.50, Blade 0.54, Shade 0.54, Blade_Arm 0.63, LightKit 0.63, Motor 0.68) have a class from the name dictionary. With no cassette or key each became an extra blocking `Q-CLS-*` question whose answer changed nothing; with a live key their golden classes could change. Golden B6 still needs L1 to classify bodies whose only evidence is geometry (`Body1`…`Body9`).
**Decision:** The class source decides. When the class came from names, metadata, standards or the user's file (ladder steps C1–C4; the line's `source` is not `cad_geom`), L1 is evidence only: the class, description, origin and confidence are never touched. If L1 agrees, or disagrees, the line gets a non-blocking open item saying so; if L1 is unavailable the line gets a non-blocking open item and no question. When the class is a geometry-only guess (C5) or there is none, L1 works as before: its answer replaces the guess, is labelled `source=llm`, capped at 0.70, and an unavailable L1 still raises a blocking question.
**Alternatives:** Recorded L1 cassettes for B3 (scripted answers, would also need mounting into the worker, real keys could still change classes); raising rule confidence for those names (hides the problem and edits the library); evidence-only for every line (breaks the B6 contract).
**Consequences:** B3 has exactly one blocking question (the blade count). Golden files are unchanged. A person can still change any class by editing the line. The LLM never overrides a name-derived class on its own.

### ADR-017 — Exports package, line history table and approval semantics (*Proposed*, P7)
**Context:** P7 needs XLSX, CSV, JSON, a JSON schema and a PDF from a frozen version, a per-line history, and a clear rule for what approving twice or editing after approval does. None existed: edits only logged `"Edited L-0003.material"`, approving always minted a new version, and edits were allowed on an approved job without any signal.
**Decision:**
1. **Exports** live in `packages/exports` (`c2b_exports`). One flat view (`flatten.py`) unwraps `Prov | plain` once, so every format reads the same rows. XLSX uses openpyxl (already present), the PDF uses WeasyPrint (BSD-3) from an escaped HTML template with the label in the `@page` footer. Column order comes from the organisation profile (`export:` in `profiles/default.yaml`). The API serves `GET /jobs/{id}/versions/{v}/export?fmt=xlsx|csv|json|schema|pdf[&part=ebom|mbom]`. `apps/api/Dockerfile` installs `libpango-1.0-0`, `libpangoft2-1.0-0`, `libharfbuzz0b` and `fonts-dejavu-core` (dynamically linked, LGPL, recorded in `docs/versions.md`).
2. **`edit_log`** (migration 007): one row per edit, answer and suggestion decision with before, after, actor and time. `GET /jobs/{id}/lines/{line}/history` returns a line's rows.
3. **Approval:** `BomVersion` also carries `product`, `excluded`, `questions`, `suggestions` and `validation`, so the JSON export and its schema are complete. Approving a draft equal to the latest version returns that version (`unchanged: true`). Any edit, answer or suggestion decision on an `approved` job sets the status back to `review` ("new draft since vN"); frozen versions are never touched.
4. **Edits** accept only a fixed field list. Quantity edits and answers now carry `source=user` provenance like every other edit.
**Alternatives:** Generate exports in the browser (the PDF and the JSON would not be the server's version); reuse `job_event.data` for history (no index per line, diverges from docs/05 §2); block edits after approval (forces a "reopen" step for no benefit).
**Consequences:** One more workspace package and an apt layer in the API image (the image grows). PDF export needs the system libraries, so a non-container run without pango answers 503 with a plain message. A re-run endpoint (which must keep user edits) is still open; the Re-run button of docs/07 §2 is not built.

### ADR-018 — APS adapter contract, consent, usage cap and recorded responses (*Proposed*, P8)
**Context:** ADR-004 allows an optional APS adapter. The stub returned one STEP path, but APS gives no STEP for SolidWorks (SLDPRT/SLDASM → F3D, OBJ, SVF, SVF2 only; F3D → STEP). The Model Derivative free tier is "rated": past its monthly allowance usage is suspended unless the account opts into the paid tier. The allowance is only in an image on Autodesk's page, so it could not be read. The cad-worker has no network (ADR-012), so the HTTP client must live elsewhere.
**Decision:**
1. **Contract.** `ConversionResult(status ok|not_configured|cap_reached|error, step_path, obj_paths, properties, message, usage_jobs, notes)`. The adapter (`apps/native-adapter`) never raises for service errors and holds no database code; the worker passes a `UsageGate` (`reserve`, `refund`). Fusion `.f3d` → one STEP AP242 job. SolidWorks → an SVF2 job, then an OBJ job on its model guid (units set to millimetres), plus the object tree and properties.
2. **Where it runs.** A `native` queue with `lock="aps"` (one conversion at a time) in the `worker`. Output goes to `data/converted/<job>/`, the one new writable mount of the worker; the cad-worker reads it through its existing read-only `/data`. A converted STEP is stored as a `cad` upload with `origin=converted`. SolidWorks meshes are parsed only in the cad-worker (`mesh_extract`, numpy only), like STEP.
3. **Consent.** Native uploads are accepted only when the adapter is configured and the job has recorded consent (`409 consent_required` otherwise; nothing is stored). The UI shows the exact wording served by `/config` (`CONSENT_TEXT`); the job stores the time and the wording version. The worker checks consent again before calling Autodesk.
4. **Cap.** Free tier only. `C2B_APS_MONTHLY_CAP` (default 20 translation jobs, SolidWorks = 2) is checked and incremented atomically (`aps_usage`); a refused or failed-before-upload conversion counts nothing. The adapter never opts into a paid tier. A conversion past the cap fails with a message that says what to do (export STEP).
5. **Cleanup.** The Autodesk object and manifest are deleted in a `finally`; the bucket is `transient` (24 h) as a backstop.
6. **Recorded responses.** `C2B_APS_MODE=live|replay|record`. Replay and record are httpx transports keyed by `METHOD path` (+ output types of a translation job); record redacts tokens and signed-URL query strings. B7 uses scripted fixtures (see the P8 report).
7. **Properties.** Names are mapped by `library/cad_props.yaml` (data, CLAUDE.md rule 6); material text resolves to a library code through its designations; nothing is guessed. Mesh-measured shapes carry `mesh_based` and a lowered confidence (`confidence.yaml: mesh.features_factor`); a mass from a mesh gets `R-MSS-MESH`.
**Alternatives:** A paid converter (CAD Exchanger, HOOPS) behind the same interface later; calling APS from the API process (holds credentials next to the web tier, blocks on polling); a Fusion-only adapter (leaves SolidWorks with no path).
**Consequences:** Files leave the server only on consent and only for the time of a conversion. The signed-S3 upload step and SolidWorks properties through SVF2 are not confirmed against a real Autodesk account (see the P8 report). `httpx` becomes a runtime dependency of the worker.

### ADR-019 — Versioned library store and real per-job pinning (*Proposed*, P8)
**Context:** `job.library_version` was a label. The API and the worker read whatever `library/` the image contained, so an admin edit would have changed existing jobs.
**Decision:** The store lives under `data/library/`: `versions/<v>/` (immutable snapshots), `drafts/<id>/` (editable copies), and `active` (one line). The API seeds the image's library as the first version at start-up. A job is pinned to `active` at creation and every later read goes through `c2b_library.access.pinned_library(job.library_version)` (API endpoints, worker, exports). The worker reads the store through its existing read-only `/data` mount. Publishing validates (`load_library` + `run_checks`), writes the next `YYYY.MM.n`, switches `active` atomically and records the version in the database (`library_version`, for search). Drafts can only change existing files; `VERSION.yaml` is written at publish. A job whose version is not in the store (made before P8, when the label was not enforced) falls back to the image's library with a warning.
**Alternatives:** Loading versions from the `library_*` tables (the engine reads files, and the tables hold derived rows only); a Docker volume for the library (a second place to back up); git-based versions (the app has no git).
**Consequences:** Re-running a job never changes its version; upgrading a job's library stays an explicit action (still to be built with the re-run endpoint, ADR-017). `data/library/` is part of the backup. The baseline became 2026.11.1 because P8 added library data (`cad_props.yaml`, the `mesh` factor); that changed the version string in the golden files and nothing else.

### ADR-020 — Admin access: reverse-proxy login plus a passcode session (*Proposed*, P8)
**Context:** docs/10 asks for the admin page to be "protected by passcode + reverse-proxy auth". There are no accounts (ADR-013).
**Decision:** Two layers. Caddy requires HTTP basic auth (`C2B_ADMIN_BASIC_USER`, bcrypt `C2B_ADMIN_BASIC_HASH`) on `/admin*` and `/api/admin/*`; with no hash configured a throw-away default keeps the paths locked. Behind it, `POST /admin/login` compares the passcode (`C2B_ADMIN_PASSCODE`) in constant time and returns an HMAC-signed token that expires after `C2B_ADMIN_SESSION_S` (1 h). It travels in `X-Admin-Token`, because the browser already uses `Authorization` for the basic-auth credentials. With no passcode set the admin API answers 404. Logins are rate limited (ADR-022) and written to `admin_audit` with publishes and draft changes.
**Alternatives:** Passcode only (one secret, easier to brute-force from anywhere); an identity provider (not worth it for one or two administrators).
**Consequences:** `scripts/deploy.sh` generates both secrets and prints them once. Rotating the passcode needs a restart. The audit log has no viewer yet.

### ADR-021 — Retention and delete-now (*Proposed*, P8)
**Context:** docs/02 §6 promises 30-day retention and delete-now; neither existed.
**Decision:** `job.expires_at` is set at creation (`C2B_RETENTION_DAYS`, default 30) and extended on approval. The API runs a sweep at start-up and then every `C2B_RETENTION_SWEEP_S` (default 1 h) under an advisory lock. `delete_job_everywhere` removes `data/jobs/<id>`, `data/out/<id>`, `data/converted/<id>` and then the row (dependants cascade). Files first: a failure leaves the row so the next sweep retries. `DELETE /jobs/{id}` (job token) uses the same function; it is refused while the job is running. The upload and review screens have a confirm dialog. Backups are not rewritten when a job is deleted; they rotate out with `C2B_BACKUP_KEEP`.
**Alternatives:** A cron outside the app (a second thing to deploy); soft delete (keeps the very data the person asked to remove).
**Consequences:** Deleted jobs reappear if an old backup is restored; the restore script and the report say so.

### ADR-022 — Rate limits, backups, monitoring and deployment (*Proposed*, P8)
**Context:** The stack had no HTTP rate limits, backups, metrics or deploy path, and `/docs` was public.
**Decision:**
1. **Rate limits:** an ASGI middleware with per-IP token buckets for job creation, uploads, admin login and everything else, using the right-most `X-Forwarded-For` entry (the one Caddy adds; client values are never trusted). 429 with `Retry-After`. In memory, which is correct for the single API replica this stack runs.
2. **Backups:** `scripts/backup.sh` (`pg_dump`, `data/jobs`, `data/library`; `.env` excluded; rotation `C2B_BACKUP_KEEP`), `scripts/restore.sh`, and a nightly systemd timer.
3. **Monitoring:** deeper `/readyz` (database, free disk, library store); Prometheus text at `/admin/metrics` (jobs by status, queue depth, Autodesk usage and cap, disk, retention and rate-limit counters) for the admin session or a metrics token; a worker healthcheck on the queue heartbeat; `scripts/healthcheck.sh` with an optional alert webhook on a 5-minute timer; JSON access logs in Caddy. No Prometheus or Grafana is installed.
4. **Deploy:** `infra/docker-compose.prod.yml` + `infra/Caddyfile.prod` (domain, automatic HTTPS, HSTS, log rotation, `C2B_ENV=production` hides `/docs` and `/openapi.json`) and `scripts/deploy.sh` for a fresh Ubuntu/Debian x86_64 or arm64 VM.
**Alternatives:** A Caddy rate-limit plugin (needs a custom image); Redis for shared limits (a new service for one replica); a full monitoring stack (too heavy for a small VM).
**Consequences:** A second API replica needs a shared limiter. The deploy script is checked for syntax only; it has not been run on a real VM. The arm64 image of the new dependencies has not been built.
