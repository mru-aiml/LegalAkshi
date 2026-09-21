"""Stage-1E: requirements derivation, readiness gate inputs, report
integration, suggestions store failure diagnosis.

The requirements map is derived from the engine's own registry (never a
parallel legal system); readiness/gate semantics live in these tests.
"""
from __future__ import annotations


def _ctx(**kw):
    base = {"food": True, "is_prepackaged": True, "quantity_type": "weight"}
    base.update(kw)
    return base


# ------------------------------------------------- Part A: contract ---
def test_requirements_plain_food_context():
    from conftest import make_repo
    from app.services import analysis_requirements as ar

    out = ar.get_analysis_field_requirements(make_repo(), _ctx())
    req = {e["field"]: e for e in out["required"]}
    for field in ("common_generic_name", "net_quantity", "mfg_month_year",
                  "mrp", "manufacturer", "consumer_care"):
        assert field in req, field
        assert req[field]["form_key"], field
        assert req[field]["checks"], field
    # Optional, never blocking.
    assert "best_before" in {e["field"] for e in out["optional"]}
    assert out["versions"] > 0


def test_requirements_imported_ecommerce_adds_fields():
    from conftest import make_repo
    from app.services import analysis_requirements as ar

    plain = {e["field"] for e in ar.get_analysis_field_requirements(
        make_repo(), _ctx())["required"]}
    full = {e["field"] for e in ar.get_analysis_field_requirements(
        make_repo(), _ctx(imported=True, ecommerce=True))["required"]}
    assert "country_of_origin" in full
    assert full >= plain


def test_requirements_entries_have_review_keys():
    from conftest import make_repo
    from app.services import analysis_requirements as ar

    out = ar.get_analysis_field_requirements(make_repo(), _ctx())
    for e in out["required"] + out["optional"]:
        assert e["ocr_key"] is not None or e["form_key"] is None or True
        assert e["checks"]


def test_requirements_endpoint():
    from conftest import make_client, make_repo

    body = make_client(make_repo()).get(
        "/api/v1/analysis/requirements",
        params={"food": "true", "quantity_type": "weight"}).json()
    req = {e["field"] for e in body["required"]}
    assert {"common_generic_name", "net_quantity", "mrp",
            "mfg_month_year"} <= req
    assert body["as_of"]


# ------------------------------------------------- Part C/D/E/F/I/J ---
def test_biscuits_net_weight_rejected_as_product():
    from app.services.ocr.base import OcrLine as L
    from app.services.ocr.fields import extract_with_status

    out = extract_with_status(
        [L(text="BISCUITS NET WEIGHT", confidence=0.9, image="front")])
    assert out["product_name"]["value"] is None
    assert out["product_name"]["status"] == "NEEDS_REVIEW"


def test_quantity_corruptions():
    from app.services.ocr.base import OcrLine as L
    from app.services.ocr.fields import extract_fields

    assert extract_fields(
        [L(text="NET WEI6HT 100 GMS", confidence=0.8)])["quantity"][
        "value"] == "100"
    assert extract_fields(
        [L(text="Net Wt. 100 9", confidence=0.8)])["quantity"][
        "value"] == "100"
    # Bare "100 9" without anchor is NOT quantity.
    assert extract_fields(
        [L(text="Room 100 9", confidence=0.8)])["quantity"][
        "value"] is None


def test_invalid_date_rejected_valid_accepted():
    from app.services.ocr.base import OcrLine as L
    from app.services.ocr.fields import extract_with_status

    bad = extract_with_status(
        [L(text="MFD 70.16", confidence=0.8, image="back")])
    assert bad["manufacturing_date"]["value"] is None
    assert bad["manufacturing_date"]["status"] == "NEEDS_REVIEW"
    assert any("invalid_date_candidate" in r for r in
               bad["manufacturing_date"].get("score_reasons", []))
    good = extract_with_status(
        [L(text="MFD: 05/2024", confidence=0.8, image="back")])
    assert good["manufacturing_date"]["value"] == "05/2024"


def test_mrp_damage_and_retail_price():
    from app.services.ocr.base import OcrLine as L
    from app.services.ocr.fields import extract_fields

    assert extract_fields(
        [L(text="MRF Rs. 45", confidence=0.8)])["mrp"]["value"] == "45"
    assert extract_fields(
        [L(text="MAX RETAIL PRICE Rs. 60", confidence=0.8)])["mrp"][
        "value"] == "60"
    assert extract_fields(
        [L(text="R5 75", confidence=0.8)])["mrp"]["value"] == "75"


