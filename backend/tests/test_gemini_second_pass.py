"""Gemini second-pass extraction + OCR reconciliation (Tasks 2-11).

Uses the deterministic mock provider (no network, no keys). Covers:
successful extraction, timeout fallback, malformed JSON fallback,
agreement/conflict/AI-extracted/both-missing verdicts, symbol
CV+Gemini matrix, never-invent behavior, timing metadata, the 8-image
limit, and the rule-engine purity guard (no vision inside app/engine).
"""
from __future__ import annotations

import io
import time

import pytest

from app.services.package_intelligence.reconciliation import (
    reconciliation_verdict,
)
from app.services.package_intelligence.vision_stage import (
    build_symbol_verification,
    overlay_reconciliation,
)
from app.services.vision.mock_provider import MockVisionProvider


def _png_bytes(label: str = "img") -> bytes:
    from PIL import Image, ImageDraw

    img = Image.new("RGB", (640, 480), "white")
    d = ImageDraw.Draw(img)
    d.text((20, 20), f"Package {label}", fill="black")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _ocr_result(fields_detailed=None, veg=None):
    return {
        "status": "OK", "engine": "mock-ocr",
        "fields_detailed": dict(fields_detailed or {}),
        "timings": {"total_ms": 1200.0, "provider_calls": 2,
                    "stage_timings": {"preprocessing_ms": 80.0,
                                      "decode_ms": 20.0}},
        "veg_nonveg_symbol": dict(veg or {
            "status": "NOT_DETECTED", "classification": "UNKNOWN",
            "confidence": 0.3, "provenance": "IMAGE"}),
        "images_analyzed": 2,
    }


def _ocr_hit(value, conf=0.9, status="DETECTED"):
    return {"value": value, "confidence": conf, "status": status,
            "image": "back", "box": None}


def _vision_cand(field, value, conf=0.9, status="DETECTED",
                 evidence="MRP Rs. 108"):
    return {"field": field, "value": value, "unit": None,
            "status": status, "confidence": conf,
            "evidence_text": evidence, "bbox": None, "image_id": "back",
            "source": "vision"}


# ------------------------------------------------- 1. success ---
def test_1_gemini_successful_extraction():
    from app.services.package_intelligence import vision_stage as vs

    prov = MockVisionProvider({"mrp": {
        "field": "mrp", "value": "108", "status": "DETECTED",
        "confidence": 0.9, "evidence_text": "MRP Rs. 108",
        "detail_status": "FOUND",
        "evidence_location": "front panel bottom-right",
        "handwritten": False, "source": "vision"}})
    ocr = _ocr_result()
    out = vs.enhance_ocr_with_vision(
        ocr, [(_png_bytes("front"), "front"),
              (_png_bytes("back"), "back")],
        None, inspection_id="gemini-t1", provider=prov)
    vision = out.get("vision") or {}
    assert vision.get("vision_calls", 0) >= 1
    assert vision.get("vision_calls", 0) <= 6
    assert vision.get("vision_status") == "ok"
    assert "mrp" in (vision.get("fields_returned") or [])
    rec = (out.get("reconciliation") or {}).get("fields") or {}
    assert rec["mrp"]["final_value"] == "108"
    # Symbol matrix always attached, even without symbol evidence.
    assert (out.get("symbol_verification") or {}).get(
        "combined") == "NOT_DETECTED"


# ------------------------------------------------- 2. timeout ---
def test_2_gemini_timeout_falls_back_to_ocr_only():
    from app.services.package_intelligence import vision_stage as vs

    class _Sleepy:
        name = "sleepy"
        model = "sleepy-1"

        def extract_package_fields(self, image, fields,
                                   ocr_candidates=None,
                                   layout_context=None):
            # Longer than the 0.5s minimum per-call budget enforced by
            # the service layer: every call must time out.
            time.sleep(1.2)
            return []

    ocr = _ocr_result({"mrp": _ocr_hit("108")})
    out = vs.enhance_ocr_with_vision(
        ocr, [(_png_bytes("front"), "front")], None,
        inspection_id="gemini-t2", provider=_Sleepy(),
        per_call_timeout_s=0.05, overall_timeout_s=30.0)
    vision = out.get("vision") or {}
    assert vision.get("vision_status") == "unavailable"
    assert "timed out" in str(vision.get("vision_error") or "")
    # Same bytes are never resent after a timeout: follow-up groups
    # record a skip instead of burning another call budget.
    groups = vision.get("groups") or {}
    assert "consolidated" in groups
    assert any("already failed" in str(rep.get("error") or "")
               for name, rep in groups.items() if name != "consolidated")
    # OCR pipeline still completed normally.
    assert out["fields_detailed"]["mrp"]["value"] == "108"
    assert (out.get("symbol_verification") or {}).get(
        "combined") in ("CV_ONLY", "NOT_DETECTED")


