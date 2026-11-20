# 04 — BoM rules engine

The rules engine (`packages/rules`) turns extracted facts into BoM lines. It is deterministic, data-driven (rules and tables in `library/`) and explainable: every value records the rule ids that produced it. The LLM is called only through the fallback ladder in §9.

## 1. Stages and their contracts

| # | Stage | Input | Output | Deterministic? |
|---|---|---|---|---|
| 4.1 | Node filter | CAD tree | Nodes marked `part`, `assembly`, `reference` (excluded: construction geometry, skeletons, mates, hidden/suppressed, zero-volume, envelope/keep-out bodies) with reason | Yes |
| 4.2 | Shape grouping | Unique shapes + signatures | Groups of identical geometry (same part under different names or copies) | Yes |
| 4.3 | Standard-part recognition | Geometry features | Designation (e.g. `ISO 4762 M4×12`) with match score | Yes |
| 4.4 | Classification | Names, metadata, features, recogniser result | Class code from the taxonomy + score | Yes; LLM fallback L1 |
| 4.5 | Material and mass | Metadata, class defaults, datasheets, user | Material code + mass (volume × density, or authored mass) | Yes; question if unknown and needed |
| 4.6 | Structure | Tree + classes | Indented EBOM: levels, find numbers, quantity per parent, phantoms, purchased assemblies | Yes |
| 4.7 | Merge | EBOM draft + user BoM + photos + datasheets | Matched lines, conflicts → questions, unmatched user lines kept | Yes |
| 4.8 | Gap rules | Merged BoM + product type | Suggestions for non-modelled items | Yes; LLM fallback L4 for unknown product types |
| 4.9 | MBOM | Approved-for-review EBOM | Build-order grouping, consumables, packaging, phantoms collapsed | Yes |
| 4.10 | Make/buy | Classes, geometry, volume, user | Recommendation + reason | Yes |
| 4.11 | Numbering and descriptions | Lines | Part numbers (if none), noun-first descriptions | Yes; LLM fallback L5 for wording of unknown nouns |
| 4.12 | Validation and confidence | All lines | Errors, warnings, per-line confidence | Yes |

## 2. Rule format

Rules are YAML in `library/rules/*.yaml`, interpreted by the engine. A rule has an id, a stage, conditions over facts, an action, and a weight.

```yaml
- id: R-CLS-104
  stage: classify
  when:
    any:
      - name_matches: "(?i)\\b(screw|bolt|shcs|bhcs)\\b"
      - std_designation_family: [ISO4762, ISO7380, ISO4017, DIN912]
  then: { class: fastener.screw }
  weight: 0.6
  note: "Screw by name or recognised standard"

- id: R-GAP-021
  stage: gap
  when:
    all:
      - product_has_class: electrical.pcba
      - not_has_class: consumable.solder
      - process_hint_in: [hand_solder, wave_solder]
  then: { suggest: { class: consumable.solder, qty_rule: per_joint_estimate, level: mbom } }
  note: "Soldered PCBA connections need solder"
```

- Condition primitives (closed set, implemented in Python): `name_matches`, `meta_equals`, `feature_between`, `feature_ratio`, `std_designation_family`, `class_is`, `parent_class_is`, `product_has_class`, `not_has_class`, `count_between`, `material_in`, `process_hint_in`, `source_is`, `expr` (safe evaluator over numeric facts only).
- Actions: `class`, `material`, `designation`, `flag`, `question`, `suggest`, `exclude`, `group_as`, `make_buy`, `description_part`.
- Scores combine per stage by weighted evidence (§8). Conflicting actions above threshold → a question, not a guess.

## 3. Node filter (4.1)

Excluded with a reason shown in the "Excluded from BoM" list (the user can re-include):

- Nodes on layers or with names matching reference patterns (`skeleton`, `envelope`, `ref`, `datum`, `sketch`, `keep_out`, `clearance`).
- Bodies with zero or near-zero volume (surface bodies), unless thin-plate rules say it is a label or film.
- Hidden or suppressed components when the format flags them.
- Duplicated instances at the same transform (modelling errors) → one kept, a warning raised.

