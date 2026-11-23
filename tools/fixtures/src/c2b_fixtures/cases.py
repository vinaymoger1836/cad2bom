"""The golden cases B1, B2, B3 (stand-in) and B6 as Spec trees (docs/10 §4)."""

from typing import Any

import cadquery as cq

from c2b_fixtures import parts as P
from c2b_fixtures.model import Spec
from c2b_library.loader import Library
from c2b_library.schemas import StandardTable


def fmt(x: float) -> str:
    return str(int(x)) if float(x).is_integer() else str(x)


class Tables:
    def __init__(self, lib: Library) -> None:
        self.by_key: dict[str, StandardTable] = {t.key: t for t in lib.standards.values()}

    def get(self, key: str) -> StandardTable:
        return self.by_key[key]


def _designation(t: StandardTable, size: str, length: float | None) -> dict[str, Any]:
    return {"standard": t.standard, "key": t.key, "family": t.family, "size": size, "length_mm": length}


def screw(tb: Tables, size: str, length: float, qty: int, name: str, key: str = "ISO4762") -> Spec:
    t = tb.get(key)
    return Spec(
        name=name, cls=t.class_, shape=P.socket_head_screw(t, size, length), qty=qty,
        designation=_designation(t, size, length), size_text=f"{size}x{fmt(length)}",
        standard_text=t.standard,
    )


def nut(tb: Tables, size: str, qty: int, name: str) -> Spec:
    t = tb.get("ISO4032")
    return Spec(name=name, cls=t.class_, shape=P.hex_nut(t, size), qty=qty,
                designation=_designation(t, size, None), size_text=size, standard_text=t.standard)


def washer(tb: Tables, size: str, qty: int, name: str) -> Spec:
    t = tb.get("ISO7089")
    return Spec(name=name, cls=t.class_, shape=P.washer(t, size), qty=qty,
                designation=_designation(t, size, None), size_text=size, standard_text=t.standard)


def bearing(tb: Tables, designation: str, name: str) -> Spec:
    t = tb.get("ISO15")
    return Spec(name=name, cls=t.class_, shape=P.ball_bearing(t, designation),
                designation=_designation(t, designation, None), designation_text=designation)


def dowel(tb: Tables, size: str, length: float, name: str) -> Spec:
    t = tb.get("ISO2338")
    return Spec(name=name, cls=t.class_, shape=P.dowel_pin(t, size, length),
                designation=_designation(t, size, length), size_text=f"{size}x{fmt(length)}",
                standard_text=t.standard)


def oring(tb: Tables, size: str, name: str) -> Spec:
    t = tb.get("ISO3601")
    return Spec(name=name, cls=t.class_, shape=P.o_ring(t, size),
                designation=_designation(t, size, None), size_text=size, standard_text=t.standard)


def bbox_text(shape: cq.Shape) -> str:
    bb = shape.BoundingBox()
    dims = sorted([bb.xlen, bb.ylen, bb.zlen], reverse=True)
    return "x".join(fmt(round(v)) for v in dims)


# --- B1: fastener zoo ----------------------------------------------------------------------------


def case_b1(lib: Library) -> Spec:
    tb = Tables(lib)
    items = [
        screw(tb, "M3", 10, 1, "Part1"),
        screw(tb, "M4", 12, 8, "Part2"),
        screw(tb, "M5", 16, 1, "Part3"),
        screw(tb, "M6", 20, 1, "Part4"),
        screw(tb, "M8", 25, 1, "Part5"),
        nut(tb, "M4", 8, "Part6"),
        nut(tb, "M6", 1, "Part7"),
        washer(tb, "M4", 8, "Part8"),
        washer(tb, "M6", 1, "Part9"),
        bearing(tb, "6201", "Part10"),
        dowel(tb, "6", 20, "Part11"),
        oring(tb, "20x2", "Part12"),
    ]
    return Spec("B1_FASTENER_ZOO", "assembly.product", children=items)


# --- B2: bracket assembly -------------------------------------------------------------------------


def case_b2(lib: Library) -> Spec:
    tb = Tables(lib)
    bracket_shape = P.bent_u_bracket()
    shaft_shape = P.turned_shaft()
    cover_shape = P.moulded_cover()
    block_shape = P.aluminium_block()
    group = Spec(
        "FASTENERS_GRP", "assembly.phantom", qty=2, phantom=True,
        children=[
            screw(tb, "M4", 12, 2, "Part1"),
            washer(tb, "M4", 2, "Part2"),
            nut(tb, "M4", 2, "Part3"),
        ],
        note="Modelling-only group (name rule _grp); collapsed in the MBOM",
    )
    return Spec(
        "BRACKET_ASSY", "assembly.product",
        children=[
            Spec("Bracket", "sheetmetal.bracket", shape=bracket_shape, dims_text=bbox_text(bracket_shape) + " T2",
                 note="2 mm sheet metal, two bends"),
            Spec("Shaft", "machined.turned.shaft", shape=shaft_shape, dims_text="Ø12x80"),
            Spec("Cover", "moulded.cover", shape=cover_shape, dims_text=bbox_text(cover_shape),
                 note="2 mm wall, drafted"),
            Spec("Block", "machined.block", shape=block_shape, dims_text=bbox_text(block_shape)),
            group,
        ],
    )


