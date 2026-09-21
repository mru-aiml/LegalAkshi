"""OCR tests — deterministic field extraction on fixture lines (no OCR
needed) plus provider/service integration, provenance override, failure
handling, and RBAC. One real RapidOCR test on a synthetic label.
"""
from __future__ import annotations

import io

import pytest
from fastapi.testclient import TestClient

from app.services.ocr.base import OcrLine, OcrOutput
from app.services.ocr.fields import FIELD_KEYS, extract_fields

CONSUMER = {"X-LegalAkshi-Role": "consumer", "X-LegalAkshi-User": "C-9"}


def _line(text: str, conf: float = 0.9) -> OcrLine:
    return OcrLine(text=text, confidence=conf, box=None, image="front")


# ------------------------------------------------------- deterministic ---
def test_field_keys_stable():
    assert set(FIELD_KEYS) == {
        "product_name", "common_generic_name", "manufacturer",
        "quantity", "unit", "mrp", "batch_lot", "manufacturing_date",
        "best_before", "use_by", "fssai_license", "consumer_care",
        "country_of_origin", "unit_sale_price"}


@pytest.mark.parametrize("text,expected", [
    ("MRP Rs.250", "250"),
    ("MRP ₹1,999.00", "1999.00"),
    ("Maximum Retail Price Rs 450", "450"),
    ("Rs. 99 only", "99"),
    ("Price: 250", None),  # no MRP context -> null, never fabricated
    ("250", None),
])
def test_mrp_extraction(text, expected):
    assert extract_fields([_line(text)])["mrp"]["value"] == expected


@pytest.mark.parametrize("text,qty,unit", [
    ("Net Qty 5 kg", "5", "kg"),
    ("NET WT. 200 g", "200", "g"),
    ("500 ml", "500", "ml"),
    ("Pack of 10", None, None),  # no unit -> null
])
def test_quantity_extraction(text, qty, unit):
    out = extract_fields([_line(text)])
    assert out["quantity"]["value"] == qty
    assert (out["unit"]["value"] == unit) if qty else True


@pytest.mark.parametrize("text,expected", [
    ("MFD 05/2024", "05/2024"),
    ("Mfg Date: 12-2023", "12-2023"),
    ("Manufactured on 04/06/2024", "04/06/2024"),
    ("05/2024", None),  # datelike but no manufacturing context
])
def test_date_extraction(text, expected):
    assert extract_fields([_line(text)])["manufacturing_date"]["value"] == expected


def test_best_before_and_use_by():
    out = extract_fields([_line("Best before 6 months from MFD 05/2024")])
    assert out["best_before"]["value"] == "05/2024"
    out = extract_fields([_line("Use by 01/2025")])
    assert out["use_by"]["value"] == "01/2025"


@pytest.mark.parametrize("text,expected", [
    ("FSSAI Lic No. 10012011000123", "10012011000123"),
    ("8901234567890", None),  # bare 14 digits (barcode?) -> null
    ("FSSAI 12345", None),  # too short -> null
])
def test_fssai_extraction(text, expected):
    assert extract_fields([_line(text)])["fssai_license"]["value"] == expected


@pytest.mark.parametrize("text,expected", [
    ("Customer Care 1800-123-456", "1800-123-456"),
    ("Call +91 9876543210", "+91 9876543210"),
    ("Toll free 1800123456", "1800123456"),
    ("9876543210", None),  # no care context -> null
])
def test_phone_extraction(text, expected):
    got = extract_fields([_line(text)])["consumer_care"]["value"]
    assert (got or "").replace(" ", "") == (expected or "").replace(" ", "")


def test_batch_manufacturer_country():
    out = extract_fields([_line("Batch No AS240514B"),
                          _line("Manufactured by ITC Limited, Kolkata"),
                          _line("Country of Origin: Thailand")])
    assert out["batch_lot"]["value"] == "AS240514B"
    assert out["manufacturer"]["value"] == "ITC Limited, Kolkata"
    assert out["country_of_origin"]["value"] == "Thailand"


def test_missing_fields_remain_null():
    out = extract_fields([_line("Tasty Biscuits")])
    for key in ("mrp", "quantity", "manufacturing_date", "fssai_license",
                "consumer_care", "batch_lot", "manufacturer"):
        assert out[key]["value"] is None, key
    assert out["product_name"]["value"] == "Tasty Biscuits"
    # Stage-1D: the generic name is distinguished from the product name
    # when a category word is present ("Biscuits"), else mirrors it.
    assert out["common_generic_name"]["value"] == "Biscuits"


