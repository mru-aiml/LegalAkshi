"""Rule import/sync tests — validate, diff, apply (memory repo), conflicts.

Pristine manifest against a freshly seeded repo must be a no-op: the
authoritative data is already the source of truth.
"""
from __future__ import annotations

import copy

import pytest

from app.repositories.memory import MemoryRepo
from app.services import rule_sync as sync


@pytest.fixture()
def repo():
    return MemoryRepo()


def test_pristine_manifest_validates_clean():
    assert sync.validate_manifest(sync.load_manifest()) == []


def test_validation_rejects_bad_manifest():
    bad = copy.deepcopy(sync.load_manifest())
    bad["legal_knowledge"]["rule_versions"][0]["status"] = "ACTIVEISH"
    bad["legal_knowledge"]["rule_versions"][1]["effective_from"] = "not-a-date"
    bad["legal_knowledge"]["applicability_rules"][0]["rule_version_id"] = "nope"
    bad["scoring_policy"][0]["rules"][0]["severity"] = "CRITICAL"
    errors = sync.validate_manifest(bad)
    assert len(errors) >= 4


def test_pristine_diff_is_noop(repo):
    diff = sync.diff_manifest(repo, sync.load_manifest())
    assert diff["new_checks"] == []
    assert diff["new_versions"] == []
    assert diff["changed_versions"] == []
    assert diff["new_applicability"] == []
    assert diff["new_weights"] == [] and diff["changed_weights"] == []


def _manifest_with_new_check():
    m = copy.deepcopy(sync.load_manifest())
    m["legal_knowledge"]["rules"].append({
        "rule_id": "CHK-TEST-NEW", "law_id": "LMPC-2011",
        "postgres_rule_id": "33333333-3333-3333-3333-333333330006",
        "rule_number": "6", "sub_rule": "1", "clause": "(zz)",
        "title": "Test check (sync fixture)", "field": "test_field",
        "check_type": "FIELD_PRESENT", "mandatory_default": False,
        "scoring_category": "OTHER",
        "requirement": "Sync fixture requirement.",
        "source_reference": "Rule 6(1)(zz)"})
    m["legal_knowledge"]["rule_versions"].append({
        "rule_version_id": "55555555-5555-5555-5555-55555555test",
        "check_id": "CHK-TEST-NEW", "effective_from": "2026-01-01",
        "effective_to": None, "status": "IN_FORCE",
        "requirement": "Sync fixture requirement.",
        "legal_text_or_paraphrase": "Fixture text.",
        "supersedes": None, "superseded_by": None,
        "provenance": {"source_title": "x", "authenticity_status": "VERIFIED_PRIMARY"}})
    m["legal_knowledge"]["applicability_rules"].append({
        "applicability_id": "test-appl-1", "check_id": "CHK-TEST-NEW",
        "rule_version_id": "55555555-5555-5555-5555-55555555test",
        "conditions": {}, "result": "REQUIRED", "reason": "Fixture."})
    m["scoring_policy"][0]["rules"].append(
        {"check_id": "CHK-TEST-NEW", "weight": 1.0, "severity": "LOW"})
    return m


def test_diff_detects_new_check(repo):
    diff = sync.diff_manifest(repo, _manifest_with_new_check())
    assert [c["rule_id"] for c in diff["new_checks"]] == ["CHK-TEST-NEW"]
    assert len(diff["new_versions"]) == 1
    assert len(diff["new_applicability"]) == 1
    assert len(diff["new_weights"]) == 1


def test_apply_then_noop_and_history_preserved(repo):
    m = _manifest_with_new_check()
    first = sync.apply_sync(repo, m, actor="ADMIN-TEST", dry_run=False)
    # registry entry + weight sync; the first version of a brand-new lineage
    # needs an admin-supplied requirement_type (never invented) -> conflict,
    # and its applicability row is blocked until the version exists.
    kinds = sorted(a["kind"] for a in first["applied"])
    assert kinds == ["check", "weight"], first
    assert first["summary"]["errors"] == 0
    assert len(first["conflicts"]) == 2, first
    assert "CHK-TEST-NEW" in [c["check_id"] for c in repo.list_checks()]
    # ... but the check has no live version yet, so it stays out of analysis
    assert all(v["check_id"] != "CHK-TEST-NEW"
               for v in repo.active_rule_versions("2026-09-15"))
    # rerun only re-reports the same actionable conflicts (idempotent)
    second = sync.apply_sync(repo, m, actor="ADMIN-TEST", dry_run=False)
    assert second["summary"]["applied"] == 0, second
    # existing history untouched
    assert len(repo.rule_detail("CHK-MRP")["versions"]) == 2


def test_new_lineage_version_via_manual_endpoint(repo):
    m = _manifest_with_new_check()
    sync.apply_sync(repo, m, actor="ADMIN-TEST", dry_run=False)
    saved = repo.insert_rule_version({
        "rule_version_id": "55555555-5555-5555-5555-55555555test",
        "rule_id": "memory-rule-6", "check_id": "CHK-TEST-NEW",
        "sub_rule": "1", "clause": "(zz)",
        "requirement": "Sync fixture requirement.",
        "legal_text_or_paraphrase": "Fixture text.",
        "requirement_type": "DECLARATION",
        "effective_from": "2026-01-01", "effective_to": None,
        "source_id": "memory-source", "status": "IN_FORCE"})
    assert saved["rule_version_id"] == "55555555-5555-5555-5555-55555555test"
    assert any(v["check_id"] == "CHK-TEST-NEW"
               for v in repo.active_rule_versions("2026-09-15"))


def test_changed_version_is_conflict_never_overwritten(repo):
    m = copy.deepcopy(sync.load_manifest())
    m["legal_knowledge"]["rule_versions"][0]["requirement"] = "EDITED BY HAND"
    diff = sync.diff_manifest(repo, m)
    assert len(diff["changed_versions"]) == 1
    result = sync.apply_sync(repo, m, actor="ADMIN-TEST", dry_run=False)
    assert result["summary"]["applied"] == 0
    assert len(result["conflicts"]) == 1
    current = repo.rule_detail(
        m["legal_knowledge"]["rule_versions"][0]["check_id"])
    assert all(v["requirement"] != "EDITED BY HAND" for v in current["versions"])


def test_overlapping_version_rejected(repo):
    m = copy.deepcopy(sync.load_manifest())
    m["legal_knowledge"]["rule_versions"].append({
        "rule_version_id": "55555555-5555-5555-5555-55555555clash",
        "check_id": "CHK-MRP", "effective_from": "2023-01-01",
        "effective_to": None, "status": "IN_FORCE",
        "requirement": "Clash.", "legal_text_or_paraphrase": "Clash.",
        "supersedes": None, "superseded_by": None,
        "provenance": {"source_title": "x",
                       "authenticity_status": "VERIFIED_PRIMARY"}})
    result = sync.apply_sync(repo, m, actor="ADMIN-TEST", dry_run=False)
    assert result["summary"]["errors"] == 1
    assert "overlap" in result["errors"][0]["error"]
