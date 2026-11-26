"""Build the golden cases: STEP and XLSX inputs plus expected JSON, from the library tables."""

import hashlib
import json
import re
import shutil
import struct
import zlib
from pathlib import Path
from typing import Any, cast

import yaml

from c2b_fixtures import cases, xlsx
from c2b_fixtures.b7 import build_b7 as build_b7_files
from c2b_fixtures.expected import Ctx, build_ebom, build_mbom, mass_rollup_g
from c2b_fixtures.model import Spec, to_node
from c2b_fixtures.xcaf import StepNode, count_solids, read_step_tree, write_step
from c2b_library.loader import Library

GENERATOR = "c2b-fixtures build"
FIXED_STEP_TIME = "2026-11-20T00:00:00"

DIRS = {
    "B1": "B1_fastener_zoo",
    "B2": "B2_bracket",
    "B3": "B3_fan",
    "B4": "B4_power_bank",
    "B5": "B5_cad_plus_bom",
    "B6": "B6_unknown",
    "B7": "B7_native",
}
ALL_CASES = ["B1", "B2", "B3", "B4", "B5", "B6", "B7"]

_STEP_TIME_RE = re.compile(r"(FILE_NAME\('[^']*',')([^']*)(')")


def _dump(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n"
    )


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


def _write_step(spec: Spec, path: Path) -> None:
    write_step(to_node(spec), path)
    # Fixed timestamp so regenerating produces a byte-identical file (stable git diffs).
    data = path.read_bytes().decode("latin-1")
    data = _STEP_TIME_RE.sub(rf"\g<1>{FIXED_STEP_TIME}\g<3>", data, count=1)
    path.write_bytes(data.encode("latin-1"))


def _case_yaml(
    case: str, title: str, lib: Library, inputs: list[str], expected: list[str], **extra: Any
) -> dict[str, Any]:
    return {
        "id": case,
        "title": title,
        "library_version": lib.version.version,
        "generated_by": GENERATOR,
        "inputs": inputs,
        "expected": expected,
        "rules_asserted": False,
        "rules_note": "Rule ids are not asserted until the rules engine exists (P3); values below are.",
        **extra,
    }


def _write_case_yaml(out: Path, case: str, data: dict[str, Any]) -> None:
    path = out / DIRS[case] / "case.yaml"
    _write_text(path, yaml.safe_dump(data, sort_keys=False, allow_unicode=True))


def _stats(ebom: list[dict[str, Any]], spec: Spec) -> dict[str, Any]:
    leaves = [line for line in ebom if line.get("cad_volume_mm3") is not None]
    return {
        "ebom_lines": len(ebom),
        "leaf_lines": len(leaves),
        "solid_instances": sum(line["qty_total"] for line in leaves),
        "mass_total_g": mass_rollup_g(ebom),
    }


def _base(
    case: str, title: str, lib: Library, ctx: Ctx, spec: Spec, inputs: list[str]
) -> dict[str, Any]:
    ebom, volumes = build_ebom(ctx, spec)
    return {
        "case": case,
        "title": title,
        "library_version": lib.version.version,
        "profile": "default",
        "inputs": inputs,
        "root": {"name": spec.name, "class": spec.cls},
        "ebom": ebom,
        "stats": _stats(ebom, spec),
        "shapes_volume_mm3": volumes,
    }


# --- B1 ---------------------------------------------------------------------------------------------


def build_b1(lib: Library, out: Path) -> None:
    ctx = Ctx(lib)
    spec = cases.case_b1(lib)
    step = out / DIRS["B1"] / "input" / "fastener_zoo.step"
    _write_step(spec, step)
    bom = _base("B1", "Fastener zoo", lib, ctx, spec, ["input/fastener_zoo.step"])
    bom["llm_calls"] = 0
    bom["assertions"] = [
        "Every item is recognised with the correct standard, size and length (designation block).",
        "Quantities are exact (qty_total).",
        "No LLM call is made (llm_calls == 0).",
        "Descriptions equal the expected strings (profile template; description_exact is true).",
        "Standard-part grade is the profile default, source=library, confidence 0.5, listed in open items.",
        "Part volumes match shapes_volume_mm3 within 0.5%.",
    ]
    _dump(out / DIRS["B1"] / "expected" / "bom.json", bom)
    _write_case_yaml(
        out,
        "B1",
        _case_yaml(
            "B1",
            "Fastener zoo",
            lib,
            ["input/fastener_zoo.step"],
            ["expected/bom.json"],
            stand_in=False,
            llm_calls=0,
        ),
    )