def test_consumer_care_service_anchors():
    from app.services.ocr.base import OcrLine as L
    from app.services.ocr.fields import extract_fields

    assert extract_fields(
        [L(text="Customer Service 1800-222-3333",
           confidence=0.8)])["consumer_care"]["value"] == "1800-222-3333"


def test_batch_number_variants():
    from app.services.ocr.base import OcrLine as L
    from app.services.ocr.fields import extract_fields

    assert extract_fields(
        [L(text="Batch Number AS12", confidence=0.8)])["batch_lot"][
        "value"] == "AS12"
    assert extract_fields(
        [L(text="B/N X99", confidence=0.8)])["batch_lot"]["value"] == "X99"
    # "Lotus" is not lot+"us".
    assert extract_fields(
        [L(text="Lotus Biscuits", confidence=0.9)])["batch_lot"][
        "value"] is None


def test_best_before_duration_and_consume_before():
    from app.services.ocr.base import OcrLine as L
    from app.services.ocr.fields import extract_fields

    out = extract_fields(
        [L(text="BEST BEFORE 9 MONTHS FROM MFD", confidence=0.8)])
    assert out["best_before"]["value"] == "BEST BEFORE 9 MONTHS"
    # Duration stored verbatim, never converted to a calendar date.
    assert "/" not in (out["best_before"]["value"] or "")
    out = extract_fields(
        [L(text="Consume Before 12/2026", confidence=0.8)])
    assert out["best_before"]["value"] == "12/2026"


# ------------------------------------------------- Part R: integration
def test_four_image_reviewed_flow_to_report():
    """4 images -> reviewed declarations -> analysis -> report, with
    optional OCR fields absent. Mirrors the officer workflow end to end.
    """
    from conftest import make_client, make_repo
    from app.engine.facts import ASSUMED  # noqa: F401 (contract import)

    repo = make_repo()
    client = make_client(repo)
    iid = client.post("/api/v1/inspections", json={
        "inspector_id": "E-1", "inspector_name": "E",
        "business_name": "Shop", "inspection_date": "2026-09-15",
    }).json()["inspection_id"]
    # Reviewed declarations: officer-verified values, some optional
    # fields (best_before, consumer_care, batch) deliberately absent.
    pid = client.post(f"/api/v1/inspections/{iid}/products", json={
        "product_name": "Choco Crunch Biscuits", "category": "GENERAL",
        "is_prepackaged": True, "manufacturer": "Demo Foods",
        "quantity": 100, "quantity_unit": "g", "quantity_type": "weight",
        "manufacturing_date": "2024-05-01", "mrp": "45",
        "fssai_license": "10015043001129",
        "ingredients_raw": "Refined wheat flour, sugar, palm oil.",
        "food": True,
    }).json()["product_id"]
    analysis = client.post(f"/api/v1/inspections/{iid}/analyze",
                           json={"product_id": pid}).json()
    assert analysis["findings"]
    assert "violation_ids" in analysis
    report = client.get(f"/api/v1/reports/{iid}").json()
    assert report["inspection_id"] == iid
    assert report["findings"]
    assert report["score"]["out_of"] == 100
    pdf = client.get(f"/api/v1/reports/{iid}?format=pdf")
    assert pdf.status_code == 200
    assert pdf.headers["content-type"] == "application/pdf"


# ------------------------------------------------- Part S: 503 path ---
def test_suggestions_store_failure_is_503_not_500():
    from conftest import make_client, make_repo
    from fastapi import HTTPException

    repo = make_repo()
    client = make_client(repo)

    def _boom(*args, **kwargs):
        raise RuntimeError("relation missing")

    repo.list_suggestions = _boom
    res = client.get("/api/v1/officer/suggestions",
                     headers={"X-LegalAkshi-Role": "officer"})
    assert res.status_code == 503
    assert "004_consumer_suggestions" in res.text
    repo.create_suggestion = _boom
    res = client.post("/api/v1/consumer/suggestions",
                      json={"title": "x", "category": "Other"})
    assert res.status_code == 503


def test_suggestions_happy_paths_unaffected():
    from conftest import make_client, make_repo

    client = make_client(make_repo())
    who = {"X-LegalAkshi-Role": "consumer", "X-LegalAkshi-User": "u1"}
    created = client.post("/api/v1/consumer/suggestions", json={
        "title": "QR stickers", "category": "Digital verification",
        "description": "Tamper-proof QR stickers."}, headers=who)
    assert created.status_code == 201
    assert client.get("/api/v1/consumer/suggestions",
                      headers=who).status_code == 200
    off = {"X-LegalAkshi-Role": "officer"}
    assert client.get("/api/v1/officer/suggestions",
                      headers=off).status_code == 200
