"""Golden case B7: native CAD through Autodesk, with scripted APS responses (ADR-018).

Two inputs, both placeholders (the generator cannot write real native files):

* `input/bracket.f3d`  - Fusion. APS returns STEP (B2's bracket assembly, so the downstream rules
  and numbers are the already-known B2 ones).
* `input/assembly.sldprt` - SolidWorks. APS returns SVF2 object properties and an OBJ with three box
  parts, so volumes and masses are exact by arithmetic (volume = a x b x c).

The APS responses in `aps/` are SCRIPTED from the documented API shapes (docs/phase-reports/P8.md):
they were not recorded against a live Autodesk account. `C2B_APS_MODE=record` can replace them
with real recordings later. Request keys use the adapter's own helpers, so file names cannot drift.
"""

import hashlib
import io
import json
import zipfile
from pathlib import Path
from typing import Any

from c2b_library.loader import Library
from c2b_native_adapter import bucket_name, download_path, object_name, urn_for, write_fixture

CLIENT_ID = "fixture-client"  # what the worker uses in replay mode when no id is configured
GUID = "11111111-2222-3333-4444-555555555555"
HOST = "https://fixture-s3.example"

# name, (x, y, z) mm, material text as SolidWorks would write it, part number ("-" = not entered)
SW_PARTS: list[tuple[str, tuple[float, float, float], str, str]] = [
    ("Mounting Plate", (100.0, 60.0, 4.0), "1.4301", "MP-0001"),
    ("Drive Shaft", (12.0, 12.0, 120.0), "ASTM B221 6061-T6", "DS-0002"),
    ("Cover", (80.0, 50.0, 30.0), "Special Alloy X", "-"),
]


def _json(status: int, obj: Any) -> dict[str, Any]:
    return {"status": status, "content_type": "application/json", "json": obj}


