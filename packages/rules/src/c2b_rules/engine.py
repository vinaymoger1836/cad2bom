"""Deterministic rules engine coordinator (docs/04 §1).

Assembles pipeline stages 4, 5, 6, and 11 into a complete draft EBOM.
"""

from typing import Any

from c2b_domain.bom import BomDraft
from c2b_domain.cad import CadExtractResult
from c2b_domain.prov import Prov
from c2b_library.loader import Library
from c2b_rules.explainer import explain_line
from c2b_rules.structure import build_ebom_lines


def _get_mass_float(val: Prov[float] | float | None) -> float:
    if val is None:
        return 0.0
    if isinstance(val, (int, float)):
        return float(val)
    if isinstance(val, Prov):
        return float(val.value)
    return 0.0


def build_ebom(
    extract: CadExtractResult,
    library: Library,
    job_id: str,
    profile_name: str = "default",
) -> BomDraft:
    """Run deterministic rules pipeline on extracted CAD nodes to build draft EBOM."""
    nodes = extract.nodes
    shapes = extract.shapes

    # Run structure, classification, recognition, materials and mass
    lines = build_ebom_lines(
        nodes=nodes,
        shapes=shapes,
        library=library,
        profile_name=profile_name,
    )

    # Attach explanations
    for line in lines:
        line.explanation = explain_line(line)

    # Aggregate stats and open items
    total_mass = sum(_get_mass_float(line.mass_g) for line in lines)
    open_items: list[str] = []
    for line in lines:
        for oi in line.open_items:
            open_items.append(f"{line.id} ({line.cad_name}): {oi}")

    stats: dict[str, Any] = {
        "line_count": len(lines),
        "total_mass_g": round(total_mass, 4),
        "standard_part_count": sum(1 for line in lines if line.designation is not None),
        "open_item_count": len(open_items),
        "phantom_count": sum(1 for line in lines if line.phantom),
    }

    return BomDraft(
        job_id=job_id,
        library_version=library.version.version,
        profile=profile_name,
        ebom=lines,
        mbom=[],
        stats=stats,
        open_items=open_items,
    )