def test_hits_carry_ocr_provenance_confidence():
    out = extract_fields([_line("MRP Rs.250", 0.82)])
    assert out["mrp"]["confidence"] == 0.82


# ------------------------------------------------------------- service ---
class _MockProvider:
    name = "mock-ocr"

    def __init__(self, lines=None, fail=False):
        self._lines = lines or []
        self._fail = fail

    def extract(self, image_np, image_side):
        from app.services.ocr.base import OcrError

        if self._fail:
            raise OcrError("mock failure")
        return OcrOutput(lines=[OcrLine(text=t, confidence=c, image=image_side)
                                for t, c in self._lines],
                         engine=self.name)


def test_service_combines_front_back():
    from app.services.ocr import service

    out = service.extract_label(_png_bytes("front"), _png_bytes("back"),
                                provider=_MockProvider([("MRP Rs.250", 0.9)]))
    assert out["status"] == "OK"
    assert out["engine"] == "mock-ocr"
    assert out["fields"]["mrp"] == {"value": "250", "provenance": "OCR",
                                    "confidence": 0.9}


def test_service_failure_becomes_needs_review():
    from app.services.ocr import service

    out = service.extract_label(_png_bytes("front"), None,
                                provider=_MockProvider(fail=True))
    assert out["status"] == "NEEDS_REVIEW"
    assert all(f["value"] is None for f in out["fields"].values())
    assert out["errors"]


def test_corrupt_bytes_never_crash():
    from app.services.ocr import service

    # Undecodable bytes fail in preprocessing (before any provider runs).
    out = service.extract_label(b"\x00\x01not-a-png", None,
                                provider=_MockProvider([("hello", 0.5)]))
    assert out["status"] == "NEEDS_REVIEW"
    assert all(f["value"] is None for f in out["fields"].values())


def test_real_decode_failure_shape():
    from app.services.ocr import service

    out = service.extract_label(b"\x00\x01not-a-png", None)
    assert out["status"] == "NEEDS_REVIEW"
    assert all(f["value"] is None for f in out["fields"].values())


# ------------------------------------------------- provenance override ---
def test_manual_edit_overrides_ocr_value():
    """Reviewed declaration wins: OCR mrp kept, edited manufacturer MANUAL."""
    from app.engine.facts import build_facts
    from app.repositories.memory import MemoryRepo

    repo = MemoryRepo()
    insp = repo.create_inspection({"inspector_id": "T", "inspector_name": "T",
                                   "business_name": "B",
                                   "inspection_date": "2026-09-15"})
    prod = repo.add_product(insp["inspection_id"], {
        "product_name": "P", "category": "GENERAL", "is_prepackaged": True,
        "manufacturer": "ITC Limited, Kolkata",  # officer-edited review value
        "mrp": "250",  # untouched OCR value
        "field_meta": {"mrp": {"ocr_engine": "rapidocr", "confidence": 0.94}}})
    facts = build_facts(repo.get_product(prod["product_id"]),
                        repo.declarations_for(prod["product_id"]))
    assert facts["mrp"]["value"] == 250
    assert facts["mrp"]["confidence"] == 0.94
    assert facts["manufacturer"]["value"] == "ITC Limited, Kolkata"
    assert facts["manufacturer"]["confidence"] is None  # never fabricated


def test_column_mirror_rows_carry_ocr_provenance():
    """Unedited column fields (manufacturer/quantity/dates) get mirror
    declaration rows with OCR provenance — same reviewed value, no dupes."""
    from app.engine.facts import build_facts
    from app.repositories.memory import MemoryRepo

    repo = MemoryRepo()
    insp = repo.create_inspection({"inspector_id": "T", "inspector_name": "T",
                                   "business_name": "B",
                                   "inspection_date": "2026-09-15"})
    prod = repo.add_product(insp["inspection_id"], {
        "product_name": "P", "category": "GENERAL", "is_prepackaged": True,
        "manufacturer": "ITC Limited",  # unedited OCR value
        "quantity": 5, "quantity_unit": "kg",
        "field_meta": {
            "manufacturer": {"ocr_engine": "rapidocr", "confidence": 0.91},
            "net_quantity": {"ocr_engine": "rapidocr", "confidence": 0.88}}})
    decls = repo.declarations_for(prod["product_id"])
    by_field = {d["field_name"]: d for d in decls}
    assert by_field["manufacturer"]["ocr_engine"] == "rapidocr"
    assert by_field["manufacturer"]["extracted_value"] == "ITC Limited"
    assert by_field["net_quantity"]["confidence"] == 0.88
    facts = build_facts(repo.get_product(prod["product_id"]), decls)
    assert facts["manufacturer"]["origin"] == "OCR"
    assert facts["manufacturer"]["confidence"] == 0.91


