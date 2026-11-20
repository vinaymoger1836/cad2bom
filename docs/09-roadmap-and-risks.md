# 09 — Roadmap and risks

## 1. Phases

| Phase | Name | Outcome | Depends on |
|---|---|---|---|
| P0 | Foundations | Monorepo, Docker Compose (api, worker, cad-worker, postgres+pgvector, caddy), multi-arch images, web shell with themes, job + upload API, SSE, `make e2e` skeleton | — |
| P1 | Library and fixtures | Library YAML (taxonomy ~250 classes, materials, metric standard tables, synonyms, headers, units, profile, confidence), loader + validation CLI, synthetic CAD fixture generator, golden fixtures B1–B6 | P0 |
| P2 | CAD extraction | STEP XCAF tree + properties, geometry features, signatures, quality notices, GLB; native-adapter interface with a stub | P1 |
| P3 | Rules engine core | Node filter, grouping, standard-part recognisers, classification C1–C6, material/mass, structure → draft EBOM with explanations (no LLM) | P2 |
| P4 | Other inputs and merge | BoM file mapper + confirmation, PDF datasheets, photo step (vision, opt-in), merge and conflicts → questions | P3 |
| P5 | LLM ladder and agent | Router with roles and budgets, cassettes, ladder L1–L6, agent summaries, question wording, chat with suggestion proposals | P4 |
| P6 | Gaps, MBOM, make/buy, numbering, validation | Gap rules (universal, class, archetype), MBOM, make/buy, profile numbering and descriptions, validators, confidence and open items | P5 |
| P7 | Review UI and exports | Review screen (questions, suggestions, table, 3D, details, chat), approve, XLSX/CSV/JSON/PDF | P6 |
| P8 | Native adapter and hardening | APS adapter (F3D → STEP; SolidWorks → SVF2 properties + OBJ) with consent and caps; admin library page; retention, rate limits, backups, deploy | P7 |

## 2. Risks

| # | Risk | L | I | Mitigation |
|---|---|---|---|---|
| R1 | "Any product" is unbounded: unknown classes and product types | High | High | Taxonomy + generic geometry classes cover most parts; L1/L4 fallbacks; unknowns become questions; library grows from rejected/answered cases (admin queue from answers) |
| R2 | Poorly modelled CAD (simplified fasteners, merged bodies, meshes) | High | Medium | Quality notices; recogniser tolerance; questions; recommend AP242 export |
| R3 | Standard tables wrong or incomplete | Medium | High | Sources per table; fixture-based recogniser tests; designation shown with score |
| R4 | LLM misclassification | Medium | Medium | Closed enums; capped confidence (≤ 0.7); always in open items; user confirms |
| R5 | APS pricing or limits change | Medium | Medium | Adapter optional, capped, STEP path always available |
| R6 | Users treat the BoM as released engineering data | Medium | High | "Generated BoM — review before release" label; open items; approval by a named user |
| R7 | Large assemblies (10k+ parts) too slow | Medium | Medium | Features per unique shape only; sampling for very large files; worker time limits; progress per step |
| R8 | Free-tier LLM quotas | Medium | Low | Rules first; per-job budget; cassettes; skip step → question |
| R9 | IP sensitivity of CAD files | Medium | High | No LLM sees geometry files; native conversion only with consent; retention and delete-now button |

## 3. Open items

- Which organisation profile is the default (numbering scheme, description case, preferred standards) — owner, before P6.
- Inch fastener tables (ASME B18) — after v1 unless a pilot customer needs them.
- ECAD import scope and format priority — after v1.
- Whether to add a paid STEP converter for SolidWorks (CAD Exchanger SDK) if APS geometry (meshes) proves too weak for recognisers — decide after P8 trials.
- Admin queue: turn answered questions ("this part is a gasket") into proposed library entries — design in P8 or after.
