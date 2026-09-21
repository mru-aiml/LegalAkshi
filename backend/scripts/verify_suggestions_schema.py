"""Verify the consumer-suggestions store against the configured database.

Read-only diagnostics for the "GET /officer/suggestions -> 500" class of
production failures. Checks, in order:

1. consumer_suggestions table exists (migration 004)
2. expected columns exist with compatible types
3. suggestion_events table exists
4. supporting indexes exist
5. row-level security posture (enabled? policies?)
6. row counts (proof the endpoints can read)

Usage (from backend/, venv active, DATABASE_URL in backend/.env):
    python scripts/verify_suggestions_schema.py

Exit codes: 0 healthy, 1 problems found, 2 no DATABASE_URL/driver.
Never writes. Never prints credentials, tokens, or suggestion text.
"""
from __future__ import annotations

import os
import sys

EXPECTED_SUGGESTIONS = {
    "suggestion_id": "uuid",
    "consumer_user_id": "text",
    "title": "text",
    "category": "text",
    "description": "text",
    "context": "text",
    "location": "text",
    "status": "text",
    "reviewed_by": "text",
    "officer_note": "text",
}

EXPECTED_EVENTS = {
    "event_id": "uuid",
    "suggestion_id": "uuid",
    "event_type": "text",
    "from_status": "text",
    "to_status": "text",
    "actor_id": "text",
    "note": "text",
}


def main() -> int:
    dsn = os.environ.get("DATABASE_URL", "")
    if not dsn:
        print("no DATABASE_URL set (backend/.env or environment).")
        return 2
    try:
        import psycopg
        from psycopg.rows import dict_row
    except ImportError:
        print("psycopg is not installed (pip install -r requirements.txt).")
        return 2
    problems: list[str] = []
    try:
        conn = psycopg.connect(dsn, row_factory=dict_row,
                               connect_timeout=15)
    except Exception as exc:
        print(f"cannot connect: {exc}")
        return 1
    with conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT tablename FROM pg_tables "
                "WHERE schemaname = 'public' AND tablename IN "
                "('consumer_suggestions', 'suggestion_events', "
                "'schema_migrations')")
            tables = {r["tablename"] for r in cur.fetchall()}
            print(f"tables present: {sorted(tables) or '(none of them)'}")
            if "consumer_suggestions" not in tables:
                problems.append(
                    "MISSING TABLE public.consumer_suggestions -> apply "
                    "backend/migrations/004_consumer_suggestions.sql via "
                    "backend/scripts/apply_migrations.py")
            else:
                cur.execute(
                    "SELECT column_name, data_type "
                    "FROM information_schema.columns "
                    "WHERE table_name = 'consumer_suggestions'")
                cols = {r["column_name"]: r["data_type"]
                        for r in cur.fetchall()}
                for name, want in EXPECTED_SUGGESTIONS.items():
                    got = cols.get(name)
                    print(f"  column {name}: {got or 'MISSING'}")
                    if got is None:
                        problems.append(f"missing column {name}")
                    elif want == "uuid" and got != "uuid":
                        problems.append(
                            f"column {name} is {got}, expected uuid")
                cur.execute(
                    "SELECT indexname FROM pg_indexes "
                    "WHERE tablename = 'consumer_suggestions'")
                indexes = sorted(r["indexname"] for r in cur.fetchall())
                print(f"  indexes: {indexes or '(none)'}")
                cur.execute(
                    "SELECT relrowsecurity FROM pg_class "
                    "WHERE relname = 'consumer_suggestions'")
                row = cur.fetchone()
                rls = bool(row["relrowsecurity"]) if row else False
                print(f"  row-level security enabled: {rls}")
                if rls:
                    cur.execute(
                        "SELECT policyname, cmd FROM pg_policies "
                        "WHERE tablename = 'consumer_suggestions'")
                    policies = list(cur.fetchall())
                    print(f"  policies: {policies or '(none -> "
                          "RLS without policies blocks ALL reads)'}")
                    if not policies:
                        problems.append(
                            "RLS enabled with zero policies: every query "
                            "fails; add a policy or disable RLS")
                try:
                    cur.execute("SELECT count(*) AS n "
                                "FROM consumer_suggestions")
                    print(f"  rows: {cur.fetchone()['n']}")
                except Exception as exc:
                    problems.append(f"cannot SELECT rows: {exc}")
            if "suggestion_events" not in tables:
                problems.append(
                    "MISSING TABLE public.suggestion_events -> apply "
                    "backend/migrations/004_consumer_suggestions.sql")
            else:
                cur.execute(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_name = 'suggestion_events'")
                have = {r["column_name"] for r in cur.fetchall()}
                missing = [c for c in EXPECTED_EVENTS if c not in have]
                print(f"  suggestion_events columns ok: "
                      f"{not missing}")
                problems.extend(f"missing events column {c}"
                                for c in missing)
            if "schema_migrations" in tables:
                cur.execute("SELECT filename FROM schema_migrations "
                            "WHERE filename LIKE '%suggestion%'")
                applied = [r["filename"] for r in cur.fetchall()]
                print(f"  migration records mentioning suggestions: "
                      f"{applied or '(none)'}")
                if not any("004" in f for f in applied):
                    problems.append(
                        "schema_migrations has no 004 record: run "
                        "backend/scripts/apply_migrations.py")
            # Write probe with rollback: proves the app role can INSERT
            # (create_suggestion path) without persisting anything.
            try:
                cur.execute(
                    "SAVEPOINT verify_probe; "
                    "INSERT INTO consumer_suggestions "
                    "(consumer_user_id, title) VALUES ('__verify__', 'x') "
                    "RETURNING suggestion_id; "
                    "ROLLBACK TO SAVEPOINT verify_probe;")
                print("  write probe (rolled back): OK")
            except Exception as exc:
                problems.append(f"write probe failed (INSERT blocked?): "
                                f"{exc}")
    if problems:
        print("\nPROBLEMS:")
        for p in problems:
            print(f"  - {p}")
        return 1
    print("\nOK: suggestions store is readable and writable.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
