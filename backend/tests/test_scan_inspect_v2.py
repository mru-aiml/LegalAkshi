"""Scan & Inspect V2: layout regions, CONTENTS gating, fallback rotation,
field evidence model, report robustness, suggestions startup check.

 Budgets from the previous pass still hold (asserted here against the
 new code paths): <=5 provider calls/image, Tesseract <=1, exactly one
 full-page Stage-1 pass per image.
"""
from __future__ import annotations

import pytest

from app.services.ocr.base import OcrLine
from app.services.ocr import service as svc


def _box(x0, y0, x1, y1):
    return [[x0, y0], [x1, y0], [x1, y1], [x0, y1]]


def _ln(text, x0, y0, x1, y1, conf=0.9, image="back"):
    return OcrLine(text=text, confidence=conf, box=_box(x0, y0, x1, y1),
                   image=image)


@pytest.fixture()
def panel_lines():
    return [
        _ln("INGREDIENTS: Refined Wheat Flour,", 60, 60, 700, 92),
        _ln("Sugar, Salt, Palm Oil, INS 500(ii).", 60, 100, 700, 132),
        _ln("Nutrition Information per 100g", 60, 300, 500, 332),
        _ln("Energy 450 kcal   Protein 8 g", 60, 340, 500, 372),
        _ln("MRP Rs. 50 (Incl. of all taxes)", 720, 60, 1200, 92),
        _ln("MFD 05/2024", 720, 100, 1000, 132),
    ]


# ------------------------------------------------- layout (Part I) ---
def test_layout_panels_from_headings(panel_lines):
    from app.services.ocr import opencv_preprocessor as ocv

    layout = ocv.detect_layout(panel_lines)
    kinds = {r["kind"] for r in layout["regions"]}
    assert "INGREDIENTS" in kinds
    assert "NUTRITION" in kinds
    assert "MRP" in kinds
    for r in layout["regions"]:
        assert set(("kind", "rect", "confidence", "reason")) <= set(r)
        x0, y0, x1, y1 = r["rect"]
        assert x1 > x0 and y1 > y0


def test_layout_product_brand_hints():
    from app.services.ocr import opencv_preprocessor as ocv

    lines = [_ln("DEMO BRAND", 200, 20, 700, 90),
             _ln("Tasty Biscuits 100 g", 200, 110, 700, 140),
             _ln("Ingredients: wheat, sugar.", 60, 400, 700, 432)]
    kinds = {r["kind"] for r in ocv.detect_layout(lines)["regions"]}
    assert "PRODUCT" in kinds
    assert "BRAND" in kinds


def test_layout_no_lines_no_regions():
    from app.services.ocr import opencv_preprocessor as ocv

    assert ocv.detect_layout([]) == {"regions": [],
                                     "notes": ["no boxed lines for layout"]}


def test_merge_layout_tightens_never_widens(panel_lines):
    from app.services.ocr import opencv_preprocessor as ocv

    layout = ocv.detect_layout(panel_lines)
    assert "INGREDIENTS" in {r["kind"] for r in layout["regions"]}
    wide = {"kind": "ingredients", "rect": (0.0, 60.0, 1280.0, 500.0),
            "prep": "ingredients"}
    merged = svc._merge_layout_regions([dict(wide)], layout)
    ing = [r for r in merged if r["kind"] == "ingredients"]
    assert len(ing) == 1
    nx0, _, nx1, _ = ing[0]["rect"]
    assert nx0 >= 0.0 and nx1 <= 1280.0
    assert (nx1 - nx0) < 1280.0  # column evidence tightened the band
    assert "layout_refined" in ing[0]
    # The heading-anchored nutrition panel joins as one targeted region.
    assert [r["kind"] for r in merged].count("nutrition") == 1


def test_merge_layout_adds_nutrition_once(panel_lines):
    from app.services.ocr import opencv_preprocessor as ocv

    layout = {"regions": [{"kind": "NUTRITION", "rect": (60.0, 300.0,
                                                         500.0, 500.0),
                           "confidence": 0.8, "reason": "t"}],
              "notes": []}
    merged = svc._merge_layout_regions([], layout)
    assert [r["kind"] for r in merged] == ["nutrition"]
    assert merged[0]["prep"] == "smallprint"
    again = svc._merge_layout_regions(merged, layout)
    assert len(again) == 1  # never duplicated


def test_merge_layout_low_confidence_ignored():
    merged = svc._merge_layout_regions(
        [], {"regions": [{"kind": "NUTRITION", "rect": (0, 0, 10, 10),
                          "confidence": 0.2, "reason": "t"}], "notes": []})
    assert merged == []


