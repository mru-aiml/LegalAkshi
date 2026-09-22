"""Field-mapping audit (§9): label fragments and cross-field numbers
must NEVER become declared values — at the OCR layer, the validator
layer, and the vision overlay layer.

Synthetic fixtures only (no demo values, no network, no key).
"""
from __future__ import annotations

from app.services.ocr.base import OcrLine


def _line(text: str, conf: float = 0.9) -> OcrLine:
    return OcrLine(text=text, confidence=conf, box=None, image="back")


def _vision_overlay(field, value, evidence, ocr_detailed=None):
    from app.services.package_intelligence.vision_stage import (
        overlay_reconciliation,
    )

    cand = {"field": field, "value": value, "unit": None,
            "status": "DETECTED", "confidence": 0.9,
            "evidence_text": evidence, "bbox": None, "image_id": "back",
            "source": "vision"}
    ocr = {"fields_detailed": dict(ocr_detailed or {})}
    return overlay_reconciliation(ocr, [cand], None)


# ------------------------------------------------- label text ---
def test_batch_label_never_becomes_batch_value():
    from app.services.ocr.fields import extract_fields
    from app.services.package_intelligence.validators import (
        validate_batch_candidate,
    )

    out = extract_fields([_line("Batch Number")])
    assert out["batch_lot"]["value"] is None
    assert validate_batch_candidate(
        "Number", evidence="Batch Number")[0] is False
    assert validate_batch_candidate(
        "Batch", evidence="Batch Number")[0] is False
    rec = _vision_overlay("batch_lot", "Number", "Batch Number")
    assert rec["fields"]["batch_lot"]["final_value"] is None


def test_packing_label_never_becomes_date_value():
    from app.services.ocr.fields import extract_fields

    out = extract_fields([_line("Date of Packing:")])
    assert out["manufacturing_date"]["value"] is None
    rec = _vision_overlay("manufacturing_date", "Date of Packing",
                          "Date of Packing:")
    assert rec["fields"]["manufacturing_date"]["final_value"] is None


def test_mrp_label_never_becomes_mrp_value():
    from app.services.ocr.fields import extract_fields
    from app.services.package_intelligence.validators import (
        validate_mrp_candidate,
    )

    out = extract_fields([_line("MRP")])
    assert out["mrp"]["value"] is None
    assert validate_mrp_candidate("MRP", evidence="MRP")[0] is False
    rec = _vision_overlay("mrp", "MRP", "MRP")
    assert rec["fields"]["mrp"]["final_value"] is None


# ------------------------------------------------- cross-field ---
def test_fssai_number_never_becomes_batch():
    from app.services.package_intelligence.validators import (
        validate_batch_candidate,
    )

    ok, _ = validate_batch_candidate(
        "21526079003978", evidence="FSSAI Lic. No. 21526079003978")
    assert ok is False
    rec = _vision_overlay("batch_lot", "21526079003978",
                          "FSSAI Lic. No. 21526079003978")
    assert rec["fields"]["batch_lot"]["final_value"] is None


def test_care_number_never_becomes_mrp():
    from app.services.package_intelligence.validators import (
        validate_mrp_candidate,
    )

    ok, _ = validate_mrp_candidate(
        "9890240514", evidence="Customer care no: 9890 240 514")
    assert ok is False
    rec = _vision_overlay("mrp", "9890240514",
                          "Customer care no: 9890 240 514")
    assert rec["fields"]["mrp"]["final_value"] is None


def test_best_before_statement_never_becomes_expiry():
    rec = _vision_overlay("expiry_date", "Best Before 24 Months",
                          "Best Before 24 Months")
    hit = rec["fields"]["expiry_date"]
    assert hit["final_value"] is None
    assert hit["status"] == "NEEDS_REVIEW"
    assert "best_before" not in rec["fields"]


def test_expiry_date_stays_expiry_not_best_before():
    rec = _vision_overlay("expiry_date", "13/12/28", "EXP 13/12/28")
    hit = rec["fields"]["expiry_date"]
    assert hit["final_value"] == "13/12/28"
    assert "best_before" not in rec["fields"]


# ------------------------------------------------- modality ---
def test_modality_preserved_on_agree_and_conflict():
    from app.services.package_intelligence.vision_stage import (
        overlay_reconciliation,
    )

    def _cand(value):
        return {"field": "mrp", "value": value, "unit": None,
                "status": "DETECTED", "confidence": 0.9,
                "evidence_text": "MRP Rs.: 460/-", "bbox": None,
                "image_id": "back", "source": "vision",
                "handwritten": True, "modality": "AI_HANDWRITTEN",
                "provider": "gemini", "model": "gemini-3.8-flash"}

    ocr = {"fields_detailed": {
        "mrp": {"value": "460", "confidence": 0.9, "status": "DETECTED",
                "image": "back", "box": None}}}
    agree = overlay_reconciliation(ocr, [_cand("460")], None)
    vis = [c for c in agree["fields"]["mrp"]["candidates"]
           if c["source"] == "vision"][0]
    assert vis["handwritten"] is True
    assert vis["modality"] == "AI_HANDWRITTEN"
    assert vis["provider"] == "gemini"

    conflict = overlay_reconciliation(ocr, [_cand("480")], None)
    vis = [c for c in conflict["fields"]["mrp"]["candidates"]
           if c["source"] == "vision"][0]
    assert vis["handwritten"] is True
    assert vis["modality"] == "AI_HANDWRITTEN"
    # OCR leg keeps no AI flags: fallback never mislabelled as AI.
    ocr_leg = [c for c in conflict["fields"]["mrp"]["candidates"]
               if c["source"] == "rapidocr"][0]
    assert ocr_leg.get("modality") is None


# ------------------------------------------------- model config ---
def test_gemini_model_env_wins_and_reaches_request(monkeypatch):
    import json as _json
    import urllib.request as _urlreq

    import app.services.vision.gemini_provider as gem_mod
    from app.core import config as config_mod
    from app.services.vision import provider as provider_mod

    monkeypatch.setenv("GEMINI_MODEL", "gemini-3.8-flash")
    monkeypatch.setenv("LEGALAKSHI_VISION_MODEL", "gemini-2.0-flash")
    monkeypatch.setenv("LEGALAKSHI_VISION_ENABLED", "true")
    monkeypatch.setenv("LEGALAKSHI_VISION_PROVIDER", "gemini")
    monkeypatch.setenv("LEGALAKSHI_VISION_API_KEY", "test-key")
    config_mod.get_settings.cache_clear()
    try:
        assert provider_mod._resolve_model(
            config_mod.get_settings()) == "gemini-3.8-flash"
        prov = provider_mod.get_vision_provider()
        assert prov is not None and prov.model == "gemini-3.8-flash"
    finally:
        config_mod.get_settings.cache_clear()

    seen: dict = {}

    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return _json.dumps({"candidates": [{"content": {
                "parts": [{"text": "[]"}]}}]}).encode()

    def _fake_urlopen(req, timeout=None):
        seen["url"] = req.full_url
        return _Resp()

    monkeypatch.setattr(_urlreq, "urlopen", _fake_urlopen)
    prov = gem_mod.GeminiVisionProvider(api_key="k",
                                        model="gemini-3.8-flash")
    prov.extract_package_fields(_blank_png(), ["mrp"],
                                None, None)
    assert "gemini-3.8-flash" in seen["url"]
    assert "gemini-2.0-flash" not in seen["url"]


def _blank_png() -> bytes:
    import io as _io

    from PIL import Image as _Image

    buf = _io.BytesIO()
    _Image.new("RGB", (64, 64), "white").save(buf, format="PNG")
    return buf.getvalue()
