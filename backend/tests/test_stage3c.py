"""Stage 3C tests: field-specific OCR accuracy + speed (18 items).

Deterministic fixtures only (scripted providers, synthetic PNGs,
golden-loader unit checks). No real package photographs exist in this
repository, so nothing here measures real-world accuracy — each test
pins one specified behavior.
"""
from __future__ import annotations

import io

from app.services.ocr.base import OcrLine, OcrOutput


def _line(text, conf=0.9, image="back", box=None):
    return OcrLine(text=text, confidence=conf, box=box, image=image)


def _box(x0, y0, x1, y1):
    return [[x0, y0], [x1, y0], [x1, y1], [x0, y1]]


def _slot_png(slot=None, w=900, h=300):
    from PIL import Image, ImageDraw

    img = Image.new("RGB", (w, h), "white")
    if slot is not None:
        col, row = slot % 3, (slot // 3) % 2
        x, y = 60 + col * 280, 50 + row * 120
        d = ImageDraw.Draw(img)
        d.rectangle([x, y, x + 180, y + 70], fill="black")
        d.text((x + 10, y + 20), f"P{slot}", fill="white")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


class _ScriptedProvider:
    name = "scripted"

    def __init__(self, by_label):
        self._by_label = by_label
        self.calls = []

    def extract(self, image_np, image_side):
        self.calls.append(str(image_side))
        label = str(image_side).split(":")[0]
        rows = self._by_label.get(label, [])
        out = []
        for row in rows:
            if len(row) == 2:
                t, c = row
                b = None
            else:
                t, c, b = row
            out.append(OcrLine(text=t, confidence=c, box=b, image=label))
        return OcrOutput(lines=out, engine=self.name)


# --- 1. MRP targeted extraction (single region, single pass) ---
def test_01_mrp_targeted_extraction():
    from PIL import Image

    from app.services.ocr import field_ocr as fo

    img = Image.new("RGB", (400, 200), "white")
    prov = _ScriptedProvider({"back": [("MRP Rs. 108", 0.9)]})
    res = fo.extract_field_from_region(
        img, {"rect": (10.0, 10.0, 200.0, 100.0), "scale": (1.0, 1.0)},
        "mrp", prov, image_id="back")
    assert res["text"] == "MRP Rs. 108"
    assert res["confidence"] == 0.9
    assert res["preprocessing"]["variant"] == "smallprint-single"
    assert res["preprocessing"]["crop_rect"] is None  # caller-owned rect
    assert res["error"] is None
    assert len(prov.calls) == 1  # minimum required OCR


def test_01b_field_region_degenerate_falls_back():
    from PIL import Image

    from app.services.ocr import field_ocr as fo

    img = Image.new("RGB", (400, 200), "white")
    prov = _ScriptedProvider({})
    res = fo.extract_field_from_region(img, {}, "mrp", prov)
    assert res["lines"] == [] and res["error"] == "no region rect"
    assert prov.calls == []


# --- 2. MRP false positives ---
def test_02_mrp_false_positives():
    from app.services.ocr.fields import extract_fields

    for bad in ("Energy 450 kcal", "Serving size 30 g", "MFD 05/2024",
                "18001031947", "8901030870164",
                "FSSAI Lic No. 10012043001234"):
        out = extract_fields([_line(bad)])
        assert out["mrp"]["value"] is None, bad


# --- 3. date extraction ---
def test_03_date_extraction_formats():
    from app.services.ocr.fields import extract_fields

    for text, expected in (("MFD 05/24", "05/24"),
                           ("MFG 05/2024", "05/2024"),
                           ("Manufactured on 04/06/2024", "04/06/2024"),
                           ("PKD 12-2023", "12-2023"),
                           ("June 2024", None)):
        out = extract_fields([_line(text)])
        assert out["manufacturing_date"]["value"] == expected, text
    out = extract_fields([_line("70.16")])
    assert out["manufacturing_date"]["value"] is None


# --- 4. FSSAI line wrapping ---
def test_04_fssai_split_digits_across_lines():
    from app.services.ocr.fields import extract_fields_detailed

    lines = [_line("FSSAI Lic No."), _line("10015043"),
             _line("001129")]
    out = extract_fields_detailed(lines)
    assert out["fssai_license"]["value"] == "10015043001129"


# --- 5. batch extraction ---
def test_05_batch_split_and_stops():
    from app.services.ocr.fields import extract_fields_detailed

    out = extract_fields_detailed([_line("Lot No."), _line("LOT99")])
    assert out["batch_lot"]["value"] == "LOT99"
    out = extract_fields_detailed([_line("Batch No"),
                                   _line("MRP Rs. 50")])
    assert out["batch_lot"]["value"] is None  # declaration stops pairing


# --- 6. manufacturer multi-line ---
def test_06_manufacturer_no_invention():
    from app.services.ocr.fields import extract_with_status

    out = extract_with_status([
        _line("Mfd. by NESTLE"), _line("INDIA LIMITED")])
    assert "INDIA LIMITED" in (out["manufacturer"]["value"] or "")
    out = extract_with_status([
        _line("Mfd. by NESTLE"), _line("Plot 12, Moga 142001")])
    # digit-heavy address line is not joined into the company name
    assert "142001" not in (out["manufacturer"]["value"] or "")


# --- 7. consumer care ---
def test_07_consumer_care_fssai_guard_and_normalize():
    from app.services.ocr.fields import extract_fields_detailed

    out = extract_fields_detailed(
        [_line("FSSAI Lic No. 18001031947012")])
    assert out["consumer_care"]["value"] is None
    out = extract_fields_detailed([_line("Toll Free 1800-103-1947")])
    assert out["consumer_care"]["value"] == "1800-103-1947"
    assert out["consumer_care"].get("normalized_value") == "18001031947"


# --- 8. ingredient region detection ---
def test_08_ingredient_field_regions():
    from app.services.ocr import service as svc

    prov = _ScriptedProvider({
        "back": [("INGREDIENTS: Wheat Flour, Sugar", 0.9,
                  _box(60, 60, 500, 95)),
                 ("MRP Rs. 50", 0.9, _box(60, 400, 300, 435))]})
    out = svc.extract_label_multi([(_slot_png(0), "back")], provider=prov)
    regions = out["diagnostics"]["images"]["back"]["field_regions"]
    assert "ingredients" in regions and "mrp" in regions
    ing = regions["ingredients"][0]
    assert ing["region"] == "INGREDIENTS"
    assert ing["anchor"].startswith("INGREDIENTS")
    assert ing["image_id"] == "back"
    assert ing["bbox"] and ing["confidence"]


# --- 9. ingredient stop boundaries ---
def test_09_ingredient_stop_boundaries():
    from app.services.ocr.food import ingredient_boundary_decision as decide

    for stop in ("Typical Values per 100g", "Know your portion: 30 g",
                 "Scan the QR code for more", "Approximate Values"):
        is_b, _section, _why = decide(stop)
        assert is_b is True, stop


# --- 10. nutrition contamination ---
def test_10_nutrition_contamination_markers():
    from app.services.package_intelligence.validators import (
        validate_ingredient_candidate,
    )

    ok, _reason = validate_ingredient_candidate(
        "INGREDIENTS Wheat Flour Energy 450 kcal per 100g",
        "INGREDIENTS Wheat Flour")
    assert ok is False


# --- 11. veg symbol crop ---
def test_11_veg_symbol_crop_classification():
    from PIL import Image, ImageDraw

    from app.services.ocr import veg_symbol as veg

    img = Image.new("RGB", (300, 300), "white")
    d = ImageDraw.Draw(img)
    d.rectangle([120, 120, 150, 150], outline=(0, 128, 0), width=3)
    d.ellipse([127, 127, 143, 143], fill=(0, 128, 0))
    out = veg.classify_symbol_crop(img, [0.3, 0.3, 0.6, 0.6])
    assert out["classification"] in ("VEGETARIAN", "UNKNOWN")
    assert out["status"] in ("DETECTED", "NEEDS_REVIEW")
    blank = veg.classify_symbol_crop(
        Image.new("RGB", (300, 300), "white"), [0.3, 0.3, 0.6, 0.6])
    assert blank["classification"] == "UNKNOWN"


# --- 12. duplicate images ---
def test_12_duplicates_reuse_without_text_duplication():
    from app.services.ocr import service as svc

    raw = _slot_png(0)
    out = svc.extract_label_multi(
        [(raw, "back"), (raw, "side")],
        provider=_ScriptedProvider({
            "back": [("MRP Rs. 50", 0.9)],
            "side": [("MRP Rs. 50", 0.9)]}))
    full = [c for c in out["timings"]["provider_calls_detail"]
            if c.get("stage") == "full-page"]
    assert len(full) == 1
    by_label = {r["image"]: r for r in out["images"]}
    assert by_label["side"]["ocr_reused_from"] == "back"
    assert out["timings"]["duplicates_reused"] == 1
    # pooled text carries the declaration once, both images cite it
    assert out["fields"]["mrp"]["value"] == "50"


# --- 13. fallback behavior ---
def test_13_legacy_mode_skips_targeted_stages():
    from app.services.ocr import service as svc

    prov = _ScriptedProvider({
        "back": [("MRP Rs. 50", 0.4),
                 ("MFD 05/2024", 0.4)]})
    out = svc.extract_label_multi([(_slot_png(0), "back")],
                                  provider=prov, targeted_stages=False)
    kinds = [e.get("stage")
             for e in out["timings"]["provider_calls_detail"]]
    assert kinds == ["full-page"]
    assert out["timings"]["call_budget"]["budget_respected"] is True
    full = svc.extract_label_multi([(_slot_png(1), "back")],
                                   provider=_ScriptedProvider({
                                       "back": [("MRP Rs. 50", 0.4),
                                                ("MFD 05/2024", 0.4)]}))
    assert len(full["timings"]["provider_calls_detail"]) >= len(kinds)


# --- 14. Gemini unresolved-field path ---
def test_14_gemini_only_when_wanted():
    from app.services.package_intelligence import vision_stage as vs
    from app.services.vision.mock_provider import MockVisionProvider

    prov = MockVisionProvider()
    resolved = {"fields_detailed": {
        "mrp": {"value": "50", "confidence": 0.95, "status": "DETECTED",
                "image": "back", "box": None}}}
    out = vs.enhance_ocr_with_vision(
        resolved, [(_slot_png(0), "back")], None,
        inspection_id="stage3c-test14", provider=prov)
    assert "mrp" not in (out.get("vision") or {}).get(
        "fields_requested", [])
    assert out["vision"]["vision_calls"] <= 6


# --- 15. golden dataset loading ---
def _golden_mod():
    import importlib.util
    from pathlib import Path

    path = Path(__file__).parent / "golden" / "evaluation.py"
    spec = importlib.util.spec_from_file_location(
        "golden_evaluation", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_15_golden_loader_skips_unverified():
    mod = _golden_mod()
    products = mod.load_products()
    assert products == []  # no verified products committed
    from pathlib import Path

    template = Path(__file__).parent / "golden" / "products" / "_template"
    assert (template / "expected.json").exists()


# --- 16. field-level evaluation ---
def test_16_field_metrics():
    mod = _golden_mod()
    score_field = mod.score_field
    evaluate_product = mod.evaluate_product

    assert score_field("mrp", "108", "108")["exact"] is True
    assert score_field("mrp", "MRP 108", "mrp 108")["normalized"] is True
    assert score_field("mrp", "108", "108.00")["value_match"] is True
    assert score_field("manufacturing_date", "05/2024",
                       "05/2024")["value_match"] is True
    assert score_field("mrp", None, "108")["scored"] is False
    summary = evaluate_product(
        {"mrp": "108", "quantity": None},
        {"mrp": {"value": "108", "status": "DETECTED"},
         "quantity": {"value": None, "status": "NOT_DETECTED"}})["summary"]
    assert summary["n_scored"] == 1
    assert summary["exact_matches"] == 1
    assert summary["needs_review_rate"] == 0.0


# --- 17. call-budget compliance ---
def test_17_six_image_budget():
    from app.services.ocr import service as svc

    out = svc.extract_label_multi(
        [(_slot_png(i), f"img_{i}") for i in range(6)],
        provider=_ScriptedProvider({}))
    assert out["timings"]["call_budget"]["budget_respected"] is True
    full = [c for c in out["timings"]["provider_calls_detail"]
            if c.get("stage") == "full-page"]
    assert len(full) == 6
    per_image = {}
    for e in out["timings"]["provider_calls_detail"]:
        per_image[e.get("image_id")] = per_image.get(
            e.get("image_id"), 0) + 1
    assert all(n <= svc.MAX_PROVIDER_CALLS_PER_IMAGE
               for n in per_image.values())


# --- 18. timing instrumentation ---
def test_18_timing_keys_present():
    from app.services.ocr import service as svc

    out = svc.extract_label_multi(
        [(_slot_png(0), "back")], provider=_ScriptedProvider({}))
    timings = out["timings"]
    for key in ("decode_ms", "normalization_ms", "role_detection_ms",
                "duplicates_reused"):
        assert key in timings or key in timings.get("stage_timings",
                                                    {}), key
    det = out["fields_detailed"]["mrp"]
    assert det["provider"] == "scripted"
    assert det["preprocessing_variant"]
