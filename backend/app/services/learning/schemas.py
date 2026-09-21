"""Learning dataset schemas (Stage 2, Part L)."""
from __future__ import annotations

from typing import Any

LEARNING_EXAMPLE_KEYS = (
    "field", "original_ocr", "ai_candidate", "final_verified_value",
    "image_ref", "evidence_ref", "confidence", "image_quality",
    "provider", "failure_type", "correction_status",
)

FAILURE_TYPES = (
    "DATE_CONFUSION", "MRP_DIGIT_CONFUSION", "QUANTITY_UNIT_CONFUSION",
    "FSSAI_CONTEXT_MISS", "MANUFACTURER_CONTEXT_MISS",
    "INGREDIENT_CONTAMINATION", "NUTRITION_TABLE_MISREAD",
    "PRODUCT_NAME_FALSE_POSITIVE", "BATCH_LOT_FALSE_POSITIVE",
    "CARE_NUMBER_CONFUSION", "ORIENTATION_FAILURE", "LOW_READABILITY",
    "OCR_VISION_CONFLICT", "UNCLASSIFIED",
)


def to_learning_example(correction: dict[str, Any],
                        failure_type: str = "UNCLASSIFIED") -> dict[str, Any]:
    """One verified correction -> one trusted learning example."""
    snap = correction.get("evidence_snapshot") or {}
    return {
        "field": correction.get("field_key"),
        "original_ocr": correction.get("original_value"),
        "ai_candidate": (snap.get("vision_value")
                         if isinstance(snap, dict) else None),
        "final_verified_value": correction.get("corrected_value"),
        "image_ref": (snap.get("image_id") if isinstance(snap, dict)
                      else None),
        "evidence_ref": snap,
        "confidence": correction.get("original_confidence"),
        "image_quality": (snap.get("image_quality")
                          if isinstance(snap, dict) else None),
        "provider": (snap.get("provider") if isinstance(snap, dict)
                     else None),
        "failure_type": failure_type,
        "correction_status": "verified"
        if correction.get("verified") else "feedback-only",
    }
