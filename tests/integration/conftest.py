"""Shared Postgres fixture for integration tests.

Skips only when no database is *reachable* at ``DATABASE_URL`` — any other
error (e.g. a broken schema script) fails the test instead of hiding behind a
skip. Set ``REQUIRE_POSTGRES=1`` (CI does) to turn "unreachable" into a
failure too, so the Postgres suite can never silently skip there.
"""

import os

import pytest
from backend.db.session import get_engine, init_schema
from sqlalchemy import text
from sqlalchemy.exc import OperationalError

_TABLES = (
    "audit_events, exceptions, line_items, invoices, seen_messages, "
    "sheet_appends, oauth_tokens, gmail_sync_state"
)


@pytest.fixture(scope="session")
def pg_engine():
    engine = get_engine()
    try:
        with engine.connect():
            pass
    except OperationalError as exc:
        if os.environ.get("REQUIRE_POSTGRES") == "1":
            pytest.fail(f"REQUIRE_POSTGRES=1 but no Postgres reachable: {exc}")
        pytest.skip("no Postgres reachable at DATABASE_URL")
    init_schema()
    return engine


@pytest.fixture
def pg_repo(pg_engine):
    from backend.db.repository import PostgresRepository

    with pg_engine.begin() as conn:
        conn.execute(text(f"TRUNCATE {_TABLES} CASCADE"))
    return PostgresRepository(pg_engine)
