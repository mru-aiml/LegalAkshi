"""Round-3 regressions: first-class ingredient OCR on the Maggi scenario.

Covers the P-list: Maggi-like ingredient paragraphs, broken lines, INS
numbers, MRP variants + date/weight rejection, garbage/partial/coherent
ingredient statuses, OCR-failure-never-FAIL, multi-image reconciliation,
veg symbol, nutrition panel, manufacturing dates, and per-stage timings.
"""
from __future__ import annotations

import io
import time

import pytest

from app.engine import engine as engine_mod
from app.engine.facts import ASSUMED
from app.repositories.memory import MemoryRepo
from app.services.ocr.base import OcrLine, OcrOutput

INSP = {"inspector_id": "T-1", "inspector_name": "Tester",
        "business_name": "Test Store", "inspection_date": "2026-09-15"}

MAGGI = {
    "product_name": "Maggi 2-Minute Noodles", "category": "GENERAL",
    "is_prepackaged": True, "manufacturer": "Nestle India Ltd",
    "quantity": 70, "quantity_unit": "g", "quantity_type": "weight",
    "manufacturing_date": "2024-05-01", "mrp": "50",
    "consumer_care": "1800-103-1947", "unit_sale_price": "Rs.714 per kg",
    "imported": False, "ecommerce": False, "food": True,
}


def _line(text: str, conf: float = 0.9, image: str = "back") -> OcrLine:
    return OcrLine(text=text, confidence=conf, box=None, image=image)


class _MockProvider:
    name = "mock-ocr"

    def __init__(self, by_label):
        self._by_label = by_label
        self.calls: list[str] = []

    def extract(self, image_np, image_side):
        self.calls.append(str(image_side))
        label = str(image_side).split(":")[0]
        rows = self._by_label.get(label, [])
        return OcrOutput(
            lines=[OcrLine(text=t, confidence=c, image=label)
                   for t, c in rows], engine=self.name)


