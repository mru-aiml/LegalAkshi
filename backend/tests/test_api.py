"""API tests — authoritative openapi.json contract via TestClient.

App built with create_app(MemoryRepo()): no DATABASE_URL, no PostgreSQL.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from conftest import make_client, make_repo

INSP = {"inspector_id": "T-1", "inspector_name": "Tester",
        "business_name": "Test Store", "inspection_date": "2026-09-15"}

PRODUCT = {"product_name": "Demo Atta", "category": "GENERAL",
           "is_prepackaged": True, "manufacturer": "Demo Foods Ltd",
           "quantity": 5, "quantity_unit": "kg", "quantity_type": "weight",
           "manufacturing_date": "2024-05-01", "mrp": "250",
           "consumer_care": "1800-000", "unit_sale_price": "Rs.50 per kg"}


@pytest.fixture()
def client():
    return make_client(make_repo())


def test_healthz(client: TestClient):
    assert client.get("/healthz").json() == {"status": "ok"}


def test_create_and_list_inspections(client: TestClient):
    r = client.post("/api/v1/inspections", json=INSP)
    assert r.status_code == 201, r.text
    assert r.json()["inspection_id"]
    lst = client.get("/api/v1/inspections").json()
    assert isinstance(lst, list) and len(lst) == 1  # authoritative: array


def test_create_inspection_requires_fields(client: TestClient):
    r = client.post("/api/v1/inspections", json={"business_name": "x"})
    assert r.status_code == 422


def test_add_product_requires_fields(client: TestClient):
    iid = client.post("/api/v1/inspections", json=INSP).json()["inspection_id"]
    r = client.post(f"/api/v1/inspections/{iid}/products",
                    json={"category": "GENERAL", "is_prepackaged": True})
    assert r.status_code == 422  # product_name required


def test_analyze_embedded_product(client: TestClient):
    iid = client.post("/api/v1/inspections", json=INSP).json()["inspection_id"]
    r = client.post(f"/api/v1/inspections/{iid}/analyze",
                    json={"product": PRODUCT, "as_of_date": "2026-09-15"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] in ("COMPLIANT", "NEEDS_REVIEW", "NON_COMPLIANT")
    assert body["score"]["out_of"] == 100.0
    assert isinstance(body["findings"], list) and body["findings"]
    f0 = body["findings"][0]
    assert {"rule_id", "status", "requirement", "evidence",
            "explanation"} <= set(f0)


def test_analyze_missing_mrp_creates_pending_violation(client: TestClient):
    iid = client.post("/api/v1/inspections", json=INSP).json()["inspection_id"]
    bad = dict(PRODUCT)
    bad.pop("mrp")
    r = client.post(f"/api/v1/inspections/{iid}/analyze", json={"product": bad})
    assert r.status_code == 200, r.text
    assert r.json()["violation_ids"]
    v = client.get(f"/api/v1/inspections/{iid}/violations").json()["items"]
    assert v and v[0]["inspector_status"] == "PENDING"


def test_compliance_endpoint(client: TestClient):
    iid = client.post("/api/v1/inspections", json=INSP).json()["inspection_id"]
    pid = client.post(f"/api/v1/inspections/{iid}/products",
                      json=PRODUCT).json()["product_id"]
    client.post(f"/api/v1/inspections/{iid}/analyze", json={"product_id": pid})
    r = client.get(f"/api/v1/inspections/{iid}/products/{pid}/compliance")
    assert r.status_code == 200 and isinstance(r.json(), list) and r.json()


def test_rules_endpoints(client: TestClient):
    rules = client.get("/api/v1/rules").json()
    assert isinstance(rules, list) and len(rules) == 15
    ids = {r["rule_id"] for r in rules}
    for required in ("CHK-MRP", "CHK-MANUFACTURER", "CHK-COMMON-NAME",
                     "CHK-NET-QTY", "CHK-MFG-DATE"):
        assert required in ids
    detail = client.get("/api/v1/rules/CHK-MRP").json()
    assert detail["check"]["check_id"] == "CHK-MRP"
    assert len(detail["versions"]) == 2  # full amendment lineage
    assert client.get("/api/v1/rules/CHK-NOPE").status_code == 404


def test_verify_flow_confirm_and_reject(client: TestClient):
    iid = client.post("/api/v1/inspections", json=INSP).json()["inspection_id"]
    bad = dict(PRODUCT)
    bad.pop("mrp")
    vid = client.post(f"/api/v1/inspections/{iid}/analyze",
                      json={"product": bad}).json()["violation_ids"][0]
    headers = {"X-LegalAkshi-Role": "officer", "X-LegalAkshi-User": "INSP-1"}
    r = client.post(f"/api/v1/violations/{vid}/verify",
                    json={"decision": "CONFIRMED", "inspector_id": "INSP-1"},
                    headers=headers)
    assert r.status_code == 200 and r.json()["inspector_status"] == "CONFIRMED"
    r = client.post(f"/api/v1/violations/{vid}/verify",
                    json={"decision": "REJECTED", "inspector_id": "INSP-1"},
                    headers=headers)
    assert r.json()["inspector_status"] == "REJECTED"


def test_verify_requires_officer_role(client: TestClient):
    iid = client.post("/api/v1/inspections", json=INSP).json()["inspection_id"]
    bad = dict(PRODUCT)
    bad.pop("mrp")
    vid = client.post(f"/api/v1/inspections/{iid}/analyze",
                      json={"product": bad}).json()["violation_ids"][0]
    r = client.post(f"/api/v1/violations/{vid}/verify",
                    json={"decision": "CONFIRMED", "inspector_id": "INSP-1"})
    assert r.status_code == 401  # anonymous caller
    r = client.post(f"/api/v1/violations/{vid}/verify",
                    json={"decision": "CONFIRMED", "inspector_id": "INSP-1"},
                    headers={"X-LegalAkshi-Role": "consumer"})
    assert r.status_code == 403  # consumer cannot verify


def test_verify_rejects_bad_decision(client: TestClient):
    iid = client.post("/api/v1/inspections", json=INSP).json()["inspection_id"]
    bad = dict(PRODUCT)
    bad.pop("mrp")
    vid = client.post(f"/api/v1/inspections/{iid}/analyze",
                      json={"product": bad}).json()["violation_ids"][0]
    r = client.post(f"/api/v1/violations/{vid}/verify",
                    json={"decision": "GUILTY", "inspector_id": "INSP-1"},
                    headers={"X-LegalAkshi-Role": "officer"})
    assert r.status_code == 422


def test_report_endpoint(client: TestClient):
    iid = client.post("/api/v1/inspections", json=INSP).json()["inspection_id"]
    client.post(f"/api/v1/inspections/{iid}/analyze", json={"product": PRODUCT})
    r = client.get(f"/api/v1/reports/{iid}")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["report_id"] == iid and body["findings"]
