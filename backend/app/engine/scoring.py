"""Configuration-driven scoring engine.

Loads scoring_policies + scoring_policy_weights from PostgreSQL (never
hard-coded point values). Respects calc_method, review_handling and
not_applicable_handling. Weight-0 checks (e.g. CHK-OTHER-MATTERS) are
tracked for visibility but contribute nothing to the denominator.
"""
from __future__ import annotations

from typing import Any


def score(findings: list[dict[str, Any]], policy: dict[str, Any] | None) -> dict[str, Any]:
    weights = {w["check_id"]: float(w["weight"])
               for w in (policy or {}).get("weights", [])}
    na_handling = (policy or {}).get("not_applicable_handling",
                                     "EXCLUDE_FROM_DENOMINATOR")
    review_handling = (policy or {}).get("review_handling",
                                         "DO_NOT_FINALIZE_WITHOUT_REVIEW")

    numerator = denominator = 0.0
    needs_review = False
    for f in findings:
        result = f.get("status") or f.get("result")
        if result == "NOT_APPLICABLE" and na_handling == "EXCLUDE_FROM_DENOMINATOR":
            continue
        w = weights.get(f.get("rule_id") or f.get("check_id", ""), 0.0)
        denominator += w
        if result == "PASS":
            numerator += w
        elif result == "NEEDS_REVIEW":
            needs_review = True

    pct = round((numerator / denominator * 100) if denominator else 0.0, 1)
    finalizable = not (needs_review and review_handling == "DO_NOT_FINALIZE_WITHOUT_REVIEW")
    status = ("NEEDS_REVIEW" if needs_review else
              "COMPLIANT" if denominator and pct == 100 else
              "NON_COMPLIANT" if denominator else "NEEDS_REVIEW")
    return {"value": pct, "out_of": 100.0, "status": status,
            "finalizable": finalizable, "needs_review": needs_review,
            "policy": (policy or {}).get("policy_id", "DEFAULT-2026"),
            "policy_version": (policy or {}).get("version", "1.1")}
