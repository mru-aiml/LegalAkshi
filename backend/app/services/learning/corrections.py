"""Correction store helpers (Stage 2, Parts I/J).

Thin helpers over the repository correction primitives: payload
validation, append-only guarantees, and verified-value overlays for
reporting (reports use FINAL VERIFIED values; unresolved required
fields report NEEDS REVIEW, never an invented value).
"""
from __future__ import annotations

import copy
from typing import Any


def build_correction(
    inspection_id: str,
    product_id: str,
    field_key: str,
    original_value: Any,
    corrected_value: Any,
    officer_user_id: str = "",
    original_status: str = "NEEDS_REVIEW",
    original_confidence: float | None = None,
    source: str = "officer-review",
    evidence_snapshot: dict[str, Any] | None = None,
    correction_reason: str = "",
) -> dict[str, Any]:
    if not str(field_key or "").strip():
        raise ValueError("field_key is required")
    return {
        "inspection_id": inspection_id,
        "product_id": product_id,
        "field_key": str(field_key).strip(),
        "original_value": None if original_value is None
        else str(original_value),
        "corrected_value": None if corrected_value is None
        else str(corrected_value),
        "original_status": original_status or "NEEDS_REVIEW",
        "original_confidence": original_confidence,
        "source": source or "officer-review",
        "evidence_snapshot": copy.deepcopy(evidence_snapshot or {}),
        "officer_user_id": officer_user_id or "",
        "correction_reason": correction_reason or "",
    }


def apply_verified_corrections(
    product: dict[str, Any],
    corrections: list[dict[str, Any]],
) -> dict[str, Any]:
    """Overlay verified corrections onto a product copy for reporting.

    Only verified=true rows apply; unverified feedback never changes the
    report. Returns a NEW dict (original untouched).
    """
    out = copy.deepcopy(product or {})
    for corr in corrections or []:
        if not corr.get("verified"):
            continue
        field = str(corr.get("field_key", "") or "")
        if not field:
            continue
        out[field] = corr.get("corrected_value")
    return out


def unresolved_report_value(field: str,
                            reconciled_status: str | None) -> str:
    """Reporting rule (Part T): unresolved required fields report
    NEEDS REVIEW rather than an invented value."""
    if reconciled_status in ("DETECTED",):
        return "DETECTED"
    return "NEEDS REVIEW"
