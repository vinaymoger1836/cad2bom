"""Provenance wrapper: every BoM value carries its source, confidence and rule ids."""

from typing import Annotated, Literal

from pydantic import BaseModel, Field

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

Confidence = Annotated[float, Field(ge=0.0, le=1.0)]


class Prov[T](BaseModel):
    """A value plus where it came from (docs/05 §1)."""

    value: T
    unit: str | None = None
    source: Source
    confidence: Confidence
    rules: list[str] = Field(default_factory=list)  # rule ids, e.g. ["R-STD-012"]
    ref: str | None = None  # "cad:N-0042", "bom:row 17", "pdf:att_03#p2"
    note: str | None = None
