# 03 — Inputs and CAD extraction

## 1. Inputs accepted

| Input | Formats | Processor | What we take from it | Base confidence |
|---|---|---|---|---|
| CAD assembly | STEP/STP (AP203, AP214, AP242) | `cad-worker` (OCP/XCAF) | Product tree, instance counts and transforms, names, colours, layers, user properties (part number, material, description, mass if authored), solids and their geometry features | Geometry 0.95; authored names/properties 0.9 |
| Native CAD | SolidWorks SLDPRT/SLDASM, Fusion F3D | `native-adapter` (APS), then as above | See §2 | As converted |
| Existing BoM | XLSX, XLS, CSV, PDF tables | Header mapper (rules, then `extractor`) → normaliser | Lines, quantities, part numbers, descriptions, materials, MPNs, reference designators, levels | User lines 1.0 for what they state |
| PCB BoM | CSV/XLSX with reference designators | Same mapper with electronics header synonyms | Grouped refdes, MPN, manufacturer, value, footprint | 1.0 for stated fields |
| Datasheets | PDF | pdfplumber → rules (regex per spec type) → `extractor` fallback | Specs with units and page refs (material, rating, dimensions, mass) | 0.85 with page ref |
| Photos | JPG/PNG/WEBP/HEIC | `vision` role (opt-in) | Visible components, counts, markings, label text | ≤ 0.4 |
| Description | Free text in the wizard | Rules (keyword dictionary) → `extractor` fallback | Product type, variants, volume, known purchased items, materials | 0.7 |

## 2. Native CAD adapter (SolidWorks, Fusion)

There is no reliable free, open library that reads SolidWorks or Fusion files directly. The adapter uses Autodesk Platform Services (APS) Model Derivative, behind an interface so other converters (CAD Exchanger SDK, a SolidWorks add-in that exports STEP AP242, a customer-run converter) can be added later.

| Source | Path | What we get |
|---|---|---|
| Fusion F3D | APS translate F3D → **STEP**, then normal STEP extraction | Full tree and geometry |
| SolidWorks SLDASM/SLDPRT | APS translate → **SVF2 + OBJ** (APS does not list STEP output for SolidWorks); read the model **object tree and properties** from the SVF2 metadata (part names, configurations, custom properties such as part number, material, mass when authored), and geometry from OBJ meshes | Tree, names and properties at full confidence; geometry as meshes (mesh-based features only, lower confidence for recognisers) |
| Recommended for SolidWorks users | Ask the user to export **STEP AP242** (File → Save As) in addition, when available | Best geometry; the adapter's properties are merged by node name |

Rules for the adapter:

- Off unless `APS_CLIENT_ID/SECRET` are configured. The UI shows: "This file will be sent to Autodesk for conversion. Continue?" and records consent with the job.
- Files are deleted from the APS bucket after conversion (transient bucket policy) and from local storage per retention.
- APS usage limits and pricing change over time (Autodesk moved APS to Free and Paid tiers in 2025–2026); the adapter tracks usage per month and stops with a clear message at the configured cap. Check the current APS pricing page before enabling it in production.
- If conversion fails or is disabled: the job continues with the other inputs and asks the user for a STEP export.

**As built (P8, ADR-018).** `apps/native-adapter` talks to APS (2-legged token, transient bucket, signed upload, translation job, manifest polling, download, delete in a `finally`). F3D is one STEP AP242 job. SolidWorks is an SVF2 job followed by an OBJ job on its model guid (millimetres), plus the object tree and properties; it counts as 2 translation jobs against `C2B_APS_MONTHLY_CAP` (free tier only, default 20). The worker runs it on the `native` queue; output goes to `data/converted/<job>/`; OBJ meshes are read only in the cad-worker (`mesh_extract`). Property names are mapped by `library/cad_props.yaml`; a part number from CAD is kept as authored (`source=cad_meta`, rule `R-NUM-CAD`), a material text resolves to a library code through its designations, and meshes get a lowered confidence (`confidence.yaml: mesh`) and the rule `R-MSS-MESH` on their mass. A native upload needs consent first (`409 consent_required`), and with APS unconfigured it is refused with a clear message (`415 native_not_configured`) while the STEP path is unaffected. `C2B_APS_MODE=replay|record` replays or records the exchanges; golden B7 uses scripted ones.

