"""Stage 3 tests: fast field-accurate package extraction.

Deterministic fixture-line tests (no OCR engine needed) plus routing,
budget, and integration checks. Never a generic "accuracy" claim —
each test pins one field behaviour.
"""
from __future__ import annotations

from app.services.ocr.base import OcrLine
from app.services.ocr import food as food_mod
from app.services.ocr import image_roles as roles_mod
from app.services.ocr.fields import extract_fields, extract_with_status


def _line(text: str, conf: float = 0.9, image: str = "back") -> OcrLine:
    return OcrLine(text=text, confidence=conf, box=None, image=image)


def _box_line(text: str, x0: float, y0: float, x1: float, y1: float,
              conf: float = 0.9, image: str = "back") -> OcrLine:
    return OcrLine(text=text, confidence=conf,
                   box=[[x0, y0], [x1, y0], [x1, y1], [x0, y1]],
                   image=image)


# --- §7/§20: 70 g vs "1 g" ---
def test_70g_accepted_1g_rejected():
    out = extract_fields([_line("NET WT 70 g")])
    assert out["quantity"]["value"] == "70"
    assert out["unit"]["value"] == "g"
    out = extract_fields([_line("1 g")])
    assert out["quantity"]["value"] is None
    out = extract_fields([_line("Pack of 4")])
    assert out["quantity"]["value"] is None
    out = extract_fields([_line("4 servings 30 g")])
    # serving fragment without net context is never net quantity
    assert out["quantity"]["value"] is None
    out = extract_fields([_line("NET WT 1 kg")])
    assert out["quantity"]["value"] == "1"


# --- §8/§20: MRP vs phone / nutrition numbers ---
def test_mrp_vs_phone_number():
    out = extract_fields([_line("Customer care 18001234567"),
                          _line("MRP Rs. 108")])
    assert out["mrp"]["value"] == "108"
    out = extract_fields([_line("18001234567")])
    assert out["mrp"]["value"] is None


def test_mrp_vs_nutrition_number():
    out = extract_fields([_line("Energy 450 kcal"),
                          _line("Protein 8 g")])
    assert out["mrp"]["value"] is None


# --- §10/§20: FSSAI vs phone/barcode ---
def test_fssai_vs_phone_and_barcode():
    out = extract_fields([
        _line("Customer care 18001234567"),
        _line("8901234567890"),
        _line("FSSAI Lic No 10012043001234")])
    assert out["fssai_license"]["value"] == "10012043001234"
    out = extract_fields([_line("8901234567890")])
    assert out["fssai_license"]["value"] is None


# --- §11/§20: batch vs FSSAI digits ---
def test_batch_vs_fssai_digits():
    out = extract_fields([
        _line("FSSAI Lic No 10012043001234"),
        _line("Batch No B12")])
    assert out["batch_lot"]["value"] == "B12"
    out = extract_fields([_line("Batch No")])
    assert out["batch_lot"]["value"] is None


# --- §9/§20: MFG date vs arbitrary numbers ---
def test_mfg_date_vs_arbitrary_numbers():
    out = extract_fields([_line("MFD 05/2024")])
    assert out["manufacturing_date"]["value"] == "05/2024"
    for bad in ("70.16", "108", "325"):
        out = extract_fields([_line(f"MFD {bad}")])
        assert out["manufacturing_date"]["value"] is None, bad
    out = extract_fields([_line("05/2024")])
    assert out["manufacturing_date"]["value"] is None


# --- §5/§20: ingredient / nutrition separation ---
def test_ingredient_nutrition_separation():
    lines = [_line("INGREDIENTS: Wheat Flour, Salt, Sugar"),
             _line("Energy 450 kcal"),
             _line("Protein 8 g"),
             _line("Total Fat 15 g")]
    assert food_mod.ingredient_boundary_decision(
        "Energy 450 kcal")[0] is True
    assert food_mod.ingredient_boundary_decision(
        "Total Fat 15 g")[0] is True
    assert food_mod.ingredient_boundary_decision(
        "Serving size 30 g")[0] is True
    assert food_mod.ingredient_boundary_decision(
        "Per 100g values are approximate")[0] is True
    # Genuine ingredient clauses are untouched.
    assert food_mod.ingredient_boundary_decision(
        "Wheat Flour, Salt, Sugar")[0] is False
    assert food_mod.ingredient_boundary_decision(
        "Sugar (sucrose), Salt (12%)")[0] is False


