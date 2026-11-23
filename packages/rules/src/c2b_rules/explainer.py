"""Deterministic "Explain this line" generator (docs/04 §1).

CLAUDE.md non-negotiable rule 7: Every rule has an id (R-STD-*, R-CLS-*, R-MKB-*).
Each derived value records rule ids so any line can be explained deterministically without an LLM.
"""

from typing import Any

from c2b_domain.bom import BomLine
from c2b_domain.prov import Prov


def _to_str(val: Any) -> str | None:
    if val is None:
        return None
    if isinstance(val, str):
        return val
    if isinstance(val, Prov):
        return str(val.value)
    if isinstance(val, dict):
        v = val.get("value")
        return str(v) if v is not None else None
    return str(val)


def _to_float(val: Any) -> float | None:
    if val is None:
        return None
    if isinstance(val, (int, float)):
        return float(val)
    if isinstance(val, Prov):
        return float(val.value)
    if isinstance(val, dict):
        v = val.get("value")
        return float(v) if v is not None else None
    return None


def explain_line(line: BomLine) -> list[str]:
    """Generate deterministic explanation paragraphs for a BoM line."""
    lines: list[str] = []

    # 1. Identity & Structure
    parent_str = f", under parent {line.parent}" if line.parent else ""
    qty_p_raw = _to_float(line.qty_per_parent) or 1.0
    qty_p = int(qty_p_raw)
    qty_t = int(line.qty_total)
    lines.append(
        f"Line {line.id} ('{line.cad_name}') is at Level {line.level} "
        f"(Find Number {line.find_no}{parent_str}) with quantity {qty_p} {line.uom} "
        f"(total {qty_t})."
    )

    # 2. Classification & Designation
    cls_val = _to_str(line.cls) or "unknown"
    if line.designation is not None:
        d = line.designation
        len_str = f" x {int(d.length_mm)} mm" if d.length_mm is not None else ""
        lines.append(
            f"Recognised as standard part {d.standard} ({d.family}, size {d.size}{len_str}) "
            f"with match score {d.score:.2f}. Classified as '{cls_val}' with confidence "
            f"{line.confidence:.2f} based on standard table dimensions (rule R-CLS-STD)."
        )
    else:
        lines.append(
            f"Classified as '{cls_val}' with confidence {line.confidence:.2f} based on "
            "CAD name dictionary and geometric feature analysis."
        )

    # 3. Description
    desc_val = _to_str(line.description) or ""
    lines.append(
        f"Description '{desc_val}' generated noun-first from class definition "
        f"(noun '{line.noun}', modifiers {line.modifiers})."
    )

    # 4. Make / Buy
    mkb_val = _to_str(line.make_buy) or "unknown"
    if mkb_val == "buy":
        lines.append(
            "Procurement: recommended as 'buy' "
            "(standard catalogue part or purchased item; rule R-MKB-001)."
        )
    elif mkb_val == "supplier_made":
        lines.append(
            "Procurement: recommended as 'supplier_made' "
            "(custom geometry made to drawing by supplier; rule R-MKB-002)."
        )
    elif mkb_val == "make":
        lines.append("Procurement: recommended as 'make' (in-house assembly; rule R-MKB-ASM).")

    # 5. Material & Mass
    mat_val = _to_str(line.material)
    mass_val = _to_float(line.mass_g)
    if mat_val and mass_val is not None:
        vol_str = (
            f"{line.cad_volume_mm3:.2f} mm3"
            if line.cad_volume_mm3 is not None
            else "measured volume"
        )
        lines.append(
            f"Material '{mat_val}' resolved from profile defaults (confidence 0.50). "
            f"Calculated mass {mass_val:.4f} g from {vol_str} x density (rule R-MSS-VOL)."
        )
    elif mat_val:
        lines.append(f"Material '{mat_val}' resolved from profile defaults.")
    else:
        lines.append("Material is currently unspecified (open item).")

    # 6. Rule provenance summary
    if line.rule_ids:
        lines.append(f"Derived using rules: {', '.join(sorted(set(line.rule_ids)))}.")

    return lines


def format_explanation_text(line: BomLine) -> str:
    """Format the explanation as a single readable markdown string."""
    return "\n\n".join(explain_line(line))
