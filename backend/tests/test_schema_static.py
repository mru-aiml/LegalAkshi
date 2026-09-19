"""Static schema tests — run without PostgreSQL (see scripts/validate_schema.py
for the full human-readable report)."""
from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import validate_schema as vs  # noqa: E402


def _sql() -> str:
    return vs.SQL_PATH.read_text(encoding="utf-8")


def test_required_tables():
    tables = set(re.findall(r"CREATE TABLE (?:IF NOT EXISTS )?(\w+)", _sql()))
    assert vs.REQUIRED_TABLES <= tables


def test_fk_targets_resolve():
    sql = _sql()
    tables = set(re.findall(r"CREATE TABLE (?:IF NOT EXISTS )?(\w+)", sql))
    refs = set(re.findall(r"REFERENCES\s+(\w+)", sql))
    assert refs <= tables


def test_temporal_exclusion_constraint():
    sql = _sql()
    assert "rule_versions_no_overlap" in sql
    assert "EXCLUDE USING gist" in sql


def test_seed_counts_and_cross_references():
    import json

    sql = _sql()
    master = json.loads(vs.MASTER_PATH.read_text(encoding="utf-8"))
    json_checks = {r["rule_id"] for r in master["legal_knowledge"]["rules"]}
    assert len(json_checks) == 15
    assert len(master["legal_knowledge"]["rule_versions"]) == 24
    assert len(master["legal_knowledge"]["applicability_rules"]) == 18
    assert len(master["scoring_policy"][0]["rules"]) == 15
    sql_versions = set(re.findall(r"'([0-9a-f]{8}-[0-9a-f-]{27,35})'", sql))
    json_versions = {v["rule_version_id"]
                     for v in master["legal_knowledge"]["rule_versions"]}
    assert json_versions <= sql_versions
