"""Read-only verification against the authoritative PostgreSQL schema."""
from __future__ import annotations

import os
import sys

TABLES = ["legal_sources", "legal_rules", "rule_versions", "rule_applicability",
          "engine_check_registry", "scoring_policies", "scoring_policy_weights",
          "inspections", "inspected_products", "inspection_evidence",
          "extracted_declarations", "compliance_results", "violations",
          "amendments", "enforcement_provisions", "legal_cases", "audit_logs"]


def main() -> int:
    dsn = os.environ.get("DATABASE_URL", "")
    if not dsn:
        print("DATABASE_URL is not set.")
        return 2
    try:
        import psycopg
    except ImportError:
        print("psycopg is not installed (pip install -r requirements.txt).")
        return 2
    try:
        ok = True
        with psycopg.connect(dsn) as conn, conn.cursor() as cur:
            for table in TABLES:
                cur.execute("SELECT to_regclass(%s)", [f"public.{table}"])
                exists = cur.fetchone()[0]
                print(("OK   " if exists else "MISS ") + table)
                ok = ok and bool(exists)
            print("--- engine_check_registry ---")
            cur.execute("SELECT check_id, title, field_name, check_type"
                        " FROM engine_check_registry ORDER BY check_id")
            for row in cur.fetchall():
                print("  " + " | ".join(str(c) for c in row))
            print("--- scoring DEFAULT-2026 ---")
            cur.execute("SELECT check_id, weight FROM scoring_policy_weights"
                        " WHERE policy_id = 'DEFAULT-2026' ORDER BY check_id")
            for row in cur.fetchall():
                print("  " + " | ".join(str(c) for c in row))
        return 0 if ok else 1
    except Exception as exc:
        print(f"connection/query failed: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
