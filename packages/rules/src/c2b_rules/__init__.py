"""Deterministic BoM rules engine (docs/04).

CLAUDE.md rule 1: pure deterministic code; no LLM imports, no network access.
"""

from c2b_rules.bom_intake import detect_and_map_bom, expand_refdes
from c2b_rules.classifier import ClassificationResult, classify_node
from c2b_rules.datasheets import extract_datasheet_specs
from c2b_rules.descriptions import build_description
from c2b_rules.engine import build_ebom
from c2b_rules.evaluator import safe_eval
from c2b_rules.explainer import explain_line, format_explanation_text
from c2b_rules.interpreter import MatchedRule, RuleContext, evaluate_condition, run_rules_for_stage
from c2b_rules.material_mass import MaterialMassResult, resolve_material_and_mass
from c2b_rules.merger import merge_cad_and_user_bom
from c2b_rules.recognisers import recognize_standard_part
from c2b_rules.structure import build_ebom_lines
from c2b_rules.vision import VisionRouterStub, process_photos

__all__ = [
    "ClassificationResult",
    "MatchedRule",
    "MaterialMassResult",
    "RuleContext",
    "VisionRouterStub",
    "build_description",
    "build_ebom",
    "build_ebom_lines",
    "classify_node",
    "detect_and_map_bom",
    "evaluate_condition",
    "expand_refdes",
    "explain_line",
    "extract_datasheet_specs",
    "format_explanation_text",
    "merge_cad_and_user_bom",
    "process_photos",
    "recognize_standard_part",
    "resolve_material_and_mass",
    "run_rules_for_stage",
    "safe_eval",
]
