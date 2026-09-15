"""Unit test matrix — MemoryRepo (test-only) + deterministic engine.

No internet, no PostgreSQL. Covers: MRP/manufacturer/common-name/net-qty/
mfg-date PASS+FAIL, imported + e-commerce applicability, NOT_APPLICABLE,
NEEDS_REVIEW, historical/future/superseded versions, scoring edges,
FAIL -> PENDING violation.
"""
from __future__ import annotations

import pytest

from app.engine import engine as engine_mod
from app.engine import scoring as scoring_mod
from app.engine.facts import ASSUMED
from app.repositories.memory import MemoryRepo

BASE_PRODUCT = {
    "product_name": "Demo Whole Wheat Atta", "category": "GENERAL",
    "is_prepackaged": True, "manufacturer": "Demo Foods Ltd, Kolkata",
    "quantity": 5, "quantity_unit": "kg", "quantity_type": "weight",
    "manufacturing_date": "2024-05-01", "mrp": "250",
    "consumer_care": "1800-000-000", "unit_sale_price": "Rs.50 per kg",
    "imported": False, "ecommerce": False,
}

INSP = {"inspector_id": "T-1", "inspector_name": "Tester",
        "business_name": "Test Store", "inspection_date": "2026-09-15"}


@pytest.fixture()
def repo():
    return MemoryRepo()


def _run(repo, decl=None, as_of="2026-09-15", origin=ASSUMED):
    insp = repo.create_inspection(dict(INSP))
    merged = dict(BASE_PRODUCT)
    if decl:
        merged.update(decl)
    prod = repo.add_product(insp["inspection_id"], merged)
    pid = prod["product_id"]
    return engine_mod.analyze(
        repo, insp, repo.get_product(pid), repo.declarations_for(pid),
        as_of=as_of, default_origin=origin)


def _status(res, check_id):
    for f in res["findings"]:
        if f["rule_id"] == check_id:
            return f["status"]
    raise AssertionError(f"missing finding {check_id}")


# --- core checks -----------------------------------------------------------
def test_mrp_pass(repo):
    assert _status(_run(repo), "CHK-MRP") == "PASS"


def test_mrp_fail(repo):
    assert _status(_run(repo, {"mrp": None}), "CHK-MRP") == "FAIL"


def test_manufacturer_pass(repo):
    assert _status(_run(repo), "CHK-MANUFACTURER") == "PASS"


def test_manufacturer_fail(repo):
    res = _run(repo, {"manufacturer": None, "qr_code": None})
    assert _status(res, "CHK-MANUFACTURER") == "FAIL"


def test_common_name_pass(repo):
    assert _status(_run(repo), "CHK-COMMON-NAME") == "PASS"


def test_common_name_fail(repo):
    assert _status(_run(repo, {"product_name": ""}), "CHK-COMMON-NAME") == "FAIL"


def test_net_qty_pass(repo):
    assert _status(_run(repo), "CHK-NET-QTY") == "PASS"


def test_net_qty_fail(repo):
    assert _status(_run(repo, {"quantity": None}), "CHK-NET-QTY") == "FAIL"


def test_mfg_date_pass(repo):
    assert _status(_run(repo), "CHK-MFG-DATE") == "PASS"


def test_mfg_date_fail(repo):
    assert _status(_run(repo, {"manufacturing_date": "not-a-date"}),
                   "CHK-MFG-DATE") == "FAIL"


# --- applicability ----------------------------------------------------------
def test_imported_requires_country_of_origin(repo):
    res = _run(repo, {"imported": True, "country_of_origin": None})
    assert _status(res, "CHK-COUNTRY-ORIGIN") == "FAIL"
    res2 = _run(repo, {"imported": True, "country_of_origin": "Thailand"})
    assert _status(res2, "CHK-COUNTRY-ORIGIN") == "PASS"


def test_ecommerce_applicability(repo):
    res = _run(repo, {"ecommerce": True,
                      "source_listing_url": "https://example.invalid/x"})
    assert _status(res, "CHK-ECOMMERCE-DECL") == "PASS"
    res2 = _run(repo, {"ecommerce": True, "imported": True,
                       "country_of_origin": "UAE",
                       "source_listing_url": "https://example.invalid/x"})
    assert _status(res2, "CHK-ECOMMERCE-COO-FILTER") == "FAIL"


def test_not_applicable_never_fails(repo):
    res = _run(repo)  # domestic, offline, non-perishable, general
    for f in res["findings"]:
        if f["rule_id"] in ("CHK-COUNTRY-ORIGIN", "CHK-ECOMMERCE-DECL",
                            "CHK-ECOMMERCE-COO-FILTER", "CHK-GM-FOOD",
                            "CHK-BEST-BEFORE", "CHK-VEG-NONVEG"):
            assert f["status"] == "NOT_APPLICABLE", f


