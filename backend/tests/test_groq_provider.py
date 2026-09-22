"""Groq vision provider tests: resolution, config, request shape,
parsing, errors, and integrity guards. No network, no real key —
urlopen is faked where transport is involved.
"""
from __future__ import annotations

import io
import json as _json

import pytest

from app.services.vision.groq_provider import (
    DEFAULT_BASE_URL,
    DEFAULT_MODEL,
    GroqVisionProvider,
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
    return seen


def _choices(items):
    return {"choices": [{"message": {"content": _json.dumps(items)}}]}


# ------------------------------------------------- resolution ---
def test_provider_resolution_selects_groq(monkeypatch):
    from app.core import config as config_mod
    from app.services.vision import provider as provider_mod

    monkeypatch.setenv("LEGALAKSHI_VISION_ENABLED", "true")
    monkeypatch.setenv("LEGALAKSHI_VISION_PROVIDER", "groq")
    monkeypatch.setenv("GROQ_API_KEY", "test-key")
    monkeypatch.delenv("GROQ_MODEL", raising=False)
    monkeypatch.delenv("GROQ_BASE_URL", raising=False)
    config_mod.get_settings.cache_clear()
    try:
        prov = provider_mod.get_vision_provider()
        assert prov is not None and prov.name == "groq"
        assert prov.model == "qwen/qwen3.8-27b"
        assert prov.base_url == "https://api.groq.com/openai/v1"
        cfg = provider_mod.get_vision_config()
        assert cfg["api_key_present"] is True
        assert cfg["model"] == "qwen/qwen3.8-27b"
    finally:
        config_mod.get_settings.cache_clear()


def test_missing_key_is_ocr_only(monkeypatch):
    from app.core import config as config_mod
    from app.services.vision import provider as provider_mod

    monkeypatch.setenv("LEGALAKSHI_VISION_ENABLED", "true")
    monkeypatch.setenv("LEGALAKSHI_VISION_PROVIDER", "groq")
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    config_mod.get_settings.cache_clear()
    try:
        assert provider_mod.get_vision_provider() is None
        status = provider_mod.describe_vision_status()
        assert status["status"] == "NOT_CONFIGURED"
        assert "GROQ_API_KEY" in status["reason"]
    finally:
        config_mod.get_settings.cache_clear()


def test_configured_key_constructs_provider(monkeypatch):
    from app.core import config as config_mod
    from app.services.vision import provider as provider_mod

    monkeypatch.setenv("LEGALAKSHI_VISION_ENABLED", "true")
    monkeypatch.setenv("LEGALAKSHI_VISION_PROVIDER", "groq")
    monkeypatch.setenv("GROQ_API_KEY", "test-key")
    monkeypatch.setenv("GROQ_MODEL", "qwen/qwen3.8-27b")
    config_mod.get_settings.cache_clear()
    try:
        prov = provider_mod.get_vision_provider()
        assert prov is not None and prov.available() is True
        assert provider_mod.describe_vision_status()["status"] == \
            "AVAILABLE"
    finally:
        config_mod.get_settings.cache_clear()


def test_defaults_and_base_url():
    assert DEFAULT_MODEL == "qwen/qwen3.8-27b"
    assert DEFAULT_BASE_URL == "https://api.groq.com/openai/v1"
    assert GroqVisionProvider().model == "qwen/qwen3.8-27b"
    assert GroqVisionProvider().base_url == \
        "https://api.groq.com/openai/v1"


# ------------------------------------------------- request ---
def test_two_images_sent_in_one_request(monkeypatch):
    seen = _fake_transport(monkeypatch, payload=_choices([]))
    prov = GroqVisionProvider(api_key="k")
    front, back = _png_bytes("front"), _png_bytes("back")
    prov.extract_package_fields([front, back], ["mrp"], None, None)
    assert seen["url"] == DEFAULT_BASE_URL + "/chat/completions"
    lowered = {k.lower(): v for k, v in seen["headers"].items()}
    assert lowered.get("authorization") == "Bearer k"
    assert lowered.get("content-type") == "application/json"
    parts = seen["body"]["messages"][0]["content"]
    images = [p for p in parts if p.get("type") == "image_url"]
    assert len(images) == 2
    for img in images:
        assert img["image_url"]["url"].startswith("data:image/jpeg;base64,")
    assert seen["body"]["model"] == "qwen/qwen3.8-27b"
    assert seen["body"]["response_format"] == {"type": "json_object"}


# ------------------------------------------------- parsing ---
def test_groq_headers_carry_key_and_neutral_ua():
    from app.services.vision.groq_provider import GroqVisionProvider

    headers = GroqVisionProvider(api_key="k")._headers()
    assert headers["Authorization"] == "Bearer k"
    assert "Python-urllib" not in headers.get("User-Agent", "")
    assert "GROQ_API_KEY" not in str(headers)


def test_json_object_parses_to_candidates():
    prov = GroqVisionProvider(api_key="k")
    payload = {"choices": [{"message": {"content": _json.dumps({
        "fields": {
            "mrp": {"value": "460", "status": "VISIBLE",
                    "confidence": 94,
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
                              "source_image": "front", "source": "AI"},
    })}}]}
    out = prov._parse(payload, ["mrp", "batch_number"])
    by_field = {c["field"]: c for c in out}
    assert by_field["mrp"]["value"] == "460"
    assert by_field["mrp"]["status"] == "DETECTED"
    assert by_field["mrp"]["image_id"] == "back"
    assert by_field["mrp"]["modality"] == "AI_HANDWRITTEN"
    assert by_field["batch_lot"]["status"] == "NOT_DETECTED"
    assert by_field["veg_nonveg"]["value"] == "VEGETARIAN"


def test_malformed_response_never_crashes():
    prov = GroqVisionProvider(api_key="k")
    out = prov._parse({"choices": [{"message": {"content": "nope {"}}]},
                      ["mrp"])
    assert len(out) == 1 and out[0]["status"] == "NEEDS_REVIEW"


# ------------------------------------------------- errors ---
def test_http_errors_fallback_cleanly_no_retry():
    from app.services.vision.base import VisionError

    from app.services.package_intelligence import vision_stage as vs

    class _Failing(GroqVisionProvider):
        def __init__(self, code):
            super().__init__(api_key="k")
            self.code = code
            self.calls = 0

        def extract_package_fields(self, image, fields,
                                   ocr_candidates=None,
                                   layout_context=None):
            self.calls += 1
            raise VisionError(f"groq HTTP {self.code}: busy")

    def _ocr():
        return {"status": "OK", "engine": "x", "fields_detailed": {},
                "timings": {"total_ms": 1.0},
                "veg_nonveg_symbol": {}, "images_analyzed": 1}

    for code in (401, 429, 503):
        prov = _Failing(code)
        out = vs.enhance_ocr_with_vision(
            _ocr(), [(_png_bytes(), "front")], None,
            inspection_id=f"groq-halt-{code}", provider=prov)
        vision = out.get("vision") or {}
        assert vision.get("vision_status") == "unavailable", code
        assert str(code) in str(vision.get("vision_error") or ""), code
        assert prov.calls == 1, code  # halted: exactly one attempt
        assert out.get("status") == "OK", code


# ------------------------------------------------- integrity ---
def test_frontend_unchanged_by_groq_switch():
    import pathlib
    import subprocess

    root = pathlib.Path(__file__).resolve().parent.parent.parent
    for rel in ("artifacts/nutricheck/src/pages/officer-scan.tsx",
                "artifacts/nutricheck/src/lib/api.ts"):
        diff = subprocess.run(
            ["git", "status", "--porcelain", rel],
            capture_output=True, text=True, cwd=str(root))
        assert diff.stdout.strip() == "", (rel, diff.stdout)


def test_engine_unchanged_by_groq_switch():
    import pathlib
    import re

    engine_dir = pathlib.Path(__file__).resolve().parent.parent \
        / "app" / "engine"
    pattern = re.compile(r"\bgroq\b", re.IGNORECASE)
    for path in sorted(engine_dir.glob("*.py")):
        assert not pattern.search(
            path.read_text(encoding="utf-8")), path.name
    # Stronger: the engine tree has no uncommitted modifications.
    import subprocess

    root = pathlib.Path(__file__).resolve().parent.parent.parent
    diff = subprocess.run(
        ["git", "status", "--porcelain", "backend/app/engine"],
        capture_output=True, text=True, cwd=str(root))
    assert diff.stdout.strip() == "", diff.stdout


def test_no_key_in_frontend_or_logs():
    import pathlib

    root = pathlib.Path(__file__).resolve().parent.parent.parent
    src = root / "artifacts" / "nutricheck" / "src"
    blob = "\n".join(
        p.read_text(encoding="utf-8", errors="replace")
        for p in src.rglob("*.ts*"))
    assert "GROQ_API_KEY" not in blob
    assert "gsk_" not in blob
    from app.services.vision.service import _safe_error_text

    assert _safe_error_text(
        Exception("Bearer gsk-test")) == "[auth material redacted]"
