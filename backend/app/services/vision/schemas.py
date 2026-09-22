"""Stage 2 Package Intelligence — vision provider schemas.

Structured candidates only, never free-form prose. A provider MUST return
null / NEEDS_REVIEW when evidence is insufficient — "probably 108" must
never become a detected legal declaration. Strict validation lives here
so every provider (mock, gemini, future) speaks the same contract.
"""
from __future__ import annotations

from typing import Any

VISION_STATUSES = ("DETECTED", "NEEDS_REVIEW", "NOT_DETECTED")

# Second-pass detail vocabulary (Gemini reports these; they are mapped
# onto the stable VISION_STATUSES below — never stored raw as verdicts).
# FOUND: value clearly readable on the pack.
# NOT_VISIBLE: the declaration is not on the visible panels.
# UNREADABLE: present but not legible (blur/glare/handwriting).
# AMBIGUOUS: visible but uncertain (conflict, partial occlusion).
VISION_DETAIL_STATUSES = ("FOUND", "NOT_VISIBLE", "UNREADABLE", "AMBIGUOUS")

# Readability vocabulary some prompts request (mapped onto the detail
# vocabulary above — never stored raw as verdicts).
READABILITY_TO_DETAIL = {
    "CLEAR": "FOUND",
    "AMBIGUOUS": "AMBIGUOUS",
    "UNREADABLE": "UNREADABLE",
    "NOT_VISIBLE": "NOT_VISIBLE",
}

# Vegetarian-symbol value vocabulary for the veg_nonveg field.
VEG_SYMBOL_VALUES = ("VEGETARIAN", "NON_VEGETARIAN", "NOT_DETECTED",
                     "AMBIGUOUS")

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
    # Second-pass detail vocabulary maps onto the stable statuses:
    # NOT_VISIBLE -> NOT_DETECTED; UNREADABLE/AMBIGUOUS -> NEEDS_REVIEW
    # (value kept for audit, never usable as a detection). FOUND keeps
    # the reported status. Unknown detail strings are ignored safely.
    # The readability_status spelling some prompts use maps first.
    detail = str(raw.get("detail_status", "") or "").strip().upper()
    if not detail:
        readability = str(raw.get("readability_status", "") or "")
        readability = readability.strip().upper()
        detail = READABILITY_TO_DETAIL.get(readability, "")
    if detail and detail in VISION_DETAIL_STATUSES:
        if detail == "NOT_VISIBLE":
            status, value = "NOT_DETECTED", None
        elif detail in ("UNREADABLE", "AMBIGUOUS") and status == "DETECTED":
            status = "NEEDS_REVIEW"
    # Vegetarian-symbol value vocabulary: only the four known marks are
    # kept; anything else becomes AMBIGUOUS (review, never a verdict).
    # NOT_DETECTED/AMBIGUOUS symbols carry no usable value.
    if field == "veg_nonveg" and value is not None:
        if str(value).strip().upper() not in VEG_SYMBOL_VALUES:
            value, status = "AMBIGUOUS", "NEEDS_REVIEW"
        elif str(value).strip().upper() in ("NOT_DETECTED", "AMBIGUOUS"):
            value, status = str(value).strip().upper(), "NEEDS_REVIEW" \
                if status == "DETECTED" else status
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
    modality = raw.get("modality")
    if modality not in ("AI", "AI_HANDWRITTEN", "AI_PRINTED"):
        modality = None
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
        # Second-pass provenance (optional; passed through untouched):
        # where on the pack the value was seen, and whether the text
        # is hand-written/stamped (MRP, batch, dates often are).
        "detail_status": detail or None,
        "evidence_location": raw.get("evidence_location"),
        "source_location": raw.get("source_location"),
        "handwritten": bool(raw.get("handwritten", False)),
        "modality": modality,
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
