# CAD-to-BoM — design and hand-off package

A standalone app that turns a product's CAD (STEP, or native SolidWorks / Fusion through a converter), photos and any existing BoM file into a reviewed, **industry-standard Bill of Materials**: indented EBOM, MBOM, make/buy, part numbers, descriptions, materials, masses, standard-part designations, and an open-items list.

The core idea: **deterministic rules do almost all the work; the LLM is used only for reasoning when the rules are blind**, and an AI agent handles summaries and conversation with the user. Every line says where it came from and how sure the system is.

## Status

| Item | Value |
|---|---|
| Stage | Design complete, ready for Claude Code, phase P0 |
| Relationship | Standalone product, own repo and library. Later the Factory Layout Platform can call it as its S1–S2 service (same `Prov` and BoM line shape) |
| LLM | LiteLLM router, roles on Groq and Gemini free tiers; paid models by config later |
| CAD | STEP/STP AP203/AP214/AP242 natively; SolidWorks and Fusion through an optional Autodesk Platform Services adapter |
| Date | 2026-11-20 |

## Documents

| # | Doc | What it covers |
|---|---|---|
| 01 | [Overview](01-overview.md) | Problem, users, scope, what "industry-standard BoM" means here, principles |
| 02 | [Architecture and tech stack](02-architecture.md) | Components, pipeline, job flow, LLM roles, stack with reasons |
| 03 | [Inputs and CAD extraction](03-inputs-and-cad.md) | STEP via OpenCascade, native CAD adapter, BoM files, PDFs, photos |
| 04 | [BoM rules engine](04-bom-rules-engine.md) | The deterministic pipeline, standard-part recognition, classification, merge, gap rules, EBOM→MBOM, confidence, and the LLM fallback ladder |
| 05 | [Data model and outputs](05-data-model-and-outputs.md) | Pydantic models, provenance, BoM fields, exports |
| 06 | [Library](06-library.md) | Taxonomy, materials, standard-part tables, name dictionary, gap rules, numbering schemes |
| 07 | [User flow and UI](07-ui.md) | Screens, review table, questions, 3D viewer, chat |
| 08 | [ADRs](08-adrs.md) | Decisions with alternatives |
| 09 | [Roadmap and risks](09-roadmap-and-risks.md) | Phases, risks, open items |
| 10 | [Claude Code hand-off](10-claude-code-handoff.md) | Setup, repo layout, golden cases, phase prompts |
| — | [CLAUDE.md](CLAUDE.md) | Rules for Claude Code, copied to the repo root |

## How to start

1. Create an empty repo, copy this folder into `docs/`, copy `CLAUDE.md` to the repo root.
2. Open Claude Code in the repo and paste the **P0** prompt from doc 10.
3. Review the phase report, commit yourself, then go to the next phase.
