"""Scan & Inspect hardening: 8-image support, OCR pipeline config,
OpenCV availability/preprocessing, and the visual veg/non-veg symbol
detector (HSV + square+circle geometry; amber packs must not become
NON_VEGETARIAN).
"""
from __future__ import annotations

import io

import pytest
from fastapi.testclient import TestClient

from app.services.ocr.base import OcrLine, OcrOutput, TooManyImagesError

OFFICER = {"X-LegalAkshi-Role": "officer", "X-LegalAkshi-User": "INSP-8"}


def _png_bytes(label: str = "img", size=(640, 480)) -> bytes:
    from PIL import Image, ImageDraw

    img = Image.new("RGB", size, "white")
    d = ImageDraw.Draw(img)
    d.text((20, 20), f"Package {label} MRP Rs. 108", fill="black")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


class _MockProvider:
    name = "mock-ocr"

    def __init__(self, lines=None):
        self._lines = lines or [("MRP Rs. 108", 0.9)]

    def extract(self, image_np, image_side):
        return OcrOutput(
            lines=[OcrLine(text=t, confidence=c, image=image_side)
                   for t, c in self._lines],
            engine=self.name)


def _symbol_png(color, bg="white") -> bytes:
    from PIL import Image, ImageDraw

    img = Image.new("RGB", (400, 400), bg)
    d = ImageDraw.Draw(img)
    d.rectangle([180, 180, 220, 220], outline=color, width=4)
    d.ellipse([188, 188, 212, 212], fill=color)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


# ------------------------------------------------- OpenCV available ---
def test_cv2_importable_in_backend_env():
    import cv2

    assert cv2.__version__
    from app.services.ocr import opencv_preprocessor as ocv

    assert ocv.cv2_available() is True


def test_opencv_preprocessing_downscales_and_grades():
    import numpy as np

    from app.services.ocr import opencv_preprocessor as ocv
    from app.services.ocr import service as svc

    raw = _png_bytes("big", size=(3000, 2000))
    working = svc.preprocess(raw)
    assert max(working.shape[0], working.shape[1]) <= svc.STAGE1_MAX_DIM
    quality = ocv.analyze_quality(np.asarray(working))
    assert quality["cv2"] is True
    for key in ("sharpness", "brightness", "contrast", "resolution",
                "readability"):
        assert key in quality, key
    orientation = ocv.estimate_orientation(np.asarray(working))
    assert isinstance(orientation, dict)


# ------------------------------------------------- 8-image support ---
def test_eight_images_accepted_by_service():
    from app.services.ocr import service as svc

    images = [(_png_bytes(f"img_{i}"), f"img_{i}") for i in range(8)]
    out = svc.extract_label_multi(images, provider=_MockProvider())
    assert out["images_analyzed"] == 8
    assert len(out["images"]) == 8
    assert out["fields"]["mrp"]["value"] == "108"


def test_nine_images_rejected_by_service():
    from app.services.ocr import service as svc

    images = [(_png_bytes(f"img_{i}"), f"img_{i}") for i in range(9)]
    with pytest.raises(TooManyImagesError) as exc:
        svc.extract_label_multi(images, provider=_MockProvider())
    assert "8" in str(exc.value)


def test_nine_images_rejected_by_route():
    from conftest import make_client, make_repo

    client: TestClient = make_client(make_repo())
    files = [( "images",
               (f"img_{i}.png", _png_bytes(f"img_{i}"),
                "image/png")) for i in range(9)]
    r = client.post("/api/v1/ocr/extract", files=files, headers=OFFICER)
    assert r.status_code == 422
    assert "8" in r.json()["detail"]