# --- B2 ---------------------------------------------------------------------------------------------


def _b2_expected(
    lib: Library, ctx: Ctx, spec: Spec, case: str, title: str, inputs: list[str]
) -> dict[str, Any]:
    bom = _base(case, title, lib, ctx, spec, inputs)
    bom["mbom"] = build_mbom(ctx, bom["ebom"])
    bom["llm_calls"] = 0
    return bom


def build_b2(lib: Library, out: Path) -> Spec:
    ctx = Ctx(lib)
    spec = cases.case_b2(lib)
    _write_step(spec, out / DIRS["B2"] / "input" / "bracket_assembly.step")
    bom = _b2_expected(lib, ctx, spec, "B2", "Bracket assembly", ["input/bracket_assembly.step"])
    bom["mass_rollup_tolerance"] = 0.01
    bom["assertions"] = [
        "Classes: sheet metal bracket, turned shaft, moulded cover, machined block.",
        "Levels and quantity roll-up are exact (FASTENERS_GRP is instanced 2x with 2 screws, 2 washers, 2 nuts each: 4 of each in total).",
        "FASTENERS_GRP is flagged phantom in the EBOM and collapsed in the MBOM (children move up to the root).",
        "Mass roll-up equals the sum of volume x density within 1% (stats.mass_total_g).",
        "No LLM call is made (llm_calls == 0).",
        "Custom-part descriptions are provisional (description_exact is false): dimensions are exact, wording may change in P6.",
    ]
    _dump(out / DIRS["B2"] / "expected" / "bom.json", bom)
    _write_case_yaml(
        out,
        "B2",
        _case_yaml(
            "B2",
            "Bracket assembly",
            lib,
            ["input/bracket_assembly.step"],
            ["expected/bom.json"],
            stand_in=False,
            llm_calls=0,
        ),
    )
    return spec


# --- B3 ---------------------------------------------------------------------------------------------

B3_DETAILS = (
    "Ceiling fan with LED light kit. 1200 mm sweep, 4 blades, 3-speed, 230 V AC mains, 75 W.\n"
    "Remote control receiver board, hand soldered. Light kit with glass shade and 12 W LED module.\n"
    "Annual volume 5000 units.\n"
)


def _placeholder_png(path: Path) -> None:
    """A 64x64 grey PNG: placeholder for the B3 photo. The vision cassette is recorded in P5."""
    w = h = 64
    raw = b"".join(b"\x00" + bytes([150]) * w for _ in range(h))

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + tag
            + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
        )

    png = (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 0, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw, 9))
        + chunk(b"IEND", b"")
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(png)


