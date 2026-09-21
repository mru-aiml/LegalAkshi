"""Deterministic OCR + Vision reconciliation (Stage 2, Part E).

OCR candidates + Tesseract candidates + Vision AI candidates + layout /
evidence + field validators = final candidate. Per field:

  {field, final_value, status, confidence, sources, candidates,
   agreement, evidence, needs_review_reason}

Rules:
- full agreement -> DETECTED, high confidence (min of agreeing confs,
  never synthesised upward).
- any conflict -> NEEDS_REVIEW, ALL candidates retained, never silent pick.
- vision-only with strong evidence -> DETECTED only if deterministic
  validation passes; otherwise NEEDS_REVIEW.
- invalid values (70.16 as date, phone as MRP) -> rejected, never stored.
"""
from __future__ import annotations

from typing import Any

from app.services.package_intelligence.validators import validate_field

AGREEMENT = ("AGREE", "CONFLICT", "SINGLE_SOURCE", "NO_EVIDENCE")
HIGH_CONFIDENCE = 0.85


def _norm(field: str, value: Any, unit: Any = None) -> Any:
    text = str(value or "").strip().replace(",", "")
    if field == "mrp":
        try:
            return round(float(text), 2)
        except ValueError:
            return text.lower()
    if field in ("quantity",):
        try:
            return (float(text), str(unit or "").lower())
        except ValueError:
            return (text.lower(), str(unit or "").lower())
    return text.lower()


def reconcile_field(
    field: str,
    ocr: dict[str, Any] | None = None,
    tesseract: dict[str, Any] | None = None,
    vision: dict[str, Any] | None = None,
    evidence_boost: bool = False,
) -> dict[str, Any]:
    """Reconcile one field's candidates deterministically."""
    cands: list[dict[str, Any]] = []
    for src, payload in (("rapidocr", ocr), ("tesseract", tesseract),
                         ("vision", vision)):
        if not payload or payload.get("value") in (None, ""):
            continue
        cands.append({
            "source": src,
            "value": str(payload.get("value")),
            "unit": payload.get("unit"),
            "confidence": payload.get("confidence"),
            "evidence_text": payload.get("evidence_text")
            or payload.get("evidence"),
            "bbox": payload.get("bbox") or payload.get("box"),
            "image_id": payload.get("image_id") or payload.get("image"),
            "provider": payload.get("provider"),
            "model": payload.get("model"),
        })
    if not cands:
        return {"field": field, "final_value": None, "unit": None,
                "status": "NOT_DETECTED", "confidence": 0.0,
                "sources": [], "candidates": [],
                "agreement": "NO_EVIDENCE", "evidence": [],
                "needs_review_reason": "no reliable evidence found"}
    # Deterministic validation FIRST: invalid candidates can never win.
    valid: list[dict[str, Any]] = []
    for cand in cands:
        ok, reason = validate_field(
            field, cand["value"], unit=cand.get("unit"),
            evidence=cand.get("evidence_text"))
        cand["validation"] = {"ok": ok, "reason": reason}
        if ok:
            valid.append(cand)
    if not valid:
        return {"field": field, "final_value": None, "unit": None,
                "status": "NEEDS_REVIEW", "confidence": 0.0,
                "sources": [c["source"] for c in cands],
                "candidates": cands, "agreement": "CONFLICT",
                "evidence": [c.get("evidence_text") for c in cands
                             if c.get("evidence_text")],
                "needs_review_reason": "candidates failed deterministic "
                f"validation: {cands[0]['validation']['reason']}"}
    norms = {_norm(field, c["value"], c.get("unit")) for c in valid}
    if len(norms) == 1:
        best = max(valid, key=lambda c: (c.get("confidence") or 0,
                                         c["source"] == "rapidocr"))
        confs = [c.get("confidence") or 0 for c in valid]
        conf = round(min(confs) if len(valid) > 1 else (confs[0] or 0), 3)
        if len(valid) == 1 and valid[0]["source"] == "vision" \
                and not evidence_boost:
            # Vision-only without strong evidence: review, not detection.
            return {"field": field, "final_value": best["value"],
                    "unit": best.get("unit"), "status": "NEEDS_REVIEW",
                    "confidence": conf,
                    "sources": [c["source"] for c in valid],
                    "candidates": valid, "agreement": "SINGLE_SOURCE",
                    "evidence": [best.get("evidence_text")],
                    "needs_review_reason": "vision-only candidate without "
                    "strong corroborating evidence"}
        status = "DETECTED" if conf >= 0.6 else "NEEDS_REVIEW"
        return {"field": field, "final_value": best["value"],
                "unit": best.get("unit"), "status": status,
                "confidence": conf if status == "DETECTED"
                else round(conf, 3),
                "sources": [c["source"] for c in valid],
                "candidates": valid,
                "agreement": "AGREE" if len(valid) > 1 else "SINGLE_SOURCE",
                "evidence": [c.get("evidence_text") for c in valid
                             if c.get("evidence_text")],
                "needs_review_reason": "" if status == "DETECTED"
                else "low agreement confidence"}
    # Conflict: retain everything, pick nothing.
    best_conf = max((c.get("confidence") or 0) for c in valid)
    return {"field": field, "final_value": None, "unit": None,
            "status": "NEEDS_REVIEW", "confidence": round(best_conf, 3),
            "sources": [c["source"] for c in valid],
            "candidates": valid, "agreement": "CONFLICT",
            "evidence": [c.get("evidence_text") for c in valid
                         if c.get("evidence_text")],
            "needs_review_reason": "conflicting candidates: " +
            ", ".join(f"{c['source']}={c['value']}" for c in valid)}


def reconcile_all(
    ocr_fields: dict[str, dict[str, Any]] | None = None,
    tesseract_fields: dict[str, dict[str, Any]] | None = None,
    vision_candidates: list[dict[str, Any]] | None = None,
    strong_evidence_fields: set[str] | None = None,
) -> dict[str, dict[str, Any]]:
    """Reconcile every field seen across OCR / Tesseract / Vision."""
    ocr_fields = ocr_fields or {}
    tesseract_fields = tesseract_fields or {}
    by_field: dict[str, dict[str, dict[str, Any]]] = {}
    for src, bucket in (("ocr", ocr_fields), ("tesseract", tesseract_fields)):
        for field, payload in (bucket or {}).items():
            by_field.setdefault(field, {})[src] = payload or {}
    for cand in vision_candidates or []:
        if not isinstance(cand, dict) or not cand.get("field"):
            continue
        by_field.setdefault(str(cand["field"]), {})["vision"] = cand
    strong = strong_evidence_fields or set()
    out: dict[str, dict[str, Any]] = {}
    for field, parts in by_field.items():
        out[field] = reconcile_field(
            field, ocr=parts.get("ocr"), tesseract=parts.get("tesseract"),
            vision=parts.get("vision"), evidence_boost=(field in strong))
    return out