# ----------------------------------------- 3. malformed JSON ---
def test_3_gemini_malformed_json_falls_back():
    from app.services.vision.gemini_provider import GeminiVisionProvider

    prov = GeminiVisionProvider(api_key="k", model="m")
    garbage = {"candidates": [{"content": {"parts": [
        {"text": "definitely not json {"}]}}]}
    out = prov._parse(garbage, ["mrp", "quantity"])
    assert {c["field"] for c in out} == {"mrp", "quantity"}
    for cand in out:
        assert cand["status"] == "NEEDS_REVIEW"
        assert cand["value"] is None


# --------------------------------------- 4/5/6/7. verdicts ---
def test_4_agreement_becomes_ai_verified():
    ocr = _ocr_result({"mrp": _ocr_hit("108", 0.9)})
    rec = overlay_reconciliation(
        ocr, [_vision_cand("mrp", "108", 0.92)], None)
    hit = rec["fields"]["mrp"]
    assert hit["status"] == "DETECTED"
    assert hit["agreement"] == "AGREE"
    assert hit["verdict"] == "AI_VERIFIED"
    assert hit["confidence"] == pytest.approx(0.9)
    assert {c["source"] for c in hit["candidates"]} == {"rapidocr",
                                                       "vision"}


def test_5_conflict_keeps_both_values():
    ocr = _ocr_result({"mrp": _ocr_hit("108", 0.9)})
    rec = overlay_reconciliation(
        ocr, [_vision_cand("mrp", "180", 0.88)], None)
    hit = rec["fields"]["mrp"]
    assert hit["status"] == "NEEDS_REVIEW"
    assert hit["verdict"] == "CONFLICT"
    assert hit["final_value"] is None
    assert {c["value"] for c in hit["candidates"]} == {"108", "180"}


def test_6_ocr_missing_gemini_found_becomes_ai_extracted():
    ocr = _ocr_result()
    rec = overlay_reconciliation(
        ocr, [_vision_cand("mrp", "108", 0.9,
                            evidence="MRP Rs. 108")], None)
    hit = rec["fields"]["mrp"]
    assert hit["verdict"] == "AI_EXTRACTED"
    assert hit["final_value"] == "108"
    # Review still required: AI values are never verified evidence.
    assert hit["status"] in ("DETECTED", "NEEDS_REVIEW")


def test_7_both_missing_stays_not_detected():
    assert reconciliation_verdict(None, None, "NO_EVIDENCE",
                                  "NOT_DETECTED") == "NOT_DETECTED"
    ocr = _ocr_result()
    rec = overlay_reconciliation(ocr, [], None)
    assert rec["fields"] == {}


def test_verdict_helper_matrix():
    assert reconciliation_verdict("108", "108", "AGREE",
                                  "DETECTED") == "AI_VERIFIED"
    assert reconciliation_verdict("108", "180", "CONFLICT",
                                  "NEEDS_REVIEW") == "CONFLICT"
    assert reconciliation_verdict(None, "108", "SINGLE_SOURCE",
                                  "DETECTED") == "AI_EXTRACTED"
    assert reconciliation_verdict("108", None, "SINGLE_SOURCE",
                                  "DETECTED") == "OCR_ONLY"
    assert reconciliation_verdict("108", "UNKNOWN", "NO_EVIDENCE",
                                  "NOT_DETECTED") == "NOT_DETECTED"


# --------------------------------------- 8/9. symbol matrix ---
def test_8_symbol_cv_gemini_agreement_verified():
    out = build_symbol_verification(
        _ocr_result(veg={"status": "DETECTED",
                         "classification": "VEGETARIAN",
                         "confidence": 0.9, "provenance": "IMAGE"}),
        [_vision_cand("veg_nonveg", "VEGETARIAN", 0.88,
                       evidence="green square-circle mark")],
        ai_available=True)
    assert out["combined"] == "AI_CV_VERIFIED"
    assert out["opencv"]["classification"] == "VEGETARIAN"
    assert out["gemini"]["classification"] == "VEGETARIAN"


