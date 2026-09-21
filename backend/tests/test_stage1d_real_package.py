"""Stage-1D real-package regression: field retrieval + fusion.

Fixture transcribed from the observed real 4-image package result
(front / back / image_0 / image_1): the OCR *strings* below mirror what
the pipeline actually saw, so these tests pin CANDIDATE SELECTION —
never recognition. Manually verified expectations only; uncertain
fields assert NEEDS_REVIEW + blank, never guesses.

The headline regression (Part 24): "TRANSFAT" must NEVER be returned
as the product name — correctly detected or NEEDS_REVIEW + blank.
"""
from __future__ import annotations

from app.services.ocr.base import OcrLine, OcrOutput
from app.services.ocr import service as svc


def _box(x0, y0, x1, y1):
    return [[x0, y0], [x1, y0], [x1, y1], [x0, y1]]


def _ln(text, conf=0.85, box=None, image="back"):
    return OcrLine(text=text, confidence=conf, box=box, image=image)


def _png_bytes(w: int = 900, h: int = 300, slot: int | None = None) -> bytes:
    # Distinct panels need distinct pixels (Stage 3A.5 dedup); slots are
    # 2D grid positions. Same slot twice = intentional duplicate.
    from PIL import Image, ImageDraw
    import io

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


class _FixtureProvider:
    """Mock provider replaying transcribed real-package OCR per label."""

    name = "mock-ocr"

    LINES = {
        # Front: brand display + descriptive product + net quantity.
        "front": [
            ("MUNCHY'S", 0.95, _box(300, 40, 620, 130)),
            ("Choco Crunch Biscuits", 0.93, _box(250, 150, 670, 200)),
            ("NET WT 100 g", 0.90, _box(350, 220, 570, 250)),
        ],
        # Back: glued ingredients, storage bleed, maker block with PIN,
        # damaged care + FSSAI context lines.
        "back": [
            ("INGREDIENTS: REFINEDWHEATFLOUR(MAIDA)(663)", 0.80,
             _box(60, 60, 840, 95)),
            ("SUGAR REFINEDPALMOIL MILKPRODUCTSWHEY", 0.78,
             _box(60, 100, 840, 135)),
            ("TO A CLEAN AIRTIGHT CONTAINER ONCE OPENED.", 0.80,
             _box(60, 145, 840, 175)),
            ("Pkg.Mtrl.Mfd.By:SHRINATH ROTOPACK PVT.LTD.", 0.75,
             _box(60, 400, 840, 430)),
            ("Plot 12, Industrial Area, Moga 142001", 0.70,
             _box(60, 435, 840, 465)),
            ("Custorner Care 1800-103-1947", 0.72,
             _box(60, 475, 840, 505)),
            ("FSSA Lic N0. 10015043001129", 0.80,
             _box(60, 515, 840, 545)),
        ],
        # image_0: nutrition table (the TRANSFAT trap lives here).
        "image_0": [
            ("Nutrition Information", 0.90, _box(60, 40, 500, 75)),
            ("Trans Fat 5 g", 0.88, _box(60, 85, 400, 115)),
            ("Energy 450 kcal", 0.88, _box(60, 125, 400, 155)),
        ],
        # image_1: side panel, damaged MRP word + dateless MFD fragment.
        "image_1": [
            ("MRF Rs. 45 (Incl. of all taxes)", 0.78,
             _box(60, 200, 700, 235)),
            ("MFD", 0.50, _box(60, 250, 160, 275)),
        ],
    }

    def extract(self, image_np, image_side):
        label = str(image_side).split(":")[0]
        return OcrOutput(
            lines=[OcrLine(text=t, confidence=c, box=b, image=label)
                   for t, c, b in self.LINES.get(label, [])],
            engine=self.name)


def _run_fixture():
    return svc.extract_label_multi(
        [(_png_bytes(slot=0), "front"),
         (_png_bytes(slot=1), "back"),
         (_png_bytes(slot=2), "image_0"),
         (_png_bytes(slot=3), "image_1")],
        provider=_FixtureProvider())


# ------------------------------------------------- Part 24: TRANSFAT ---
def test_transfat_never_product_name_unit():
    from app.services.ocr.fields import extract_with_status

    out = extract_with_status([
        _ln("Trans Fat 5 g", 0.91, _box(60, 85, 400, 115), "image_0"),
        _ln("Energy 450 kcal", 0.88, _box(60, 125, 400, 155), "image_0"),
    ])
    assert out["product_name"]["value"] is None
    assert out["product_name"]["status"] == "NEEDS_REVIEW"


def test_transfat_never_product_name_fixture():
    det = _run_fixture()["fields_detailed"]
    value = (det["product_name"]["value"] or "")
    assert "TRANS" not in value.upper().replace(" ", "")
    assert det["product_name"]["status"] in ("DETECTED", "NEEDS_REVIEW")
    if det["product_name"]["status"] == "DETECTED":
        assert value == "Choco Crunch Biscuits"


def test_product_brand_generic_separated():
    det = _run_fixture()["fields_detailed"]
    prod = det["product_name"]
    assert prod["value"] == "Choco Crunch Biscuits"
    assert prod["brand"] == "MUNCHY'S"
    assert det["common_generic_name"]["value"] == "Biscuits"
    assert prod["image"] == "front"  # best source image recorded


# ------------------------------------------------- field retrieval ---
def test_quantity_detected_with_unit():
    det = _run_fixture()["fields_detailed"]
    assert det["quantity"]["value"] == "100"
    assert det["quantity"]["status"] == "DETECTED"
    assert det["unit"]["value"] == "g"