def test_route_accepts_eight_images_with_patched_pipeline(monkeypatch):
    from conftest import make_client, make_repo

    from app.services.ocr import service as ocr_service

    def _fake_multi(images, provider=None, tess_provider=None,
                    targeted_stages=True):
        supplied = [raw for raw, _ in images or [] if raw]
        assert len(supplied) == 8
        return {"status": "OK", "engine": "mock-ocr", "images": [],
                "images_analyzed": 8, "fields": {}, "fields_detailed": {},
                "food": {}, "veg_nonveg_symbol": {}, "contact": {},
                "errors": [], "timings": {}, "rejected_lines": [],
                "diagnostics": {}}

    monkeypatch.setattr(ocr_service, "extract_label_multi", _fake_multi)
    client: TestClient = make_client(make_repo())
    files = [("images",
              (f"img_{i}.png", _png_bytes(f"img_{i}"),
               "image/png")) for i in range(8)]
    r = client.post("/api/v1/ocr/extract", files=files, headers=OFFICER)
    assert r.status_code == 200
    assert r.json()["images_analyzed"] == 8


def test_image_cap_follows_configuration(monkeypatch):
    from app.core import config as config_mod
    from app.services.ocr import service as svc

    monkeypatch.setenv("OCR_MAX_IMAGES", "2")
    config_mod.get_settings.cache_clear()
    try:
        assert svc._max_images() == 2
        images = [(_png_bytes(f"img_{i}"), f"img_{i}") for i in range(3)]
        with pytest.raises(TooManyImagesError):
            svc.extract_label_multi(images, provider=_MockProvider())
    finally:
        config_mod.get_settings.cache_clear()


def test_ocr_pipeline_config_defaults():
    from app.core.config import get_settings

    settings = get_settings()
    assert settings.OCR_MAX_IMAGES == 8
    assert settings.OCR_TIMEOUT_SECONDS == 420.0


# ------------------------------------------------- symbol detector ---
def test_vegetarian_symbol_detected():
    from app.services.ocr.veg_symbol import detect_veg_symbol

    out = detect_veg_symbol([_symbol_png((0, 160, 0))])
    assert out["status"] == "DETECTED"
    assert out["classification"] == "VEGETARIAN"
    assert (out["confidence"] or 0) >= 0.55
    assert out["provenance"] == "IMAGE"


def test_non_vegetarian_symbol_detected():
    from app.services.ocr.veg_symbol import detect_veg_symbol

    out = detect_veg_symbol([_symbol_png((165, 42, 42))])
    assert out["status"] == "DETECTED"
    assert out["classification"] == "NON_VEGETARIAN"
    assert (out["confidence"] or 0) >= 0.55


def test_amber_oil_like_mark_is_not_non_vegetarian():
    """Sunflower-oil regression: amber/yellow hues are neither verdict."""
    from app.services.ocr.veg_symbol import detect_veg_symbol

    for color in ((220, 150, 30), (240, 210, 50), (200, 170, 60)):
        out = detect_veg_symbol([_symbol_png(color)])
        assert out["classification"] in ("UNKNOWN", "VEGETARIAN"), color
        assert out["classification"] != "NON_VEGETARIAN", color


def test_unknown_symbol_never_reads_as_non_vegetarian():
    from PIL import Image

    from app.services.ocr.veg_symbol import detect_veg_symbol

    buf = io.BytesIO()
    Image.new("RGB", (400, 400), "white").save(buf, format="PNG")
    out = detect_veg_symbol([buf.getvalue()])
    assert out["classification"] == "UNKNOWN"
    assert "not a finding" in out["reason"].lower()


def test_symbol_detector_needs_review_on_garbage():
    from app.services.ocr.veg_symbol import detect_veg_symbol

    out = detect_veg_symbol([b"\x00\x01not-an-image"])
    assert out["status"] == "NEEDS_REVIEW"
    assert out["classification"] == "UNKNOWN"


# ------------------------------------------------- schema compat ---
def test_ocr_response_schema_backward_compatible():
    from app.services.ocr import service as svc

    out = svc.extract_label_multi(
        [(_png_bytes("front"), "front"), (_png_bytes("back"), "back")],
        provider=_MockProvider())
    for key in ("status", "engine", "images", "images_analyzed",
                "fields", "fields_detailed", "food",
                "veg_nonveg_symbol", "contact", "errors", "timings",
                "rejected_lines", "diagnostics", "front", "back"):
        assert key in out, key
    assert set(out["veg_nonveg_symbol"]) >= {
        "status", "classification", "confidence", "provenance"}
