"""BoM domain models (docs/05 §1). Pure Pydantic."""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from c2b_domain.prov import Prov, Source

MakeBuyType = Literal["make", "buy", "supplier_made"]
ReviewStatus = Literal["accepted", "edited", "derived", "flagged"]


class Designation(BaseModel):
    """Standard part designation (ISO 4762 M4x12, 6201, etc.)."""

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    standard: str
    family: str
    size: str
    length_mm: float | None = None
    score: float = 1.0
    key: str | None = None


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
    qty_total: float = 1.0
    material: Prov[str] | dict[str, Any] | str | None = None
    finish: Prov[str] | dict[str, Any] | str | None = None
    mass_g: Prov[float] | float | None = None
    make_buy: Prov[MakeBuyType] | MakeBuyType | None = None
    refdes: list[str] = Field(default_factory=list)
    mpn: str | None = None
    manufacturer: str | None = None
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


__all__ = [
    "BomDraft",
    "BomLine",
    "Designation",
    "MakeBuyType",
    "ReviewStatus",
]
