"""Consumer portal V2: suggestions lifecycle/RBAC + scoped reports.

Suggestions use their own table + lifecycle (migration 004); reports are
derived from the caller's OWN complaints (never all inspections).
"""
from __future__ import annotations

CONSUMER_A = {"X-LegalAkshi-Role": "consumer", "X-LegalAkshi-User": "user-a"}
CONSUMER_B = {"X-LegalAkshi-Role": "consumer", "X-LegalAkshi-User": "user-b"}
OFFICER = {"X-LegalAkshi-Role": "officer", "X-LegalAkshi-User": "off-1"}

SUGGESTION = {"title": "Tamper-proof QR stickers",
              "category": "Digital verification",
              "description": "Add QR stickers so buyers can verify.",
              "context": "Milk packets", "location": "Karnataka"}


def _setup_inspected_product(repo, name="Report Atta"):
    from app.engine import engine as engine_mod
    from app.engine.facts import ASSUMED

    insp = repo.create_inspection(
        {"inspector_id": "I-1", "inspector_name": "I",
         "business_name": "Store", "inspection_date": "2026-09-15"})
    prod = repo.add_product(insp["inspection_id"], {
        "product_name": name, "category": "GENERAL", "is_prepackaged": True,
        "manufacturer": "M Foods", "quantity": 1, "quantity_unit": "kg",
        "quantity_type": "weight", "manufacturing_date": "2024-05-01",
        "mrp": "100", "consumer_care": "1800-111-2222",
        "unit_sale_price": "Rs.100 per kg", "barcode": "8909999999999",
        "imported": False, "ecommerce": False, "food": True})
    pid = prod["product_id"]
    engine_mod.analyze(repo, insp, repo.get_product(pid),
                       repo.declarations_for(pid),
                       as_of="2026-09-15", default_origin=ASSUMED)
    return insp, prod


# ------------------------------------------------- suggestions ---
def test_suggestion_create_list_detail():
    from conftest import make_client, make_repo

    client = make_client(make_repo())
    created = client.post("/api/v1/consumer/suggestions", json=SUGGESTION,
                          headers=CONSUMER_A)
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["status"] == "SUBMITTED"
    assert body["suggestion_id"]
    assert "consumer_user_id" not in body  # never exposed to consumer
    mine = client.get("/api/v1/consumer/suggestions",
                      headers=CONSUMER_A).json()
    assert [s["suggestion_id"] for s in mine] == [body["suggestion_id"]]
    detail = client.get(
        f"/api/v1/consumer/suggestions/{body['suggestion_id']}",
        headers=CONSUMER_A).json()
    assert detail["title"] == SUGGESTION["title"]
    assert detail["timeline"], "lifecycle timeline must exist"


def test_suggestion_validation():
    from conftest import make_client, make_repo

    client = make_client(make_repo())
    assert client.post("/api/v1/consumer/suggestions", json={},
                       headers=CONSUMER_A).status_code == 422
    bad = dict(SUGGESTION, category="Nope")
    assert client.post("/api/v1/consumer/suggestions", json=bad,
                       headers=CONSUMER_A).status_code == 422


def test_consumer_cannot_view_another_suggestion():
    from conftest import make_client, make_repo

    client = make_client(make_repo())
    sid = client.post("/api/v1/consumer/suggestions", json=SUGGESTION,
                      headers=CONSUMER_A).json()["suggestion_id"]
    assert client.get(f"/api/v1/consumer/suggestions/{sid}",
                      headers=CONSUMER_B).status_code == 403
    assert client.get("/api/v1/consumer/suggestions",
                      headers=CONSUMER_B).json() == []


def test_officer_suggestions_list_detail_update_masked():
    from conftest import make_client, make_repo

    client = make_client(make_repo())
    sid = client.post("/api/v1/consumer/suggestions", json=SUGGESTION,
                      headers=CONSUMER_A).json()["suggestion_id"]
    rows = client.get("/api/v1/officer/suggestions",
                      headers=OFFICER).json()
    assert len(rows) == 1
    assert rows[0]["consumer"] != "user-a"  # privacy-safe identifier
    assert "***" in rows[0]["consumer"]
    assert "consumer_user_id" not in rows[0]
    detail = client.get(f"/api/v1/officer/suggestions/{sid}",
                        headers=OFFICER).json()
    assert detail["timeline"]
    moved = client.patch(f"/api/v1/officer/suggestions/{sid}",
                         json={"to_status": "UNDER_REVIEW",
                               "note": "looking into QR cost"},
                         headers=OFFICER).json()
    assert moved["status"] == "UNDER_REVIEW"
    assert moved["officer_note"] == "looking into QR cost"
    assert [e["to_status"] for e in moved["timeline"]
            if e["event_type"] == "STATUS_CHANGE"] == ["UNDER_REVIEW"]
    # Illegal transition rejected by lifecycle, not invented.
    bad = client.patch(f"/api/v1/officer/suggestions/{sid}",
                       json={"to_status": "SUBMITTED"}, headers=OFFICER)
    assert bad.status_code == 422