def build_b3(lib: Library, out: Path) -> None:
    ctx = Ctx(lib)
    spec = cases.case_b3(lib)
    d = out / DIRS["B3"]
    _write_step(spec, d / "input" / "fan.step")
    _write_text(d / "input" / "details.txt", B3_DETAILS)
    _placeholder_png(d / "input" / "photo.png")
    bom = _b2_expected(
        lib,
        ctx,
        spec,
        "B3",
        "Ceiling fan (STAND-IN model)",
        ["input/fan.step", "input/details.txt", "input/photo.png"],
    )
    bom["stand_in"] = True
    bom["stand_in_note"] = (
        "The real '13-solid fan STEP from the S2 demo' is not in the repository. input/fan.step is a "
        "synthetic 13-solid fan generated by tools/fixtures with 3 blades (the details text says 4). "
        "Replace it and re-record this file when the real model is available."
    )
    bom["assertions"] = [
        "Expected EBOM equals ebom (classes, quantities, levels; custom descriptions provisional).",
        "The blade-count conflict (CAD 3 vs text 4) is raised as ONE blocking question (questions.json).",
        "Exactly four gap suggestions: rating label, manual, carton, solder (suggestions.json).",
        "Nothing is added to the BoM before a suggestion is accepted.",
        "The photo is placeholder grey; vision findings come from the cassette recorded in P5.",
    ]
    blade_line = next(line for line in bom["ebom"] if line["cad_name"] == "Blade")
    questions = [
        {
            "id": "Q-001",
            "kind": "choice",
            "blocking": True,
            "line_ids": [blade_line["id"]],
            "text": "Blade count: the CAD model shows 3 blades but your description says 4. Which is correct?",
            "options": ["3", "4"],
            "evidence": [
                "cad: BladeAssy x3 -> Blade x1 each (qty_total 3)",
                "text: '4 blades' (details.txt)",
            ],
            "answer": None,
        }
    ]
    suggestions = [
        {
            "id": "S-001",
            "class": "label.rating",
            "rule": "R-GAP-001",
            "origin": "gap_rule",
            "level": "ebom",
            "qty": 1,
            "text": "Add a rating label?",
            "review": "suggested",
        },
        {
            "id": "S-002",
            "class": "documentation.manual",
            "rule": "R-GAP-002",
            "origin": "gap_rule",
            "level": "ebom",
            "qty": 1,
            "text": "Add a user manual?",
            "review": "suggested",
        },
        {
            "id": "S-003",
            "class": "packaging.carton",
            "rule": "R-GAP-003",
            "origin": "gap_rule",
            "level": "mbom",
            "qty": 1,
            "text": "Add a shipper carton?",
            "review": "suggested",
        },
        {
            "id": "S-004",
            "class": "consumable.solder",
            "rule": "R-GAP-021",
            "origin": "gap_rule",
            "level": "mbom",
            "qty": None,
            "qty_note": "per_joint_estimate; the engine proposes the number, it is not asserted here",
            "text": "Add solder? The receiver board is hand soldered (details text).",
            "review": "suggested",
        },
    ]
    _dump(d / "expected" / "bom.json", bom)
    _dump(d / "expected" / "questions.json", questions)
    _dump(d / "expected" / "suggestions.json", suggestions)
    _write_case_yaml(
        out,
        "B3",
        _case_yaml(
            "B3",
            "Ceiling fan (stand-in model)",
            lib,
            ["input/fan.step", "input/details.txt", "input/photo.png"],
            ["expected/bom.json", "expected/questions.json", "expected/suggestions.json"],
            stand_in=True,
            vision_cassette="to be recorded in P5; photo.png is a grey placeholder",
        ),
    )


# --- B4 ---------------------------------------------------------------------------------------------


def expand_refdes(text: str) -> list[str]:
    """'R1-R4, R7' -> [R1, R2, R3, R4, R7]."""
    out: list[str] = []
    for part in (p.strip() for p in text.split(",") if p.strip()):
        m = re.fullmatch(r"([A-Z]+)(\d+)\s*-\s*\1?(\d+)", part)
        if m:
            out += [f"{m.group(1)}{i}" for i in range(int(m.group(2)), int(m.group(3)) + 1)]
        else:
            out.append(part)
    return out


