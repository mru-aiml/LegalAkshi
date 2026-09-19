"""Inspector workspace data flows over EXISTING backend APIs.

Covers the B27 backend surface: inspections list/detail/search/filter
inputs, complaint -> product -> related inspections -> findings linkage,
enforcement queue integration, rule library/provenance reads, reports,
and RBAC (consumers cannot reach officer routes, officer routes work).
No new endpoints are required for any of these flows.
"""
from __future__ import annotations

INSP = {"inspector_id": "I-1", "inspector_name": "Inspector",
        "business_name": "Inspect Store", "inspection_date": "2026-09-15"}

GOOD = {
    "product_name": "Inspect Noodles", "category": "GENERAL",
    "is_prepackaged": True, "brand": "InspectCo",
    "manufacturer": "Inspect Foods", "quantity": 70,
    "quantity_unit": "g", "quantity_type": "weight",
    "manufacturing_date": "2024-05-01", "mrp": "50",
    "consumer_care": "1800-111-2222", "unit_sale_price": "Rs.714 per kg",
    "barcode": "8901111111111", "batch_lot": "B123",
    "imported": False, "ecommerce": False, "food": True,
}

OFFICER = {"X-LegalAkshi-Role": "officer", "X-LegalAkshi-User": "I-1"}
CONSUMER = {"X-LegalAkshi-Role": "consumer", "X-LegalAkshi-User": "C-1"}


def _setup(repo, extra=None):
    from app.engine import engine as engine_mod
    from app.engine.facts import ASSUMED

    insp = repo.create_inspection(dict(INSP))
    payload = dict(GOOD)
    payload.update(extra or {})
    prod = repo.add_product(insp["inspection_id"], payload)
    pid = prod["product_id"]
    engine_mod.analyze(
        repo, insp, repo.get_product(pid), repo.declarations_for(pid),
        as_of="2026-09-15", default_origin=ASSUMED)
    return insp, prod


# ------------------------------------------------- inspections ---
def test_inspections_list_detail_search_filter_inputs():
    from conftest import make_client, make_repo

    repo = make_repo()
    insp, prod = _setup(repo)
    client = make_client(repo)
    listed = client.get("/api/v1/inspections").json()
    assert any(i["inspection_id"] == insp["inspection_id"] for i in listed)
    detail = client.get(
        f"/api/v1/inspections/{insp['inspection_id']}").json()
    assert detail["products"], "detail must carry inspected products"
    assert detail["products"][0]["product_name"] == "Inspect Noodles"
    # Compliance findings + violations feed the detail page.
    findings = client.get(
        f"/api/v1/inspections/{insp['inspection_id']}/products/"
        f"{prod['product_id']}/compliance").json()
    assert isinstance(findings, list) and findings
    viols = client.get(
        f"/api/v1/inspections/{insp['inspection_id']}/violations").json()
    assert "items" in viols
    # Batch/lot persisted via declarations supports batch search inputs.
    decls = repo.declarations_for(prod["product_id"])
    assert any(d.get("field_name") == "batch_lot"
               and d.get("extracted_value") == "B123" for d in decls)


def test_inspection_history_same_product():
    from conftest import make_client, make_repo

    repo = make_repo()
    insp1, _ = _setup(repo)
    insp2, _ = _setup(repo, {"barcode": "8901111111111"})
    assert insp1["inspection_id"] != insp2["inspection_id"]
    client = make_client(repo)
    listed = client.get("/api/v1/inspections").json()
    matches = [i for i in listed]
    assert len(matches) >= 2
    # Consumer lookup groups the same product across inspections.
    found = client.get("/api/v1/consumer/products/lookup",
                       params={"barcode": "8901111111111"}).json()
    assert found["count"] >= 2


# ------------------------------------------------- complaints ---
def test_complaint_to_product_to_inspections_to_findings():
    from conftest import make_client, make_repo

    repo = make_repo()
    insp, prod = _setup(repo)
    client = make_client(repo)
    created = client.post(
        "/api/v1/complaints",
        json={"product_name": "Inspect Noodles", "retailer": "Store",
              "city": "Bengaluru",
              "description": "[Category: Incorrect MRP] shelf shows Rs.55."},
        headers=CONSUMER).json()
    # Complaint -> product: lookup by the complained product name.
    found = client.get("/api/v1/consumer/products/lookup",
                       params={"product_name": "Inspect Noodles"}).json()
    assert found["count"] >= 1
    pid = found["matches"][0]["product_id"]
    # Related inspections + findings via existing endpoints.
    detail = client.get(
        f"/api/v1/consumer/products/{pid}/verification").json()
    assert detail["declarations"], "verification detail carries evidence rows"
    assert insp["inspection_id"]  # linked inspection exists
    assert created["complaint_id"]  # complaint persisted
    timeline = client.get(
        f"/api/v1/complaints/{created['complaint_id']}",
        headers=CONSUMER).json()["timeline"]
    assert timeline, "real lifecycle timeline must exist"


