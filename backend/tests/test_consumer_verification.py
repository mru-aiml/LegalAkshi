"""Consumer two-sided experience: verification lookup, states, nutrition.

Officer workflow (photos -> OCR -> review -> rule engine -> decision) is
untouched; these tests prove the consumer side reads ONLY persisted
inspection/verification state and never fabricates results.
"""
from __future__ import annotations

import pathlib

INSP = {"inspector_id": "V-1", "inspector_name": "Verifier",
        "business_name": "Verify Store", "inspection_date": "2026-09-15"}

GOOD = {
    "product_name": "Verify Atta 5kg", "category": "GENERAL",
    "is_prepackaged": True, "brand": "Verify", "manufacturer": "Verify Foods",
    "quantity": 5, "quantity_unit": "kg", "quantity_type": "weight",
    "manufacturing_date": "2024-05-01", "mrp": "250",
    "consumer_care": "1800-111-2222", "unit_sale_price": "Rs.50 per kg",
    "barcode": "8901234567890",
    "imported": False, "ecommerce": False, "food": True,
}


def _setup_product(repo, extra=None, analyze=True):
    insp = repo.create_inspection(dict(INSP))
    payload = dict(GOOD)
    payload.update(extra or {})
    prod = repo.add_product(insp["inspection_id"], payload)
    pid = prod["product_id"]
    if analyze:
        from app.engine import engine as engine_mod
        from app.engine.facts import ASSUMED

        engine_mod.analyze(
            repo, insp, repo.get_product(pid), repo.declarations_for(pid),
            as_of="2026-09-15", default_origin=ASSUMED)
    return insp, prod


def _result_statuses(repo, insp, prod):
    return {r.get("check_id"): r.get("result")
            for r in repo.results_for(insp["inspection_id"],
                                      prod["product_id"])}


# ------------------------------------------------- lookup + states ---
def test_verified_product_lookup_by_barcode():
    from conftest import make_client, make_repo

    repo = make_repo()
    insp, prod = _setup_product(repo)
    statuses = _result_statuses(repo, insp, prod)
    assert statuses, "engine must persist compliance results"
    # Only the zero-weight informational catch-all may sit in review;
    # every weighted check must be decisive for a VERIFIED product.
    policy = repo.scoring_policy("DEFAULT-2026") or {}
    weights = {w.get("check_id"): w.get("weight", 0)
               for w in policy.get("weights", [])}
    blocking = [c for c, s in statuses.items()
                if s == "NEEDS_REVIEW" and float(weights.get(c, 0) or 0) != 0]
    assert blocking == [], statuses
    assert repo.violations_for(insp["inspection_id"]) == []
    client = make_client(repo)
    res = client.get("/api/v1/consumer/products/lookup",
                     params={"barcode": "8901234567890"})
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["count"] == 1
    match = body["matches"][0]
    assert match["status"] == "VERIFIED"
    assert match["product_name"] == "Verify Atta 5kg"
    assert match["pack_size"] == "5 kg"
    assert match["manufacturer"] == "Verify Foods"
    # No internals leak.
    for forbidden in ("rule_version_id", "compliance_result_id",
                      "violation_id", "confidence", "ocr", "audit"):
        assert forbidden not in res.text.lower(), forbidden


def test_unknown_barcode_has_no_matches():
    from conftest import make_client, make_repo

    repo = make_repo()
    _setup_product(repo)
    client = make_client(repo)
    body = client.get("/api/v1/consumer/products/lookup",
                      params={"barcode": "0000000000000"}).json()
    assert body["count"] == 0
    assert body["matches"] == []


def test_product_without_results_is_not_verified():
    from conftest import make_client, make_repo

    repo = make_repo()
    _insp, prod = _setup_product(repo, analyze=False)
    client = make_client(repo)
    body = client.get("/api/v1/consumer/products/lookup",
                      params={"barcode": "8901234567890"}).json()
    assert body["matches"][0]["status"] == "NOT_VERIFIED"
    assert "No verified LegalAkshi inspection" in body["matches"][0]["reason"]


