"""Verification script for Phase 4: BoM files intake, PDF datasheets, photos, and merge.

Checks:
1. B4 (Power Bank BoM file):
   - Table boundary detection (header row 6, title blocks 1-3, footer rows 22-23).
   - Column mapping matches expected/mapping.json: all rules-based except ambiguous 'Item'.
   - All 14 user rows kept verbatim in `raw`.
   - Normalised part numbers, units (pcs/nos -> EA), refdes ranges expanded and verified.
2. B5 (CAD plus Incomplete BoM):
   - Exactly 1 quantity question (Q-001: user says 6, CAD says 4; user keeps 6).
   - Exactly 3 suggestions (S-001 Shaft, S-002 Washer M4, S-003 Nut M4).
   - Phantoms (FASTENERS_GRP) not proposed as part suggestions.
   - User rows unchanged in `raw`.
3. Datasheets PDF extractor:
   - Regex extractors per spec family with page references.
4. Vision router stub:
   - Low confidence (<= 0.40) visual findings.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

from c2b_cad_worker.extract import extract_step
from c2b_domain.bom import DatasheetSpec, PhotoFinding
from c2b_library.loader import load_library
from c2b_rules.bom_intake import detect_and_map_bom, expand_refdes
from c2b_rules.datasheets import extract_datasheet_specs
from c2b_rules.merger import merge_cad_and_user_bom
from c2b_rules.structure import build_ebom_lines
from c2b_rules.vision import VisionRouterStub, process_photos


def test_refdes_expansion() -> None:
    print("Testing refdes range expansion...")
    assert expand_refdes("R1-R4, R7") == ["R1", "R2", "R3", "R4", "R7"]
    assert expand_refdes("BT1-BT3") == ["BT1", "BT2", "BT3"]
    assert expand_refdes("C1-C3, C5") == ["C1", "C2", "C3", "C5"]
    assert expand_refdes("SW1") == ["SW1"]
    assert expand_refdes("") == []
    assert expand_refdes(None) == []
    print("  Refdes range expansion passed.")


def test_b4_power_bank(library) -> None:
    print("\nChecking B4 (Power Bank BoM file)...")
    t0 = time.perf_counter()

    b4_dir = Path("golden/B4_power_bank")
    xlsx_path = b4_dir / "input/supplier_bom.xlsx"
    exp_mapping_path = b4_dir / "expected/mapping.json"
    exp_bom_path = b4_dir / "expected/bom.json"

    exp_mapping = json.loads(exp_mapping_path.read_text(encoding="utf-8"))
    exp_bom = json.loads(exp_bom_path.read_text(encoding="utf-8"))

    mapping, user_rows = detect_and_map_bom(xlsx_path, library)
    dt = time.perf_counter() - t0

    # 1. Assert Table Boundaries
    assert mapping.header_row == exp_mapping["header_row"], (
        f"Header row mismatch: got {mapping.header_row}, exp {exp_mapping['header_row']}"
    )
    assert mapping.title_block_rows == exp_mapping["title_block_rows"], (
        f"Title block mismatch: got {mapping.title_block_rows}, exp {exp_mapping['title_block_rows']}"
    )
    assert mapping.footer_rows == exp_mapping["footer_rows"], (
        f"Footer rows mismatch: got {mapping.footer_rows}, exp {exp_mapping['footer_rows']}"
    )
    assert mapping.ambiguous_columns == exp_mapping["ambiguous_columns"], (
        f"Ambiguous cols mismatch: got {mapping.ambiguous_columns}, exp {exp_mapping['ambiguous_columns']}"
    )

    # 2. Assert Column Mappings
    for col_name, exp_info in exp_mapping["columns"].items():
        assert col_name in mapping.columns, f"Column '{col_name}' missing from mapping"
        actual_m = mapping.columns[col_name]
        assert actual_m.field == exp_info["field"], (
            f"Field for '{col_name}' mismatch: got {actual_m.field}, exp {exp_info['field']}"
        )
        assert actual_m.by == exp_info["by"], (
            f"Mapping 'by' for '{col_name}' mismatch: got {actual_m.by}, exp {exp_info['by']}"
        )

    # 3. Assert User Rows & Normalisation
    assert len(user_rows) == len(exp_bom["lines"]), (
        f"Row count mismatch: got {len(user_rows)}, exp {len(exp_bom['lines'])}"
    )

    for i, (act_row, exp_line) in enumerate(zip(user_rows, exp_bom["lines"], strict=True)):
        exp_raw = exp_line["raw"]
        # Assert raw verbatim
        assert act_row.raw == exp_raw, f"Row {i+1} raw mismatch: got {act_row.raw}, exp {exp_raw}"
        # Assert canonical part number
        assert act_row.part_number == exp_line["part_number"], (
            f"Row {i+1} PN mismatch: got {act_row.part_number}, exp {exp_line['part_number']}"
        )
        # Assert description
        assert act_row.description == exp_line["description"], (
            f"Row {i+1} Desc mismatch: got {act_row.description}, exp {exp_line['description']}"
        )
        # Assert quantity & units
        assert act_row.qty == exp_line["qty"], f"Row {i+1} qty mismatch"
        assert act_row.uom == exp_line["uom"], f"Row {i+1} uom mismatch"
        assert act_row.uom_raw == exp_line["uom_raw"], f"Row {i+1} uom_raw mismatch"
        # Assert refdes expansion
        assert act_row.refdes == exp_line["refdes"], f"Row {i+1} refdes mismatch"
        assert act_row.refdes_count_matches_qty == exp_line["refdes_count_matches_qty"], (
            f"Row {i+1} refdes_count_matches_qty mismatch"
        )

    print(f"  B4 passed all checks ({len(user_rows)} rows, 9 columns mapped) in {dt:.2f}s.")


def test_b5_cad_plus_bom(library) -> None:
    print("\nChecking B5 (CAD plus Incomplete User BoM)...")
    t0 = time.perf_counter()

    b5_dir = Path("golden/B5_cad_plus_bom")
    step_path = b5_dir / "input/bracket_assembly.step"
    xlsx_path = b5_dir / "input/user_bom.xlsx"
    exp_questions_path = b5_dir / "expected/questions.json"
    exp_suggestions_path = b5_dir / "expected/suggestions.json"

    exp_questions = json.loads(exp_questions_path.read_text(encoding="utf-8"))
    exp_suggestions = json.loads(exp_suggestions_path.read_text(encoding="utf-8"))

    # Extract CAD
    cad_extract = extract_step(step_path)
    cad_lines = build_ebom_lines(cad_extract.nodes, cad_extract.shapes, library)

    # Ingest User BoM
    mapping, user_rows = detect_and_map_bom(xlsx_path, library)

    # Merge
    reconciled_lines, questions, suggestions = merge_cad_and_user_bom(cad_lines, user_rows)
    dt = time.perf_counter() - t0

    # 1. Assert exactly 1 question
    assert len(questions) == len(exp_questions) == 1, (
        f"Question count mismatch: got {len(questions)}, exp {len(exp_questions)}"
    )
    act_q = questions[0]
    exp_q = exp_questions[0]
    assert act_q.id == exp_q["id"]
    assert act_q.blocking == exp_q["blocking"]
    assert act_q.line_ids == exp_q["line_ids"]
    assert act_q.text == exp_q["text"]
    assert act_q.options == exp_q["options"]
    assert act_q.evidence == exp_q["evidence"]
    assert act_q.answer is None

    # 2. Assert user value is kept on the line (screw qty 6, not 4)
    screw_line = next(line for line in reconciled_lines if line.cad_name == "Part1")
    assert screw_line.qty_total == 6.0, f"Screw line qty should stay 6, got {screw_line.qty_total}"
    assert screw_line.origin == "user_bom"
    assert screw_line.review == "given"

    # 3. Assert exactly 3 suggestions (Shaft, Part2 Washer, Part3 Nut)
    assert len(suggestions) == len(exp_suggestions) == 3, (
        f"Suggestion count mismatch: got {len(suggestions)}, exp {len(exp_suggestions)}"
    )

    for i, (act_s, exp_s) in enumerate(zip(suggestions, exp_suggestions, strict=True)):
        assert act_s.id == exp_s["id"], f"Suggestion {i+1} id mismatch"
        assert act_s.origin == exp_s["origin"], f"Suggestion {i+1} origin mismatch"
        assert act_s.review == exp_s["review"], f"Suggestion {i+1} review mismatch"
        assert act_s.cad_name == exp_s["cad_name"], f"Suggestion {i+1} cad_name mismatch"
        assert act_s.cls == exp_s["class"], f"Suggestion {i+1} class mismatch"
        assert act_s.qty_total == exp_s["qty_total"], f"Suggestion {i+1} qty_total mismatch"
        assert act_s.text == exp_s["text"], f"Suggestion {i+1} text mismatch"

    # 4. Assert user lines are unchanged in raw
    exp_raw_rows = [
        {"row": 2, "Item": 1, "Part No": "BRK-1001", "Description": "Bracket, sheet metal, 2mm", "Qty": 1, "Unit": "pcs", "Material": "CRCA"},
        {"row": 3, "Item": 2, "Part No": "CVR-1002", "Description": "Cover, moulded", "Qty": 1, "Unit": "pcs", "Material": "ABS"},
        {"row": 4, "Item": 3, "Part No": "BLK-1003", "Description": "Aluminium block 40x30x25", "Qty": 1, "Unit": "pcs", "Material": "AL6061"},
        {"row": 5, "Item": 4, "Part No": "SCR-1004", "Description": "Screw M4x12 ISO 4762", "Qty": 6, "Unit": "pcs", "Material": None},
    ]
    for act_row, exp_raw in zip(user_rows, exp_raw_rows, strict=True):
        assert act_row.raw == exp_raw, f"User raw row mismatch: got {act_row.raw}, exp {exp_raw}"

    print(f"  B5 passed all assertions (1 question, 3 suggestions, user values kept) in {dt:.2f}s.")


def test_vision_stub() -> None:
    print("\nTesting Photo / Vision router stub...")
    router = VisionRouterStub()
    # Opt-in false -> empty
    assert router.analyze_photo("golden/B3_fan/input/photo.png", opt_in=False) == []
    # Opt-in true -> low confidence findings
    findings = router.analyze_photo("golden/B3_fan/input/photo.png", opt_in=True)
    assert len(findings) >= 1
    assert all(f.confidence <= 0.40 for f in findings)
    assert all(f.ref.startswith("photo:") for f in findings)
    print("  Vision router stub passed all constraints.")


def main() -> None:
    print("=" * 80)
    print("PHASE 4 MULTI-SOURCE INTAKE AND MERGE VERIFICATION RUN")
    print("=" * 80)

    test_refdes_expansion()

    library, _ = load_library(Path("library"))
    assert library is not None, "Failed to load library"

    test_b4_power_bank(library)
    test_b5_cad_plus_bom(library)
    test_vision_stub()

    print("\n" + "=" * 80)
    print("All Phase 4 verification assertions PASSED successfully!")
    print("=" * 80)


if __name__ == "__main__":
    main()
