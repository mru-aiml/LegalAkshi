"""OpenCV preprocessing layer: quality, orientation, deskew, quad,
table detection, variant ordering. Pure pixels in, evidence out — this
module never calls an OCR engine (asserted structurally below).
"""
from __future__ import annotations

import io

import pytest

from app.services.ocr import opencv_preprocessor as ocv

pytestmark = pytest.mark.skipif(not ocv.cv2_available(),
                                reason="cv2 not installed")


def _panel(w=900, h=600, font_size=26, blur=0.0, brightness=1.0,
           dense=False):
    from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont

    img = Image.new("RGB", (w, h), "white")
    d = ImageDraw.Draw(img)
    try:
        font = ImageFont.load_default(size=font_size)
    except Exception:
        font = ImageFont.load_default()
    lines = ["INGREDIENTS: Refined Wheat Flour (Maida) 68%, Palm Oil,",
             "Sugar, Salt, Mixed Spices, INS 500(ii).",
             "MRP Rs. 50 (Incl. of all taxes)   MFD 05/2024",
             "FSSAI Lic No. 10012043001234   NET WT 70 g"]
    if dense:
        lines = ["INGREDIENTS: Refined Wheat Flour (Maida) 68%, Palm Oil, Sugar, Salt,",
                 "Mixed Spices 0.5%, Acidity Regulator INS 500(ii), Humectant E451.",
                 "MRP Rs. 50 (Incl. of all taxes)      MFD 05/2024     NET WT 70 g",
                 "FSSAI Lic No. 10012043001234   Mfd by Demo Foods, Moga 142001",
                 "Customer Care 1800-103-1947    Best Before 9 months from mfg"]
    y = 50
    step = 40 if not dense else 44
    for ln in lines * (6 if not dense else 8):
        d.text((50, y), ln, fill="black", font=font)
        y += step
        if y > h - 50:
            break
    if blur:
        img = img.filter(ImageFilter.GaussianBlur(blur))
    if brightness != 1.0:
        img = ImageEnhance.Brightness(img).enhance(brightness)
    return img


def _np(img):
    import numpy as np

    return np.asarray(img)


# ------------------------------------------------- module hygiene ---
def test_never_calls_ocr_engines():
    import ast
    import inspect

    tree = ast.parse(inspect.getsource(ocv))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                assert a.name.split(".")[0] not in (
                    "rapidocr_onnxruntime", "pytesseract"), a.name
        elif isinstance(node, ast.ImportFrom) and node.module:
            mod = node.module
            assert not mod.startswith(
                ("rapidocr_onnxruntime", "pytesseract")), mod
            if mod.startswith("app."):
                # Only lazy, function-local imports of shared prep/extraction
                # helpers — never providers or engines.
                assert mod in ("app.services.ocr",
                               "app.services.ocr.base",
                               "app.services.ocr.service"), mod


def test_graceful_without_cv2(monkeypatch):
    import app.services.ocr.opencv_preprocessor as mod

    monkeypatch.setattr(mod, "_cv2", lambda: None)
    monkeypatch.setattr(mod, "cv2_available", lambda: False)
    q = mod.analyze_quality(_np(_panel()))
    assert q["readability"] in ("GOOD", "FAIR", "POOR")
    assert mod.estimate_orientation(_np(_panel()))["decisive"] is False
    gray = _np(_panel().convert("L"))
    _, applied, _, _ = mod.deskew_image(gray)
    assert applied is False
    assert mod.find_quad(_np(_panel()))["found"] is False
    assert mod.find_table_rect(_np(_panel()))["found"] is False
    assert mod.choose_variant("ingredients", None) == ["orig", "up"]
    # prepare_variant falls back to the legacy PIL path without cv2.
    arr = mod.prepare_variant(_panel().crop((0, 0, 400, 200)), "adapt")
    assert arr is not None and arr.shape[2] == 3


# ------------------------------------------------- quality (Part D) ---
def test_quality_sharp_panel_readable():
    q = ocv.analyze_quality(_np(_panel(w=1280, h=800)))
    assert q["width"] == 1280 and q["height"] == 800
    assert q["aspect"] == pytest.approx(1.6)
    assert q["sharpness"]["grade"] == "GOOD"
    assert q["readability"] == "GOOD"


def test_quality_blur_degrades_sharpness_only():
    q = ocv.analyze_quality(_np(_panel(blur=3.0)))
    assert q["sharpness"]["grade"] == "POOR"
    assert q["readability"] == "POOR"
    assert any("blur" in n for n in q["notes"])


def test_quality_never_rejects():
    # Even garbage input yields grades, never an exception.
    q = ocv.analyze_quality(None)
    assert q["readability"] == "POOR"
    assert q["notes"]