def test_mrp_recovered_from_damaged_word():
    det = _run_fixture()["fields_detailed"]
    assert det["mrp"]["value"] == "45"
    assert det["mrp"]["status"] == "DETECTED"
    assert det["mrp"]["image"] == "image_1"
    assert det["mrp"]["box"] is not None  # evidence box stored
    assert det["mrp"]["all_candidates"], "candidate pool retained"


def test_date_fragment_needs_review_blank():
    det = _run_fixture()["fields_detailed"]
    mfg = det["manufacturing_date"]
    assert mfg["value"] is None
    assert mfg["status"] == "NEEDS_REVIEW"  # anchor seen, value unreadable


def test_fssai_detected_despite_damage():
    det = _run_fixture()["fields_detailed"]
    fssai = det["fssai_license"]
    assert fssai["value"] == "10015043001129"
    assert fssai["status"] == "DETECTED"
    assert fssai["box"] is not None


def test_manufacturer_normalized_not_rewritten():
    det = _run_fixture()["fields_detailed"]
    maker = det["manufacturer"]
    assert maker["value"] == "SHRINATH ROTOPACK PVT. LTD."
    assert maker["status"] == "DETECTED"
    assert "Pkg" not in (maker["value"] or "")


def test_consumer_care_detected():
    det = _run_fixture()["fields_detailed"]
    care = det["consumer_care"]
    assert care["value"] == "1800-103-1947"
    assert care["status"] == "DETECTED"


# ------------------------------------------------- ingredients ---
def test_ingredient_spacing_and_no_contamination():
    food = _run_fixture()["food"]["fields"]["ingredients"]
    cleaned = (food.get("cleaned_text") or "").upper()
    assert "REFINED WHEAT FLOUR" in cleaned
    assert "REFINED PALM OIL" in cleaned or "PALM OIL" in cleaned
    assert "MILK PRODUCTS" in cleaned or "MILK" in cleaned
    assert "(663)" in (food.get("cleaned_text") or "")  # kept, not INS
    for banned in ("AIRTIGHT", "ONCE OPENED", "1800-103", "142001",
                   "ROTOPACK", "MFD.BY", "CUSTOMER", "CUSTORNER",
                   "LIC N0", "PLOT 12"):
        assert banned not in cleaned, banned
    assert food.get("rejected_lines"), "rejections recorded as evidence"


def test_plot12_is_not_batch_lot():
    det = _run_fixture()["fields_detailed"]
    assert det["batch_lot"]["value"] is None
    assert det["batch_lot"]["status"] == "NOT_DETECTED"


def test_ins_codes_preserved_not_converted():
    from app.services.ocr.food import parse_ingredients

    items = parse_ingredients("Raising Agents (INS 500(ii)), Salt (663)")
    by_raw = {i["raw_segment"]: i for i in items}
    assert by_raw["Raising Agents (INS 500(ii))"]["ins_number"] == "INS 500(ii)"
    assert by_raw["Salt (663)"]["ins_number"] is None


# ------------------------------------------------- fusion ---
def test_multi_image_agreement_keeps_sources():
    det = _run_fixture()["fields_detailed"]
    assert det["fssai_license"]["status"] == "DETECTED"


def test_multi_image_conflict_needs_review_with_candidates():
    from app.services.ocr.fields import extract_fields_detailed
    from app.services.ocr.service import _reconcile

    hits = [
        {"value": "50", "confidence": 0.9, "image": "back",
         "image_index": 3, "box": None, "unit": None},
        {"value": "45", "confidence": 0.88, "image": "image_1",
         "image_index": 0, "box": None, "unit": None},
    ]
    merged = _reconcile("mrp", hits)
    assert merged["status"] == "NEEDS_REVIEW"
    assert merged["value"] is None  # never chosen silently
    assert {c["value"] for c in merged["candidates"]} == {"50", "45"}


def test_cross_field_phone_is_not_mrp():
    from app.services.ocr.fields import extract_with_status

    out = extract_with_status([
        _ln("MRP 18001031947", 0.8, _box(0, 0, 200, 30), "back"),
        _ln("Customer Care 18001031947", 0.9, _box(0, 40, 200, 70), "back"),
    ])
    assert out["mrp"]["value"] is None  # phone digits rejected as MRP


def test_evidence_boxes_present():
    det = _run_fixture()["fields_detailed"]
    for key in ("product_name", "quantity", "mrp", "fssai_license",
                "manufacturer", "consumer_care"):
        hit = det[key]
        if hit["value"] is not None:
            assert hit["box"] is not None, key
            assert hit["image"] in ("front", "back", "image_0",
                                    "image_1"), key


def test_honest_confidence_fields():
    det = _run_fixture()["fields_detailed"]
    for key, hit in det.items():
        if key not in ("product_name", "common_generic_name",
                       "manufacturer", "quantity", "unit", "mrp",
                       "batch_lot", "manufacturing_date", "best_before",
                       "use_by", "fssai_license", "consumer_care",
                       "country_of_origin", "unit_sale_price"):
            continue
        assert "fused_confidence" in hit, key
        assert "score_reasons" in hit, key
        if hit["status"] == "DETECTED" and hit["value"] not in (None, ""):
            assert (hit["fused_confidence"] or 0) >= 0.6, key


def test_call_budget_held_on_fixture():
    out = _run_fixture()
    t = out["timings"]
    assert t["provider_calls"] <= 4 * svc.MAX_PROVIDER_CALLS_PER_IMAGE
    assert t["tesseract_calls"] <= 4 * svc.MAX_TESSERACT_CALLS_PER_IMAGE
    assert t["call_budget"]["budget_respected"] is True
    full = [e for e in t["provider_calls_detail"]
            if e.get("stage") == "full-page"]
    assert len(full) == 4  # one full-page pass per image, no reruns
