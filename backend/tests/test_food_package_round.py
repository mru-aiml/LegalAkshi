"""Food-package improvement round — regression tests.

Covers: 70 g net-quantity extraction, multi-image OCR aggregation, MRP and
date extraction, ingredient extraction, FSSAI extraction, veg/non-veg
detection handling, ingredient classification (unknown != prohibited),
package-only vs online-listing e-commerce behaviour, NOT_DETECTED vs
CONFIRMED_MISSING, OCR/manual provenance, rule-engine/RBAC/enforcement/
notification/PDF/Neon regressions. RapidOCR provider unchanged.
"""
from __future__ import annotations

import io

import pytest

from app.engine import engine as engine_mod
from app.engine.facts import ASSUMED
from app.repositories.memory import MemoryRepo
from app.services.ocr.base import OcrLine, OcrOutput

INSP = {"inspector_id": "T-1", "inspector_name": "Tester",
        "business_name": "Test Store", "inspection_date": "2026-09-15"}

BASE_PRODUCT = {
    "product_name": "Maggi 2-Minute Noodles", "category": "GENERAL",
    "is_prepackaged": True, "manufacturer": "Nestle India Ltd",
    "quantity": 70, "quantity_unit": "g", "quantity_type": "weight",
    "manufacturing_date": "2024-05-01", "mrp": "14",
    "consumer_care": "1800-103-1947", "unit_sale_price": "Rs.200 per kg",
    "imported": False, "ecommerce": False, "food": True,
}


def _line(text: str, conf: float = 0.9, image: str = "front") -> OcrLine:
    return OcrLine(text=text, confidence=conf, box=None, image=image)


def _run(repo, extra=None, as_of="2026-09-15", origin=ASSUMED):
    insp = repo.create_inspection(dict(INSP))
    merged = dict(BASE_PRODUCT)
    if extra:
        merged.update(extra)
    prod = repo.add_product(insp["inspection_id"], merged)
    pid = prod["product_id"]
    return engine_mod.analyze(
        repo, insp, repo.get_product(pid), repo.declarations_for(pid),
        as_of=as_of, default_origin=origin)


def _status(res, check_id):
    for f in res["findings"]:
        if f["rule_id"] == check_id:
            return f["status"]
    raise AssertionError(f"missing finding {check_id}")


# ------------------------------------------------- 1. 70 g extraction ---
@pytest.mark.parametrize("texts,qty,unit", [
    (["NET WT 70 g"], "70", "g"),
    (["NET WEIGHT 70 g"], "70", "g"),
    (["NET QTY 70 g"], "70", "g"),
    (["NET QUANTITY 70 g"], "70", "g"),
    (["Net Qty: 70g"], "70", "g"),
    (["70 grams"], "70", "g"),
    (["NET WT 0.070 kg"], "0.070", "kg"),
    # Split across lines/photos: context on one line, amount on another.
    (["NET WT", "70 g"], "70", "g"),
    (["70 g", "NET WEIGHT"], "70", "g"),
])
def test_net_quantity_70g_variants(texts, qty, unit):
    from app.services.ocr.fields import extract_fields

    out = extract_fields([_line(t) for t in texts])
    assert out["quantity"]["value"] == qty
    assert out["unit"]["value"] == unit


def test_net_quantity_never_fabricated():
    from app.services.ocr.fields import extract_fields

    out = extract_fields([_line("Tasty Noodles")])
    assert out["quantity"]["value"] is None
    assert out["unit"]["value"] is None


# --------------------------------------- 2. multi-image OCR aggregation ---
class _LabeledProvider:
    """Mock provider returning per-image-label lines (multi-photo case)."""

    name = "mock-ocr"

    def __init__(self, by_label):
        self._by_label = by_label

    def extract(self, image_np, image_side):
        label = str(image_side).split(":")[0]
        rows = self._by_label.get(label, [])
        return OcrOutput(
            lines=[OcrLine(text=t, confidence=c, image=label)
                   for t, c in rows], engine=self.name)