Sources: APS Model Derivative supported translations (https://aps.autodesk.com/en/docs/model-derivative/v2/supported-translations), change history (SolidWorks 2025 to SVF/SVF2 and OBJ, https://aps.autodesk.com/en/docs/model-derivative/v2/change_history), APS business model (https://aps.autodesk.com/node/3776).

## 3. STEP extraction (cad-worker)

Per job the worker produces `cad_extract.json` (schema in doc 05) and `model.glb`.

**Tree and metadata (XCAF)**

- Load with `STEPCAFControl_Reader` (names, colours, layers, properties on), read `XCAFDoc_ShapeTool` labels: assemblies, components (instances), simple shapes.
- For each node: id (`N-0001`…), parent, label name, product name and description if present, instance count under parent, transform, colour, layer, and every user-defined property (AP242 property definitions such as `part_number`, `material`, `revision`, `mass`).
- Units from the file header; everything converted to mm and g.

**Geometry features per unique shape** (computed once per prototype shape, shared by its instances)

| Feature | Method | Used by |
|---|---|---|
| Volume, surface area, centre of mass, inertia | `BRepGProp` | Mass, symmetry |
| Axis-aligned and oriented bounding box (OBB) | `BRepBndLib` / OBB | Size rules, sheet thickness |
| Face-type histogram | Count and area of planar, cylindrical, conical, spherical, toroidal, B-spline faces | Recognisers (doc 04 §4) |
| Cylinder list | Radius, axis, length, inner/outer for each cylindrical face | Fasteners, pins, bearings, shafts |
| Helical/thread evidence | Helical B-spline faces, or cosmetic thread attributes in AP242, or "pitch-like" repeated conical faces | Threaded fasteners (many CAD libraries model threads as plain cylinders, so this is optional evidence) |
| Rotational symmetry | Fraction of area on surfaces of revolution sharing one axis | Turned parts, washers, bearings |
| Constant-thickness test | Paired parallel planar faces at a single distance covering most of the area; bend radii | Sheet metal |
| Wall-thickness sample | Ray casting at sampled points | Moulded parts, castings |
| Extrusion test | Constant cross-section along one axis | Profiles, extrusions |
| Thin-plate test | OBB smallest side ≤ 3 mm and large planar faces | PCBs, labels, gaskets |
| Solid count, shell closed, free edges | Topology | Quality notes (open shells, imported meshes) |
| Shape signature | Hash of rounded volume, area, sorted OBB, face histogram | Grouping identical parts with different names |

**Outputs to the viewer:** tessellated GLB (mm → m scale 0.001, node names = node ids) so the review table can highlight a line's instances in 3D.

**Quality checks on the file:** empty shapes, open shells, zero-volume solids, duplicate overlapping instances, unit mismatch (e.g. a 10 m screw), very large solid counts (> 5,000 leaves: warn, continue with sampling for features). Each check becomes a notice or a question.

## 4. BoM file intake

1. Detect table region (first row with ≥ 3 header-like cells; skip title blocks).
2. Map headers with the header-synonym dictionary (doc 06 §7): `Qty|Quantity|QTY.|Menge|Nos` → `quantity`, `P/N|Part No|Item code` → `part_number`, `Ref Des|Designator` → `refdes`, `Level|Lvl|Indent` → `level`, etc. Rules score each column (header match + value pattern match).
3. If a required column (`description` or `part_number`, and `quantity`) is unmapped or ambiguous → `extractor` proposes a mapping from the header row and five sample rows (fallback ladder step L3). The user confirms the mapping on screen before anything else uses it.
4. Normalise: trim, unify units (`pcs`, `nos`, `ea` → `EA`; `mtr` → `m`), part-number canonical form (configurable regex), levels from indentation or dotted find numbers (`1.2.3`), split grouped refdes (`R1-R4, R7`).
5. Every user line is kept verbatim in `raw` and never rewritten (only `normalised` fields are added).

## 5. Datasheets and photos

- **Datasheets:** regex extractors per spec family (dimensions, mass, material, voltage, current, power, IP rating, standard designations) with page references; `extractor` only when the regex set finds nothing for a field the BoM line needs.
- **Photos (opt-in):** `vision` returns a list of visible items with a count, a class candidate from the taxonomy (closed list), and any readable text (labels, part markings). Used only to raise or lower confidence, to propose suggestions (e.g. "a power cord is visible but not in the BoM"), and to answer count conflicts as a question. Never as a source of dimensions.
