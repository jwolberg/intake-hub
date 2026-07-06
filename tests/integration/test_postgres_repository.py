"""PostgresRepository round-trip (P1-T10).

Skipped unless a Postgres is reachable at ``DATABASE_URL`` (e.g. after
``docker compose up -d db`` with ``DATABASE_URL`` pointed at localhost). It runs
a clean invoice through the orchestrator persisting to Postgres and asserts the
detail reads back, exercising every JSONB/NUMERIC column round-trip.
"""

import json
import pathlib

import pytest
from backend.clients import PassthroughLLMClient, StubSheetsClient
from backend.db.session import get_engine, init_schema
from backend.domain import InvoiceStatus
from backend.orchestrator import process
from sqlalchemy.exc import SQLAlchemyError

SAMPLES = pathlib.Path(__file__).resolve().parents[2] / "samples"


@pytest.fixture(scope="module")
def pg_repo():
    try:
        with get_engine().connect():
            pass
        init_schema()
    except SQLAlchemyError:
        pytest.skip("no Postgres reachable at DATABASE_URL")
    from backend.db.repository import PostgresRepository

    return PostgresRepository(get_engine())


def test_postgres_round_trip(pg_repo):
    sample = json.loads((SAMPLES / "inv_clean_001.json").read_text())
    invoice = process(
        sample, pg_repo,
        llm=PassthroughLLMClient(), sheets=StubSheetsClient(),
    )

    reloaded = pg_repo.get_invoice(invoice.id)
    assert reloaded is not None
    assert reloaded.status in (InvoiceStatus.POSTED, InvoiceStatus.HELD)

    detail = pg_repo.get_detail(invoice.id)
    assert len(detail["line_items"]) == 4
    assert detail["line_items"][0].total is not None  # NUMERIC round-trip
    assert detail["context"] is None  # resolved_context table removed (U5)
    assert detail["matches"] == []  # match_results table removed (U5)
    assert len(detail["audit"]) > 0
