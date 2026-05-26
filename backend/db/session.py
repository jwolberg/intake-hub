"""Database engine and schema bootstrap.

Uses SQLAlchemy Core (engine + ``text``) rather than the ORM: the domain layer
is already Pydantic, and stages persist explicit, typed rows. ``init_schema``
applies ``schema.sql`` idempotently (every statement is ``IF NOT EXISTS``).
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from sqlalchemy import Engine, create_engine, text

from backend.config import settings

_SCHEMA_PATH = Path(__file__).with_name("schema.sql")


@lru_cache(maxsize=1)
def get_engine() -> Engine:
    """Return a process-wide SQLAlchemy engine."""
    return create_engine(settings.database_url, pool_pre_ping=True, future=True)


def init_schema() -> None:
    """Create tables if they do not exist. Safe to call on every startup."""
    sql = _SCHEMA_PATH.read_text(encoding="utf-8")
    with get_engine().begin() as conn:
        for statement in _split_statements(sql):
            conn.execute(text(statement))


def _split_statements(sql: str) -> list[str]:
    """Split a SQL script into individual statements on ``;`` boundaries.

    Comment-only fragments are dropped so the driver never receives an empty
    statement.
    """
    statements: list[str] = []
    for chunk in sql.split(";"):
        lines = [
            line for line in chunk.splitlines() if not line.strip().startswith("--")
        ]
        cleaned = "\n".join(lines).strip()
        if cleaned:
            statements.append(cleaned)
    return statements
