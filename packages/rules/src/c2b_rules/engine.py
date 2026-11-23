"""Deterministic rules engine coordinator (docs/04 §1).

Assembles pipeline stages 3, 4, 5, 6, 7, 10, 11 into a complete draft EBOM + MBOM.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from c2b_domain.bom import (
    BomDraft,
    DatasheetSpec,
    PhotoFinding,
    Question,
    Suggestion,
    TableMapping,
)
from c2b_domain.cad import CadExtractResult
from c2b_domain.prov import Prov
from c2b_library.loader import Library
from c2b_rules.bom_intake import detect_and_map_bom
from c2b_rules.datasheets import extract_datasheet_specs
from c2b_rules.explainer import explain_line
from c2b_rules.gap import evaluate_gap_rules
from c2b_rules.makebuy import recommend_make_buy
from c2b_rules.mbom import derive_mbom
from c2b_rules.merger import merge_cad_and_user_bom
from c2b_rules.numbering import assign_part_numbers
from c2b_rules.structure import build_ebom_lines
from c2b_rules.validator import validate_draft
from c2b_rules.vision import process_photos


def _get_mass_float(val: Prov[float] | float | None) -> float:
    if val is None:
        return 0.0
    if isinstance(val, (int, float)):
        return float(val)
    if isinstance(val, Prov):
        return float(val.value)
    return 0.0


def build_ebom(
    extract: CadExtractResult | None,
    library: Library,
    job_id: str,
    profile_name: str = "default",
    bom_file_path: Path | str | None = None,
    datasheet_paths: list[Path | str] | None = None,
    photo_paths: list[Path | str] | None = None,
    mapping_overrides: dict[str, str] | None = None,
    photo_opt_in: bool = False,
    cassette_dir: Path | str | None = None,
    details_path: Path | str | None = None,
    product_desc: str = "",
    annual_volume: int | None = None,
    archetype: str | None = None,
    rejected_keys: set[str] | None = None,
    assign_numbers: bool = False,
) -> BomDraft:
    """Run deterministic rules pipeline on extracted CAD nodes and user inputs.

    Builds the draft EBOM with MBOM, questions, suggestions, and validation.
    """
    cad_lines: list[Any] = []
    if extract is not None:
        nodes = extract.nodes
        shapes = extract.shapes
        cad_lines = build_ebom_lines(
            nodes=nodes,
            shapes=shapes,
            library=library,
            profile_name=profile_name,
        )
        for line in cad_lines:
            line.explanation = explain_line(line)

    # Ingest Datasheets if present
    datasheet_specs: list[DatasheetSpec] = []
    if datasheet_paths:
        for dp in datasheet_paths:
            datasheet_specs.extend(extract_datasheet_specs(dp))

    # Ingest Photos if present and opted-in
    photo_findings: list[PhotoFinding] = []
    if photo_paths and photo_opt_in:
        photo_findings = process_photos(photo_paths, opt_in=True, cassette_dir=cassette_dir)

    # Ingest User BoM file if present
    mapping: TableMapping | None = None
    user_rows_raw: list[dict[str, Any]] = []
    questions: list[Question] = []
    suggestions: list[Suggestion] = []
    final_lines = cad_lines

    if bom_file_path is not None:
        mapping, user_rows = detect_and_map_bom(bom_file_path, library, overrides=mapping_overrides)
        user_rows_raw = [r.raw for r in user_rows]
        final_lines, questions, suggestions = merge_cad_and_user_bom(
            cad_lines=cad_lines,
            user_rows=user_rows,
            datasheet_specs=datasheet_specs,
            photo_findings=photo_findings,
            library=library,
        )
        # Ambiguous spreadsheet columns become blocking questions; L3 may propose an answer, but
        # only the user confirms it (docs/04 §9, CLAUDE.md rule 5).
        for col in mapping.ambiguous_columns:
            proposal = mapping.columns[col]
            next_id = 1 + max((int(q.id.split("-")[1]) for q in questions), default=0)
            questions.append(
                Question(
                    id=f"Q-{next_id:03d}",
                    kind="choice",
                    blocking=True,
                    mapping_column=col,
                    text=f"Column '{col}' could be more than one BoM field. Which field is it?",
                    options=list(library.headers.fields.keys()),
                    evidence=[proposal.why or "", f"rules propose: {proposal.field}"],
                    note="R-MAP-AMB",
                )
            )

    # Details text ingestion
    details_text = product_desc
    if details_path is not None:
        p = Path(details_path)
        if p.exists():
            details_text = p.read_text(encoding="utf-8").strip()

    if annual_volume is None and details_text:
        vol_m = re.search(r"annual volume\s+(\d+)", details_text, re.IGNORECASE)
        if vol_m:
            annual_volume = int(vol_m.group(1))

    # Blade-count conflict detection (CAD vs text)
    if details_text:
        blade_m = re.search(r"(\d+)\s+blades?", details_text, re.IGNORECASE)
        if blade_m:
            text_blade_count = int(blade_m.group(1))
            blade_line = next(
                (
                    ln
                    for ln in final_lines
                    if ln.cad_name == "Blade" or "blade" in str(ln.cls).lower()
                ),
                None,
            )
            if blade_line and int(blade_line.qty_total) != text_blade_count:
                q_id = f"Q-{len(questions) + 1:03d}"
                questions.append(
                    Question(
                        id=q_id,
                        kind="choice",
                        blocking=True,
                        line_ids=[blade_line.id],
                        text=(
                            f"Blade count: the CAD model shows {int(blade_line.qty_total)} blades "
                            f"but your description says {text_blade_count}. Which is correct?"
                        ),
                        options=[str(int(blade_line.qty_total)), str(text_blade_count)],
                        evidence=[
                            (
                                f"cad: BladeAssy x{int(blade_line.qty_total)} -> "
                                f"Blade x1 each (qty_total {int(blade_line.qty_total)})"
                            ),
                            f"text: '{text_blade_count} blades' (details.txt)",
                        ],
                        answer=None,
                    )
                )

    # Make/buy recommendations
    for line in final_lines:
        line.make_buy = recommend_make_buy(line, library, annual_volume=annual_volume)

    # Numbering (when requested or profile active)
    if assign_numbers:
        final_lines = assign_part_numbers(final_lines, library, profile_name=profile_name)

    # Gap rules
    if details_text:
        suggestions = evaluate_gap_rules(
            ebom=final_lines,
            library=library,
            product_desc=details_text,
            process_hints=None,
            rejected_keys=rejected_keys,
            existing_suggestions=suggestions,
            archetype=archetype,
        )

    # Derive MBOM
    mbom = derive_mbom(
        ebom=final_lines,
        library=library,
        accepted_suggestions=suggestions,
    )

    # Aggregate stats and open items
    total_mass = sum(_get_mass_float(line.mass_g) for line in final_lines)
    open_items: list[str] = []
    for line in final_lines:
        for oi in line.open_items:
            open_items.append(f"{line.id} ({line.cad_name}): {oi}")

    stats: dict[str, Any] = {
        "line_count": len(final_lines),
        "total_mass_g": round(total_mass, 4),
        "standard_part_count": sum(1 for line in final_lines if line.designation is not None),
        "open_item_count": len(open_items),
        "phantom_count": sum(1 for line in final_lines if line.phantom),
        "question_count": len(questions),
        "suggestion_count": len(suggestions),
    }

    draft = BomDraft(
        job_id=job_id,
        library_version=library.version.version,
        profile=profile_name,
        ebom=final_lines,
        mbom=mbom,
        stats=stats,
        open_items=open_items,
        questions=questions,
        suggestions=suggestions,
        mapping=mapping,
        user_rows_raw=user_rows_raw,
    )
    draft.validation = validate_draft(draft, library)
    return draft
