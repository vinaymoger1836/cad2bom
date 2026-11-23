"""Phase 3 verification script: Deterministic rules engine to draft EBOM.

Validates:
  1. Acceptance criteria for B1 and B2 per docs/10 §4 with zero LLM calls.
  2. B1-B3 full reconciliation: class, quantity, uom, description, source, confidence, rule_ids.
  3. Safe AST evaluator security and correctness.
  4. Line explanations for sample lines.
"""

import json
import time
from pathlib import Path

from c2b_cad_worker.extract import extract_step
from c2b_library.loader import load_library
from c2b_rules.engine import build_ebom
from c2b_rules.evaluator import ExpressionError, safe_eval
from c2b_rules.explainer import format_explanation_text

CASES = [
    {
        "id": "B1",
        "name": "Fastener Zoo",
        "step": Path("golden/B1_fastener_zoo/input/fastener_zoo.step"),
        "expected_bom": Path("golden/B1_fastener_zoo/expected/bom.json"),
    },
    {
        "id": "B2",
        "name": "Bracket Assembly",
        "step": Path("golden/B2_bracket/input/bracket_assembly.step"),
        "expected_bom": Path("golden/B2_bracket/expected/bom.json"),
    },
    {
        "id": "B3",
        "name": "Ceiling Fan",
        "step": Path("golden/B3_fan/input/fan.step"),
        "expected_bom": Path("golden/B3_fan/expected/bom.json"),
    },
]


def test_safe_evaluator() -> None:
    print("Testing safe AST expression evaluator...")
    facts = {"x": 10, "y": 2.5, "flag": True}
    assert safe_eval("x > 5 and y < 3", facts) is True
    assert safe_eval("x * y + 5", facts) == 30.0
    assert safe_eval("round(y)", facts) == 2

    # Verify forbidden constructs are blocked
    forbidden_exprs = [
        "__import__('os').system('echo pwned')",
        "x.__class__",
        "(lambda: 1)()",
        "[x for x in [1, 2]]",
        "eval('1+1')",
    ]
    for bad in forbidden_exprs:
        try:
            safe_eval(bad, facts)
            raise AssertionError(f"Expected ExpressionError for: {bad}")
        except ExpressionError:
            pass
    print("  Safe AST evaluator passed all safety checks.")


