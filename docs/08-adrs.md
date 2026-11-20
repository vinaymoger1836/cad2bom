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
