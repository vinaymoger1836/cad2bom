"""Make/buy recommendation engine (docs/04 §10, library/rules/makebuy.yaml).

Evaluates whether each component should be:
- 'buy': Standard catalogue parts, fasteners, bearings, purchased modules.
- 'make': In-house manufactured or assembled components.
- 'supplier_made': Custom geometry parts made to drawing by an external supplier.
Incorporates annual volume bands and generates provenance traces.
"""

from __future__ import annotations

import logging
from typing import Literal

from c2b_domain.bom import BomLine
from c2b_domain.prov import Prov
from c2b_library.loader import Library

log = logging.getLogger(__name__)

MakeBuyType = Literal["make", "buy", "supplier_made"]


def recommend_make_buy(
    line: BomLine,
    library: Library | None = None,
    annual_volume: int | None = None,
) -> Prov[MakeBuyType]:
    """Determine make/buy recommendation with rules and volume considerations."""
    # 1. User specified always has highest priority
    if hasattr(line, "make_buy") and line.make_buy:
        if isinstance(line.make_buy, Prov) and line.make_buy.source == "user":
            return line.make_buy

    cls_str = line.cls if isinstance(line.cls, str) else line.cls.value
    rules: list[str] = []

    # 2. Standard catalogue parts or designated fasteners
    if line.designation is not None or line.origin == "std_recogniser":
        return Prov(
            value="buy",
            source="library",
            confidence=0.95,
            rules=["R-MKB-001"],
            note="Standard catalogue part or purchased component (rule R-MKB-001)",
        )

    # 3. Class definitions in library
    cls_def = None
    if library and library.taxonomy:
        cls_def = next((c for c in library.taxonomy.classes if c.code == cls_str), None)

    if cls_def and cls_def.buy_as_assembly:
        return Prov(
            value="buy",
            source="library",
            confidence=0.95,
            rules=["R-MKB-001"],
            note="Purchased assembly bought complete (rule R-MKB-001)",
        )

    # Fasteners, bearings, seals, electrical components bought by default
    buy_prefixes = (
        "fastener",
        "bearing",
        "seal",
        "spring.standard",
        "electrical.component",
        "electrical.motor",
        "electrical.switch",
        "electrical.cable",
        "consumable",
        "packaging",
        "label",
        "documentation",
        "purchased",
    )
    if any(cls_str.startswith(p) for p in buy_prefixes):
        return Prov(
            value="buy",
            source="library",
            confidence=0.90,
            rules=["R-MKB-001"],
            note="Standard catalogue item or purchased component (rule R-MKB-001)",
        )

    # 4. In-house assemblies
    if cls_str.startswith("assembly") and not (cls_def and cls_def.buy_as_assembly):
        return Prov(
            value="make",
            source="library",
            confidence=0.90,
            rules=["R-MKB-ASM"],
            note="In-house assembly build step (rule R-MKB-ASM)",
        )

    # 5. Custom geometry classes (sheet metal, machined, moulded, cast, extrusion)
    custom_prefixes = (
        "sheetmetal",
        "machined",
        "moulded",
        "cast",
        "extrusion",
        "wood",
        "glass",
    )
    if any(cls_str.startswith(p) for p in custom_prefixes):
        note = "Custom geometry made to drawing by supplier (rule R-MKB-002)"
        rules.append("R-MKB-002")

        if annual_volume is not None:
            if cls_str.startswith("moulded") and annual_volume < 5000:
                note += f"; annual volume ({annual_volume}) suggests low-volume tooling or soft tooling"
                rules.append("R-MKB-VOL")
            elif cls_str.startswith("moulded") and annual_volume >= 50000:
                note += f"; annual volume ({annual_volume}) justifies hardened steel multi-cavity injection mould"
                rules.append("R-MKB-VOL")

        return Prov(
            value="supplier_made",
            source="library",
            confidence=0.70,
            rules=rules,
            note=note,
        )

    # Default fallback
    default_mb: MakeBuyType = "supplier_made"
    if cls_def and cls_def.make_buy_default:
        default_mb = cls_def.make_buy_default

    return Prov(
        value=default_mb,
        source="library",
        confidence=0.60,
        rules=["R-MKB-DEF"],
        note=f"Default make/buy classification for class '{cls_str}'",
    )
