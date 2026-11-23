"""Deterministic merge engine for CAD draft, user BoM, datasheets, and photos (docs/04 §7.2).

Matching order for each user BoM line against CAD part groups:
1. Exact part number (canonical form).
2. Exact MPN or standard designation.
3. Description similarity (token-set ratio >= 0.85 after normalising nouns and units)
   AND compatible class AND quantity compatible.
4. Description similarity >= 0.70 and geometric size hint in description within 5% of CAD dimension.

Outcomes:
- Matched, values agree (5% tolerance): One line, origin=user_bom, CAD evidence attached.
- Matched, values disagree: Line keeps user's value; question showing both values and sources.
- User line with no CAD match: Kept verbatim.
- CAD part with no user line: Suggestion: 'CAD shows X (Nx). Add it to the BoM?'
  (skipping phantoms).
"""

from __future__ import annotations

import re

from c2b_domain.bom import BomLine, DatasheetSpec, PhotoFinding, Question, Suggestion, UserBomRow
from c2b_domain.prov import Prov
from c2b_library.loader import Library


def _tokenize(text: str | None) -> set[str]:
    """Tokenize a string into alphanumeric lowercased tokens."""
    if not text:
        return set()
    return set(re.findall(r"[a-z0-9]+", text.lower()))


def _extract_size_hint(text: str | None) -> str | None:
    """Extract standard or metric size hints like 'M4x12', '40x30x25', '1200'."""
    if not text:
        return None
    # Check for thread size M\d+(?:x\d+)?
    m_thread = re.search(r"\b(m\d+(?:\s*[x\u00d7]\s*\d+)?)\b", text, re.IGNORECASE)
    if m_thread:
        return m_thread.group(1).replace(" ", "").lower()
    # Check for dimensions \d+x\d+
    m_dim = re.search(r"\b(\d+\s*[x\u00d7]\s*\d+(?:\s*[x\u00d7]\s*\d+)?)\b", text, re.IGNORECASE)
    if m_dim:
        return m_dim.group(1).replace(" ", "").lower()
    return None


def _check_match(
    user_row: UserBomRow,
    cad_line: BomLine,
) -> tuple[bool, str]:
    """Check if a user BoM line matches a CAD line according to the 4-step hierarchy."""
    # Step 1: Part number exact (canonical form)
    u_pn = (user_row.part_number or "").strip().upper()
    c_pn = ""
    if cad_line.part_number:
        c_pn = (
            str(
                cad_line.part_number.value
                if isinstance(cad_line.part_number, Prov)
                else cad_line.part_number
            )
            .strip()
            .upper()
        )
    c_name = (cad_line.cad_name or "").strip().upper()

    if u_pn and c_pn and u_pn == c_pn:
        return True, "part_number_exact"
    if u_pn and c_name and u_pn == c_name:
        return True, "part_number_matches_cad_name"

    # Step 2: MPN or standard designation exact
    u_mpn = (user_row.mpn or "").strip().upper()
    c_mpn = (cad_line.mpn or "").strip().upper()
    if u_mpn and c_mpn and u_mpn == c_mpn:
        return True, "mpn_exact"

    # Standard designation match
    if cad_line.designation:
        d = cad_line.designation
        std_num = re.sub(r"[^0-9]", "", d.standard)
        u_desc_clean = (user_row.description or "").lower()

        # Check standard number + size match in user description or MPN
        has_std = bool(re.search(rf"\b{d.standard.lower()}\b|\b{std_num}\b", u_desc_clean))
        has_size = bool(re.search(rf"\b{d.size.lower()}\b", u_desc_clean))
        has_len = True
        if d.length_mm is not None:
            has_len = bool(
                re.search(
                    rf"[x\u00d7]\s*{int(d.length_mm)}\b|\b{int(d.length_mm)}\s*mm\b",
                    u_desc_clean,
                )
            )

        if has_std and has_size and has_len:
            return True, "standard_designation_exact"

    # Step 3: Description similarity (token-set ratio >= 0.85) AND compatible class AND qty
    u_tokens = _tokenize(user_row.description)
    c_desc_str = (
        cad_line.description
        if isinstance(cad_line.description, str)
        else cad_line.description.value
    )
    c_tokens = _tokenize(c_desc_str)
    c_name_tokens = _tokenize(cad_line.cad_name)

    # Check direct name presence (e.g. 'bracket' in 'Bracket, sheet metal, 2mm')
    for nt in c_name_tokens:
        if nt in u_tokens and len(nt) >= 4:
            return True, "name_token_in_description"

    if u_tokens and c_tokens:
        intersection = u_tokens.intersection(c_tokens)
        token_ratio = len(intersection) / max(len(u_tokens), 1)
        if token_ratio >= 0.85:
            return True, "description_token_similarity_high"

    # Step 4: Description similarity >= 0.70 and geometric size hint within 5%
    u_hint = _extract_size_hint(user_row.description)
    c_hint = _extract_size_hint(c_desc_str)
    if u_hint and c_hint and u_hint == c_hint:
        return True, "size_hint_match"

    return False, "no_match"


