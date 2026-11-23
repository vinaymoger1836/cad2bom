"""Phase 6 verification script: Gap rules, MBOM, make/buy, numbering, descriptions, validation, approval.

Acceptance criteria (docs/10 §4, docs/10 §5 P6):
1. B3 gap suggestions match golden/B3_fan/expected/suggestions.json exactly (4 suggestions: rating label, manual, carton, solder).
   - MBOM matches golden/B3_fan/expected/bom.json (10 lines, M-0001..M-0010, BladeAssy subassembly, LightKit buy-as-assembly).
2. B5 and B6 approval blocked initially due to open questions/suggestions, then succeeds when resolved.
3. Determinism: Re-running B1-B6 with the same inputs and cassettes gives byte-identical JSON (excluding timestamps).
4. Partial re-run on user edit:
   - Line edit gets source='user', confidence=1.0, and is never overwritten.
   - Rejected suggestion is stored in rejected_suggestion_keys and never re-suggested.
"""

from __future__ import annotations

import os
import warnings

warnings.filterwarnings("ignore", category=UserWarning)

os.environ["LITELLM_LOCAL_MODEL_COST_MAP"] = "True"

import asyncio
import json
import time
from pathlib import Path
from typing import Any

from c2b_agents.pipeline import resolve_draft_with_ladder
from c2b_agents.router import LlmRouter
from c2b_cad_worker.extract import extract_step
from c2b_domain.bom import BomVersion
from c2b_library.loader import load_library
from c2b_rules.dependency import (
    apply_question_answer,
    apply_suggestion_review,
    apply_user_line_edit,
    recalculate_draft,
)
from c2b_rules.engine import build_ebom
from c2b_rules.gap import evaluate_gap_rules
from c2b_rules.validator import validate_draft


def test_b3_gap_suggestions_and_mbom(library) -> None:
    """Assert B3 gap suggestions and MBOM match expected contract."""
    print("\n--- Verifying B3 Gap Suggestions and MBOM ---", flush=True)

    b3_step = Path("golden/B3_fan/input/fan.step")
    b3_details = Path("golden/B3_fan/input/details.txt")
    b3_photo = Path("golden/B3_fan/input/photo.png")

    b3_cad = extract_step(b3_step)
    b3_draft = build_ebom(
        b3_cad,
        library,
        "B3",
        details_path=b3_details,
        photo_paths=[b3_photo],
        assign_numbers=True,
    )

    # 1. Check suggestions against golden/B3_fan/expected/suggestions.json
    exp_sugg_path = Path("golden/B3_fan/expected/suggestions.json")
    exp_suggs = json.loads(exp_sugg_path.read_text(encoding="utf-8"))

    assert len(b3_draft.suggestions) == len(exp_suggs), (
        f"Expected {len(exp_suggs)} suggestions, got {len(b3_draft.suggestions)}"
    )

    for act_s, exp_s in zip(b3_draft.suggestions, exp_suggs):
        assert act_s.id == exp_s["id"], f"Suggestion id mismatch: {act_s.id} vs {exp_s['id']}"
        act_cls = act_s.cls
        assert act_cls == exp_s["class"], (
            f"Suggestion class mismatch: {act_cls} vs {exp_s['class']}"
        )
        assert act_s.rule == exp_s["rule"], f"Rule mismatch: {act_s.rule} vs {exp_s['rule']}"
        assert act_s.origin == exp_s["origin"], (
            f"Origin mismatch: {act_s.origin} vs {exp_s['origin']}"
        )
        assert act_s.level == exp_s["level"], f"Level mismatch: {act_s.level} vs {exp_s['level']}"
        if exp_s["qty"] is not None:
            assert act_s.qty_total == exp_s["qty"], (
                f"Qty mismatch: {act_s.qty_total} vs {exp_s['qty']}"
            )
        assert act_s.review == exp_s["review"]

    print(
        f"  B3 suggestions verified: exactly {len(b3_draft.suggestions)} gap suggestions match expected.",
        flush=True,
    )

    # 2. Check MBOM against golden/B3_fan/expected/bom.json
    exp_bom_path = Path("golden/B3_fan/expected/bom.json")
    exp_bom = json.loads(exp_bom_path.read_text(encoding="utf-8"))
    exp_mbom = exp_bom["mbom"]

    assert len(b3_draft.mbom) == len(exp_mbom), (
        f"Expected {len(exp_mbom)} MBOM lines, got {len(b3_draft.mbom)}"
    )

    for act_m, exp_m in zip(b3_draft.mbom, exp_mbom):
        assert act_m.id == exp_m["id"], f"MBOM id mismatch: {act_m.id} vs {exp_m['id']}"
        assert act_m.parent == exp_m["parent"], f"MBOM parent mismatch for {act_m.id}"
        assert act_m.ebom_refs == exp_m["ebom_refs"], f"MBOM ebom_refs mismatch for {act_m.id}"
        act_cls = act_m.cls if isinstance(act_m.cls, str) else act_m.cls.value
        assert act_cls == exp_m["class"], f"MBOM class mismatch: {act_cls} vs {exp_m['class']}"
        assert act_m.qty_per_parent == exp_m["qty_per_parent"], (
            f"MBOM qty_per_parent mismatch for {act_m.id}"
        )
        assert act_m.qty_total == exp_m["qty_total"], f"MBOM qty_total mismatch for {act_m.id}"
        act_mb = act_m.make_buy if isinstance(act_m.make_buy, str) else act_m.make_buy.value
        assert act_mb == exp_m["make_buy"], (
            f"MBOM make_buy mismatch: {act_mb} vs {exp_m['make_buy']}"
        )
        assert act_m.find_no == exp_m["find_no"], f"MBOM find_no mismatch for {act_m.id}"

    print(
        f"  B3 MBOM verified: exactly {len(b3_draft.mbom)} MBOM lines match expected.", flush=True
    )


