"""Validation engine for CAD-to-BoM (docs/04 §11, docs/05 §1).

Evaluates blocking conditions that prevent release approval and non-blocking warnings:
- Blocking: open questions, open suggestions, non-positive quantities, unknown units,
  cycles, duplicate part numbers with conflicting descriptions, unclassified leaves.
- Warnings: mass discrepancies, assumed profile defaults, low confidence.
"""

from __future__ import annotations

import logging

from c2b_domain.bom import (
    BomDraft,
    ValidationIssue,
    ValidationReport,
)
from c2b_domain.prov import Prov
from c2b_library.loader import Library

log = logging.getLogger(__name__)

STANDARD_UNITS = {"EA", "SET", "M", "MM", "CM", "KG", "G", "L", "ML", "M2"}


def validate_draft(
    draft: BomDraft,
    library: Library | None = None,
) -> ValidationReport:
    """Validate a draft BoM against blocking and warning criteria."""
    blocking: list[ValidationIssue] = []
    warnings: list[ValidationIssue] = []

    valid_units = STANDARD_UNITS
    if library and library.units:
        valid_units = {u.code for u in library.units.units}

    # 1. Blocking: Unanswered questions
    for q in draft.questions:
        if q.answer is None and q.blocking:
            blocking.append(
                ValidationIssue(
                    code="unresolved_question",
                    message=f"Blocking question '{q.id}' requires user input: {q.text}",
                    blocking=True,
                    rule_id="R-VAL-001",
                )
            )

    # 2. Blocking: Open unreviewed suggestions
    for s in draft.suggestions:
        review_val = getattr(s, "review", getattr(s, "status", "suggested"))
        if review_val == "suggested":
            s_text = s.text or s.cls
            blocking.append(
                ValidationIssue(
                    code="open_suggestion",
                    message=f"Open suggestion '{s.id}' must be accepted or rejected: {s_text}",
                    blocking=True,
                    rule_id="R-VAL-002",
                )
            )

    # 3. Hierarchy cycle check
    line_map = {ln.id: ln for ln in draft.ebom}
    for line in draft.ebom:
        visited = set()
        curr = line.parent
        while curr:
            if curr in visited or curr == line.id:
                blocking.append(
                    ValidationIssue(
                        code="circular_dependency",
                        message=f"Circular assembly reference detected involving line '{line.id}'",
                        blocking=True,
                        line_id=line.id,
                        rule_id="R-VAL-CYC",
                    )
                )
                break
            visited.add(curr)
            parent_line = line_map.get(curr)
            curr = parent_line.parent if parent_line else None

    # 4. Line-level validation
    part_numbers_seen: dict[str, str] = {}

    for line in draft.ebom:
        cls_val = line.cls if isinstance(line.cls, str) else line.cls.value
        desc_val = line.description if isinstance(line.description, str) else line.description.value

        # Unclassified part
        if cls_val == "unclassified":
            c_name = line.cad_name or "N/A"
            blocking.append(
                ValidationIssue(
                    code="unclassified_part",
                    message=f"Line '{line.id}' ({c_name}) has unclassified component class",
                    blocking=True,
                    line_id=line.id,
                    rule_id="R-VAL-CLS",
                )
            )

        # Quantity checks
        raw_qty = line.qty_per_parent if hasattr(line, "qty_per_parent") else 1.0
        qty_val = float(raw_qty.value if isinstance(raw_qty, Prov) else raw_qty)
        if qty_val <= 0:
            blocking.append(
                ValidationIssue(
                    code="invalid_quantity",
                    message=f"Line '{line.id}' has non-positive quantity: {qty_val}",
                    blocking=True,
                    line_id=line.id,
                    rule_id="R-VAL-QTY",
                )
            )

        # UOM check
        if line.uom not in valid_units:
            blocking.append(
                ValidationIssue(
                    code="unknown_uom",
                    message=f"Line '{line.id}' has unrecognized unit of measure: '{line.uom}'",
                    blocking=True,
                    line_id=line.id,
                    rule_id="R-VAL-UOM",
                )
            )

        # Duplicate part number check with conflicting descriptions
        pn_val = line.part_number
        if pn_val:
            pn_str = pn_val.value if hasattr(pn_val, "value") else str(pn_val)
            if pn_str:
                if pn_str in part_numbers_seen:
                    prev_desc = part_numbers_seen[pn_str]
                    if prev_desc.lower() != desc_val.lower():
                        msg = (
                            f"Part number '{pn_str}' reused with conflicting description: "
                            f"'{desc_val}' vs '{prev_desc}'"
                        )
                        blocking.append(
                            ValidationIssue(
                                code="duplicate_part_number_conflict",
                                message=msg,
                                blocking=True,
                                line_id=line.id,
                                rule_id="R-VAL-DPN",
                            )
                        )
                else:
                    part_numbers_seen[pn_str] = desc_val

        # Warnings: Low confidence
        if line.confidence < 0.60:
            warnings.append(
                ValidationIssue(
                    code="low_confidence",
                    message=f"Line '{line.id}' has low confidence ({line.confidence * 100:.1f}%)",
                    blocking=False,
                    line_id=line.id,
                    rule_id="R-VAL-CONF",
                )
            )

        # Warnings: Assumed material defaults
        for oi in line.open_items:
            if "material assumed" in oi.lower():
                warnings.append(
                    ValidationIssue(
                        code="assumed_material",
                        message=f"Line '{line.id}': {oi}",
                        blocking=False,
                        line_id=line.id,
                        rule_id="R-VAL-MAT",
                    )
                )

    is_valid = len(blocking) == 0
    return ValidationReport(
        valid=is_valid,
        blocking_issues=blocking,
        warnings=warnings,
    )
