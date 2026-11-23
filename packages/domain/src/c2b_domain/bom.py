"""BoM domain models (docs/05 §1). Pure Pydantic."""

from datetime import UTC, datetime
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
    cls: str | None = Field(
        default=None,
        validation_alias="class",
        serialization_alias="class",
    )
    qty_total: float | None = None
    text: str = ""
    rule: str | None = None
    level: str = "ebom"

    # Fields for modification proposals (e.g. chat agent, material ladder):
    line_id: str | None = None
    action: str = "add"  # "add", "modify", "remove"
    field: str | None = None  # e.g. "material", "quantity"
    value: Any | None = None
    confidence: float = 0.50
    rule_ids: list[str] = Field(default_factory=list)
    reason: str | None = None
    status: str = "suggested"


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


class LlmCallRecord(BaseModel):
    """Audited log entry for an LLM call (docs/05 §2)."""

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    id: int | None = None
    job_id: str
    role: Literal["reasoner", "extractor", "vision", "agent"]
    model: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    latency_ms: int = 0
    fallback_reason: str = ""
    line_id: str | None = None
    cassette_key: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class ChatMessage(BaseModel):
    """Message in conversational agent chat."""

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    role: Literal["user", "assistant", "system"]
    content: str


class ChatRequest(BaseModel):
    """User chat message request."""

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    message: str
    history: list[ChatMessage] = Field(default_factory=list)


class ChatResponse(BaseModel):
    """Agent chat reply with proposed suggestions."""

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    reply: str
    suggestions: list[Suggestion] = Field(default_factory=list)
    line_refs: list[str] = Field(default_factory=list)


class JobSummary(BaseModel):
    """Summary of a completed BoM run produced by the agent."""

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    text: str
    total_lines: int = 0
    standard_parts_count: int = 0
    custom_parts_count: int = 0
    open_questions_count: int = 0
    pending_suggestions_count: int = 0
    open_items_count: int = 0


class MbomLine(BaseModel):
    """One line in the manufacturing Bill of Materials (build view)."""

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    id: str  # "M-0001", "M-0002", ...
    parent: str | None = None
    ebom_refs: list[str] = Field(default_factory=list)
    cls: str = Field(validation_alias="class", serialization_alias="class")
    description: str
    qty_per_parent: float = 1.0
    qty_total: float = 1.0
    uom: str = "EA"
    make_buy: str = "make"
    find_no: int = 10
    part_number: str | None = None
    notes: str | None = None


class ValidationIssue(BaseModel):
    """A validation issue identified in a draft BoM."""

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    code: str
    message: str
    blocking: bool = True
    line_id: str | None = None
    rule_id: str | None = None


class ValidationReport(BaseModel):
    """Validation report with blocking issues and non-blocking warnings."""

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    valid: bool
    blocking_issues: list[ValidationIssue] = Field(default_factory=list)
    warnings: list[ValidationIssue] = Field(default_factory=list)


class BomVersion(BaseModel):
    """An approved, immutable frozen version of a BoM."""

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    job_id: str
    version: int = 1
    ebom: list[BomLine] = Field(default_factory=list)
    mbom: list[MbomLine] = Field(default_factory=list)
    open_items: list[str] = Field(default_factory=list)
    stats: dict[str, Any] = Field(default_factory=dict)
    library_version: str
    llm_calls: int = 0
    created_at: str | None = None


class BomDraft(BaseModel):
    """Draft BoM container for a job."""

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    job_id: str
    library_version: str
    profile: str = "profiles/default.yaml"
    ebom: list[BomLine] = Field(default_factory=list)
    mbom: list[MbomLine] = Field(default_factory=list)
    stats: dict[str, Any] = Field(default_factory=dict)
    open_items: list[str] = Field(default_factory=list)
    questions: list[Question] = Field(default_factory=list)
    suggestions: list[Suggestion] = Field(default_factory=list)
    mapping: TableMapping | None = None
    user_rows_raw: list[dict[str, Any]] = Field(default_factory=list)
    llm_calls: list[LlmCallRecord] = Field(default_factory=list)
    summary: JobSummary | None = None
    validation: ValidationReport | None = None
    rejected_suggestion_keys: set[str] = Field(default_factory=set)


__all__ = [
    "BomDraft",
    "BomLine",
    "BomVersion",
    "ChatMessage",
    "ChatRequest",
    "ChatResponse",
    "ColumnMapping",
    "DatasheetSpec",
    "Designation",
    "JobSummary",
    "LlmCallRecord",
    "MakeBuyType",
    "MbomLine",
    "PhotoFinding",
    "Prov",
    "Question",
    "QuestionKind",
    "ReviewStatus",
    "Suggestion",
    "SuggestionReview",
    "TableMapping",
    "UserBomRow",
    "ValidationIssue",
    "ValidationReport",
]
