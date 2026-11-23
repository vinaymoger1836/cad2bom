"""Phase 2 verification script: B1, B2, B3 fixture extraction and broken file check."""

import json
import sys
import tempfile
import time
from pathlib import Path

from c2b_cad_worker.extract import extract_step

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


def main() -> None:
    results: list[dict[str, object]] = []
    all_ok = True

    print("=" * 80)
    print("PHASE 2 CAD EXTRACTION VERIFICATION RUN")
    print("=" * 80)

    for c in CASES:
        step_file = c["step"].resolve()
        expected_json = c["expected_bom"].resolve()
        assert step_file.exists(), f"Missing {step_file}"
        assert expected_json.exists(), f"Missing {expected_json}"

        with open(expected_json, encoding="utf-8") as f:
            expected = json.load(f)

        expected_volumes = expected.get("shapes_volume_mm3", {})

        with tempfile.TemporaryDirectory() as tmp_dir:
            out_dir = Path(tmp_dir)

            t0 = time.monotonic()
            extract_res = extract_step(step_file, out_dir=out_dir)
            elapsed_s = time.monotonic() - t0

            # Check GLB
            glb_file = out_dir / "model.glb"
            glb_exists = glb_file.exists() and glb_file.stat().st_size > 0
            glb_size_bytes = glb_file.stat().st_size if glb_exists else 0

            # Shape volume reconciliation
            vol_checks: list[dict[str, object]] = []
            extracted_shapes = extract_res.shapes

            # Map shape name or id
            for node in extract_res.nodes:
                if node.kind == "part" and node.shape_id:
                    sid = node.shape_id
                    name = node.name
                    if name in expected_volumes and sid in extracted_shapes:
                        exp_vol = expected_volumes[name]
                        act_vol = extracted_shapes[sid].volume_mm3
                        pct_diff = abs(act_vol - exp_vol) / exp_vol * 100.0 if exp_vol > 0 else 0.0
                        passed = pct_diff <= 0.5
                        if not passed:
                            all_ok = False
                        vol_checks.append(
                            {
                                "part": name,
                                "shape_id": sid,
                                "expected_mm3": exp_vol,
                                "actual_mm3": act_vol,
                                "pct_diff": pct_diff,
                                "passed": passed,
                            }
                        )

            # Node and instance count checks
            leaf_count = extract_res.leaf_count
            node_count = len(extract_res.nodes)
            shape_count = len(extract_res.shapes)

            print(f"\n--- Case {c['id']}: {c['name']} ---")
            print(f"File: {step_file.name} ({step_file.stat().st_size:,} bytes)")
            print(f"Time: {elapsed_s:.3f} s")
            print(f"Nodes: {node_count}, Unique Shapes: {shape_count}, Leaves: {leaf_count}")
            print(f"GLB Output: {glb_file.name} ({glb_size_bytes:,} bytes)")
            print(f"Notices: {len(extract_res.notices)}")

            print("Volume Reconciliation (0.5% tolerance):")
            for vc in vol_checks:
                status = "PASS" if vc["passed"] else "FAIL"
                print(
                    f"  [{status}] {vc['part']:<14} Exp: {vc['expected_mm3']:>10.2f} mm3 | "
                    f"Act: {vc['actual_mm3']:>10.2f} mm3 | Diff: {vc['pct_diff']:>6.3f}%"
                )

            results.append(
                {
                    "case": c["id"],
                    "name": c["name"],
                    "step_file": step_file.name,
                    "file_bytes": step_file.stat().st_size,
                    "elapsed_s": round(elapsed_s, 3),
                    "node_count": node_count,
                    "leaf_count": leaf_count,
                    "shape_count": shape_count,
                    "glb_size_bytes": glb_size_bytes,
                    "vol_checks": vol_checks,
                    "notices": [n.model_dump() for n in extract_res.notices],
                }
            )

    # Broken STEP file check
    print("\n--- Broken / Corrupt STEP Check ---")
    with tempfile.NamedTemporaryFile(suffix=".step", delete=False, mode="w") as f:
        f.write("ISO-10303-21;\nHEADER;\nFILE_DESCRIPTION(('broken'),'2;1');\n/* TRUNCATED JUNK\n")
        broken_path = Path(f.name)

    try:
        t0 = time.monotonic()
        broken_res = extract_step(broken_path)
        broken_time = time.monotonic() - t0
        print(f"Status: {broken_res.status} (expected 'error')")
        print(f"Notices count: {len(broken_res.notices)}")
        for n in broken_res.notices:
            print(f"  Notice [{n.code}]: {n.message}")
        assert broken_res.status == "error", "Expected status 'error'"
        assert any(n.code == "broken_file" for n in broken_res.notices), (
            "Expected broken_file notice"
        )
        print(f"Broken check PASS in {broken_time:.3f} s")
    finally:
        if broken_path.exists():
            broken_path.unlink()

    if all_ok:
        print("\nALL PHASE 2 CONTRACT CHECKS PASSED!")
    else:
        print("\nSOME VOLUME RECONCILIATIONS EXCEEDED TOLERANCE!")
        sys.exit(1)


if __name__ == "__main__":
    main()
