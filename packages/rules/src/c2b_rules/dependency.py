"""Line dependency index and partial re-run engine (docs/04 §12).

Maintains a dependency DAG for BoM properties to execute fast, incremental updates
when users answer questions or edit fields. Enforces Non-negotiable Rules:
- User edits are source='user', confidence=1.0, and NEVER overwritten by re-runs.
- Rejected suggestions remain permanently rejected and are not re-suggested.
"""

from __future__ import annotations

import logging
from typing import Any

from c2b_domain.bom import BomDraft, BomLine, ColumnMapping
from c2b_domain.prov import Prov
from c2b_library.loader import Library
from c2b_rules.makebuy import recommend_make_buy
from c2b_rules.mbom import derive_mbom
from c2b_rules.validator import validate_draft

log = logging.getLogger(__name__)


def apply_user_line_edit(
    draft: BomDraft,
    line_id: str,
    field: str,
    new_value: Any,
    library: Library | None = None,
) -> BomDraft:
    """Apply an explicit user edit to a line with source='user' and confidence 1.0."""
    target_line = next((ln for ln in draft.ebom if ln.id == line_id), None)
    if not target_line:
        raise ValueError(f"Line '{line_id}' not found in draft EBOM")

    # Enforce Non-negotiable Rule 4: source='user', confidence=1.0
    user_prov: Prov[Any] = Prov(
        value=new_value,
        source="user",
        confidence=1.0,
        rules=["R-USR-EDIT"],
        note="Explicit user edit",
    )

    if field == "description":
        target_line.description = user_prov
    elif field == "material":
        target_line.material = user_prov
        # Recalculate mass if volume is known
        if target_line.cad_volume_mm3 and library and library.materials:
            mat_info = next(
                (m for m in library.materials.materials if m.code == str(new_value)),
                None,
            )
            if mat_info and mat_info.density_g_cm3:
                density = float(mat_info.density_g_cm3)
                vol_cm3 = target_line.cad_volume_mm3 / 1000.0
                target_line.mass_g = round(vol_cm3 * density, 4)
    elif field in ("qty", "qty_per_parent"):
        target_line.qty_per_parent = Prov(
            value=float(new_value),
            source="user",
            confidence=1.0,
            rules=["R-USR-EDIT"],
            note="Explicit user edit",
        )
    elif field == "class":
        target_line.cls = user_prov
        if library:
            target_line.make_buy = recommend_make_buy(target_line, library)
    elif field == "part_number":
        target_line.part_number = user_prov
    elif field == "finish":
        target_line.finish = user_prov
    elif field == "make_buy":
        target_line.make_buy = user_prov
    elif field == "uom":
        target_line.uom = str(new_value)

    # Re-roll quantities and MBOM
    recalculate_draft(draft, library)
    return draft


def get_line_field(line: BomLine, field: str) -> Any:
    """Plain value of an editable line field, unwrapping provenance (for the edit log)."""
    key = {"qty": "qty_per_parent", "class": "cls"}.get(field, field)
    raw = getattr(line, key, None)
    if isinstance(raw, Prov):
        return raw.value
    if isinstance(raw, dict):
        return raw.get("value", raw)
    return raw


def apply_mapping_confirmation(draft: BomDraft, column: str, field: str) -> None:
    """The user confirms (or changes) the field of a spreadsheet column: source=user, final."""
    if draft.mapping is None or column not in draft.mapping.columns:
        raise ValueError(f"Column '{column}' is not part of the BoM file mapping")
    draft.mapping.columns[column] = ColumnMapping(
        field=field,
        by="user",
        score=1.0,
        confirmed_by_user=True,
        why="Confirmed by the user",
    )
    if column in draft.mapping.ambiguous_columns:
        draft.mapping.ambiguous_columns.remove(column)
    if field == "description":
        # User lines keep the user's own text; it only becomes the line description now.
        for line in draft.ebom:
            if line.raw and column in line.raw:
                text = str(line.raw[column]).strip()
                if text:
                    line.description = text


