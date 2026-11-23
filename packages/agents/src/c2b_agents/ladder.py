"""Fallback ladder steps L1-L6 exactly as specified in docs/04 §9.

Rules:
- Structured output against JSON Schemas whose enums are generated from the library.
- Confidence caps (class cap <= 0.70; gap suggestions <= 0.40).
- L6 acceptance rule: quote must contain the value (assert value in quote).
- When the LLM is unavailable or over budget, the step becomes a question.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from c2b_agents.router import BudgetExceededError, LlmRouter
from c2b_agents.schemas import (
    L1Output,
    L2Output,
    L3Output,
    L4Output,
    L5Output,
    L6Output,
    get_header_fields,
    get_material_families,
    get_taxonomy_codes,
    validate_enum_field,
)
from c2b_domain.bom import (
    DatasheetSpec,
    Question,
    Suggestion,
)
from c2b_domain.cad import CadNode, ShapeFeatures
from c2b_domain.prov import Source
from c2b_library.models import Library

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# L1: Classify unknown part (role: reasoner)
# ---------------------------------------------------------------------------


async def run_l1_classify(
    router: LlmRouter,
    library: Library,
    node: CadNode,
    parent_names: list[str],
    sibling_classes: list[str],
    features: ShapeFeatures | None,
    product_desc: str,
    candidate_classes: list[str],
    job_id: str = "default",
    line_id: str | None = None,
) -> tuple[str, float, str, str] | Question:
    """Classify a part when rules score below threshold or tie (docs/04 §9).

    Returns:
        (chosen_class, confidence, reason, fallback_reason) OR Question if unavailable/failed.
    """
    valid_classes = get_taxonomy_codes(library)
    fallback_reason = "Classification below threshold after C1-C6"

    # Format compact prompt facts per docs/04 §9
    feat_summary = ""
    if features:
        feat_summary = (
            f"size=({features.obb_mm[0]:.1f}, {features.obb_mm[1]:.1f}, {features.obb_mm[2]:.1f}) mm, "
            f"vol={features.volume_mm3:.1f} mm3, rot_sym={features.rot_symmetry:.2f}, "
            f"const_thick={features.const_thickness_mm}, thin_plate={features.thin_plate}, "
            f"helix={features.helix_evidence}"
        )

    prompt = (
        f"You are a CAD classification reasoner. Classify this mechanical part into exactly one "
        f"taxonomy class code from the allowed taxonomy.\n\n"
        f"Part Name: {node.name}\n"
        f"Parent Assemblies: {', '.join(parent_names) if parent_names else 'Root'}\n"
        f"Sibling Classes: {', '.join(sibling_classes) if sibling_classes else 'None'}\n"
        f"Geometric Features: {feat_summary}\n"
        f"Product Context: {product_desc}\n"
        f"Candidate Classes from Rules: {', '.join(candidate_classes) if candidate_classes else 'None'}\n\n"
        f"Allowed Taxonomy Codes: {', '.join(valid_classes)}\n\n"
        f"Return JSON strictly conforming to schema with fields 'cls', 'confidence' (0.0-1.0), and 'reason'."
    )

    try:
        res = await router.complete(
            role="reasoner",
            prompt=prompt,
            schema=L1Output,
            job_id=job_id,
            line_id=line_id,
            fallback_reason=fallback_reason,
            system_prompt=(
                "You are an expert mechanical engineering classifier. Choose strictly from the "
                "allowed taxonomy codes."
            ),
        )

        out: L1Output | None = res.parsed
        if not out or not validate_enum_field(out.cls, valid_classes, "class"):
            log.warning("L1 returned invalid class '%s'", getattr(out, "cls", None))
            return Question(
                id=f"Q-CLS-{node.id}",
                line_ids=[line_id] if line_id else [],
                kind="choice",
                text=f"Part '{node.name}' could not be automatically classified. Please select a class:",
                options=candidate_classes[:5] if candidate_classes else valid_classes[:5],
                evidence=[f"cad_features: {feat_summary}"],
                blocking=True,
            )

        # Confidence cap <= 0.70 per docs/04 §9 and confidence.yaml llm.class_cap
        confidence = min(float(out.confidence), 0.70)
        return (out.cls, confidence, out.reason, fallback_reason)

    except (BudgetExceededError, Exception) as exc:
        log.warning("L1 classification failed or budget exceeded (%s); falling back to Question", exc)
        return Question(
            id=f"Q-CLS-{node.id}",
            line_ids=[line_id] if line_id else [],
            kind="choice",
            text=f"Part '{node.name}' could not be classified. Please choose its component class:",
            options=candidate_classes[:5] if candidate_classes else ["moulded.housing", "sheetmetal.bracket"],
            evidence=[f"cad_node: {node.name}"],
            blocking=True,
        )


# ---------------------------------------------------------------------------
# L2: Material family (role: reasoner)
# ---------------------------------------------------------------------------


async def run_l2_material_family(
    router: LlmRouter,
    library: Library,
    part_name: str,
    cls: str,
    colour: str | None,
    photo_findings: list[str],
    description: str,
    job_id: str = "default",
    line_id: str | None = None,
) -> Suggestion | Question:
    """Propose a material family when material is unknown (docs/04 §9)."""
    valid_families = get_material_families(library)
    fallback_reason = "Material needed, unknown, class default ambiguous"

    prompt = (
        f"You are a materials selection expert. Determine the most probable material family.\n"
        f"Part Name: {part_name}\n"
        f"Class: {cls}\n"
        f"Colour: {colour or 'unspecified'}\n"
        f"Photo Observations: {', '.join(photo_findings) if photo_findings else 'none'}\n"
        f"Description: {description}\n\n"
        f"Allowed Material Families: {', '.join(valid_families)}\n"
        f"Return JSON conforming to schema with 'material_family' and 'reason'."
    )

    try:
        res = await router.complete(
            role="reasoner",
            prompt=prompt,
            schema=L2Output,
            job_id=job_id,
            line_id=line_id,
            fallback_reason=fallback_reason,
        )
        out: L2Output | None = res.parsed
        if not out or not validate_enum_field(out.material_family, valid_families, "material_family"):
            raise ValueError(f"Invalid material family {getattr(out, 'material_family', None)}")

        return Suggestion(
            id=f"S-MAT-{line_id or '001'}",
            line_id=line_id or "",
            action="modify",
            field="material",
            value=out.material_family,
            confidence=0.60,
            rule_ids=["R-MAT-LLM"],
            reason=f"LLM material family: {out.reason}",
            status="suggested",
        )
    except Exception as exc:
        log.warning("L2 material family failed (%s); returning Question", exc)
        return Question(
            id=f"Q-MAT-{line_id or '001'}",
            line_ids=[line_id] if line_id else [],
            kind="choice",
            text=f"Material for '{part_name}' is unspecified. Which material family should be used?",
            options=valid_families[:6],
            evidence=[f"class: {cls}"],
            blocking=False,
        )


# ---------------------------------------------------------------------------
# L3: BoM header mapping (role: extractor)
# ---------------------------------------------------------------------------


async def run_l3_header_mapping(
    router: LlmRouter,
    header_row: list[str],
    sample_rows: list[list[str]],
    ambiguous_column: str,
    job_id: str = "default",
) -> dict[str, Any] | Question:
    """Resolve an ambiguous or unmapped spreadsheet column (docs/04 §9)."""
    valid_fields = get_header_fields()
    fallback_reason = f"Column '{ambiguous_column}' unmapped or ambiguous after rules"

    samples_fmt = "\n".join(
        f"Row {i+1}: " + " | ".join(str(cell) for cell in row)
        for i, row in enumerate(sample_rows[:5])
    )

    prompt = (
        f"You are a tabular BoM mapping specialist. Map the spreadsheet columns to canonical BoM fields.\n\n"
        f"Header Row: {' | '.join(header_row)}\n"
        f"Sample Data Rows:\n{samples_fmt}\n\n"
        f"Ambiguous Column to Resolve: '{ambiguous_column}'\n"
        f"Allowed Canonical Fields: {', '.join(valid_fields)}\n\n"
        f"Return JSON conforming to schema with 'mappings' (dict of col -> field) and 'reason'."
    )

    try:
        res = await router.complete(
            role="extractor",
            prompt=prompt,
            schema=L3Output,
            job_id=job_id,
            fallback_reason=fallback_reason,
            system_prompt="Map table columns to canonical fields based on header names and row content.",
        )
        out: L3Output | None = res.parsed
        if not out:
            raise ValueError("No structured output returned for L3")

        field = out.mappings.get(ambiguous_column)
        if field and validate_enum_field(field, valid_fields, "field"):
            return {
                "field": field,
                "by": "extractor_L3",
                "why": out.reason,
                "mappings": out.mappings,
            }
        raise ValueError(f"Ambiguous column '{ambiguous_column}' not mapped to valid field")

    except Exception as exc:
        log.warning("L3 header mapping failed (%s); turning into Question", exc)
        return Question(
            id=f"Q-MAP-{ambiguous_column}",
            line_ids=[],
            kind="choice",
            text=f"Column '{ambiguous_column}' is ambiguous. What kind of data does it contain?",
            options=["description", "find_no", "part_number", "notes"],
            evidence=[f"header: {ambiguous_column}"],
            blocking=True,
        )


# ---------------------------------------------------------------------------
# L4: Gap candidates for unknown product type (role: reasoner)
# ---------------------------------------------------------------------------


async def run_l4_gap_candidates(
    router: LlmRouter,
    library: Library,
    product_desc: str,
    current_classes: list[str],
    job_id: str = "default",
) -> list[Suggestion]:
    """Propose gap suggestions for an unknown product type (docs/04 §9)."""
    valid_classes = get_taxonomy_codes(library)
    fallback_reason = "No archetype match and universal/class rules done"

    prompt = (
        f"You are a design-for-manufacturing expert. Identify missing components commonly required "
        f"for this product that are not yet in the BoM.\n\n"
        f"Product Description: {product_desc}\n"
        f"Current BoM Component Classes: {', '.join(current_classes)}\n\n"
        f"Allowed Taxonomy Classes: {', '.join(valid_classes)}\n\n"
        f"Return JSON strictly conforming to schema with at most 8 'candidates' (each having 'cls' and 'reason')."
    )

    try:
        res = await router.complete(
            role="reasoner",
            prompt=prompt,
            schema=L4Output,
            job_id=job_id,
            fallback_reason=fallback_reason,
        )
        out: L4Output | None = res.parsed
        if not out:
            return []

        suggestions: list[Suggestion] = []
        for i, cand in enumerate(out.candidates[:8]):
            if validate_enum_field(cand.cls, valid_classes, "class"):
                suggestions.append(
                    Suggestion(
                        id=f"S-GAP-{i+1:03d}",
                        line_id="",
                        action="add",
                        cls=cand.cls,
                        qty=1.0,
                        uom="EA",
                        description=cand.cls.replace(".", " ").upper(),
                        confidence=0.40,  # Low confidence per docs/04 §9
                        rule_ids=["R-GAP-LLM"],
                        reason=f"LLM product gap suggestion: {cand.reason}",
                        status="suggested",
                    )
                )
        return suggestions
    except Exception as exc:
        log.warning("L4 gap candidates failed (%s); returning empty suggestions", exc)
        return []


# ---------------------------------------------------------------------------
# L5: Description wording (role: agent)
# ---------------------------------------------------------------------------


async def run_l5_description_wording(
    router: LlmRouter,
    cls: str,
    designation: str | None,
    key_dims: str | None,
    material: str | None,
    job_id: str = "default",
    line_id: str | None = None,
) -> tuple[str, list[str]]:
    """Generate uppercase noun and modifier wording when missing from dictionary (docs/04 §9)."""
    fallback_reason = "Noun or modifier missing from dictionary for classified line"

    prompt = (
        f"Propose standard uppercase noun and modifiers for engineering BoM line.\n"
        f"Class: {cls}\n"
        f"Designation: {designation or 'None'}\n"
        f"Key Dimensions: {key_dims or 'None'}\n"
        f"Material: {material or 'None'}\n\n"
        f"Return JSON conforming to schema with 'noun' (string) and 'modifiers' (list of strings)."
    )

    try:
        res = await router.complete(
            role="agent",
            prompt=prompt,
            schema=L5Output,
            job_id=job_id,
            line_id=line_id,
            fallback_reason=fallback_reason,
        )
        out: L5Output | None = res.parsed
        if out and out.noun:
            return (out.noun.upper(), [m.upper() for m in out.modifiers])
    except Exception as exc:
        log.warning("L5 description wording failed (%s); using default noun", exc)

    return (cls.split(".")[-1].upper(), [])


# ---------------------------------------------------------------------------
# L6: Datasheet field (role: extractor)
# ---------------------------------------------------------------------------


async def run_l6_datasheet_field(
    router: LlmRouter,
    page_text: str,
    needed_field: str,
    page_no: int,
    pdf_filename: str = "datasheet.pdf",
    job_id: str = "default",
) -> DatasheetSpec | Question:
    """Extract a technical specification from datasheet text snippet (docs/04 §9).

    ACCEPTANCE RULE: Accepted only if the quoted text contains the extracted value.
    """
    fallback_reason = f"Regex extractors found nothing for needed field '{needed_field}'"

    prompt = (
        f"You are a technical document extractor. Extract the value for field '{needed_field}'.\n"
        f"Page Text Snippet:\n\"\"\"{page_text}\"\"\"\n\n"
        f"Return JSON conforming to schema with 'field', 'value', 'unit', 'page', and 'quote'.\n"
        f"CRITICAL RULE: 'quote' MUST be an exact sentence from the text that contains 'value'."
    )

    try:
        res = await router.complete(
            role="extractor",
            prompt=prompt,
            schema=L6Output,
            job_id=job_id,
            fallback_reason=fallback_reason,
        )
        out: L6Output | None = res.parsed
        if not out:
            raise ValueError("No output returned for L6")

        # Acceptance check: quoted text MUST contain the value
        if out.value not in out.quote:
            log.warning(
                "L6 REJECTED: extracted value '%s' is not in quote '%s'", out.value, out.quote
            )
            return Question(
                id=f"Q-SPEC-{needed_field}",
                line_ids=[],
                kind="text",
                text=f"Please specify the '{needed_field}' from page {page_no} of {pdf_filename}:",
                evidence=[f"unverified_quote: {out.quote}"],
                blocking=False,
            )

        return DatasheetSpec(
            spec_type=out.field,
            value=out.value,
            unit=out.unit,
            raw_text=out.quote,
            page=out.page,
            ref=f"pdf:{pdf_filename}#p{out.page}",
            confidence=0.60,
        )
    except Exception as exc:
        log.warning("L6 datasheet extraction failed (%s); converting to Question", exc)
        return Question(
            id=f"Q-SPEC-{needed_field}",
            line_ids=[],
            kind="text",
            text=f"Please specify the '{needed_field}' value from the datasheet:",
            evidence=[f"page: {page_no}"],
            blocking=False,
        )
