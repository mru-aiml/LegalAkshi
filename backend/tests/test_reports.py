"""Report generation tests — one service, three formats, persisted data only."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from conftest import make_client, make_repo

INSP = {"inspector_id": "R-1", "inspector_name": "Reporter",
        "business_name": "Report Store", "inspection_date": "2026-09-15"}

PRODUCT = {"product_name": "Report Atta", "category": "GENERAL",
           "is_prepackaged": True, "manufacturer": "Report Foods",
           "quantity": 5, "quantity_unit": "kg", "quantity_type": "weight",
           "manufacturing_date": "2024-05-01",
           "consumer_care": "1800-000", "unit_sale_price": "Rs.50 per kg"}


@pytest.fixture()
def analyzed():
    repo = make_repo()
    client = make_client(repo)
    iid = client.post("/api/v1/inspections", json=INSP).json()["inspection_id"]
    bad = dict(PRODUCT)
    bad.pop("mrp", None)
    # PRODUCT has no mrp key; drop manufacturer instead for a FAIL
    bad2 = dict(PRODUCT)
    bad2.pop("manufacturer")
    client.post(f"/api/v1/inspections/{iid}/analyze", json={"product": bad2})
    return client, iid


def test_json_report_shape(analyzed):
    client, iid = analyzed
    body = client.get(f"/api/v1/reports/{iid}").json()
    assert body["report_id"] == iid and body["findings"]
    assert body["score"]["out_of"] == 100


def test_html_report_contains_legal_references(analyzed):
    client, iid = analyzed
    r = client.get(f"/api/v1/reports/{iid}?format=html")
    assert r.status_code == 200, r.text
    assert "text/html" in r.headers["content-type"]
    assert "CHK-MANUFACTURER" in r.text
    assert "Rule 6(1)(a)" in r.text
    assert "FAIL" in r.text  # no fabricated findings: real FAIL present


def test_pdf_report_is_real_pdf(analyzed):
    client, iid = analyzed
    r = client.get(f"/api/v1/reports/{iid}?format=pdf")
    assert r.status_code == 200, r.text
    assert r.headers["content-type"] == "application/pdf"
    assert r.content.startswith(b"%PDF")
    assert b"CHK-MANUFACTURER" in r.content
    assert "attachment" in r.headers.get("content-disposition", "")


def test_bad_format_rejected(analyzed):
    client, iid = analyzed
    assert client.get(f"/api/v1/reports/{iid}?format=docx").status_code == 422


def test_missing_report_404(analyzed):
    client, _ = analyzed
    import uuid
    assert client.get(f"/api/v1/reports/{uuid.uuid4()}").status_code == 404