def test_pending_violation_means_needs_review():
    from conftest import make_client, make_repo

    repo = make_repo()
    insp, prod = _setup_product(
        repo, {"product_name": "Verify Atta 5kg No MRP", "mrp": None,
               "barcode": "8901234567891"})
    vids = [v["violation_id"]
            for v in repo.violations_for(insp["inspection_id"])]
    assert vids, "missing MRP must raise a potential violation"
    client = make_client(repo)
    body = client.get("/api/v1/consumer/products/lookup",
                      params={"barcode": "8901234567891"}).json()
    assert body["matches"][0]["status"] == "NEEDS_REVIEW"


def test_confirmed_violation_is_not_verified_not_illegal():
    from conftest import make_client, make_repo

    repo = make_repo()
    insp, prod = _setup_product(
        repo, {"product_name": "Verify Atta 5kg No MRP", "mrp": None,
               "barcode": "8901234567891"})
    vid = repo.violations_for(insp["inspection_id"])[0]["violation_id"]
    repo.verify_violation(vid, "CONFIRMED", "V-1", "checked on shelf")
    client = make_client(repo)
    match = client.get("/api/v1/consumer/products/lookup",
                       params={"barcode": "8901234567891"}).json()["matches"][0]
    assert match["status"] == "NOT_VERIFIED"
    assert "does not mean the product is illegal or unsafe" in match["reason"]
    detail = client.get(
        f"/api/v1/consumer/products/{prod['product_id']}/verification").json()
    assert detail["status"] == "NOT_VERIFIED"
    assert detail["not_verified_note"] is not None


def test_verification_detail_includes_batch_lot():
    from conftest import make_client, make_repo

    repo = make_repo()
    _insp, prod = _setup_product(repo, {"batch_lot": "M50924A"})
    client = make_client(repo)
    detail = client.get(
        f"/api/v1/consumer/products/{prod['product_id']}/verification").json()
    assert detail["batch_lot"] == "M50924A"


def test_lookup_requires_a_parameter():
    from conftest import make_client, make_repo

    client = make_client(make_repo())
    assert client.get("/api/v1/consumer/products/lookup").status_code == 422


def test_name_search_matches():
    from conftest import make_client, make_repo

    repo = make_repo()
    _setup_product(repo)
    client = make_client(repo)
    body = client.get("/api/v1/consumer/products/lookup",
                      params={"product_name": "verify atta"}).json()
    assert body["count"] >= 1


# ------------------------------------------------- verification detail ---
def test_verification_detail_declarations_from_evidence():
    from conftest import make_client, make_repo

    repo = make_repo()
    _insp, prod = _setup_product(repo)
    client = make_client(repo)
    detail = client.get(
        f"/api/v1/consumer/products/{prod['product_id']}/verification").json()
    assert detail["status"] == "VERIFIED"
    labels = [d["label"] for d in detail["declarations"]]
    for expected in ("Product identity", "Net quantity", "MRP",
                     "Manufacturer", "FSSAI licence", "Date declaration",
                     "Consumer-care details", "Ingredients"):
        assert expected in labels, labels
    # Only checks actually supported show Verified; the rest say so.
    assert all(d["state"] in ("Verified", "Not verified", "Needs review")
               for d in detail["declarations"])
    assert "LEGAL" not in str(detail) and "ILLEGAL" not in str(detail)


def test_consumer_cannot_mutate_verification():
    from conftest import make_client, make_repo

    repo = make_repo()
    _setup_product(repo)
    client = make_client(repo)
    for method in ("post", "put", "patch"):
        res = getattr(client, method)(
            "/api/v1/consumer/products/lookup", json={})
        assert res.status_code in (404, 405), (method, res.status_code)
    res = client.delete("/api/v1/consumer/products/lookup")
    assert res.status_code in (404, 405), res.status_code
    res = client.post("/api/v1/consumer/overview", json={})
    assert res.status_code in (404, 405)


# ------------------------------------------------- nutrition ---
def test_nutrition_available_from_persisted_declarations():
    from conftest import make_client, make_repo

    repo = make_repo()
    _insp, prod = _setup_product(repo, {"energy": "450 kcal",
                                        "protein": "8 g",
                                        "total_fat": "15 g"})
    client = make_client(repo)
    body = client.get(
        f"/api/v1/consumer/products/{prod['product_id']}/nutrition").json()
    assert body["available"] is True
    by_name = {n["name"]: n for n in body["nutrients"]}
    assert by_name["Energy"]["value"] == "450 kcal"
    assert by_name["Energy"]["about"], "each nutrient needs an explanation"
    assert by_name["Protein"]["value"] == "8 g"