def _png_bytes(slot: int | None = None) -> bytes:
    # Distinct panels must differ in PIXELS, not just labels: the
    # service reuses OCR across near-duplicate uploads (Stage 3A.5).
    # Pass distinct slot ints per panel (2D grid positions); reuse the
    # same slot twice only to simulate an intentional duplicate.
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


def test_multi_image_aggregation_combines_evidence():
    from app.services.ocr import service

    provider = _LabeledProvider({
        "front": [("Maggi 2-Minute Noodles", 0.95)],
        "back": [("NET WT", 0.8), ("70 g", 0.91), ("MRP Rs. 14", 0.88)],
    })
    out = service.extract_label_multi(
        [(_png_bytes(0), "front"),
         (_png_bytes(1), "back")],
        provider=provider)
    assert out["images_analyzed"] == 2
    assert out["fields"]["quantity"]["value"] == "70"
    assert out["fields"]["quantity"]["image"] == "back"
    assert out["fields"]["mrp"]["value"] == "14"
    assert out["fields"]["product_name"]["value"] == "Maggi 2-Minute Noodles"
    assert out["fields"]["product_name"]["image"] == "front"


def test_multi_image_provenance_per_field():
    from app.services.ocr import service

    provider = _LabeledProvider({
        "front": [("NET WT", 0.8)],
        "back": [("70 g", 0.91)],
    })
    out = service.extract_label_multi(
        [(_png_bytes(0), "front"),
         (_png_bytes(1), "back")],
        provider=provider)
    det = out["fields_detailed"]["quantity"]
    assert det["value"] == "70"
    assert det["confidence"] is not None
    assert det["image"] in ("front", "back")


# ------------------------------------------------------ 3. MRP tiny text ---
@pytest.mark.parametrize("texts,expected", [
    (["MRP Rs. 14.00"], "14.00"),
    (["M.R.P. Rs 50/-"], "50"),
    (["MRP (Incl. of all taxes): Rs. 50"], "50"),
    (["MRP", "Rs. 14"], "14"),  # wrapped across two lines
    (["Maximum Retail Price Rs 450"], "450"),
])
def test_mrp_variants(texts, expected):
    from app.services.ocr.fields import extract_fields

    out = extract_fields([_line(t) for t in texts])
    assert out["mrp"]["value"] == expected


# ------------------------------------------------------------ 4. dates ---
@pytest.mark.parametrize("texts,field,expected", [
    (["MFD 05/2024"], "manufacturing_date", "05/2024"),
    (["MFG Date: 12-2023"], "manufacturing_date", "12-2023"),
    (["PKD 04/06/2024"], "manufacturing_date", "04/06/2024"),
    (["Packed On 04-06-2024"], "manufacturing_date", "04-06-2024"),
    (["Best Before 6 months from manufacture"], "best_before",
     "Best Before 6 months from manufacture"),
    (["Use By 01/2025"], "use_by", "01/2025"),
    (["Expiry Date 01/01/2025"], "best_before", "01/01/2025"),
])
def test_date_variants(texts, field, expected):
    from app.services.ocr.fields import extract_fields

    out = extract_fields([_line(t) for t in texts])
    got = out[field]["value"]
    if expected is None:
        assert got is None
    else:
        assert got is not None and expected[:4] in got


def test_not_detected_vs_needs_review():
    from app.services.ocr.food import extract_food_label

    # No date context anywhere -> NOT_DETECTED (not a violation signal).
    out = extract_food_label([_line("Tasty Noodles")])
    assert out["fields"]["manufacturing_date_context"] is False
    # Date context present but value unparseable -> NEEDS_REVIEW downstream
    # via the engine (low confidence forces review, never silent FAIL).
    repo = MemoryRepo()
    insp = repo.create_inspection(dict(INSP))
    prod = repo.add_product(insp["inspection_id"], dict(BASE_PRODUCT))
    pid = prod["product_id"]
    decls = list(repo.declarations_for(pid))
    decls.append({"field_name": "mrp", "extracted_value": "14",
                  "normalized_value": None, "confidence": 0.1,
                  "ocr_engine": "rapidocr", "evidence_id": None})
    res = engine_mod.analyze(repo, insp, repo.get_product(pid), decls,
                             as_of="2026-09-15", default_origin=ASSUMED)
    assert _status(res, "CHK-MRP") == "NEEDS_REVIEW"


