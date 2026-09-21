"""Stage 2 Package Intelligence — vision provider schemas.

Structured candidates only, never free-form prose. A provider MUST return
null / NEEDS_REVIEW when evidence is insufficient — "probably 108" must
never become a detected legal declaration. Strict validation lives here
so every provider (mock, gemini, future) speaks the same contract.
"""
from __future__ import annotations

from typing import Any

VISION_STATUSES = ("DETECTED", "NEEDS_REVIEW", "NOT_DETECTED")

# Grouped extraction tasks (bounded: never one AI call per field).
VISION_FIELD_GROUPS: dict[str, list[str]] = {
    "product": ["product_name", "brand_name", "common_generic_name"],
    "declarations": ["quantity", "unit", "mrp", "manufacturing_date",
                     "best_before", "batch_lot"],
    "business": ["manufacturer", "manufacturer_address", "fssai_license",
                 "consumer_care"],
    "food": ["ingredients", "allergens", "veg_nonveg"],
    "nutrition": ["energy", "protein", "carbohydrate", "total_sugars",
                  "added_sugars", "total_fat", "saturated_fat", "trans_fat",
                  "sodium", "serving_size", "nutrition_basis"],
}

# Hard budget: vision calls per inspection (grouped, cached, bounded).
MAX_VISION_CALLS_PER_INSPECTION = 6


def validate_vision_candidate(raw: dict[str, Any]) -> dict[str, Any] | None:
    """Strict schema validation for one vision candidate.

    Returns a normalised candidate dict, or None when the payload is
    structurally invalid (rejected, never coerced into a detection).
    Hedged values ("probably 108", "looks like ...", "likely ...") are
    demoted to NEEDS_REVIEW with value preserved but unusable.
    """
    if not isinstance(raw, dict):
        return None
    field = str(raw.get("field", "") or "").strip()
    if not field:
        return None
    status = str(raw.get("status", "") or "").strip().upper()
    if status not in VISION_STATUSES:
        return None
    value = raw.get("value")
    if value is not None:
        value = str(value).strip()
        if value == "":
            value = None
    if status == "DETECTED" and value in (None, ""):
        # A detection with no value is incoherent — demote, never accept.
        status = "NEEDS_REVIEW"
    if value and _is_hedged(value):
        status = "NEEDS_REVIEW"
    try:
        confidence = float(raw.get("confidence", 0.0) or 0.0)
    except (TypeError, ValueError):
        return None
    if not 0.0 <= confidence <= 1.0:
        return None
    bbox = raw.get("bbox")
    if bbox is not None and not isinstance(bbox, dict):
        return None
    return {
        "field": field,
        "value": value,
        "unit": raw.get("unit"),
        "status": status,
        "confidence": round(confidence, 3),
        "evidence_text": raw.get("evidence_text"),
        "bbox": bbox,
        "image_id": raw.get("image_id"),
        "source": "vision",
    }


def _is_hedged(value: str) -> bool:
    low = value.strip().lower()
    return low.startswith(("probably ", "looks like", "likely ",
                           "maybe ", "possibly ", "i think", "appears"))


def groups_for_fields(requested: list[str]) -> dict[str, list[str]]:
    """Map requested fields onto the bounded extraction groups."""
    wanted = {f for f in requested if f}
    out: dict[str, list[str]] = {}
    for group, members in VISION_FIELD_GROUPS.items():
        hit = [m for m in members if m in wanted]
        if hit:
            out[group] = hit
    # Fields outside every known group form their own bounded group.
    known = {m for members in VISION_FIELD_GROUPS.values() for m in members}
    rest = sorted(f for f in wanted if f not in known)
    if rest:
        out["other"] = rest
    return out