# --------------------------------------- fallback rotation (Part E) ---
def test_fallback_rotation_legacy_default():
    assert svc.choose_fallback_rotation([], None) == 90
    assert svc.choose_fallback_rotation([], {"decisive": False}) == 90


def test_fallback_rotation_upside_down_evidence():
    cv = {"transpose": False, "decisive": True, "ratio": 0.3}
    assert svc.choose_fallback_rotation([], cv) == 180


def test_fallback_rotation_transpose_evidence():
    cv = {"transpose": True, "decisive": True, "ratio": 3.0}
    assert svc.choose_fallback_rotation([], cv) == 90


def test_fallback_rotation_enough_lines_skips():
    lines = [_ln(f"row {i}", 0, i * 20, 300, i * 20 + 15) for i in range(8)]
    assert svc.choose_fallback_rotation(lines, None) == 0


# --------------------------------------- CONTENTS gating (Part J) ---
@pytest.mark.parametrize("text,context,expected", [
    ("CONTENTS:", None, False),
    ("CONTENTS", None, False),
    ("Contents: Wheat Flour, Sugar, Salt", None, True),
    ("Contents: Milk, Soy", None, False),
    ("Contains: Milk, Soy", None, False),
    ("Contains: Refined oil, salt, spices", None, True),
    ("MADE FROM wheat flour", None, True),
    ("CONTENTS", "wheat flour, sugar, salt, palm oil", True),
    ("CONTENTS", "table of contents page 1 2 3", False),
    ("Net contents 70 g", None, False),
])
def test_contents_needs_evidence(text, context, expected):
    from app.services.ocr.food import _is_ingredient_heading

    assert _is_ingredient_heading(text, context) is expected, text


def test_allergen_shape_never_heading():
    from app.services.ocr.food import match_section_boundary

    assert match_section_boundary("Contains: Milk, Soy") == "CONTAINS"
    assert match_section_boundary("Contents: Milk, Soy") == "CONTAINS"


def test_strong_heading_unaffected_by_context():
    from app.services.ocr.food import _is_ingredient_heading

    assert _is_ingredient_heading("INGREDIENTS:") is True
    assert _is_ingredient_heading("INGREDIENTS / CONTENTS") is True
    assert _is_ingredient_heading("COMPOSITON") is True
    assert _is_ingredient_heading("Nutrition Information") is False


# --------------------------------------- field evidence (Part R) ---
@pytest.mark.parametrize("key", ["product_name", "quantity", "mrp",
                                 "manufacturing_date", "best_before",
                                 "fssai_license", "manufacturer",
                                 "consumer_care", "ingredients", "nutrition",
                                 "batch_lot"])
def test_field_evidence_shape(key):
    """Every important field's contract carries the evidence model."""
    from app.services.ocr import fields as fields_mod

    assert key in fields_mod.FIELD_KEYS or key in ("ingredients",
                                                   "nutrition")


def test_fields_detailed_carries_boxes_and_status():
    from app.services.ocr.base import OcrLine as L
    from app.services.ocr.fields import extract_fields_detailed

    lines = [L(text="MRP Rs. 50 (Incl. of all taxes)", confidence=0.9,
               box=_box(10, 10, 200, 30), image="back")]
    out = extract_fields_detailed(lines)
    hit = out["mrp"]
    assert hit["value"] == "50"
    assert hit["box"] == _box(10, 10, 200, 30)
    assert hit["image"] == "back"


# --------------------------------------- OCR-error tolerance ---
@pytest.mark.parametrize("text,expected", [
    ("MRF Rs. 50", "50"),  # damaged MRP word, anchored amount
    ("MPP Rs. 50", "50"),
    ("R5 50", "50"),  # damaged Rs anchor
    ("MFD 05/24", "05/24"),  # short month/year beside context
    ("Mfd. by Demo Foods Ltd.", "Demo Foods Ltd."),
    ("Mkt by Demo Foods Ltd.", "Demo Foods Ltd."),
    ("Manufactured for Demo Foods", "Demo Foods"),
    ("FSSA Lic N0. 10012043001234", "10012043001234"),
    ("Customer Care l800-103-1947", "l800-103-1947"),
])
def test_ocr_damage_tolerance(text, expected):
    from app.services.ocr.base import OcrLine as L
    from app.services.ocr.fields import extract_fields

    out = extract_fields([L(text=text, confidence=0.7, image="back")])
    found = expected in str({k: v["value"]
                             for k, v in out.items()}.values())
    assert found, (text, out)


