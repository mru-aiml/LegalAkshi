"""Stage 2 tests: Package Intelligence + AI Vision + Human-in-the-Loop.

Covers the 25 areas in the Stage 2 spec (Part S) without touching the
Rule Engine, legal data, scoring, RBAC, or existing OCR pipeline.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.services.package_intelligence import reconciliation as rec_mod
from app.services.package_intelligence import service as pi_mod
from app.services.package_intelligence import validators as validators_mod
from app.services.vision import mock_provider as mock_mod
from app.services.vision import provider as provider_mod
from app.services.vision import schemas as vision_schemas
from app.services.vision import service as vision_service


def _ocr(value, conf=0.9, evidence=None):
    return {"value": value, "confidence": conf,
            "evidence_text": evidence or f"ctx {value}",
            "image_id": "back", "bbox": None}


# --- 1. vision provider schema validation ---
def test_vision_schema_accepts_structured_candidate():
    cand = vision_schemas.validate_vision_candidate({
        "field": "mrp", "value": "108", "unit": "INR",
        "status": "DETECTED", "confidence": 0.94,
        "evidence_text": "MRP Rs. 108/-", "bbox": None,
        "image_id": "back", "source": "vision"})
    assert cand and cand["field"] == "mrp" and cand["status"] == "DETECTED"


def test_vision_schema_rejects_hedged_and_bad_shapes():
    assert vision_schemas.validate_vision_candidate(
        {"field": "mrp", "value": "probably 108",
         "status": "DETECTED", "confidence": 0.9})["status"] == \
        "NEEDS_REVIEW"
    assert vision_schemas.validate_vision_candidate(
        {"field": "", "value": "108", "status": "DETECTED",
         "confidence": 0.9}) is None
    assert vision_schemas.validate_vision_candidate(
        {"field": "mrp", "value": "108", "status": "MAYBE",
         "confidence": 0.9}) is None
    assert vision_schemas.validate_vision_candidate(
        {"field": "mrp", "value": "108", "status": "DETECTED",
         "confidence": 9.0}) is None


# --- 2. provider unavailable ---
def test_provider_unavailable_when_disabled(monkeypatch):
    monkeypatch.setenv("LEGALAKSHI_VISION_ENABLED", "false")
    from app.core import config as config_mod
    config_mod.get_settings.cache_clear()
    try:
        assert provider_mod.get_vision_provider() is None
        summary = vision_service.extract_with_vision(
            None, [(None, "front")], ["mrp"])
        assert summary["candidates"] == [] and summary["calls"] == 0
        assert "OCR-only" in (summary["error"] or "")
    finally:
        config_mod.get_settings.cache_clear()


# --- 3. OCR-only fallback ---
def test_ocr_only_fallback_reconciliation():
    out = rec_mod.reconcile_all(
        ocr_fields={"mrp": _ocr("108", 0.9, "MRP Rs. 108")})
    assert out["mrp"]["status"] == "DETECTED"
    assert out["mrp"]["final_value"] == "108"


# --- 4. OCR + vision agreement ---
def test_agreement_detected_high_confidence():
    out = rec_mod.reconcile_field(
        "mrp", ocr=_ocr("108", 0.9, "MRP Rs. 108"),
        vision={"value": "108", "confidence": 0.94,
                "evidence_text": "MRP Rs. 108/-"})
    assert out["status"] == "DETECTED"
    assert out["agreement"] == "AGREE"
    assert set(out["sources"]) == {"rapidocr", "vision"}


# --- 5. OCR + vision conflict ---
def test_conflict_needs_review_retains_both():
    out = rec_mod.reconcile_field(
        "mrp", ocr=_ocr("108", 0.9, "MRP Rs. 108"),
        vision={"value": "180", "confidence": 0.9,
                "evidence_text": "MRP Rs. 180"})
    assert out["status"] == "NEEDS_REVIEW"
    assert out["agreement"] == "CONFLICT"
    assert out["final_value"] is None
    assert {c["value"] for c in out["candidates"]} == {"108", "180"}


# --- 6. vision-only candidate ---
def test_vision_only_strong_evidence_detected():
    out = rec_mod.reconcile_field(
        "mrp", vision={"value": "108", "confidence": 0.95,
                       "evidence_text": "MRP Rs. 108"},
        evidence_boost=True)
    assert out["status"] == "DETECTED"
    out2 = rec_mod.reconcile_field(
        "mrp", vision={"value": "108", "confidence": 0.95,
                       "evidence_text": "MRP Rs. 108"})
    assert out2["status"] == "NEEDS_REVIEW"  # no strong evidence


# --- 7. invalid AI value rejection ---
def test_invalid_ai_date_rejected():
    out = rec_mod.reconcile_field(
        "manufacturing_date",
        vision={"value": "70.16", "confidence": 0.9,
                "evidence_text": "70.16"})
    assert out["status"] in ("NEEDS_REVIEW", "NOT_DETECTED")
    assert out["final_value"] is None


# --- 8-16. field validators ---
def test_mrp_validation():
    ok, _ = validators_mod.validate_mrp_candidate("108", "MRP Rs. 108")
    assert ok
    for bad in ("180012345678", "10000012345678", "notanum"):
        ok, _ = validators_mod.validate_mrp_candidate(bad, "MRP Rs. X")
        assert not ok


def test_quantity_validation():
    ok, _ = validators_mod.validate_quantity_candidate("200", "g",
                                                       "Net Qty 200 g")
    assert ok
    ok, _ = validators_mod.validate_quantity_candidate("200", "parsecs",
                                                       "Net Qty")
    assert not ok
    ok, _ = validators_mod.validate_quantity_candidate("", "g")
    assert not ok


def test_date_validation():
    ok, _ = validators_mod.validate_date_candidate("05/2024",
                                                   "MFD 05/2024")
    assert ok
    ok, _ = validators_mod.validate_date_candidate("70.16", "MFD 70.16")
    assert not ok
    ok, _ = validators_mod.validate_date_candidate("05/2024", "random text")
    assert not ok


def test_fssai_validation():
    ok, _ = validators_mod.validate_fssai_candidate(
        "10012043001234", "FSSAI Lic No 10012043001234")
    assert ok
    ok, _ = validators_mod.validate_fssai_candidate("12345", "FSSAI 12345")
    assert not ok
    ok, _ = validators_mod.validate_fssai_candidate(
        "10012043001234", "hello world")
    assert not ok


def test_manufacturer_validation():
    ok, _ = validators_mod.validate_manufacturer_candidate(
        "Acme Foods Pvt Ltd", "Manufactured by Acme Foods")
    assert ok
    ok, _ = validators_mod.validate_manufacturer_candidate(
        "Acme Foods", "random line")
    assert not ok


def test_care_validation():
    ok, _ = validators_mod.validate_care_candidate(
        "18001234567", "Customer care 18001234567")
    assert ok
    ok, _ = validators_mod.validate_care_candidate("hello", "care hello")
    assert not ok


def test_batch_validation():
    ok, _ = validators_mod.validate_batch_candidate(
        "B12/24", "Batch No B12/24")
    assert ok
    ok, _ = validators_mod.validate_batch_candidate(
        "10012043001234", "Batch 10012043001234")
    assert not ok


def test_ingredient_contamination():
    ok, _ = validators_mod.validate_ingredient_candidate(
        "Sugar, Salt, Wheat Flour", "INGREDIENTS: Sugar, Salt")
    assert ok
    ok, _ = validators_mod.validate_ingredient_candidate(
        "XYZ", "random")
    assert not ok


def test_nutrition_table_validation():
    ok, _ = validators_mod.validate_nutrition_candidate(
        "energy", "120", "kcal", "Nutrition per 100g")
    assert ok
    ok, _ = validators_mod.validate_nutrition_candidate(
        "energy", "lots", "kcal")
    assert not ok


def _make_inspection_product(client):
    insp = client.post("/api/v1/inspections", json={
        "inspector_id": "i1", "inspector_name": "Off",
        "business_name": "Shop", "inspection_date": "2026-09-20"}).json()
    iid = insp["inspection_id"]
    prod = client.post(f"/api/v1/inspections/{iid}/products",
                       json={"product_name": "P",
                             "category": "GENERAL"}).json()
    return iid, prod["product_id"]


# --- 17/18. correction persistence + append-only ---
def test_correction_persistence_and_append_only():
    from conftest import make_client, make_repo

    client = make_client(make_repo())
    iid, pid = _make_inspection_product(client)
    headers = {"X-LegalAkshi-Role": "officer",
               "X-LegalAkshi-User": "off1"}
    body = {"field_key": "mrp", "original_value": "108",
            "corrected_value": "180", "original_status": "DETECTED",
            "original_confidence": 0.9,
            "evidence_snapshot": {"image_id": "back"},
            "correction_reason": "re-read label"}
    r1 = client.post(
        f"/api/v1/inspections/{iid}/products/{pid}/corrections",
        json=body, headers=headers)
    assert r1.status_code == 201, r1.text
    r2 = client.post(
        f"/api/v1/inspections/{iid}/products/{pid}/corrections",
        json={**body, "corrected_value": "181"}, headers=headers)
    assert r2.status_code == 201
    rows = client.get(
        f"/api/v1/inspections/{iid}/products/{pid}/corrections",
        headers=headers).json()
    assert len(rows) == 2  # append-only: both kept
    assert {r["corrected_value"] for r in rows} == {"180", "181"}
    assert all(r["original_value"] == "108" for r in rows)


# --- 19. correction RBAC ---
def test_correction_rbac_consumer_denied():
    from conftest import make_client, make_repo

    client = make_client(make_repo())
    iid, pid = _make_inspection_product(client)
    denied = client.post(
        f"/api/v1/inspections/{iid}/products/{pid}/corrections",
        json={"field_key": "mrp", "corrected_value": "1"},
        headers={"X-LegalAkshi-Role": "consumer",
                 "X-LegalAkshi-User": "c1"})
    assert denied.status_code == 403


# --- 20. verified correction filtering ---
def test_verified_correction_filtering():
    from app.services.learning.evaluation import export_verified_dataset

    rows = [
        {"field_key": "mrp", "original_value": "108",
         "corrected_value": "180", "verified": True,
         "evidence_snapshot": {}},
        {"field_key": "mrp", "original_value": "108",
         "corrected_value": "181", "verified": False,
         "evidence_snapshot": {}},
    ]
    dataset = export_verified_dataset(rows)
    assert len(dataset) == 1
    assert dataset[0]["final_verified_value"] == "180"


# --- 21. error pattern classification ---
def test_error_pattern_classification():
    from app.services.learning.error_patterns import (
        aggregate_error_patterns,
        classify_failure,
    )

    assert classify_failure(
        {"field_key": "manufacturing_date",
         "original_value": "70.16"}) == "DATE_CONFUSION"
    assert classify_failure(
        {"field_key": "mrp", "original_value": "108",
         "corrected_value": "180"}) == "MRP_DIGIT_CONFUSION"
    patterns = aggregate_error_patterns([
        {"field_key": "mrp", "original_value": "108",
         "corrected_value": "180", "verified": True,
         "evidence_snapshot": {}},
        {"field_key": "mrp", "original_value": "1",
         "corrected_value": "2", "verified": False,
         "evidence_snapshot": {}},
    ])
    assert len(patterns) == 1 and patterns[0]["occurrences"] == 1


def test_error_patterns_endpoint_officer_only():
    from conftest import make_client, make_repo

    client = make_client(make_repo())
    ok = client.get("/api/v1/officer/learning/error-patterns",
                    headers={"X-LegalAkshi-Role": "officer"})
    assert ok.status_code == 200
    denied = client.get("/api/v1/officer/learning/error-patterns",
                        headers={"X-LegalAkshi-Role": "consumer"})
    assert denied.status_code == 403


# --- 22. active-learning priority ---
def test_active_learning_priority():
    from app.services.learning.evaluation import review_queue

    rows = [
        {"field_key": "mrp", "original_value": "108",
         "corrected_value": "180", "verified": False,
         "created_at": "2026-01-01",
         "evidence_snapshot": {"vision_value": "180"}},
        {"field_key": "country_of_origin", "original_value": "",
         "corrected_value": "India", "verified": False,
         "created_at": "2026-01-02", "evidence_snapshot": {}},
    ]
    queue = review_queue(rows, {"mrp"})
    assert queue[0]["priority"] == "HIGH"
    assert "conflict" in queue[0]["reason"].lower() \
        or "required" in queue[0]["reason"].lower()
    assert queue[-1]["priority"] == "LOW"


def test_review_queue_endpoint():
    from conftest import make_client, make_repo

    client = make_client(make_repo())
    res = client.get("/api/v1/officer/learning/review-queue",
                     headers={"X-LegalAkshi-Role": "officer"})
    assert res.status_code == 200
    assert "queue" in res.json()


# --- 23. analysis readiness ---
def test_analysis_readiness_from_requirements():
    from conftest import make_client, make_repo

    client = make_client(make_repo())
    reqs = client.get("/api/v1/analysis/requirements?food=true").json()
    assert "required" in reqs
    reconciled = {r.get("ocr_key") or r.get("field"):
                  {"status": "DETECTED"}
                  for r in reqs["required"]}
    ready = pi_mod.readiness(reconciled, reqs)
    assert ready["ready"] is True and ready["fields_not_detected"] == 0
    ready2 = pi_mod.readiness({}, reqs)
    if reqs["required"]:
        assert ready2["ready"] is False


# --- 24. performance / call-budget enforcement ---
def test_vision_call_budget_enforced():
    provider = mock_mod.MockVisionProvider()
    images = [(object(), "front")]
    fields = [f"custom_field_{i}" for i in range(30)]
    summary = vision_service.extract_with_vision(
        provider, images, fields, inspection_id="budget-test")
    assert summary["calls"] <= vision_schemas.MAX_VISION_CALLS_PER_INSPECTION
    # Cached second run issues no new provider calls.
    before = len(provider.calls)
    summary2 = vision_service.extract_with_vision(
        provider, images, fields, inspection_id="budget-test")
    assert len(provider.calls) == before
    assert summary2["cached"] > 0


# --- 25. AI failure fallback ---
def test_ai_failure_fallback_to_ocr_only():
    class _Boom:
        name = "boom"

        def extract_package_fields(self, *a, **k):
            raise RuntimeError("model down")

    summary = vision_service.extract_with_vision(
        _Boom(), [(object(), "front")], ["mrp"])
    assert summary["candidates"] == [] and summary["calls"] == 0
    assert summary["error"]
    pack = pi_mod.run_package_intelligence(
        {"fields": {"mrp": {"value": "108", "confidence": 0.9,
                            "evidence_text": "MRP Rs. 108"}}},
        summary, None, {})
    assert pack["fields"]["mrp"]["status"] == "DETECTED"


def test_package_intelligence_diagnostics_shape():
    pack = pi_mod.run_package_intelligence(
        {"fields": {}, "images_analyzed": 2}, {"calls": 1,
                                              "latency_ms": 5.0,
                                              "provider": "mock",
                                              "model": None,
                                              "candidates": [],
                                              "fields_requested": [],
                                              "fields_returned": []},
        {"required": [], "optional": []}, {"total_ms": 10.0})
    diag = pack["diagnostics"]
    for key in ("images", "ocr_calls", "vision_calls", "fields_required",
                "fields_detected", "fields_needs_review",
                "fields_not_detected", "ocr_latency_ms",
                "vision_latency_ms", "reconciliation_latency_ms",
                "total_latency_ms"):
        assert key in diag
    assert "accuracy" not in json.dumps(diag).lower()


def test_gemini_provider_unconfigured_fails_safe():
    from app.services.vision.gemini_provider import GeminiVisionProvider

    provider = GeminiVisionProvider(api_key="", model="")
    assert provider.available() is False
    with pytest.raises(Exception):
        provider.extract_package_fields(object(), ["mrp"])


def test_reconcile_endpoint_requires_auth_and_shape():
    from conftest import make_client, make_repo

    client = make_client(make_repo())
    anon = client.post("/api/v1/package-intelligence/reconcile", json={})
    assert anon.status_code == 401
    headers = {"X-LegalAkshi-Role": "officer",
               "X-LegalAkshi-User": "off1"}
    res = client.post("/api/v1/package-intelligence/reconcile",
                      json={"ocr": {"mrp": {
                          "value": "108", "confidence": 0.9,
                          "evidence_text": "MRP Rs. 108"}}},
                      headers=headers).json()
    assert res["fields"]["mrp"]["status"] == "DETECTED"
    assert "diagnostics" in res and "readiness" in res


def test_vision_status_never_exposes_secret():
    from conftest import make_client, make_repo

    client = make_client(make_repo())
    res = client.get("/api/v1/package-intelligence/vision-status",
                     headers={"X-LegalAkshi-Role": "officer"})
    assert res.status_code == 200
    body = res.json()
    # Stage 2C mandates the api_key_present *boolean*; key material
    # itself must never appear. No string value may carry key material.
    assert body.get("api_key_present") in (True, False)
    for key, value in body.items():
        if key == "api_key_present":
            continue
        assert "api_key" not in key.lower(), key
        if isinstance(value, str):
            assert "api_key" not in value.lower()
            assert "secret" not in value.lower()
    assert "secret" not in json.dumps(body).lower()


def test_reports_use_verified_values():
    from app.services.learning.corrections import (
        apply_verified_corrections,
    )

    product = {"product_name": "P", "mrp": "108"}
    out = apply_verified_corrections(product, [
        {"field_key": "mrp", "corrected_value": "180", "verified": True},
        {"field_key": "mrp", "corrected_value": "999", "verified": False},
    ])
    assert out["mrp"] == "180" and product["mrp"] == "108"


def test_golden_infrastructure_harness(tmp_path: Path):
    # Golden dir documents null-label discipline; no images shipped.
    golden = Path(__file__).resolve().parent / "golden"
    assert (golden / "README.md").exists()
    template = json.loads(
        (golden / "examples.template.json").read_text())
    assert template and all(
        ex["expected_value"] is None and ex["verified"] is False
        for ex in template)
    assert not any((golden / "images").glob("*.jpg"))
