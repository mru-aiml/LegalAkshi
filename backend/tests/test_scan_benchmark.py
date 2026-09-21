"""Real-engine Scan & Inspect benchmark (Part X).

Two synthetic but realistic package panels (back with ingredients +
declarations, nutrition table) through the full staged pipeline with
the real RapidOCR engine. Asserts the call-budget contract and honest
field outcomes; per-stage timings ride along in ``timings`` for the
release report (no wall-clock assertions — CI machines vary).

The four-panel before/after comparison (OpenCV on vs legacy path)
lives in backend/scripts/benchmark_scan.py; its numbers feed the
final report, not CI gates.
"""
from __future__ import annotations

import io

import pytest

from app.services.ocr import service as svc

pytest.importorskip("rapidocr_onnxruntime")


def _panel(lines, w=1280, h=900, tilt=0.0):
    from PIL import Image, ImageDraw, ImageFont

    img = Image.new("RGB", (w, h), "white")
    d = ImageDraw.Draw(img)
    try:
        font = ImageFont.load_default(size=30)
    except Exception:
        font = ImageFont.load_default()
    y = 60
    for ln in lines:
        d.text((60, y), ln, fill="black", font=font)
        y += 48
    if tilt:
        img = img.rotate(tilt, expand=True, fillcolor="white")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


BACK = ["INGREDIENTS: Refined Wheat Flour (Maida) 62%, Sugar, Palm Oil,",
        "Salt, Cocoa Solids 4%, Raising Agents INS 500(ii).",
        "TO A CLEAN AIRTIGHT CONTAINER ONCE OPENED.",
        "MRP Rs. 50 (Incl. of all taxes)   MFD 05/2024",
        "FSSAI Lic No. 10012043001234",
        "Mfd. by Demo Foods Ltd., Moga, Punjab 142001",
        "Customer Care 1800-103-1947  customercare@demo.in"]
NUTRITION = ["Nutrition Information per 100g",
             "Energy 450 kcal   Protein 8 g",
             "Carbohydrate 60 g   Total Sugars 25 g",
             "Total Fat 15 g   Saturated Fat 7 g   Sodium 300 mg"]


def _run():
    from app.services.ocr.rapidocr_provider import RapidOCRProvider

    RapidOCRProvider._engine = None
    return svc.extract_label_multi(
        [(_panel(BACK, tilt=2.0), "back"), (_panel(NUTRITION), "nutrition")],
        provider=RapidOCRProvider())


def test_benchmark_call_budget():
    out = _run()
    t = out["timings"]
    assert t["provider_calls"] <= 2 * svc.MAX_PROVIDER_CALLS_PER_IMAGE
    assert t["tesseract_calls"] <= 2 * svc.MAX_TESSERACT_CALLS_PER_IMAGE
    full = [e for e in t["provider_calls_detail"]
            if e.get("stage") == "full-page"]
    assert len(full) == 2  # exactly one Stage-1 pass per image
    assert t["call_budget"]["budget_respected"] is True


def test_benchmark_field_outcomes():
    out = _run()
    det = out["fields_detailed"]
    # Core declarations read off the synthetic back panel.
    assert det["quantity"]["value"] is None or det["quantity"]["status"] in (
        "DETECTED", "NEEDS_REVIEW")
    assert det["mrp"]["value"] == "50"
    assert det["manufacturing_date"]["value"] == "05/2024"
    assert det["fssai_license"]["value"] == "10012043001234"
    assert det["consumer_care"]["value"] == "1800-103-1947"
    # Storage / care text must not contaminate the cleaned declaration.
    cleaned = ((out["food"]["fields"]["ingredients"].get("cleaned_text")
                or "").upper())
    for banned in ("AIRTIGHT", "ONCE OPENED", "1800-103", "@DEMO"):
        assert banned not in cleaned
    # Uncertainty is honest: DETECTED or NEEDS_REVIEW, never invented.
    assert out["food"]["fields"]["ingredients"]["detection"] in (
        "DETECTED", "NEEDS_REVIEW")


def test_benchmark_nutrition_region():
    out = _run()
    nutri = out["food"]["fields"]["nutrition"]
    found = {k: v.get("value") for k, v in nutri.items() if v.get("value")}
    assert found.get("energy") == "450 kcal"
    assert found.get("protein") == "8 g"
    # Per-image quality diagnostics present for the officer UI.
    for label, im in out["timings"]["images"].items():
        assert "image_quality" in im, label
        assert im["image_quality"].get("readability") in (
            "GOOD", "FAIR", "POOR")
