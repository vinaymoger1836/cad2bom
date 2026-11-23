"""JSON Schemas and Pydantic models with closed enums from the library (docs/04 §9).

Rules:
- Structured output against a JSON schema whose enums are the library codes.
- No free-form generation of classes, material families, or header fields.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from c2b_library.loader import Library


def get_taxonomy_codes(library: Library | None = None) -> list[str]:
    """Retrieve all valid taxonomy class codes from library."""
    if library and library.taxonomy:
        return [cls.code for cls in library.taxonomy.classes]
    # Fallback standard set if library not loaded
    return [
        "moulded.housing",
        "moulded.housing.top",
        "moulded.housing.bottom",
        "moulded.cover",
        "moulded.knob",
        "moulded.button",
        "moulded.cap",
        "machined.turned.knob",
        "machined.turned",
        "machined.milled",
        "sheetmetal.bracket",
        "sheetmetal.clip",
        "sheetmetal.chassis",
        "fastener.screw.socket_head",
        "fastener.screw.hex_head",
        "fastener.nut.hex",
        "fastener.washer.plain",
        "fastener.clip",
        "spring.compression",
        "spring.clip",
        "seal.gasket.flat",
        "seal.gasket.formed",
        "seal.gasket.foam",
        "seal.o_ring",
        "bearing.radial_ball",
        "electrical.pcb",
        "electrical.pcba",
        "electrical.battery.li_ion",
        "electrical.battery.pack",
        "electrical.motor.ac_induction",
        "electrical.wire",
        "packaging.carton",
        "documentation.manual",
        "label.rating",
        "consumable.solder",
        "consumable.adhesive",
    ]


def get_material_families(library: Library | None = None) -> list[str]:
    """Retrieve all valid material families from library."""
    if library and library.materials:
        families = {m.family for m in library.materials.materials if m.family}
        if families:
            return sorted(families)
    return [
        "steel",
        "stainless_steel",
        "aluminium",
        "copper_brass",
        "plastic_engineering",
        "plastic_commodity",
        "rubber_elastomer",
        "glass_ceramic",
        "wood_natural",
    ]


def get_header_fields() -> list[str]:
    """Standard BoM table fields for column mapping."""
    return [
        "find_no",
        "part_number",
        "description",
        "qty",
        "uom",
        "level",
        "refdes",
        "material",
        "manufacturer",
        "mpn",
        "notes",
        "unmapped",
    ]


# Structured output models for fallback ladder L1-L6 (docs/04 §9)


class L1Output(BaseModel):
    """Output schema for L1: Classify unknown part."""

    cls: str = Field(description="Taxonomy class code selected strictly from allowed taxonomy")
    confidence: float = Field(ge=0.0, le=1.0, description="Confidence score 0.0 to 1.0")
    reason: str = Field(description="Compact technical justification for the classification")


class L2Output(BaseModel):
    """Output schema for L2: Material family."""

    material_family: str = Field(description="Material family code selected strictly from library")
    reason: str = Field(description="One-line technical justification")


class L3Output(BaseModel):
    """Output schema for L3: BoM header mapping."""

    mappings: dict[str, str] = Field(
        description="Dictionary mapping header column names to canonical field enum values"
    )
    reason: str = Field(description="Reason explaining the column field resolution")


class L4GapCandidate(BaseModel):
    """A candidate gap item class proposed for an unknown product type."""

    cls: str = Field(description="Taxonomy class code selected strictly from allowed taxonomy")
    reason: str = Field(description="One-line reason why this component is expected in the product")


class L4Output(BaseModel):
    """Output schema for L4: Gap candidates for unknown product type (max 8)."""

    candidates: list[L4GapCandidate] = Field(
        max_length=8, description="List of at most 8 gap candidates"
    )


class L5Output(BaseModel):
    """Output schema for L5: Description wording."""

    noun: str = Field(description="Uppercase primary noun")
    modifiers: list[str] = Field(
        default_factory=list, description="List of uppercase modifying terms"
    )


class L6Output(BaseModel):
    """Output schema for L6: Datasheet field."""

    field: str = Field(description="Specification field name")
    value: str = Field(description="Extracted value string exactly matching quoted text")
    unit: str | None = Field(default=None, description="Physical unit if present")
    page: int = Field(ge=1, description="Page number where the value appears")
    quote: str = Field(
        description="Exact verbatim sentence or phrase from the page containing the value"
    )


def validate_enum_field(value: str, allowed: list[str], field_name: str) -> bool:
    """Validate that value is in allowed closed enum list."""
    return value.strip().lower() in [item.strip().lower() for item in allowed]
