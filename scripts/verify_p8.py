"""Phase 8 host-side verification (no Docker): the parts of P8 that do not need the stack.

  1. Library versions: seed, draft, edit, validate, publish; a job pinned to the old version keeps
     its result while a new job gets the changed one (acceptance 2).
  2. APS adapter against the scripted B7 responses (acceptance 1): F3D -> STEP, SolidWorks ->
     properties + OBJ, usage counted, objects deleted, cap and unconfigured behaviour.
  3. Mesh reader (cad-worker code), SolidWorks property merge, and the rules on the merged nodes.
  4. Admin session tokens and the rate limiter.

`make e2e` repeats the stack-level parts. Run: uv run python scripts/verify_p8.py
"""

from __future__ import annotations

import asyncio
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TMP = Path(tempfile.mkdtemp(prefix="c2b-p8-"))
os.environ.setdefault("C2B_DATABASE_URL", "postgresql://unused/unused")
os.environ["C2B_DATA_DIR"] = str(TMP / "data")
os.environ["C2B_LIBRARY_DIR"] = str(ROOT / "library")
os.environ["C2B_ADMIN_PASSCODE"] = "verify-passcode"
os.environ["C2B_ADMIN_SECRET"] = "verify-secret"  # noqa: S105  (throwaway)

from c2b_domain.cad import CadExtractResult
from c2b_library import versions
from c2b_library.loader import load_library
from c2b_native_adapter import (
    ApsAdapter,
    ReplayTransport,
    StubNativeAdapter,
    UsageGate,
)

FAILS: list[str] = []


def check(ok: bool, what: str, detail: object = "") -> None:
    print(("PASS  " if ok else "FAIL  ") + what + ("" if ok else f"  {detail}"), flush=True)
    if not ok:
        FAILS.append(what)


def val(x: object) -> object:
    """Unwrap a Prov-like value for printing and comparing."""
    return getattr(x, "value", x)


# --- 1. library versions ------------------------------------------------------------------------


def library_versions() -> None:
    print("\n--- library versions and pinning ---")
    store = TMP / "store"
    base = ROOT / "library"
    v0 = versions.seed(store, base)
    check(v0 == "2026.11.1", "baseline seeded as a version", v0)
    check(versions.active_version(store, base) == v0, "active version is the baseline")

    draft = versions.create_draft(store, base, v0)
    text = versions.read_file(versions.draft_path(store, draft), "materials.yaml")
    # Raise the density of cold-rolled steel (the B2 bracket's material) from 7.85 to 7.90.
    edited = text.replace(
        'code: steel_crca,   family: carbon_steel, display: "Mild steel, cold rolled (CRCA)", '
        'designations: { en: ["DC01", "1.0330"], is: ["IS 513 CR1"], astm: ["ASTM A1008 CS"], '
        'jis: ["SPCC"] }, density_g_cm3: 7.85',
        'code: steel_crca,   family: carbon_steel, display: "Mild steel, cold rolled (CRCA)", '
        'designations: { en: ["DC01", "1.0330"], is: ["IS 513 CR1"], astm: ["ASTM A1008 CS"], '
        'jis: ["SPCC"] }, density_g_cm3: 7.90',
    )
    check(edited != text, "the draft edit applies")
    versions.write_draft_file(store, draft, "materials.yaml", edited)
    ok, _ = versions.validate_draft(store, draft)
    check(ok, "the edited draft validates")

    # A broken draft is refused on save (bad YAML) and on publish (schema error).
    try:
        versions.write_draft_file(store, draft, "materials.yaml", "a: [unclosed")
        check(False, "invalid YAML is refused on save")
    except versions.VersionError:
        check(True, "invalid YAML is refused on save")

    changes = versions.diff_versions(
        versions.version_path(store, base, v0), versions.draft_path(store, draft)
    )
    check(
        [c["file"] for c in changes] == ["materials.yaml"] and changes[0]["added"] == 1,
        "diff shows exactly the one changed line",
        changes and [c["file"] for c in changes],
    )

    # An existing job is pinned to v0; a job made after publishing gets the new active version.
    job_old_version = versions.active_version(store, base)
    v1 = versions.publish_draft(store, base, draft, ["steel_crca density 7.90 (verification)"])
    check(v1 == "2026.11.2", "published as the next version", v1)
    check(versions.active_version(store, base) == v1, "the new version is active")
    job_new_version = versions.active_version(store, base)
    check(job_old_version == v0 and job_new_version == v1, "old job keeps v0, new job gets v1")

    from c2b_rules.engine import build_ebom

    cad = _b2_extract()
    old = build_ebom(
        extract=cad, library=versions.resolve_library(store, base, job_old_version), job_id="old"
    )
    new = build_ebom(
        extract=cad, library=versions.resolve_library(store, base, job_new_version), job_id="new"
    )
    m_old = round(sum(float(val(ln.mass_g) or 0) * ln.qty_total for ln in old.ebom), 2)
    m_new = round(sum(float(val(ln.mass_g) or 0) * ln.qty_total for ln in new.ebom), 2)
    check(m_new > m_old, "a new job's result changes (mass differs)", (m_old, m_new))
    again = build_ebom(
        extract=cad, library=versions.resolve_library(store, base, job_old_version), job_id="old2"
    )
    m_again = round(sum(float(val(ln.mass_g) or 0) * ln.qty_total for ln in again.ebom), 2)
    check(
        m_again == m_old, "the existing job's pinned version gives its old result", (m_old, m_again)
    )
    check(
        old.library_version == v0 and new.library_version == v1, "BoMs carry their library version"
    )

    check(
        [v.version for v in versions.list_versions(store, base)] == [v1, v0],
        "both versions are listed, newest first",
    )
    # Versions are immutable: publishing never touches the old snapshot.
    check(
        "7.85" in versions.read_file(versions.version_path(store, base, v0), "materials.yaml"),
        "the old version's files are unchanged",
    )