## 4. Standard-part recognition (4.3)

Many CAD models use catalogue parts with poor names (`Part12`). Recognisers measure geometry and look the result up in standard tables (doc 06 §4).

| Recogniser | Evidence | Lookup |
|---|---|---|
| Socket head cap screw | Cylinder shank Ø d + cylindrical head Ø dk, head height k, hex socket (6 planar faces in a recess) | ISO 4762 / DIN 912 table: match d, dk, k within tolerance; length from shank |
| Hex bolt / screw | Hex prism head (6 planar faces, across-flats s) + shank | ISO 4014/4017, ASME B18.2.1 |
| Button / pan / countersunk heads | Spherical/conical head shapes | ISO 7380, ISO 7045, ISO 10642 |
| Nut | Hex prism with through bore, height ≈ 0.8 d | ISO 4032/4035, DIN 934 |
| Washer | Thin annulus: OD, ID, thickness | ISO 7089/7090/7092/7093, DIN 125 |
| Spring/lock washer, circlip | Split annulus / tabbed ring | DIN 127, DIN 471/472 |
| Dowel / pin | Plain cylinder, L/D within range, chamfers | ISO 2338, ISO 8734 |
| Ball bearing | Two concentric rings (inner bore d, outer D, width B) with toroidal raceway or simplified block | Bearing series table (6000/6200/6300…) |
| O-ring | Torus: ID and cross-section | ISO 3601 / AS568 |
| Rivet, set screw, threaded insert | Shape rules per family | Tables per family |

Rules:

- Tolerance per dimension in the table (e.g. ±0.15 mm for head diameters of modelled screws). Match score = share of dimensions within tolerance, weighted.
- Thread evidence is optional (many libraries model plain cylinders). Without it, a nominal diameter is snapped to the nearest standard size only if within tolerance; otherwise the line is a "screw-like part, size unknown" with a question.
- Length is the shank length measured along the axis, snapped to the table's standard lengths within ±0.3 mm.
- Material/grade of standard parts is never inferred from geometry: default from the organisation profile (e.g. `steel 8.8 zinc plated`, `A2-70`), marked `library` with confidence 0.5 and surfaced as an open item.

## 5. Classification ladder (4.4)

For each part group, evidence is collected in this order and scored (§8). The first step that reaches the acceptance threshold (default 0.75) wins; otherwise the next step adds evidence.

