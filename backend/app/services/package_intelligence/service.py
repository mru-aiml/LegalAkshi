"""Package Intelligence orchestrator (Stage 2, Parts A/D/G/O/P).

Boundary: package images + OCR candidates + bboxes + quality diagnostics
+ analysis requirements -> reviewed declarations / candidates. The Rule
Engine then consumes the resulting facts exactly as today; AI never
decides compliance.

Budgets (Part P): one full-page OCR per image (existing OCR service
owns that), Tesseract cap unchanged, vision grouped + bounded
(MAX_VISION_CALLS_PER_INSPECTION) + cached per inspection.
"""
from __future__ import annotations

import time
from typing import Any

from app.services.package_intelligence.reconciliation import reconcile_all
from app.services.vision.schemas import MAX_VISION_CALLS_PER_INSPECTION

AUTO = "AUTO-DETECTED"
REVIEW = "NEEDS REVIEW"
MISSING = "NOT DETECTED"
NA = "OPTIONAL / NOT APPLICABLE"


def unresolved_fields(ocr_statuses: dict[str, dict[str, Any]],
                      required: list[str],
                      high_value: list[str] | None = None) -> list[str]:
    """Fields worth a vision call: required-but-unresolved + high-value."""
    out: list[str] = []
    for field in required:
        hit = (ocr_statuses or {}).get(field) or {}
        if hit.get("status") != "DETECTED" or not hit.get("value"):
            out.append(field)
    for field in high_value or []:
        if field not in out:
            hit = (ocr_statuses or {}).get(field) or {}
            if hit.get("status") != "DETECTED":
                out.append(field)
    return out


def readiness(reconciled: dict[str, dict[str, Any]],
              requirements: dict[str, Any] | None = None) -> dict[str, Any]:
    """Required-field readiness from analysis_requirements (Part G).

    Optional / not-applicable fields never gate analysis.
    """
    requirements = requirements or {}
    required = [r.get("ocr_key") or r.get("field")
                for r in requirements.get("required", [])]
    required = [f for f in required if f]
    auto = review = missing = 0
    blocked: list[str] = []
    for field in required:
        hit = (reconciled or {}).get(field)
        status = (hit or {}).get("status", "NOT_DETECTED")
        if status == "DETECTED":
            auto += 1
        elif status == "NEEDS_REVIEW":
            review += 1
            blocked.append(field)
        else:
            missing += 1
            blocked.append(field)
    return {"fields_required": len(required),
            "fields_detected": auto,
            "fields_needs_review": review,
            "fields_not_detected": missing,
            "blocked_fields": blocked,
            "ready": not blocked,
            "display": {f: _display((reconciled.get(f) or {}).get("status"))
                        for f in required}}


def _display(status: str | None) -> str:
    if status == "DETECTED":
        return AUTO
    if status == "NEEDS_REVIEW":
        return REVIEW
    if status == "NOT_DETECTED":
        return MISSING
    return NA


def run_package_intelligence(
    ocr_result: dict[str, Any] | None = None,
    vision_summary: dict[str, Any] | None = None,
    requirements: dict[str, Any] | None = None,
    timings: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Pure orchestration: OCR result + vision summary -> reconciled pack.

    Never calls providers itself (routes/services do that within budget);
    this keeps the function deterministic and unit-testable. Returns
    {fields, readiness, diagnostics}.
    """
    t0 = time.perf_counter()
    ocr_result = ocr_result or {}
    vision_summary = vision_summary or {}
    raw_fields = ocr_result.get("fields") or {}
    ocr_bucket: dict[str, dict[str, Any]] = {}
    for field, hit in raw_fields.items():
        if not isinstance(hit, dict):
            continue
        ocr_bucket[field] = {
            "value": hit.get("value"), "unit": hit.get("unit"),
            "confidence": hit.get("confidence"),
            "evidence_text": hit.get("evidence_text"),
            "evidence": hit.get("evidence_text"),
            "bbox": hit.get("bbox", hit.get("box")),
            "box": hit.get("box", hit.get("bbox")),
            "image_id": hit.get("image_id", hit.get("image")),
            "image": hit.get("image", hit.get("image_id")),
        }
    strong = {f for f, h in raw_fields.items()
              if isinstance(h, dict) and h.get("status") == "DETECTED"
              and (h.get("confidence") or 0) >= 0.85}
    reconciled = reconcile_all(
        ocr_fields=ocr_bucket,
        tesseract_fields=None,
        vision_candidates=vision_summary.get("candidates") or [],
        strong_evidence_fields=strong)
    # Carry OCR-only fields with no vision counterpart still through
    # validation-aware single-source reconciliation (done above via
    # reconcile_all since every OCR field is in ocr_bucket).
    ready = readiness(reconciled, requirements)
    rec_ms = round((time.perf_counter() - t0) * 1000, 1)
    timings = timings or {}
    diag = {
        "images": ocr_result.get("images_analyzed", 0),
        "ocr_calls": (timings.get("provider_calls")
                      or ocr_result.get("timings", {}).get("provider_calls")
                      or 0),
        "vision_calls": vision_summary.get("calls", 0),
        "vision_calls_budget": MAX_VISION_CALLS_PER_INSPECTION,
        "fields_required": ready["fields_required"],
        "fields_detected": ready["fields_detected"],
        "fields_needs_review": ready["fields_needs_review"],
        "fields_not_detected": ready["fields_not_detected"],
        "ocr_latency_ms": (timings.get("total_ms")
                           or ocr_result.get("timings", {}).get("total_ms")),
        "vision_latency_ms": vision_summary.get("latency_ms"),
        "reconciliation_latency_ms": rec_ms,
        "total_latency_ms": round(
            float(timings.get("total_ms") or 0)
            + float(vision_summary.get("latency_ms") or 0) + rec_ms, 1),
        "vision_provider": vision_summary.get("provider"),
        "vision_model": vision_summary.get("model"),
        "vision_error": vision_summary.get("error"),
    }
    return {"fields": reconciled, "readiness": ready, "diagnostics": diag}
