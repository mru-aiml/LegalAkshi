"""Deterministic error-pattern classification (Stage 2, Part M).

Every verified correction attempts failure classification. Pure
heuristics over (field, original, corrected, evidence snapshot) — no
model, no accuracy claims. "AI accuracy" is never presented; only
counts, agreement, and trends over verified corrections.
"""
from __future__ import annotations

import re
from typing import Any


def classify_failure(correction: dict[str, Any]) -> str:
    field = str(correction.get("field_key", "") or "")
    orig = str(correction.get("original_value", "") or "")
    fixed = str(correction.get("corrected_value", "") or "")
    snap = correction.get("evidence_snapshot") or {}
    snap_text = str(snap)[:2000]
    if field in ("manufacturing_date", "best_before", "use_by"):
        if re.fullmatch(r"[\d.,/]{3,12}", orig) and "/" not in orig \
                and "-" not in orig:
            return "DATE_CONFUSION"
        return "DATE_CONFUSION"
    if field == "mrp":
        if re.sub(r"\D", "", orig) and re.sub(r"\D", "", orig) != \
                re.sub(r"\D", "", fixed):
            return "MRP_DIGIT_CONFUSION"
        return "MRP_DIGIT_CONFUSION"
    if field in ("quantity", "unit"):
        return "QUANTITY_UNIT_CONFUSION"
    if field == "fssai_license":
        return "FSSAI_CONTEXT_MISS"
    if field == "manufacturer":
        return "MANUFACTURER_CONTEXT_MISS"
    if field == "ingredients":
        return "INGREDIENT_CONTAMINATION"
    if field.startswith(("energy", "protein", "carbohydrate", "total_",
                         "saturated", "trans_", "sodium", "serving",
                         "nutrition")):
        return "NUTRITION_TABLE_MISREAD"
    if field == "product_name":
        return "PRODUCT_NAME_FALSE_POSITIVE"
    if field in ("batch_lot", "batch"):
        return "BATCH_LOT_FALSE_POSITIVE"
    if field == "consumer_care":
        return "CARE_NUMBER_CONFUSION"
    if "orientation" in snap_text.lower() or "rotat" in snap_text.lower():
        return "ORIENTATION_FAILURE"
    if "readability" in snap_text.lower() or "poor" in snap_text.lower():
        return "LOW_READABILITY"
    if isinstance(snap, dict) and snap.get("vision_value") not in (None, "") \
            and str(snap.get("vision_value")) != orig:
        return "OCR_VISION_CONFLICT"
    return "UNCLASSIFIED"


def aggregate_error_patterns(
        corrections: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Group verified corrections by (field, failure_type) with counts."""
    buckets: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for corr in corrections or []:
        if not corr.get("verified"):
            continue
        key = (str(corr.get("field_key", "")),
               classify_failure(corr))
        buckets.setdefault(key, []).append(corr)
    out: list[dict[str, Any]] = []
    for (field, failure), rows in sorted(buckets.items()):
        out.append({
            "field": field,
            "failure_type": failure,
            "occurrences": len(rows),
            "verified_corrections": len(rows),
            "recent": rows[-3:],
        })
    return out