def test_b5_approval_blocking_and_success(library) -> None:
    """Assert B5 approval is blocked initially, then succeeds once question and suggestions resolved."""
    print("\n--- Verifying B5 Approval Blocking and Release ---", flush=True)

    b5_step = Path("golden/B5_cad_plus_bom/input/bracket_assembly.step")
    b5_xlsx = Path("golden/B5_cad_plus_bom/input/user_bom.xlsx")

    b5_cad = extract_step(b5_step)
    b5_draft = build_ebom(
        b5_cad,
        library,
        "B5",
        bom_file_path=b5_xlsx,
        assign_numbers=True,
    )

    # 1. Assert initial state is blocked
    val_initial = validate_draft(b5_draft, library)
    assert not val_initial.valid, "B5 should initially be blocked from approval"
    assert any(i.code == "unresolved_question" for i in val_initial.blocking_issues), (
        "B5 should have unresolved_question blocking issue"
    )
    assert any(i.code == "open_suggestion" for i in val_initial.blocking_issues), (
        "B5 should have open_suggestion blocking issue"
    )
    print(
        f"  Initial validation: BLOCKED with {len(val_initial.blocking_issues)} issues (expected).",
        flush=True,
    )

    # 2. Answer question Q-001 (e.g. choose 4)
    q_id = b5_draft.questions[0].id
    b5_draft = apply_question_answer(b5_draft, q_id, "4 (CAD)", library)
    assert b5_draft.questions[0].answer == "4 (CAD)"

    # Check validation still blocked by suggestions
    val_after_q = validate_draft(b5_draft, library)
    assert not val_after_q.valid, "B5 should still be blocked while suggestions are open"

    # 3. Review all suggestions
    # Accept S-001, reject S-002, accept S-003
    for i, sug in enumerate(b5_draft.suggestions):
        decision = "rejected" if i == 1 else "accepted"
        b5_draft = apply_suggestion_review(b5_draft, sug.id, decision, library)

    # 4. Assert validation now succeeds
    val_after_review = validate_draft(b5_draft, library)
    assert val_after_review.valid, (
        f"B5 should now be valid for approval, but got blocking issues: {val_after_review.blocking_issues}"
    )

    # 5. Freeze BomVersion
    version = BomVersion(
        job_id="B5",
        version=1,
        ebom=b5_draft.ebom,
        mbom=b5_draft.mbom,
        open_items=b5_draft.open_items,
        stats=b5_draft.stats,
        library_version=library.version.version,
    )
    assert version.version == 1
    assert len(version.ebom) > 0
    assert len(version.mbom) > 0
    print(
        f"  B5 Approval Release: SUCCESS (version {version.version} frozen with {len(version.ebom)} EBOM lines).",
        flush=True,
    )


