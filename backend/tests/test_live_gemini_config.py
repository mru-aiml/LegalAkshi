"""Live-Gemini migration tests: model config, key resolution, the STRICT
JSON prototype shape, single-field extractions (MRP/batch/packing-date/
ingredients), duplicate prevention, and frontend timing pins.

No network, no real API key: the Gemini provider is exercised through
_parse (pure function) and the mock provider everywhere else.
"""
from __future__ import annotations

import io

import pytest

from app.services.vision.gemini_provider import (
    DEFAULT_MODEL,
    GeminiVisionProvider,
)


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


# ------------------------------------------------- 1/2/3/4 ---
def test_1_enabled_plus_key_constructs_configured_provider(monkeypatch):
    from app.core import config as config_mod
    from app.services.vision import provider as provider_mod

    monkeypatch.setenv("LEGALAKSHI_VISION_ENABLED", "true")
    monkeypatch.setenv("LEGALAKSHI_VISION_PROVIDER", "gemini")
    monkeypatch.setenv("LEGALAKSHI_VISION_MODEL", "gemini-3.8-flash")
    monkeypatch.setenv("LEGALAKSHI_VISION_API_KEY", "test-key")
    config_mod.get_settings.cache_clear()
    try:
        cfg = provider_mod.get_vision_config()
        assert cfg["enabled"] is True
        assert cfg["api_key_present"] is True
        prov = provider_mod.get_vision_provider()
        assert prov is not None and prov.name == "gemini"
        assert prov.model == "gemini-3.8-flash"
        assert prov.available() is True
    finally:
        config_mod.get_settings.cache_clear()


def test_2_disabled_gives_ocr_only(monkeypatch):
    from app.core import config as config_mod
    from app.services.vision import provider as provider_mod

    monkeypatch.setenv("LEGALAKSHI_VISION_ENABLED", "false")
    monkeypatch.setenv("LEGALAKSHI_VISION_PROVIDER", "gemini")
    monkeypatch.setenv("LEGALAKSHI_VISION_API_KEY", "test-key")
    config_mod.get_settings.cache_clear()
    try:
        assert provider_mod.get_vision_provider() is None
        assert provider_mod.get_vision_config()["enabled"] is False
    finally:
        config_mod.get_settings.cache_clear()


