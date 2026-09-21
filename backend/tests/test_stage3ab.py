"""Stage 3A + 3B regression tests: image/region intelligence + field
extraction (30 items).

Deterministic fixtures throughout (OcrLine units, scripted providers,
synthetic PNGs). No real package images exist in this repository, so
nothing here measures real-world accuracy — each test pins one
specified behavior.
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


# ------------------------------------------------------- 3A.1/3A.2 ---
def test_01_rotated_image_normalization():
    from app.services.ocr import service as svc

    vote = {"transpose": True, "decisive": True, "ratio": 2.1, "notes": []}
    working, meta = svc._normalize_working_image(
        svc._decode(_slot_png(0)), vote, {"readability": "FAIR"})
    assert meta["orientation"]["rotation_applied"] == 90
    assert meta["orientation"]["uncertain"] is False
    assert working.size == (300, 900)  # upright working frame
    assert "deskew_applied" in meta
    assert "perspective_corrected" in meta


def test_02_orientation_uncertainty_preserves_behavior():
    from app.services.ocr import service as svc

    original = svc._decode(_slot_png(1))
    vote = {"transpose": False, "decisive": False, "ratio": 1.1,
            "notes": []}
    working, meta = svc._normalize_working_image(
        original, vote, {"readability": "GOOD"})
    assert meta["orientation"]["rotation_applied"] == 0
    assert meta["orientation"]["uncertain"] is True
    assert working.size == original.size
    assert meta["deskew_applied"] is False
    assert meta["perspective_corrected"] is False


# ------------------------------------------------------- 3A.4/3A.5 ---
def test_03_duplicate_images_reuse_ocr():
    from app.services.ocr import service as svc

    raw = _slot_png(2)
    out = svc.extract_label_multi(
        [(raw, "back"), (raw, "side")],
        provider=_ScriptedProvider({
            "back": [("MRP Rs. 50", 0.9)],
            "side": [("MRP Rs. 50", 0.9)]}))
    full = [c for c in out["timings"]["provider_calls_detail"]
            if c.get("stage") == "full-page"]
    assert len(full) == 1  # second identical upload reuses OCR
    by_label = {r["image"]: r for r in out["images"]}
    assert by_label["side"]["duplicate_of"] == "back"
    assert by_label["side"]["ocr_reused_from"] == "back"
    assert by_label["back"].get("duplicate_of") is None
    assert out["timings"]["image_roles"]["side"] == "DUPLICATE"
    # pooled text is not duplicated: one MRP line, not two
    assert out["images_analyzed"] == 2  # both retained as evidence


def test_04_front_back_side_classification():
    from app.services.ocr import image_roles as roles

    assert roles.classify_label("front", []) == "FRONT"
    assert roles.classify_label("back", []) == "BACK"
    side_texts = ["MRF Rs. 45", "MFD 05/2024", "Batch No B12"]
    assert roles.classify_label("image_2", side_texts) == "SIDE"
    back_texts = ["INGREDIENTS: Wheat Flour", "Nutrition Information",
                  "FSSAI Lic No 10012043001234",
                  "Manufactured by Acme", "MRP Rs. 50"]
    assert roles.classify_label("image_9", back_texts) == "BACK"
    assert roles.classify_label("image_7", ["Tasty Noodles"]) in (
        "FRONT", "UNKNOWN")
    assert roles.classify_label("mystery", []) == "UNKNOWN"
    ordered = roles.ordered_labels_for_field(
        "mrp", ["front", "back"], {"front": "FRONT", "back": "BACK"})
    assert ordered[0] == "back"


# ------------------------------------------------------- 3B.1 ---
def test_05_product_name_false_positives():
    from app.services.ocr.fields import extract_fields

    for bad in ("STORE IN A COOL PLACE", "Serving Size 30 g",
                "How to Cook Noodles", "NET WT 70 g",
                "Customer Care 18001031947"):
        out = extract_fields([_line(bad, image="front")])
        assert out["product_name"]["value"] != bad, bad
    out = extract_fields([_line("MAGGI 2-Minute Noodles", 0.95,
                                image="front")])
    assert out["product_name"]["value"] == "MAGGI 2-Minute Noodles"


# ------------------------------------------------------- 3B.2 ---
def test_06_quantity_1g_inside_nutrition_rejected():
    from app.services.ocr.fields import extract_fields

    out = extract_fields([_line("Protein 8 g per 100 g serving")])
    assert out["quantity"]["value"] is None
    out = extract_fields([_line("Energy 1 g sugar")])
    assert out["quantity"]["value"] is None


def test_07_valid_net_wt_70g():
    from app.services.ocr.fields import extract_fields

    out = extract_fields([_line("NET WT 70 g")])
    assert out["quantity"]["value"] == "70"
    assert out["unit"]["value"] == "g"
    out = extract_fields([_line("NET CONTENTS 1 kg")])
    assert out["quantity"]["value"] == "1"


# ------------------------------------------------------- 3B.3 ---
def test_08_mrp_candidate_conflict():
    from app.services.ocr.fields import extract_with_status

    out = extract_with_status([
        _line("MRP Rs. 108", 0.9), _line("MRP Rs. 180", 0.88)])
    # No silent winner: NEEDS_REVIEW with the full candidate pool and
    # evidence retained (the top suggestion stays visible for review).
    assert out["mrp"]["status"] == "NEEDS_REVIEW"
    pool = {c["value"] for c in out["mrp"]["all_candidates"]}
    assert {"108", "180"} <= pool


def test_09_mrp_currency_extraction():
    from app.services.ocr.fields import extract_fields

    out = extract_fields([_line("MRP \u20b9108")])
    assert out["mrp"]["value"] == "108"
    out = extract_fields([_line("Maximum Retail Price Rs 250")])
    assert out["mrp"]["value"] == "250"


# ------------------------------------------------------- 3B.6 ---
def test_10_split_fssai_three_lines():
    from app.services.ocr.fields import extract_fields_detailed

    lines = [_line("FSSAI"), _line("Lic. No."),
             _line("10015043001129")]
    out = extract_fields_detailed(lines)
    assert out["fssai_license"]["value"] == "10015043001129"


def test_11_fssai_vs_barcode_confusion():
    from app.services.ocr.fields import extract_fields_detailed

    lines = [_line("8901030870164",
                   box=_box(10, 10, 200, 40)),
             _line("FSSAI Lic No. 10012043001234",
                   box=_box(10, 100, 300, 130))]
    out = extract_fields_detailed(lines)
    assert out["fssai_license"]["value"] == "10012043001234"


# ------------------------------------------------------- 3B.7 ---
def test_12_split_batch_accepted():
    from app.services.ocr.fields import extract_fields_detailed

    lines = [_line("BATCH"), _line("A12345")]
    out = extract_fields_detailed(lines)
    assert out["batch_lot"]["value"] == "A12345"


# ------------------------------------------------------- 3B.4/3B.5 ---
def test_13_manufacturing_date_with_mfd():
    from app.services.ocr.fields import extract_fields

    out = extract_fields([_line("MFD 05/2024")])
    assert out["manufacturing_date"]["value"] == "05/2024"


def test_14_corrupted_mfd_variant():
    from app.services.ocr.fields import extract_fields

    out = extract_fields([_line("MFO 05/2024")])
    assert out["manufacturing_date"]["value"] == "05/2024"
    out = extract_fields([_line("70.16")])
    assert out["manufacturing_date"]["value"] is None


def test_15_best_before_months_kept_separate():
    from app.services.ocr.fields import extract_fields

    out = extract_fields([_line("Best before 6 months from manufacture")])
    assert out["best_before"]["value"] is not None
    assert "month" in out["best_before"]["value"].lower()
    assert out["manufacturing_date"]["value"] is None


# ------------------------------------------------------- 3B.8 ---
def test_16_manufacturer_multiline_reconstruction():
    from app.services.ocr.fields import extract_with_status

    out = extract_with_status([
        _line("Mfd. by NESTLE"), _line("INDIA LIMITED")])
    maker = out["manufacturer"]
    assert maker["value"] is not None
    assert "NESTLE" in maker["value"] and "INDIA LIMITED" in maker["value"]
    assert maker["status"] == "NEEDS_REVIEW"  # joined: officer confirms


# ------------------------------------------------------- 3B.9 ---
def test_17_consumer_care_phone_normalization():
    from app.services.ocr.fields import extract_fields_detailed

    out = extract_fields_detailed([_line("Customer Care 1800 103 1947")])
    care = out["consumer_care"]
    assert care["value"] == "1800 103 1947"
    assert care.get("normalized_value") == "18001031947"


# ------------------------------------------------------- 3B.10 ---
def test_18_ingredient_column_isolation():
    from app.services.ocr import service as svc

    head = [[10, 10], [90, 10], [90, 30], [10, 30]]
    body = [[10, 40], [90, 40], [90, 120], [10, 120]]
    side = [[400, 40], [520, 40], [520, 120], [400, 120]]

    class _Ln:
        def __init__(self, text, box):
            self.text = text
            self.confidence = 0.9
            self.box = box

    from app.services.ocr import food as food_mod

    lines = [_Ln("INGREDIENTS", head), _Ln("Wheat Flour", body),
             _Ln("MRP Rs. 50", side)]
    cols = food_mod.cluster_text_columns(lines)
    picked = svc._select_body_column((10.0, 30.0, 90.0, 200.0), cols,
                                     lines, food_mod, 200.0)
    assert picked is not None
    assert picked["x1"] <= 200.0  # neighbour MRP column excluded


def test_19_ingredient_stops_before_nutrition():
    from app.services.ocr.food import extract_food_label

    lines = [_line("INGREDIENTS: Wheat Flour, Sugar, Salt"),
             _line("Energy 450 kcal"),
             _line("Protein 8 g")]
    out = extract_food_label(lines)
    cleaned = (out["fields"]["ingredients"].get("cleaned_text") or "")
    assert "Energy" not in cleaned and "Protein" not in cleaned
    assert "Wheat Flour" in cleaned


def test_20_ingredient_stops_before_storage():
    from app.services.ocr.food import extract_food_label

    lines = [_line("INGREDIENTS: Wheat Flour, Sugar"),
             _line("Store in a cool dry place")]
    out = extract_food_label(lines)
    cleaned = (out["fields"]["ingredients"].get("cleaned_text") or "")
    assert "Store in" not in cleaned


def test_21_ingredient_stops_before_manufacturer():
    from app.services.ocr.food import extract_food_label

    lines = [_line("INGREDIENTS: Wheat Flour, Sugar"),
             _line("Manufactured by Acme Foods")]
    out = extract_food_label(lines)
    cleaned = (out["fields"]["ingredients"].get("cleaned_text") or "")
    assert "Manufactured by" not in cleaned


def test_22_contents_not_ingredients():
    from app.services.ocr import food as food_mod

    assert food_mod._is_ingredient_heading("CONTENTS") is False
    assert food_mod._is_ingredient_heading(
        "Contents: wheat flour, sugar (12%)",
        "Contents: wheat flour, sugar (12%)") is True


def test_23_nutrition_rows_excluded_from_ingredients():
    from app.services.ocr.food import (
        extract_food_label, ingredient_boundary_decision)

    assert ingredient_boundary_decision("Energy 450 kcal")[0] is True
    assert ingredient_boundary_decision("Per 100g values")[0] is True
    lines = [_line("INGREDIENTS: Wheat Flour"),
             _line("Nutrition Information"),
             _line("Energy 450 kcal")]
    out = extract_food_label(lines)
    cleaned = (out["fields"]["ingredients"].get("cleaned_text") or "")
    assert "450" not in cleaned


# ------------------------------------------------------- 3B.12 ---
def test_24_targeted_veg_symbol_detection():
    from PIL import Image, ImageDraw

    from app.services.ocr import veg_symbol as veg

    img = Image.new("RGB", (300, 300), "white")
    d = ImageDraw.Draw(img)
    d.rectangle([120, 120, 150, 150], outline="green", width=3)
    d.ellipse([127, 127, 143, 143], fill="green")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    cands = veg.find_symbol_candidates(buf.getvalue())
    assert len(cands) >= 1
    assert len(cands[0].get("rel_box") or []) == 4
    blank = Image.new("RGB", (300, 300), "white")
    buf2 = io.BytesIO()
    blank.save(buf2, format="PNG")
    assert veg.find_symbol_candidates(buf2.getvalue()) == []


# ------------------------------------------------------- 3B.13 ---
def test_25_cross_image_agreement():
    from app.services.ocr import service as svc

    prov = _ScriptedProvider({
        "front": [("MRP Rs. 108", 0.9)],
        "back": [("MRP Rs. 108", 0.92)]})
    out = svc.extract_label_multi(
        [(_slot_png(0), "front"), (_slot_png(1), "back")],
        provider=prov)
    mrp = out["fields_detailed"]["mrp"]
    assert mrp["value"] == "108" and mrp["status"] == "DETECTED"


def test_26_cross_image_conflict():
    from app.services.ocr import service as svc

    prov = _ScriptedProvider({
        "front": [("MRP Rs. 108", 0.9)],
        "back": [("MRP Rs. 180", 0.9)]})
    out = svc.extract_label_multi(
        [(_slot_png(0), "front"), (_slot_png(1), "back")],
        provider=prov)
    mrp = out["fields_detailed"]["mrp"]
    assert mrp["status"] == "NEEDS_REVIEW"
    assert mrp["value"] is None
    assert {c["value"] for c in mrp["candidates"]} == {"108", "180"}


# ------------------------------------------------------- vision ---
def test_27_gemini_only_for_unresolved_fields():
    from app.services.package_intelligence import vision_stage as vs
    from app.services.vision.mock_provider import MockVisionProvider

    statuses = {"mrp": "DETECTED", "quantity": "NOT_DETECTED",
                "product_name": "NOT_DETECTED"}
    wanted = vs.wanted_vision_fields(None, statuses)
    assert "mrp" not in wanted  # resolved: never costs a call
    prov = MockVisionProvider()
    out = {"fields_detailed": {
        "mrp": {"value": "108", "confidence": 0.95,
                "status": "DETECTED", "image": "back", "box": None},
        "quantity": {"value": None, "confidence": None,
                     "status": "NOT_DETECTED"}}}
    res = vs.enhance_ocr_with_vision(
        out, [(_slot_png(0), "back")], None,
        inspection_id="stage3ab-test27", provider=prov)
    assert res["vision"]["vision_calls"] <= 6
    assert res["fields_detailed"]["mrp"]["value"] == "108"  # untouched


def test_28_no_duplicate_full_page_ocr():
    from app.services.ocr import service as svc

    out = svc.extract_label_multi(
        [(_slot_png(i), f"img_{i}") for i in range(4)],
        provider=_ScriptedProvider({}))
    full = [c for c in out["timings"]["provider_calls_detail"]
            if c.get("stage") == "full-page"]
    assert len(full) == 4
    dup = svc.extract_label_multi(
        [(_slot_png(0), "a"), (_slot_png(0), "b")],
        provider=_ScriptedProvider({}))
    full2 = [c for c in dup["timings"]["provider_calls_detail"]
             if c.get("stage") == "full-page"]
    assert len(full2) == 1
    by_label = {r["image"]: r for r in dup["images"]}
    assert by_label["b"]["duplicate_of"] == "a"


def test_29_call_budget_enforced_with_dupes():
    from app.services.ocr import service as svc

    out = svc.extract_label_multi(
        [(_slot_png(i % 3), f"img_{i}") for i in range(6)],
        provider=_ScriptedProvider({}))
    assert out["timings"]["call_budget"]["budget_respected"] is True
    assert out["timings"]["image_roles"]["img_3"] == "DUPLICATE"


def test_30_golden_fixture_format_documented():
    import json
    from pathlib import Path

    template = json.loads(
        (Path(__file__).parent / "golden" / "expected.template.json")
        .read_text(encoding="utf-8"))
    assert set(template) >= {"product", "verification_source",
                             "verified", "fields"}
    assert template["verified"] is False
    # No fabricated ground truth: every documented value is null.
    for _f, _v in template["fields"].items():
        assert _v["value"] is None, _f
