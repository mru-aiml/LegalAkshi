"""OCR speed + accuracy regressions: the real Maggi 70 g package case.

Every test here maps to an observed field failure:
  70 g ending up MANUAL, MRP "1010005", ingredient garbage
  ("transformpilates.in"), manufacturing-date FAIL on OCR uncertainty,
  10-minute two-image runs.

Staged pipeline, candidate scoring, validation gates and reconciliation
are exercised with deterministic mock providers (no model needed); one
test runs the real RapidOCR engine on a synthetic label.
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

# Maggi-like product: every declaration present and officer-confirmed.
MAGGI = {
    "product_name": "Maggi 2-Minute Noodles", "category": "GENERAL",
    "is_prepackaged": True, "manufacturer": "Nestle India Ltd",
    "quantity": 70, "quantity_unit": "g", "quantity_type": "weight",
    "manufacturing_date": "2024-05-01", "mrp": "50",
    "consumer_care": "1800-103-1947", "unit_sale_price": "Rs.714 per kg",
    "imported": False, "ecommerce": False, "food": True,
}


def _line(text: str, conf: float = 0.9, image: str = "front") -> OcrLine:
    return OcrLine(text=text, confidence=conf, box=None, image=image)


class _CountingProvider:
    """Mock provider: per-label lines + call counting for speed tests."""

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


# Maggi-style Stage-1 lines, split across front/back exactly as a two-photo
# inspection would deliver them.
MAGGI_LINES = {
    "front": [("MAGGI 2-Minute Noodles", 0.95),
              ("Masala", 0.9),
              ("NET WT 70 g", 0.93)],
    "back": [("Ingredients: Refined wheat flour (Maida) (68%), palm oil, "
              "salt, sugar, transformpilates.in mixed spices,", 0.75),
             ("MRP Rs. 50 (Incl. of all taxes)", 0.91),
             ("MFD 05/2024", 0.72),
             ("Best Before 9 months from manufacture", 0.7),
             ("FSSAI Lic No. 10012043001234", 0.88),
             ("Mfd. by Nestle India Limited, Moga, Punjab 142001", 0.8),
             ("Customer Care 1800-103-1947", 0.9),
             ("Batch No M50924A", 0.85)],
}


# ------------------------------------------------- 1. 70 g stays OCR ---
def test_maggi_70g_extracted_with_ocr_provenance():
    from app.services.ocr import service

    provider = _CountingProvider(MAGGI_LINES)
    out = service.extract_label_multi(
        [(_png_bytes(0), "front"),
         (_png_bytes(1), "back")],
        provider=provider)
    qty = out["fields_detailed"]["quantity"]
    assert qty["value"] == "70"
    assert qty["provenance"] == "OCR"
    assert qty["status"] == "DETECTED"
    assert qty["image"] == "front"  # NET WT 70 g lives on the front
    assert out["fields_detailed"]["unit"]["value"] == "g"
    # Exact extracted Maggi declarations.
    det = out["fields_detailed"]
    assert det["mrp"]["value"] == "50"
    assert det["fssai_license"]["value"] == "10012043001234"
    assert "Nestle" in (det["manufacturer"]["value"] or "")
    assert det["consumer_care"]["value"] == "1800-103-1947"
    assert det["manufacturing_date"]["value"] == "05/2024"
    assert det["batch_lot"]["value"] == "M50924A"
    assert "Refined wheat flour" in (
        out["food"]["fields"]["ingredients"]["raw_text"] or "")
    # No rotation fallback needed: everything resolved in fast passes.
    assert not any("rot90" in (r.get("variants") or [])
                   for r in out["images"])


# --------------------------------- 2-5. MRP false positives rejected ---
@pytest.mark.parametrize("texts", [
    ["MRP 1010005"],  # barcode digits merged beside the MRP anchor
    ["Rs. 1010005"],  # long anchored number: still not a price
    ["1010005"],  # bare long digits: never MRP
    ["MRP 18001031947"],  # phone digits beside the anchor
    ["FSSAI Lic No. 10012043001234", "MRP 10012043001234"],
])
def test_long_digit_strings_rejected_as_mrp(texts):
    from app.services.ocr.fields import extract_fields

    out = extract_fields([_line(t) for t in texts])
    assert out["mrp"]["value"] is None, texts


def test_maggi_mrp_rs50_accepted():
    from app.services.ocr.fields import extract_fields

    out = extract_fields([_line("MRP Rs. 50 (Incl. of all taxes)")])
    assert out["mrp"]["value"] == "50"


def test_fssai_number_is_not_mistaken_for_mrp():
    from app.services.ocr.fields import extract_fields

    out = extract_fields([_line("FSSAI Lic No. 10012043001234"),
                          _line("MRP Rs. 50")])
    assert out["mrp"]["value"] == "50"
    assert out["fssai_license"]["value"] == "10012043001234"


def test_phone_number_is_not_mistaken_for_mrp():
    from app.services.ocr.fields import extract_fields

    out = extract_fields([_line("Customer Care 1800-103-1947"),
                          _line("MRP Rs.50")])
    assert out["mrp"]["value"] == "50"
    assert out["consumer_care"]["value"] == "1800-103-1947"


# --------------------------------------- 6-7. ingredient garbage -------
def test_ingredient_garbage_cleaned_raw_preserved():
    from app.services.ocr.food import extract_food_label

    lines = [_line("Ingredients: Refined wheat flour (Maida), palm oil, "
                   "transformpilates.in salt, sugar,"),
             _line("8901058845123"),  # barcode digit spill
             _line("Refined wheat flour (Maida), palm oil,"),  # duplicate
             _line("Nutrition Information per 100g")]
    out = extract_food_label(lines)
    ing = out["fields"]["ingredients"]
    assert "transformpilates.in" in (ing["raw_text"] or "")  # audit trail
    assert "transformpilates" not in (ing["cleaned_text"] or "")
    assert "8901058845123" not in (ing["cleaned_text"] or "")
    assert "Refined wheat flour" in (ing["cleaned_text"] or "")
    assert ing["cleaning_notes"]  # cleaning is disclosed, never silent


# --------------------------------------- 8. unknown != prohibited -----
def test_maggi_unknown_ingredient_never_prohibited():
    from app.services.ingredient_analysis import analyze_ingredients

    out = analyze_ingredients(["Refined wheat flour (Maida)", "palm oil",
                               "transformpilates", "mixed spices"])
    assert all(r["status"] != "PROHIBITED" for r in out["ingredients"])


# --------------------------------- 9-10. date uncertainty, not FAIL ---
def test_officer_confirmed_missing_date_still_fails():
    # No OCR meta: the officer confirms the declaration is absent.
    res = _analyze({"manufacturing_date": None})
    assert _status(res, "CHK-MFG-DATE") == "FAIL"


def test_ocr_missed_date_becomes_needs_review():
    # OCR attempted the date but found nothing readable (NULL value +
    # measured zero confidence): uncertainty, not a violation.
    res = _analyze(
        {"manufacturing_date": None},
        field_meta={"mfg_month_year": {"ocr_engine": "rapidocr",
                                       "confidence": 0.0}})
    assert _status(res, "CHK-MFG-DATE") == "NEEDS_REVIEW"
    finding = next(f for f in res["findings"]
                   if f["rule_id"] == "CHK-MFG-DATE")
    assert "routed to inspector" in finding["explanation"]


def test_low_confidence_date_value_becomes_needs_review():
    res = _analyze(
        {"manufacturing_date": "05/2024"},
        field_meta={"mfg_month_year": {"ocr_engine": "rapidocr",
                                       "confidence": 0.3}})
    assert _status(res, "CHK-MFG-DATE") == "NEEDS_REVIEW"


# --------------------------------- 11-12. cross-image reconciliation --
def test_cross_image_agreement_keeps_best_confidence():
    from app.services.ocr import service

    provider = _CountingProvider({
        "front": [("70 g", 0.72)],
        "back": [("70 g", 0.94)],
    })
    out = service.extract_label_multi(
        [(_png_bytes(0), "front"),
         (_png_bytes(1), "back")],
        provider=provider)
    qty = out["fields_detailed"]["quantity"]
    assert qty["value"] == "70"
    assert qty["status"] == "DETECTED"
    assert qty["confidence"] == 0.94
    assert len(qty["sources"]) == 2  # both evidence sources retained


def test_cross_image_conflict_becomes_needs_review():
    from app.services.ocr import service

    provider = _CountingProvider({
        "front": [("MRP Rs.50", 0.9)],
        "back": [("MRP Rs.80", 0.88)],
    })
    out = service.extract_label_multi(
        [(_png_bytes(0), "front"),
         (_png_bytes(1), "back")],
        provider=provider)
    mrp = out["fields_detailed"]["mrp"]
    assert mrp["status"] == "NEEDS_REVIEW"
    assert mrp["value"] is None  # no automatic winner
    assert {c["value"] for c in mrp["candidates"]} == {"50", "80"}


# --------------------------------- 13-14. speed: staged, singleton ---
def test_staged_pipeline_single_pass_when_complete():
    from app.services.ocr import service

    provider = _CountingProvider({
        "front": [("Maggi 2-Minute Noodles", 0.95), ("NET WT 70 g", 0.93)],
        "back": [("MRP Rs. 50", 0.91), ("MFD 05/2024", 0.9)],
    })
    t0 = time.perf_counter()
    out = service.extract_label_multi(
        [(_png_bytes(0), "front"),
         (_png_bytes(1), "back")],
        provider=provider)
    elapsed = time.perf_counter() - t0
    # Old pipeline: 4 full-frame variants x 2 images = 8 provider calls.
    # Staged: exactly one fast pass per image when Stage 1 resolves all.
    assert len(provider.calls) == 2, provider.calls
    assert out["timings"]["provider_calls"] == 2
    assert all("rot90" not in c for c in provider.calls)
    assert elapsed < 20  # mock-speed sanity bound for the unit path
    assert out["timings"]["total_ms"] is not None
    assert set(out["timings"]["images"]) == {"front", "back"}


def test_targeted_region_only_for_unresolved_fields():
    from app.services.ocr import service

    # MRP context visible but amount unreadable in Stage 1 -> one targeted
    # region call; quantity/date already resolved -> no regions for them.
    provider = _CountingProvider({
        "front": [("NET WT 70 g", 0.9), ("MFD 05/2024", 0.9),
                  ("MRP", 0.5)],
    })
    out = service.extract_label_multi([(_png_bytes(), "front")],
                                      provider=provider)
    region_calls = [c for c in provider.calls if "region:" in c]
    assert len(region_calls) <= 2, provider.calls
    assert any(c.startswith("front:stage1") for c in provider.calls)


def test_rapidocr_singleton_reused_across_passes():
    import sys
    import types

    from app.services.ocr.rapidocr_provider import RapidOCRProvider

    created = []

    class _FakeEngine:
        def __init__(self):
            created.append(self)

        def __call__(self, image_np):
            return []

    module = types.ModuleType("rapidocr_onnxruntime")
    module.RapidOCR = _FakeEngine
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setitem(sys.modules, "rapidocr_onnxruntime", module)
    RapidOCRProvider._engine = None
    try:
        first = RapidOCRProvider()._engine_or_raise()
        second = RapidOCRProvider()._engine_or_raise()
        assert first is second  # initialised once, reused everywhere
        assert len(created) == 1
    finally:
        monkeypatch.undo()
        RapidOCRProvider._engine = None


def test_real_rapidocr_staged_on_synthetic_label():
    pytest.importorskip("rapidocr_onnxruntime")
    from app.services.ocr import service

    from PIL import Image, ImageDraw

    img = Image.new("RGB", (900, 300), "white")
    ImageDraw.Draw(img).text((40, 100), "MRP Rs.250", fill="black")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    t0 = time.perf_counter()
    out = service.extract_label(buf.getvalue(), None)
    elapsed = time.perf_counter() - t0
    assert out["status"] == "OK"
    assert out["fields"]["mrp"]["value"] == "250"
    assert out["fields"]["mrp"]["provenance"] == "OCR"
    assert out["timings"]["provider_calls"] <= 3
    assert elapsed < 300  # documents the seconds-scale staged budget