def build_b4(lib: Library, out: Path) -> None:
    d = out / DIRS["B4"]
    raw_rows = xlsx.write_b4(d / "input" / "supplier_bom.xlsx")
    header_row = 6
    mapping = {
        "header_row": header_row,
        "title_block_rows": [1, 2, 3],
        "footer_rows": [header_row + len(xlsx.B4_ROWS) + 2, header_row + len(xlsx.B4_ROWS) + 3],
        "columns": {
            "Pos": {"field": "find_no", "by": "rules"},
            "Teilenummer": {"field": "part_number", "by": "rules"},
            "Item": {
                "field": "description",
                "by": "extractor_L3",
                "confirmed_by_user": True,
                "why": "header 'Item' scores for find_no (also claimed by 'Pos'); values are long text",
            },
            "Menge": {"field": "quantity", "by": "rules"},
            "Unit": {"field": "uom", "by": "rules"},
            "Designator": {"field": "refdes", "by": "rules"},
            "Hersteller": {"field": "manufacturer", "by": "rules"},
            "MPN": {"field": "mpn", "by": "rules"},
            "Bemerkung": {"field": "notes", "by": "rules"},
        },
        "ambiguous_columns": ["Item"],
        "assertions": [
            "All columns except 'Item' are mapped by rules (library/headers.yaml).",
            "'Item' is resolved through the extractor cassette (L3) and confirmed by the user.",
        ],
    }
    lines: list[dict[str, Any]] = []
    for src, row in zip(xlsx.B4_ROWS, raw_rows, strict=True):
        refdes = expand_refdes(src[5]) if src[5] else []
        lines.append(
            {
                "id": f"L-{len(lines) + 1:04d}",
                "raw": {k: v for k, v in row.items()},
                "part_number": str(src[1]).strip(),
                "description": src[2],
                "qty": src[3],
                "uom": "EA",
                "uom_raw": src[4],
                "refdes": refdes,
                "refdes_count_matches_qty": (not refdes) or len(refdes) == src[3],
                "class_acceptable": src[9],
                "origin": "user_bom",
                "review": "given",
            }
        )
    bom = {
        "case": "B4",
        "title": "Power bank BoM file",
        "library_version": lib.version.version,
        "inputs": ["input/supplier_bom.xlsx"],
        "assertions": [
            "Every user line is unchanged in `raw` (the cell values as read from the file, including padding spaces).",
            "Part numbers are normalised only by trimming (canonical form); units pcs/nos become EA.",
            "Grouped refdes are split (R1-R4, R7 -> 5 designators) and counted; counts equal Menge on every line.",
            "Gap suggestions are present and exactly those in suggestions.json; nothing is added before acceptance.",
        ],
        "lines": lines,
    }
    suggestions = [
        {
            "id": "S-001",
            "class": "documentation.manual",
            "rule": "R-GAP-002",
            "origin": "gap_rule",
            "level": "ebom",
            "text": "Add a user manual?",
            "review": "suggested",
        },
        {
            "id": "S-002",
            "class": "packaging.carton",
            "rule": "R-GAP-003",
            "origin": "gap_rule",
            "level": "mbom",
            "text": "Add a shipper carton?",
            "review": "suggested",
        },
        {
            "id": "S-003",
            "class": "label.transport",
            "rule": "R-GAP-004",
            "origin": "gap_rule",
            "level": "ebom",
            "text": "Lithium cells are present: add a transport label?",
            "review": "suggested",
        },
        {
            "id": "S-004",
            "class": "consumable.solder",
            "rule": "archetype:power_bank",
            "origin": "skeleton",
            "level": "mbom",
            "text": "Power banks usually need solder for hand or wave soldered joints. Add it?",
            "review": "suggested",
        },
    ]
    _dump(d / "expected" / "mapping.json", mapping)
    _dump(d / "expected" / "bom.json", bom)
    _dump(d / "expected" / "suggestions.json", suggestions)
    _write_case_yaml(
        out,
        "B4",
        _case_yaml(
            "B4",
            "Power bank BoM file",
            lib,
            ["input/supplier_bom.xlsx"],
            ["expected/mapping.json", "expected/bom.json", "expected/suggestions.json"],
            stand_in=False,
            extractor_cassette="to be recorded in P5 (header mapping for 'Item')",
        ),
    )


# --- B5 ---------------------------------------------------------------------------------------------


