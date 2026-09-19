"""Notification inbox tests — every row must trace to a real event."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from conftest import make_client, make_repo

OFFICER = {"X-LegalAkshi-Role": "officer", "X-LegalAkshi-User": "INSP-9"}
CONSUMER = {"X-LegalAkshi-Role": "consumer", "X-LegalAkshi-User": "C-9"}

INSP = {"inspector_id": "N-1", "inspector_name": "Notifier",
        "business_name": "Notify Store", "inspection_date": "2026-09-15"}
BAD_PRODUCT = {"product_name": "Notify Pack", "category": "GENERAL",
               "is_prepackaged": True, "manufacturer": "Notify Foods",
               "quantity": 1, "quantity_unit": "kg", "quantity_type": "weight",
               "manufacturing_date": "2024-05-01", "consumer_care": "1800-000",
               "unit_sale_price": "Rs.50 per kg"}  # no mrp -> FAIL


@pytest.fixture()
def client():
    return make_client(make_repo())


def _officer_notes(client: TestClient):
    return client.get("/api/v1/notifications", headers=OFFICER).json()


def test_engine_fail_emits_officer_notification(client: TestClient):
    iid = client.post("/api/v1/inspections", json=INSP).json()["inspection_id"]
    r = client.post(f"/api/v1/inspections/{iid}/analyze",
                    json={"product": BAD_PRODUCT})
    assert r.status_code == 200
    notes = _officer_notes(client)
    assert any(n["type"] == "violation_created" and n["audience"] == "officer"
               for n in notes)
    assert any(n["type"] == "review_required" for n in notes)


def test_complaint_lifecycle_emits_notifications(client: TestClient):
    created = client.post("/api/v1/complaints", json={
        "product_name": "Notify Pack", "retailer": "R", "city": "C",
        "description": "probe"}, headers=CONSUMER).json()
    cid = created["complaint_id"]
    notes = _officer_notes(client)
    assert any(n["type"] == "complaint_submitted" for n in notes)
    moved = client.post(f"/api/v1/complaints/{cid}/transitions",
                        json={"to_status": "ACKNOWLEDGED", "note": "seen"},
                        headers=OFFICER)
    assert moved.status_code == 200
    mine = client.get("/api/v1/notifications", headers=CONSUMER).json()
    assert any(n["type"] == "complaint_status_changed"
               and n["audience"] == "user:C-9" for n in mine)


def test_read_scoping_and_mark_all(client: TestClient):
    client.post("/api/v1/complaints", json={"product_name": "P"},
                headers=CONSUMER)
    notes = _officer_notes(client)
    assert notes and all(not n["read"] for n in notes)
    first = notes[0]["notification_id"]
    # consumer cannot mark an officer-audience notice
    assert client.post(f"/api/v1/notifications/{first}/read",
                       headers=CONSUMER).status_code == 404
    assert client.post(f"/api/v1/notifications/{first}/read",
                       headers=OFFICER).status_code == 200
    marked = client.post("/api/v1/notifications/read-all",
                         headers=OFFICER).json()["marked"]
    assert marked == len(notes) - 1
    assert all(n["read"] for n in _officer_notes(client))
