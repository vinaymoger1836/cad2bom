"""Ladder pipeline runner integrating L1-L6 into the BoM workflow (docs/02 §3, docs/04 §9)."""

from __future__ import annotations

import logging
from typing import Any

from c2b_agents.agent import generate_job_summary
from c2b_agents.ladder import (
    run_l1_classify,
    run_l3_header_mapping,
    run_l4_gap_candidates,
)
from c2b_agents.router import LlmRouter
from c2b_domain.bom import BomDraft, BomLine, ColumnMapping, Question
from c2b_domain.cad import CadExtractResult
from c2b_domain.prov import Prov
from c2b_library.loader import Library
from c2b_rules.descriptions import build_description
from c2b_rules.explainer import explain_line

log = logging.getLogger(__name__)


async def resolve_draft_with_ladder(
    draft: BomDraft,
    library: Library,
    router: LlmRouter,
    extract: CadExtractResult | None = None,
    product_desc: str = "",
    confirm_l3_immediately: bool = True,
) -> BomDraft:
    """Execute LLM fallback ladder steps L1-L6 on a draft BoM where needed."""
    print("START resolve_draft_with_ladder", flush=True)
    node_map = {n.id: n for n in (extract.nodes if extract else [])}
    shape_map = extract.shapes if extract else {}

    # 1. Ladder Step L1: Classify parts below threshold or unclassified
    class_threshold = 0.75
    if library and library.confidence and library.confidence.thresholds:
        class_threshold = float(library.confidence.thresholds.get("class", 0.75))

    for line in draft.ebom:
        cls_val = line.cls if isinstance(line.cls, str) else line.cls.value
        conf_val = line.confidence

        # Only run L1 if line is from CAD and score is below threshold or unclassified
        if (cls_val == "unclassified" or conf_val < class_threshold) and line.origin == "cad":
            # Find matching CadNode
            matched_node = None
            if line.cad_nodes:
                matched_node = node_map.get(line.cad_nodes[0])
            if not matched_node and line.cad_name:
                matched_node = next(
                    (n for n in (extract.nodes if extract else []) if n.name == line.cad_name), None
                )

            if matched_node:
                features = shape_map.get(matched_node.shape_id) if matched_node.shape_id else None
                sibling_classes = [
                    (l.cls if isinstance(l.cls, str) else l.cls.value)
                    for l in draft.ebom
                    if l.id != line.id and (l.cls if isinstance(l.cls, str) else l.cls.value) != "unclassified"
                ]

                l1_res = await run_l1_classify(
                    router=router,
                    library=library,
                    node=matched_node,
                    parent_names=[],
                    sibling_classes=sibling_classes,
                    features=features,
                    product_desc=product_desc,
                    candidate_classes=[],
                    job_id=draft.job_id,
                    line_id=line.id,
                )

                if isinstance(l1_res, Question):
                    draft.questions.append(l1_res)
                else:
                    chosen_cls, l1_conf, l1_reason, fallback_reason = l1_res
                    # Update line with LLM class provenance (capped at 0.70)
                    line.cls = Prov(
                        value=chosen_cls,
                        source="llm",
                        confidence=l1_conf,
                        rules=["R-CLS-LLM"],
                        note=l1_reason,
                    )
                    line.origin = "llm"
                    line.confidence = l1_conf

                    # Class definition from library
                    cls_def = None
                    if library.taxonomy:
                        cls_def = next(
                            (c for c in library.taxonomy.classes if c.code == chosen_cls), None
                        )

                    noun = cls_def.noun if cls_def else chosen_cls.split(".")[-1].upper()
                    modifiers = cls_def.modifiers if cls_def else []

                    # Regenerate noun-first description
                    desc_str, noun, modifiers, is_exact = build_description(
                        cls_code=chosen_cls,
                        desig=line.designation,
                        shape=features,
                        grade_label=None,
                        mat_display=None,
                        library=library,
                    )
                    line.noun = noun
                    line.modifiers = modifiers
                    line.description = desc_str
                    line.description_exact = is_exact

                    # LLM-sourced lines appear in open items per contract
                    line.open_items.append(f"LLM-sourced class: {l1_reason}")
                    line.explanation = explain_line(line)

    # 2. Ladder Step L3: Resolve ambiguous or unmapped spreadsheet headers
    if draft.mapping and draft.mapping.ambiguous_columns:
        ambiguous_cols = list(draft.mapping.ambiguous_columns)
        for col in ambiguous_cols:
            sample_rows = [
                list(r.values())
                for r in draft.user_rows_raw[:5]
            ]
            header_row = list(draft.mapping.columns.keys()) + ambiguous_cols

            l3_res = await run_l3_header_mapping(
                router=router,
                header_row=header_row,
                sample_rows=sample_rows,
                ambiguous_column=col,
                job_id=draft.job_id,
            )

            if isinstance(l3_res, Question):
                draft.questions.append(l3_res)
            elif isinstance(l3_res, dict) and "field" in l3_res:
                resolved_field = l3_res["field"]
                draft.mapping.columns[col] = ColumnMapping(
                    source_header=col,
                    field=resolved_field,
                    confidence=0.85,
                    rule_id="R-MAP-L3",
                    sample_values=[str(r.get(col, "")) for r in draft.user_rows_raw[:3]],
                )
                draft.mapping.ambiguous_columns.remove(col)

                # If confirmed, apply resolved field to user rows
                if confirm_l3_immediately and resolved_field == "description":
                    for line in draft.ebom:
                        if line.raw and col in line.raw:
                            desc_val = str(line.raw[col]).strip()
                            if desc_val:
                                line.description = desc_val

    # 3. Ladder Step L4: Gap candidates for unknown product types
    if product_desc and not draft.suggestions:
        current_classes = [
            (l.cls if isinstance(l.cls, str) else l.cls.value)
            for l in draft.ebom
            if (l.cls if isinstance(l.cls, str) else l.cls.value) != "unclassified"
        ]
        gap_suggestions = await run_l4_gap_candidates(
            router=router,
            library=library,
            product_desc=product_desc,
            current_classes=current_classes,
            job_id=draft.job_id,
        )
        draft.suggestions.extend(gap_suggestions)

    # 4. Job summary and LLM call logs
    draft.summary = generate_job_summary(draft)
    draft.llm_calls = list(router.call_logs)

    # Re-calculate open items count
    open_items = []
    for line in draft.ebom:
        for oi in line.open_items:
            open_items.append(f"{line.id}: {oi}")
    draft.open_items = open_items

    return draft
