"""Static validation of the authoritative schema (no PostgreSQL needed).

Verifies structure, FK resolution, constraints, lookup seeds, seed counts
(15 checks / 24+ versions / 18+ applicability / 15 weights) and consistency
between backend/authoritative/legalakshi_master_v4.json and the SQL seed.

This proves *structural* integrity only — executability against a live
PostgreSQL instance still requires the integration test path.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
SQL_PATH = HERE / "legalakshi_schema_v3_final.sql"
MASTER_PATH = HERE / "authoritative" / "legalakshi_master_v4.json"

REQUIRED_TABLES = {
    "legal_sources", "legal_rules", "rule_versions", "rule_applicability",
    "engine_check_registry", "scoring_policies", "scoring_policy_weights",
    "inspections", "inspected_products", "inspection_evidence",
    "extracted_declarations", "compliance_results", "violations",
    "enforcement_provisions", "enforcement_actions", "notices",
    "compounding_cases", "legal_cases", "case_events", "case_documents",
    "audit_logs", "amendments", "exceptions", "transition_provisions",
    "lkp_check_type", "lkp_compliance_result", "lkp_applicable_result",
    "lkp_inspector_status", "lkp_provision_status", "lkp_authenticity_status",
}

# Additive application tables live in backend/migrations/ (never in the
# authoritative file, which predates them).
MIGRATION_TABLES = {"complaints", "complaint_events", "notifications"}

errors: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    print(("PASS " if cond else "FAIL ") + name + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        errors.append(name + (f": {detail}" if detail else ""))


def main() -> int:
    sql = SQL_PATH.read_text(encoding="utf-8")
    master = json.loads(MASTER_PATH.read_text(encoding="utf-8"))
    tables = set(re.findall(r"CREATE TABLE (?:IF NOT EXISTS )?(\w+)", sql))
    mig_sql = "".join(
        p.read_text(encoding="utf-8")
        for p in sorted((HERE / "migrations").glob("*.sql")))
    mig_tables = set(re.findall(r"CREATE TABLE (?:IF NOT EXISTS )?(\w+)", mig_sql))
    tables |= mig_tables

    check("required tables present",
          REQUIRED_TABLES <= tables,
          f"missing={sorted(REQUIRED_TABLES - tables)}")

    refs = set(re.findall(r"REFERENCES\s+(\w+)", sql))
    check("all FK targets resolve", refs <= tables, f"unresolved={sorted(refs - tables)}")

    check("btree_gist + pgcrypto extensions",
          '"btree_gist"' in sql and '"pgcrypto"' in sql)
    check("exclusion constraint rule_versions_no_overlap",
          "rule_versions_no_overlap" in sql and "EXCLUDE USING gist" in sql)
    check("UUID PK defaults", sql.count("gen_random_uuid()") >= 10,
          f"count={sql.count('gen_random_uuid()')}")
    check("temporal columns on rule_versions",
          all(k in sql for k in ("effective_from", "effective_to")) and
          "lkp_provision_status" in sql)
    check("audit + enforcement + case tables",
          all(t in tables for t in ("audit_logs", "enforcement_actions",
                                    "legal_cases", "case_documents")))
    check("operational views",
          "v_current_rule_requirements" in sql and "v_operational_legal_sources" in sql)

    for code in ("REQUIRED", "NOT_REQUIRED", "NOT_APPLICABLE", "CONDITIONAL", "NEEDS_REVIEW"):
        check(f"lookup lkp_applicable_result.{code}", f"('{code}'" in sql)
    for code in ("PASS", "FAIL", "NOT_APPLICABLE", "NEEDS_REVIEW"):
        check(f"lookup lkp_compliance_result.{code}", f"('{code}'" in sql)
    for code in ("PENDING", "CONFIRMED", "REJECTED", "REQUIRES_REVIEW"):
        check(f"lookup lkp_inspector_status.{code}", f"('{code}'" in sql)

    sql_checks = set(re.findall(r"\('(CHK-[A-Z-]+)'", sql))
    json_checks = {r["rule_id"] for r in master["legal_knowledge"]["rules"]}
    check("15 checks in registry seed", len(sql_checks & json_checks) >= 15,
          f"sql={len(sql_checks)} json={len(json_checks)}")

    sql_versions = set(re.findall(r"'([0-9a-f]{8}-[0-9a-f-]{27,35})'", sql))
    json_versions = {v["rule_version_id"] for v in master["legal_knowledge"]["rule_versions"]}
    check("24 rule versions in seed", len(json_versions) == 24,
          f"json={len(json_versions)}")
    check("JSON versions present in SQL", json_versions <= sql_versions,
          f"missing={sorted(json_versions - sql_versions)[:3]}")

    json_appl = master["legal_knowledge"]["applicability_rules"]
    check("18 applicability rows", len(json_appl) == 18, f"json={len(json_appl)}")
    check("applicability versions resolve",
          {a["rule_version_id"] for a in json_appl} <= sql_versions)

    weights = master["scoring_policy"][0]["rules"]
    check("15 scoring weights", len(weights) == 15, f"json={len(weights)}")
    check("weight check_ids resolve", {w["check_id"] for w in weights} <= json_checks)
    check("DEFAULT-2026 weights in SQL",
          sql.count("('DEFAULT-2026'") >= 15,
          f"count={sql.count(chr(40)+chr(39)+'DEFAULT-2026'+chr(39))}")

    # PostgresRepo table compatibility (authoritative schema + migrations)
    repo_sql = (HERE / "app" / "repositories" / "postgres.py").read_text(encoding="utf-8")
    used = set(re.findall(r"(?:FROM|JOIN|INSERT INTO|UPDATE)\s+(\w+)", repo_sql))
    used -= {"USING", "SET"}  # join syntax / ON CONFLICT ... DO UPDATE SET
    check("repo tables exist", used <= tables, f"unknown={sorted(used - tables)}")
    check("migration tables defined",
          MIGRATION_TABLES <= mig_tables,
          f"missing={sorted(MIGRATION_TABLES - mig_tables)}")

    print(f"\n{len(errors)} failure(s)" if errors else "\nALL STATIC CHECKS PASSED")
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
