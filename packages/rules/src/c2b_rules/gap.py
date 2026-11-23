"""Gap rules engine for CAD-to-BoM (docs/04 §7.3, docs/06 §2, §6).

Evaluates:
1. Universal gap rules (label, manual, carton).
2. Class-triggered gap rules (solder, hose clamps, transport label).
3. Archetype skeleton gaps (matching against library/archetypes/).
4. Suppression of previously rejected suggestions.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from c2b_domain.bom import BomLine, Suggestion
from c2b_library.loader import Library

log = logging.getLogger(__name__)


def evaluate_gap_rules(
    ebom: list[BomLine],
    library: Library,
    product_desc: str = "",
    process_hints: list[str] | None = None,
    rejected_keys: set[str] | None = None,
    existing_suggestions: list[Suggestion] | None = None,
) -> list[Suggestion]:
    """Evaluate gap rules and return candidate suggestions."""
    rejected = rejected_keys or set()
    suggestions: list[Suggestion] = list(existing_suggestions or [])
    existing_cls = {
        (l.cls if isinstance(l.cls, str) else l.cls.value) for l in ebom
    }
    # Also include existing suggestion classes
    for s in suggestions:
        if s.cls:
            existing_cls.add(s.cls)

    # Helper to check if any class starts with prefix
    def has_class_prefix(prefix: str) -> bool:
        return any(c.startswith(prefix) for c in existing_cls)

    counter = len(suggestions) + 1

    desc_lower = product_desc.lower()
    hints = set(h.lower() for h in (process_hints or []))
    if "hand solder" in desc_lower or "hand soldered" in desc_lower or "soldered" in desc_lower:
        hints.add("hand_solder")
    if "wave solder" in desc_lower:
        hints.add("wave_solder")

    # -------------------------------------------------------------------------
    # 1. Universal Rules
    # -------------------------------------------------------------------------
    # R-GAP-001: Rating / serial label
    if not has_class_prefix("label.rating") and not has_class_prefix("label.identification"):
        s_id = f"S-{counter:03d}"
        key = "R-GAP-001:label.rating"
        if key not in rejected and s_id not in rejected:
            suggestions.append(
                Suggestion(
                    id=s_id,
                    cls="label.rating",
                    rule="R-GAP-001",
                    origin="gap_rule",
                    level="ebom",
                    qty_total=1.0,
                    text="Add a rating label?",
                    review="suggested",
                )
            )
            counter += 1

    # R-GAP-002: User manual
    if not has_class_prefix("documentation.manual") and not has_class_prefix("documentation"):
        s_id = f"S-{counter:03d}"
        key = "R-GAP-002:documentation.manual"
        if key not in rejected and s_id not in rejected:
            suggestions.append(
                Suggestion(
                    id=s_id,
                    cls="documentation.manual",
                    rule="R-GAP-002",
                    origin="gap_rule",
                    level="ebom",
                    qty_total=1.0,
                    text="Add a user manual?",
                    review="suggested",
                )
            )
            counter += 1

    # R-GAP-003: Shipper carton (MBOM packaging level)
    if not has_class_prefix("packaging.carton") and not has_class_prefix("packaging.box"):
        s_id = f"S-{counter:03d}"
        key = "R-GAP-003:packaging.carton"
        if key not in rejected and s_id not in rejected:
            suggestions.append(
                Suggestion(
                    id=s_id,
                    cls="packaging.carton",
                    rule="R-GAP-003",
                    origin="gap_rule",
                    level="mbom",
                    qty_total=1.0,
                    text="Add a shipper carton?",
                    review="suggested",
                )
            )
            counter += 1

    # -------------------------------------------------------------------------
    # 2. Class-Triggered Rules
    # -------------------------------------------------------------------------
    # R-GAP-021: Solder for hand-soldered or wave-soldered PCBA
    has_pcba = (
        has_class_prefix("electrical.pcba")
        or has_class_prefix("electrical.pcb")
        or "receiver board" in desc_lower
        or "board" in desc_lower
        or "pcba" in desc_lower
    )
    has_solder_hint = bool(hints & {"hand_solder", "wave_solder"})

    if has_pcba and has_solder_hint and not has_class_prefix("consumable.solder"):
        s_id = f"S-{counter:03d}"
        key = "R-GAP-021:consumable.solder"
        if key not in rejected and s_id not in rejected:
            solder_text = "Add solder? The receiver board is hand soldered (details text)."
            suggestions.append(
                Suggestion(
                    id=s_id,
                    cls="consumable.solder",
                    rule="R-GAP-021",
                    origin="gap_rule",
                    level="mbom",
                    qty_total=None,
                    text=solder_text,
                    review="suggested",
                )
            )
            counter += 1

    # R-GAP-004: Transport label for Li-ion battery
    if has_class_prefix("electrical.battery.li_ion") and not has_class_prefix("label.transport"):
        s_id = f"S-{counter:03d}"
        key = "R-GAP-004:label.transport"
        if key not in rejected and s_id not in rejected:
            suggestions.append(
                Suggestion(
                    id=s_id,
                    cls="label.transport",
                    rule="R-GAP-004",
                    origin="gap_rule",
                    level="ebom",
                    qty_total=1.0,
                    text="Add a lithium battery transport label?",
                    review="suggested",
                )
            )
            counter += 1

    # R-GAP-030: Clamps for fluid hoses
    if has_class_prefix("fluid.hose") and not has_class_prefix("fluid.clamp"):
        s_id = f"S-{counter:03d}"
        key = "R-GAP-030:fluid.clamp"
        if key not in rejected and s_id not in rejected:
            suggestions.append(
                Suggestion(
                    id=s_id,
                    cls="fluid.clamp",
                    rule="R-GAP-030",
                    origin="gap_rule",
                    level="ebom",
                    qty_total=2.0,
                    text="Add hose clamps?",
                    review="suggested",
                )
            )
            counter += 1

    # -------------------------------------------------------------------------
    # 3. Archetype Skeletons
    # -------------------------------------------------------------------------
    if library.archetypes and product_desc:
        for arch_id, arch in library.archetypes.items():
            # Check match keywords
            matches = any(kw.lower() in desc_lower for kw in arch.match_keywords)
            if matches:
                for expected_item in arch.expected:
                    exp_cls = expected_item.class_
                    if not has_class_prefix(exp_cls):
                        s_id = f"S-{counter:03d}"
                        key = f"ARCH-{arch_id}:{exp_cls}"
                        if key not in rejected and s_id not in rejected:
                            suggestions.append(
                                Suggestion(
                                    id=s_id,
                                    cls=exp_cls,
                                    rule=f"ARCH-{arch_id.upper()}",
                                    origin="skeleton",
                                    level="ebom",
                                    qty_total=float(expected_item.min_count),
                                    text=f"Add {expected_item.note or exp_cls}?",
                                    review="suggested",
                                )
                            )
                            counter += 1

    return suggestions
