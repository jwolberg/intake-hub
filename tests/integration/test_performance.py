"""Performance / scale test (PRD §14 benchmarks, §18 large scenario).

Not a wall-clock benchmark (those are flaky in CI) — this asserts the workflow
stays *stable and correct* at scale: the large sample invoice (80 line items)
processes to a terminal state without raising, in reasonable time.
"""

import json
import pathlib
import time

from backend.clients import PassthroughLLMClient, StubSheetsClient
from backend.db.repository import InMemoryRepository
from backend.domain import InvoiceStatus
from backend.orchestrator import process

SAMPLES = pathlib.Path(__file__).resolve().parents[2] / "samples"

# Generous ceiling for an offline, in-process run — this is a stability check,
# not a benchmark, so it should never be close to flaky.
_MAX_SECONDS = 5.0


def test_large_invoice_is_stable_and_reaches_a_terminal_state():
    sample = json.loads((SAMPLES / "inv_large_007.json").read_text())
    repo = InMemoryRepository()

    start = time.monotonic()
    invoice = process(sample, repo, llm=PassthroughLLMClient(), sheets=StubSheetsClient())
    elapsed = time.monotonic() - start

    assert invoice.status in (InvoiceStatus.POSTED, InvoiceStatus.HELD)
    assert elapsed < _MAX_SECONDS
