"""Applicability engine — evaluated BEFORE any compliance check.

Reads rule_applicability.condition_expression (authoritative column) and
maps to lkp_applicable_result values:
REQUIRED / NOT_REQUIRED / NOT_APPLICABLE / CONDITIONAL / NEEDS_REVIEW.

NOT_REQUIRED (legally need not be satisfied) and NOT_APPLICABLE both mean
"do not evaluate checks"; findings are recorded as NOT_APPLICABLE (the only
non-evaluated value compliance_results.result accepts) with the original
applicability outcome preserved in the explanation.
"""
from __future__ import annotations

from typing import Any

from app.engine.conditions import evaluate


def decide(rule_version_id: str, rows: list[dict],
           values: dict[str, Any],
           mandatory_default: bool = True) -> dict[str, Any]:
    """mandatory_default comes from engine_check_registry: the base legal
    requirement stands even when a CONDITIONAL proviso row (e.g. the
    electronic-product QR manner) does not trigger. The QR-aware check
    handler itself implements the proviso, so closing the proviso path must
    not exempt the base requirement."""
    if not rows:
        if mandatory_default:
            return {"applicable": True, "status": "REQUIRED",
                    "reason": "no applicability exception configured; base "
                              "requirement applies."}
        return {"applicable": None, "status": "NEEDS_REVIEW",
                "reason": f"no applicability config for {rule_version_id}"}
    needs_review: str | None = None
    for row in rows:
        outcome = row.get("applicable_result") or row.get("outcome", "NEEDS_REVIEW")
        expr = row.get("condition_expression") or {}
        reason = row.get("reason") or row.get("note", "")
        if outcome == "NOT_APPLICABLE":
            verdict = evaluate(expr, values)
            if verdict is True:
                return {"applicable": False, "status": "NOT_APPLICABLE",
                        "reason": reason}
            if verdict is None:
                needs_review = reason
            continue
        if outcome == "NOT_REQUIRED":
            verdict = evaluate(expr, values)
            if verdict is True:
                return {"applicable": False, "status": "NOT_REQUIRED",
                        "reason": reason}
            if verdict is None:
                needs_review = reason
            continue
        if outcome in ("REQUIRED", "NOT_REQUIRED_UNCONDITIONAL"):
            verdict = evaluate(expr, values)
            if verdict is False:
                continue
            if verdict is None and expr:
                needs_review = reason
                continue
            return {"applicable": True, "status": outcome, "reason": reason}
        if outcome == "CONDITIONAL":
            verdict = evaluate(expr, values)
            if verdict is True:
                return {"applicable": True, "status": "CONDITIONAL",
                        "reason": reason}
            if verdict is None:
                needs_review = reason
                continue
            if mandatory_default:  # proviso closed, base requirement stands
                return {"applicable": True, "status": "REQUIRED",
                        "reason": (reason + " [proviso not triggered; base "
                                   "requirement applies]") if reason
                        else "base requirement applies."}
            return {"applicable": False, "status": "NOT_APPLICABLE",
                    "reason": reason}
        needs_review = reason or f"unknown outcome {outcome}"
    if needs_review is not None:
        return {"applicable": None, "status": "NEEDS_REVIEW", "reason": needs_review}
    return {"applicable": False, "status": "NOT_APPLICABLE",
            "reason": "no applicability row matched"}
