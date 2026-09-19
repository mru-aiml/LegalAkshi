"""Production entrypoint: PostgreSQL required, fails fast otherwise."""
from __future__ import annotations

import logging
import os
import re

from dotenv import load_dotenv

load_dotenv()  # backend/.env (never committed); real env vars take precedence

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("legalakshi")


def _redact(dsn: str) -> str:
    return re.sub(r"(://[^:/@]+:)[^@]+@", r"\1***@", dsn)


dsn = os.environ.get("DATABASE_URL", "")
if not dsn:
    raise RuntimeError(
        "DATABASE_URL must be set to run the LegalAkshi API "
        "(backend/.env or environment). "
        "The production API never runs against in-memory legal data; "
        "unit tests use the in-memory test repository via dependency injection.")

from app.factory import create_app  # noqa: E402
from app.repositories.postgres import PostgresRepo  # noqa: E402

repo = PostgresRepo(dsn)
if not repo.ping():
    raise RuntimeError(
        f"Cannot connect to PostgreSQL (DATABASE_URL={_redact(dsn)!r}). "
        "Create the database and apply backend/legalakshi_schema_v3_final.sql.")

app = create_app(repo)
log.info("LegalAkshi backend up (store=postgres)")
