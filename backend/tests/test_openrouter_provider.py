"""OpenRouter (Gemma free) provider tests: config, request shape, parsing,
fallback, and integrity guards. No network, no real key — urlopen is
faked where transport is involved.
"""
from __future__ import annotations

import io
import json as _json

import pytest

from app.services.vision.openrouter_provider import (
    DEFAULT_BASE_URL,
    DEFAULT_MODEL,
    OpenRouterVisionProvider,
)


def _png_bytes(label: str = "img") -> bytes:
    from PIL import Image, ImageDraw

    img = Image.new("RGB", (640, 480), "white")
    d = ImageDraw.Draw(img)
    d.text((20, 20), f"Package {label}", fill="black")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _fake_transport(monkeypatch, payload=None, http_code=None):
    import urllib.request as _urlreq

    import app.services.vision.openrouter_provider as or_mod

    seen: dict = {}

    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return _json.dumps(payload).encode()

    def _fake_urlopen(req, timeout=None):
        seen["url"] = req.full_url
        seen["headers"] = dict(req.header_items())
        seen["body"] = _json.loads(req.data.decode())
        if http_code is not None:
            import urllib.error as _urlerr

            raise _urlerr.HTTPError(
                req.full_url, http_code, "error", {}, io.BytesIO(b"{}"))
        return _Resp()

    monkeypatch.setattr(_urlreq, "urlopen", _fake_urlopen)
    monkeypatch.setattr(or_mod.log, "disabled", True)
    return seen


def _choices(items):
    return {"choices": [{"message": {"content": _json.dumps(items)}}]}


# ------------------------------------------------- config ---
def test_default_model_is_exactly_free_gemma():
    assert DEFAULT_MODEL == "google/gemma-4-31b-it:free"
    assert DEFAULT_BASE_URL == "https://openrouter.ai/api/v1"
    assert OpenRouterVisionProvider().model == "google/gemma-4-31b-it:free"


def test_resolution_and_free_only_guard(monkeypatch):
    from app.core import config as config_mod
    from app.services.vision import provider as provider_mod

    monkeypatch.setenv("LEGALAKSHI_VISION_ENABLED", "true")
    monkeypatch.setenv("LEGALAKSHI_VISION_PROVIDER", "openrouter")
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.delenv("OPENROUTER_MODEL", raising=False)
    config_mod.get_settings.cache_clear()
    try:
        prov = provider_mod.get_vision_provider()
        assert prov is not None and prov.name == "openrouter"
        assert prov.model == "google/gemma-4-31b-it:free"
        assert provider_mod.get_vision_config()["api_key_present"] is True
    finally:
        config_mod.get_settings.cache_clear()

    # Explicit paid model is refused (OCR-only), never silently used.
    monkeypatch.setenv("OPENROUTER_MODEL", "google/gemma-4-31b-it")
    config_mod.get_settings.cache_clear()
    try:
        assert provider_mod.get_vision_provider() is None
        body_status = provider_mod.describe_vision_status()["status"]
        assert body_status == "INVALID_CONFIGURATION"
    finally:
        config_mod.get_settings.cache_clear()


def test_missing_key_is_ocr_only(monkeypatch):
    from app.core import config as config_mod
    from app.services.vision import provider as provider_mod

    monkeypatch.setenv("LEGALAKSHI_VISION_ENABLED", "true")
    monkeypatch.setenv("LEGALAKSHI_VISION_PROVIDER", "openrouter")
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    config_mod.get_settings.cache_clear()
    try:
        assert provider_mod.get_vision_provider() is None
    finally:
        config_mod.get_settings.cache_clear()


# ------------------------------------------------- request ---
def test_images_actually_sent_as_data_urls(monkeypatch):
    seen = _fake_transport(monkeypatch, payload=_choices([]))
    prov = OpenRouterVisionProvider(api_key="k")
    front, back = _png_bytes("front"), _png_bytes("back")
    prov.extract_package_fields([front, back], ["mrp"], None, None)
    assert seen["url"] == DEFAULT_BASE_URL + "/chat/completions"
    assert seen["headers"].get("Authorization") == "Bearer k"
    parts = seen["body"]["messages"][0]["content"]
    images = [p for p in parts if p.get("type") == "image_url"]
    assert len(images) == 2
    for img in images:
        assert img["image_url"]["url"].startswith("data:image/jpeg;base64,")
    assert seen["body"]["model"] == "google/gemma-4-31b-it:free"
    assert seen["body"]["response_format"] == {"type": "json_object"}


def test_no_deprecated_generation_params(monkeypatch):
    seen = _fake_transport(monkeypatch, payload=_choices([]))
    prov = OpenRouterVisionProvider(api_key="k")
    prov.extract_package_fields(_png_bytes(), ["mrp"], None, None)
    # OpenAI chat schema carries no generationConfig at all.
    assert "generationConfig" not in seen["body"]
    for banned in ("temperature", "top_p", "top_k", "thinking_level"):
        assert banned not in _json.dumps(seen["body"]), banned


