# 01 — Overview

## 1. Problem

A product owner has a 3D model, some photos, maybe a half-finished parts list, and needs a Bill of Materials good enough to buy parts, plan production and get quotes. CAD files are missing most of what a BoM needs: part numbers, materials, purchased items that were not modelled (adhesives, labels, cables, packaging), the difference between a design view and a build view, and consistent names. Building that BoM by hand takes days and is error-prone.

The app reads everything the user has, applies a large set of deterministic rules, asks precise questions where the inputs disagree or are silent, and produces a reviewed BoM in standard formats.

## 2. Users

| User | Needs |
|---|---|
| Product owner / founder | A complete BoM without BoM expertise; plain-language questions; a file to send to suppliers |
| Design or manufacturing engineer | Correct structure, standard designations, units, materials; every line explainable; fast editing |
| Purchasing | Buy lines grouped, standard parts named the way suppliers name them, quantities per unit and per build |
| Platform admin | Edit the library (classes, synonyms, standard-part tables, gap rules) without a code release |

## 3. Scope

**In scope (v1)**

- Discrete, assembled products: mechanical, electro-mechanical, electronics enclosures, appliances, furniture, tools, small machines.
- Inputs: STEP/STP assemblies (AP203, AP214, AP242); SolidWorks (SLDPRT/SLDASM) and Fusion (F3D) through a converter adapter; photos; BoM files (XLSX, CSV, PDF tables); datasheets (PDF); a free-text product description.
- Outputs: indented EBOM, MBOM, make/buy, open items, explanation per line; XLSX, CSV, JSON, PDF parts list.
- Standard-part recognition from geometry for common fasteners, washers, nuts, pins, bearings, O-rings and similar (doc 06 §4).

**Out of scope (v1)**

- Process industries (chemicals, food, pharma) and recipe/formula BoMs.
- ECAD parsing (Altium, KiCad, ODB++, IPC-2581). A PCB's own BoM is taken as a BoM file (CSV with reference designators). ECAD import is a later phase.
- Costing and supplier search (the platform's vendor stage does this). Unit cost columns are kept if the user supplies them.
- Drawings (2D DWG/PDF drawings as input are only read as text, not interpreted geometrically).
- PLM/ERP write-back. Export files only.

## 4. What "industry-standard BoM" means here

The app cannot promise a perfect BoM for every product; it promises a BoM that follows recognised conventions, is complete against explicit rules, and makes every remaining uncertainty visible. Concretely:

| Aspect | Convention used |
|---|---|
| Structure | Indented multi-level BoM following the CAD product structure (ISO 10303 AP242 assembly structure), with item/find numbers per parent and quantity per parent |
| Parts list fields | Find number, part number, revision, description, quantity, unit of measure, material, notes, in the spirit of ASME Y14.34 associated lists |
| Descriptions | Noun-first naming: `SCREW, CAP, SOCKET HEAD, M4 X 12, ISO 4762, A2-70` — noun, modifiers, size, standard, material/grade |
| Standard parts | Designated by their standard and size (ISO/DIN/IS/ANSI tables in the library) |
| Electronics | Reference designators kept and grouped per line; manufacturer part number (MPN) and manufacturer columns |
| Units | ISO 80000 / SI; `EA` for countable items, length/mass/volume units for bulk items |
| Two views | EBOM (as designed) and MBOM (as built: sub-assemblies in build order, consumables, packaging), linked line by line |
| Traceability | Every value has a source, a confidence and the rule ids that produced it |

Which standards and naming conventions apply is configurable per organisation (doc 06 §6).

## 5. Principles

1. **Rules first, LLM last** (ADR-001). Deterministic rules from the library produce the BoM; the LLM is a fallback for blind spots, never a source of numbers.
2. **Suggestions, not silent changes.** The user's data is never rewritten; additions are suggestions.
3. **Explain every line.** Source, confidence, rule ids, and the evidence (CAD node, file row, photo, datasheet page).
4. **Ask precisely.** A question only when inputs conflict or a required field is unknown and cannot be inferred; typed answers; batched by impact.
5. **Library as data.** Improving the app mostly means editing YAML (classes, synonyms, tables, rules), with tests.
6. **Reproducible.** Same inputs + same library version + same recorded LLM responses → same BoM.