def test_nutrition_unavailable_state():
    from conftest import make_client, make_repo

    repo = make_repo()
    _insp, prod = _setup_product(repo)
    client = make_client(repo)
    body = client.get(
        f"/api/v1/consumer/products/{prod['product_id']}/nutrition").json()
    assert body["available"] is False
    assert body["nutrients"] == []
    assert "not been verified" in (body["note"] or "")


# ------------------------------------------------- complaints ---
def test_complaint_creation_and_status_lifecycle():
    from conftest import make_client, make_repo

    repo = make_repo()
    client = make_client(repo)
    who = {"X-LegalAkshi-Role": "consumer",
           "X-LegalAkshi-User": "consumer-1"}
    created = client.post(
        "/api/v1/complaints",
        json={"product_name": "Verify Atta 5kg", "retailer": "Store",
              "city": "Bengaluru", "severity": "Medium",
              "description": "[Category: Incorrect MRP] MRP mismatch."},
        headers=who).json()
    assert created["status"] == "SUBMITTED"
    mine = client.get("/api/v1/complaints", headers=who).json()
    assert any(c["complaint_id"] == created["complaint_id"] for c in mine)
    officer = {"X-LegalAkshi-Role": "officer", "X-LegalAkshi-User": "V-1"}
    ack = client.post(
        f"/api/v1/complaints/{created['complaint_id']}/transitions",
        json={"to_status": "ACKNOWLEDGED", "note": "seen"},
        headers=officer)
    assert ack.status_code == 200, ack.text
    resp = client.post(
        f"/api/v1/complaints/{created['complaint_id']}/transitions",
        json={"to_status": "UNDER_REVIEW", "note": "investigating"},
        headers=officer)
    assert resp.status_code == 200, resp.text
    moved = resp.json()
    assert moved["status"] == "UNDER_REVIEW"
    assert moved["timeline"], "timeline must exist for consumer view"


def test_consumer_overview_uses_real_data():
    from conftest import make_client, make_repo

    repo = make_repo()
    _setup_product(repo)
    client = make_client(repo)
    body = client.get("/api/v1/consumer/overview").json()
    assert body["products_checked"] >= 1
    assert body["verified_products"] >= 1
    assert body["complaints_raised"] == 0
    assert body["recent_checks"], "recent checks must list real products"
    assert body["recent_checks"][0]["status"] == "VERIFIED"


# ------------------------------------------------- navigation/routes ---
def test_consumer_navigation_and_routes_static():
    root = pathlib.Path(__file__).resolve().parents[2]
    app_tsx = (root / "artifacts" / "nutricheck" / "src" / "App.tsx"
               ).read_text(encoding="utf-8")
    # New consumer navigation: verification-first, no OCR, no Lab check.
    assert "Verify a product" in app_tsx
    assert "href: '/verify-product'" in app_tsx
    assert "href: '/complaints'" in app_tsx
    assert "href: '/reports'" in app_tsx
    assert "href: '/lab-check'" not in app_tsx  # unlinked from nav
    assert "Scan a label" not in app_tsx  # consumer OCR retired
    assert "label: 'Lab check'" not in app_tsx
    # New consumer routes exist.
    assert 'path="/verify-product"' in app_tsx
    assert 'path="/verify-product/:productId"' in app_tsx
    assert 'path="/nutrition/:productId"' in app_tsx
    # Consumer OCR upload retired to the verification flow.
    assert 'Redirect to="/verify-product"' in app_tsx
    # Lab-check route preserved internally (not deleted).
    assert 'path="/lab-check"' in app_tsx
    # Officer workflow routes remain unchanged.
    for route in ('path="/inspector/dashboard"',
                  'path="/inspector/scan"',
                  'path="/inspector/reports"',
                  'path="/inspector/rules"',
                  'path="/inspector/complaints"',
                  'path="/inspector/audit/:id"',
                  'path="/inspector/profile"'):
        assert route in app_tsx, route
    # No LEGAL/ILLEGAL consumer labels.
    assert 'LEGAL"' not in app_tsx.replace("ILLEGAL", "")
