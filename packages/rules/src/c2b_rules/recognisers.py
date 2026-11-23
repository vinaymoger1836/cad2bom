"""Standard-part recognisers (docs/04 §4).

Driven by standard tables in library/standards/*.yaml and tolerances in confidence.yaml.
Matches geometric features (cylinders, OBB, face types, volume) against catalogue tables
and returns a standard Designation (e.g. ISO 4762 M4x12, 6201, ISO 3601-1 20x2) with score.
"""

import math
from typing import Any

from c2b_domain.bom import Designation
from c2b_domain.cad import ShapeFeatures
from c2b_library.schemas import StandardTable


def snap_length(
    raw_length: float, standard_lengths: list[float], tol_mm: float = 0.3
) -> float | None:
    """Snap a measured length to the nearest standard length within tolerance."""
    for l_std in standard_lengths:
        if abs(raw_length - float(l_std)) <= tol_mm:
            return float(l_std)
    return None


def recognize_iso4762(
    shape: ShapeFeatures,
    table: StandardTable,
    length_tol_mm: float = 0.3,
) -> Designation | None:
    """Recognize ISO 4762 socket head cap screw."""
    # Must have at least 2 outer cylinders (shank + head)
    outer_cyls = [c for c in shape.cylinders if not c.is_inner]
    if len(outer_cyls) < 2:
        return None

    # Sort outer cylinders by radius ascending: cyl[0] is shank, cyl[-1] is head
    outer_cyls.sort(key=lambda c: c.radius_mm)
    shank = outer_cyls[0]
    head = outer_cyls[-1]

    d_meas = shank.radius_mm * 2.0
    l_meas = shank.length_mm
    dk_meas = head.radius_mm * 2.0
    k_meas = head.length_mm

    tol = table.tolerance_mm
    tol_d = float(tol.get("d", 0.15))
    tol_dk = float(tol.get("dk", 0.25))
    tol_k = float(tol.get("k", 0.25))

    best_match: dict[str, Any] | None = None
    best_len: float | None = None

    for row in table.sizes:
        d_nom = float(row.get("d", 0.0))
        dk_nom = float(row.get("dk", 0.0))
        k_nom = float(row.get("k", 0.0))

        if (
            abs(d_meas - d_nom) <= tol_d
            and abs(dk_meas - dk_nom) <= tol_dk
            and abs(k_meas - k_nom) <= tol_k
        ):
            # Check length snapping
            snapped = snap_length(l_meas, table.lengths_mm or [], tol_mm=length_tol_mm)
            if snapped is not None:
                best_match = row
                best_len = snapped
                break

    if best_match is not None and best_len is not None:
        size_name = str(best_match.get("size", f"M{round(d_meas)}"))
        return Designation(
            standard=table.standard,
            key=table.key,
            family=table.family,
            size=size_name,
            length_mm=best_len,
            score=1.0,
        )
    return None


def recognize_iso4032(
    shape: ShapeFeatures,
    table: StandardTable,
) -> Designation | None:
    """Recognize ISO 4032 hex nut."""
    # Must have 1 inner cylinder (through bore)
    inner_cyls = [c for c in shape.cylinders if c.is_inner]
    if not inner_cyls:
        return None

    bore = inner_cyls[0]
    d_meas = bore.radius_mm * 2.0
    m_meas = bore.length_mm

    tol = table.tolerance_mm
    tol_d = float(tol.get("d", 0.2))
    tol_m = float(tol.get("m", 0.3))

    for row in table.sizes:
        d_nom = float(row.get("d", 0.0))
        m_nom = float(row.get("m", 0.0))
        if abs(d_meas - d_nom) <= tol_d and abs(m_meas - m_nom) <= tol_m:
            size_name = str(row.get("size", f"M{round(d_meas)}"))
            return Designation(
                standard=table.standard,
                key=table.key,
                family=table.family,
                size=size_name,
                length_mm=None,
                score=1.0,
            )
    return None


def recognize_iso7089(
    shape: ShapeFeatures,
    table: StandardTable,
) -> Designation | None:
    """Recognize ISO 7089 plain washer."""
    inner_cyls = [c for c in shape.cylinders if c.is_inner]
    outer_cyls = [c for c in shape.cylinders if not c.is_inner]
    if not inner_cyls or not outer_cyls:
        return None

    d1_meas = inner_cyls[0].radius_mm * 2.0
    d2_meas = outer_cyls[0].radius_mm * 2.0
    h_meas = min(shape.obb_mm)  # thickness

    tol = table.tolerance_mm
    tol_d1 = float(tol.get("d1", 0.15))
    tol_d2 = float(tol.get("d2", 0.25))
    tol_h = float(tol.get("h", 0.1))

    for row in table.sizes:
        d1_nom = float(row.get("d1", 0.0))
        d2_nom = float(row.get("d2", 0.0))
        h_nom = float(row.get("h", 0.0))
        if (
            abs(d1_meas - d1_nom) <= tol_d1
            and abs(d2_meas - d2_nom) <= tol_d2
            and abs(h_meas - h_nom) <= tol_h
        ):
            size_name = str(row.get("size", f"M{round(d1_meas)}"))
            return Designation(
                standard=table.standard,
                key=table.key,
                family=table.family,
                size=size_name,
                length_mm=None,
                score=1.0,
            )
    return None