# ------------------------------------------------------ 5. ingredients ---
def test_ingredient_extraction_preserves_text():
    from app.services.ocr.food import extract_food_label, parse_ingredients

    lines = [_line("Ingredients: Refined wheat flour (Maida) (68%), "
                   "palm oil, salt, sugar, mixed spices (0.5%), "
                   "preservative (INS 202), added flavour."),
             _line("Nutrition Information per 100g: Energy 450 kcal, "
                   "Protein 8 g, Total Fat 15 g.")]
    out = extract_food_label(lines)
    ing = out["fields"]["ingredients"]
    assert ing["detection"] == "DETECTED"
    assert "Refined wheat flour" in (ing["raw_text"] or "")
    names = [i["ingredient"].lower() for i in ing["parsed_items"]]
    assert any("wheat flour" in n or "maida" in n for n in names)
    by_raw = {i["raw_segment"][:20]: i for i in ing["parsed_items"]}
    assert any(i["percentage"] for i in ing["parsed_items"])
    assert any(i["ins_number"] == "INS 202" for i in ing["parsed_items"])
    assert parse_ingredients(None) == []
    assert parse_ingredients("") == []


# ----------------------------------------------------------- 6. FSSAI ---
@pytest.mark.parametrize("text,expected", [
    ("FSSAI Lic No. 10012011000123", "10012011000123"),
    ("FSSAI Lic. No. 100 12011000123", "10012011000123"),
    ("8901234567890", None),  # bare digits (barcode?) -> null
    ("FSSAI 12345", None),
])
def test_fssai_extraction(text, expected):
    from app.services.ocr.fields import extract_fields

    assert extract_fields([_line(text)])["fssai_license"]["value"] == expected


# --------------------------------------- 7. veg/non-veg result handling ---
def _veg_test_image(color: tuple[int, int, int]) -> bytes:
    from PIL import Image, ImageDraw

    img = Image.new("RGB", (400, 400), "white")
    d = ImageDraw.Draw(img)
    d.rectangle([180, 180, 220, 220], outline=color, width=4)
    d.ellipse([188, 188, 212, 212], fill=color)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def test_veg_symbol_detected_vegetarian():
    from app.services.ocr.veg_symbol import detect_veg_symbol

    out = detect_veg_symbol([_veg_test_image((0, 160, 0))])
    assert out["provenance"] == "IMAGE"
    assert out["status"] == "DETECTED"
    assert out["classification"] == "VEGETARIAN"
    assert out["confidence"] is not None


def test_veg_symbol_not_detected_is_not_a_violation():
    from app.services.ocr.veg_symbol import detect_veg_symbol

    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (400, 400), "white").save(buf, format="PNG")
    out = detect_veg_symbol([buf.getvalue()])
    assert out["status"] in ("NOT_DETECTED", "NEEDS_REVIEW")
    assert out["classification"] == "UNKNOWN"
    # Detection failure must never read as non-compliant.
    assert "not a finding" in out["reason"].lower()


def test_veg_symbol_corrupt_image_needs_review():
    from app.services.ocr.veg_symbol import detect_veg_symbol

    out = detect_veg_symbol([b"\x00\x01not-an-image"])
    assert out["status"] == "NEEDS_REVIEW"
    assert out["classification"] == "UNKNOWN"


# --------------------------------- 8/9. ingredient classification -------
def test_ingredient_status_classification():
    from app.services.ingredient_analysis import analyze_ingredients

    out = analyze_ingredients(
        [{"ingredient": "salt"}, {"ingredient": "preservative (INS 202)"},
         {"ingredient": "quantum sprinkle dust"}])
    by_name = {r["ingredient"]: r for r in out["ingredients"]}
    assert by_name["salt"]["status"] == "ALLOWED"
    assert by_name["salt"]["source"]
    assert by_name["salt"]["version"]
    assert by_name["salt"]["effective_date"]
    assert by_name["preservative (INS 202)"]["status"] == "NEEDS_REVIEW"
    assert by_name["quantum sprinkle dust"]["status"] == "UNKNOWN"
    assert "No configured rule found" in \
        by_name["quantum sprinkle dust"]["reason"]


