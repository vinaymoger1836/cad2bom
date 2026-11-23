"""CadQuery builders for standard parts and the custom parts of the golden assemblies.

Standard-part dimensions are read from the library tables (`StandardTable` rows); nothing is
hard-coded here except modelling choices that the tables do not define (socket depth, chamfers).
Threads are plain cylinders: docs/04 §4 treats thread evidence as optional.
"""

import math

import cadquery as cq

from c2b_library.schemas import StandardTable

HEX_FACTOR = 1.0 / math.cos(math.radians(30))  # across-flats -> circumscribed diameter


def row_of(table: StandardTable, size: str) -> dict[str, str | float | int]:
    for r in table.sizes:
        if str(r["size"]) == size:
            return r
    raise KeyError(f"{table.standard}: no size {size!r}")


def num(row: dict[str, str | float | int], key: str) -> float:
    v = row[key]
    if not isinstance(v, int | float):
        raise TypeError(f"{key} is not numeric: {v!r}")
    return float(v)


def _solid(wp: cq.Workplane) -> cq.Shape:
    solids = wp.solids().vals()
    if len(solids) != 1:
        raise RuntimeError(f"expected one solid, got {len(solids)}")
    return solids[0]


# --- standard parts -----------------------------------------------------------------------------


def socket_head_screw(table: StandardTable, size: str, length: float) -> cq.Shape:
    """ISO 4762-style cap screw: head on top (z 0..k), shank below (z -L..0), hex socket in head."""
    r = row_of(table, size)
    d, dk, k, s = num(r, "d"), num(r, "dk"), num(r, "k"), num(r, "s")
    head = cq.Workplane("XY").circle(dk / 2).extrude(k)
    socket = cq.Workplane("XY").workplane(offset=k * 0.5).polygon(6, s * HEX_FACTOR).extrude(k)
    shank = cq.Workplane("XY").circle(d / 2).extrude(-length)
    return _solid(head.union(shank).cut(socket))


def hex_nut(table: StandardTable, size: str) -> cq.Shape:
    r = row_of(table, size)
    d, s, m = num(r, "d"), num(r, "s"), num(r, "m")
    body = cq.Workplane("XY").polygon(6, s * HEX_FACTOR).extrude(m)
    bore = cq.Workplane("XY").circle(d / 2).extrude(m)
    return _solid(body.cut(bore))


def washer(table: StandardTable, size: str) -> cq.Shape:
    r = row_of(table, size)
    d1, d2, h = num(r, "d1"), num(r, "d2"), num(r, "h")
    return _solid(cq.Workplane("XY").circle(d2 / 2).circle(d1 / 2).extrude(h))


def dowel_pin(table: StandardTable, size: str, length: float) -> cq.Shape:
    r = row_of(table, size)
    d, c = num(r, "d"), num(r, "c")
    pin = cq.Workplane("XY").circle(d / 2).extrude(length)
    return _solid(pin.faces(">Z or <Z").chamfer(c * 0.9))


def ball_bearing(table: StandardTable, designation: str) -> cq.Shape:
    """Simplified deep groove bearing: one annulus d x D x B with chamfered edges."""
    r = row_of(table, designation)
    d, big_d, b = num(r, "d"), num(r, "D"), num(r, "B")
    ring = cq.Workplane("XY").circle(big_d / 2).circle(d / 2).extrude(b)
    return _solid(ring.edges().chamfer(min(0.4, (big_d - d) / 20)))


def o_ring(table: StandardTable, size: str) -> cq.Shape:
    r = row_of(table, size)
    d1, d2 = num(r, "d1"), num(r, "d2")
    torus = cq.Solid.makeTorus(d1 / 2 + d2 / 2, d2 / 2)
    return torus


# --- custom parts (B2) ---------------------------------------------------------------------------


def bent_u_bracket(length: float = 60.0, width: float = 40.0, height: float = 30.0,
                   t: float = 2.0, r_outer: float = 4.0, hole_d: float = 4.5) -> cq.Shape:
    """Sheet-metal U bracket: constant thickness t, two bends (inner radius r_outer - t)."""
    ri = r_outer - t
    c = math.sqrt(0.5)
    pts = cq.Workplane("XZ").moveTo(0, height).lineTo(0, r_outer)
    pts = pts.threePointArc((r_outer * (1 - c), r_outer * (1 - c)), (r_outer, 0))
    pts = pts.lineTo(length - r_outer, 0)
    pts = pts.threePointArc((length - r_outer * (1 - c), r_outer * (1 - c)), (length, r_outer))
    pts = pts.lineTo(length, height).lineTo(length - t, height).lineTo(length - t, r_outer)
    pts = pts.threePointArc(
        (length - r_outer + ri * c, r_outer - ri * c), (length - r_outer, t)
    )
    pts = pts.lineTo(r_outer, t)
    pts = pts.threePointArc((r_outer - ri * c, r_outer - ri * c), (t, r_outer))
    pts = pts.lineTo(t, height).close()
    body = pts.extrude(-width)  # XZ normal is -Y, so -width extrudes toward +Y
    cutters = (
        cq.Workplane("XY")
        .workplane(offset=-1)
        .pushPoints([(length * 0.3, width / 2), (length * 0.7, width / 2)])
        .circle(hole_d / 2)
        .extrude(t + 2)
    )
    return _solid(body.cut(cutters))


