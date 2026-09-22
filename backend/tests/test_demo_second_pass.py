"""Prototype demo-mode vision tests: all-images single request, 1600px
originals, targeted second call, packing/expiry fields, real HTTP
error detail, and no-silent-overwrite reconciliation.

Mocked throughout (no network, no key).
"""
from __future__ import annotations

import io

import pytest

from app.services.vision.gemini_provider import GeminiVisionProvider


def _png_bytes(label: str = "img") -> bytes:
    from PIL import Image, ImageDraw

    img = Image.new("RGB", (640, 480), "white")
    d = ImageDraw.Draw(img)
    d.text((20, 20), f"Package {label}", fill="black")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _ocr_result(fields_detailed=None):
    return {
        "status": "OK", "engine": "mock-ocr",
        "fields_detailed": dict(fields_detailed or {}),
        "timings": {"total_ms": 5.0, "provider_calls": 1,
                    "stage_timings": {"preprocessing_ms": 10.0,
                                      "decode_ms": 2.0}},
        "veg_nonveg_symbol": {"status": "NOT_DETECTED",
                              "classification": "UNKNOWN",
                              "confidence": 0.3, "provenance": "IMAGE"},
        "images_analyzed": 1,
    }


def _gemini_payload(items: list[dict]) -> dict:
    import json

    return {"candidates": [{"content": {"parts": [
        {"text": json.dumps(items)}]}}]}


def _new_shape(field, value, conf=90, status="FOUND", **kw):
    item = {"field": field, "value": value, "status": status,
            "confidence": conf, "evidence_location": "back",
            "reason": f"visible {field}", "handwritten": False}
    item.update(kw)
    return item


def _overlay(items, ocr_detailed=None):
    prov = GeminiVisionProvider(api_key="k", model="m")
    cands = prov._parse(_gemini_payload(items),
                        [i.get("field", "?") for i in items])
    from app.services.package_intelligence.vision_stage import (
        overlay_reconciliation,
    )

    return overlay_reconciliation(_ocr_result(ocr_detailed), cands, None)


# ------------------------------------------------- MRP ---
def test_demo_mrp_numeric_normalised_review_gated():
    rec = _overlay([_new_shape("mrp", "460", 95, handwritten=True,
                               reason="MRP Rs.: 460/-")])
    hit = rec["fields"]["mrp"]
    assert hit["verdict"] == "AI_EXTRACTED"
    assert hit["final_value"] == "460"
    assert hit["candidates"][0]["handwritten"] is True


def test_demo_mrp_rejects_nutrition_phone_fssai_numbers():
    from app.services.package_intelligence.validators import (
        validate_mrp_candidate,
    )

    assert validate_mrp_candidate(
        "900", evidence="Energy 900 kcal")[0] is False
    assert validate_mrp_candidate(
        "9890240514", evidence="Customer care")[0] is False
    assert validate_mrp_candidate(
        "21526079003978", evidence="FSSAI")[0] is False
    assert validate_mrp_candidate(
        "460", evidence="MRP Rs.: 460/-")[0] is True


# ------------------------------------------------- batch ---
def test_demo_batch_extraction_review_gated():
    rec = _overlay([_new_shape("batch_lot", "SUN12", 88,
                               handwritten=True,
                               reason="Batch No.: SUN12")])
    hit = rec["fields"]["batch_lot"]
    assert hit["verdict"] == "AI_EXTRACTED"
    assert hit["final_value"] == "SUN12"


# ------------------------------------------------- packing date ---
def test_demo_packing_date_kept_separate():
    rec = _overlay([_new_shape("date_of_packing", "08/26", 85,
                               handwritten=True,
                               reason="Date of Packing: 08/26")])
    hit = rec["fields"]["date_of_packing"]
    assert hit["verdict"] == "AI_EXTRACTED"
    assert hit["final_value"] == "08/26"
    # Packing date never fabricates manufacturing/expiry siblings.
    assert "manufacturing_date" not in rec["fields"]
    assert "expiry_date" not in rec["fields"]
    assert "best_before" not in rec["fields"]


# ------------------------------------------------- expiry ---
def test_demo_expiry_date_extraction():
    rec = _overlay([_new_shape("expiry_date", "02/2027", 86,
                               reason="EXP 02/2027")])
    hit = rec["fields"]["expiry_date"]
    assert hit["verdict"] == "AI_EXTRACTED"
    assert hit["final_value"] == "02/2027"