def test_reviewed_declaration_reaches_engine():
    from app.engine import engine as engine_mod
    from app.repositories.memory import MemoryRepo

    repo = MemoryRepo()
    insp = repo.create_inspection({"inspector_id": "T", "inspector_name": "T",
                                   "business_name": "B",
                                   "inspection_date": "2026-09-15"})
    prod = repo.add_product(insp["inspection_id"], {
        "product_name": "P", "category": "GENERAL", "is_prepackaged": True,
        "manufacturer": "ITC Limited, Kolkata",
        "quantity": 5, "quantity_unit": "kg", "quantity_type": "weight",
        "manufacturing_date": "2024-05-01", "consumer_care": "1800-1",
        "unit_sale_price": "Rs.50 per kg", "mrp": "250",
        "field_meta": {"mrp": {"ocr_engine": "rapidocr", "confidence": 0.94}}})
    pid = prod["product_id"]
    res = engine_mod.analyze(repo, insp, repo.get_product(pid),
                             repo.declarations_for(pid), as_of="2026-09-15",
                             default_origin="MANUAL")
    mrp = next(f for f in res["findings"] if f["rule_id"] == "CHK-MRP")
    assert mrp["status"] == "PASS"
    assert mrp["evidence"]["detected_value"] == "250"
    assert mrp["evidence"]["confidence"] == 0.94
    assert mrp["evidence"]["confidence_origin"] == "OCR"
    mfr = next(f for f in res["findings"] if f["rule_id"] == "CHK-MANUFACTURER")
    assert "Kolkata" in (mfr["evidence"]["detected_value"] or "")
    assert mfr["evidence"]["confidence_origin"] == "MANUAL"


# ------------------------------------------------------------ endpoint ---
def _client():
    from conftest import make_client, make_repo

    return make_client(make_repo())


def _png_bytes(text: str) -> bytes:
    from PIL import Image, ImageDraw

    img = Image.new("RGB", (900, 300), "white")
    ImageDraw.Draw(img).text((40, 100), text, fill="black")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def test_ocr_endpoint_requires_auth():
    assert _client().post("/api/v1/ocr/extract").status_code == 401


def test_ocr_endpoint_validates_input():
    c = _client()
    assert c.post("/api/v1/ocr/extract", headers=CONSUMER).status_code == 422
    big = {"front_image": ("x.png", b"0" * (10 * 1024 * 1024 + 1), "image/png")}
    assert c.post("/api/v1/ocr/extract", files=big,
                  headers=CONSUMER).status_code == 422


def test_ocr_endpoint_mock_provider(monkeypatch):
    from app.services.ocr.base import OcrOutput
    from app.services.ocr import service as svc

    def fake(front, back, provider=None):
        return {"status": "OK", "engine": "mock-ocr", "front": {}, "back": {},
                "fields": {"mrp": {"value": "250", "provenance": "OCR",
                                   "confidence": 0.9}}, "errors": []}

    monkeypatch.setattr(svc, "extract_label", fake)
    r = _client().post("/api/v1/ocr/extract",
                       files={"front_image": ("f.png", _png_bytes("x"),
                                              "image/png")},
                       headers=CONSUMER)
    assert r.status_code == 200
    assert r.json()["fields"]["mrp"]["value"] == "250"


def test_real_rapidocr_on_synthetic_label():
    pytest.importorskip("rapidocr_onnxruntime")
    from app.services.ocr import service

    img_bytes = _png_bytes("MRP Rs.250")
    out = service.extract_label(img_bytes, None)
    assert out["status"] == "OK"
    assert out["fields"]["mrp"]["value"] == "250"
    assert out["fields"]["mrp"]["provenance"] == "OCR"
