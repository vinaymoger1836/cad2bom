"""Ladder pipeline runner integrating L1-L6 into the BoM workflow (docs/02 §3, docs/04 §9)."""

from __future__ import annotations

import logging

from c2b_agents.agent import generate_job_summary
from c2b_agents.ladder import (
    run_l1_classify,
    run_l3_header_mapping,
    run_l4_gap_candidates,
)
from c2b_agents.router import LlmRouter
from c2b_domain.bom import BomDraft, ColumnMapping, Question
from c2b_domain.cad import CadExtractResult
from c2b_domain.prov import Prov
from c2b_library.loader import Library
from c2b_rules.dependency import apply_mapping_confirmation
from c2b_rules.descriptions import build_description
from c2b_rules.explainer import explain_line

log = logging.getLogger(__name__)


def _matches_archetype(library: Library, text: str) -> bool:
    """True when the product text names a known archetype (docs/04 §9: L4 is for unknown types)."""
    low = text.lower()
    return any(
        kw.lower() in low
        for arch in (library.archetypes or {}).values()
        for kw in arch.match_keywords
    )


async def resolve_draft_with_ladder(
    draft: BomDraft,
    library: Library,
    router: LlmRouter,
    extract: CadExtractResult | None = None,
    product_desc: str = "",
    confirm_l3_immediately: bool = False,
    run_l4_gap: bool = False,
) -> BomDraft:
    """Execute LLM fallback ladder steps L1-L6 on a draft BoM where needed."""
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
                    (ln.cls if isinstance(ln.cls, str) else ln.cls.value)
                    for ln in draft.ebom
                    if ln.id != line.id
                    and (ln.cls if isinstance(ln.cls, str) else ln.cls.value) != "unclassified"
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
                    chosen_cls, l1_conf, l1_reason, _fallback_reason = l1_res
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
            sample_rows = [list(r.values()) for r in draft.user_rows_raw[:5]]
            header_row = list(draft.mapping.columns.keys()) + ambiguous_cols

            l3_res = await run_l3_header_mapping(
                router=router,
                header_row=header_row,
                sample_rows=sample_rows,
                ambiguous_column=col,
                job_id=draft.job_id,
            )

            mq = next((q for q in draft.questions if q.mapping_column == col), None)
            if isinstance(l3_res, Question):
                # L3 unavailable or failed: the engine's mapping question already asks the user.
                if mq is None:
                    draft.questions.append(l3_res)
            elif isinstance(l3_res, dict) and "field" in l3_res:
                resolved_field = l3_res["field"]
                if confirm_l3_immediately:
                    # Test/replay switch that stands in for the user's confirmation click.
                    apply_mapping_confirmation(draft, col, resolved_field)
                    if mq is not None:
                        mq.answer = resolved_field
                    draft.mapping.columns[col].by = "extractor_L3"
                    draft.mapping.columns[col].score = 0.85
                    draft.mapping.columns[col].why = "Resolved via L3 LLM header mapping"
                else:
                    # LLM proposal only: stays unconfirmed and the question stays open.
                    draft.mapping.columns[col] = ColumnMapping(
                        field=resolved_field,
                        by="extractor_L3",
                        score=0.85,
                        confirmed_by_user=False,
                        why="Proposed by L3 LLM header mapping; needs your confirmation",
                    )
                    if mq is not None:
                        mq.evidence.append(f"L3 suggests: {resolved_field}")
                        mq.note = "R-MAP-AMB; L3 proposal"

    # 3. Ladder Step L4: Gap candidates for unknown product types
    if run_l4_gap and product_desc and not _matches_archetype(library, product_desc):
        current_classes = [
            (ln.cls if isinstance(ln.cls, str) else ln.cls.value)
            for ln in draft.ebom
            if (ln.cls if isinstance(ln.cls, str) else ln.cls.value) != "unclassified"
        ]
        gap_suggestions = await run_l4_gap_candidates(
            router=router,
            library=library,
            product_desc=product_desc,
            current_classes=current_classes,
            job_id=draft.job_id,
        )
        # Rules already suggested some classes (labels, manual, carton...): do not repeat them.
        known = {str(x.cls) for x in draft.suggestions} | set(current_classes)
        draft.suggestions.extend(x for x in gap_suggestions if str(x.cls) not in known)

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
