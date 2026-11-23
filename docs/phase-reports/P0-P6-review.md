# P0–P6 review and fixes

Date: 2026-11-23. Scope: re-check of everything claimed in the P0–P6 phase reports, then fix what did not hold up. Nothing was committed (owner rule 2).

## 1. What was found

| # | Finding | Severity | Fixed |
|---|---|---|---|
| 1 | `ruff check` reported 84 errors, `ruff format` 10 files; mypy failed in `packages/rules` (strict), `agents`, `apps/api` (11), `apps/worker` (18); ESLint 28 errors. P2–P4 and P6 reports said "clean". | High | Yes, all clean |
| 2 | **Worker could not start.** `apps/worker/.../tasks.py` imported `c2b_jobs.cad_client` and `c2b_worker.app`, neither of which exists. P3–P6 never ran through the worker. | Critical | Yes |
| 3 | **Every API error path raised `TypeError`** (500 instead of 404/409/422): `ProblemError` was called with 3 arguments, it needs 4 (`title`). 11 sites in `apps/api/.../jobs.py`. | High | Yes |
| 4 | cad-worker mounts `/data` read-only but wrote `model.glb` to `data/jobs/<id>/out/`: GLB export and the STEP extraction would have failed in Docker. | Critical | Yes, ADR-014 |
| 5 | `library/` was not in any image or mount: worker and API loaded `Path("library")` and would fail with "could not load library definitions". | Critical | Yes (copied into the images, `C2B_LIBRARY_DIR`) |
| 6 | `make e2e` had not been run since P0, and it still waited for the P0 `cad_stub`, which no longer exists. | High | Yes: extended with B3 and B5 |
| 7 | **Rules engine faked an LLM result.** `bom_intake.py` mapped the B4 column "Item" by name, labelled it `extractor_L3` and `confirmed_by_user=True`, and hard-coded B4 wording into `assertions`. `resolve_draft_with_ladder` defaulted to confirming L3 answers itself, and the worker used that default. Breaks CLAUDE.md rules 1, 5, 6. | High | Yes, ADR-015 |
| 8 | Worker never passed the job's description, annual volume or `assign_numbers` to `build_ebom`: the B3 blade-count question, volume bands and part numbering never happened in the real pipeline. | High | Yes |
| 9 | Approve froze the raw draft instead of a `BomVersion` and never set the job to `approved`. | Medium | Yes |
| 10 | `POST /mapping/confirm` required an existing draft but the screen is reachable before the job starts; the web client was typed with `any`. | Medium | Yes |
| 11 | `packages/agents` was missing from `make lint`. | Low | Yes |
| 12 | Gemini 1.5 defaults are retired. | Low | Yes: 2.5 (**verify**, configurable) |
| 13 | Report errors: P1 B1 "16 parts" (12 distinct, 33 instances), P2 B1 labels (M3x12 …; golden is M3×10, M4×12, M5×16, M6×20, M8×25, O-ring 20×2), P5 model names, library checksum changed after P1 without a version bump. | Low | Corrected in place |
| 14 | P6 prompt asked for the organisation-profile defaults before building; the report never listed them. | Medium | Listed in §3 |
| 15 | **Job creation returned 500 since P2**: `JobCreate` gained `consent_native_conversion`, `create_job` validated the whole body as `ProductInfo` (extras forbidden). New jobs also pinned `library_version="none"`. | Critical | Yes (pinned to `library/VERSION.yaml`) |
| 16 | **The real extractor never ran in Docker**: `c2b-domain` was not a declared dependency of `cad-worker`, so every STEP crashed the child process (`ModuleNotFoundError`). `apps/api` imported `c2b_rules`/`c2b_library` and `agents` imported `c2b_rules` without declaring them, and `uv.lock` lacked the `c2b-agents` edges. | Critical | Yes (declared, relocked) |
| 17 | **No draft could be saved**: `BomDraft.rejected_suggestion_keys` is a `set`, not JSON serialisable; the worker crashed in `save_bom` and the job stayed in `building` forever. | Critical | Yes (`mode="json"`, sorted output, and the worker now fails the job visibly on any unexpected error) |
| 18 | **Cassette matching was fitted to the tests.** `CassetteStore.find` had a "Fallback 2" matching by hard-coded golden-case keywords (`m4`+`stainless`, `item`, `body1`…, `identify missing components`). The B3/B4/B6 cassettes never matched by prompt hash; the P5 "replayed from cassettes" results came from that shortcut and would have returned B6 answers for any user's part named `Body3`. | High | Yes: fallback removed; cassettes re-recorded through the real flows (see §4.6) |
| 19 | L4 gap candidates ran only when the draft had no suggestions, which never holds (universal rules always suggest), and the worker never enabled L4. Archetype detection is not automatic. | Medium | L4 now runs for product text that matches no archetype, de-duplicated against rule suggestions; worker enables it |
| 20 | Generated GLBs were committed (`golden/*/out/`, `out/`): owner rule 1. Library-style `extract_step()` calls wrote into the repo. | Medium | GLB only when an output folder is given; `/out/`, `golden/**/out/`, `*.glb` ignored; strays removed from the working tree |
| 21 | `verify_p5` rewrote two tracked cassettes on every run. | Low | L6 cases use a temp cassette dir |

