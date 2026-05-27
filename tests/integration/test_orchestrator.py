"""Orchestrator tests over the sample invoices (P1-T9).

Uses the in-process ``InMemoryRepository`` and stub clients (no DB, no network)
to verify state transitions, persistence of each stage output, audit trail, and
per-invoice failure isolation.
"""

import json
import pathlib

from backend.clients import PassthroughLLMClient, StubClinRunClient, StubMCPReferenceClient
from backend.clients.llm import StubLLMClient
from backend.db.repository import InMemoryRepository
from backend.domain import Decision, InvoiceStatus
from backend.orchestrator import process, process_all

SAMPLES = pathlib.Path(__file__).resolve().parents[2] / "samples"


def _stubs():
    return {
        "llm": PassthroughLLMClient(),
        "ref": StubMCPReferenceClient(),
        "clinrun": StubClinRunClient(),
    }


def _load(name: str) -> dict:
    return json.loads((SAMPLES / name).read_text())


def test_clean_invoice_processes_to_submitted():
    repo = InMemoryRepository()
    invoice = process(_load("inv_clean_001.json"), repo, **_stubs())

    assert invoice.status is InvoiceStatus.SUBMITTED
    assert invoice.decision is Decision.SUBMIT

    # every stage output persisted
    assert len(repo.get_line_items(invoice.id)) == 4
    assert repo.get_context(invoice.id).sponsor_id == "sponsor_001"
    assert all(m.catalog_item_id for m in repo.get_matches(invoice.id))
    assert repo.get_exceptions(invoice.id) == []

    # audit trail records the full path through the pipeline
    actions = [e.action.value for e in repo.get_audit(invoice.id)]
    assert actions == [
        "received", "parsed", "extracted", "context_resolved",
        "catalog_matched", "submitted",
    ]


def test_unmatched_invoice_processes_to_held():
    repo = InMemoryRepository()
    invoice = process(_load("inv_hold_unmatched_002.json"), repo, **_stubs())

    assert invoice.status is InvoiceStatus.HELD
    assert invoice.decision is Decision.HOLD
    exc_types = {e.type for e in repo.get_exceptions(invoice.id)}
    assert "unmatched_line_item" in exc_types
    assert [e.action.value for e in repo.get_audit(invoice.id)][-1] == "held"


def test_stage_failure_marks_failed_and_is_isolated():
    repo = InMemoryRepository()
    # an LLM response whose total_amount can't be parsed makes extraction raise
    bad_llm = StubLLMClient(responses=[{"metadata": {"total_amount": "not-a-number"}}])
    stubs = _stubs()

    results = process_all(
        [_load("inv_clean_001.json"), {"source": {}, "document": {}}],
        repo,
        llm=bad_llm,
        ref=stubs["ref"],
        clinrun=stubs["clinrun"],
    )

    # both invoices were processed despite the first failing
    assert len(results) == 2
    statuses = {r.status for r in results}
    assert InvoiceStatus.FAILED in statuses
    failed = next(r for r in results if r.status is InvoiceStatus.FAILED)
    assert [e.action.value for e in repo.get_audit(failed.id)][-1] == "failed"
    assert any(e.type == "stage_failure" for e in repo.get_exceptions(failed.id))
