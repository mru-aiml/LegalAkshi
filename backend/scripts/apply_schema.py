"""Apply the authoritative schema to PostgreSQL (idempotent guard).

Usage (from backend/):
    set DATABASE_URL=postgresql://postgres:PASSWORD@localhost:5432/legalakshi
    python scripts/apply_schema.py

Refuses to run without DATABASE_URL. Skips the apply when legal_rules is
already populated (the authoritative file is a full seed, not a migration).
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()  # backend/.env; real env vars take precedence

SCHEMA_PATH = Path(__file__).resolve().parent.parent / "legalakshi_schema_v3_final.sql"
MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "migrations"


def main() -> int:
    dsn = os.environ.get("DATABASE_URL", "")
    if not dsn:
        print("DATABASE_URL is not set. Refusing to guess a database.")
        return 2
    try:
        import psycopg
    except ImportError:
        print("psycopg is not installed (pip install -r requirements.txt).")
        return 2
    try:
        with psycopg.connect(dsn, autocommit=True) as conn, conn.cursor() as cur:
            cur.execute("SELECT to_regclass('public.legal_rules')")
            if cur.fetchone()[0] is not None:
                cur.execute("SELECT count(*) FROM legal_rules")
                if cur.fetchone()[0] > 0:
                    print("legal_rules already populated — nothing to apply.")
                    return 0
            print(f"applying {SCHEMA_PATH.name} ...")
            cur.execute(SCHEMA_PATH.read_text(encoding="utf-8"))
            cur.execute("SELECT count(*) FROM engine_check_registry")
            print(f"engine_check_registry rows: {cur.fetchone()[0]}")
            cur.execute("SELECT count(*) FROM rule_versions")
            print(f"rule_versions rows: {cur.fetchone()[0]}")
        with psycopg.connect(dsn, autocommit=True) as conn, conn.cursor() as cur:
            for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
                print(f"applying migration {path.name} ...")
                cur.execute(path.read_text(encoding="utf-8"))
            print("schema applied successfully.")
            return 0
    except Exception as exc:
        print(f"FAILED: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
