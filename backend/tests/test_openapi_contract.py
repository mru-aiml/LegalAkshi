"""OpenAPI contract tests — FastAPI responses validated against
lib/api-spec/openapi.json (authoritative, v4.0.0).

Same routes serve PostgreSQL in production, so shape conformance proven
here transfers (only the repository differs).
"""
from __future__ import annotations

import copy
import json
from pathlib import Path

import jsonschema
import pytest
from fastapi.testclient import TestClient

from conftest import make_client, make_repo

SPEC = json.loads((Path(__file__).resolve().parent.parent.parent
                   / "lib" / "api-spec" / "openapi.json").read_text(encoding="utf-8"))


def resolve(node, root):
    if isinstance(node, dict):
        if set(node) == {"$ref"} and node["$ref"].startswith("#/"):
            target = root
            for part in node["$ref"][2:].split("/"):
                target = target[part]
            return resolve(copy.deepcopy(target), root)
        return {k: resolve(v, root) for k, v in node.items()}
    if isinstance(node, list):
        return [resolve(v, root) for v in node]
    return node


def schema(name: str) -> dict:
    return resolve(copy.deepcopy(SPEC["components"]["schemas"][name]), SPEC)


def validate(name: str, payload) -> None:
    jsonschema.validate(payload, resolve(schema(name), SPEC))


INSP = {"inspector_id": "C-1", "inspector_name": "Contract",
        "business_name": "Contract Store", "inspection_date": "2026-09-15"}

PRODUCT = {"product_name": "Contract Atta", "category": "GENERAL",
           "is_prepackaged": True, "manufacturer": "Contract Foods",
           "quantity": 5, "quantity_unit": "kg", "quantity_type": "weight",
           "manufacturing_date": "2024-05-01", "mrp": "250",
           "consumer_care": "1800-000", "unit_sale_price": "Rs.50 per kg"}


@pytest.fixture()
def client():
    return make_client(make_repo())


def test_paths_match_implementation(client: TestClient):
    for path in ("/inspections",
                 "/inspections/{inspection_id}/products",
                 "/inspections/{inspection_id}/analyze",
                 "/inspections/{inspection_id}/products/{product_id}/compliance",
                 "/rules", "/rules/{check_id}", "/reports/{report_id}",
                 "/violations/{violation_id}/verify"):
        assert path in SPEC["paths"], path


def test_inspection_create_and_list_shapes(client: TestClient):
    created = client.post("/api/v1/inspections", json=INSP).json()
    validate("Inspection", created)
    listed = client.get("/api/v1/inspections").json()
    assert isinstance(listed, list)  # authoritative: bare array
    for item in listed:
        validate("Inspection", item)


def test_full_flow_shapes(client: TestClient):
    iid = client.post("/api/v1/inspections", json=INSP).json()["inspection_id"]
    pid = client.post(f"/api/v1/inspections/{iid}/products",
                      json=PRODUCT).json()["product_id"]
    analysis = client.post(f"/api/v1/inspections/{iid}/analyze",
                           json={"product_id": pid}).json()
    validate("AnalysisResponse", analysis)
    assert analysis["score"]["out_of"] == 100
    assert isinstance(analysis["score"]["finalizable"], bool)
    for finding in analysis["findings"]:
        validate("Finding", finding)
    compliance = client.get(
        f"/api/v1/inspections/{iid}/products/{pid}/compliance").json()
    assert isinstance(compliance, list)
    for finding in compliance:
        validate("Finding", finding)
    rules = client.get("/api/v1/rules").json()
    assert isinstance(rules, list)
    for rule in rules:
        validate("Rule", rule)
    bad = dict(PRODUCT)
    bad.pop("mrp")
    vid = client.post(f"/api/v1/inspections/{iid}/analyze",
                      json={"product": bad}).json()["violation_ids"][0]
    verified = client.post(f"/api/v1/violations/{vid}/verify",
                           json={"decision": "CONFIRMED", "inspector_id": "C-1",
                                 "verification_notes": "ok"},
                           headers={"X-LegalAkshi-Role": "officer"}).json()
    assert verified["inspector_status"] == "CONFIRMED"
    report = client.get(f"/api/v1/reports/{iid}").json()
    validate("FinalReport", report)