## 2. What was verified as correct

`c2b-library validate` (0 errors, 23 warnings), `verify_p2` … `verify_p6` all pass (volumes within 0.01%, B1–B3 golden lines, B4/B5 merge, B6 ladder, B3 gap suggestions and MBOM, blocked then successful approval for B5/B6, byte-identical B1–B6 output), `tsc` clean. The golden B1/B2 files are produced by the fixture generator, so those checks are not circular.

## 3. Organisation-profile values still at their defaults (P6 prompt)

All of `library/profiles/default.yaml` is a default awaiting the owner's confirmation:

- **Numbering:** `{prefix}-{seq:05d}`, revision starts at `A`, prefixes FAS, BRG, TRN, SEL, SPR, SHM, MCH, MLD, CST, EXT, WDP, GLS, ELE, FLD, PKG, LBL, DOC, CON, PUR, ASM, UNC, GEN.
- **Descriptions:** upper case, noun first, separator `, `.
- **Find numbers:** start 10, step 10, standard parts last.
- **Preferred standards:** ISO, DIN, IS, ASME.
- **Default grades/materials:** steel 8.8 zinc plated (fasteners), A2-70 (stainless), steel 200 HV zinc plated (washers), spring steel C75 phosphated (circlips), C45 (dowels, turned), 100Cr6 (bearings), NBR 70 (seals), CRCA zinc plated (sheet metal), 6061-T6 anodised clear (machined), PC/ABS (moulded), ADC12 (cast), 6063-T5 anodised clear (extrusion), FR4, soda-lime glass, birch plywood, corrugated board, flexible PVC.
- Every standards table (16/16), material (66/66) and archetype (2/2) still has `verify: true`.

## 4. Open decisions for the owner (not changed)

1. **L1 replaces a rule-made class.** docs/04 §9 says an L1 answer is "added as evidence with weight 0.5" and "can never alone exceed 0.7". The implementation replaces the rules' class with the LLM class when the line confidence is under 0.75. In B3 six parts (Canopy, Motor, Blade, Blade_Arm, LightKit, Shade) are under 0.75, so with live LLM keys their golden classes can change, and without keys (or a cassette) they become six extra blocking questions. The P7 acceptance for B3 ("answer the blade question, accept/reject suggestions, approve") needs one of: recorded L1 cassettes for B3, higher rule confidence for those names, or L1 as additional evidence only. This needs your call; it is not a small fix.
2. **Photos:** the vision step is a cassette-replay stub (no data leaves the server) and the worker passes `photo_opt_in=True`. Before a live vision provider is wired, a per-job consent flag is needed (same pattern as `consent_native_conversion`).
3. **Library version:** content changed after P1 without a version bump (a loaded version is meant to be immutable) but `2026.11.0` is recorded in every golden file. Not bumped here, because a bump means regenerating the golden JSON (rule 10: needs your explanation). Decide whether to bump to `2026.11.1` and regenerate.
4. **`.xls`** is accepted as a BoM file but `openpyxl` cannot read it.
5. **`CLAUDE.md` and `docs/10-…` are git-ignored** (your existing entries), so status edits to them live only in the working tree.
6. **The cassettes are scripted, not recorded from a model.** No API keys were available, so `tools/fixtures/generate_cassettes.py` now runs the real pipeline and chat code with only the provider call replaced by an authored answer; keys are the real role + prompt hashes, and each file says `model: "fixture/scripted"`. The 3,060-token total in P5 is therefore a figure for these scripted answers. Re-record with real keys (`C2B_CASSETTE_MODE=record`) when you have them, and check that the real L1 answers still fall in the B6 acceptable classes.
7. **Archetypes are never auto-applied.** `ceiling_fan` and `power_bank` skeletons only run when `archetype=` is passed; the worker passes none. Auto-applying them would add capacitor, switch and cable suggestions to B3 and break its golden (exactly 4 suggestions). Your call.
8. **Image sizes grew** now that the api and worker really contain the rules, agents and litellm: api 876 MB, worker 845 MB (arm64 300 / 256 MB, compressed content sizes differ), cad-worker 2.86 GB unchanged.

