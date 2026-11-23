"""Classification ladder C1-C6 with noisy-OR evidence scoring (docs/04 §5, §8).

Boundary: deterministic only; no LLM calls. L1 is a placeholder that creates a question
when confidence stays below the acceptance threshold (default 0.75).
"""

import re
from dataclasses import dataclass, field

from c2b_domain.bom import Designation
from c2b_domain.cad import CadNode, ShapeFeatures
from c2b_library.loader import Library
from c2b_rules.interpreter import RuleContext, run_rules_for_stage


@dataclass
class Evidence:
    """A single piece of classification evidence."""

    cls: str
    weight: float
    source: str
    rule_id: str
    note: str


@dataclass
class ClassificationResult:
    """Outcome of classifying a component."""

    cls: str
    confidence: float
    rule_ids: list[str] = field(default_factory=list)
    source: str = "cad"
    open_items: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def _split_name_tokens(name: str) -> list[str]:
    """Split names like 'Mount_Plate', 'Blade_Arm', 'LightKit', 'FASTENERS_GRP' into tokens."""
    # Split camelCase / PascalCase
    s1 = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", name)
    # Split underscores and hyphens
    parts = re.split(r"[_\s\-]+", s1)
    tokens = [p.lower() for p in parts if p.strip()]
    # Also add full name lowercased and rejoined
    full = " ".join(tokens)
    res = list(tokens)
    if full and full not in res:
        res.append(full)
    return res