def _placeholder_f3d() -> bytes:
    """A ZIP (Fusion files are ZIP containers) with a fixed timestamp, so it is byte-stable."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED) as zf:
        info = zipfile.ZipInfo("manifest.txt", date_time=(2026, 11, 26, 0, 0, 0))
        zf.writestr(info, "Placeholder for a Fusion design. Not a real model (golden case B7).\n")
    return buf.getvalue()


def _placeholder_sldprt() -> bytes:
    """OLE2 signature plus padding: enough for the upload type check, not a real SolidWorks file."""
    return b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 504 + b"golden case B7 placeholder\n"


def _box_obj(name: str, dims: tuple[float, float, float], base: int) -> tuple[str, int]:
    """One OBJ group: a box at the origin with outward-facing quads. Returns (text, vertices)."""
    a, b, c = dims
    verts = [
        (0, 0, 0), (a, 0, 0), (a, b, 0), (0, b, 0),
        (0, 0, c), (a, 0, c), (a, b, c), (0, b, c),
    ]  # fmt: skip
    quads = [(1, 4, 3, 2), (5, 6, 7, 8), (1, 2, 6, 5), (2, 3, 7, 6), (3, 4, 8, 7), (4, 1, 5, 8)]
    lines = [f"g {name}"]
    lines += [f"v {x:g} {y:g} {z:g}" for x, y, z in verts]
    lines += ["f " + " ".join(str(base + i) for i in q) for q in quads]
    return "\n".join(lines) + "\n", len(verts)


def _obj_text() -> str:
    out = ["# scripted fixture for golden case B7 (units: millimetres)"]
    base = 0
    for name, dims, _mat, _pn in SW_PARTS:
        text, n = _box_obj(name, dims, base)
        out.append(text.rstrip("\n"))
        base += n
    return "\n".join(out) + "\n"


def _manifest(urn: str, derivs: list[dict[str, Any]]) -> dict[str, Any]:
    done = all(d["status"] == "success" for d in derivs)
    return {
        "type": "manifest",
        "hasThumbnail": "false",
        "status": "success" if done else "inprogress",
        "progress": "complete" if done else "1% complete",
        "region": "US",
        "urn": urn,
        "derivatives": derivs,
    }


def _deriv(urn: str, output: str, status: str, resource: str | None) -> dict[str, Any]:
    d: dict[str, Any] = {
        "name": "fixture",
        "hasThumbnail": "false",
        "status": status,
        "progress": "complete" if status == "success" else "10% complete",
        "outputType": output,
    }
    if resource:
        d["children"] = [
            {
                "guid": hashlib.sha1(resource.encode(), usedforsecurity=False).hexdigest()[:32],
                "type": "resource",
                "role": "graphics",
                "urn": f"urn:adsk.viewing:fs.file:{urn}/output/{resource}",
                "status": "success",
            }
        ]
    return d


def _common(aps: Path, bucket: str) -> None:
    write_fixture(
        aps,
        "POST /authentication/v2/token",
        [_json(200, {"access_token": "REDACTED", "token_type": "Bearer", "expires_in": 3599})],
    )
    write_fixture(
        aps,
        "POST /oss/v2/buckets",
        [_json(200, {"bucketKey": bucket, "policyKey": "transient", "permissions": []})],
    )


def _upload_and_delete(aps: Path, bucket: str, obj: str, urn: str, size: int) -> None:
    base = f"/oss/v2/buckets/{bucket}/objects/{obj}/signeds3upload"
    part = f"/{bucket}/{obj}/part1"
    write_fixture(
        aps,
        f"GET {base}",
        [
            _json(
                200,
                {"uploadKey": "REDACTED", "urls": [f"{HOST}{part}?X-Amz-Signature=REDACTED"]},
            )
        ],
    )
    write_fixture(aps, f"PUT {part}", [{"status": 200, "content_type": "text/plain", "text": ""}])
    write_fixture(
        aps,
        f"POST {base}",
        [_json(200, {"bucketKey": bucket, "objectKey": obj, "size": size})],
    )
    write_fixture(
        aps,
        f"DELETE /modelderivative/v2/designdata/{urn}/manifest",
        [_json(200, {"result": "success"})],
    )
    write_fixture(
        aps, f"DELETE /oss/v2/buckets/{bucket}/objects/{obj}", [{"status": 200, "text": ""}]
    )


def _f3d_flow(aps: Path, bucket: str, f3d: bytes, step_src: Path) -> dict[str, str]:
    obj = object_name(hashlib.sha256(f3d).hexdigest(), ".f3d")
    urn = urn_for(bucket, obj)
    _upload_and_delete(aps, bucket, obj, urn, len(f3d))
    write_fixture(
        aps,
        "POST /modelderivative/v2/designdata/job #step",
        [_json(200, {"result": "success", "urn": urn, "acceptedJobs": {"output": {}}})],
    )
    manifest_key = f"GET /modelderivative/v2/designdata/{urn}/manifest"
    write_fixture(
        aps,
        manifest_key,
        [
            _json(200, _manifest(urn, [_deriv(urn, "step", "inprogress", None)])),
            _json(200, _manifest(urn, [_deriv(urn, "step", "success", "bracket.step")])),
        ],
    )
    derivative = f"urn:adsk.viewing:fs.file:{urn}/output/bracket.step"
    rel = "converted/bracket.step"
    (aps / "converted").mkdir(parents=True, exist_ok=True)
    (aps / rel).write_bytes(step_src.read_bytes())
    write_fixture(
        aps,
        f"GET {download_path(urn, derivative)}",
        [{"status": 200, "content_type": "application/step", "file": rel}],
    )
    return {"urn": urn, "object": obj}


def _sw_flow(aps: Path, bucket: str, sldprt: bytes) -> dict[str, str]:
    obj = object_name(hashlib.sha256(sldprt).hexdigest(), ".sldprt")
    urn = urn_for(bucket, obj)
    _upload_and_delete(aps, bucket, obj, urn, len(sldprt))
    job = "POST /modelderivative/v2/designdata/job"
    ok = _json(200, {"result": "success", "urn": urn})
    write_fixture(aps, f"{job} #svf2", [ok])
    write_fixture(aps, f"{job} #obj", [ok])
    svf2_run = _deriv(urn, "svf2", "inprogress", None)
    svf2_ok = _deriv(urn, "svf2", "success", "bracket.svf2")
    obj_run = _deriv(urn, "obj", "inprogress", None)
    obj_ok = _deriv(urn, "obj", "success", "assembly.obj")
    write_fixture(
        aps,
        f"GET /modelderivative/v2/designdata/{urn}/manifest",
        [
            _json(200, _manifest(urn, [svf2_run])),
            _json(200, _manifest(urn, [svf2_ok])),
            _json(200, _manifest(urn, [svf2_ok, obj_run])),
            _json(200, _manifest(urn, [svf2_ok, obj_ok])),
        ],
    )
    meta = f"/modelderivative/v2/designdata/{urn}/metadata"
    write_fixture(
        aps,
        f"GET {meta}",
        [
            _json(
                200,
                {
                    "data": {
                        "type": "metadata",
                        "metadata": [{"name": "assembly", "role": "3d", "guid": GUID}],
                    }
                },
            )
        ],
    )
    tree = {
        "objectid": 1,
        "name": "Bracket Assembly",
        "objects": [
            {"objectid": 2 + i, "name": f"{name}<1>"} for i, (name, *_rest) in enumerate(SW_PARTS)
        ],
    }
    write_fixture(
        aps, f"GET {meta}/{GUID}", [_json(200, {"data": {"type": "objects", "objects": [tree]}})]
    )
    collection: list[dict[str, Any]] = [
        {
            "objectid": 1,
            "name": "Bracket Assembly",
            "properties": {"General": {"Name": "Bracket Assembly"}},
        }
    ]
    for i, (name, _dims, material, pn) in enumerate(SW_PARTS):
        collection.append(
            {
                "objectid": 2 + i,
                "name": f"{name}<1>",
                "properties": {
                    "General": {"Name": f"{name}<1>", "Layer": "Default"},
                    "Custom Properties": {
                        "Part Number": pn,
                        "Material": material,
                        "Supplier": "ACME Fixtures",
                    },
                },
            }
        )
    write_fixture(
        aps,
        f"GET {meta}/{GUID}/properties",
        [_json(200, {"data": {"type": "properties", "collection": collection}})],
    )
    obj_deriv = f"urn:adsk.viewing:fs.file:{urn}/output/assembly.obj"
    (aps / "converted").mkdir(parents=True, exist_ok=True)
    (aps / "converted" / "assembly.obj").write_text(_obj_text(), encoding="utf-8", newline="\n")
    write_fixture(
        aps,
        f"GET {download_path(urn, obj_deriv)}",
        [{"status": 200, "content_type": "text/plain", "file": "converted/assembly.obj"}],
    )
    return {"urn": urn, "object": obj}


def _density(lib: Library, text: str) -> float | None:
    want = "".join(ch for ch in text.lower() if ch.isalnum() or ch == "/")
    for m in lib.materials.materials:
        names = [m.code, m.display, *(n for v in m.designations.values() for n in v)]
        if any(want == "".join(ch for ch in n.lower() if ch.isalnum() or ch == "/") for n in names):
            return float(m.density_g_cm3)
    return None


def _index(aps: Path) -> None:
    """A human-readable list of what each hashed fixture file answers."""
    rows = []
    for f in sorted(aps.glob("*.json")):
        data = json.loads(f.read_text(encoding="utf-8"))
        rows.append(f"{f.name}  {data['key']}  ({len(data['responses'])} response(s))")
    (aps / "INDEX.txt").write_text(
        "Scripted APS responses for golden case B7 (NOT recorded against live Autodesk).\n"
        "One file per request key; repeated polls return the responses in order.\n\n"
        + "\n".join(rows)
        + "\n",
        encoding="utf-8",
        newline="\n",
    )


def build_b7(lib: Library, case_dir: Path, b2_step: Path, b2_expected: dict[str, Any]) -> None:
    """Write B7's inputs, scripted APS fixtures and expected results into `case_dir`."""
    aps = case_dir / "aps"
    # Rebuild from scratch so a changed fixture never leaves a stale file behind.
    for old in list(aps.glob("*.json")) + list((aps / "converted").glob("*")):
        old.unlink()
    f3d, sldprt = _placeholder_f3d(), _placeholder_sldprt()
    (case_dir / "input").mkdir(parents=True, exist_ok=True)
    (case_dir / "input" / "bracket.f3d").write_bytes(f3d)
    (case_dir / "input" / "assembly.sldprt").write_bytes(sldprt)

    bucket = bucket_name(CLIENT_ID)
    _common(aps, bucket)
    f3d_ids = _f3d_flow(aps, bucket, f3d, b2_step)
    sw_ids = _sw_flow(aps, bucket, sldprt)
    _index(aps)

    parts = []
    for name, (a, b, c), material, pn in SW_PARTS:
        density = _density(lib, material)
        volume = round(a * b * c, 3)
        parts.append(
            {
                "cad_name": name,
                "part_number_from_cad": None if pn == "-" else pn,
                "material_text": material,
                "material_in_library": density is not None,
                "volume_mm3": volume,
                "mass_g": None if density is None else round(volume * density / 1000.0, 4),
                "mesh_based": True,
            }
        )
    expected = {
        "case": "B7",
        "title": "Native CAD through Autodesk (scripted APS responses)",
        "library_version": lib.version.version,
        "aps_fixtures": "scripted from the documented API shapes, not recorded against live APS",
        "ids": {"bucket": bucket, "f3d": f3d_ids, "solidworks": sw_ids},
        "f3d": {
            "input": "input/bracket.f3d",
            "consent_required_before_upload": True,
            "translation_jobs": 1,
            "converted_step": "aps/converted/bracket.step (identical to B2's bracket_assembly.step)",
            "downstream": {
                "same_as": "B2",
                "ebom_lines": b2_expected["stats"]["ebom_lines"],
                "mbom_lines": len(b2_expected["mbom"]),
            },
            "upload_listed_as": "converted",
        },
        "solidworks": {
            "input": "input/assembly.sldprt",
            "translation_jobs": 2,
            "parts": parts,
            "notices": ["mesh_based", "material_not_in_library", "props_merged"],
            "mesh_confidence_factor": lib.confidence.mesh.get("features_factor"),
        },
        "usage": {"after_f3d": 1, "after_solidworks": 3},
        "assertions": [
            "A native upload without recorded consent is refused (409 consent_required); with APS unconfigured it is refused with a clear message (415 native_not_configured).",
            "The F3D conversion counts 1 translation job, the SolidWorks conversion 2.",
            "The converted STEP is listed as a converted upload and follows the B2 rules and numbers.",
            "SolidWorks properties reach the nodes: part numbers become source=cad_meta (rule R-NUM-CAD), library materials resolve by designation, an unknown material is reported and left to the rules.",
            "Mesh-based mass confidence is 0.9 x mesh factor; the mass rule list contains R-MSS-MESH.",
            "Autodesk objects are deleted after each conversion (DELETE requests are made).",
            "A conversion past C2B_APS_MONTHLY_CAP is stopped with a clear message and counts nothing.",
        ],
    }
    path = case_dir / "expected" / "b7.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(expected, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n"
    )
