"""Generic condition-expression evaluator for rule applicability.

Supports the operators required by the brief: eq, neq, in, not_in, gt, gte,
lt, lte, all, any. Robust to the shapes found in the DB:

  {}                                              -> True (unconditional)
  {"field": "imported", "eq": true}               -> leaf comparison
  {"all": [...]} / {"any": [...]}                 -> boolean combinators
  {"field": ..., "op": "eq", "value": ...}        -> alternate leaf shape

Unknown operators evaluate to None (uncertain) so callers flag NEEDS_REVIEW
instead of guessing.
"""
from __future__ import annotations

from typing import Any


def _coerce(value: Any) -> Any:
    if isinstance(value, str):
        low = value.strip().lower()
        if low in ("true", "false"):
            return low == "true"
    return value


def _compare(actual: Any, op: str, expected: Any) -> bool | None:
    actual, expected = _coerce(actual), _coerce(expected)
    try:
        if op == "eq":
            return actual == expected
        if op == "neq":
            return actual != expected
        if op == "in":
            return actual in (expected or [])
        if op == "not_in":
            return actual not in (expected or [])
        if op in ("gt", "gte", "lt", "lte"):
            if actual is None:
                return False
            a, e = float(actual), float(expected)  # type: ignore[arg-type]
            return {"gt": a > e, "gte": a >= e, "lt": a < e, "lte": a <= e}[op]
    except (TypeError, ValueError):
        return None
    return None


def evaluate(expr: Any, facts: dict[str, Any]) -> bool | None:
    """Evaluate a condition expression against product facts.

    Returns True / False, or None when the expression cannot be decided
    (unknown operator, missing field with comparison, malformed node).
    """
    if not expr:
        return True
    if not isinstance(expr, dict):
        return None
    if "all" in expr:
        children = expr.get("all") or []
        results = [evaluate(c, facts) for c in children]
        if any(r is False for r in results):
            return False
        if any(r is None for r in results):
            return None
        return True
    if "any" in expr:
        children = expr.get("any") or []
        results = [evaluate(c, facts) for c in children]
        if any(r is True for r in results):
            return True
        if any(r is None for r in results):
            return None
        return False
    field = expr.get("field")
    if field is None:
        return None
    actual = facts.get(field)
    # Alternate shape: {"field": x, "op": "eq", "value": v}
    if "op" in expr:
        return _compare(actual, str(expr["op"]), expr.get("value"))
    for op in ("eq", "neq", "in", "not_in", "gt", "gte", "lt", "lte"):
        if op in expr:
            if actual is None and op not in ("eq", "neq"):
                return None
            return _compare(actual, op, expr[op])
    # Bare presence test: {"field": "x", "present": true}
    if "present" in expr:
        want = bool(expr["present"])
        has = actual is not None and actual != ""
        return has is want
    return None
