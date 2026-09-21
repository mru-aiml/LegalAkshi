"""Image role classification + field-to-image priority (Stage 3 §2).

Uploaded photos are classified approximately as FRONT / BACK / SIDE /
TOP / BOTTOM / UNKNOWN from (a) the upload slot (front/back are
explicit) and (b) deterministic OCR/layout evidence for the rest.
Classification never needs to be perfect — it only orders which
images each field extractor (and each vision group) tries first, so
extractors stop scanning every image for every field.

FIELD_IMAGE_PRIORITY maps each OCR field key onto role preference
order; UNKNOWN-role images always participate (evidence may live
anywhere) but rank last, except where listed.
"""
from __future__ import annotations

import re
from typing import Any

FRONT = "FRONT"
BACK = "BACK"
SIDE = "SIDE"
TOP = "TOP"
BOTTOM = "BOTTOM"
UNKNOWN = "UNKNOWN"
# Stage 3A.5: assigned by the OCR service (never by this classifier)
# when an upload is a near-duplicate of an earlier image in the same
# inspection. The duplicate is retained as evidence and its OCR is
# reused; the classifier itself never invents DUPLICATE from thin
# evidence.
DUPLICATE = "DUPLICATE"

IMAGE_ROLES = (FRONT, BACK, SIDE, TOP, BOTTOM, UNKNOWN, DUPLICATE)

# Stage 3 §2 deterministic priority map (OCR field key -> role order).
FIELD_IMAGE_PRIORITY: dict[str, tuple[str, ...]] = {
    "product_name": (FRONT, BACK, SIDE, UNKNOWN),
    "common_generic_name": (FRONT, BACK, SIDE, UNKNOWN),
    "quantity": (FRONT, BACK, SIDE, UNKNOWN),
    "unit": (FRONT, BACK, SIDE, UNKNOWN),
    "veg_nonveg": (FRONT, BACK, UNKNOWN),
    "ingredients": (BACK, SIDE, UNKNOWN),
    "allergens": (BACK, SIDE, UNKNOWN),
    "nutrition": (BACK, SIDE, UNKNOWN),
    "fssai_license": (BACK, SIDE, UNKNOWN),
    "manufacturer": (BACK, SIDE, UNKNOWN),
    "manufacturer_address": (BACK, SIDE, UNKNOWN),
    "mrp": (BACK, SIDE, FRONT, UNKNOWN),
    "manufacturing_date": (SIDE, BACK, BOTTOM, UNKNOWN),
    "best_before": (SIDE, BACK, BOTTOM, UNKNOWN),
    "use_by": (SIDE, BACK, BOTTOM, UNKNOWN),
    "batch_lot": (SIDE, BACK, BOTTOM, UNKNOWN),
    "batch": (SIDE, BACK, BOTTOM, UNKNOWN),
    "consumer_care": (BACK, SIDE, UNKNOWN),
    "country_of_origin": (BACK, SIDE, FRONT, UNKNOWN),
}

# Vision group -> preferred roles (Stage 2C selection refined by role).
GROUP_ROLE_PRIORITY: dict[str, tuple[str, ...]] = {
    "A_product": (FRONT, BACK, SIDE, UNKNOWN),
    "B_declarations": (BACK, SIDE, FRONT, UNKNOWN),
    "C_business": (BACK, SIDE, UNKNOWN),
    "D_ingredients": (BACK, SIDE, UNKNOWN),
    "E_nutrition": (BACK, SIDE, UNKNOWN),
    "F_symbols": (FRONT, BACK, UNKNOWN),
    "F_other": (BACK, SIDE, FRONT, UNKNOWN),
}

# --- evidence markers (module-level, compiled once) ---
_DISPLAY_MARKERS = re.compile(
    r"ingredients?|composition", re.I)  # weak alone; see _score_front
_BRAND_CASE = re.compile(r"[A-Z]{4,}")
_DECLARATION_MARKERS = re.compile(
    r"ingredients?|nutrition|fssai|lic\.?\s*no|licen[sc]e|"
    r"manufactured\s+(?:by|for)|marketed\s+by|packed\s+by|"
    r"consumer\s*care|customer\s*care|helpline|toll|"
    r"net\s*(?:wt|qty|quantity|weight)|best\s*before|use\s*by|"
    r"\bmfd\b|\bmfg\b|\bpkd\b|batch|lot\s*no|m\.?\s*r\.?\s*p|"
    r"maximum\s+retail\s+price", re.I)
_SMALL_PRINT_DENSITY = 6  # >= this many short lines => dense back panel


