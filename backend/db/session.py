"""Database engine.

Uses SQLAlchemy Core (engine + ``text``) rather than the ORM: the domain layer
is already Pydantic, and stages persist explicit, typed rows. The schema is
managed by versioned migrations (``backend/db/migrate.py``, #0013), applied as an
explicit deploy step — never implicitly on app startup.
"""

from __future__ import annotations

from functools import lru_cache

from sqlalchemy import Engine, create_engine

from backend.config import settings


@lru_cache(maxsize=1)
def get_engine() -> Engine:
    """Return a process-wide SQLAlchemy engine."""
    return create_engine(settings.database_url, pool_pre_ping=True, future=True)