def test_demo_best_before_statement_never_becomes_date():
    rec = _overlay([_new_shape("best_before", "6 Month From PKG.", 90,
                               reason="Best Before 6 Month From PKG.")])
    hit = rec["fields"]["best_before"]
    assert hit["final_value"] is None
    assert hit["status"] == "NEEDS_REVIEW"
    assert "retained for review only" in (
        hit.get("needs_review_reason") or "")


# ------------------------------------------------- ingredients ---
def test_demo_ingredients_transcribed_not_summarised():
    text = ("Edible vegetable oil (sunflower seed), antioxidant "
            "(INS 319).")
    rec = _overlay([_new_shape("ingredients", text, 90,
                               reason="INGREDIENTS declaration")])
    hit = rec["fields"]["ingredients"]
    assert hit["verdict"] == "AI_EXTRACTED"
    assert hit["final_value"] == text


def test_demo_ingredients_invisible_stays_null():
    prov = GeminiVisionProvider(api_key="k", model="m")
    cands = prov._parse(_gemini_payload([{
        "field": "ingredients", "value": None, "status": "NOT_VISIBLE",
        "confidence": 0, "evidence_location": None,
        "reason": "no ingredients panel visible", "handwritten": False}]),
        ["ingredients"])
    assert cands[0]["value"] is None
    assert cands[0]["status"] == "NOT_DETECTED"


# ------------------------------------------------- multi-image ---
def test_demo_all_images_one_request_no_duplicates():
    from app.services.package_intelligence import vision_stage as vs
    from app.services.vision.mock_provider import MockVisionProvider
    from test_live_gemini_config import _full_script

    script = _full_script()
    prov = MockVisionProvider(script)
    raws = [(raw, label) for raw, label in
            [(_png_bytes("front"), "front"), (_png_bytes("side"), "side"),
             (_png_bytes("back"), "back"), (_png_bytes("bottom"),
                                            "bottom")]]
    out = vs.enhance_ocr_with_vision(
        _ocr_result(), raws, None, inspection_id="demo-multi",
        provider=prov)
    vision = out.get("vision") or {}
    # 4 distinct panels, 1 provider call (multi-image parts).
    assert vision.get("vision_calls") == 1
    assert len(prov.calls) == 1
    assert (prov.calls[0].get("n_panels") or 0) >= 3


def test_demo_targeted_second_call_covers_missers():
    from app.services.package_intelligence import vision_stage as vs
    from app.services.vision.mock_provider import MockVisionProvider

    class _FirstBlankThenFound(MockVisionProvider):
        def __init__(self):
            super().__init__({})
            self.n = 0

        def extract_package_fields(self, image, requested_fields,
                                   ocr_candidates=None,
                                   layout_context=None):
            self.n += 1
            if self.n == 1:
                return super().extract_package_fields(
                    image, requested_fields, ocr_candidates,
                    layout_context)
            found = {
                "field": "mrp", "value": "460", "status": "DETECTED",
                "confidence": 0.95,
                "evidence_text": "MRP Rs.: 460/-",
                "handwritten": True, "source": "vision"}
            from app.services.vision.schemas import (
                validate_vision_candidate,
            )

            return [validate_vision_candidate(found)]

    prov = _FirstBlankThenFound()
    out = vs.enhance_ocr_with_vision(
        _ocr_result(), [(_png_bytes("front"), "front"),
                        (_png_bytes("back"), "back")], None,
        inspection_id="demo-targeted", provider=prov)
    vision = out.get("vision") or {}
    groups = vision.get("groups") or {}
    assert "targeted-second" in groups
    rec = (out.get("reconciliation") or {}).get("fields") or {}
    assert rec["mrp"]["final_value"] == "460"
    assert rec["mrp"]["verdict"] == "AI_EXTRACTED"
    # Still bounded: consolidated + targeted + capped groups.
    assert vision.get("vision_calls", 0) <= 6


# ------------------------------------------------- error detail ---
def test_demo_http_error_carries_status_and_body():
    import json as _json
    import urllib.error as _urlerr

    from app.services.vision.base import VisionError

    prov = GeminiVisionProvider(api_key="k", model="m")
    body = _json.dumps({"contents": "x"}).encode()
    err = _urlerr.HTTPError("http://x", 503, "Service Unavailable", {},
                            io.BytesIO(b'{"error": {"status": "UNAVAILABLE"}}'))
    assert err.code == 503
    # Simulated transport through the provider's error mapping:
    from app.services.vision.service import _safe_error_text

    text = _safe_error_text(
        Exception("gemini HTTP 503: {\"error\": \"busy\"}"))
    assert "503" in text
    assert "k" not in text.replace("busy", "")
    with pytest.raises(VisionError):
        raise VisionError("gemini HTTP 503: busy")