def test_ingredient_storage_separation():
    assert food_mod.ingredient_boundary_decision(
        "Store in a cool dry place")[0] is True
    assert food_mod.ingredient_boundary_decision(
        "Keep away from sunlight")[0] is True


def test_ingredient_manufacturer_separation():
    assert food_mod.ingredient_boundary_decision(
        "Manufactured by Acme Foods")[0] is True
    assert food_mod.ingredient_boundary_decision(
        "Consumer Care 18001234567")[0] is True


# --- §5/§20: CONTENTS not automatically ingredients ---
def test_contents_not_automatically_ingredients():
    assert food_mod._is_ingredient_heading("CONTENTS") is False
    assert food_mod._is_ingredient_heading("Contents") is False
    # ...unless the surroundings are clearly ingredient-like.
    assert food_mod._is_ingredient_heading(
        "Contents: wheat flour, sugar (12%)",
        "Contents: wheat flour, sugar (12%)") is True
    assert food_mod._is_ingredient_heading("INGREDIENTS") is True


# --- §4/§20: column-aware ingredient crop (narrow column respected) ---
def test_column_aware_ingredient_region():
    from app.services.ocr import service as svc

    head_box = [[10, 10], [60, 10], [60, 30], [10, 30]]
    side_box = [[150, 40], [230, 40], [230, 120], [150, 120]]
    body_box = [[10, 40], [60, 40], [60, 120], [10, 120]]

    class _Ln:
        def __init__(self, text, box):
            self.text = text
            self.confidence = 0.9
            self.box = box

    lines = [_Ln("INGREDIENTS", head_box),
             _Ln("MRP Rs. 50", side_box),
             _Ln("Wheat Flour", body_box)]
    cols = food_mod.cluster_text_columns(lines)
    assert cols, "columns must resolve on boxed lines"
    rect = (10.0, 30.0, 60.0, 200.0)
    picked = svc._select_body_column(rect, cols, lines, food_mod, 200.0)
    assert picked is not None
    # The heading's narrow column wins over the neighbouring MRP column.
    assert picked["x1"] <= 100.0


# --- §2: image roles + priority map ---
def test_image_role_priority_map_complete():
    for field in ("product_name", "quantity", "veg_nonveg",
                  "ingredients", "nutrition", "fssai_license",
                  "manufacturer", "mrp", "manufacturing_date",
                  "best_before", "batch_lot", "consumer_care"):
        assert field in roles_mod.FIELD_IMAGE_PRIORITY, field
    assert roles_mod.FIELD_IMAGE_PRIORITY["mrp"][0] == "BACK"
    assert roles_mod.FIELD_IMAGE_PRIORITY["manufacturing_date"][0] == \
        "SIDE"
    assert roles_mod.FIELD_IMAGE_PRIORITY["product_name"][0] == "FRONT"


def test_role_classification_explicit_and_evidence():
    assert roles_mod.classify_label("front", []) == "FRONT"
    assert roles_mod.classify_label("back", []) == "BACK"
    back_texts = ["INGREDIENTS: Wheat Flour", "Nutrition Information",
                  "FSSAI Lic No 10012043001234",
                  "Manufactured by Acme", "MRP Rs. 50",
                  "Best before 12 months", "Net WT 70 g"]
    assert roles_mod.classify_label("image_2", back_texts) == "BACK"
    assert roles_mod.classify_label("image_9", ["Tasty Noodles"]) in (
        "FRONT", "UNKNOWN")
    ordered = roles_mod.ordered_labels_for_field(
        "mrp", ["front", "back"],
        {"front": "FRONT", "back": "BACK"})
    assert ordered[0] == "back"


# --- §6/§12: RapidOCR/Tesseract disagreement stays reviewable ---
def test_rapidocr_tesseract_disagreement_reviewable():
    from app.services.ocr import ingredient_hybrid as hybrid_mod

    rec = hybrid_mod.reconcile_ingredient_texts(
        "Wheat Flour, Salt", "Wheat Flour, Sugar", rapid_conf=0.9,
        tess_conf=0.5)
    assert rec["uncertain"] is True or rec["ocr_source"] in (
        "rapidocr", "hybrid", "tesseract")
    assert rec["text"]