def recognize_bearing_iso15(
    shape: ShapeFeatures,
    table: StandardTable,
) -> Designation | None:
    """Recognize ISO 15 deep groove ball bearing (60xx, 62xx, 63xx)."""
    inner_cyls = [c for c in shape.cylinders if c.is_inner]
    outer_cyls = [c for c in shape.cylinders if not c.is_inner]
    if not inner_cyls or not outer_cyls:
        return None

    # Inner bore d, outer diameter D, width B (min OBB dim)
    d_meas = inner_cyls[0].radius_mm * 2.0
    d_outer_meas = outer_cyls[0].radius_mm * 2.0
    b_meas = min(shape.obb_mm)

    tol = table.tolerance_mm
    tol_d = float(tol.get("d", 0.1))
    tol_d_outer = float(tol.get("D", 0.15))
    tol_b = float(tol.get("B", 0.15))

    for row in table.sizes:
        d_nom = float(row.get("d", 0.0))
        d_out_nom = float(row.get("D", 0.0))
        b_nom = float(row.get("B", 0.0))
        if (
            abs(d_meas - d_nom) <= tol_d
            and abs(d_outer_meas - d_out_nom) <= tol_d_outer
            and abs(b_meas - b_nom) <= tol_b
        ):
            size_name = str(row.get("size", ""))
            return Designation(
                standard=table.standard,
                key=table.key,
                family=table.family,
                size=size_name,
                length_mm=None,
                score=1.0,
            )
    return None


def recognize_iso2338(
    shape: ShapeFeatures,
    table: StandardTable,
    length_tol_mm: float = 0.3,
) -> Designation | None:
    """Recognize ISO 2338 parallel dowel pin."""
    outer_cyls = [c for c in shape.cylinders if not c.is_inner]
    inner_cyls = [c for c in shape.cylinders if c.is_inner]
    if len(outer_cyls) != 1 or len(inner_cyls) != 0:
        return None

    cyl = outer_cyls[0]
    d_meas = cyl.radius_mm * 2.0
    # Total pin length along cylindrical axis is max dimension in OBB
    l_meas = max(shape.obb_mm)

    tol = table.tolerance_mm
    tol_d = float(tol.get("d", 0.05))

    for row in table.sizes:
        d_nom = float(row.get("d", 0.0))
        if abs(d_meas - d_nom) <= tol_d:
            snapped = snap_length(l_meas, table.lengths_mm or [], tol_mm=length_tol_mm)
            if snapped is not None:
                size_name = str(row.get("size", str(round(d_meas))))
                return Designation(
                    standard=table.standard,
                    key=table.key,
                    family=table.family,
                    size=size_name,
                    length_mm=snapped,
                    score=1.0,
                )
    return None


def recognize_iso3601(
    shape: ShapeFeatures,
    table: StandardTable,
) -> Designation | None:
    """Recognize ISO 3601 metric O-ring."""
    torus_share = shape.face_hist.get("torus", 0.0)
    if torus_share < 0.5:
        return None

    # Cross section thickness d2 = min OBB dimension
    d2_meas = min(shape.obb_mm)
    r_section = d2_meas / 2.0

    # Volume of torus = 2 * pi^2 * R * r^2
    # R = V / (2 * pi^2 * r^2)
    # Inner diameter d1 = 2 * (R - r)
    v = shape.volume_mm3
    denom = 2.0 * (math.pi**2) * (r_section**2)
    if denom > 0:
        r_major = v / denom
        d1_meas = 2.0 * (r_major - r_section)
    else:
        d1_meas = max(shape.obb_mm) - 2.0 * d2_meas

    tol = table.tolerance_mm
    tol_d1 = float(tol.get("d1", 0.25))
    tol_d2 = float(tol.get("d2", 0.1))

    for row in table.sizes:
        d1_nom = float(row.get("d1", 0.0))
        d2_nom = float(row.get("d2", 0.0))
        if abs(d1_meas - d1_nom) <= tol_d1 and abs(d2_meas - d2_nom) <= tol_d2:
            size_name = str(row.get("size", f"{round(d1_meas)}x{round(d2_meas, 1)}"))
            return Designation(
                standard=table.standard,
                key=table.key,
                family=table.family,
                size=size_name,
                length_mm=None,
                score=1.0,
            )
    return None


def recognize_standard_part(
    shape: ShapeFeatures,
    standards: dict[str, StandardTable],
    tolerances: dict[str, float] | None = None,
) -> Designation | None:
    """Run all standard part recognisers against the geometry features."""
    snap_tol = float((tolerances or {}).get("length_snap_mm", 0.3))

    # Fasteners: ISO 4762
    for name, std in standards.items():
        if "iso4762" in name.lower() or std.key == "ISO4762":
            desig = recognize_iso4762(shape, std, length_tol_mm=snap_tol)
            if desig:
                return desig

    # Hex nut: ISO 4032
    for name, std in standards.items():
        if "iso4032" in name.lower() or std.key == "ISO4032":
            desig = recognize_iso4032(shape, std)
            if desig:
                return desig

    # Plain washer: ISO 7089
    for name, std in standards.items():
        if "iso7089" in name.lower() or std.key == "ISO7089":
            desig = recognize_iso7089(shape, std)
            if desig:
                return desig

    # Bearings: ISO 15 (60xx/62xx/63xx)
    for name, std in standards.items():
        if "bearing" in name.lower() or std.key == "ISO15":
            desig = recognize_bearing_iso15(shape, std)
            if desig:
                return desig

    # Dowel pins: ISO 2338
    for name, std in standards.items():
        if "iso2338" in name.lower() or std.key == "ISO2338":
            desig = recognize_iso2338(shape, std, length_tol_mm=snap_tol)
            if desig:
                return desig

    # O-rings: ISO 3601
    for name, std in standards.items():
        if "oring" in name.lower() or "iso3601" in name.lower() or std.key == "ISO3601":
            desig = recognize_iso3601(shape, std)
            if desig:
                return desig

    return None
