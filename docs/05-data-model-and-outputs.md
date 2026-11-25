# 05 — Data model and outputs

## 1. Core types (Pydantic v2, abbreviated)

```python
Source = Literal[
    "user",
    "bom_file",
    "cad_meta",
    "cad_geom",
    "std_table",
    "library",
    "datasheet",
    "vision",
    "llm",
    "engine",
]


class Prov(BaseModel, Generic[T]):
    value: T
    unit: str | None = None
    source: Source
    confidence: confloat(ge=0, le=1)
    rules: list[str] = []  # rule ids, e.g. ["R-STD-012","R-CLS-104"]
    ref: str | None = None  # "cad:N-0042", "bom:row 17", "pdf:att_03#p2", "photo:att_05"
    note: str | None = None


class Job(BaseModel):
    id: UUID
    share_token_hash: str
    created_at: datetime
    expires_at: datetime
    library_version: str
    profile: str  # organisation profile (numbering, defaults)
    product: ProductInfo  # name, type, description, variants, annual volume
    consent_native_conversion: bool = False
    status: Literal["uploading", "extracting", "building", "review", "approved", "failed"]


class CadNode(BaseModel):
    id: str
    parent: str | None
    kind: Literal["assembly", "part", "reference"]
    name: str
    product_name: str | None
    props: dict[str, str]
    shape_id: str | None
    instance_count: int
    transform: list[float]
    excluded: str | None = None  # reason when filtered


class ShapeFeatures(BaseModel):
    shape_id: str
    signature: str
    volume_mm3: float
    area_mm2: float
    obb_mm: tuple[float, float, float]
    face_hist: dict[str, float]
    cylinders: list[Cyl]
    rot_symmetry: float
    const_thickness_mm: float | None
    wall_sample_mm: list[float]
    thin_plate: bool
    helix_evidence: bool
    closed: bool


class Designation(BaseModel):
    standard: str
    family: str
    size: str
    length_mm: float | None
    score: float


class BomLine(BaseModel):
    id: str
    parent: str | None
    level: int
    find_no: int
    part_number: Prov[str] | None
    revision: str = "A"
    description: Prov[str]
    noun: str
    modifiers: list[str]
    cls: Prov[str]  # taxonomy code
    designation: Designation | None
    qty_per_parent: Prov[float]
    uom: str
    qty_total: float
    material: Prov[str] | None
    finish: Prov[str] | None
    mass_g: Prov[float] | None
    make_buy: Prov[Literal["make", "buy", "supplier_made"]] | None
    refdes: list[str] = []
    mpn: str | None = None
    manufacturer: str | None = None
    cad_nodes: list[str] = []
    shape_group: str | None = None
    origin: Literal["user_bom", "cad", "std_recogniser", "gap_rule", "skeleton", "llm", "engine"]
    review: Literal["given", "derived", "suggested", "accepted", "rejected"]
    phantom: bool = False
    buy_as_assembly: bool = False
    confidence: float
    open_items: list[str] = []
    raw: dict | None = None  # verbatim user row


class Question(BaseModel):
    id: str
    line_ids: list[str]
    kind: Literal["choice", "number", "text", "confirm"]
    text: str
    options: list[str] = []
    evidence: list[str]
    blocking: bool
    answer: Any | None


class BomVersion(BaseModel):
    job_id: UUID
    version: int
    ebom: list[BomLine]
    mbom: list[MbomLine]
    open_items: list[OpenItem]
    excluded: list[CadNode]
    stats: BomStats
    library_version: str
    llm_calls: int
    created_at: datetime
```

## 2. Storage

| Table | Content |
|---|---|
| `job` | Job metadata, status, pinned library version, profile, consent |
| `upload` | File metadata, type, size, hash, path under `data/jobs/<id>/` |
| `cad_extract` | JSONB: nodes, features, notices (one per upload) |
| `bom_draft` | Current draft (JSONB) + revision number |
| `bom_version` | Approved immutable versions |
| `question`, `suggestion` | Open and answered items with history |
| `edit_log` | Every user edit and answer (who, when, before/after) |
| `llm_call` | Role, model, tokens, latency, fallback_reason, line id, cassette key |
| `library_*` | Versioned library tables (doc 06) |
| procrastinate tables | Jobs and locks |

## 3. Outputs

| Output | Content |
|---|---|
| **BoM workbook (XLSX)** | Sheets: `EBOM` (indented, find no, part no, rev, description, qty, UoM, material, finish, mass, make/buy, class, standard, MPN, refdes, source, confidence), `MBOM` (build view with `ebom_refs`), `Purchased items` (buy lines rolled up by part number with total qty per build and per year if volume given), `Open items`, `Excluded from BoM`, `Explanations` (line → rule ids and evidence), `About` (library version, inputs, date) |
| CSV | Flat EBOM and MBOM (one row per line with `level` and `parent`) for ERP import |
| JSON | `BomVersion` as above; JSON Schema published |
| PDF parts list | Title block + indented list in parts-list layout; open items appendix; "Generated BoM — review before release" footer |
| GLB + viewer state | For sharing the reviewed model view (optional) |

Export columns and their order are configurable per organisation profile; the defaults follow the parts-list fields in doc 01 §4.

## 4. API (main endpoints)

| Method | Path | Purpose |
|---|---|---|
| POST | `/jobs` | Create job (product info) → link |
| POST | `/jobs/{id}/files` | Upload a file (raw body, `?filename=`), type-checked (built as `/files`, not `/uploads`) |
| POST | `/jobs/{id}/start` | Start the pipeline (built as `/start`, not `/run`; there is no re-run yet, ADR-017) |
| GET | `/jobs/{id}/events` | SSE progress |
| GET | `/jobs/{id}/bom` | Current draft with questions and suggestions |
| PATCH | `/jobs/{id}/lines/{line}` | User edit (becomes `source=user`) |
| POST | `/jobs/{id}/questions/{q}/answer` | Answer → partial re-run |
| POST | `/jobs/{id}/suggestions/{s}` | Accept or reject |
| POST | `/jobs/{id}/approve` | Validate and freeze a version |
| GET | `/jobs/{id}/versions/{v}/export?fmt=xlsx\|csv\|json\|schema\|pdf&part=ebom\|mbom` | Exports (`part` only for CSV) |
| GET | `/jobs/{id}/lines/{line}/history` | Edits, answers and decisions on a line, before and after |
| GET | `/jobs/{id}/excluded` | CAD bodies left out of the BoM, with the reason |
| POST | `/jobs/{id}/chat` | Agent chat (grounded in job data; proposals come back as edit suggestions) |
| GET | `/jobs/{id}/model.glb` | Viewer model |
| `/admin/library/*` | Browse, validate, publish library versions (protected) |