def test_damage_tolerance_never_invents():
    from app.services.ocr.base import OcrLine as L
    from app.services.ocr.fields import extract_fields

    # Damaged anchors without real values stay empty.
    out = extract_fields([L(text="MRF", confidence=0.7, image="back")])
    assert out["mrp"]["value"] is None
    out = extract_fields([L(text="FSSA Lic", confidence=0.7, image="back")])
    assert out["fssai_license"]["value"] is None
    out = extract_fields([L(text="05/24", confidence=0.7, image="back")])
    assert out["manufacturing_date"]["value"] is None


# --------------------------------------- diagnostics (Parts B/D) ---
def test_db_calls_header_present():
    from conftest import make_client, make_repo

    res = make_client(make_repo()).get("/api/v1/inspections")
    assert res.status_code == 200
    assert res.headers.get("x-request-duration-ms") is not None
    assert res.headers.get("x-db-calls") == "0"  # memory repo: no SQL


def test_dbmetrics_counter_semantics():
    from app.core import dbmetrics as m

    m.reset()
    assert m.count() == 0
    m.increment()
    m.increment()
    assert m.count() == 2
    m.reset()
    assert m.count() == 0


# --------------------------------------- budgets preserved ---
def test_call_budgets_unchanged():
    assert svc.MAX_PROVIDER_CALLS_PER_IMAGE == 5
    assert svc.MAX_TESSERACT_CALLS_PER_IMAGE == 1
    assert svc.MAX_TARGETED_CALLS_PER_IMAGE == 3
    assert svc.INGREDIENT_VARIANT_BUDGET == 2


def test_variant_order_respects_budget():
    from app.services.ocr import opencv_preprocessor as ocv

    for quality in (None, {"sharpness": {"grade": "POOR"}},
                    {"brightness": {"grade": "POOR"}}):
        order = ocv.choose_variant("ingredients", quality)
        assert len(order[:svc.INGREDIENT_VARIANT_BUDGET]) <= \
            svc.INGREDIENT_VARIANT_BUDGET


# --------------------------------------- Part A: startup check ---
def test_startup_table_check_never_raises():
    from app.factory import _check_app_tables, create_app
    from conftest import make_repo

    repo = make_repo()
    _check_app_tables(repo)  # must not raise on a healthy repo

    class _Broken:
        def list_suggestions(self, *a, **k):
            raise RuntimeError("relation missing")

        def list_complaints(self, *a, **k):
            raise RuntimeError("relation missing")

    _check_app_tables(_Broken())  # missing tables warn, never raise
    create_app(repo)  # factory path includes the check


# --------------------------------------- Part V: report robustness ---
def _minimal_inspection(repo, product_extra=None):
    from app.engine import engine as engine_mod
    from app.engine.facts import ASSUMED

    insp = repo.create_inspection(
        {"inspector_id": "R-1", "inspector_name": "R",
         "business_name": "Shop", "inspection_date": "2026-09-15"})
    payload = {"product_name": "Robust Goods", "category": "GENERAL",
               "is_prepackaged": True}
    payload.update(product_extra or {})
    prod = repo.add_product(insp["inspection_id"], payload)
    pid = prod["product_id"]
    engine_mod.analyze(repo, insp, repo.get_product(pid),
                       repo.declarations_for(pid),
                       as_of="2026-09-15", default_origin=ASSUMED)
    return insp, prod


@pytest.mark.parametrize("extra", [
    {},  # everything optional missing
    {"mrp": None, "manufacturing_date": None},
    {"quantity": None, "ingredients_raw": None},
])
def test_report_json_missing_optional_fields(extra):
    from conftest import make_client, make_repo

    repo = make_repo()
    insp, _ = _minimal_inspection(repo, extra)
    body = make_client(repo).get(
        f"/api/v1/reports/{insp['inspection_id']}").json()
    assert body["inspection_id"] == insp["inspection_id"]
    assert body["findings"]
    assert body["score"]["out_of"] == 100


def test_report_pdf_no_nutrition_no_ingredients():
    from conftest import make_client, make_repo

    repo = make_repo()
    insp, _ = _minimal_inspection(repo)
    res = make_client(repo).get(
        f"/api/v1/reports/{insp['inspection_id']}?format=pdf")
    assert res.status_code == 200
    assert res.headers["content-type"] == "application/pdf"
    assert len(res.content) > 1000


def test_report_html_missing_fields():
    from conftest import make_client, make_repo

    repo = make_repo()
    insp, _ = _minimal_inspection(repo, {"mrp": None})
    res = make_client(repo).get(
        f"/api/v1/reports/{insp['inspection_id']}?format=html")
    assert res.status_code == 200
    assert "Robust Goods" in res.text