| Step | Evidence | Typical weight |
|---|---|---|
| C1 | User BoM line already matched to this part with a class or clear description | 1.0 |
| C2 | Authored metadata in CAD (part number found in the library's known parts, description, material) | 0.9 |
| C3 | Standard-part recogniser match | 0.85–0.95 by score |
| C4 | Name dictionary (synonyms, abbreviations, multilingual, vendor prefixes: `SHCS`, `PCB`, `PCBA`, `HSG`, `BRKT`) | 0.5–0.8 |
| C5 | Geometry class rules: sheet metal (constant thickness + bends), turned (rotational symmetry > 0.8), moulded (thin uniform wall, drafts), cast (thick variable wall, fillets), extrusion (constant section), PCB (thin plate 0.8–3.2 mm with many small solids on it), cable/hose (swept tube), spring (helix), label/film (≤ 0.3 mm plate), glass/panel (thin plate, transparent colour) | 0.4–0.7 |
| C6 | Context: parent assembly class, sibling classes, product type (e.g. inside a motor assembly → laminations, magnets, windings) | 0.1–0.3 boost |
| L1 | **LLM fallback** (`reasoner`) if still below threshold or two classes within 0.1 of each other | see §9 |

The result is a class code from the taxonomy (doc 06 §2), never free text. Below threshold after L1 → line kept as `unclassified` with a question to the user (multiple choice of the top candidates plus "other").

## 6. Material and mass (4.5)

1. Material priority: user > CAD property > datasheet > user BoM file > class default for the organisation profile (e.g. `sheet metal → CRCA steel`, `moulded → PC/ABS`) > unknown.
2. Mass priority: user > authored mass in CAD > volume × density (material from step 1) > datasheet > unknown. Purchased standard parts take mass from the standard table when available.
3. If material is a class default, the line gets an open item ("material assumed: CRCA steel"); if the material is needed for a downstream rule (make/buy, MBOM consumables) and unknown, a question.
4. Total product mass = roll-up of lines; compared with the CAD's total solid volume × densities and, if given, the user's stated product mass (5% tolerance) → warning if off.

## 7. Structure, merge and gaps

### 7.1 Structure (4.6)

- Levels follow the CAD tree. Quantity per parent = instance count of that child under that parent; total quantity = product of quantities up the tree.
- **Phantoms:** assemblies that only group parts for modelling (single child, or name/layer rules such as `_grp`, `subasm_for_layout`) are flagged phantom; the user can confirm. Phantoms are kept in the EBOM view and collapsed in the MBOM.
- **Purchased assemblies:** an assembly whose class is a purchased item (motor, fan module, PSU, gearbox, PCBA bought complete) becomes **one** buy line; its children are moved to a "contents of purchased assembly" note (not separate lines). Rule-based by class flag `buy_as_assembly`.
- **Find numbers:** sequential per parent (10, 20, 30… configurable) in tree order, standard parts after custom parts (configurable).
- **Identical parts under different names** (same signature) → one part number with a question if names disagree materially.

### 7.2 Merge with user BoM and other sources (4.7)

Matching order for each user BoM line against CAD part groups:

1. Part number exact (canonical form).
2. MPN or standard designation exact.
3. Description similarity (token-set ratio ≥ 0.85 after normalising nouns and units) **and** compatible class **and** quantity compatible.
4. Description similarity ≥ 0.7 and geometric size hint in the description (e.g. `M4x12`, `500x120`) within 5% of the CAD dimension.

Outcomes:

| Situation | Result |
|---|---|
| Matched, values agree (5% tolerance) | One line, `origin=user_bom`, CAD evidence attached, confidence raised |
| Matched, values disagree (qty, material, size) | Line keeps the user's value; question showing both values and sources |
| User line with no CAD match | Kept. Not flagged when its class is commonly not modelled (electronics components, consumables, labels, packaging, cables, fasteners in simplified models); otherwise an information note |
| CAD part with no user line | Suggestion: "CAD shows X (2×). Add to the BoM?" |
| Photo shows an item not in BoM or CAD | Suggestion with photo evidence, low confidence |

### 7.3 Gap rules (4.8)

Gap rules add **suggestions** for things CAD rarely shows. Three sources, in order:

1. **Universal rules** (all products): labels (rating/serial/compliance), packaging (unit box, protective inserts, shipper carton), user documentation, consumables per joining method found (threadlocker if threaded joints and the profile says so, adhesive for bonded parts, solder for PCBA hand work).
2. **Class-triggered rules:** e.g. `electrical.mains_powered` → power cord, strain relief, earth label; `electrical.pcba` → standoffs or screws if no fastener found near the PCB; `fluid.hose` → clamps; `motor` → mounting fasteners; `glass.panel` → gasket/seal; `battery.li_ion` → insulation, transport label.
3. **Product-type skeletons** (optional): when the product type matches a known archetype in the library, its skeleton lists expected classes; missing classes → suggestions.

For a product type not in the library, the ladder step L4 (§9) asks the `reasoner` for candidate classes **from the taxonomy only**, each with a one-line reason; they are shown as low-confidence suggestions. Rejected suggestions are remembered for the job and not proposed again.

## 8. Confidence and scoring

- Each piece of evidence contributes `weight × source_base_confidence` to a candidate value. The candidate's score is `1 − Π(1 − contribution)` (noisy-OR), capped by the strongest source's base.
- Acceptance thresholds (library-configurable): class 0.75, material 0.7, designation 0.8.
- Line confidence = min over required fields (class, quantity, description; material if needed). Shown as High (≥ 0.85), Medium (0.6–0.85), Low (< 0.6).
- Open items list = every line below High, every assumed default, every LLM-sourced value, every unresolved suggestion.

## 9. LLM fallback ladder

The LLM is called only at these points, each with a recorded `fallback_reason`, a structured-output schema with closed enums, and a per-job budget. If the LLM is unavailable, the step is skipped and the case becomes a question.

| Step | When | Role | Input (compact) | Output (schema) | What happens to it |
|---|---|---|---|---|---|
| L1 Classify unknown part | Classification below threshold after C1–C6, or two classes tie | `reasoner` | Part name, parent names, sibling classes, key features (size, symmetry, thickness, face mix), product description; top-5 candidate classes from rules | `{class: enum(taxonomy), confidence: 0–1, reason}` | Added as evidence with weight 0.5 (`source=llm`); can never alone exceed 0.7 → line stays Medium/Low and appears in open items |
| L2 Material family | Material needed, unknown, class default ambiguous | `reasoner` | Class, name, colour, photo findings, description | `{material_family: enum, reason}` | Suggestion only; user confirms |
| L3 BoM header mapping | Required column unmapped or ambiguous after rules | `extractor` | Header row + 5 sample rows | `{column_index → field enum}` | Shown to the user for confirmation before use |
| L4 Gap candidates for unknown product type | No archetype match and universal/class rules done | `reasoner` | Product description, current class list | `[{class: enum, reason}]` (max 8) | Low-confidence suggestions |
| L5 Description wording | Noun or modifier missing from the dictionary for a classified line | `agent` | Class, designation, key dims, material | `{noun, modifiers[]}` from allowed vocab where possible | Wording only; numbers inserted by the engine |
| L6 Datasheet field | Regex extractors found nothing for a needed field | `extractor` | Page text snippet | `{field, value, unit, page}` with quote | Accepted only if the quoted text contains the value |

Not allowed: LLM-generated quantities, dimensions, masses or costs; LLM-created part numbers; any LLM value applied without a visible source label.

## 10. MBOM, make/buy, numbering (4.9–4.11)

**MBOM rules**

- Collapse phantoms; keep purchased assemblies as single lines.
- Group by build sequence: sub-assemblies that are built separately (rule: assemblies with class `module` or user-marked, or where the CAD sub-assembly has ≥ 3 parts and is joined to the rest by ≤ 2 fasteners) become MBOM sub-assemblies.
- Add consumables from joining-method rules (adhesive, threadlocker, solder, flux, grease, cable ties) with per-unit quantity rules from the library; add packaging levels.
- Each MBOM line links to its EBOM line(s) (`ebom_refs`).

**Make/buy recommendation** (user decides):

- Class defaults (standard parts, electronics components, motors, PSUs → buy).
- Custom geometry classes (sheet metal, machined, moulded, cast) → `make or buy (supplier-made to drawing)`; the recommendation uses annual volume bands from the library (e.g. moulded parts: buy below a tooling-justifying volume) and is phrased as "made to your drawing by a supplier" vs "made in-house".
- Reason text lists the rule ids.

**Numbering and descriptions**

- Keep user and CAD part numbers. Assign new numbers only to lines without one, using the organisation scheme (doc 06 §6), e.g. `{class_prefix}-{seq:05d}-{rev}` → `FAS-00012-A`. Standard parts can use a designation-based key so the same screw gets the same number across jobs.
- Descriptions are built from the class noun + modifiers + size + standard + material/grade using the template of the class (doc 06 §5), upper-case noun-first by default, configurable.

## 11. Validation (4.12)

Blocking (approval disabled): unresolved questions; open suggestions; a quantity ≤ 0 or missing; a unit not in the unit list; a cycle in the structure; a CAD leaf neither mapped to a line nor excluded with a reason; duplicate part numbers with different descriptions.

Warnings: mass roll-up mismatch; class defaults used for material; standard-part grade from profile default; very low confidence lines; user lines without CAD match in classes usually modelled.

## 12. Re-runs and edits

- Answers and edits create a new draft revision; only affected lines and their dependants are recomputed (dependency index: line → evidence → rule).
- User values are `source=user` and are never overwritten. Rejected suggestions stay rejected.
- The library version is pinned per job; upgrading the library is an explicit action that re-runs and shows a diff.
