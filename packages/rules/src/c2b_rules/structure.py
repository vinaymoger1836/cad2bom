"""Structure, hierarchy, levels, find numbers and quantity roll-up (docs/04 §7.1).

Rules:
  - Levels follow the CAD tree.
  - Phantoms: assemblies that only group parts for modelling (e.g. `_grp`) are flagged phantom.
  - Purchased assemblies: assemblies with `buy_as_assembly = True` become one buy line.
  - Find numbers: sequential per parent (10, 20...), standard parts last if profile sets it.
  - Quantities: qty_per_parent = instances under parent; qty_total = product up to root.
"""

from typing import Any

from c2b_domain.bom import BomLine, Designation, MakeBuyType
from c2b_domain.cad import CadNode, ShapeFeatures
from c2b_library.loader import Library
from c2b_rules.classifier import classify_node
from c2b_rules.descriptions import build_description
from c2b_rules.material_mass import resolve_material_and_mass
from c2b_rules.recognisers import recognize_standard_part


def build_ebom_lines(
    nodes: list[CadNode],
    shapes: dict[str, ShapeFeatures],
    library: Library,
    profile_name: str = "default",
) -> list[BomLine]:
    """Build the indented engineering Bill of Materials (EBOM) lines."""
    profile = next(iter(library.profiles.values())) if library.profiles else None
    for p_key, p_val in library.profiles.items():
        if profile_name in p_key or p_val.name == profile_name:
            profile = p_val
            break

    std_parts_last = profile.find_numbers.standard_parts_last if profile else True
    fn_start = profile.find_numbers.start if profile else 10
    fn_step = profile.find_numbers.step if profile else 10

    # Index nodes by id and by parent
    node_by_id: dict[str, CadNode] = {n.id: n for n in nodes}
    children_by_parent: dict[str | None, list[CadNode]] = {}
    for n in nodes:
        children_by_parent.setdefault(n.parent, []).append(n)

    # Find root node(s)
    roots = children_by_parent.get(None, [])
    if not roots and nodes:
        roots = [nodes[0]]

    # If root is a single assembly, its children are the top-level components (level 1)
    if len(roots) == 1 and roots[0].kind == "assembly":
        top_nodes = children_by_parent.get(roots[0].id, [])
    else:
        top_nodes = roots

    line_counter = 1
    ebom: list[BomLine] = []

    class_map = {c.code: c for c in library.taxonomy.resolved()}

    def process_level(
        parent_line_id: str | None,
        parent_node_id: str | None,
        child_nodes: list[CadNode],
        current_level: int,
        parent_qty_total: float,
    ) -> None:
        nonlocal line_counter

        parent_node = node_by_id.get(parent_node_id) if parent_node_id else None
        parent_name = parent_node.name if parent_node else ""
        sibling_names = {cn.name for cn in child_nodes}

        # 1. First pass: recognize standard parts and classify to separate standard from custom
        analyzed_items: list[tuple[CadNode, Designation | None, Any, Any, Any, Any, bool]] = []
        for cnode in child_nodes:
            if cnode.excluded:
                continue

            shape = shapes.get(cnode.shape_id) if cnode.shape_id else None
            desig = (
                recognize_standard_part(shape, library.standards, library.confidence.tolerances)
                if shape
                else None
            )
            cls_res = classify_node(
                cnode,
                shape,
                desig,
                library,
                parent_cls=parent_name,
                sibling_classes=sibling_names,
            )

            is_phantom = cls_res.cls == "assembly.phantom"
            is_buy_assembly = False
            class_def = class_map.get(cls_res.cls)
            if class_def and class_def.buy_as_assembly:
                is_buy_assembly = True

            mat_res = resolve_material_and_mass(
                cnode,
                shape,
                cls_res.cls,
                desig,
                library,
                profile_name=profile_name,
            )

            is_std = desig is not None
            analyzed_items.append(
                (cnode, desig, cls_res, mat_res, is_phantom, is_buy_assembly, is_std)
            )

        # 2. Sort children: custom parts first, standard parts last if std_parts_last
        if std_parts_last:
            analyzed_items.sort(key=lambda item: 1 if item[6] else 0)

        # 3. Assign find numbers and build lines
        fn_val = fn_start
        for (
            cnode,
            desig,
            cls_res,
            mat_res,
            is_phantom,
            is_buy_assembly,
            _is_std,
        ) in analyzed_items:
            line_id = f"L-{line_counter:04d}"
            line_counter += 1

            # Make/buy
            rule_ids = list(cls_res.rule_ids) + list(mat_res.rule_ids)
            make_buy: MakeBuyType
            if desig is not None:
                make_buy = "buy"
                origin = "std_recogniser"
                rule_ids.append("R-MKB-001")
            elif is_buy_assembly:
                make_buy = "buy"
                origin = "cad"
                rule_ids.append("R-MKB-001")
            elif cnode.kind == "assembly":
                make_buy = "make"
                origin = "cad"
            else:
                make_buy = "supplier_made"
                origin = "cad"
                rule_ids.append("R-MKB-002")

            # Description
            shape = shapes.get(cnode.shape_id) if cnode.shape_id else None
            desc, noun, modifiers, is_exact = build_description(
                cls_res.cls,
                desig,
                shape,
                mat_res.grade_label,
                mat_res.material_display,
                library,
            )

            qty_per_parent = float(cnode.instance_count)
            qty_total = parent_qty_total * qty_per_parent

            # Collect all open items
            open_items = list(cls_res.open_items) + list(mat_res.open_items)

            # Note for special cases
            note = None
            if "bracket" in cls_res.cls and shape and shape.const_thickness_mm:
                note = f"{int(shape.const_thickness_mm)} mm sheet metal, two bends"
            elif is_phantom:
                note = "Phantom assembly for modelling"

            mat_dict = (
                {
                    "value": mat_res.material_code,
                    "source": mat_res.material_source,
                    "confidence": mat_res.material_confidence,
                }
                if mat_res.material_code
                else None
            )
            finish_dict = (
                {
                    "value": mat_res.finish_code,
                    "source": mat_res.finish_source,
                }
                if mat_res.finish_code
                else None
            )

            c_cls_def = class_map.get(cls_res.cls)
            uom_val = c_cls_def.uom if c_cls_def else "EA"

            line = BomLine(
                id=line_id,
                parent=parent_line_id,
                level=current_level,
                find_no=fn_val,
                cad_name=cnode.name,
                cad_nodes=[cnode.id],
                noun=noun,
                modifiers=modifiers,
                cls=cls_res.cls,
                designation=desig,
                qty_per_parent=qty_per_parent,
                uom=uom_val,
                qty_total=qty_total,
                description=desc,
                description_exact=is_exact,
                material=mat_dict,
                finish=finish_dict,
                mass_g=mat_res.mass_g,
                make_buy=make_buy,
                origin=origin,
                review="derived",
                phantom=is_phantom,
                buy_as_assembly=is_buy_assembly,
                cad_volume_mm3=round(shape.volume_mm3, 2) if shape else None,
                confidence=cls_res.confidence,
                source=cls_res.source,
                rule_ids=rule_ids,
                open_items=open_items,
                note=note,
            )
            ebom.append(line)
            fn_val += fn_step

            # Recursively process children if assembly and NOT buy_as_assembly
            if cnode.kind == "assembly" and not is_buy_assembly:
                sub_children = children_by_parent.get(cnode.id, [])
                if sub_children:
                    process_level(
                        parent_line_id=line_id,
                        parent_node_id=cnode.id,
                        child_nodes=sub_children,
                        current_level=current_level + 1,
                        parent_qty_total=qty_total,
                    )

    process_level(
        parent_line_id=None,
        parent_node_id=None,
        child_nodes=top_nodes,
        current_level=1,
        parent_qty_total=1.0,
    )

    return ebom