# --- B3: fan stand-in -----------------------------------------------------------------------------


def case_b3(lib: Library) -> Spec:
    """13 solids: canopy, downrod, motor, hub, mount plate, 3 x (blade + arm), light kit, shade.

    STAND-IN for the "13-solid fan STEP from the S2 demo" which is not in the repository. It models
    3 blades; the details text says 4 (the conflict B3 must raise as a question).
    """
    blade = Spec("Blade", "moulded.blade", shape=P.fan_blade(), dims_text="450x120x4")
    arm = Spec("Blade_Arm", "sheetmetal.bracket", shape=P.flat_bar(140, 25, 3, [(15, 0), (125, 0)], 6),
               dims_text="140x25x3")
    blade_locs = [
        cq.Location(cq.Vector(0, 0, 0), cq.Vector(0, 0, 1), angle) for angle in (0.0, 120.0, 240.0)
    ]
    blade_assy = Spec(
        "BladeAssy", "assembly.subassembly", qty=3, locs=blade_locs,
        children=[blade, arm],
    )
    # Fan axis is Z. Stack from the ceiling down.
    canopy = P.hollow_cylinder(120, 30, 1.5, closed_end=True)
    rod = P.hollow_cylinder(25, 300, 1.5)
    motor = P.hollow_cylinder(180, 70, 3.0, closed_end=True)
    hub = P.disc(100, 25, bore=20)
    plate = P.flat_bar(150, 40, 3, [(25, 0), (125, 0)], 6)
    kit = P.hollow_cylinder(200, 60, 2.0, closed_end=True)
    shade = P.dome_shell(180, 3)
    return Spec(
        "FAN", "assembly.product",
        children=[
            Spec("Canopy", "sheetmetal.cover", shape=canopy, dims_text=bbox_text(canopy)),
            Spec("Downrod", "extrusion.tube.round", shape=rod, dims_text="Ø25x300"),
            Spec("Motor", "electrical.motor.ac_induction", shape=motor, dims_text=bbox_text(motor)),
            Spec("Hub", "machined.turned.hub", shape=hub, dims_text="Ø100x25"),
            Spec("Mount_Plate", "sheetmetal.plate.mounting", shape=plate, dims_text="150x40x3"),
            blade_assy,
            Spec("LightKit", "electrical.lighting.led_module", shape=kit, dims_text=bbox_text(kit)),
            Spec("Shade", "glass.shade", shape=shade, dims_text=bbox_text(shade)),
        ],
    )


# --- B6: unknown product type ---------------------------------------------------------------------


def case_b6(lib: Library) -> Spec:
    """Handheld controller, a product type that is not in the archetypes. Parts are `Body1..Body9`
    with mixed geometry; no standard parts. Classes are a truth SET, not one value (see
    golden/B6_unknown/expected/llm_expectations.json)."""

    def part(n: int, shape: cq.Shape, primary: str, ok: list[str], note: str) -> Spec:
        return Spec(f"Body{n}", primary, shape=shape, acceptable_classes=ok, note=note)

    clip = P.bent_clip(20, 8, 0.5)
    return Spec(
        "HANDHELD_CONTROLLER", "assembly.product",
        children=[
            part(1, P.shell_box(70, 40, 15, 1.8, open_top=False), "moulded.housing.top",
                 ["moulded.housing.top", "moulded.housing", "moulded.cover"], "thin-wall open shell, 1.8 mm"),
            part(2, P.shell_box(70, 40, 12, 1.8, open_top=True), "moulded.housing.bottom",
                 ["moulded.housing.bottom", "moulded.housing"], "thin-wall open shell, 1.8 mm"),
            part(3, P.plate_with_holes(60, 30, 1.6, [(-25, -10), (25, 10), (-25, 10), (25, -10)], 3.0),
                 "electrical.pcb", ["electrical.pcb", "electrical.pcba"], "1.6 mm plate with 4 holes"),
            part(4, P.knob(20, 12, 6), "moulded.knob",
                 ["moulded.knob", "machined.turned.knob"], "rotational part with bore"),
            part(5, P.cylinder(8, 4), "moulded.button", ["moulded.button", "moulded.cap"], "small cylinder"),
            part(6, P.coil_spring(0.8, 6, 2.5, 15), "spring.compression",
                 ["spring.compression"], "helix, 6 turns"),
            part(7, clip, "sheetmetal.clip", ["sheetmetal.clip", "fastener.clip", "spring.clip"],
                 "0.5 mm sheet strip, one bend"),
            part(8, P.rect_ring((66, 36), (60, 30), 1.0), "seal.gasket.flat",
                 ["seal.gasket.flat", "seal.gasket.formed", "seal.gasket.foam"], "1 mm flat frame"),
            part(9, P.cylinder(18, 65), "electrical.battery.li_ion",
                 ["electrical.battery.li_ion", "electrical.battery.pack"], "18 x 65 cylinder (18650-size)"),
        ],
    )