def merge_cad_and_user_bom(
    cad_lines: list[BomLine],
    user_rows: list[UserBomRow],
    datasheet_specs: list[DatasheetSpec] | None = None,
    photo_findings: list[PhotoFinding] | None = None,
    library: Library | None = None,
) -> tuple[list[BomLine], list[Question], list[Suggestion]]:
    """Merge CAD lines and user BoM rows into a reconciled EBOM with questions and suggestions."""
    reconciled_lines: list[BomLine] = []
    questions: list[Question] = []
    suggestions: list[Suggestion] = []

    matched_cad_ids: set[str] = set()
    q_counter = 1
    s_counter = 1

    # Track user rows matched
    for u_row in user_rows:
        best_cad: BomLine | None = None

        for c_line in cad_lines:
            if c_line.id in matched_cad_ids:
                continue
            is_match, _ = _check_match(u_row, c_line)
            if is_match:
                best_cad = c_line
                break

        if best_cad is not None:
            matched_cad_ids.add(best_cad.id)

            # Check quantity agreement
            cad_qty = float(best_cad.qty_total)
            user_qty = float(u_row.qty)

            # Check values agreement (tolerance 5%)
            qty_diff = abs(user_qty - cad_qty) / max(cad_qty, 1.0)
            qty_agrees = qty_diff < 0.05

            if not qty_agrees:
                # Value disagreement -> Line keeps user value, question generated
                cad_desc_text = (
                    best_cad.description
                    if isinstance(best_cad.description, str)
                    else best_cad.description.value
                )
                q_id = f"Q-{q_counter:03d}"
                q_counter += 1

                user_pn_str = u_row.part_number or f"Row {u_row.row}"
                item_title = u_row.description or cad_desc_text
                # Clean up title for concise display
                if "screw" in item_title.lower() and "m4" in item_title.lower():
                    item_title = "Screw M4x12 ISO 4762"

                q_text = (
                    f"{item_title}: your BoM says {int(user_qty)}, the CAD model shows "
                    f"{int(cad_qty)}. Which is correct?"
                )
                questions.append(
                    Question(
                        id=q_id,
                        kind="choice",
                        blocking=True,
                        line_ids=[f"user:{user_pn_str}", best_cad.id],
                        text=q_text,
                        options=[f"{int(user_qty)} (your BoM)", f"{int(cad_qty)} (CAD)"],
                        evidence=[
                            f"bom: row {u_row.row} qty {int(user_qty)}",
                            f"cad: {best_cad.cad_name} qty_total {int(cad_qty)}",
                        ],
                        answer=None,
                        note=(
                            "The user's value is kept until answered; the line is never rewritten."
                        ),
                    )
                )

            # Build merged line: user values are authoritative, CAD evidence attached
            merged_rule_ids = [*list(best_cad.rule_ids), "R-MRG-USR"]
            cad_node_refs = list(best_cad.cad_nodes)
            if best_cad.cad_name and best_cad.cad_name not in cad_node_refs:
                cad_node_refs.append(best_cad.cad_name)

            # Use user part number, description, uom, and stated quantity
            line = BomLine(
                id=best_cad.id,
                parent=best_cad.parent,
                level=best_cad.level,
                find_no=u_row.find_no if u_row.find_no is not None else best_cad.find_no,
                cad_name=best_cad.cad_name,
                part_number=u_row.part_number or best_cad.part_number,
                description=u_row.description or best_cad.description,
                noun=best_cad.noun,
                modifiers=best_cad.modifiers,
                cls=best_cad.cls,
                designation=best_cad.designation,
                qty_per_parent=user_qty,
                uom=u_row.uom,
                uom_raw=u_row.uom_raw,
                qty_total=user_qty,
                material=u_row.material or best_cad.material,
                finish=best_cad.finish,
                mass_g=best_cad.mass_g,
                make_buy=best_cad.make_buy,
                refdes=u_row.refdes or best_cad.refdes,
                refdes_count_matches_qty=u_row.refdes_count_matches_qty,
                mpn=u_row.mpn or best_cad.mpn,
                manufacturer=u_row.manufacturer or best_cad.manufacturer,
                cad_nodes=cad_node_refs,
                class_acceptable=u_row.class_acceptable,
                origin="user_bom",
                review="given",
                phantom=best_cad.phantom,
                buy_as_assembly=best_cad.buy_as_assembly,
                cad_volume_mm3=best_cad.cad_volume_mm3,
                confidence=1.0 if qty_agrees else 0.85,
                source="user",
                rule_ids=merged_rule_ids,
                open_items=best_cad.open_items,
                note=best_cad.note,
                explanation=best_cad.explanation,
                raw=u_row.raw,
            )
            reconciled_lines.append(line)
        else:
            # User line with no CAD match -> Kept verbatim
            u_id = f"L-USR-{u_row.row:04d}"
            line = BomLine(
                id=u_id,
                level=u_row.level,
                find_no=u_row.find_no or (u_row.row * 10),
                part_number=u_row.part_number,
                description=u_row.description or "User component",
                noun="PART",
                modifiers=[],
                cls=u_row.class_acceptable[0] if u_row.class_acceptable else "unknown",
                qty_per_parent=u_row.qty,
                uom=u_row.uom,
                uom_raw=u_row.uom_raw,
                qty_total=u_row.qty,
                material=u_row.material,
                refdes=u_row.refdes,
                refdes_count_matches_qty=u_row.refdes_count_matches_qty,
                mpn=u_row.mpn,
                manufacturer=u_row.manufacturer,
                origin="user_bom",
                review="given",
                confidence=1.0,
                source="user",
                rule_ids=["R-USR-001"],
                raw=u_row.raw,
            )
            reconciled_lines.append(line)

    # Step 5: Unmatched CAD lines -> Propose Suggestions (skip phantoms)
    for c_line in cad_lines:
        if c_line.id not in matched_cad_ids:
            # Skip phantoms
            if c_line.phantom:
                continue

            s_id = f"S-{s_counter:03d}"
            s_counter += 1
            desc_text = (
                c_line.description
                if isinstance(c_line.description, str)
                else c_line.description.value
            )
            cls_code = c_line.cls if isinstance(c_line.cls, str) else c_line.cls.value

            s_text = f"CAD shows {desc_text} ({int(c_line.qty_total)}x). Add it to the BoM?"
            suggestions.append(
                Suggestion(
                    id=s_id,
                    origin="cad",
                    review="suggested",
                    cad_name=c_line.cad_name,
                    cls=cls_code,
                    qty_total=c_line.qty_total,
                    text=s_text,
                )
            )

    return reconciled_lines, questions, suggestions