def _score_front(texts: list[str], boxes: list) -> float:
    """Display-panel signals: few lines, title-case/brand caps, marketing."""
    score = 0.0
    n = len(texts)
    if 0 < n <= 12:
        score += 1.0
    joined = " ".join(texts)
    if _BRAND_CASE.search(joined):
        score += 1.0
    if re.search(r"tasty|delicious|new|offer|crunchy|yummy", joined, re.I):
        score += 1.0
    if _DECLARATION_MARKERS.search(joined):
        score -= 1.5
    return score


def _score_back(texts: list[str]) -> float:
    """Declaration-panel signals: many lines, section markers."""
    joined = " ".join(texts)
    hits = len(_DECLARATION_MARKERS.findall(joined))
    score = min(hits * 0.5, 3.0)
    if len(texts) >= _SMALL_PRINT_DENSITY:
        score += 1.0
    return score


# Stage 3A.4 SIDE indicators: MRP / batch / date panels carry a few
# short declaration lines and none of the back-panel prose sections
# (ingredients, nutrition, maker, care). Deterministic and cheap.
_SIDE_MARKERS = re.compile(
    r"\bmfd\b|\bmfg\b|\bpkd\b|\bpkg\b|batch|lot\s*no|\bb\.?\s*no\b|"
    r"m\.?\s*r\.?\s*p\b|maximum\s+retail\s+price|best\s*before|"
    r"use\s*by|expir", re.I)
_BACK_ONLY_MARKERS = re.compile(
    r"ingredients?|nutrition|manufactured\s+(?:by|for)|marketed\s+by|"
    r"consumer\s*care|customer\s*care|storage|fssai", re.I)


def _score_side(texts: list[str]) -> float:
    """Side-panel signals: few short lines, date/batch/MRP markers only."""
    joined = " ".join(texts)
    if not joined.strip():
        return 0.0
    if _BACK_ONLY_MARKERS.search(joined):
        return 0.0
    if len(texts) > 10:
        return 0.0
    if _SIDE_MARKERS.search(joined):
        return 2.0
    return 0.0


def classify_label(label: str, texts: list[str],
                   boxes: list | None = None) -> str:
    """Role for one image label (never raises, never guesses hard).

    Explicit slots win (front/back). Anything else is scored on OCR
    evidence; ties and thin evidence stay UNKNOWN (participates last).
    """
    low = (label or "").strip().lower()
    if low == "front":
        return FRONT
    if low == "back":
        return BACK
    if low in ("top",):
        return TOP
    if low in ("bottom",):
        return BOTTOM
    if low.startswith("side"):
        return SIDE
    if low == "listing":
        return UNKNOWN
    front_s = _score_front(texts or [], boxes or [])
    back_s = _score_back(texts or [])
    side_s = _score_side(texts or [])
    if side_s >= 2.0 and side_s >= back_s and side_s >= front_s:
        return SIDE
    if back_s >= front_s + 1.0:
        return BACK
    if front_s >= back_s + 1.0:
        return FRONT
    if back_s > 0 and len(texts or []) >= _SMALL_PRINT_DENSITY:
        return SIDE
    return UNKNOWN


def classify_roles(
    labels: list[str],
    lines_by_label: dict[str, list[Any]] | None = None,
) -> dict[str, str]:
    """{label: role} for a whole inspection (pure function)."""
    lines_by_label = lines_by_label or {}
    out: dict[str, str] = {}
    for label in labels:
        lines = lines_by_label.get(label) or []
        texts = [str(getattr(ln, "text", "") or "") for ln in lines]
        boxes = [getattr(ln, "box", None) for ln in lines]
        try:
            out[label] = classify_label(label, texts, boxes)
        except Exception:
            out[label] = UNKNOWN
    return out


def ordered_labels_for_field(
    field: str,
    labels: list[str],
    roles: dict[str, str] | None = None,
) -> list[str]:
    """Labels ordered by field priority (Stage 3 §2 routing).

    Unknown-role images always participate (last unless the map says
    otherwise); unmapped fields keep upload order.
    """
    roles = roles or {}
    priority = FIELD_IMAGE_PRIORITY.get(field)
    if not priority:
        return list(labels)
    rank = {role: i for i, role in enumerate(priority)}
    return sorted(labels,
                  key=lambda lab: (rank.get(roles.get(lab, UNKNOWN),
                                            len(priority)), labels.index(lab)))


def ordered_labels_for_group(
    group: str,
    labels: list[str],
    roles: dict[str, str] | None = None,
) -> list[str]:
    """Vision-group analogue of ordered_labels_for_field."""
    roles = roles or {}
    priority = GROUP_ROLE_PRIORITY.get(group, (BACK, SIDE, FRONT, UNKNOWN))
    rank = {role: i for i, role in enumerate(priority)}
    return sorted(labels,
                  key=lambda lab: (rank.get(roles.get(lab, UNKNOWN),
                                            len(priority)), labels.index(lab)))
