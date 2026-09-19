"""Officer console tests — stats, queue, case detail, actions, profile."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from conftest import make_client, make_repo

OFFICER = {"X-LegalAkshi-Role": "officer", "X-LegalAkshi-User": "INSP-9"}
ADMIN = {"X-LegalAkshi-Role": "admin", "X-LegalAkshi-User": "ADMIN-1"}
CONSUMER = {"X-LegalAkshi-Role": "consumer", "X-LegalAkshi-User": "C-9"}

INSP = {"inspector_id": "O-1", "inspector_name": "Officer",
        "business_name": "Queue Store", "inspection_date": "2026-09-15",
        "state": "Karnataka", "district": "Bengaluru"}

PRODUCT = {"product_name": "Queue Atta", "category": "GENERAL",
           "is_prepackaged": True, "manufacturer": "Queue Foods",
           "quantity": 5, "quantity_unit": "kg", "quantity_type": "weight",
           "manufacturing_date": "2024-05-01",
           "consumer_care": "1800-000", "unit_sale_price": "Rs.50 per kg"}


@pytest.fixture()
def case():
    repo = make_repo()
    client = make_client(repo)
    iid = client.post("/api/v1/inspections", json=INSP).json()["inspection_id"]
    bad = dict(PRODUCT)
    bad.pop("manufacturer")
    vid = client.post(f"/api/v1/inspections/{iid}/analyze",
                      json={"product": bad}).json()["violation_ids"][0]
    return client, repo, iid, vid


def test_stats_reflect_backend_data(case):
    client, _, _, _ = case
    stats = client.get("/api/v1/officer/stats", headers=OFFICER).json()
    assert stats["awaiting_review"] >= 1
    assert stats["total_violations"] >= 1
    assert stats["active_rules"] >= 15
    assert 0.0 <= stats["resolution_rate"] <= 1.0


def test_queue_filters(case):
    client, _, _, _ = case
    all_items = client.get("/api/v1/officer/queue", headers=OFFICER).json()
    assert all_items
    pending = client.get("/api/v1/officer/queue?status=PENDING",
                         headers=OFFICER).json()
    assert len(pending) == len(all_items)
    assert client.get("/api/v1/officer/queue?status=CONFIRMED",
                      headers=OFFICER).json() == []
    item = all_items[0]
    assert {"violation_id", "product_name", "business_name", "severity",
            "inspector_status"} <= set(item)


def test_case_detail_bundle(case):
    client, _, _, vid = case
    detail = client.get(f"/api/v1/officer/cases/{vid}",
                        headers=OFFICER).json()
    for key in ("violation", "product", "inspection", "findings", "evidence",
                "declarations", "rules", "enforcement", "audit"):
        assert key in detail, key
    assert detail["findings"]
    assert detail["rules"]


def test_officer_confirm_persists_with_audit(case):
    client, repo, _, vid = case
    r = client.post(f"/api/v1/officer/cases/{vid}/actions",
                    json={"action": "confirm", "notes": "re-checked shelf"},
                    headers=OFFICER)
    assert r.status_code == 200, r.text
    assert r.json()["violation"]["inspector_status"] == "CONFIRMED"
    assert repo.audit_list("violation", vid)


def test_request_evidence_and_reject(case):
    client, _, _, vid = case
    r = client.post(f"/api/v1/officer/cases/{vid}/actions",
                    json={"action": "request_evidence", "notes": "need photo"},
                    headers=OFFICER)
    assert r.json()["violation"]["inspector_status"] == "REQUIRES_REVIEW"
    r = client.post(f"/api/v1/officer/cases/{vid}/actions",
                    json={"action": "reject", "notes": "pack ok"},
                    headers=OFFICER)
    assert r.json()["violation"]["inspector_status"] == "REJECTED"


def test_action_taken_records_enforcement(case):
    client, repo, _, vid = case
    r = client.post(f"/api/v1/officer/cases/{vid}/actions",
                    json={"action": "action_taken"}, headers=OFFICER)
    assert r.status_code == 422  # legal_basis + action_type required
    r = client.post(
        f"/api/v1/officer/cases/{vid}/actions",
        json={"action": "action_taken", "action_type": "NOTICE",
              "legal_basis": "Section 36 pending verification",
              "authority": "Legal Metrology Officer", "notes": "notice issued"},
        headers=OFFICER)
    assert r.status_code == 200, r.text
    detail = r.json()
    assert detail["violation"]["inspector_status"] == "CONFIRMED"
    assert len(detail["enforcement"]) == 1
    assert detail["enforcement"][0]["action_type"] == "NOTICE"
    assert repo.enforcement_for_violation(vid)


def test_profile_from_authenticated_data(case):
    client, _, _, _ = case
    me = client.get("/api/v1/officer/profile", headers=OFFICER).json()
    assert me["user_id"] == "INSP-9"
    assert me["role"] == "officer"
    assert me["role_source"] == "dev"
    assert "violations.verify" in me["permissions"]
    assert "activity" in me


def test_consumer_blocked_from_officer_console(case):
    client, _, _, vid = case
    assert client.get("/api/v1/officer/queue", headers=CONSUMER).status_code == 403
    assert client.get(f"/api/v1/officer/cases/{vid}",
                      headers=CONSUMER).status_code == 403
    r = client.post(f"/api/v1/officer/cases/{vid}/actions",
                    json={"action": "confirm"}, headers=CONSUMER)
    assert r.status_code == 403