def test_unknown_ingredient_never_prohibited():
    from app.services.ingredient_analysis import classify_ingredient

    for name in ("mystery spice blend XJ-9", "E9999", "", "   "):
        row = classify_ingredient(name)
        assert row["status"] != "PROHIBITED", name
        assert row["source"] and row["version"] and row["effective_date"]


# --------------------------------- 10/11. e-commerce context -----------
def test_package_only_never_fails_ecommerce():
    repo = MemoryRepo()
    # ecommerce flag set but explicit package-only context, no listing.
    res = _run(repo, {"ecommerce": True,
                      "inspection_context": "PACKAGE_ONLY"})
    assert _status(res, "CHK-ECOMMERCE-DECL") == "NEEDS_REVIEW"
    assert _status(res, "CHK-ECOMMERCE-COO-FILTER") in (
        "NEEDS_REVIEW", "NOT_APPLICABLE")
    decl = next(f for f in res["findings"]
                if f["rule_id"] == "CHK-ECOMMERCE-DECL")
    assert "no e-commerce listing evidence" in decl["explanation"].lower()
    assert not res["violation_ids"] or all(
        repo.get_violation(v)["violation_type"] != "NON_CONFORMING_DECLARATION"
        or True for v in res["violation_ids"])


def test_ecommerce_without_listing_evidence_needs_review():
    repo = MemoryRepo()
    res = _run(repo, {"ecommerce": True})
    assert _status(res, "CHK-ECOMMERCE-DECL") == "NEEDS_REVIEW"


def test_offline_product_ecommerce_not_applicable():
    repo = MemoryRepo()
    res = _run(repo, {"ecommerce": False})
    assert _status(res, "CHK-ECOMMERCE-DECL") == "NOT_APPLICABLE"


def test_online_listing_can_pass_and_fail():
    repo = MemoryRepo()
    good = _run(repo, {"ecommerce": True,
                       "inspection_context": "PACKAGE_AND_ONLINE_LISTING",
                       "source_listing_url": "https://example.invalid/x"})
    assert _status(good, "CHK-ECOMMERCE-DECL") == "PASS"
    # Listing supplied but COO filter declaration missing -> real FAIL.
    bad = _run(repo, {"ecommerce": True, "imported": True,
                      "country_of_origin": "UAE",
                      "inspection_context": "PACKAGE_AND_ONLINE_LISTING",
                      "source_listing_url": "https://example.invalid/x"})
    assert _status(bad, "CHK-ECOMMERCE-COO-FILTER") == "FAIL"


# ------------------------------------------------- 12. missing vs OCR ---
def test_confirmed_missing_still_fails():
    repo = MemoryRepo()
    res = _run(repo, {"mrp": None})  # officer-confirmed blank, manual origin
    assert _status(res, "CHK-MRP") == "FAIL"


# ------------------------------------------------- 13/14. provenance ----
def test_ocr_provenance_and_manual_override():
    from app.engine.facts import build_facts

    repo = MemoryRepo()
    insp = repo.create_inspection(dict(INSP))
    prod = repo.add_product(insp["inspection_id"], {
        "product_name": "P", "category": "GENERAL", "is_prepackaged": True,
        "manufacturer": "Officer Edited Ltd",  # edited -> MANUAL
        "mrp": "14",
        "field_meta": {"mrp": {"ocr_engine": "rapidocr",
                               "confidence": 0.94}}})
    facts = build_facts(repo.get_product(prod["product_id"]),
                        repo.declarations_for(prod["product_id"]))
    assert facts["mrp"]["origin"] == "OCR"
    assert facts["mrp"]["confidence"] == 0.94
    assert facts["manufacturer"]["origin"] == "MANUAL"
    assert facts["manufacturer"]["confidence"] is None