def _png_bytes(slot: int | None = None) -> bytes:
    # Distinct panels need distinct pixels (Stage 3A.5 dedup); slots are
    # 2D grid positions. Same slot twice = intentional duplicate.
    from PIL import Image, ImageDraw

    img = Image.new("RGB", (900, 300), "white")
    if slot is not None:
        col, row = slot % 3, (slot // 3) % 2
        x, y = 60 + col * 280, 50 + row * 120
        d = ImageDraw.Draw(img)
        d.rectangle([x, y, x + 180, y + 70], fill="black")
        d.text((x + 10, y + 20), f"P{slot}", fill="white")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _status(res, check_id):
    for f in res["findings"]:
        if f["rule_id"] == check_id:
            return f["status"]
    raise AssertionError(f"missing finding {check_id}")


def _analyze(product_extra, field_meta=None):
    repo = MemoryRepo()
    insp = repo.create_inspection(dict(INSP))
    payload = dict(MAGGI)
    payload.update(product_extra or {})
    if field_meta is not None:
        payload["field_meta"] = field_meta
    prod = repo.add_product(insp["inspection_id"], payload)
    pid = prod["product_id"]
    return engine_mod.analyze(
        repo, insp, repo.get_product(pid), repo.declarations_for(pid),
        as_of="2026-09-15", default_origin=ASSUMED)


# A faithful Maggi-70g-style back panel (front carries brand + net qty).
MAGGI_BACK = [
    "INGREDIENTS: Refined Wheat Flour (Maida) (68%), Palm Oil, Salt,",
    "Sugar, Mixed Spices (0.5%), Acidity Regulators (INS 500(ii)),",
    "Humectant (E451), Added Flavour.",
    "Nutrition Information per 100g:",
    "Energy 450 kcal",
    "Protein",
    "8 g",
    "Total Fat 15 g",
    "MRP Rs. 50 (Incl. of all taxes)",
    "MFD 05/2024",
    "Best Before 9 months from manufacture",
    "FSSAI Lic No. 10012043001234",
    "Mfd. by Nestle India Limited, Moga, Punjab 142001",
    "Customer Care 1800-103-1947",
    "Batch No M50924A",
]


# ------------------------------------------ 1. Maggi-like paragraph ---
def test_maggi_like_ingredient_paragraph():
    from app.services.ocr.food import extract_food_label

    out = extract_food_label([_line(t) for t in MAGGI_BACK])
    ing = out["fields"]["ingredients"]
    assert ing["detection"] == "DETECTED"
    assert "Refined Wheat Flour" in (ing["raw_text"] or "")
    assert "Humectant" in (ing["cleaned_text"] or "")
    # Block stops at the nutrition heading: no MRP/FSSAI bleed.
    assert "MRP" not in (ing["cleaned_text"] or "")
    assert "FSSAI" not in (ing["cleaned_text"] or "")
    names = [i["name"].lower() for i in ing["parsed_items"]]
    assert any("wheat flour" in n for n in names)
    assert any(i["ins_number"] for i in ing["parsed_items"])


# --------------------------------------------- 2. broken lines --------
def test_broken_ingredient_lines_rejoined():
    from app.services.ocr.food import extract_food_label, \
        reconstruct_ingredient_text

    text, notes = reconstruct_ingredient_text([
        _line("Refined wheat"), _line("flour (Maida),"),
        _line("pack-"), _line("aged salt, sugar"),
    ])
    assert "packaged" in text
    assert "flour (Maida)" in text
    out = extract_food_label([], region_lines=[
        _line("Refined wheat"), _line("flour (Maida), salt")])
    ing = out["fields"]["ingredients"]
    assert "flour (Maida)" in (ing["raw_text"] or "")


# ------------------------------------------------ 3. INS numbers -------
@pytest.mark.parametrize("segment,ins", [
    ("Acidity Regulators (INS 500(ii))", "INS 500(ii)"),
    ("Humectant (E451)", None),  # E-number kept verbatim in the segment
    ("Preservative (INS 202)", "INS 202"),
])
def test_ins_number_forms(segment, ins):
    from app.services.ocr.food import parse_ingredients

    items = parse_ingredients(segment)
    assert items, segment
    if ins is not None:
        assert items[0]["ins_number"] == ins, items[0]
    assert "500(ii)" in items[0]["raw_segment"] if "500" in segment else True


def test_ins_ocr_confusion_repaired_in_gated_pattern():
    from app.services.ocr.food import reconstruct_ingredient_text

    text, notes = reconstruct_ingredient_text(
        [_line("Acidity Regulators (INS 5O1), Humectant (E45I)")])
    assert "INS 501" in text
    assert "E451" in text
    assert notes  # every repair disclosed
    # Outside INS patterns nothing is rewritten.
    text2, _ = reconstruct_ingredient_text([_line("Palm Oii salt")])
    assert "Palm Oii" in text2


# --------------------------------------- 4-5. MRP Rs.50 variants ------
@pytest.mark.parametrize("text,expected", [
    ("MRP Rs. 50", "50"),
    ("MRP Rs.50", "50"),
    ("MRP: \u20b950", "50"),
    ("M.R.P. (incl. of all taxes): Rs. 50", "50"),
    ("M.R.P. (inclusive of all taxes) Rs.50", "50"),
    ("Rs 50", "50"),
    ("Rs. 50", "50"),
])
def test_mrp_punctuation_variants(text, expected):
    from app.services.ocr.fields import extract_fields

    assert extract_fields([_line(text)])["mrp"]["value"] == expected, text


# ------------------------------- heading tolerance --------------------
@pytest.mark.parametrize("heading,expected", [
    ("INGREDIENTS:", True),
    ("INGREDIENTS.", True),  # period instead of colon
    ("INGREDIENTS / CONTENTS", True),
    ("INGRED1ENTS", True),  # OCR digit confusion
    ("INGREDlENTS", True),
    ("COMPOSITON", True),  # dropped letter
    ("सामग्री", True),  # Hindi equivalent
    ("Net contents 70 g", False),  # quantity line, never a heading
    ("8901058845123", False),
    ("FSSAI Lic No. 10012043001234", False),
    ("Nutrition Information", False),
])
def test_tolerant_ingredient_headings(heading, expected):
    from app.services.ocr.food import _is_ingredient_heading

    assert _is_ingredient_heading(heading) is expected, heading


def test_heading_sharing_line_with_content():
    # Colon dropped by OCR and content on the same line: the declaration
    # must still be found (first-token exact match, length irrelevant).
    from app.services.ocr.food import extract_food_label

    out = extract_food_label(
        [_line("INGREDIENTS Refined Wheat Flour (Maida), palm oil, salt")])
    assert "palm oil" in (out["fields"]["ingredients"]["raw_text"] or "")


# --------------------------------- 6-7. FSSAI/phone/date/weight ------
@pytest.mark.parametrize("texts", [
    ["FSSAI Lic No. 10012043001234", "MRP 10012043001234"],
    ["Customer Care 1800-103-1947", "MRP 18001031947"],
])
def test_fssai_phone_never_become_mrp(texts):
    from app.services.ocr.fields import extract_fields

    assert extract_fields([_line(t) for t in texts])["mrp"]["value"] is None


@pytest.mark.parametrize("text", ["Rs. 05/2024", "MRP 05/2024", "Rs. 5 g"])
def test_date_weight_fragments_rejected_as_mrp(text):
    from app.services.ocr.fields import extract_fields

    assert extract_fields([_line(text)])["mrp"]["value"] is None, text


# --------------------------------- 8. garbage -> NOT_DETECTED --------
def test_ingredient_garbage_is_not_detected():
    from app.services.ocr.food import extract_food_label

    lines = [_line("INGREDIENTS:"),
             _line("NodsWhfuEdibiegbieilSalWhatguen Aodty", 0.45),
             _line("regulators(500&501@)andHumectant(4510)", 0.4)]
    out = extract_food_label(lines)
    ing = out["fields"]["ingredients"]
    assert ing["detection"] == "NOT_DETECTED"
    assert ing["coherence"]["score"] < 0.35
    assert ing["raw_text"]  # audit trail preserved, never shown as valid


# ------------------------------ 9. partial -> NEEDS_REVIEW -----------
def test_partially_readable_ingredients_need_review():
    from app.services.ocr.food import extract_food_label

    lines = [_line("Ingredients: Refined wheat flour, Xkzqvm NdsWhf 12345,",
                   0.65),
             _line("palm oil, salt", 0.65)]
    out = extract_food_label(lines)
    ing = out["fields"]["ingredients"]
    assert ing["detection"] == "NEEDS_REVIEW"
    assert 0.35 <= ing["coherence"]["score"] < 0.70


# -------------------------------- 10. coherent -> DETECTED -----------
def test_coherent_ingredients_detected():
    from app.services.ocr.food import extract_food_label

    lines = [_line("Ingredients: Refined wheat flour (Maida), palm oil, "
                   "salt, sugar,", 0.9),
             _line("mixed spices, preservative (INS 202).", 0.88)]
    out = extract_food_label(lines)
    ing = out["fields"]["ingredients"]
    assert ing["detection"] == "DETECTED"
    assert ing["coherence"]["score"] >= 0.70


# ----------------- INS suffix repair + no bare-digit invention -----
def test_ins_roman_suffix_repaired_not_invented():
    from app.services.ocr.food import parse_ingredients, \
        reconstruct_ingredient_text

    text, notes = reconstruct_ingredient_text(
        [_line("Acidity Regulators (INS 500(il))")])
    assert "INS 500(ii)" in text  # (il) is a misread of valid (ii)
    assert notes
    items = parse_ingredients("Acidity Regulators (INS 500(ii))")
    assert items[0]["ins_number"] == "INS 500(ii)"
    # Bare digits without an INS/E prefix are kept verbatim, never
    # promoted to an additive identity.
    bare = parse_ingredients("Humectant (4510), salt")
    assert bare[0]["ins_number"] is None
    assert "(4510)" in bare[0]["raw_segment"]


def test_registry_typo_normalisation_gated():
    from app.services.ingredient_analysis import classify_ingredient

    hit = classify_ingredient("Palm oii")
    assert hit["status"] == "ALLOWED"
    assert hit["normalised_from"] == "palm oil"
    assert hit["confidence"] == 0.6  # capped, disclosed
    # Ambiguous or distant strings stay UNKNOWN — never invented.
    assert classify_ingredient("quantum sprinkle dust")["status"] == "UNKNOWN"
    assert "normalised_from" not in classify_ingredient("palm oil")


def test_dense_scan_result_guard():
    from app.services.ocr.food import scan_result_usable

    assert not scan_result_usable(
        "FSSAI Lic No. 10012043001234 MRP Rs. 50 MFD 05/2024",
        {"score": 0.4})
    assert scan_result_usable(
        "Refined wheat flour, palm oil, salt", {"score": 0.8})
    assert not scan_result_usable("", {"score": 0.0})


# ------------------------------ 11. OCR failure != legal FAIL --------
def test_ocr_missed_mrp_becomes_needs_review_not_fail():
    res = _analyze(
        {"mrp": None},
        field_meta={"mrp": {"ocr_engine": "rapidocr", "confidence": 0.0}})
    assert _status(res, "CHK-MRP") == "NEEDS_REVIEW"


def test_officer_confirmed_missing_mrp_still_fails():
    res = _analyze({"mrp": None})
    assert _status(res, "CHK-MRP") == "FAIL"


# --------------------------- 12. multi-image reconciliation ---------
def test_ingredients_reconciled_across_front_back():
    from app.services.ocr import service

    provider = _MockProvider({
        "front": [("MAGGI 2-Minute Noodles", 0.95), ("NET WT 70 g", 0.9)],
        "back": [("Ingredients: Refined wheat flour, palm oil, salt", 0.8),
                 ("MRP Rs. 50", 0.9)],
    })
    out = service.extract_label_multi(
        [(_png_bytes(0), "front"),
         (_png_bytes(1), "back")],
        provider=provider)
    # Strongest evidence per field wins; nothing overwritten by weaker.
    assert out["fields_detailed"]["quantity"]["value"] == "70"
    assert out["fields_detailed"]["quantity"]["image"] == "front"
    ing = out["food"]["fields"]["ingredients"]
    assert "palm oil" in (ing["raw_text"] or "")
    assert out["fields_detailed"]["mrp"]["value"] == "50"


# ---------------------------------------- 13. veg symbol -------------
def test_veg_symbol_cheap_and_honest():
    from PIL import Image, ImageDraw

    from app.services.ocr.veg_symbol import detect_veg_symbol

    buf = io.BytesIO()
    Image.new("RGB", (200, 200), "white").save(buf, format="PNG")
    blank = detect_veg_symbol([buf.getvalue()])
    assert blank["status"] == "NOT_DETECTED"
    assert blank["provenance"] == "IMAGE"
    assert blank["confidence"] is not None
    img = Image.new("RGB", (200, 200), "white")
    dr = ImageDraw.Draw(img)
    dr.rectangle([80, 80, 120, 120], outline="green", width=4)
    dr.ellipse([88, 88, 112, 112], fill="green")
    buf2 = io.BytesIO()
    img.save(buf2, format="PNG")
    sym = detect_veg_symbol([buf2.getvalue()])
    assert sym["status"] in ("DETECTED", "NEEDS_REVIEW", "NOT_DETECTED")
    assert set(sym) >= {"status", "confidence", "provenance"}


# -------------------------------------- 14. nutrition panel ---------
def test_nutrition_table_aware_no_fabrication():
    from app.services.ocr.food import extract_food_label

    out = extract_food_label([_line(t) for t in [
        "Nutrition Information per 100g:",
        "Energy 450 kcal",
        "Protein",
        "8 g",
        "Total Fat 15 g",
        "MRP Rs. 50",
    ]])
    nut = out["fields"]["nutrition"]
    assert nut["energy"]["value"] == "450 kcal"
    assert nut["protein"]["value"] == "8 g"  # split rows rejoined
    assert nut["total_fat"]["value"] == "15 g"
    assert nut["sodium"]["value"] is None  # never fabricated
    assert nut["sodium"]["detection"] in ("NEEDS_REVIEW", "NOT_DETECTED")


# --------------------------------- 15. manufacturing dates ----------
@pytest.mark.parametrize("text,expected", [
    ("MFD 05/2024", "05/2024"),
    ("MFG 12-2023", "12-2023"),
    ("MFD May 2024", "May 2024"),
    ("Packed On 04/06/2024", "04/06/2024"),
])
def test_manufacturing_date_forms(text, expected):
    from app.services.ocr.fields import extract_fields

    assert extract_fields([_line(text)])["manufacturing_date"]["value"] \
        == expected, text


def test_low_confidence_date_needs_review_status():
    from app.services.ocr.fields import extract_with_status

    out = extract_with_status([_line("MFD 05/2024", 0.3)])
    assert out["manufacturing_date"]["value"] == "05/2024"
    assert out["manufacturing_date"]["status"] == "NEEDS_REVIEW"


# ---------------- parchment: report never shows garbage --------------
def test_report_garbage_ingredients_render_needs_review():
    from conftest import make_client, make_repo

    repo = make_repo()
    client = make_client(repo)
    iid = client.post("/api/v1/inspections", json=dict(INSP)).json()[
        "inspection_id"]
    bad = dict(MAGGI)
    bad["ingredients_raw"] = (
        "NodsWhfuEdibiegbieilSalWhatguen Aodty regulators(500&501@)"
        "andHumectant(4510)")
    client.post(f"/api/v1/inspections/{iid}/analyze", json={"product": bad})
    html = client.get(f"/api/v1/reports/{iid}?format=html").text
    assert "did not meet OCR coherence threshold" in html
    assert "NEEDS REVIEW" in html


# --------------------------- stage timings always reported ----------
def test_per_stage_timings_reported():
    from app.services.ocr import service

    provider = _MockProvider({
        "front": [("Maggi 2-Minute Noodles", 0.95), ("NET WT 70 g", 0.93)],
        "back": [("MRP Rs. 50", 0.91), ("MFD 05/2024", 0.9)],
    })
    out = service.extract_label_multi(
        [(_png_bytes(0), "front"),
         (_png_bytes(1), "back")],
        provider=provider)
    t = out["timings"]
    for key in ("stage1_ms", "ingredient_ms", "declaration_ms",
                "nutrition_ms", "symbol_ms", "reconciliation_ms",
                "total_ms"):
        assert key in t, key
        assert t[key] is not None and t[key] >= 0