# ------------------------------------------------- parsing ---
def test_object_shape_parses_with_source_image():
    prov = OpenRouterVisionProvider(api_key="k")
    payload = {"choices": [{"message": {"content": _json.dumps({
        "fields": {
            "mrp": {"value": "460", "status": "VISIBLE",
                    "confidence": 95,
                    "evidence_location": "back lower-right",
                    "source_image": "back", "source": "AI_HANDWRITTEN"},
            "batch_number": {"value": None, "status": "NOT_VISIBLE",
                             "confidence": 0,
                             "evidence_location": "searched images",
                             "source_image": None, "source": "AI"},
        },
        "vegetarian_symbol": {"value": "VEGETARIAN", "status": "VISIBLE",
                              "confidence": 98,
                              "evidence_location": "front",
                              "source_image": "front",
                              "source": "AI"},
    })}}]}
    out = prov._parse(payload, ["mrp", "batch_number"])
    by_field = {c["field"]: c for c in out}
    assert by_field["mrp"]["value"] == "460"
    assert by_field["mrp"]["status"] == "DETECTED"
    assert by_field["mrp"]["confidence"] == pytest.approx(0.95)
    assert by_field["mrp"]["image_id"] == "back"
    assert by_field["mrp"]["modality"] == "AI_HANDWRITTEN"
    assert by_field["batch_lot"]["status"] == "NOT_DETECTED"
    assert by_field["veg_nonveg"]["value"] == "VEGETARIAN"


def test_malformed_response_never_crashes():
    prov = OpenRouterVisionProvider(api_key="k")
    out = prov._parse({"choices": [{"message": {"content": "nope {"}}]},
                      ["mrp", "batch_lot"])
    assert {c["field"] for c in out} == {"mrp", "batch_lot"}
    assert all(c["status"] == "NEEDS_REVIEW" and c["value"] is None
               for c in out)


def test_flat_field_map_parses():
    prov = OpenRouterVisionProvider(api_key="k")
    payload = {"choices": [{"message": {"content": _json.dumps({
        "mrp": {"value": "460", "status": "VISIBLE", "confidence": 95,
                "evidence_location": "back", "source_image": "back",
                "source": "AI_HANDWRITTEN"},
        "notes": "free prose must not become a field",
    })}}]}
    out = prov._parse(payload, ["mrp"])
    assert len(out) == 1
    assert out[0]["field"] == "mrp"
    assert out[0]["value"] == "460"
    assert out[0]["status"] == "DETECTED"


def test_content_blocks_join_and_empty_parse_logged(caplog):
    import logging as _logging

    prov = OpenRouterVisionProvider(api_key="k")
    payload = {"choices": [{"message": {"content": [
        {"type": "text", "text": _json.dumps([
            {"field": "mrp", "value": "460", "status": "VISIBLE",
             "confidence": 95, "evidence_location": "back",
             "source_image": "back", "source": "AI"}])}]}}]}
    out = prov._parse(payload, ["mrp"])
    assert len(out) == 1 and out[0]["value"] == "460"
    with caplog.at_level(_logging.DEBUG,
                         logger="legalakshi.vision.openrouter"):
        assert prov._parse({"choices": [{"message": {"content": "{}"}}]},
                           ["mrp"]) == []
    assert "openrouter parse" in caplog.text


def test_health_check_shape(monkeypatch):
    seen = _fake_transport(
        monkeypatch,
        payload={"choices": [{"message": {"content": "OK"}}]})
    prov = OpenRouterVisionProvider(api_key="k")
    assert prov.health_check()["ok"] is True
    assert "chat/completions" in seen["url"]


# ------------------------------------------------- fallback ---
def test_http_errors_fallback_cleanly_no_retry():
    from app.services.vision.base import VisionError

    import app.services.vision.openrouter_provider as or_mod

    class _Failing(OpenRouterVisionProvider):
        def __init__(self, code):
            super().__init__(api_key="k")
            self.code = code
            self.calls = 0

        def extract_package_fields(self, image, fields,
                                   ocr_candidates=None,
                                   layout_context=None):
            self.calls += 1
            raise VisionError(f"openrouter HTTP {self.code}: busy")

    from app.services.package_intelligence import vision_stage as vs

    def _ocr():
        return {"status": "OK", "engine": "x", "fields_detailed": {},
                "timings": {"total_ms": 1.0},
                "veg_nonveg_symbol": {}, "images_analyzed": 1}

    for code in (401, 429, 503):
        prov = _Failing(code)
        out = vs.enhance_ocr_with_vision(
            _ocr(), [(_png_bytes(), "front")], None,
            inspection_id=f"or-halt-{code}", provider=prov)
        vision = out.get("vision") or {}
        assert vision.get("vision_status") == "unavailable", code
        assert str(code) in str(vision.get("vision_error") or ""), code
        assert prov.calls == 1, code  # halted: exactly one attempt
        assert out.get("status") == "OK", code