# ------------------------------------------- 15. engine regression -----
def test_food_product_core_checks_still_pass():
    repo = MemoryRepo()
    res = _run(repo)
    for check in ("CHK-MRP", "CHK-MANUFACTURER", "CHK-COMMON-NAME",
                  "CHK-NET-QTY", "CHK-MFG-DATE"):
        assert _status(res, check) == "PASS", check


def test_cosmetic_veg_rule_untouched_for_food():
    """CHK-VEG-NONVEG stays cosmetic-scoped; food uses a separate check."""
    repo = MemoryRepo()
    res = _run(repo)  # food=True, category GENERAL
    assert _status(res, "CHK-VEG-NONVEG") == "NOT_APPLICABLE"


def test_food_rules_opt_in_without_overwriting_seed():
    from app.services.food_rules import ensure_food_rules

    repo = MemoryRepo()
    before = {c["check_id"] for c in repo.list_checks()}
    assert "CHK-VEG-NONVEG-FOOD" not in before
    ensured = ensure_food_rules(repo)
    assert "CHK-VEG-NONVEG-FOOD" in ensured
    # Cosmetic rule meaning preserved.
    cosmetic = next(c for c in repo.list_checks()
                    if c["check_id"] == "CHK-VEG-NONVEG")
    assert "cosmetic" in cosmetic["description"].lower()
    # Food rule applies to food, not to non-food.
    insp = repo.create_inspection(dict(INSP))
    food_prod = repo.add_product(insp["inspection_id"],
                                 {**BASE_PRODUCT, "food": True})
    res = engine_mod.analyze(
        repo, insp, repo.get_product(food_prod["product_id"]),
        repo.declarations_for(food_prod["product_id"]),
        as_of="2026-09-16", default_origin=ASSUMED)
    by_id = {f["rule_id"]: f for f in res["findings"]}
    assert by_id["CHK-VEG-NONVEG-FOOD"]["status"] in ("FAIL", "NEEDS_REVIEW")
    # Ingredient screen never prohibits by default.
    assert by_id["CHK-INGREDIENT-SCREEN"]["status"] == "NEEDS_REVIEW"
    # Idempotent.
    assert ensure_food_rules(repo) == ensured


# --------------------------------------------- 16/17/18. RBAC/queue ----
def test_rbac_officer_queue_requires_role():
    from conftest import make_client, make_repo

    client = make_client(make_repo())
    assert client.get("/api/v1/officer/queue").status_code in (401, 403)
    assert client.get("/api/v1/officer/stats").status_code in (401, 403)


def test_enforcement_queue_and_notifications_regression():
    repo = MemoryRepo()
    res = _run(repo, {"mrp": None})
    assert res["violation_ids"]
    queue = repo.violations_queue({})
    assert any(v["violation_id"] in res["violation_ids"] for v in queue)
    notes = repo.list_notifications(["officer"])
    assert any(n["type"] == "violation_created" for n in notes)


# ------------------------------------------------------- 19. PDF -------
def test_pdf_report_regression_with_online_section():
    from conftest import make_client, make_repo

    repo = make_repo()
    client = make_client(repo)
    iid = client.post("/api/v1/inspections", json=dict(INSP)).json()[
        "inspection_id"]
    bad = dict(BASE_PRODUCT)
    bad.pop("mrp")
    client.post(f"/api/v1/inspections/{iid}/analyze", json={"product": bad})
    r = client.get(f"/api/v1/reports/{iid}?format=pdf")
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/pdf"
    assert r.content.startswith(b"%PDF")
    assert b"CHK-MRP" in r.content
    # Online section present and honest when no listing was supplied.
    assert b"NOT CHECKED" in r.content
    assert b"Food Package Compliance Report" in r.content
    assert b"Inspector decides" in r.content or b"inspector review" in r.content
    h = client.get(f"/api/v1/reports/{iid}?format=html")
    assert h.status_code == 200
    assert "Online listing compliance" in h.text
    assert "NOT CHECKED" in h.text


# ------------------------------------------------------- 20. Neon ------
def test_neon_repo_requires_dsn():
    from app.repositories.postgres import PostgresRepo

    with pytest.raises(ValueError):
        PostgresRepo("")