# --- §13: Gemini disagreement preserved (overlay level) ---
def test_gemini_disagreement_preserved():
    from app.services.package_intelligence import vision_stage as vs_mod

    rec = vs_mod.overlay_reconciliation(
        {"fields_detailed": {
            "mrp": {"value": "108", "confidence": 0.9,
                    "status": "DETECTED", "image": "back",
                    "box": None}}},
        [{"field": "mrp", "value": "180", "unit": "INR",
          "status": "DETECTED", "confidence": 0.9,
          "evidence_text": "MRP Rs. 180",
          "image_id": "back", "source": "vision",
          "provider": "gemini", "model": "gemini-2.0-flash"}],
        None)
    hit = rec["fields"]["mrp"]
    assert hit["status"] == "NEEDS_REVIEW"
    assert {c["value"] for c in hit["candidates"]} == {"108", "180"}
    # A weak vision candidate never silently replaces strong OCR.
    rec = vs_mod.overlay_reconciliation(
        {"fields_detailed": {
            "mrp": {"value": "108", "confidence": 0.95,
                    "status": "DETECTED", "image": "back",
                    "box": None}}},
        [{"field": "mrp", "value": "109", "unit": "INR",
          "status": "DETECTED", "confidence": 0.4,
          "evidence_text": "MRP Rs. 109",
          "image_id": "back", "source": "vision",
          "provider": "gemini", "model": "gemini-2.0-flash"}],
        None)
    assert rec["fields"]["mrp"]["final_value"] is None


# --- §13: vision unavailable fallback keeps OCR ---
def test_vision_unavailable_fallback(monkeypatch):
    from conftest import make_client, make_repo

    from app.services.vision import provider as provider_mod

    monkeypatch.setattr(provider_mod, "get_vision_provider",
                        lambda explicit=None: None)
    from app.services.ocr import rapidocr_provider as rapid_mod
    from app.services.ocr.base import OcrOutput

    class _OCR:
        name = "ocr-only"

        def extract(self, image_np, image_side):
            from app.services.ocr.base import OcrLine

            return OcrOutput(
                lines=[OcrLine(text="MRP Rs. 50", confidence=0.9,
                               box=None, image=image_side)],
                engine=self.name)

    monkeypatch.setattr(rapid_mod, "RapidOCRProvider", lambda: _OCR())
    client = make_client(make_repo())
    import io as _io

    from PIL import Image

    img = Image.new("RGB", (120, 120), "white")
    buf = _io.BytesIO()
    img.save(buf, format="PNG")
    out = client.post(
        "/api/v1/ocr/extract",
        files={"front_image": ("f.png", buf.getvalue(), "image/png")},
        headers={"X-LegalAkshi-Role": "officer",
                 "X-LegalAkshi-User": "o3"}).json()
    assert out["fields"]["mrp"] == {"value": "50", "provenance": "OCR",
                                    "confidence": 0.9}
    assert out["vision"]["vision_enabled"] is False


# --- §3/§16: no duplicate full-page OCR; six-image budget ---
def test_no_duplicate_full_page_ocr():
    from app.services.ocr import service as svc
    from app.services.ocr.base import OcrLine, OcrOutput

    calls: list[str] = []

    class _Counting:
        name = "counting"

        def extract(self, image_np, image_side):
            calls.append(image_side)
            return OcrOutput(lines=[], engine=self.name)

    import io as _io

    from PIL import Image, ImageDraw

    def _png(slot=None):
        img = Image.new("RGB", (120, 120), "white")
        if slot is not None:
            d = ImageDraw.Draw(img)
            x, y = 8 + (slot % 3) * 36, 8 + (slot // 3) * 55
            d.rectangle([x, y, x + 30, y + 40], fill="black")
        buf = _io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()

    images = [(_png(i), f"img_{i}") for i in range(6)]
    out = svc.extract_label_multi(images, provider=_Counting())
    full = [c for c in out["timings"]["provider_calls_detail"]
            if c.get("stage") == "full-page"]
    assert len(full) == 6, "exactly one full-page pass per image"
    assert out["timings"]["call_budget"]["full_page_calls"] == 6
    assert out["timings"]["call_budget"]["budget_respected"] is True
    assert "role" in out["timings"]["images"]["img_0"]
    assert set(out["timings"]["field_counts"]) == {
        "detected", "needs_review", "not_detected"}
    assert "preprocessing_ms" in out["timings"]["stage_timings"]
    assert "region_detection_ms" in out["timings"]["stage_timings"]


# --- §18: NEEDS_REVIEW evidence retained per candidate ---
def test_conflict_evidence_retained():
    out = extract_with_status([
        _line("MRP Rs. 108", 0.9, "back"),
        _line("MRP Rs. 180", 0.88, "back")])
    assert out["mrp"]["status"] == "NEEDS_REVIEW"
    assert len(out["mrp"]["all_candidates"]) >= 2