# --------------------------------------------- orientation (Part E) ---
@pytest.mark.parametrize("angle,transposed", [
    (0, False), (90, True), (180, False), (270, True)])
def test_orientation_four_rotations(angle, transposed):
    import numpy as np

    base = _panel(w=1280, h=800, font_size=30, dense=True)
    img = base.rotate(angle, expand=True) if angle else base
    out = ocv.estimate_orientation(np.asarray(img))
    assert out["transpose"] is transposed
    assert out["decisive"] is True


# ------------------------------------------------- deskew (Part F) ---
def test_deskew_straight_image_untouched():
    gray = _np(_panel().convert("L"))
    out, applied, angle, _ = ocv.deskew_image(gray)
    assert applied is False
    assert abs(angle) < 0.5
    assert out.shape == gray.shape


def test_deskew_tilted_image_corrected():
    tilted = _panel().rotate(4, expand=True, fillcolor="white")
    gray = _np(tilted.convert("L"))
    out, applied, angle, _ = ocv.deskew_image(gray)
    assert applied is True
    assert 2.0 <= abs(angle) <= 6.0
    assert out.shape == gray.shape  # no resampling of dimensions


def test_deskew_wild_tilt_refused():
    tilted = _panel().rotate(30, expand=True, fillcolor="white")
    _, applied, _, notes = ocv.deskew_image(
        _np(tilted.convert("L")))
    assert applied is False
    assert any("cap" in n for n in notes)


# --------------------------------------- perspective fallback (G) ---
def test_quad_plain_panel_not_found():
    out = ocv.find_quad(_np(_panel()))
    assert out["found"] is False
    assert out["quad"] is None


def test_rectify_crop_plain_unchanged():
    crop = _panel().crop((0, 0, 500, 300))
    out, corrected, info = ocv.rectify_crop(crop)
    assert corrected is False
    assert info["perspective_corrected"] is False
    assert out.size == crop.size


def test_rectify_crop_tilted_quad():
    # Simulate an angled capture: a tilted white text panel on a dark
    # background; rectify_crop must straighten the inner quad.
    import cv2
    import numpy as np

    from PIL import Image as _Image
    from PIL import ImageDraw, ImageFont

    canvas = np.full((500, 700, 3), 40, dtype=np.uint8)
    quad = np.array([[90, 60], [610, 30], [640, 430], [60, 460]])
    cv2.fillPoly(canvas, [quad], (255, 255, 255))
    pil = _Image.fromarray(canvas)
    d = ImageDraw.Draw(pil)
    try:
        font = ImageFont.load_default(size=28)
    except Exception:
        font = ImageFont.load_default()
    d.text((150, 150), "INGREDIENTS: Wheat Flour,", fill="black",
           font=font)
    d.text((150, 190), "Sugar, Salt, INS 500.", fill="black", font=font)
    out, corrected, _ = ocv.rectify_crop(pil)
    assert corrected is True
    assert out.size[0] > 100 and out.size[1] > 100


# --------------------------------------------- variants (Part H) ---
def test_variant_order_adapts_to_quality():
    assert ocv.choose_variant("ingredients", None)[0] == "orig"
    blurry = {"sharpness": {"grade": "POOR"}}
    assert ocv.choose_variant("ingredients", blurry)[0] == "up"
    dark = {"sharpness": {"grade": "GOOD"},
            "brightness": {"grade": "POOR"}}
    assert ocv.choose_variant("ingredients", dark)[0] == "adapt"
    # Budget-relevant: always a permutation of the known set.
    for order in (ocv.choose_variant("ingredients", blurry),
                  ocv.choose_variant("ingredients", dark),
                  ocv.choose_variant("smallprint", None)):
        assert set(order) <= {"orig", "up", "adapt", "smallprint"}
        assert len(order) == len(set(order))


def test_adapt_variant_deterministic_and_safe():
    crop = _panel().crop((0, 0, 500, 250))
    first = ocv.prepare_variant(crop, "adapt")
    second = ocv.prepare_variant(crop, "adapt")
    import numpy as np

    assert (np.asarray(first) == np.asarray(second)).all()
    # Legacy variants byte-identical via delegation.
    assert (np.asarray(ocv.prepare_variant(crop, "orig"))
            == np.asarray(crop.convert("RGB"))).all()
    with pytest.raises(Exception):
        ocv.prepare_variant(crop, "nope")


def test_table_rect_needs_numeric_support():
    # Plain text panel: no table structure -> not found, never invented.
    out = ocv.find_table_rect(_np(_panel()))
    assert out["found"] is False