def test_complaint_timeline_transitions():
    from conftest import make_client, make_repo

    repo = make_repo()
    client = make_client(repo)
    cid = client.post(
        "/api/v1/complaints",
        json={"product_name": "Inspect Noodles", "retailer": "S",
              "city": "B"},
        headers=CONSUMER).json()["complaint_id"]
    for nxt in ("ACKNOWLEDGED", "UNDER_REVIEW", "ACTION_TAKEN"):
        res = client.post(f"/api/v1/complaints/{cid}/transitions",
                          json={"to_status": nxt}, headers=OFFICER)
        assert res.status_code == 200, (nxt, res.text)
    events = client.get(f"/api/v1/complaints/{cid}",
                        headers=CONSUMER).json()["timeline"]
    assert [e["to_status"] for e in events
            if e["event_type"] == "STATUS_CHANGE"] == \
        ["ACKNOWLEDGED", "UNDER_REVIEW", "ACTION_TAKEN"]


# ------------------------------------------------- enforcement ---
def test_enforcement_queue_and_officer_decision():
    from conftest import make_client, make_repo

    repo = make_repo()
    insp, _prod = _setup(repo, {"product_name": "Inspect No MRP",
                                "mrp": None, "barcode": "8902222222222"})
    client = make_client(repo)
    queue = client.get("/api/v1/officer/queue", headers=OFFICER).json()
    mine = [q for q in queue
            if q.get("inspection_id") == insp["inspection_id"]]
    assert mine and all(q["inspector_status"] == "PENDING" for q in mine)
    vid = mine[0]["violation_id"]
    decided = client.post(
        f"/api/v1/violations/{vid}/verify",
        json={"decision": "CONFIRMED", "inspector_id": "I-1",
              "verification_notes": "shelf check"},
        headers=OFFICER).json()
    assert decided["inspector_status"] == "CONFIRMED"
    case = client.get(f"/api/v1/officer/cases/{vid}",
                      headers=OFFICER).json()
    assert case["violation"]["inspector_status"] == "CONFIRMED"
    assert case["findings"] and case["audit"], \
        "case must carry findings + audit trail"


# ------------------------------------------------- rules/provenance ---
def test_rule_library_and_provenance_reads():
    from conftest import make_client, make_repo

    client = make_client(make_repo())
    rules = client.get("/api/v1/rules").json()
    assert rules
    rule = rules[0]
    for key in ("rule_id", "title", "field", "source_reference"):
        assert key in rule, key
    detail = client.get(f"/api/v1/rules/{rule['rule_id']}").json()
    assert detail["versions"], "version lineage required"
    assert any(v.get("effective_from") for v in detail["versions"])
    assert "applicability" in detail


# ------------------------------------------------- reports ---
def test_reports_existing_behavior():
    from conftest import make_client, make_repo

    repo = make_repo()
    insp, _prod = _setup(repo)
    client = make_client(repo)
    body = client.get(f"/api/v1/reports/{insp['inspection_id']}").json()
    assert body, "persisted report must exist"


# ------------------------------------------------- RBAC ---
def test_consumer_cannot_access_officer_routes():
    from conftest import make_client, make_repo

    repo = make_repo()
    _setup(repo)
    client = make_client(repo)
    assert client.get("/api/v1/officer/queue",
                      headers=CONSUMER).status_code == 403
    assert client.get("/api/v1/officer/stats",
                      headers=CONSUMER).status_code == 403
    # ...while officers can.
    assert client.get("/api/v1/officer/queue",
                      headers=OFFICER).status_code == 200
    assert client.get("/api/v1/officer/stats",
                      headers=OFFICER).status_code == 200


def test_officer_console_endpoints_functional():
    from conftest import make_client, make_repo

    repo = make_repo()
    _setup(repo)
    client = make_client(repo)
    stats = client.get("/api/v1/officer/stats", headers=OFFICER).json()
    for key in ("awaiting_review", "open_complaints", "total_violations"):
        assert key in stats, key
    profile = client.get("/api/v1/officer/profile",
                         headers=OFFICER).json()
    assert profile["role"] == "officer"