def test_b6_approval_blocking_and_success(library) -> None:
    """Assert B6 approval is blocked initially by gap suggestions, then succeeds when resolved."""
    print("\n--- Verifying B6 Approval Blocking and Release ---", flush=True)

    router = LlmRouter()
    b6_step = Path("golden/B6_unknown/input/unknown_product.step")
    details_path = Path("golden/B6_unknown/input/details.txt")
    product_desc = details_path.read_text(encoding="utf-8").strip()

    b6_cad = extract_step(b6_step)
    b6_draft = build_ebom(b6_cad, library, "B6", details_path=details_path, assign_numbers=True)

    b6_draft = asyncio.run(
        resolve_draft_with_ladder(
            b6_draft,
            library,
            router,
            extract=b6_cad,
            product_desc=product_desc,
            run_l4_gap=True,
        )
    )

    # 1. Assert initial validation is blocked by open suggestions
    assert len(b6_draft.suggestions) > 0
    val_initial = validate_draft(b6_draft, library)
    assert not val_initial.valid, "B6 should initially be blocked by open gap suggestions"
    assert any(i.code == "open_suggestion" for i in val_initial.blocking_issues)
    print(
        f"  B6 initial validation: BLOCKED with {len(val_initial.blocking_issues)} issues (expected).",
        flush=True,
    )

    # 2. Review all suggestions
    for sug in b6_draft.suggestions:
        b6_draft = apply_suggestion_review(b6_draft, sug.id, "rejected", library)

    # 3. Assert validation now passes
    val_resolved = validate_draft(b6_draft, library)
    assert val_resolved.valid, (
        f"B6 should now be valid for approval, but got: {val_resolved.blocking_issues}"
    )

    # 4. Freeze BomVersion
    version = BomVersion(
        job_id="B6",
        version=1,
        ebom=b6_draft.ebom,
        mbom=b6_draft.mbom,
        open_items=b6_draft.open_items,
        stats=b6_draft.stats,
        library_version=library.version.version,
    )
    assert version.version == 1
    print(f"  B6 Approval Release: SUCCESS (version {version.version} frozen).", flush=True)


def test_determinism_b1_to_b6(library) -> None:
    """Assert re-running B1-B6 produces byte-identical JSON outputs."""
    print("\n--- Verifying Determinism Across B1-B6 ---", flush=True)

    def run_suite() -> dict[str, Any]:
        results: dict[str, Any] = {}

        # B1
        b1_step = Path("golden/B1_fastener_zoo/input/fastener_zoo.step")
        b1_cad = extract_step(b1_step)
        b1_draft = build_ebom(b1_cad, library, "B1", assign_numbers=True)
        results["B1"] = b1_draft.model_dump(mode="json", by_alias=True)

        # B2
        b2_step = Path("golden/B2_bracket/input/bracket_assembly.step")
        b2_cad = extract_step(b2_step)
        b2_draft = build_ebom(b2_cad, library, "B2", assign_numbers=True)
        results["B2"] = b2_draft.model_dump(mode="json", by_alias=True)

        # B3
        b3_step = Path("golden/B3_fan/input/fan.step")
        b3_cad = extract_step(b3_step)
        b3_draft = build_ebom(
            b3_cad,
            library,
            "B3",
            details_path=Path("golden/B3_fan/input/details.txt"),
            photo_paths=[Path("golden/B3_fan/input/photo.png")],
            assign_numbers=True,
        )
        results["B3"] = b3_draft.model_dump(mode="json", by_alias=True)

        # B4
        b4_xlsx = Path("golden/B4_power_bank/input/supplier_bom.xlsx")
        b4_draft = build_ebom(None, library, "B4", bom_file_path=b4_xlsx, assign_numbers=True)
        r4 = LlmRouter()
        b4_draft = asyncio.run(
            resolve_draft_with_ladder(
                b4_draft,
                library,
                r4,
                extract=None,
                product_desc="Power bank assembly with Li-ion battery",
                confirm_l3_immediately=True,
            )
        )
        results["B4"] = b4_draft.model_dump(mode="json", by_alias=True)

        # B5
        b5_step = Path("golden/B5_cad_plus_bom/input/bracket_assembly.step")
        b5_xlsx = Path("golden/B5_cad_plus_bom/input/user_bom.xlsx")
        b5_cad = extract_step(b5_step)
        b5_draft = build_ebom(
            b5_cad,
            library,
            "B5",
            bom_file_path=b5_xlsx,
            assign_numbers=True,
        )
        results["B5"] = b5_draft.model_dump(mode="json", by_alias=True)

        # B6
        b6_step = Path("golden/B6_unknown/input/unknown_product.step")
        b6_details = Path("golden/B6_unknown/input/details.txt")
        b6_cad = extract_step(b6_step)
        b6_draft = build_ebom(b6_cad, library, "B6", details_path=b6_details, assign_numbers=True)
        r6 = LlmRouter()
        b6_draft = asyncio.run(
            resolve_draft_with_ladder(
                b6_draft,
                library,
                r6,
                extract=b6_cad,
                product_desc=b6_details.read_text(encoding="utf-8").strip(),
                run_l4_gap=True,
            )
        )
        results["B6"] = b6_draft.model_dump(mode="json", by_alias=True)

        return results

    def strip_dynamic_fields(obj: Any) -> Any:
        """Strip timestamps and dynamic call-log timestamps if any."""
        if isinstance(obj, dict):
            return {
                k: strip_dynamic_fields(v)
                for k, v in obj.items()
                if k not in ("created_at", "updated_at", "timestamp", "elapsed_seconds")
            }
        elif isinstance(obj, list):
            return [strip_dynamic_fields(item) for item in obj]
        return obj

    print("  Running pass 1 of B1-B6...", flush=True)
    pass1 = strip_dynamic_fields(run_suite())
    print("  Running pass 2 of B1-B6...", flush=True)
    pass2 = strip_dynamic_fields(run_suite())

    for case_id in ("B1", "B2", "B3", "B4", "B5", "B6"):
        json1 = json.dumps(pass1[case_id], sort_keys=True, indent=2)
        json2 = json.dumps(pass2[case_id], sort_keys=True, indent=2)
        assert json1 == json2, f"Case {case_id} produced non-deterministic output between runs!"
        print(f"  {case_id}: byte-identical output verified ({len(json1)} bytes).", flush=True)

    print("  Determinism verified across all cases: PASS", flush=True)