def apply_question_answer(
    draft: BomDraft,
    question_id: str,
    answer: Any,
    library: Library | None = None,
) -> BomDraft:
    """Apply a user's answer to an open question and trigger an incremental update."""
    question = next((q for q in draft.questions if q.id == question_id), None)
    if not question:
        raise ValueError(f"Question '{question_id}' not found in draft")

    question.answer = answer
    if question.mapping_column:
        apply_mapping_confirmation(draft, question.mapping_column, str(answer))
        recalculate_draft(draft, library)
        return draft

    # Apply answer to target lines if applicable
    import re

    match = re.search(r"\b(\d+(?:\.\d+)?)\b", str(answer))
    for lid in question.line_ids:
        line = next((ln for ln in draft.ebom if ln.id == lid), None)
        if line and match:
            num_val = float(match.group(1))
            answered: Prov[float] = Prov(
                value=num_val,
                source="user",
                confidence=1.0,
                rules=["R-USR-ANS"],
                ref=f"question:{question_id}",
                note="Answered by the user",
            )
            if line.parent:
                parent_line = next((ln for ln in draft.ebom if ln.id == line.parent), None)
                if parent_line:
                    parent_line.qty_per_parent = answered
                    parent_line.qty_total = num_val
            else:
                line.qty_per_parent = answered

    recalculate_draft(draft, library)
    return draft


def apply_suggestion_review(
    draft: BomDraft,
    suggestion_id: str,
    decision: str,  # "accepted" or "rejected"
    library: Library | None = None,
) -> BomDraft:
    """Record acceptance or rejection of a suggestion."""
    sug = next((s for s in draft.suggestions if s.id == suggestion_id), None)
    if not sug:
        raise ValueError(f"Suggestion '{suggestion_id}' not found in draft")

    sug.review = "accepted" if decision == "accepted" else "rejected"
    if hasattr(sug, "status"):
        sug.status = sug.review

    if decision == "rejected":
        # Suppress future suggestions for this rule / class
        key = f"{sug.rule}:{sug.cls}"
        draft.rejected_suggestion_keys.add(key)
        draft.rejected_suggestion_keys.add(sug.id)
    elif decision == "accepted" and sug.action == "modify":
        # Chat / ladder proposal to change one closed-list field of an existing line. The user's
        # click is the acceptance; the value keeps its AI provenance and capped confidence.
        target = next((ln for ln in draft.ebom if ln.id == sug.line_id), None)
        if target is not None and sug.value is not None and sug.field in ("material", "make_buy"):
            proposed: Prov[Any] = Prov(
                value=sug.value,
                source="llm",
                confidence=min(sug.confidence, 0.70),
                rules=sug.rule_ids or ["R-AGT-CHAT"],
                ref=f"suggestion:{sug.id}",
                note=f"Accepted by the user: {sug.reason or sug.text}".strip(),
            )
            if sug.field == "material":
                target.material = proposed
            else:
                target.make_buy = proposed
            target.review = "accepted"
    elif decision == "accepted" and getattr(sug, "action", "add") == "add" and sug.level == "ebom":
        # If suggestion is an addition to EBOM, add as a new line
        new_id = f"L-{len(draft.ebom) + 1:04d}"
        desc = sug.text or (sug.cls or "Item").upper()
        new_line = BomLine(
            id=new_id,
            level=1,
            find_no=(len(draft.ebom) + 1) * 10,
            cls=sug.cls or "unclassified",
            noun=(sug.cls or "ITEM").split(".")[-1].upper(),
            description=desc,
            qty_per_parent=sug.qty_total or 1.0,
            qty_total=sug.qty_total or 1.0,
            uom="EA",
            origin="gap_rule",
            review="accepted",
        )
        draft.ebom.append(new_line)

    recalculate_draft(draft, library)
    return draft


def recalculate_draft(
    draft: BomDraft,
    library: Library | None = None,
) -> None:
    """Recalculate roll-up quantities, MBOM, open items, and validation."""
    line_map = {ln.id: ln for ln in draft.ebom}

    # 1. Recalculate total quantities down the tree
    def compute_total_qty(line: BomLine) -> float:
        parent_qty = 1.0
        if line.parent and line.parent in line_map:
            parent_qty = compute_total_qty(line_map[line.parent])
        raw_qty = line.qty_per_parent if hasattr(line, "qty_per_parent") else 1.0
        curr_qty = float(raw_qty.value if isinstance(raw_qty, Prov) else (raw_qty or 1.0))
        tot = parent_qty * curr_qty
        line.qty_total = tot
        return tot

    for line in draft.ebom:
        compute_total_qty(line)

    # 2. Recalculate MBOM
    accepted_suggs = [s for s in draft.suggestions if getattr(s, "review", "") == "accepted"]
    draft.mbom = derive_mbom(draft.ebom, library, accepted_suggestions=accepted_suggs)

    # 3. Recalculate open items
    open_items: list[str] = []
    for line in draft.ebom:
        for oi in line.open_items:
            open_items.append(f"{line.id}: {oi}")
    draft.open_items = open_items

    # 4. Re-run validation
    draft.validation = validate_draft(draft, library)