_CAD: CadExtractResult | None = None


def _b2_extract() -> CadExtractResult:
    global _CAD
    if _CAD is None:
        from c2b_cad_worker.extract import extract_step

        _CAD = extract_step(ROOT / "golden" / "B2_bracket" / "input" / "bracket_assembly.step")
    return _CAD


# --- 2. APS adapter ------------------------------------------------------------------------------


class Counter:
    def __init__(self, cap: int) -> None:
        self.used = 0
        self.cap = cap

    async def reserve(self, n: int) -> bool:
        if self.used + n > self.cap:
            return False
        self.used += n
        return True

    async def refund(self, n: int) -> None:
        self.used = max(0, self.used - n)


async def _no_sleep(_: float) -> None:
    return None


def _adapter() -> tuple[ApsAdapter, ReplayTransport]:
    transport = ReplayTransport(ROOT / "golden" / "B7_native" / "aps")
    return (
        ApsAdapter("fixture-client", "fixture-secret", transport=transport, sleep=_no_sleep),
        transport,
    )


async def aps_adapter() -> None:
    print("\n--- APS adapter against the scripted B7 responses ---")
    case = ROOT / "golden" / "B7_native"
    counter = Counter(cap=3)
    gate = UsageGate(reserve=counter.reserve, refund=counter.refund)

    adapter, transport = _adapter()
    res = await adapter.convert(case / "input" / "bracket.f3d", TMP / "conv" / "job1", gate)
    check(res.status == "ok", "F3D conversion succeeds", res.message)
    check(counter.used == 1 and res.usage_jobs == 1, "F3D counts 1 translation job", counter.used)
    check(
        res.step_path is not None
        and res.step_path.read_bytes()
        == (case / "aps" / "converted" / "bracket.step").read_bytes(),
        "the STEP comes back byte for byte",
    )
    check(
        any(k.startswith("DELETE ") and "/objects/" in k for k in transport.requests),
        "the object is deleted at Autodesk",
    )
    check(
        any(k.startswith("DELETE ") and k.endswith("/manifest") for k in transport.requests),
        "the derivative is deleted",
    )
    check(any("#step" in k for k in transport.requests), "a STEP job was requested")

    adapter, transport = _adapter()
    res = await adapter.convert(case / "input" / "assembly.sldprt", TMP / "conv" / "job2", gate)
    check(res.status == "ok", "SolidWorks conversion succeeds", res.message)
    check(
        counter.used == 3 and res.usage_jobs == 2,
        "SolidWorks counts 2 translation jobs",
        counter.used,
    )
    check(
        res.step_path is None and len(res.obj_paths) == 1, "SolidWorks returns an OBJ and no STEP"
    )
    check(
        any("#svf2" in k for k in transport.requests)
        and any("#obj" in k for k in transport.requests),
        "SVF2 and OBJ jobs were requested",
    )
    names = {n.name for n in res.properties}
    check(
        {"Bracket Assembly", "Mounting Plate<1>", "Drive Shaft<1>", "Cover<1>"} <= names,
        "object tree and properties are joined by object id",
        names,
    )
    plate = next(n for n in res.properties if n.name.startswith("Mounting Plate"))
    check(plate.props.get("Part Number") == "MP-0001", "custom properties are read", plate.props)
    check(plate.path == "Bracket Assembly / Mounting Plate<1>", "path follows the tree", plate.path)

    adapter, _ = _adapter()
    capped = await adapter.convert(case / "input" / "bracket.f3d", TMP / "conv" / "job3", gate)
    check(
        capped.status == "cap_reached" and counter.used == 3,
        "past the cap: stopped, nothing counted",
        capped,
    )
    check(
        "monthly limit" in capped.message and "STEP" in capped.message, "the cap message is clear"
    )

    stub = await StubNativeAdapter().convert(case / "input" / "bracket.f3d", TMP, gate)
    check(
        stub.status == "not_configured" and "STEP AP242" in stub.message,
        "unconfigured: clear message",
    )

    # A missing recorded response is an error with a message, never a crash.
    broken = ApsAdapter(
        "other-client",
        "x",
        transport=ReplayTransport(case / "aps"),
        sleep=_no_sleep,
    )
    c2 = Counter(cap=5)
    try:
        bad = await broken.convert(
            case / "input" / "bracket.f3d", TMP / "conv" / "job4", UsageGate(c2.reserve, c2.refund)
        )
        check(bad.status == "error", "an unexpected exchange is reported", bad)
    except Exception as exc:
        check(False, "an unexpected exchange must not raise", repr(exc))
    check(c2.used == 0, "a failed conversion before upload gives the count back", c2.used)


