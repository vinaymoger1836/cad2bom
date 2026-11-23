"""Rule interpreter for library/rules/*.yaml (docs/04 §2).

Condition primitives (closed set):
  - name_matches
  - meta_equals
  - feature_between
  - feature_ratio
  - std_designation_family
  - class_is
  - parent_class_is
  - product_has_class
  - not_has_class
  - count_between
  - material_in
  - process_hint_in
  - source_is
  - expr (safe evaluator)
  - all, any, not
"""

import re
from dataclasses import dataclass, field
from typing import Any

from c2b_domain.bom import Designation
from c2b_rules.evaluator import safe_eval


@dataclass
class RuleContext:
    """Facts available for evaluating rule conditions on a node / line."""

    name: str = ""
    props: dict[str, str] = field(default_factory=dict)
    features: dict[str, Any] = field(default_factory=dict)
    std_designation: Designation | None = None
    class_code: str | None = None
    parent_class: str | None = None
    product_classes: set[str] = field(default_factory=set)
    children_count: int = 0
    material: str | None = None
    process_hints: list[str] = field(default_factory=list)
    source: str | None = None
    extra_facts: dict[str, Any] = field(default_factory=dict)


@dataclass
class MatchedRule:
    """A rule that successfully matched its conditions."""

    id: str
    stage: str
    action: dict[str, Any]
    weight: float
    note: str


def evaluate_condition(cond: dict[str, Any], ctx: RuleContext) -> bool:
    """Evaluate a single rule condition (or nested condition tree)."""
    for primitive, val in cond.items():
        if primitive == "all":
            if not isinstance(val, list):
                return False
            if not all(evaluate_condition(sub, ctx) for sub in val):
                return False

        elif primitive == "any":
            if not isinstance(val, list):
                return False
            if not any(evaluate_condition(sub, ctx) for sub in val):
                return False

        elif primitive == "not":
            if not isinstance(val, dict):
                return False
            if evaluate_condition(val, ctx):
                return False

        elif primitive == "name_matches":
            pattern = str(val)
            if not re.search(pattern, ctx.name):
                return False

        elif primitive == "meta_equals":
            if not isinstance(val, dict):
                return False
            for k, expected_v in val.items():
                if ctx.props.get(str(k)) != str(expected_v):
                    return False

        elif primitive == "feature_between":
            if not isinstance(val, dict):
                return False
            for feat_name, bounds in val.items():
                if not isinstance(bounds, (list, tuple)) or len(bounds) != 2:
                    return False
                actual_val = ctx.features.get(str(feat_name))
                if actual_val is None or not (bounds[0] <= float(actual_val) <= bounds[1]):
                    return False

        elif primitive == "feature_ratio":
            if not isinstance(val, dict):
                return False
            num_key = str(val.get("num", ""))
            den_key = str(val.get("den", ""))
            min_r = float(val.get("min", 0.0))
            max_r = float(val.get("max", float("inf")))
            num_v = ctx.features.get(num_key)
            den_v = ctx.features.get(den_key)
            if num_v is None or den_v is None or float(den_v) == 0.0:
                return False
            ratio = float(num_v) / float(den_v)
            if not (min_r <= ratio <= max_r):
                return False

        elif primitive == "std_designation_family":
            if ctx.std_designation is None:
                return False
            allowed = val if isinstance(val, list) else [val]
            allowed_set = {str(x).upper().replace(" ", "").replace("_", "") for x in allowed}
            fam = ctx.std_designation.family.upper().replace(" ", "").replace("_", "")
            key = (ctx.std_designation.key or "").upper().replace(" ", "").replace("_", "")
            std = ctx.std_designation.standard.upper().replace(" ", "").replace("_", "")
            if (
                fam not in allowed_set
                and key not in allowed_set
                and std not in allowed_set
                and not any(x in std for x in allowed_set)
            ):
                return False

        elif primitive == "class_is":
            allowed = val if isinstance(val, list) else [val]
            if ctx.class_code is None or ctx.class_code not in allowed:
                return False

        elif primitive == "parent_class_is":
            allowed = val if isinstance(val, list) else [val]
            if ctx.parent_class is None:
                return False
            if ctx.parent_class not in allowed and not any(
                ctx.parent_class.startswith(f"{x}.") for x in allowed
            ):
                return False

        elif primitive == "product_has_class":
            target = str(val)
            if not any(
                cls == target or cls.startswith(f"{target}.") for cls in ctx.product_classes
            ):
                return False

        elif primitive == "not_has_class":
            target = str(val)
            if any(cls == target or cls.startswith(f"{target}.") for cls in ctx.product_classes):
                return False

        elif primitive == "count_between":
            if not isinstance(val, dict):
                return False
            for metric, bounds in val.items():
                if metric == "children":
                    actual = ctx.children_count
                else:
                    actual = ctx.features.get(str(metric), 0)
                if not isinstance(bounds, (list, tuple)) or len(bounds) != 2:
                    return False
                if not (bounds[0] <= float(actual) <= bounds[1]):
                    return False

        elif primitive == "material_in":
            allowed = val if isinstance(val, list) else [val]
            if ctx.material is None or ctx.material not in allowed:
                return False

        elif primitive == "process_hint_in":
            allowed = val if isinstance(val, list) else [val]
            if not any(h in allowed for h in ctx.process_hints):
                return False

        elif primitive == "source_is":
            if ctx.source != str(val):
                return False

        elif primitive == "expr":
            expr_str = str(val)
            facts: dict[str, Any] = {
                "children": ctx.children_count,
                **ctx.features,
                **ctx.extra_facts,
            }
            try:
                res = safe_eval(expr_str, facts)
                if not bool(res):
                    return False
            except Exception:
                return False

        else:
            return False

    return True


def run_rules_for_stage(
    stage_rules: list[Any],
    ctx: RuleContext,
) -> list[MatchedRule]:
    """Evaluate all rules for a given stage against the context."""
    matched: list[MatchedRule] = []
    for rule in stage_rules:
        when = getattr(rule, "when", {})
        if evaluate_condition(when, ctx):
            matched.append(
                MatchedRule(
                    id=rule.id,
                    stage=rule.stage,
                    action=getattr(rule, "then", {}),
                    weight=float(rule.weight),
                    note=rule.note,
                )
            )
    return matched
