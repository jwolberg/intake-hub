"""PostgresRepository round-trip (P1-T10).

Skipped unless a Postgres is reachable at ``DATABASE_URL`` (see conftest.py) (e.g. after
``docker compose up -d db`` with ``DATABASE_URL`` pointed at localhost). It runs
a clean invoice through the orchestrator persisting to Postgres and asserts the
detail reads back, exercising every JSONB/NUMERIC column round-trip.
"""

import json
import pathlib

from backend.clients import PassthroughLLMClient, StubSheetsClient
from backend.domain import InvoiceStatus
from backend.orchestrator import process

SAMPLES = pathlib.Path(__file__).resolve().parents[2] / "samples"


def test_postgres_round_trip(pg_repo):
    sample = json.loads((SAMPLES / "inv_clean_001.json").read_text())
    invoice = process(
        sample,
        pg_repo,
        llm=PassthroughLLMClient(),
        sheets=StubSheetsClient(),
    )

    reloaded = pg_repo.get_invoice(invoice.id)
    assert reloaded is not None
    assert reloaded.status in (InvoiceStatus.POSTED, InvoiceStatus.HELD)

    detail = pg_repo.get_detail(invoice.id)
    assert len(detail["line_items"]) == 4
    assert detail["line_items"][0].total is not None  # NUMERIC round-trip
    assert len(detail["audit"]) > 0


def test_postgres_clear_exceptions(pg_repo):
    from backend.exceptions import build as build_exception

    sample = json.loads((SAMPLES / "inv_clean_001.json").read_text())
    invoice = process(sample, pg_repo, llm=PassthroughLLMClient(), sheets=StubSheetsClient())
    pg_repo.add_exceptions([build_exception(invoice.id, "low_confidence", message="x")])
    assert pg_repo.get_exceptions(invoice.id)

    pg_repo.clear_exceptions(invoice.id)

    assert pg_repo.get_exceptions(invoice.id) == []