# --- 3. mesh, properties, rules ------------------------------------------------------------------


def mesh_props_rules() -> None:
    print("\n--- mesh reader, property merge, rules ---")
    from c2b_cad_worker.mesh import extract_mesh
    from c2b_native_adapter import flatten_properties
    from c2b_rules.engine import build_ebom

    case = ROOT / "golden" / "B7_native"
    out = TMP / "out" / "jobx"
    mesh = extract_mesh([case / "aps" / "converted" / "assembly.obj"], out, "assembly")
    check(mesh.status == "done", "mesh reads", mesh.notices)
    parts = [n for n in mesh.nodes if n.kind == "part"]
    check(
        len(parts) == 3 and len(mesh.nodes) == 4,
        "an assembly node plus 3 parts",
        [n.name for n in mesh.nodes],
    )
    vols = {n.name: mesh.shapes[n.shape_id].volume_mm3 for n in parts if n.shape_id}
    check(
        vols == {"Mounting Plate": 24000.0, "Drive Shaft": 17280.0, "Cover": 120000.0},
        "volumes equal a x b x c",
        vols,
    )
    check(
        all(s.mesh_based and s.closed for s in mesh.shapes.values()),
        "shapes are closed and marked mesh-based",
    )
    plate_shape = mesh.shapes[next(n.shape_id for n in parts if n.name == "Mounting Plate")]  # type: ignore[index]
    check(plate_shape.obb_mm == (100.0, 60.0, 4.0), "oriented box is the box", plate_shape.obb_mm)
    glb = (out / "model.glb").read_bytes()
    check(
        glb[:4] == b"glTF" and mesh.glb_relpath == "out/jobx/model.glb",
        "a GLB is written for the viewer",
    )

    # Merge the properties exactly as the worker does.
    import json

    from c2b_worker.props_merge import merge_props

    library, _ = load_library(ROOT / "library")
    assert library is not None
    data = json.loads(mesh.model_dump_json())
    adapter_props = _scripted_props()
    updated, notices = merge_props(data["nodes"], adapter_props, library)
    by_name = {n["name"]: n for n in data["nodes"]}
    check(updated == 3, "three parts got properties", updated)
    check(by_name["Mounting Plate"]["props"].get("part_number") == "MP-0001", "part number merged")
    check(
        by_name["Mounting Plate"]["props"].get("material") == "ss_304",
        "material 1.4301 -> ss_304",
        by_name["Mounting Plate"]["props"],
    )
    check(
        by_name["Drive Shaft"]["props"].get("material") == "al_6061_t6",
        "material by designation -> al_6061_t6",
    )
    check("part_number" not in by_name["Cover"]["props"], "an empty part number ('-') is ignored")
    check(
        by_name["Cover"]["props"].get("material_raw") == "Special Alloy X"
        and any(n["code"] == "material_not_in_library" for n in notices),
        "an unknown material is reported, not guessed",
    )
    check(
        by_name["Mounting Plate"]["props"].get("custom.Supplier") == "ACME Fixtures",
        "custom properties are carried",
    )

    draft = build_ebom(
        extract=CadExtractResult.model_validate(data),
        library=library,
        job_id="b7b",
        assign_numbers=True,
    )
    lines = {ln.cad_name: ln for ln in draft.ebom if ln.cad_name}
    plate = lines["Mounting Plate"]
    pn = plate.part_number
    check(
        val(pn) == "MP-0001"
        and getattr(pn, "source", None) == "cad_meta"
        and "R-NUM-CAD" in (getattr(pn, "rules", None) or []),
        "part number from CAD is kept (source cad_meta, R-NUM-CAD)",
        pn,
    )
    check(
        abs(float(val(plate.mass_g)) - 190.32) < 1e-6,
        "plate mass = 24000 mm3 x 7.93 g/cm3",
        val(plate.mass_g),
    )
    shaft = lines["Drive Shaft"]
    check(
        abs(float(val(shaft.mass_g)) - 46.656) < 1e-6,
        "shaft mass = 17280 mm3 x 2.70 g/cm3",
        val(shaft.mass_g),
    )
    mass_prov = plate.mass_g
    conf = getattr(mass_prov, "confidence", None)
    check(conf is not None and abs(conf - 0.54) < 1e-6, "mesh mass confidence = 0.9 x 0.6", conf)
    check(
        "R-MSS-MESH" in (getattr(mass_prov, "rules", None) or plate.rule_ids),
        "mass names the mesh rule",
    )
    cover = lines["Cover"]
    check(
        val(cover.part_number) != "-" and val(cover.part_number),
        "a part without a CAD number is numbered by the rules",
    )

    # flatten_properties handles a tree with no properties (defensive parsing).
    check(flatten_properties({}, {}) == [], "empty Autodesk answers give no properties")