def classify_node(
    node: CadNode,
    shape: ShapeFeatures | None,
    desig: Designation | None,
    library: Library,
    parent_cls: str | None = None,
    sibling_classes: set[str] | None = None,
) -> ClassificationResult:
    """Run classification ladder C1-C6 on a node."""
    source_bases = library.confidence.source_base
    threshold = float(library.confidence.thresholds.get("class", 0.75))
    tie_margin = float(library.confidence.thresholds.get("class_tie_margin", 0.1))

    evidence_list: list[Evidence] = []

    # C2: Authored CAD metadata (props)
    if "class" in node.props:
        cls_prop = node.props["class"]
        evidence_list.append(
            Evidence(
                cls=cls_prop,
                weight=0.9,
                source="cad_meta",
                rule_id="R-CLS-C02",
                note=f"Authored CAD property class={cls_prop}",
            )
        )

    # C3: Standard-part recogniser match
    if desig is not None:
        # Evaluate classify.yaml rules with std_designation
        ctx = RuleContext(
            name=node.name,
            props=node.props,
            std_designation=desig,
            features=shape.model_dump() if shape else {},
        )
        classify_rf = library.rules.get("rules/classify.yaml")
        classify_rules = list(classify_rf.rules) if classify_rf else []
        matched = run_rules_for_stage(classify_rules, ctx)
        for m in matched:
            target_cls = m.action.get("class")
            if target_cls:
                evidence_list.append(
                    Evidence(
                        cls=str(target_cls),
                        weight=m.weight,
                        source="std_table",
                        rule_id=m.id,
                        note=m.note,
                    )
                )

    # C4: Name dictionary (synonyms and abbreviations)
    name_tokens = _split_name_tokens(node.name)
    node_name_lower = node.name.lower()

    # Search synonyms
    en_synonyms = library.synonyms.get("synonyms/en.yaml")
    if en_synonyms:
        for entry in en_synonyms.entries:
            target_cls = entry.class_
            weight = float(entry.weight)
            # Fan context motor specialization
            is_fan = (
                sibling_classes is not None
                and any("fan" in s.lower() or "blade" in s.lower() for s in sibling_classes)
            ) or (
                parent_cls is not None
                and ("fan" in parent_cls.lower() or "blade" in parent_cls.lower())
            )
            if target_cls == "electrical.motor" and is_fan:
                target_cls = "electrical.motor.ac_induction"
                weight = max(weight, 0.75)

            for tok in entry.tokens:
                tok_str = str(tok).lower()
                # Check exact token match or full phrase match
                if (
                    tok_str == node_name_lower
                    or tok_str in name_tokens
                    or f" {tok_str} " in f" {node_name_lower} "
                ):
                    evidence_list.append(
                        Evidence(
                            cls=target_cls,
                            weight=weight,
                            source="cad_meta",
                            rule_id="R-CLS-SYN",
                            note=f"Name matches dictionary synonym '{tok_str}' -> {target_cls}",
                        )
                    )
                    break

    # C5: Geometry class rules
    if shape is not None:
        plane_ratio = shape.face_hist.get("plane", 0.0)
        cyl_ratio = shape.face_hist.get("cylinder", 0.0)

        # Constant thickness evidence -> sheet metal
        if shape.const_thickness_mm is not None and shape.const_thickness_mm > 0:
            if "bracket" in node_name_lower:
                candidate = "sheetmetal.bracket"
            elif "cover" in node_name_lower or "canopy" in node_name_lower:
                candidate = "sheetmetal.cover"
            elif "plate" in node_name_lower or "mount" in node_name_lower:
                candidate = (
                    "sheetmetal.plate.mounting"
                    if "mount" in node_name_lower
                    else "sheetmetal.plate"
                )
            else:
                candidate = "sheetmetal.plate"
            evidence_list.append(
                Evidence(
                    cls=candidate,
                    weight=0.6,
                    source="cad_geom",
                    rule_id="R-CLS-SHM",
                    note=(
                        f"Constant thickness {shape.const_thickness_mm:.1f} mm "
                        "evidence for sheet metal"
                    ),
                )
            )

        # Rotational symmetry and cylinder dominant -> turned
        is_non_turned = any(
            k in node_name_lower
            for k in ("canopy", "motor", "blade", "light", "shade", "cover", "bracket", "plate")
        )
        if (
            shape.rot_symmetry >= 0.85
            and (cyl_ratio >= 0.35 or len(shape.cylinders) >= 1)
            and plane_ratio < 0.65
            and not is_non_turned
        ):
            if "shaft" in node_name_lower:
                candidate = "machined.turned.shaft"
            elif "hub" in node_name_lower:
                candidate = "machined.turned.hub"
            elif "downrod" in node_name_lower or "tube" in node_name_lower:
                candidate = "extrusion.tube.round"
            else:
                candidate = "machined.turned"
            evidence_list.append(
                Evidence(
                    cls=candidate,
                    weight=0.6,
                    source="cad_geom",
                    rule_id="R-CLS-TRN",
                    note=(
                        f"Rotational symmetry {shape.rot_symmetry:.2f} and cylindrical "
                        "faces evidence for turned / circular section"
                    ),
                )
            )

        # Prismatic block with planes -> machined block
        if plane_ratio >= 0.75 and shape.rot_symmetry < 0.7:
            if "block" in node_name_lower:
                evidence_list.append(
                    Evidence(
                        cls="machined.block",
                        weight=0.6,
                        source="cad_geom",
                        rule_id="R-CLS-MCH",
                        note="Prismatic planar geometry evidence for machined block",
                    )
                )
            elif "cover" in node_name_lower and shape.const_thickness_mm is None:
                evidence_list.append(
                    Evidence(
                        cls="moulded.cover",
                        weight=0.55,
                        source="cad_geom",
                        rule_id="R-CLS-MLD",
                        note="Thin shell cover geometry evidence for moulded cover",
                    )
                )

        # Thin plate rules in classify.yaml (R-CLS-201, etc.)
        ctx = RuleContext(
            name=node.name,
            props=node.props,
            features=shape.model_dump(),
        )
        if "rules/classify.yaml" in library.rules:
            matched = run_rules_for_stage(library.rules["rules/classify.yaml"].rules, ctx)
            for m in matched:
                target_cls = m.action.get("class")
                if target_cls:
                    evidence_list.append(
                        Evidence(
                            cls=str(target_cls),
                            weight=m.weight,
                            source="cad_geom",
                            rule_id=m.id,
                            note=m.note,
                        )
                    )

    # C6: Context boost (parent assembly class, sibling classes)
    if parent_cls and "motor" in parent_cls and any("magnet" in tok for tok in name_tokens):
        evidence_list.append(
            Evidence(
                cls="electrical.motor_part.magnet",
                weight=0.2,
                source="cad_meta",
                rule_id="R-CLS-CTX",
                note=f"Context boost from parent {parent_cls}",
            )
        )

    # Assemblies (if kind == 'assembly')
    if node.kind == "assembly":
        # Check phantom rule
        if re.search(r"(?i)(_grp\b|_group\b|subasm_for_layout)", node.name):
            return ClassificationResult(
                cls="assembly.phantom",
                confidence=0.85,
                rule_ids=["R-STR-001"],
                source="cad_meta",
                notes=["Assembly named like a modelling-only group is a phantom"],
            )
        # Check purchased assembly by name
        if "motor" in node_name_lower:
            return ClassificationResult(
                cls="electrical.motor.ac_induction",
                confidence=0.85,
                rule_ids=["R-STR-003", "R-CLS-SYN"],
                source="cad_meta",
                notes=["Purchased assembly motor"],
            )
        return ClassificationResult(
            cls="assembly.subassembly",
            confidence=0.9,
            rule_ids=["R-STR-ASM"],
            source="cad_meta",
            notes=["Subassembly"],
        )

    # Combine evidence per class using noisy-OR:
    # score = 1 - prod(1 - weight * source_base)
    # cap = max(source_base)
    class_evidence: dict[str, list[Evidence]] = {}
    for ev in evidence_list:
        class_evidence.setdefault(ev.cls, []).append(ev)

    if not class_evidence:
        return ClassificationResult(
            cls="unclassified",
            confidence=0.3,
            rule_ids=[],
            source="cad_geom",
            open_items=[f"Part '{node.name}' could not be classified (no matching rules)"],
            notes=["L1 fallback: unclassified part becomes an open question"],
        )

    scored_classes: list[tuple[str, float, list[str], list[str], str]] = []
    for cls_name, ev_items in class_evidence.items():
        prod = 1.0
        max_base = 0.5
        rule_ids = []
        notes = []
        primary_source = "cad"

        for ev in ev_items:
            base = float(source_bases.get(ev.source, 0.5))
            contrib = ev.weight * base
            prod *= 1.0 - contrib
            max_base = max(max_base, base)
            if ev.rule_id not in rule_ids:
                rule_ids.append(ev.rule_id)
            notes.append(ev.note)
            primary_source = ev.source

        combined_score = min(1.0 - prod, max_base)
        scored_classes.append((cls_name, combined_score, rule_ids, notes, primary_source))

    # Sort descending by score
    scored_classes.sort(key=lambda x: x[1], reverse=True)

    top_cls, top_score, top_rules, top_notes, top_source = scored_classes[0]

    # Check tie or below threshold
    if len(scored_classes) > 1:
        second_score = scored_classes[1][1]
        if abs(top_score - second_score) <= tie_margin and top_score < 0.9:
            # Tied candidates -> open item
            return ClassificationResult(
                cls=top_cls,
                confidence=top_score,
                rule_ids=top_rules,
                source=top_source,
                open_items=[f"Classification tie between '{top_cls}' and '{scored_classes[1][0]}'"],
                notes=top_notes,
            )

    if top_score < threshold:
        return ClassificationResult(
            cls=top_cls,
            confidence=top_score,
            rule_ids=top_rules,
            source=top_source,
            open_items=[
                f"Classification confidence {top_score:.2f} is below threshold {threshold:.2f}"
            ],
            notes=top_notes,
        )

    return ClassificationResult(
        cls=top_cls,
        confidence=round(top_score, 4),
        rule_ids=top_rules,
        source=top_source,
        notes=top_notes,
    )
