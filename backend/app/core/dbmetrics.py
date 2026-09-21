"""Lightweight per-request database-call accounting.

A contextvar counter incremented by the Postgres repository on every
query (``fetch_all``/``execute``). The timing middleware resets it at
request start and reports it at request end — no credentials, no SQL
text, no row contents, just a count. Zero third-party imports so unit
tests never need a database driver.
"""
from __future__ import annotations

import contextvars

_db_calls: contextvars.ContextVar[int] = contextvars.ContextVar(
    "legalakshi_db_calls", default=0)


def reset() -> None:
    _db_calls.set(0)


def increment() -> None:
    try:
        _db_calls.set(_db_calls.get() + 1)
    except LookupError:  # pragma: no cover - defensive, default is set
        _db_calls.set(1)


def count() -> int:
    try:
        return int(_db_calls.get())
    except (LookupError, TypeError, ValueError):  # pragma: no cover
        return 0