# ------------------------------------------------- separation ---
def test_packing_expiry_best_before_stay_separate():
    from app.services.package_intelligence.vision_stage import (
        overlay_reconciliation,
    )

    def _cand(field, value, evidence="seen"):
        return {"field": field, "value": value, "unit": None,
                "status": "DETECTED", "confidence": 0.9,
                "evidence_text": evidence, "bbox": None, "image_id": "back",
                "source": "vision"}

    ocr = {"fields_detailed": {}}
    rec = overlay_reconciliation(
        ocr, [_cand("date_of_packing", "13/08/26",
                    "Date of Packing: 13/08/26"),
              _cand("manufacturing_date", "13/08/26",
                    "MFD 13/08/26")], None)
    # Same text, two distinct fields — never merged, never converted.
    assert rec["fields"]["date_of_packing"]["final_value"] == "13/08/26"
    assert rec["fields"]["manufacturing_date"]["final_value"] == "13/08/26"
    rec = overlay_reconciliation(
        ocr, [_cand("best_before", "Best Before 24 Months")], None)
    assert "expiry_date" not in rec["fields"]


# ------------------------------------------------- integrity ---
def test_no_key_in_frontend_or_logs():
    import pathlib

    root = pathlib.Path(__file__).resolve().parent.parent.parent
    src = root / "artifacts" / "nutricheck" / "src"
    blob = "\n".join(
        p.read_text(encoding="utf-8", errors="replace")
        for p in src.rglob("*.ts*"))
    assert "OPENROUTER_API_KEY" not in blob
    assert "sk-or-" not in blob
    from app.services.vision.service import _safe_error_text

    assert _safe_error_text(
        Exception("Bearer sk-or-secret")) == "[auth material redacted]"


def test_readability_status_maps_to_stable_statuses():
    prov = OpenRouterVisionProvider(api_key="k")
    out = prov._parse(_choices([
        {"field": "mrp", "value": "460", "readability_status": "CLEAR",
         "confidence": 95, "evidence_location": "back",
         "source_image": "back", "source": "AI"},
        {"field": "batch_lot", "value": "SUN?", "readability_status": "AMBIGUOUS",
         "confidence": 40, "evidence_location": "back",
         "source_image": "back", "source": "AI"},
        {"field": "expiry_date", "value": None,
         "readability_status": "NOT_VISIBLE", "confidence": 0,
         "evidence_location": "searched", "source_image": None,
         "source": "AI"},
    ]), ["mrp", "batch_lot", "expiry_date"])
    by_field = {c["field"]: c for c in out}
    assert by_field["mrp"]["status"] == "DETECTED"
    assert by_field["batch_lot"]["status"] == "NEEDS_REVIEW"
    assert by_field["expiry_date"]["status"] == "NOT_DETECTED"


def test_startup_diagnostic_never_logs_key():
    import pathlib

    root = pathlib.Path(__file__).resolve().parent.parent.parent
    main_src = (root / "backend" / "app" / "main.py").read_text(
        encoding="utf-8")
    assert "vision provider = %s" in main_src
    assert "API_KEY" not in main_src
    assert "api_key" not in main_src.lower().replace(
        "api_key_present", "")


def test_frontend_unchanged_by_provider_switch():
    import pathlib
    import subprocess

    root = pathlib.Path(__file__).resolve().parent.parent.parent
    for rel in ("artifacts/nutricheck/src/pages/officer-scan.tsx",
                "artifacts/nutricheck/src/lib/api.ts"):
        diff = subprocess.run(
            ["git", "status", "--porcelain", rel],
            capture_output=True, text=True, cwd=str(root))
        assert diff.stdout.strip() == "", (rel, diff.stdout)


def test_rule_engine_untouched_by_provider_switch():
    import pathlib
    import re

    root = pathlib.Path(__file__).resolve().parent.parent.parent
    # Suite runs with rootdir backend/: engine sources live at
    # backend/app/engine regardless of the repo checkout name.
    engine_dir = pathlib.Path(__file__).resolve().parent.parent \
        / "app" / "engine"
    pattern = re.compile(r"\bopenrouter\b|\bqwen\b", re.IGNORECASE)
    for path in sorted(engine_dir.glob("*.py")):
        assert not pattern.search(
            path.read_text(encoding="utf-8")), path.name
    # Stronger: the engine tree has no uncommitted modifications.
    import subprocess

    diff = subprocess.run(
        ["git", "status", "--porcelain", "backend/app/engine"],
        capture_output=True, text=True, cwd=str(root))
    assert diff.stdout.strip() == "", diff.stdout
