"""PostgreSQL integration path — runs ONLY when PostgreSQL is reachable.

Applies backend/legalakshi_schema_v3_final.sql (authoritative, verbatim),
runs inspection -> product -> analyze -> verify, and asserts rows are
actually persisted. Also proves the exclusion constraint rejects
overlapping rule versions.

If PostgreSQL is unavailable the test SKIPS with an explicit message —
it never pretends to pass.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

SCHEMA_PATH = (Path(__file__).resolve().parent.parent
               / "legalakshi_schema_v3_final.sql")

INSP = {"inspector_id": "PG-1", "inspector_name": "PG Officer",
        "business_name": "PG Store", "inspection_date": "2026-09-15"}

PRODUCT = {"product_name": "PG Atta", "category": "GENERAL",
           "is_prepackaged": True, "manufacturer": "PG Foods",
           "quantity": 5, "quantity_unit": "kg", "quantity_type": "weight",
           "manufacturing_date": "2024-05-01", "consumer_care": "1800-000",
           "unit_sale_price": "Rs.50 per kg"}  # no mrp -> FAIL -> violation


def _dsn() -> str:
    return os.environ.get("DATABASE_URL", "")


def _reachable(dsn: str) -> bool:
    try:
        import psycopg
        with psycopg.connect(dsn, connect_timeout=3):
            return True
    except Exception:
        return False


needs_pg = pytest.mark.skipif(
    not _dsn() or not _reachable(_dsn()),
    reason="PostgreSQL integration test not executed because "
           "PostgreSQL was unavailable.")


def _apply_schema(dsn: str) -> None:
    import psycopg

    sql = SCHEMA_PATH.read_text(encoding="utf-8")
    with psycopg.connect(dsn, autocommit=True) as conn, conn.cursor() as cur:
        # skip if already loaded (sample workflow UUID present)
        cur.execute("SELECT to_regclass('public.legal_rules')")
        if cur.fetchone()[0] is None:
            cur.execute(sql)
            return
        cur.execute("SELECT count(*) FROM legal_rules")
        if cur.fetchone()[0] == 0:
            cur.execute(sql)


@needs_pg
def test_postgres_full_flow_persists():
    from app.engine import engine as engine_mod
    from app.repositories.postgres import PostgresRepo

    dsn = _dsn()
    _apply_schema(dsn)
    repo = PostgresRepo(dsn)
    assert repo.ping()

    insp = repo.create_inspection(dict(INSP))
    iid = insp["inspection_id"]
    prod = repo.add_product(iid, dict(PRODUCT))
    pid = prod["product_id"]
    res = engine_mod.analyze(repo, insp, repo.get_product(pid),
                             repo.declarations_for(pid), as_of="2026-09-15",
                             default_origin="MANUAL")
    mrp = next(f for f in res["findings"] if f["rule_id"] == "CHK-MRP")
    assert mrp["status"] == "FAIL"
    assert res["violation_ids"]

    # persisted compliance results?
    rows = repo.results_for(iid, pid)
    assert len(rows) == len(res["findings"])
    assert any(r["result"] == "FAIL" for r in rows)
    # persisted violations?
    violations = repo.violations_for(iid)
    assert len(violations) == len(res["violation_ids"])
    assert all(v["inspector_status"] == "PENDING" for v in violations)
    # inspector verification persists?
    vid = res["violation_ids"][0]
    updated = repo.verify_violation(vid, "CONFIRMED", "PG-1", "checked shelf")
    assert updated and updated["inspector_status"] == "CONFIRMED"
    assert repo.violations_for(iid)[0]["inspector_status"] in (
        "CONFIRMED", "PENDING")  # ordering not guaranteed; re-fetch below
    by_id = {v["violation_id"]: v for v in repo.violations_for(iid)}
    assert by_id[vid]["inspector_status"] == "CONFIRMED"


@needs_pg
def test_postgres_rejects_overlapping_versions():
    import psycopg
    from psycopg import errors

    dsn = _dsn()
    _apply_schema(dsn)
    with psycopg.connect(dsn, autocommit=True) as conn, conn.cursor() as cur:
        with pytest.raises(errors.ExclusionViolation):
            cur.execute(
                """INSERT INTO rule_versions
                   (rule_id, sub_rule, clause, requirement,
                    legal_text_or_paraphrase, requirement_type, effective_from,
                    effective_to, source_id, status, check_id)
                   SELECT rule_id, sub_rule, clause, 'overlap probe', 'probe',
                          'DECLARATION', DATE '2026-01-01', NULL, source_id,
                          'IN_FORCE', check_id
                   FROM rule_versions
                   WHERE rule_version_id = '55555555-5555-5555-5555-555555550030'""")


@needs_pg
def test_postgres_complaint_lifecycle_persists():
    """Consumer intake tables (migrations/) work on real PostgreSQL:
    create -> SUBMITTED -> ACKNOWLEDGED with timeline rows."""
    from app.repositories.postgres import PostgresRepo

    dsn = _dsn()
    repo = PostgresRepo(dsn)
    created = repo.create_complaint({
        "reporter_id": "PG-TESTER", "product_name": "PG Test Pack",
        "retailer": "PG Store", "city": "PG City", "severity": "Medium",
        "description": "integration probe (clearly labeled test data)",
        "evidence": []})
    assert created["status"] == "SUBMITTED"
    cid = created["complaint_id"]
    assert repo.get_complaint(cid)["status"] == "SUBMITTED"
    moved = repo.transition_complaint(cid, "ACKNOWLEDGED", "PG-1", "seen")
    assert moved and moved["status"] == "ACKNOWLEDGED"
    events = repo.complaint_timeline(cid)
    assert [e["event_type"] for e in events] == ["CREATED", "STATUS_CHANGE"]
    with pytest.raises(ValueError):
        repo.transition_complaint(cid, "RESOLVED", "PG-1")  # illegal jump"")