def build_b5(lib: Library, out: Path, b2_spec: Spec) -> None:
    ctx = Ctx(lib)
    d = out / DIRS["B5"]
    _write_step(b2_spec, d / "input" / "bracket_assembly.step")
    raw_rows = xlsx.write_b5(d / "input" / "user_bom.xlsx")
    ebom, _ = build_ebom(ctx, b2_spec)
    by_name = {line["cad_name"]: line for line in ebom}
    screw = by_name["Part1"]
    questions = [
        {
            "id": "Q-001",
            "kind": "choice",
            "blocking": True,
            "line_ids": ["user:SCR-1004", screw["id"]],
            "text": "Screw M4x12 ISO 4762: your BoM says 6, the CAD model shows 4. Which is correct?",
            "options": ["6 (your BoM)", "4 (CAD)"],
            "evidence": [
                "bom: row 5 qty 6",
                f"cad: {screw['cad_name']} qty_total {screw['qty_total']}",
            ],
            "answer": None,
            "note": "The user's value 6 is kept until answered; the line is never rewritten.",
        }
    ]
    missing = ["Shaft", "Part2", "Part3"]
    suggestions = [
        {
            "id": f"S-{i + 1:03d}",
            "origin": "cad",
            "review": "suggested",
            "cad_name": by_name[n]["cad_name"],
            "class": by_name[n]["class"],
            "qty_total": by_name[n]["qty_total"],
            "text": f"CAD shows {by_name[n]['description']} ({by_name[n]['qty_total']}x). Add it to the BoM?",
        }
        for i, n in enumerate(missing)
    ]
    expected = {
        "case": "B5",
        "title": "B2 CAD plus an incomplete user BoM",
        "library_version": lib.version.version,
        "inputs": ["input/bracket_assembly.step", "input/user_bom.xlsx"],
        "assertions": [
            "Exactly 3 'CAD shows X, add it?' suggestions (suggestions.json) and exactly 1 quantity question (questions.json).",
            "User values are kept: the screw line stays at qty 6 until the question is answered.",
            "Approval is blocked until the question is answered and all suggestions are accepted or rejected.",
            "User rows are unchanged in `raw`.",
        ],
        "user_rows_raw": raw_rows,
        "matched_user_rows": {
            "BRK-1001": "Bracket",
            "CVR-1002": "Cover",
            "BLK-1003": "Block",
            "SCR-1004": "Part1 (ISO 4762 M4x12)",
        },
        "approval_blocked_initially": True,
    }
    _dump(d / "expected" / "expected.json", expected)
    _dump(d / "expected" / "questions.json", questions)
    _dump(d / "expected" / "suggestions.json", suggestions)
    _write_case_yaml(
        out,
        "B5",
        _case_yaml(
            "B5",
            "CAD plus incomplete BoM",
            lib,
            ["input/bracket_assembly.step", "input/user_bom.xlsx"],
            ["expected/expected.json", "expected/questions.json", "expected/suggestions.json"],
            stand_in=False,
        ),
    )


# --- B6 ---------------------------------------------------------------------------------------------

B6_DETAILS = (
    "Handheld soil-moisture controller with a rotary knob, one button and a rechargeable cell.\n"
)


def build_b6(lib: Library, out: Path) -> None:
    spec = cases.case_b6(lib)
    d = out / DIRS["B6"]
    _write_step(spec, d / "input" / "unknown_product.step")
    _write_text(d / "input" / "details.txt", B6_DETAILS)
    parts = []
    for i, child in enumerate(spec.children):
        parts.append(
            {
                "id": f"L-{i + 1:04d}",
                "cad_name": child.name,
                "qty_total": child.qty,
                "geometry_note": child.note,
                "acceptable_classes": child.acceptable_classes,
                "cad_volume_mm3": round(child.shape.Volume(), 2)
                if child.shape is not None
                else None,
            }
        )
    bom = {
        "case": "B6",
        "title": "Unknown product type",
        "library_version": lib.version.version,
        "inputs": ["input/unknown_product.step", "input/details.txt"],
        "root": {"name": spec.name, "class": spec.cls},
        "archetype": None,
        "parts": parts,
        "stats": {"solid_instances": len(parts)},
        "assertions": [
            "Every part ends with a class from its acceptable_classes (or stays unclassified with a question).",
            "No archetype matches the product (archetype is null).",
        ],
    }
    llm = {
        "case": "B6",
        "assertions": [
            "Parts whose class score stays below the class threshold (confidence.yaml thresholds.class) after C1-C6 go through ladder step L1 (role reasoner).",
            "Every LLM-sourced class has source=llm, confidence <= confidence.yaml llm.class_cap (0.7), a fallback_reason, and appears in open items.",
            "L4 (gap candidates for an unknown product type) returns at most 8 suggestions, all low confidence (llm.gap_suggestion_confidence).",
            "Approval is blocked until all questions are answered and all suggestions are accepted or rejected.",
            "Every LLM output enum value is a class code from the taxonomy.",
        ],
        "which_parts_call_l1": (
            "Not fixed here: it depends on the rule scores the engine produces (P3). Expectation: all parts "
            "named Body1..Body9 lack name and standard evidence, so geometry alone (weights 0.4-0.7) is below the "
            "0.75 threshold and each goes to L1 unless a C6 context boost lifts it. P5 records the cassette and "
            "this file is updated then with the exact prompt keys."
        ),
        "cassette": "to be recorded in P5",
        "llm_confidence_cap": lib.confidence.llm["class_cap"],
        "threshold_class": lib.confidence.thresholds["class"],
    }
    _dump(d / "expected" / "bom.json", bom)
    _dump(d / "expected" / "llm_expectations.json", llm)
    _write_case_yaml(
        out,
        "B6",
        _case_yaml(
            "B6",
            "Unknown product type",
            lib,
            ["input/unknown_product.step", "input/details.txt"],
            ["expected/bom.json", "expected/llm_expectations.json"],
            stand_in=False,
            cassette="to be recorded in P5",
        ),
    )


