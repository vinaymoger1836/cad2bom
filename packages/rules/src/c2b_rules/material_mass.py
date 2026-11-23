"""Material resolution and mass calculation (docs/04 §6).

Material priority:
  user > CAD property > datasheet > user BoM file > class default for profile > unknown.
Mass priority:
  user > authored mass in CAD > volume x density > datasheet > unknown.
"""

from dataclasses import dataclass, field

from c2b_domain.bom import Designation
from c2b_domain.cad import CadNode, ShapeFeatures
from c2b_library.loader import Library
from c2b_library.schemas import Profile


@dataclass
class MaterialMassResult:
    """Resolved material, finish, mass, and open items."""

    material_code: str | None = None
    material_source: str = "library"
    material_confidence: float = 0.5
    material_display: str | None = None
    finish_code: str | None = None
    finish_source: str = "library"
    grade_label: str | None = None
    mass_g: float | None = None
    mass_source: str = "engine"
    mass_confidence: float = 0.9
    rule_ids: list[str] = field(default_factory=list)
    open_items: list[str] = field(default_factory=list)


def resolve_material_and_mass(
    node: CadNode,
    shape: ShapeFeatures | None,
    cls_code: str,
    desig: Designation | None,
    library: Library,
    profile_name: str = "default",
) -> MaterialMassResult:
    """Resolve material, finish, mass, and open items for a node."""
    profile: Profile | None = None
    for p_key, p_val in library.profiles.items():
        if profile_name in p_key or p_val.name == profile_name:
            profile = p_val
            break
    if profile is None and library.profiles:
        profile = next(iter(library.profiles.values()))

    # Find class definition in taxonomy
    class_map = {c.code: c for c in library.taxonomy.resolved()}
    class_def = class_map.get(cls_code)
    prof_key = class_def.material_default_profile_key if class_def else None

    materials_map = {m.code: m for m in library.materials.materials}

    # Step 1: Material priority
    mat_code: str | None = None
    mat_source = "library"
    mat_conf = 0.5
    finish_code: str | None = None
    grade_label: str | None = None
    open_items: list[str] = []
    rule_ids: list[str] = []

    # 1.1 Authored CAD property
    if "material" in node.props:
        mat_code = node.props["material"]
        mat_source = "cad_meta"
        mat_conf = 0.9
        rule_ids.append("R-MAT-CAD")
    # 1.2 Profile default
    elif profile and prof_key and prof_key in profile.defaults:
        def_item = profile.defaults[prof_key]
        mat_code = def_item.material
        finish_code = def_item.finish
        grade_label = def_item.label
        mat_source = "library"
        mat_conf = 0.5
        rule_ids.append("R-MAT-DEF")

    # Step 2: Open items for assumed materials / grades
    mat_display: str | None = None
    if mat_code and mat_code in materials_map:
        mat_meta = materials_map[mat_code]
        mat_display = mat_meta.display

    if desig is not None and grade_label:
        open_items.append(f"grade assumed: {grade_label}")
    elif mat_display and mat_source == "library":
        open_items.append(f"material assumed: {mat_display}")
    elif mat_code is None and node.kind == "part":
        open_items.append("material unknown")

    # Step 3: Mass calculation
    mass_g: float | None = None
    if shape is not None and shape.volume_mm3 > 0 and mat_code and mat_code in materials_map:
        density = float(materials_map[mat_code].density_g_cm3)
        # volume in mm^3, density in g/cm^3 -> 1 cm^3 = 1000 mm^3
        # mass in g = volume_mm3 * (density / 1000.0)
        mass_val = shape.volume_mm3 * (density / 1000.0)
        mass_g = round(mass_val, 4)
        rule_ids.append("R-MSS-VOL")

    return MaterialMassResult(
        material_code=mat_code,
        material_source=mat_source,
        material_confidence=mat_conf,
        material_display=mat_display,
        finish_code=finish_code,
        finish_source="library",
        grade_label=grade_label,
        mass_g=mass_g,
        mass_source="engine",
        mass_confidence=0.9 if mass_g is not None else 0.0,
        rule_ids=rule_ids,
        open_items=open_items,
    )
