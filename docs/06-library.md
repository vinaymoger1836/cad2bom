# 06 — Library

The library is the app's knowledge. It is YAML in `library/`, loaded into Postgres with a version (`YYYY.MM.patch`), validated by tests, and editable through the admin page. Jobs pin the version they ran with.

## 1. Layout

```
library/
  taxonomy.yaml          # class tree with nouns, defaults, flags
  synonyms/*.yaml        # name dictionary per language and domain
  materials.yaml         # material codes, families, densities, designations
  standards/             # standard-part tables
    iso4762.yaml  iso4017.yaml  iso4032.yaml  iso7089.yaml  din127.yaml
    iso2338.yaml  bearings_60xx_62xx_63xx.yaml  iso3601_orings.yaml ...
  rules/                 # classify.yaml, structure.yaml, gap.yaml, mbom.yaml, makebuy.yaml, validate.yaml
  archetypes/            # optional product-type skeletons (ceiling_fan.yaml, power_bank.yaml ...)
  headers.yaml           # BoM file header synonyms and value patterns
  units.yaml             # unit aliases → canonical units
  profiles/default.yaml  # numbering scheme, description style, default grades, export columns
  confidence.yaml        # source base confidences, thresholds, weights
```

## 2. Taxonomy (`taxonomy.yaml`)

A tree of class codes, three levels deep, each with a noun and defaults. Example:

```yaml
- code: fastener.screw.socket_head
  noun: SCREW
  modifiers: [CAP, SOCKET HEAD]
  uom: EA
  make_buy_default: buy
  standard_families: [ISO4762, DIN912, ASME_B18_3]
  material_default_profile_key: fastener_steel
  usually_modelled: true       # if missing from CAD, do not flag a user line
  description_template: "{noun}, {modifiers}, {size}, {standard}, {grade}"
- code: electrical.pcba
  noun: PCB ASSEMBLY
  make_buy_default: buy
  buy_as_assembly: true
  usually_modelled: true
- code: consumable.adhesive
  noun: ADHESIVE
  uom: ML
  usually_modelled: false
```

Initial coverage (v1): about 250 classes across fasteners, bearings and power transmission, seals, springs, sheet metal, machined, moulded, cast, extrusions, wood and panels, glass, electrical (cables, connectors, switches, motors, PSUs, PCBAs, components), fluid (hoses, fittings, valves), packaging, labels, documentation, consumables, purchased modules. An optional mapping to UNSPSC codes is kept per class for purchasing systems.

## 3. Materials (`materials.yaml`)

Code, family, display name, common designations (EN, IS, ASTM/SAE, JIS), density, typical uses, and finishes. Example: `steel_crca` (IS 513 CR, EN DC01, 7.85 g/cm³), `al_6061_t6`, `ss_304` (A2), `pc_abs`, `pa66_gf30`, `brass_cw614n`. Densities have sources.

## 4. Standard-part tables (`standards/`)

One file per standard: nominal sizes and the dimensions the recognisers measure, with tolerances and standard lengths. Example (ISO 4762):

```yaml
standard: ISO 4762
family: socket_head_cap_screw
tolerance_mm: { d: 0.15, dk: 0.25, k: 0.25 }
lengths_mm: [4, 5, 6, 8, 10, 12, 16, 20, 25, 30, 35, 40, 45, 50, 55, 60, 65, 70, 80, 90, 100]
sizes:
  - { size: M3,  d: 3,  dk: 5.5,  k: 3,  s: 2.5 }
  - { size: M4,  d: 4,  dk: 7,    k: 4,  s: 3 }
  - { size: M5,  d: 5,  dk: 8.5,  k: 5,  s: 4 }
  - { size: M6,  d: 6,  dk: 10,   k: 6,  s: 5 }
  - { size: M8,  d: 8,  dk: 13,   k: 8,  s: 6 }
source: "ISO 4762 nominal values; verify against the purchased edition"
```

Tables are entered from standards the organisation owns, or from public supplier catalogues, with the source recorded. v1 ships metric fasteners M2–M24, common washers and nuts, dowel pins, 60xx/62xx/63xx bearings and metric O-rings; inch series (ASME B18) in a later version.

## 5. Name dictionary (`synonyms/`)

Maps tokens and abbreviations to classes and nouns, per language: `shcs → fastener.screw.socket_head`, `brkt|bracket → sheetmetal.bracket`, `hsg|housing|enclosure → moulded.housing`, `pcb|pwb|board → electrical.pcb`, `pcba|board assy → electrical.pcba`. Includes vendor and CAD-library prefixes (McMaster, Misumi, Toolbox naming patterns), units and size patterns (`M4x12`, `#8-32`, `1/4-20`, `Ø6`). Hindi and German entries can be added per profile.

## 6. Organisation profile (`profiles/*.yaml`)

```yaml
numbering: { scheme: "{prefix}-{seq:05d}", prefixes: { fastener: FAS, sheetmetal: SHM, moulded: MLD, electrical: ELE, assembly: ASM }, revision_start: A }
descriptions: { case: upper, style: noun_first, separator: ", " }
find_numbers: { start: 10, step: 10, standard_parts_last: true }
defaults: { fastener_steel: "STEEL 8.8 ZINC PLATED", fastener_ss: "A2-70", sheet_metal: steel_crca, moulded: pc_abs }
export: { columns: [find_no, part_number, revision, description, qty, uom, material, finish, mass_g, make_buy, notes] }
standards_preference: [ISO, DIN, IS, ASME]
```

## 7. BoM header synonyms (`headers.yaml`)

Per target field: header patterns (multilingual), value patterns (e.g. quantity column is mostly positive numbers; refdes column matches `^[A-Z]{1,3}\d+`), and scoring weights.

## 8. Library tests (run by `c2b-library validate` and in the end-to-end check)

- Schema validation for every file; every class referenced by a rule exists; every material referenced exists; every standard table is monotonic and complete.
- Recogniser fixtures: synthetic CAD parts generated from each standard table (doc 10 §4) are recognised with the right size.
- Synonym collisions reported (one token → two classes without a context rule).
- Every rule id unique; every rule has a note.