# --- B7: native CAD through Autodesk (scripted APS responses) ---------------------------------------


def build_b7_case(lib: Library, out: Path, b2_spec: Spec) -> None:
    """B7 reuses B2's bracket assembly as the STEP that the scripted APS conversion returns."""
    ctx = Ctx(lib)
    b2_expected = _b2_expected(lib, ctx, b2_spec, "B2", "Bracket assembly", [])
    d = out / DIRS["B7"]
    d.mkdir(parents=True, exist_ok=True)
    step = d / "aps" / "_b2.step"
    step.parent.mkdir(parents=True, exist_ok=True)
    _write_step(b2_spec, step)
    try:
        build_b7_files(lib, d, step, b2_expected)
    finally:
        step.unlink(missing_ok=True)
    _write_case_yaml(
        out,
        "B7",
        _case_yaml(
            "B7",
            "Native CAD through Autodesk (scripted APS responses)",
            lib,
            ["input/bracket.f3d", "input/assembly.sldprt"],
            ["expected/b7.json"],
            stand_in=True,
            stand_in_note="placeholder native files; APS responses are scripted, not recorded",
            llm_calls=0,
            aps_fixtures="aps/",
        ),
    )


# --- B3b: the owner's real fan.STEP, measured, not classified ------------------------------------------


def _tree_json(n: StepNode) -> dict[str, Any]:
    out: dict[str, Any] = {
        "name": n.name,
        "kind": "assembly" if n.is_assembly else "solid",
        "instances": n.count,
    }
    if n.volume_mm3 is not None:
        out["volume_mm3"] = round(n.volume_mm3, 2)
    if n.children:
        out["children"] = [_tree_json(c) for c in n.children]
    return out


def import_real_fan(lib: Library, src: Path, out: Path) -> Path:
    """Copy the owner's fan STEP into B3 as `fan_real.step` and record what was MEASURED from it.

    Classes and quantities are deliberately not asserted: the part names are Part1..Part9 and the
    owner has not classified them. Only geometry facts are recorded.
    """
    d = out / DIRS["B3"]
    dest = d / "input" / "fan_real.step"
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src, dest)
    tree = read_step_tree(dest)
    unique = sorted(
        (
            {"name": c.name, "instances": c.count, "volume_mm3": round(c.volume_mm3 or 0.0, 2)}
            for c in tree.children
        ),
        key=lambda x: -cast(float, x["volume_mm3"]),
    )
    data = {
        "case": "B3b",
        "title": "Fan model supplied by the owner (provisional)",
        "provisional": True,
        "library_version": lib.version.version,
        "input": "input/fan_real.step",
        "source_note": (
            "Copied from the owner's Downloads folder (fan.STEP). It is not confirmed to be the S2 demo "
            "fan: it has 15 solid instances (9 unique shapes), not 13."
        ),
        "sha256": hashlib.sha256(dest.read_bytes()).hexdigest(),
        "bytes": dest.stat().st_size,
        "solid_instances": count_solids(tree),
        "unique_shapes": len(unique),
        "tree": _tree_json(tree),
        "unique_shapes_by_volume": unique,
        "observations": [
            "Three identical large solids (Part2, x3) are probably the blades; the details text for B3 says 4 blades, so the 3 vs 4 question would apply here too.",
            "Classes, descriptions and quantities are NOT asserted: the owner has not classified Part1..Part9.",
        ],
        "assertions_when_classified": "Add expected classes here once the owner has labelled each part; until then B3b only checks that the file reads and the counts/volumes above hold (within 0.5%).",
    }
    _dump(d / "expected" / "fan_real_provisional.json", data)
    return dest


# --- entry --------------------------------------------------------------------------------------------


