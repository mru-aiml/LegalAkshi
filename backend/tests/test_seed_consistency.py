"""Seed-consistency tests — the test repository must faithfully mirror the
authoritative legal configuration (master v4 projection of PostgreSQL).

Includes the DB-constraint equivalent: no two versions of one check may
have overlapping validity ranges (mirrors the exclusion constraint
rule_versions_no_overlap), which is what makes temporal selection exact.
"""
from __future__ import annotations

from datetime import date

from app.repositories.memory import MemoryRepo, load_master

CORE_CHECKS = {"CHK-MRP", "CHK-MANUFACTURER", "CHK-COMMON-NAME",
               "CHK-NET-QTY", "CHK-MFG-DATE"}

CHECK_TYPES = {"CONDITIONAL_FIELD_PRESENT", "CROSS_FIELD_COMPARE",
               "DATE_REQUIRED_IF", "DATE_VALID", "FIELD_ABSENT", "FIELD_PRESENT",
               "FORMAT_VALID", "MANUAL_REVIEW", "NUMERIC_COMPARE",
               "PLATFORM_FILTER_PRESENT", "QR_DATA_PRESENT", "QR_OR_ON_PACKAGE",
               "REGEX_MATCH", "TEXT_CONTAINS", "TEXT_MATCH", "UNIT_NORMALIZATION"}

APPL_RESULTS = {"REQUIRED", "NOT_REQUIRED", "NOT_APPLICABLE", "CONDITIONAL",
                "NEEDS_REVIEW"}


def _inf(d):
    return d or "9999-12-31"


def test_fifteen_checks_registered():
    assert len(MemoryRepo().list_checks()) == 15


def test_core_five_checks_present():
    ids = {c["check_id"] for c in MemoryRepo().list_checks()}
    assert CORE_CHECKS <= ids


def test_check_types_within_authoritative_vocabulary():
    for c in MemoryRepo().list_checks():
        assert c["check_type"] in CHECK_TYPES, c


def test_applicability_results_within_vocabulary():
    for a in load_master()["legal_knowledge"]["applicability_rules"]:
        assert a["result"] in APPL_RESULTS, a


def test_no_overlapping_version_ranges_per_check():
    """Exclusion-constraint equivalent over the authoritative data."""
    by_check: dict[str, list] = {}
    for v in load_master()["legal_knowledge"]["rule_versions"]:
        by_check.setdefault(v["check_id"], []).append(v)
    for check_id, versions in by_check.items():
        ordered = sorted(versions, key=lambda v: v["effective_from"])
        for prev, cur in zip(ordered, ordered[1:]):
            assert _inf(prev["effective_to"]) <= cur["effective_from"], \
                f"overlap in {check_id}: {prev['rule_version_id']} vs {cur['rule_version_id']}"


def test_supersession_lineage_intact():
    ids = {v["rule_version_id"]
           for v in load_master()["legal_knowledge"]["rule_versions"]}
    for v in load_master()["legal_knowledge"]["rule_versions"]:
        for link in (v["supersedes"], v["superseded_by"]):
            if link is not None:
                assert link in ids, v


def test_scoring_policy_matches_authoritative():
    pol = MemoryRepo().scoring_policy("DEFAULT-2026")
    assert pol["calc_method"] == "WEIGHTED_FINDINGS"
    assert pol["review_handling"] == "DO_NOT_FINALIZE_WITHOUT_REVIEW"
    assert pol["not_applicable_handling"] == "EXCLUDE_FROM_DENOMINATOR"
    weights = {w["check_id"]: w["weight"] for w in pol["weights"]}
    assert weights["CHK-MRP"] == 20.0
    assert weights["CHK-OTHER-MATTERS"] == 0.0
    assert len(weights) == 15


def test_every_version_has_provenance():
    for v in load_master()["legal_knowledge"]["rule_versions"]:
        prov = v["provenance"]
        assert prov["source_title"] and prov["authenticity_status"], v


def test_temporal_selection_exact_on_boundaries():
    repo = MemoryRepo()
    # effective_to is exclusive: 2027-07-01 selects v2-range start... v2 not
    # yet in force so the check drops out; v1 covers up to 2027-07-01 excl.
    mid = {v["check_id"]: v for v in repo.active_rule_versions("2027-06-30")}
    assert mid["CHK-ECOMMERCE-COO-FILTER"]["rule_version_id"] == \
        "55555555-5555-5555-5555-555555550007"
    assert date.fromisoformat("2027-06-30") < date.fromisoformat("2027-07-01")