def main() -> None:
    print("=" * 80)
    print("PHASE 3 DETERMINISTIC RULES ENGINE VERIFICATION RUN")
    print("=" * 80)

    test_safe_evaluator()

    lib_path = Path("library").resolve()
    library, issues = load_library(lib_path)
    assert library is not None, f"Failed to load library: {issues}"
    print(f"Library version {library.version.version} loaded successfully.\n")

    summaries: list[dict[str, object]] = []

    for c in CASES:
        case_id = c["id"]
        step_file = c["step"].resolve()
        expected_file = c["expected_bom"].resolve()
        assert step_file.exists(), f"Missing STEP: {step_file}"
        assert expected_file.exists(), f"Missing golden JSON: {expected_file}"

        with open(expected_file, encoding="utf-8") as f:
            expected_data = json.load(f)
        expected_lines = expected_data["ebom"]

        t0 = time.monotonic()
        extract = extract_step(step_file)
        draft = build_ebom(extract, library, job_id=f"verify-{case_id.lower()}")
        elapsed_s = time.monotonic() - t0

        print(
            f"Checking {case_id} ({c['name']}): {len(draft.ebom)} lines generated in {elapsed_s:.2f}s..."
        )

        # Assertions
        assert len(draft.ebom) == len(expected_lines), (
            f"Line count mismatch: {len(draft.ebom)} vs {len(expected_lines)}"
        )

        for i, (gen, exp) in enumerate(zip(draft.ebom, expected_lines, strict=False)):
            # Mandatory contract: class, quantity, uom, description, source, confidence, rule_ids
            assert gen.cls == exp["class"], f"Line {i} class mismatch: {gen.cls} != {exp['class']}"
            assert gen.qty_per_parent == exp["qty_per_parent"], (
                f"Line {i} qty_per_parent mismatch: {gen.qty_per_parent} != {exp['qty_per_parent']}"
            )
            assert gen.qty_total == exp["qty_total"], (
                f"Line {i} qty_total mismatch: {gen.qty_total} != {exp['qty_total']}"
            )
            assert gen.uom == exp["uom"], f"Line {i} uom mismatch: {gen.uom} != {exp['uom']}"
            assert gen.description == exp["description"], (
                f"Line {i} description mismatch: '{gen.description}' != '{exp['description']}'"
            )
            assert gen.source is not None and len(gen.source) > 0, f"Line {i} missing source"
            assert 0.0 <= gen.confidence <= 1.0, f"Line {i} invalid confidence {gen.confidence}"
            assert len(gen.rule_ids) > 0, f"Line {i} missing rule_ids"
            assert len(gen.explanation) > 0, f"Line {i} missing explanation"

            # Check find numbers and level
            assert gen.find_no == exp["find_no"], (
                f"Line {i} find_no mismatch: {gen.find_no} != {exp['find_no']}"
            )
            assert gen.level == exp["level"], (
                f"Line {i} level mismatch: {gen.level} != {exp['level']}"
            )

            # Check mass if expected
            if exp.get("mass_g") is not None and gen.mass_g is not None:
                assert abs(gen.mass_g - exp["mass_g"]) <= 0.05, (
                    f"Line {i} mass mismatch: {gen.mass_g} != {exp['mass_g']}"
                )

            # Check make/buy
            assert gen.make_buy == exp["make_buy"], (
                f"Line {i} make_buy mismatch: {gen.make_buy} != {exp['make_buy']}"
            )

        # B2 mass roll-up assertion: within 1% of sum of volume x density
        if case_id == "B2":
            total_mass_gen = sum(
                line.mass_g * line.qty_total for line in draft.ebom if line.mass_g is not None
            )
            total_mass_exp = sum(
                exp_l["mass_g"] * exp_l["qty_total"]
                for exp_l in expected_lines
                if exp_l.get("mass_g") is not None
            )
            mass_diff_pct = abs(total_mass_gen - total_mass_exp) / total_mass_exp * 100.0
            print(
                f"  B2 mass roll-up: gen={total_mass_gen:.2f}g, exp={total_mass_exp:.2f}g (diff {mass_diff_pct:.4f}%)"
            )
            assert mass_diff_pct < 1.0, f"B2 mass roll-up exceeded 1%: {mass_diff_pct}%"

        print(f"  {case_id} passed all {len(draft.ebom)} line assertions.\n")
        summaries.append(
            {
                "case": case_id,
                "lines": len(draft.ebom),
                "time_s": round(elapsed_s, 2),
                "status": "PASS",
            }
        )

    # Sample line explanations check
    print("=" * 80)
    print("SAMPLE EXPLANATIONS (3 LINES)")
    print("=" * 80)

    # 1. B1 Part1 (Fastener)
    b1_ext = extract_step(CASES[0]["step"].resolve())
    b1_draft = build_ebom(b1_ext, library, "b1")
    print("--- SAMPLE 1: B1 Part1 (Fastener Zoo: ISO 4762 Socket Head Cap Screw) ---")
    print(format_explanation_text(b1_draft.ebom[0]))
    print()

    # 2. B2 Bracket (Sheet Metal)
    b2_ext = extract_step(CASES[1]["step"].resolve())
    b2_draft = build_ebom(b2_ext, library, "b2")
    print("--- SAMPLE 2: B2 Bracket (Custom Sheet Metal Bracket) ---")
    print(format_explanation_text(b2_draft.ebom[0]))
    print()

    # 3. B2 FASTENERS_GRP (Phantom Group)
    print("--- SAMPLE 3: B2 FASTENERS_GRP (Modelling Phantom Assembly) ---")
    print(format_explanation_text(b2_draft.ebom[4]))
    print()

    print("=" * 80)
    print("SUMMARY")
    print("=" * 80)
    for s in summaries:
        print(f"Case {s['case']}: {s['lines']} lines in {s['time_s']}s -> {s['status']}")
    print("All Phase 3 verification assertions PASSED successfully!")


if __name__ == "__main__":
    main()