def test_partial_rerun_and_user_edit_persistence(library) -> None:
    """Assert user edits get source='user', confidence=1.0, and rejected suggestions are never re-suggested."""
    print("\n--- Verifying Partial Re-run and User Edit Persistence ---", flush=True)

    b2_step = Path("golden/B2_bracket/input/bracket_assembly.step")
    b2_cad = extract_step(b2_step)
    draft = build_ebom(b2_cad, library, "B2", assign_numbers=True)

    # 1. Edit a line: change Part1 (screw) material to stainless
    target_line = draft.ebom[0]
    line_id = target_line.id
    draft = apply_user_line_edit(draft, line_id, "material", "stainless_304", library)

    updated_line = next(l for l in draft.ebom if l.id == line_id)
    assert updated_line.material.value == "stainless_304"
    assert updated_line.material.source == "user"
    assert updated_line.material.confidence == 1.0
    print(
        f"  User edit applied: {line_id} material='{updated_line.material.value}', source='{updated_line.material.source}', conf={updated_line.material.confidence}",
        flush=True,
    )

    # 2. Check that re-running recalculate_draft does NOT overwrite user edit
    recalculate_draft(draft, library)
    post_recalc_line = next(l for l in draft.ebom if l.id == line_id)
    assert post_recalc_line.material.value == "stainless_304"
    assert post_recalc_line.material.source == "user"
    assert post_recalc_line.material.confidence == 1.0
    print("  User edit preserved after recalculate_draft: PASS", flush=True)

    # 3. Test suggestion rejection permanence
    b3_step = Path("golden/B3_fan/input/fan.step")
    b3_cad = extract_step(b3_step)
    b3_draft = build_ebom(
        b3_cad,
        library,
        "B3",
        details_path=Path("golden/B3_fan/input/details.txt"),
        assign_numbers=True,
    )
    sug_count_before = len(b3_draft.suggestions)
    assert sug_count_before == 4

    # Reject S-004 (solder)
    b3_draft = apply_suggestion_review(b3_draft, "S-004", "rejected", library)
    assert "R-GAP-021:consumable.solder" in b3_draft.rejected_suggestion_keys

    # Re-evaluate gap rules passing rejected_keys
    fresh_suggestions = evaluate_gap_rules(
        ebom=b3_draft.ebom,
        library=library,
        product_desc=Path("golden/B3_fan/input/details.txt").read_text(encoding="utf-8"),
        rejected_keys=b3_draft.rejected_suggestion_keys,
    )
    assert not any(s.cls == "consumable.solder" for s in fresh_suggestions), (
        "Rejected suggestion should NOT be re-suggested!"
    )
    print("  Rejected suggestion suppressed from future gap evaluations: PASS", flush=True)


def main() -> None:
    print("=====================================================================")
    print("           STARTING PHASE 6 CONTRACT VERIFICATION                    ")
    print("=====================================================================")

    t0 = time.perf_counter()
    library, _ = load_library(Path("library"))

    # Test 1: B3 gap suggestions and MBOM
    test_b3_gap_suggestions_and_mbom(library)

    # Test 2: B5 approval blocking and release
    test_b5_approval_blocking_and_success(library)

    # Test 3: B6 approval blocking and release
    test_b6_approval_blocking_and_success(library)

    # Test 4: Determinism B1-B6
    test_determinism_b1_to_b6(library)

    # Test 5: Partial re-run and user edits
    test_partial_rerun_and_user_edit_persistence(library)

    elapsed = time.perf_counter() - t0
    print("\n=====================================================================")
    print(f" ALL PHASE 6 VERIFICATION ASSERTIONS PASSED SUCCESSFULLY IN {elapsed:.2f}s!")
    print("=====================================================================")


if __name__ == "__main__":
    main()