def build(lib: Library, out: Path, selected: list[str]) -> list[Path]:
    """Build the selected cases into `out`. B5 needs B2's spec, so it is built from case_b2 again."""
    want = set(selected)
    if "B1" in want:
        build_b1(lib, out)
    b2_spec: Spec | None = None
    if "B2" in want:
        b2_spec = build_b2(lib, out)
    if "B3" in want:
        build_b3(lib, out)
    if "B4" in want:
        build_b4(lib, out)
    if "B5" in want:
        build_b5(lib, out, b2_spec or cases.case_b2(lib))
    if "B6" in want:
        build_b6(lib, out)
    if "B7" in want:
        build_b7_case(lib, out, b2_spec or cases.case_b2(lib))
    return sorted(
        p for p in out.rglob("*") if p.is_file() and any(DIRS[c] in p.parts for c in want)
    )


def write_readme(out: Path, lib: Library) -> None:
    _write_text(out / "README.md", README_TEXT.replace("{VERSION}", lib.version.version))


README_TEXT = """# Golden cases (the contract)

Inputs and expected outputs for the cases in docs/10 §4. Everything here is generated by
`uv run c2b-fixtures build` from the library tables (version {VERSION}); do not edit by hand.
Changing an expected value needs an explanation in the phase report (CLAUDE.md rule 10).

| Case | Folder | Inputs | Status |
|---|---|---|---|
| B1 Fastener zoo | `B1_fastener_zoo/` | STEP | Exact: standards, sizes, lengths, quantities, descriptions |
| B2 Bracket assembly | `B2_bracket/` | STEP | Exact except custom-part description wording (`description_exact: false`) |
| B3 Fan | `B3_fan/` | STEP (**stand-in**), details text, placeholder photo | Stand-in model: see below |
| B3b Fan (owner's file) | `B3_fan/input/fan_real.step` | the owner's own fan.STEP | Provisional: measured only, not classified |
| B4 Power bank BoM file | `B4_power_bank/` | messy supplier XLSX | Mapping, normalisation and suggestions; classes are acceptable sets |
| B5 CAD + incomplete BoM | `B5_cad_plus_bom/` | B2 STEP + user XLSX | 3 suggestions, 1 question |
| B6 Unknown product | `B6_unknown/` | STEP, details text | Acceptable class sets; LLM constraints; cassette in P5 |
| B7 Native CAD (Autodesk) | `B7_native/` | placeholder `.f3d` and `.sldprt`, **scripted** APS responses in `aps/` | Consent, usage counter, converted STEP follows B2, SolidWorks properties and mesh features |

## B3 is a stand-in

docs/10 names "the 13-solid fan STEP from the S2 demo". That file is not in this repository. `B3_fan/input/fan.step`
is a synthetic 13-solid ceiling fan from the fixture generator (3 blades modelled; the details text says 4).
Replace it with the real file and regenerate `expected/` when it is available.

`B3_fan/input/fan_real.step` is a fan model the owner had in their Downloads folder (`c2b-fixtures import-fan`).
It is not confirmed to be the S2 demo fan (15 solid instances, 9 unique shapes, not 13). Only measured facts are in
`expected/fan_real_provisional.json`; the owner has not classified its parts.

## B7 is scripted

`B7_native/aps/` holds the Autodesk responses as JSON, one file per request (`INDEX.txt` lists them). They were scripted
from the documented API shapes, **not recorded against a live account**; the native input files are placeholders. Set
`C2B_APS_MODE=record` with real credentials to replace them with real recordings (git-ignored under `aps-live/`).

## Conventions

* `expected/bom.json` follows `BomLine` (docs/05 §1) as a subset. Rule ids are **not** asserted until the rules engine exists (P3).
* Standard parts are named `Part1..PartN` in the STEP file on purpose; the real identity is in `designation`.
* Standard-part material/grade is the profile default (`library/profiles/default.yaml`): source `library`, confidence 0.5, listed in `open_items`.
* Masses are `volume x density` from the library; volumes are measured by OpenCascade on the generated solids.
* Part numbers are not asserted (numbering is P6). Custom-part description wording is provisional.
* STEP files carry a fixed timestamp so regeneration is byte-identical.
* Cassettes (vision, extractor, reasoner) are recorded in P5; the expectation files say which calls are expected.
"""