def _scripted_props() -> list[dict[str, object]]:
    case = ROOT / "golden" / "B7_native"

    async def go() -> list[dict[str, object]]:
        adapter, _ = _adapter()
        c = Counter(5)
        res = await adapter.convert(
            case / "input" / "assembly.sldprt",
            TMP / "conv" / "jobp",
            UsageGate(c.reserve, c.refund),
        )
        return [{"name": n.name, "path": n.path, "props": n.props} for n in res.properties]

    return asyncio.run(go())


# --- 4. admin token, rate limiter ----------------------------------------------------------------


def admin_and_limits() -> None:
    print("\n--- admin session token and rate limiter ---")
    from c2b_api.admin import issue_token, token_valid
    from c2b_api.ratelimit import Limiter, classify, client_ip

    token, ttl = issue_token()
    check(token_valid(token) and ttl == 3600, "a fresh token is valid")
    check(not token_valid(token, now=time.time() + ttl + 5), "a token expires")
    check(
        not token_valid(token[:-2] + "xx") and not token_valid(None) and not token_valid("a.b"),
        "forged or missing tokens are refused",
    )

    lim = Limiter()
    allowed = sum(1 for _ in range(30) if lim.allow("job_create", "1.2.3.4", 20) == 0)
    check(allowed == 20, "burst limited to the per-minute budget", allowed)
    check(lim.allow("job_create", "5.6.7.8", 20) == 0, "another address has its own budget")
    check(lim.allow("job_create", "1.2.3.4", 20) > 0, "the wait time is reported")
    check(
        classify("POST", "/jobs") == "job_create"
        and classify("POST", "/admin/login") == "admin_login",
        "requests are classified",
    )
    check(classify("GET", "/healthz") is None, "health checks are never limited")
    scope = {"headers": [(b"x-forwarded-for", b"9.9.9.9, 10.0.0.2")], "client": ("172.18.0.5", 1)}
    check(client_ip(scope) == "10.0.0.2", "the proxy-added (right-most) address is used")  # type: ignore[arg-type]


def main() -> int:
    try:
        library_versions()
        asyncio.run(aps_adapter())
        mesh_props_rules()
        admin_and_limits()
    finally:
        shutil.rmtree(TMP, ignore_errors=True)
    print()
    if FAILS:
        print(f"{len(FAILS)} check(s) FAILED:")
        for f in FAILS:
            print(" -", f)
        return 1
    print("ALL P8 HOST CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