def test_officer_suggestion_filters_and_search():
    from conftest import make_client, make_repo

    client = make_client(make_repo())
    client.post("/api/v1/consumer/suggestions", json=SUGGESTION,
                headers=CONSUMER_A)
    client.post("/api/v1/consumer/suggestions",
                json=dict(SUGGESTION, title="Label font sizes",
                          category="Label transparency"),
                headers=CONSUMER_B)
    assert len(client.get("/api/v1/officer/suggestions",
                          headers=OFFICER).json()) == 2
    assert len(client.get("/api/v1/officer/suggestions",
                          params={"category": "Label transparency"},
                          headers=OFFICER).json()) == 1
    assert len(client.get("/api/v1/officer/suggestions",
                          params={"q": "tamper-proof"},
                          headers=OFFICER).json()) == 1
    assert len(client.get("/api/v1/officer/suggestions",
                          params={"status": "ACTIONED"},
                          headers=OFFICER).json()) == 0


def test_consumer_cannot_access_officer_suggestions():
    from conftest import make_client, make_repo

    client = make_client(make_repo())
    client.post("/api/v1/consumer/suggestions", json=SUGGESTION,
                headers=CONSUMER_A)
    assert client.get("/api/v1/officer/suggestions",
                      headers=CONSUMER_A).status_code == 403
    assert client.patch("/api/v1/officer/suggestions/x",
                        json={"to_status": "CLOSED"},
                        headers=CONSUMER_A).status_code in (401, 403, 404)


def test_consumer_cannot_change_suggestion_status():
    from conftest import make_client, make_repo

    client = make_client(make_repo())
    sid = client.post("/api/v1/consumer/suggestions", json=SUGGESTION,
                      headers=CONSUMER_A).json()["suggestion_id"]
    # No consumer status endpoint exists: only officer PATCH can move it.
    res = client.post(f"/api/v1/consumer/suggestions/{sid}",
                      json={"to_status": "CLOSED"}, headers=CONSUMER_A)
    assert res.status_code in (404, 405)


# ------------------------------------------------- scoped reports ---
def _complain(client, headers, product):
    return client.post(
        "/api/v1/complaints",
        json={"product_name": product, "retailer": "Shop", "city": "Blr",
              "description": "[Category: Incorrect MRP] wrong MRP."},
        headers=headers).json()


def test_consumer_sees_only_own_reports():
    from conftest import make_client, make_repo

    repo = make_repo()
    _setup_inspected_product(repo, "Report Atta")
    client = make_client(repo)
    _complain(client, CONSUMER_A, "Report Atta")
    _complain(client, CONSUMER_B, "Other Goods")
    mine = client.get("/api/v1/consumer/reports",
                      headers=CONSUMER_A).json()
    assert mine["count"] == 1
    assert mine["reports"][0]["product_name"] == "Report Atta"
    # Related inspection resolved server-side for the own complaint.
    related = mine["reports"][0]["related_inspection"]
    assert related is not None
    assert related["report_available"] is True
    assert related["status"] in ("VERIFIED", "NEEDS_REVIEW", "NOT_VERIFIED")
    theirs = client.get("/api/v1/consumer/reports",
                        headers=CONSUMER_B).json()
    assert theirs["count"] == 1
    assert theirs["reports"][0]["related_inspection"] is None
    assert theirs["reports"][0]["state_note"] == \
        "Submitted — awaiting review."


def test_unrelated_inspections_never_shown():
    from conftest import make_client, make_repo

    repo = make_repo()
    _setup_inspected_product(repo, "Secret Brand X")
    client = make_client(repo)
    _complain(client, CONSUMER_A, "Something Else")
    body = client.get("/api/v1/consumer/reports",
                      headers=CONSUMER_A).json()["reports"][0]
    assert "Secret Brand X" not in str(body)
    assert body["related_inspection"] is None


def test_report_detail_timeline_and_declarations():
    from conftest import make_client, make_repo

    repo = make_repo()
    _setup_inspected_product(repo, "Report Atta")
    client = make_client(repo)
    cid = _complain(client, CONSUMER_A, "Report Atta")["complaint_id"]
    detail = client.get(f"/api/v1/consumer/reports/{cid}",
                        headers=CONSUMER_A).json()
    assert detail["timeline"], "real lifecycle timeline"
    assert detail["declarations"], "persisted declaration evidence"
    assert detail["related_inspection"]["report_available"] is True
    # Another consumer gets 403, not the data.
    assert client.get(f"/api/v1/consumer/reports/{cid}",
                      headers=CONSUMER_B).status_code == 403


def test_report_without_inspection_state():
    from conftest import make_client, make_repo

    client = make_client(make_repo())
    cid = _complain(client, CONSUMER_A, "Ghost Product")["complaint_id"]
    detail = client.get(f"/api/v1/consumer/reports/{cid}",
                        headers=CONSUMER_A).json()
    assert detail["related_inspection"] is None
    assert detail["state_note"] == "Submitted — awaiting review."
    assert "Not analyzed" not in str(detail)
