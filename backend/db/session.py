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

# Bound how long a connect can hang: against a black-holed host (e.g. a suspended
# Cloud SQL IP) the OS TCP timeout is ~75 s, which would hang /health, /ready and
# every request's worker (#0015 review).
CONNECT_TIMEOUT_SECONDS = 5


def make_engine(url: str) -> Engine:
    return create_engine(
        url,
        pool_pre_ping=True,
        pool_timeout=CONNECT_TIMEOUT_SECONDS,
        connect_args={"connect_timeout": CONNECT_TIMEOUT_SECONDS},
    )


@lru_cache(maxsize=1)
def get_engine() -> Engine:
    """Return a process-wide SQLAlchemy engine."""
    return make_engine(settings.database_url)