def test_needs_review_manual_catchall(repo):
    res = _run(repo)
    assert _status(res, "CHK-OTHER-MATTERS") == "NEEDS_REVIEW"


def test_low_ocr_confidence_forces_review(repo):
    insp = repo.create_inspection(dict(INSP))
    prod = repo.add_product(insp["inspection_id"],
                            {k: v for k, v in BASE_PRODUCT.items() if k != "mrp"})
    pid = prod["product_id"]
    decls = list(repo.declarations_for(pid))  # manual declarations for rest
    decls.append({"field_name": "mrp", "extracted_value": "250",
                  "normalized_value": None, "confidence": 0.15,
                  "ocr_engine": "legalakshi-ocr-v1", "evidence_id": None})
    res = engine_mod.analyze(repo, insp, repo.get_product(pid), decls,
                             as_of="2026-09-15", default_origin=ASSUMED)
    assert _status(res, "CHK-MRP") == "NEEDS_REVIEW"
    assert res["score"]["finalizable"] is False


# --- temporal versions -------------------------------------------------------
def test_historical_rule_selection(repo):
    old = {v["check_id"]: v for v in repo.active_rule_versions("2020-01-01")}
    now = {v["check_id"]: v for v in repo.active_rule_versions("2026-09-15")}
    assert old["CHK-MRP"]["rule_version_id"] == \
        "55555555-5555-5555-5555-555555550003"
    assert now["CHK-MRP"]["rule_version_id"] == \
        "55555555-5555-5555-5555-555555550030"


def test_superseded_version_not_selected_now(repo):
    now = {v["check_id"]: v for v in repo.active_rule_versions("2026-09-15")}
    assert now["CHK-MFG-DATE"]["rule_version_id"] == \
        "55555555-5555-5555-5555-555555550029"
    assert now["CHK-MANUFACTURER"]["rule_version_id"] == \
        "55555555-5555-5555-5555-555555550025"


def test_future_rule_excluded(repo):
    now = {v["check_id"]: v for v in repo.active_rule_versions("2026-09-15")}
    assert now["CHK-ECOMMERCE-COO-FILTER"]["rule_version_id"] == \
        "55555555-5555-5555-5555-555555550007"  # v1 IN_FORCE
    later = {v["check_id"]: v for v in repo.active_rule_versions("2030-01-01")}
    # v1 expired 2027-07-01, v2 still NOT_YET_IN_FORCE -> check absent
    assert "CHK-ECOMMERCE-COO-FILTER" not in later


# --- scoring ------------------------------------------------------------------
def test_score_calculation(repo):
    policy = repo.scoring_policy("DEFAULT-2026")
    out = scoring_mod.score(
        [{"rule_id": "CHK-MRP", "status": "PASS"},
         {"rule_id": "CHK-NET-QTY", "status": "FAIL"}], policy)
    # MRP 20 pass / (20 + 15) denominator
    assert out["value"] == pytest.approx(57.1, abs=0.2)


def test_na_excluded_from_denominator(repo):
    policy = repo.scoring_policy("DEFAULT-2026")
    out = scoring_mod.score(
        [{"rule_id": "CHK-MRP", "status": "PASS"},
         {"rule_id": "CHK-COUNTRY-ORIGIN", "status": "NOT_APPLICABLE"}], policy)
    assert out["value"] == 100.0


def test_review_prevents_finalization(repo):
    policy = repo.scoring_policy("DEFAULT-2026")
    out = scoring_mod.score(
        [{"rule_id": "CHK-MRP", "status": "PASS"},
         {"rule_id": "CHK-OTHER-MATTERS", "status": "NEEDS_REVIEW"}], policy)
    assert out["finalizable"] is False
    assert out["status"] == "NEEDS_REVIEW"


def test_weight_zero_check_excluded(repo):
    policy = repo.scoring_policy("DEFAULT-2026")
    out = scoring_mod.score([{"rule_id": "CHK-OTHER-MATTERS", "status": "FAIL"}],
                            policy)
    assert out["value"] == 0.0  # denominator 0, not a crash


# --- violations ---------------------------------------------------------------
def test_fail_creates_pending_violation(repo):
    res = _run(repo, {"mrp": None})
    assert res["violation_ids"]
    insp_id = res["inspection_id"]
    violations = repo.violations_for(insp_id)
    assert violations and all(v["inspector_status"] == "PENDING" for v in violations)
