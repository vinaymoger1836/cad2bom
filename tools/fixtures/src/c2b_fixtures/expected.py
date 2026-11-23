"""Expected EBOM / MBOM JSON for a case, derived from the same Spec tree that built the STEP file.

Line shape follows `BomLine` in docs/05 §1 (subset). Rule ids are NOT asserted yet (the rules
engine arrives in P3); everything numeric is exact (quantities, standard sizes, volumes, masses).
"""

from string import Formatter
from typing import Any

from c2b_fixtures.model import Spec
from c2b_fixtures.parts import volume_mm3
from c2b_library.loader import Library
from c2b_library.schemas import ResolvedClass

PROFILE = "profiles/default.yaml"


class Ctx:
    def __init__(self, lib: Library) -> None:
        self.lib = lib
        self.classes: dict[str, ResolvedClass] = {c.code: c for c in lib.taxonomy.resolved()}
        self.profile = lib.profiles[PROFILE]
        self.materials = {m.code: m for m in lib.materials.materials}
        self.conf = lib.confidence

    def material_default(self, cls: ResolvedClass) -> dict[str, Any] | None:
        key = cls.material_default_profile_key
        if not key:
            return None
        d = self.profile.defaults[key]
        return {"key": key, "material": d.material, "finish": d.finish, "label": d.label}


def describe(ctx: Ctx, spec: Spec, cls: ResolvedClass, default: dict[str, Any] | None) -> str:
    """Noun-first description from the class template and the profile style (docs/04 §10)."""
    values: dict[str, str] = {
        "noun": cls.noun,
        "modifiers": ", ".join(cls.modifiers),
        "size": spec.size_text or "",
        "standard": spec.standard_text or "",
        "grade": (default or {}).get("label") or "",
        "designation": spec.designation_text or "",
        "dims": spec.dims_text or "",
        "material": spec.material_text
        or (ctx.materials[default["material"]].display if default else ""),
        "finish": (default or {}).get("finish") or "",
    }
    parts = [
        values.get(name, "")
        for _, name, _, _ in Formatter().parse(cls.description_template)
        if name
    ]
    text = ctx.profile.descriptions.separator.join(p for p in parts if p)
    case = ctx.profile.descriptions.case
    return text.upper() if case == "upper" else text


def _order_children(ctx: Ctx, children: list[Spec]) -> list[Spec]:
    """Find-number order: custom parts and assemblies first, standard parts last (profile)."""
    if not ctx.profile.find_numbers.standard_parts_last:
        return list(children)
    return [c for c in children if not c.is_standard] + [c for c in children if c.is_standard]


def build_ebom(ctx: Ctx, root: Spec) -> tuple[list[dict[str, Any]], dict[str, float]]:
    """Walk the tree; return EBOM lines (root excluded) and part volumes by STEP name."""
    lines: list[dict[str, Any]] = []
    volumes: dict[str, float] = {}
    fn = ctx.profile.find_numbers

    def walk(spec: Spec, parent_id: str | None, level: int, qty_parent_total: int) -> None:
        for i, child in enumerate(_order_children(ctx, spec.children)):
            cls = ctx.classes[child.cls]
            default = ctx.material_default(cls)
            line_id = f"L-{len(lines) + 1:04d}"
            qty_total = child.qty * qty_parent_total
            line: dict[str, Any] = {
                "id": line_id,
                "parent": parent_id,
                "level": level,
                "find_no": fn.start + fn.step * i,
                "cad_name": child.name,
                "class": child.cls,
                "noun": cls.noun,
                "modifiers": cls.modifiers,
                "designation": child.designation,
                "qty_per_parent": child.qty,
                "uom": cls.uom,
                "qty_total": qty_total,
                "description": describe(ctx, child, cls, default),
                "description_exact": child.is_standard or child.is_assembly,
                "make_buy": cls.make_buy_default,
                "origin": "std_recogniser" if child.is_standard else "cad",
                "review": "derived",
                "phantom": child.phantom,
                "buy_as_assembly": cls.buy_as_assembly,
            }
            if child.shape is not None:
                vol = round(volume_mm3(child.shape), 2)
                volumes[child.name] = vol
                line["cad_volume_mm3"] = vol
                if default:
                    mat = ctx.materials[default["material"]]
                    line["material"] = {
                        "value": default["material"],
                        "source": "library",
                        "confidence": ctx.conf.source_base["library"],
                    }
                    if default["finish"]:
                        line["finish"] = {"value": default["finish"], "source": "library"}
                    line["mass_g"] = round(vol / 1000.0 * mat.density_g_cm3, 4)
                    if child.is_standard and default["label"]:
                        line["open_items"] = ["grade assumed: " + default["label"]]
                    else:
                        line["open_items"] = ["material assumed: " + mat.display]
                else:
                    line["material"] = None
                    line["mass_g"] = None
                    line["open_items"] = ["material unknown"]
            else:
                line["open_items"] = []
            if child.note:
                line["note"] = child.note
            lines.append(line)
            if child.is_assembly:
                walk(child, line_id, level + 1, qty_total)

    walk(root, None, 1, 1)
    return lines, volumes


def mass_rollup_g(lines: list[dict[str, Any]]) -> float:
    return round(sum(row["qty_total"] * row["mass_g"] for row in lines if row.get("mass_g")), 4)


def build_mbom(ctx: Ctx, ebom: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Collapse phantoms: their children move up to the phantom's parent (docs/04 §10)."""
    by_id = {line["id"]: line for line in ebom}
    out: list[dict[str, Any]] = []
    fn = ctx.profile.find_numbers

    def effective_parent(line: dict[str, Any]) -> str | None:
        p = line["parent"]
        while p is not None and by_id[p]["phantom"]:
            p = by_id[p]["parent"]
        return p

    for line in ebom:
        if line["phantom"]:
            continue
        parent = effective_parent(line)
        qty_parent = line["qty_per_parent"]
        p = line["parent"]
        while p is not None and by_id[p]["phantom"]:
            qty_parent *= by_id[p]["qty_per_parent"]
            p = by_id[p]["parent"]
        out.append(
            {
                "id": f"M-{len(out) + 1:04d}",
                "parent": parent and f"M-ref:{parent}",
                "ebom_refs": [line["id"]],
                "class": line["class"],
                "description": line["description"],
                "qty_per_parent": qty_parent,
                "qty_total": line["qty_total"],
                "uom": line["uom"],
                "make_buy": line["make_buy"],
            }
        )
    # resolve parent refs to MBOM ids and assign find numbers per parent
    ref_to_m = {row["ebom_refs"][0]: row["id"] for row in out}
    counters: dict[str | None, int] = {}
    for m in out:
        if m["parent"]:
            m["parent"] = ref_to_m[m["parent"].removeprefix("M-ref:")]
        n = counters.get(m["parent"], 0)
        m["find_no"] = fn.start + fn.step * n
        counters[m["parent"]] = n + 1
    return out
