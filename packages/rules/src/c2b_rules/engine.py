"""Deterministic rules engine coordinator (docs/04 §1).

Assembles pipeline stages 3, 4, 5, 6, 7, and 11 into a complete draft EBOM.
"""

from pathlib import Path
from typing import Any

from c2b_domain.bom import BomDraft, DatasheetSpec, PhotoFinding, Question, Suggestion, TableMapping
from c2b_domain.cad import CadExtractResult
from c2b_domain.prov import Prov
from c2b_library.loader import Library
from c2b_rules.bom_intake import detect_and_map_bom
from c2b_rules.datasheets import extract_datasheet_specs
from c2b_rules.explainer import explain_line
from c2b_rules.merger import merge_cad_and_user_bom
from c2b_rules.structure import build_ebom_lines
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
    photo_opt_in: bool = False,
    cassette_dir: Path | str | None = None,
) -> BomDraft:
    """Run deterministic rules pipeline on extracted CAD nodes and user inputs.

    Builds the draft EBOM with questions and suggestions.
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
        mapping, user_rows = detect_and_map_bom(bom_file_path, library)
        user_rows_raw = [r.raw for r in user_rows]
        final_lines, questions, suggestions = merge_cad_and_user_bom(
            cad_lines=cad_lines,
            user_rows=user_rows,
            datasheet_specs=datasheet_specs,
            photo_findings=photo_findings,
            library=library,
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

    return BomDraft(
        job_id=job_id,
        library_version=library.version.version,
        profile=profile_name,
        ebom=final_lines,
        mbom=[],
        stats=stats,
        open_items=open_items,
        questions=questions,
        suggestions=suggestions,
        mapping=mapping,
        user_rows_raw=user_rows_raw,
    )