def turned_shaft(diameter: float = 12.0, length: float = 80.0) -> cq.Shape:
    shaft = cq.Workplane("XY").circle(diameter / 2).extrude(length)
    shaft = shaft.faces(">Z or <Z").chamfer(1.0)
    groove = (
        cq.Workplane("XY").workplane(offset=length - 18)
        .circle(diameter / 2 + 1).circle(diameter / 2 - 1.0).extrude(2)
    )
    return _solid(shaft.cut(groove))


def moulded_cover(length: float = 60.0, width: float = 40.0, height: float = 18.0,
                  wall: float = 2.0) -> cq.Shape:
    """Open-bottom cover: 2 mm walls, drafted sides, rounded corners and two internal screw bosses."""
    outer = cq.Workplane("XY").rect(length, width).extrude(height, taper=1.5)
    cover = outer.faces("<Z").shell(-wall)
    for x in (-length / 2 + 9, length / 2 - 9):  # bosses hang from the inside of the top wall
        boss = (
            cq.Workplane("XY").workplane(offset=height - wall - 10)
            .center(x, 0).circle(3.5).circle(1.5).extrude(10.5)
        )
        cover = cover.union(boss)
    return _solid(cover)


def aluminium_block(x: float = 40.0, y: float = 30.0, z: float = 25.0) -> cq.Shape:
    block = cq.Workplane("XY").box(x, y, z, centered=(True, True, False))
    through = cq.Workplane("XY").circle(5).extrude(z)
    tapped = (
        cq.Workplane("XY").workplane(offset=z - 15)
        .pushPoints([(-14, -9), (14, -9), (-14, 9), (14, 9)]).circle(1.65).extrude(15)
    )
    return _solid(block.cut(through).cut(tapped))


# --- fan stand-in (B3) --------------------------------------------------------------------------


def hollow_cylinder(outer_d: float, length: float, wall: float, closed_end: bool = False) -> cq.Shape:
    wp = cq.Workplane("XY").circle(outer_d / 2).extrude(length)
    inner_len = length - wall if closed_end else length
    return _solid(wp.cut(cq.Workplane("XY").circle(outer_d / 2 - wall).extrude(inner_len)))


def disc(diameter: float, thickness: float, bore: float = 0.0) -> cq.Shape:
    wp = cq.Workplane("XY").circle(diameter / 2)
    if bore:
        wp = wp.circle(bore / 2)
    return _solid(wp.extrude(thickness))


def flat_bar(length: float, width: float, thickness: float, holes: list[tuple[float, float]],
             hole_d: float) -> cq.Shape:
    bar = cq.Workplane("XY").box(length, width, thickness, centered=(False, True, False))
    cut = cq.Workplane("XY").workplane(offset=-1).pushPoints(holes).circle(hole_d / 2).extrude(thickness + 2)
    return _solid(bar.cut(cut))


def fan_blade(length: float = 450.0, width: float = 120.0, thickness: float = 4.0) -> cq.Shape:
    plate = cq.Workplane("XY").slot2D(length, width, 0).extrude(thickness)
    return _solid(plate.translate((length / 2, 0, 0)))


def dome_shell(diameter: float, wall: float) -> cq.Shape:
    """Half-sphere shell, open at the bottom."""
    outer = cq.Workplane("XY").sphere(diameter / 2)
    inner = cq.Workplane("XY").sphere(diameter / 2 - wall)
    cut_below = cq.Workplane("XY").box(diameter * 2, diameter * 2, diameter, centered=(True, True, False)).translate((0, 0, -diameter))
    return _solid(outer.cut(inner).cut(cut_below))


# --- unknown product (B6) ------------------------------------------------------------------------


def shell_box(length: float, width: float, height: float, wall: float, open_top: bool) -> cq.Shape:
    box = cq.Workplane("XY").box(length, width, height, centered=(True, True, False))
    face = ">Z" if open_top else "<Z"
    return _solid(box.faces(face).shell(-wall))


def plate_with_holes(length: float, width: float, thickness: float,
                     holes: list[tuple[float, float]], hole_d: float) -> cq.Shape:
    plate = cq.Workplane("XY").box(length, width, thickness, centered=(True, True, False))
    cut = cq.Workplane("XY").workplane(offset=-1).pushPoints(holes).circle(hole_d / 2).extrude(thickness + 2)
    return _solid(plate.cut(cut))


def knob(diameter: float, height: float, bore: float) -> cq.Shape:
    k = cq.Workplane("XY").circle(diameter / 2).extrude(height).faces(">Z").edges().fillet(1.5)
    return _solid(k.cut(cq.Workplane("XY").circle(bore / 2).extrude(height - 3)))


def coil_spring(wire_d: float, coil_d: float, pitch: float, height: float) -> cq.Shape:
    helix = cq.Wire.makeHelix(pitch, height, coil_d / 2)
    profile = cq.Workplane("XZ").center(coil_d / 2, 0).circle(wire_d / 2)
    return _solid(profile.sweep(cq.Workplane(obj=helix), isFrenet=True))


def bent_clip(length: float, width: float, t: float) -> cq.Shape:
    """Thin sheet strip bent once (L section)."""
    leg = cq.Workplane("XY").box(length, width, t, centered=(False, True, False))
    up = cq.Workplane("XY").box(t, width, length * 0.5, centered=(False, True, False))
    return _solid(leg.union(up))


def rect_ring(outer: tuple[float, float], inner: tuple[float, float], t: float) -> cq.Shape:
    return _solid(
        cq.Workplane("XY").rect(*outer).rect(*inner).extrude(t)
    )


def cylinder(diameter: float, length: float) -> cq.Shape:
    return _solid(cq.Workplane("XY").circle(diameter / 2).extrude(length))


def volume_mm3(shape: cq.Shape) -> float:
    return float(shape.Volume())
