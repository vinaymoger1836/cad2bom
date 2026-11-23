"""Safe AST expression evaluator for rule condition `expr` (docs/04 §2).

CLAUDE.md rule 12: Never eval/exec library or LLM text.
Only pure numeric/boolean AST operators and whitelisted safe functions are permitted.
"""

import ast
import operator
from collections.abc import Callable
from typing import Any

_OPERATORS: dict[type[ast.AST], Callable[..., Any]] = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
    ast.Eq: operator.eq,
    ast.NotEq: operator.ne,
    ast.Lt: operator.lt,
    ast.LtE: operator.le,
    ast.Gt: operator.gt,
    ast.GtE: operator.ge,
    ast.USub: operator.neg,
    ast.UAdd: operator.pos,
    ast.Not: operator.not_,
}

_FUNCTIONS: dict[str, Callable[..., Any]] = {
    "abs": abs,
    "min": min,
    "max": max,
    "round": round,
}


class ExpressionError(ValueError):
    """Raised when an expression contains invalid or unsafe AST nodes."""


def _eval_node(node: ast.AST, facts: dict[str, Any]) -> Any:
    if isinstance(node, ast.Expression):
        return _eval_node(node.body, facts)

    if isinstance(node, ast.Constant):
        if isinstance(node.value, (int, float, bool, str)) or node.value is None:
            return node.value
        raise ExpressionError(f"Unsupported constant type: {type(node.value)}")

    if isinstance(node, ast.Name):
        if node.id in facts:
            return facts[node.id]
        if node.id in _FUNCTIONS:
            return _FUNCTIONS[node.id]
        raise ExpressionError(f"Unknown variable: '{node.id}'")

    if isinstance(node, ast.UnaryOp):
        op_fn = _OPERATORS.get(type(node.op))
        if op_fn is None:
            raise ExpressionError(f"Unsupported unary operator: {type(node.op)}")
        operand = _eval_node(node.operand, facts)
        return op_fn(operand)

    if isinstance(node, ast.BinOp):
        op_fn = _OPERATORS.get(type(node.op))
        if op_fn is None:
            raise ExpressionError(f"Unsupported binary operator: {type(node.op)}")
        left = _eval_node(node.left, facts)
        right = _eval_node(node.right, facts)
        return op_fn(left, right)

    if isinstance(node, ast.BoolOp):
        if isinstance(node.op, ast.And):
            return all(_eval_node(val, facts) for val in node.values)
        if isinstance(node.op, ast.Or):
            return any(_eval_node(val, facts) for val in node.values)
        raise ExpressionError(f"Unsupported boolean operator: {type(node.op)}")

    if isinstance(node, ast.Compare):
        left = _eval_node(node.left, facts)
        for op, comparator in zip(node.ops, node.comparators, strict=True):
            op_fn = _OPERATORS.get(type(op))
            if op_fn is None:
                raise ExpressionError(f"Unsupported comparison operator: {type(op)}")
            right = _eval_node(comparator, facts)
            if not op_fn(left, right):
                return False
            left = right
        return True

    if isinstance(node, ast.Call):
        func = _eval_node(node.func, facts)
        if func not in _FUNCTIONS.values():
            raise ExpressionError(f"Function call not permitted: {node.func}")
        args = [_eval_node(a, facts) for a in node.args]
        return func(*args)

    raise ExpressionError(f"Forbidden AST node: {type(node).__name__}")


def safe_eval(expr_str: str, facts: dict[str, Any]) -> Any:
    """Safely evaluate an expression string using an AST visitor with whitelisted operations."""
    try:
        tree = ast.parse(expr_str.strip(), mode="eval")
    except SyntaxError as exc:
        raise ExpressionError(f"Syntax error in expression '{expr_str}': {exc}") from exc
    return _eval_node(tree, facts)
