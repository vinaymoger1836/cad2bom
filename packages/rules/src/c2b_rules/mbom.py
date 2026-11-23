"""Manufacturing BoM (MBOM) derivation engine (docs/04 §10, docs/05 §1).

Transforms an indented Engineering BoM (EBOM) into an MBOM by:
1. Collapsing phantoms (assembly.phantom promoted to parent).
2. Preserving purchased assemblies as single lines (buy_as_assembly).
3. Preserving build-order sub-assemblies with child hierarchies.
4. Integrating manufacturing consumables and packaging levels.
5. Assigning sequential find numbers and bidirectional ebom_refs.
"""

from __future__ import annotations

import logging

from c2b_domain.bom import BomLine, MbomLine, Suggestion
from c2b_domain.prov import Prov
from c2b_library.loader import Library

log = logging.getLogger(__name__)


def derive_mbom(
    ebom: list[BomLine],
    library: Library | None = None,
    accepted_suggestions: list[Suggestion] | None = None,
) -> list[MbomLine]:
    """Derive Manufacturing BoM lines from an Engineering BoM."""
    if not ebom:
        return []

    line_map = {ln.id: ln for ln in ebom}
    children_map: dict[str | None, list[BomLine]] = {}
    for ln in ebom:
        children_map.setdefault(ln.parent, []).append(ln)

    # Detect phantoms and purchased assemblies
    phantom_ids = {ln.id for ln in ebom if ln.phantom or ln.cls == "assembly.phantom"}
    buy_assembly_ids = {ln.id for ln in ebom if ln.buy_as_assembly}

    mbom_lines: list[MbomLine] = []
    mbom_counter = 1
    ebom_to_mbom_id: dict[str, str] = {}

    def get_effective_parent(ebom_parent_id: str | None) -> str | None:
        """Resolve parent upwards through any collapsed phantoms."""
        curr = ebom_parent_id
        while curr and curr in phantom_ids:
            parent_line = line_map.get(curr)
            curr = parent_line.parent if parent_line else None
        if not curr:
            return None
        return ebom_to_mbom_id.get(curr)

    def process_node(
        node: BomLine,
        mbom_parent_id: str | None,
        find_no: int,
    ) -> None:
        nonlocal mbom_counter

        # 1. If node is a phantom: collapse it in MBOM
        if node.id in phantom_ids:
            # Promote children directly to this phantom's mbom parent
            child_nodes = children_map.get(node.id, [])
            fn = 10
            for child in child_nodes:
                process_node(child, mbom_parent_id, fn)
                fn += 10
            return

        # 2. Regular node or subassembly or purchased assembly
        m_id = f"M-{mbom_counter:04d}"
        mbom_counter += 1
        ebom_to_mbom_id[node.id] = m_id

        # Description string
        desc_val = node.description if isinstance(node.description, str) else node.description.value
        cls_val = node.cls if isinstance(node.cls, str) else node.cls.value
        if isinstance(node.make_buy, Prov):
            make_buy_val = str(node.make_buy.value)
        else:
            make_buy_val = str(node.make_buy or "make")

        raw_val = (
            node.qty_per_parent.value
            if isinstance(node.qty_per_parent, Prov)
            else node.qty_per_parent
        )
        q_pp = float(raw_val)

        mbom_line = MbomLine(
            id=m_id,
            parent=mbom_parent_id,
            ebom_refs=[node.id],
            cls=cls_val,
            description=desc_val,
            qty_per_parent=q_pp,
            qty_total=node.qty_total if hasattr(node, "qty_total") else 1.0,
            uom=node.uom,
            make_buy=make_buy_val,
            find_no=find_no,
            part_number=str(node.part_number) if node.part_number else None,
        )
        mbom_lines.append(mbom_line)

        # 3. Children processing (skip children if purchased as an assembly)
        if node.id in buy_assembly_ids:
            return

        child_nodes = children_map.get(node.id, [])
        child_fn = 10
        for child in child_nodes:
            process_node(child, m_id, child_fn)
            child_fn += 10

    # Process all root lines (parent is None)
    root_lines = children_map.get(None, [])
    top_fn = 10
    for root_line in root_lines:
        process_node(root_line, None, top_fn)
        top_fn += 10

    # 4. Integrate accepted suggestions that target level="mbom"
    if accepted_suggestions:
        for s in accepted_suggestions:
            if s.level == "mbom" and s.review == "accepted":
                m_id = f"M-{mbom_counter:04d}"
                mbom_counter += 1
                mbom_lines.append(
                    MbomLine(
                        id=m_id,
                        parent=None,
                        ebom_refs=[],
                        cls=s.cls or "consumable",
                        description=s.text or (s.cls or "MBOM Item").upper(),
                        qty_per_parent=s.qty_total or 1.0,
                        qty_total=s.qty_total or 1.0,
                        uom="EA",
                        make_buy="buy",
                        find_no=top_fn,
                        notes=f"Added from suggestion {s.id} ({s.rule or ''})",
                    )
                )
                top_fn += 10

    return mbom_lines