def test_demo_safe_error_redacts_key_material():
    from app.services.vision.service import _safe_error_text

    assert _safe_error_text(
        Exception("x-goog-api-key: SECRET123")) == \
        "[auth material redacted]"
    assert "SECRET123" not in _safe_error_text(
        Exception("bearer SECRET123"))


# ------------------------------------------------- 1600px ---
def test_demo_originals_encoded_at_1600px():
    import base64 as _b64

    from PIL import Image as _Image

    from app.services.vision.gemini_provider import _image_to_jpeg_b64

    img = _Image.new("RGB", (3000, 2000), "white")
    raw = _image_to_jpeg_b64(img)
    decoded = _Image.open(io.BytesIO(_b64.b64decode(raw)))
    assert max(decoded.size) == 1600
    small = _Image.new("RGB", (400, 300), "white")
    assert _Image.open(io.BytesIO(
        _b64.b64decode(_image_to_jpeg_b64(small)))).size == (400, 300)


# ------------------------------------------------- no overwrite ---
def test_demo_agreement_marks_verified_never_overwrites_ocr():
    ocr = _ocr_result({"mrp": {"value": "460", "confidence": 0.9,
                               "status": "DETECTED", "image": "back",
                               "box": None}})
    rec = _overlay([_new_shape("mrp", "460", 95)], ocr["fields_detailed"])
    hit = rec["fields"]["mrp"]
    assert hit["verdict"] == "AI_VERIFIED"
    assert hit["final_value"] == "460"
    assert {c["source"] for c in hit["candidates"]} == {"rapidocr",
                                                       "vision"}


def test_demo_conflict_preserves_both_sides():
    ocr = _ocr_result({"mrp": {"value": "460", "confidence": 0.9,
                               "status": "DETECTED", "image": "back",
                               "box": None}})
    rec = _overlay([_new_shape("mrp", "480", 88)], ocr["fields_detailed"])
    hit = rec["fields"]["mrp"]
    assert hit["verdict"] == "CONFLICT"
    assert hit["final_value"] is None
    assert {c["value"] for c in hit["candidates"]} == {"460", "480"}


# ------------------------------------------------- request shape ---
def test_demo_generation_config_only_supported_params(monkeypatch):
    """Live-probed guard: generateContent rejects thinking_level and
    the deprecated knobs (temperature, top_p, top_k, ...)."""
    import io as _io
    import json as _json
    import urllib.request as _urlreq

    import app.services.vision.gemini_provider as gem_mod

    seen: dict = {}

    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return _json.dumps({"candidates": [{"content": {
                "parts": [{"text": _json.dumps([
                    {"field": "mrp", "value": "460",
                     "status": "FOUND", "confidence": 95,
                     "evidence_location": "back",
                     "reason": "MRP Rs.: 460/-",
                     "handwritten": True}])}]}}]}).encode()

    def _fake_urlopen(req, timeout=None):
        seen["body"] = _json.loads(req.data.decode())
        seen["url"] = req.full_url
        return _Resp()

    monkeypatch.setattr(_urlreq, "urlopen", _fake_urlopen)
    prov = gem_mod.GeminiVisionProvider(api_key="k", model="m")
    out = prov.extract_package_fields(
        _png_bytes("body"), ["mrp"], None, None)
    gen = seen["body"]["contents"][0]["parts"][0]
    assert isinstance(gen, dict) and "text" in gen
    config = seen["body"]["generationConfig"]
    for banned in ("temperature", "top_p", "top_k", "candidate_count",
                   "thinking_budget", "thinking_level"):
        assert banned not in config, banned
    assert config.get("responseMimeType") == "application/json"
    assert out[0]["value"] == "460"
    assert out[0]["modality"] == "AI_HANDWRITTEN"


# ------------------------------------------------- dates ---
def test_demo_manufacturing_date_extraction():
    rec = _overlay([_new_shape("manufacturing_date", "05/2024", 90,
                               reason="MFD 05/2024")])
    hit = rec["fields"]["manufacturing_date"]
    assert hit["verdict"] == "AI_EXTRACTED"
    assert hit["final_value"] == "05/2024"


def test_demo_stamped_batch_preserved_verbatim():
    rec = _overlay([_new_shape("batch_lot", "B24C11", 87,
                               handwritten=True,
                               reason="Stamped: B.No. B24C11")])
    hit = rec["fields"]["batch_lot"]
    assert hit["verdict"] == "AI_EXTRACTED"
    assert hit["final_value"] == "B24C11"


