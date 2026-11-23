"""BoM domain models (docs/05 §1). Pure Pydantic."""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from c2b_domain.prov import Prov, Source

MakeBuyType = Literal["make", "buy", "supplier_made"]
ReviewStatus = Literal["accepted", "edited", "derived", "flagged", "given", "suggested"]
QuestionKind = Literal["choice", "number", "text", "confirm"]
SuggestionReview = Literal["suggested", "accepted", "rejected"]


class Designation(BaseModel):
    """Standard part designation (ISO 4762 M4x12, 6201, etc.)."""

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    standard: str
    family: str
    size: str
    length_mm: float | None = None
    score: float = 1.0
    key: str | None = None


class Question(BaseModel):
    """A blocking or non-blocking question for the user (docs/05 §1)."""

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    id: str  # "Q-001", "Q-002", ...
    kind: QuestionKind = "choice"
    blocking: bool = True
    line_ids: list[str] = Field(default_factory=list)
    text: str
    options: list[str] = Field(default_factory=list)
    evidence: list[str] = Field(default_factory=list)
    answer: Any | None = None
    note: str | None = None


class Suggestion(BaseModel):
    """A proposed addition or change the user accepts or rejects (docs/05 §1)."""

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    id: str  # "S-001", "S-002", ...
    origin: str = "cad"  # "cad", "gap_rule", "skeleton", "photo", "user"
    review: SuggestionReview = "suggested"
    cad_name: str | None = None
    cls: str = Field(
        validation_alias="class",
        serialization_alias="class",
    )
    qty_total: float | None = None
    text: str
    rule: str | None = None
    level: str = "ebom"


class ColumnMapping(BaseModel):
    """Mapping for a single column in a BoM file."""

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    field: str
    by: str = "rules"  # "rules", "extractor_L3", "user"
    score: float = 1.0
    confirmed_by_user: bool = False
    why: str | None = None


class TableMapping(BaseModel):
    """Detected and confirmed table boundary and column mapping."""

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    header_row: int
    title_block_rows: list[int] = Field(default_factory=list)
    footer_rows: list[int] = Field(default_factory=list)
    columns: dict[str, ColumnMapping] = Field(default_factory=dict)
    ambiguous_columns: list[str] = Field(default_factory=list)
    assertions: list[str] = Field(default_factory=list)


class UserBomRow(BaseModel):
    """One row extracted and normalised from a user BoM file."""

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    row: int
    raw: dict[str, Any]
    part_number: str | None = None
    description: str | None = None
    qty: float = 1.0
    uom: str = "EA"
    uom_raw: str | None = None
    refdes: list[str] = Field(default_factory=list)
    refdes_count_matches_qty: bool = True
    manufacturer: str | None = None
    mpn: str | None = None
    material: str | None = None
    notes: str | None = None
    find_no: int | None = None
    level: int = 1
    origin: str = "user_bom"
    review: str = "given"
    class_acceptable: list[str] = Field(default_factory=list)


class DatasheetSpec(BaseModel):
    """Specification extracted from a datasheet PDF."""

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    spec_type: str  # "dimension", "mass", "material", "voltage", "current", "power", "ip_rating"
    value: Any
    unit: str | None = None
    raw_text: str
    page: int
    ref: str  # "pdf:filename.pdf#p2"
    confidence: float = 0.85


class PhotoFinding(BaseModel):
    """Visual item finding from product photos."""

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    item_description: str
    candidate_class: str | None = None
    count: int = 1
    detected_text: list[str] = Field(default_factory=list)
    confidence: float = 0.40
    ref: str = ""


class BomLine(BaseModel):
    """One line in the engineering Bill of Materials (indented EBOM)."""

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    id: str  # "L-0001", "L-0002", ...
    parent: str | None = None  # id of parent BomLine
    level: int = 1
    find_no: int = 10
    cad_name: str | None = None
    part_number: Prov[str] | str | None = None
    revision: str = "A"
    description: Prov[str] | str
    description_exact: bool = True
    noun: str
    modifiers: list[str] = Field(default_factory=list)
    cls: Prov[str] | str = Field(
        validation_alias="class",
        serialization_alias="class",
    )  # taxonomy code
    designation: Designation | None = None
    qty_per_parent: Prov[float] | float
    uom: str = "EA"
    uom_raw: str | None = None
    qty_total: float = 1.0
    material: Prov[str] | dict[str, Any] | str | None = None
    finish: Prov[str] | dict[str, Any] | str | None = None
    mass_g: Prov[float] | float | None = None
    make_buy: Prov[MakeBuyType] | MakeBuyType | None = None
    refdes: list[str] = Field(default_factory=list)
    refdes_count_matches_qty: bool = True
    mpn: str | None = None
    manufacturer: str | None = None
    cad_nodes: list[str] = Field(default_factory=list)
    class_acceptable: list[str] = Field(default_factory=list)
    origin: str = "cad"
    review: ReviewStatus = "derived"
    phantom: bool = False
    buy_as_assembly: bool = False
    cad_volume_mm3: float | None = None
    source: Source = "cad_meta"
    confidence: float = 1.0
    rule_ids: list[str] = Field(default_factory=list)
    open_items: list[str] = Field(default_factory=list)
    note: str | None = None
    explanation: list[str] = Field(default_factory=list)
    raw: dict[str, Any] | None = None


class BomDraft(BaseModel):
    """Draft BoM container for a job."""

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    job_id: str
    library_version: str
    profile: str = "profiles/default.yaml"
    ebom: list[BomLine] = Field(default_factory=list)
    mbom: list[dict[str, Any]] = Field(default_factory=list)
    stats: dict[str, Any] = Field(default_factory=dict)
    open_items: list[str] = Field(default_factory=list)
    questions: list[Question] = Field(default_factory=list)
    suggestions: list[Suggestion] = Field(default_factory=list)
    mapping: TableMapping | None = None
    user_rows_raw: list[dict[str, Any]] = Field(default_factory=list)


__all__ = [
    "BomDraft",
    "BomLine",
    "ColumnMapping",
    "DatasheetSpec",
    "Designation",
    "MakeBuyType",
    "PhotoFinding",
    "Question",
    "QuestionKind",
    "ReviewStatus",
    "Suggestion",
    "SuggestionReview",
    "TableMapping",
    "UserBomRow",
]
