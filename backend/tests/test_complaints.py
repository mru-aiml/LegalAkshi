"""Complaint lifecycle tests — intake, ownership, transitions, timeline."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from conftest import make_client, make_repo

OFFICER = {"X-LegalAkshi-Role": "officer", "X-LegalAkshi-User": "INSP-9"}
ALICE = {"X-LegalAkshi-Role": "consumer", "X-LegalAkshi-User": "alice"}
BOB = {"X-LegalAkshi-Role": "consumer", "X-LegalAkshi-User": "bob"}

COMPLAINT = {"product_name": "Atta X", "retailer": "Store Y",
             "city": "Bengaluru", "severity": "High",
             "description": "MRP missing on pack."}


@pytest.fixture()
def client():
    return make_client(make_repo())


def test_create_and_read_own_complaint(client: TestClient):
    created = client.post("/api/v1/complaints", json=COMPLAINT,
                          headers=ALICE).json()
    assert created["status"] == "SUBMITTED"
    assert created["reporter_id"] == "alice"
    got = client.get(f"/api/v1/complaints/{created['complaint_id']}",
                     headers=ALICE).json()
    assert got["complaint_id"] == created["complaint_id"]
    assert got["timeline"][0]["event_type"] == "CREATED"


def test_consumer_cannot_read_others_complaint(client: TestClient):
    created = client.post("/api/v1/complaints", json=COMPLAINT,
                          headers=ALICE).json()
    r = client.get(f"/api/v1/complaints/{created['complaint_id']}", headers=BOB)
    assert r.status_code == 403


def test_officer_sees_all_consumers_see_own(client: TestClient):
    client.post("/api/v1/complaints", json=COMPLAINT, headers=ALICE)
    client.post("/api/v1/complaints", json=COMPLAINT, headers=BOB)
    assert len(client.get("/api/v1/complaints", headers=ALICE).json()) == 1
    assert len(client.get("/api/v1/complaints", headers=OFFICER).json()) == 2


def test_full_lifecycle_transitions(client: TestClient):
    cid = client.post("/api/v1/complaints", json=COMPLAINT,
                      headers=ALICE).json()["complaint_id"]
    for nxt in ("ACKNOWLEDGED", "UNDER_REVIEW", "INSPECTION_SCHEDULED",
                "INSPECTION_COMPLETED", "ACTION_TAKEN", "RESOLVED", "CLOSED"):
        r = client.post(f"/api/v1/complaints/{cid}/transitions",
                        json={"to_status": nxt, "note": f"moving to {nxt}"},
                        headers=OFFICER)
        assert r.status_code == 200, (nxt, r.text)
        assert r.json()["status"] == nxt
    timeline = client.get(f"/api/v1/complaints/{cid}",
                          headers=OFFICER).json()["timeline"]
    assert [e["to_status"] for e in timeline[1:]] == [
        "ACKNOWLEDGED", "UNDER_REVIEW", "INSPECTION_SCHEDULED",
        "INSPECTION_COMPLETED", "ACTION_TAKEN", "RESOLVED", "CLOSED"]


def test_illegal_transition_rejected(client: TestClient):
    cid = client.post("/api/v1/complaints", json=COMPLAINT,
                      headers=ALICE).json()["complaint_id"]
    r = client.post(f"/api/v1/complaints/{cid}/transitions",
                    json={"to_status": "RESOLVED"}, headers=OFFICER)
    assert r.status_code == 422
    r = client.post(f"/api/v1/complaints/{cid}/transitions",
                    json={"to_status": "BOGUS"}, headers=OFFICER)
    assert r.status_code == 422


def test_consumer_cannot_transition(client: TestClient):
    cid = client.post("/api/v1/complaints", json=COMPLAINT,
                      headers=ALICE).json()["complaint_id"]
    r = client.post(f"/api/v1/complaints/{cid}/transitions",
                    json={"to_status": "ACKNOWLEDGED"}, headers=ALICE)
    assert r.status_code == 403
    r = client.post(f"/api/v1/complaints/{cid}/transitions",
                    json={"to_status": "ACKNOWLEDGED"})
    assert r.status_code == 401