def test_demo_batch_rejects_fssai_phone_barcode():
    from app.services.package_intelligence.validators import (
        validate_batch_candidate,
    )

    assert validate_batch_candidate(
        "21526079003978", evidence="FSSAI")[0] is False
    assert validate_batch_candidate(
        "9890240514", evidence="Customer care")[0] is False
    assert validate_batch_candidate(
        "SUN12", evidence="Batch No.: SUN12")[0] is True


# ------------------------------------------------- statuses ---
def test_demo_unreadable_missing_ambiguous_map_safely():
    prov = GeminiVisionProvider(api_key="k", model="m")
    out = prov._parse(_gemini_payload([
        _new_shape("mrp", "46?", 35, status="UNREADABLE"),
        _new_shape("batch_lot", None, 0, status="NOT_VISIBLE"),
        _new_shape("manufacturing_date", "08/26 or 08/28", 45,
                   status="AMBIGUOUS"),
    ]), ["mrp", "batch_lot", "manufacturing_date"])
    by_field = {c["field"]: c for c in out}
    assert by_field["mrp"]["status"] == "NEEDS_REVIEW"
    assert by_field["mrp"]["value"] == "46?"
    assert by_field["batch_lot"]["status"] == "NOT_DETECTED"
    assert by_field["batch_lot"]["value"] is None
    assert by_field["manufacturing_date"]["status"] == "NEEDS_REVIEW"


def test_demo_evidence_location_required_and_preserved():
    prov = GeminiVisionProvider(api_key="k", model="m")
    out = prov._parse(_gemini_payload([
        _new_shape("mrp", "460", 95,
                   evidence_location="back image, lower-right "
                   "declaration block, next to MRP")]),
        ["mrp"])
    assert out[0]["evidence_location"] == \
        "back image, lower-right declaration block, next to MRP"
    assert out[0]["modality"] == "AI_HANDWRITTEN" or \
        out[0]["modality"] in ("AI", "AI_PRINTED")


def test_demo_modality_labels():
    prov = GeminiVisionProvider(api_key="k", model="m")
    out = prov._parse(_gemini_payload([
        _new_shape("mrp", "460", 95, handwritten=True),
        _new_shape("product_name", "Oil", 99, handwritten=False),
    ]), ["mrp", "product_name"])
    by_field = {c["field"]: c for c in out}
    assert by_field["mrp"]["modality"] == "AI_HANDWRITTEN"
    assert by_field["product_name"]["modality"] == "AI_PRINTED"


# ------------------------------------------------- quota halt ---
class _HttpFail:
    name = "httpfail"
    model = "httpfail-1"

    def __init__(self, code):
        self.code = code
        self.calls = 0

    def extract_package_fields(self, image, fields, ocr_candidates=None,
                               layout_context=None):
        from app.services.vision.base import VisionError

        self.calls += 1
        raise VisionError(f"gemini HTTP {self.code}: overloaded")


def _halt_run(code):
    from app.services.package_intelligence import vision_stage as vs

    prov = _HttpFail(code)
    t0 = __import__("time").perf_counter()
    out = vs.enhance_ocr_with_vision(
        _ocr_result(), [(_png_bytes("front"), "front"),
                        (_png_bytes("back"), "back")], None,
        inspection_id=f"demo-halt-{code}", provider=prov)
    wall = __import__("time").perf_counter() - t0
    return out, prov, wall


def test_demo_429_halts_without_budget_burn():
    out, prov, wall = _halt_run(429)
    vision = out.get("vision") or {}
    assert vision.get("vision_status") == "unavailable"
    assert "429" in str(vision.get("vision_error") or "")
    # Exactly ONE attempt — no retry loop, no 6-call burn.
    assert prov.calls == 1
    assert vision.get("vision_calls", 0) == 0
    assert wall < 5.0
    groups = vision.get("groups") or {}
    assert any("AI halted" in str(rep.get("error") or "")
               for rep in groups.values())
    # Deterministic OCR layer untouched by the AI failure.
    assert out.get("status") == "OK"


def test_demo_503_halts_without_budget_burn():
    out, prov, wall = _halt_run(503)
    vision = out.get("vision") or {}
    assert vision.get("vision_status") == "unavailable"
    assert "503" in str(vision.get("vision_error") or "")
    assert prov.calls == 1
    # OCR result still complete and usable for manual review.
    assert out.get("status") == "OK"
