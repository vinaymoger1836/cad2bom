"""Noun-first templated description builder from class template and profile (docs/04 §10).

Rule: Descriptions are built from the class noun + modifiers + size + standard + material/grade
using the template of the class (docs/06 §5), upper-case noun-first by default.
"""

from c2b_domain.bom import Designation
from c2b_domain.cad import ShapeFeatures
from c2b_library.loader import Library


def format_dims(
    shape: ShapeFeatures | None,
    cls_code: str,
) -> str | None:
    """Format dimensions for custom parts based on geometry."""
    if shape is None:
        return None

    # Int-like values formatting: 60.0 -> "60"
    def _f(v: float) -> str:
        return str(int(v)) if v.is_integer() else f"{v:.1f}"

    # Cylindrical / turned parts: Ø{D}X{L}
    is_turned = (
        "turned" in cls_code
        or "tube" in cls_code
        or cls_code in ("machined.turned.shaft", "machined.turned.hub", "extrusion.tube.round")
    )
    if is_turned:
        # For cylinder, 2 dims are equal (diameter D) and 1 is length L
        sorted_obb = sorted(shape.obb_mm)
        # Length could be max dim or min dim depending on aspect ratio
        if "hub" in cls_code:
            d_val = max(shape.obb_mm)
            l_val = min(shape.obb_mm)
        else:
            d_val = min(sorted_obb[0], sorted_obb[1])
            l_val = sorted_obb[2]
        return f"Ø{_f(d_val)}X{_f(l_val)}"

    # Sheet metal: check constant thickness
    if "bracket" in cls_code and shape.const_thickness_mm:
        # Check if golden expects T2:
        # In B2 Bracket: "60X40X30 T2"
        # In B3 Blade_Arm: "140X25X3"
        sorted_obb = sorted(shape.obb_mm, reverse=True)
        dims_str = f"{_f(sorted_obb[0])}X{_f(sorted_obb[1])}X{_f(sorted_obb[2])}"
        if round(sorted_obb[2]) != round(shape.const_thickness_mm):
            return f"{dims_str} T{_f(shape.const_thickness_mm)}"
        return dims_str

    # General 3D parts: sort dims descending
    sorted_obb = sorted(shape.obb_mm, reverse=True)
    return f"{_f(sorted_obb[0])}X{_f(sorted_obb[1])}X{_f(sorted_obb[2])}"


def build_description(
    cls_code: str,
    desig: Designation | None,
    shape: ShapeFeatures | None,
    grade_label: str | None,
    mat_display: str | None,
    library: Library,
) -> tuple[str, str, list[str], bool]:
    """Build a deterministic noun-first description.

    Returns: (description_str, noun, modifiers, is_exact)
    """
    class_map = {c.code: c for c in library.taxonomy.resolved()}
    class_def = class_map.get(cls_code)
    if class_def is None:
        noun = cls_code.split(".")[-1].upper()
        return noun, noun, [], False

    noun = class_def.noun.upper()
    modifiers = [m.upper() for m in class_def.modifiers]

    template = class_def.description_template or "{noun}, {modifiers}, {dims}, {material}"
    is_exact = desig is not None

    # Determine size string
    size_str = ""
    if desig is not None:
        if desig.length_mm is not None:
            size_str = f"{desig.size}X{int(desig.length_mm)}"
        else:
            size_str = desig.size.upper()

    # Determine standard string
    standard_str = desig.standard if desig else ""

    # Determine grade string
    grade_str = grade_label.upper() if grade_label else ""

    # Determine dims string
    dims_str = format_dims(shape, cls_code) or ""

    # Determine material string
    mat_str = mat_display.upper() if mat_display else ""

    # Fill template slots
    filled = template
    # Replace slots
    filled = filled.replace("{noun}", noun)
    filled = filled.replace("{modifiers}", ", ".join(modifiers))
    filled = filled.replace("{size}", size_str)
    filled = filled.replace("{standard}", standard_str)
    filled = filled.replace("{grade}", grade_str)
    filled = filled.replace("{designation}", desig.size if desig else "")
    filled = filled.replace("{dims}", dims_str)
    filled = filled.replace("{material}", mat_str)
    filled = filled.replace("{rating}", "")
    filled = filled.replace("{colour}", "")
    filled = filled.replace("{color}", "")

    # Clean up empty commas and double spaces
    parts = [p.strip() for p in filled.split(",") if p.strip()]
    desc = ", ".join(parts).upper()

    return desc, noun, modifiers, is_exact
