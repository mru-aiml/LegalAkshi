"""Apply pending migrations from backend/migrations/ (tracked table).

Usage (from backend/, venv active, DATABASE_URL in backend/.env):
    python scripts/apply_migrations.py

Never modifies backend/legalakshi_schema_v3_final.sql (authoritative).
"""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

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
    files = sorted(MIGRATIONS_DIR.glob("*.sql"))
    if not files:
        print("no migrations found.")
        return 0
    try:
        with psycopg.connect(dsn, autocommit=True) as conn, conn.cursor() as cur:
            cur.execute("CREATE TABLE IF NOT EXISTS schema_migrations"
                        " (filename TEXT PRIMARY KEY, applied_at TIMESTAMPTZ"
                        " NOT NULL DEFAULT now())")
            cur.execute("SELECT filename FROM schema_migrations")
            done = {r[0] for r in cur.fetchall()}
            for path in files:
                if path.name in done:
                    print(f"skip {path.name} (already applied)")
                    continue
                print(f"apply {path.name} ...")
                cur.execute(path.read_text(encoding="utf-8"))
                cur.execute("INSERT INTO schema_migrations (filename) VALUES (%s)",
                            [path.name])
                print(f"applied {path.name}")
            return 0
    except Exception as exc:
        print(f"FAILED: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