def test_9_symbol_cv_gemini_conflict_needs_manual_review():
    out = build_symbol_verification(
        _ocr_result(veg={"status": "DETECTED",
                         "classification": "NON_VEGETARIAN",
                         "confidence": 0.9, "provenance": "IMAGE"}),
        [_vision_cand("veg_nonveg", "VEGETARIAN", 0.88,
                       evidence="green square-circle mark")],
        ai_available=True)
    assert out["combined"] == "CONFLICT_REVIEW"


def test_symbol_unknown_plus_gemini_is_ai_extracted_review():
    out = build_symbol_verification(
        _ocr_result(),
        [_vision_cand("veg_nonveg", "VEGETARIAN", 0.88,
                       evidence="green square-circle mark")],
        ai_available=True)
    assert out["combined"] == "AI_EXTRACTED_REVIEW"


def test_symbol_unavailable_ai_keeps_cv_only():
    out = build_symbol_verification(
        _ocr_result(veg={"status": "DETECTED",
                         "classification": "VEGETARIAN",
                         "confidence": 0.9, "provenance": "IMAGE"}),
        [], ai_available=False)
    assert out["combined"] == "CV_ONLY"


# --------------------------------------- 10. never invent ---
def test_10_gemini_never_invents_missing_fields():
    from app.services.package_intelligence import vision_stage as vs

    prov = MockVisionProvider()  # default: everything NOT_DETECTED
    ocr = _ocr_result()
    out = vs.enhance_ocr_with_vision(
        ocr, [(_png_bytes("front"), "front")], None,
        inspection_id="gemini-t10", provider=prov)
    rec = (out.get("reconciliation") or {}).get("fields") or {}
    for field, entry in rec.items():
        assert entry.get("final_value") in (None, ""), field
    assert (out.get("symbol_verification") or {}).get(
        "combined") == "NOT_DETECTED"


# --------------------------------------- 11. timing metadata ---
def test_11_timing_metadata_phases():
    from app.routes.ocr import _attach_timing

    result = _ocr_result({"mrp": _ocr_hit("108")})
    result["images_analyzed"] = 2
    result["timings"]["total_ms"] = 5.0
    result["vision"] = {"vision_enabled": False,
                        "vision_status": "unavailable",
                        "vision_latency_ms": 45.0,
                        "vision_calls": 0,
                        "reconciliation_ms": None}
    result["reconciliation"] = {"fields": {},
                                "reconciliation_ms": 3.0}
    time.sleep(0.01)
    out = _attach_timing(result, time.perf_counter() - 0.05, 2)
    timing = out.get("timing") or {}
    for key in ("ocr_ms", "preprocessing_ms", "ai_verification_ms",
                "reconciliation_ms", "total_backend_ms", "images"):
        assert key in timing, key
    assert timing["ocr_ms"] == 5.0
    assert timing["ai_verification_ms"] == 45.0
    assert timing["reconciliation_ms"] == 3.0
    # OCR is one phase, never the total.
    assert timing["total_backend_ms"] >= timing["ocr_ms"]


# --------------------------------------- 12. 8-image limit ---
def test_12_eight_image_limit_with_vision():
    from app.services.ocr import service as svc
    from app.services.package_intelligence import vision_stage as vs
    from app.services.vision.mock_provider import MockVisionProvider

    class _MockOcr:
        name = "mock-ocr"

        def extract(self, image_np, image_side):
            from app.services.ocr.base import OcrLine, OcrOutput

            return OcrOutput(
                lines=[OcrLine(text="MRP Rs. 108", confidence=0.9,
                               image=image_side)],
                engine=self.name)

    images = [(_png_bytes(f"img_{i}"), f"img_{i}") for i in range(8)]
    out = svc.extract_label_multi(images, provider=_MockOcr())
    assert out["images_analyzed"] == 8
    raws = [(raw, label) for raw, label in images]
    enhanced = vs.enhance_ocr_with_vision(
        out, raws, None, inspection_id="gemini-t12",
        provider=MockVisionProvider())
    assert enhanced["images_analyzed"] == 8
    assert "vision" in enhanced
    assert "symbol_verification" in enhanced
    assert "timing" not in enhanced  # route attaches it, not the stage


# --------------------------------------- rule engine purity ---
def test_rule_engine_has_no_vision_dependency():
    import pathlib
    import re

    engine_dir = pathlib.Path(__file__).resolve().parent.parent \
        / "app" / "engine"
    pattern = re.compile(r"\bvision\b|\bgemini\b", re.IGNORECASE)
    for path in sorted(engine_dir.glob("*.py")):
        text = path.read_text(encoding="utf-8")
        assert not pattern.search(text), path.name