## 5. Changes (working tree only)

Backend: `apps/worker/.../tasks.py` (imports, product description/volume/numbering, overrides), `apps/api/.../jobs.py` (ProblemError, library path, GLB path, mapping confirm, approve), `apps/cad-worker/.../{server,extract}.py` (output tree), `packages/jobs/.../{settings,mapping_store}.py`, `packages/rules/.../{bom_intake,engine,dependency}.py`, `packages/agents/.../{pipeline,router,agent,config}.py`, `packages/domain/.../bom.py` (`Question.mapping_column`).
Infra: `infra/docker-compose.yml` (output mount, LLM key pass-through, `C2B_LIBRARY_DIR`), `apps/{api,worker}/Dockerfile` (library), `.env.example`, `Makefile`, `pyproject.toml` (ruff config, first-party list).
Web: `apps/web/src/lib/api.ts`, `apps/web/src/screens/MappingConfirm.tsx` (typed, all library header fields offered).
Checks: `scripts/e2e.py` (B3, B5, read-only input, writable output), `scripts/verify_p4.py`, `scripts/verify_p5.py` (production L3 path).
Docs: ADR-014, ADR-015, P1/P2/P5 corrections, this file.

## 6. Results after the fixes

| Check | Result |
|---|---|
| `make e2e` (`uv run python scripts/e2e.py`; `make` itself is not installed here) | **120 checks pass**, 407 s including a cold image build. Covers all P0 checks, **golden B3** (10 EBOM / 10 MBOM lines, the 4 golden suggestions, the blade-count question showing CAD 3 vs text 4, GLB served, approval blocked then succeeding after review), **B4** (14 user lines unchanged in `raw`, the ambiguous column is a blocking question, nothing confirmed without a person, confirm saves `source=user`), **B5** (3 suggestions, 1 quantity question, 0 LLM calls, approval blocked then succeeding), a read-only input mount and a writable output mount for the cad-worker |
| `ruff check` / `ruff format --check` | clean (104 files) |
| mypy (10 packages, strict in `domain`, `rules`) | clean |
| `tsc --noEmit`, ESLint (web) | clean |
| `verify_p2` … `verify_p6` | all pass with the re-recorded cassettes and no keyword matching |
| `c2b-library validate` | 0 errors, 23 warnings (unchanged) |
| Images smoke-imported | api, worker, cad-worker import all their modules; `library/` is present in the worker image |

Not verified: arm64 compose stack (images were not rebuilt for arm64 after these changes); live LLM calls (no keys); the web UI in a browser (keyboard, themes, reduced motion): still a manual check for you (docs/10 §7).

## 7. For you to commit

Your environment auto-committed part of this work during the session (`d202d7f`, under your identity); I never ran `git commit`. That commit includes generated GLB files. They are now ignored and deleted from the working tree, so the next commit should contain their removal. Also in the next commit: re-recorded cassettes (old ones deleted, 12 new files), `uv.lock`, the new ADRs.