def test_3_missing_key_gives_ocr_only(monkeypatch):
    from app.core import config as config_mod
    from app.services.vision import provider as provider_mod

    monkeypatch.setenv("LEGALAKSHI_VISION_ENABLED", "true")
    monkeypatch.setenv("LEGALAKSHI_VISION_PROVIDER", "gemini")
    monkeypatch.setenv("LEGALAKSHI_VISION_MODEL", "gemini-3.8-flash")
    monkeypatch.delenv("LEGALAKSHI_VISION_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    config_mod.get_settings.cache_clear()
    try:
        assert provider_mod.get_vision_provider() is None
        cfg = provider_mod.get_vision_config()
        assert cfg["api_key_present"] is False
    finally:
        config_mod.get_settings.cache_clear()


def test_4_model_configurable_default_is_new_flash(monkeypatch):
    from app.core import config as config_mod
    from app.services.vision import provider as provider_mod

    assert DEFAULT_MODEL == "gemini-3.8-flash"
    # No hard-coded legacy default anywhere in the provider.
    assert GeminiVisionProvider().model == "gemini-3.8-flash"
    # Plain GEMINI_API_KEY alias resolves the same secret.
    monkeypatch.setenv("LEGALAKSHI_VISION_ENABLED", "true")
    monkeypatch.setenv("LEGALAKSHI_VISION_PROVIDER", "gemini")
    monkeypatch.setenv("LEGALAKSHI_VISION_MODEL", "gemini-3.8-flash")
    monkeypatch.delenv("LEGALAKSHI_VISION_API_KEY", raising=False)
    monkeypatch.setenv("GEMINI_API_KEY", "alias-key")
    config_mod.get_settings.cache_clear()
    try:
        prov = provider_mod.get_vision_provider()
        assert prov is not None and prov.available() is True
        assert provider_mod.get_vision_config()[
            "api_key_present"] is True
    finally:
        config_mod.get_settings.cache_clear()


# ------------------------------------------------- 5/6 ---
def _full_script():
    return {f: {"field": f, "value": "seen", "status": "DETECTED",
                "confidence": 0.9, "evidence_text": "seen",
                "source": "vision"}
            for f in ("product_name", "category", "manufacturer",
                      "quantity", "unit", "manufacturing_date", "mrp",
                      "batch_lot", "best_before", "date_of_packing",
                      "expiry_date", "fssai_license",
                      "consumer_care", "country_of_origin", "ingredients",
                      "veg_nonveg", "nutrition_information")}


def test_5_consolidated_single_call_covers_wanted_fields():
    from app.services.package_intelligence import vision_stage as vs
    from app.services.vision.mock_provider import MockVisionProvider

    prov = MockVisionProvider(_full_script())
    out = vs.enhance_ocr_with_vision(
        _ocr_result(), [(_png_bytes("front"), "front"),
                        (_png_bytes("back"), "back")], None,
        inspection_id="live-t5", provider=prov)
    vision = out.get("vision") or {}
    # Front+back panels, ONE provider call (multi-image parts).
    assert vision.get("vision_calls") == 1
    assert vision.get("consolidated") is True
    assert len(prov.calls) == 1


def test_6_strict_shape_parses_code_fences_and_scales():
    prov = GeminiVisionProvider(api_key="k", model="m")
    payload = {"candidates": [{"content": {"parts": [{"text":
        "```json\n" + __import__("json").dumps([
            {"field": "mrp", "value": "460", "status": "FOUND",
             "confidence": 91, "evidence_location": "back label",
             "reason": "printed MRP line", "handwritten": False},
            {"field": "vegetarian_symbol", "value": "VEGETARIAN",
             "status": "FOUND", "confidence": 94,
             "evidence_location": "front", "reason": "green mark",
             "handwritten": False},
        ]) + "\n```"}]}}]}
    out = prov._parse(payload, ["mrp", "vegetarian_symbol"])
    by_field = {c["field"]: c for c in out}
    assert by_field["mrp"]["value"] == "460"
    assert by_field["mrp"]["status"] == "DETECTED"
    assert by_field["mrp"]["confidence"] == pytest.approx(0.91)
    assert by_field["mrp"]["handwritten"] is False
    assert by_field["mrp"]["evidence_location"] == "back label"
    # Spec alias normalises to the internal field name.
    assert "vegetarian_symbol" not in by_field
    assert by_field["veg_nonveg"]["value"] == "VEGETARIAN"


def test_6b_not_visible_and_unreadable_map_safely():
    prov = GeminiVisionProvider(api_key="k", model="m")
    out = prov._parse(_gemini_payload([
        {"field": "batch_lot", "value": None, "status": "NOT_VISIBLE",
         "confidence": 0, "evidence_location": None,
         "reason": "no batch panel visible", "handwritten": False},
        {"field": "mrp", "value": "460", "status": "UNREADABLE",
         "confidence": 40, "evidence_location": "back label",
         "reason": "glare", "handwritten": True},
    ]), ["batch_lot", "mrp"])
    by_field = {c["field"]: c for c in out}
    assert by_field["batch_lot"]["status"] == "NOT_DETECTED"
    assert by_field["batch_lot"]["value"] is None
    assert by_field["mrp"]["status"] == "NEEDS_REVIEW"
    assert by_field["mrp"]["handwritten"] is True


# ------------------------------------------------- 8/9/10/11 ---
def _parse_and_overlay(items, ocr_detailed=None):
    prov = GeminiVisionProvider(api_key="k", model="m")
    cands = prov._parse(_gemini_payload(items),
                        [i["field"] for i in items])
    from app.services.package_intelligence.vision_stage import (
        overlay_reconciliation,
    )

    return overlay_reconciliation(_ocr_result(ocr_detailed), cands,
                                  None)


def test_8_gemini_only_mrp_extraction():
    rec = _parse_and_overlay([{
        "field": "mrp", "value": "460", "status": "FOUND",
        "confidence": 91, "evidence_location": "back label",
        "reason": "MRP Rs.: 460/-", "handwritten": True}])
    hit = rec["fields"]["mrp"]
    assert hit["verdict"] == "AI_EXTRACTED"
    assert hit["final_value"] == "460"
    assert hit["candidates"][0]["handwritten"] is True


def test_9_gemini_only_batch_extraction():
    rec = _parse_and_overlay([{
        "field": "batch_lot", "value": "SUN12", "status": "FOUND",
        "confidence": 88, "evidence_location": "back label",
        "reason": "Batch No.: SUN12", "handwritten": True}])
    hit = rec["fields"]["batch_lot"]
    assert hit["verdict"] == "AI_EXTRACTED"
    assert hit["final_value"] == "SUN12"


def test_10_packing_date_kept_separate_from_best_before():
    rec = _parse_and_overlay([
        {"field": "manufacturing_date", "value": "08/26",
         "status": "FOUND", "confidence": 85,
         "evidence_location": "back label",
         "reason": "Date of Packing: 08/26", "handwritten": True},
    ])
    hit = rec["fields"]["manufacturing_date"]
    assert hit["verdict"] == "AI_EXTRACTED"
    assert hit["final_value"] == "08/26"
    # A packing date never fabricates a best-before statement.
    assert "best_before" not in rec["fields"]


def test_7_relative_best_before_retained_for_review_only():
    rec = _parse_and_overlay([{
        "field": "best_before", "value": "6 months from packing",
        "status": "FOUND", "confidence": 80,
        "evidence_location": "back label",
        "reason": "Best Before 6 Month From PKG", "handwritten": False}])
    hit = rec["fields"]["best_before"]
    # Not converted to an exact date, never usable — but visible.
    assert hit["final_value"] is None
    assert hit["status"] == "NEEDS_REVIEW"
    assert "retained for review only" in (
        hit.get("needs_review_reason") or "")


def test_11_ingredients_visible_and_invisible():
    rec = _parse_and_overlay([{
        "field": "ingredients", "value": "edible vegetable oil",
        "status": "FOUND", "confidence": 82,
        "evidence_location": "back label",
        "reason": "ingredients declaration", "handwritten": False}])
    assert rec["fields"]["ingredients"]["verdict"] == "AI_EXTRACTED"
    rec = _parse_and_overlay([{
        "field": "ingredients", "value": None, "status": "NOT_VISIBLE",
        "confidence": 0, "evidence_location": None,
        "reason": "no ingredients panel visible", "handwritten": False}])
    assert "ingredients" not in rec["fields"]


# ------------------------------------------------- 17 ---
def test_17_duplicate_images_single_consolidated_call():
    from app.services.package_intelligence import vision_stage as vs
    from app.services.package_intelligence.vision_stage import (
        _payload_hash,
    )
    from app.services.vision.mock_provider import MockVisionProvider

    raw = _png_bytes("same")
    assert _payload_hash(raw) == _payload_hash(bytes(raw))
    assert _payload_hash(raw) != _payload_hash(_png_bytes("other"))
    prov = MockVisionProvider(_full_script())
    out = vs.enhance_ocr_with_vision(
        _ocr_result(), [(raw, "front"), (bytes(raw), "back")], None,
        inspection_id="live-t17", provider=prov)
    # Identical bytes never multiply into repeated Gemini calls.
    assert (out.get("vision") or {}).get("vision_calls") == 1
    assert (out.get("vision") or {}).get("consolidated") is True
    assert len(prov.calls) == 1


# ------------------------------------------------- engine purity ---
def test_rule_engine_untouched_by_live_migration():
    import pathlib
    import re

    engine_dir = pathlib.Path(__file__).resolve().parent.parent \
        / "app" / "engine"
    pattern = re.compile(r"\bgemini\b|\bvision\b", re.IGNORECASE)
    for path in sorted(engine_dir.glob("*.py")):
        assert not pattern.search(
            path.read_text(encoding="utf-8")), path.name
