"""Orchestrator tests over the sample invoices (P1-T9).

Uses the in-process ``InMemoryRepository`` and stub clients (no DB, no network)
to verify state transitions, persistence of each stage output, audit trail, and
per-invoice failure isolation.
"""

import json
import pathlib

from backend.clients import PassthroughLLMClient, StubClinRunClient, StubMCPReferenceClient
from backend.clients.errors import ReferenceUnavailable
from backend.clients.llm import StubLLMClient
from backend.db.repository import InMemoryRepository
from backend.domain import Decision, InvoiceStatus
from backend.orchestrator import process, process_all, recover

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
    trail = repo.get_audit(invoice.id)
    actions = [e.action.value for e in trail]
    assert actions == [
        "received", "parsed", "extracted", "context_resolved",
        "catalog_matched", "submitted",
    ]
    # actor attribution: intake/parse are system, the AI stages are ai (PRD §17)
    actors = {e.action.value: e.actor.value for e in trail}
    assert actors["received"] == "system" and actors["parsed"] == "system"
    assert actors["extracted"] == "ai" and actors["submitted"] == "ai"
    # the terminal event carries a reason (PRD §17 reason/note)
    submitted = next(e for e in trail if e.action.value == "submitted")
    assert submitted.details.get("reason")


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
    # an extraction error is now classified specifically (P3-T1), not generic
    assert any(e.type == "extraction_failed" for e in repo.get_exceptions(failed.id))


class _CatalogUnavailableRef:
    """Resolves context normally but the catalog endpoint is down (retryable)."""

    def __init__(self):
        self._inner = StubMCPReferenceClient()
        self.catalog_calls = 0

    def resolve_sponsor_study_site(self, clues):
        return self._inner.resolve_sponsor_study_site(clues)

    def get_catalog(self, sponsor_id, study_id):
        self.catalog_calls += 1
        raise ReferenceUnavailable("catalog endpoint timeout")


def test_catalog_transport_error_marks_failed_not_held():
    repo = InMemoryRepository()
    stubs = _stubs()
    invoice = process(
        _load("inv_clean_001.json"), repo,
        llm=stubs["llm"], ref=_CatalogUnavailableRef(), clinrun=stubs["clinrun"],
    )

    # a transport error is a retryable failure, distinct from a deliberate hold
    assert invoice.status is InvoiceStatus.FAILED
    assert [e.action.value for e in repo.get_audit(invoice.id)][-1] == "failed"
    assert any(e.type == "catalog_fetch_failed" for e in repo.get_exceptions(invoice.id))


class _FlakyCatalogRef:
    """Catalog endpoint fails the first ``fail_times`` calls, then recovers."""

    def __init__(self, fail_times: int):
        self._inner = StubMCPReferenceClient()
        self.calls = 0
        self._fail_times = fail_times

    def resolve_sponsor_study_site(self, clues):
        return self._inner.resolve_sponsor_study_site(clues)

    def get_catalog(self, sponsor_id, study_id):
        self.calls += 1
        if self.calls <= self._fail_times:
            raise ReferenceUnavailable("transient")
        return self._inner.get_catalog(sponsor_id, study_id)


def test_transient_catalog_error_is_retried_within_attempts():
    # Fails twice, succeeds on the 3rd attempt → bounded retry self-heals (PRD §14).
    repo = InMemoryRepository()
    stubs = _stubs()
    ref = _FlakyCatalogRef(fail_times=2)
    invoice = process(_load("inv_clean_001.json"), repo,
                      llm=stubs["llm"], ref=ref, clinrun=stubs["clinrun"])
    assert invoice.status is InvoiceStatus.SUBMITTED
    assert ref.calls == 3  # two failures + one success, all in-process


def test_recover_resumes_failed_catalog_without_reextracting():
    # Catalog down past the retry budget → FAILED (retryable); recover() with a
    # healthy ref resumes from the catalog stage, reusing the existing extraction.
    repo = InMemoryRepository()
    stubs = _stubs()
    invoice = process(_load("inv_clean_001.json"), repo,
                      llm=stubs["llm"], ref=_CatalogUnavailableRef(), clinrun=stubs["clinrun"])
    assert invoice.status is InvoiceStatus.FAILED
    assert any(e.type == "catalog_fetch_failed" for e in repo.get_exceptions(invoice.id))

    recovered = recover(invoice.id, repo,
                        llm=stubs["llm"], ref=StubMCPReferenceClient(), clinrun=stubs["clinrun"])
    assert recovered.status is InvoiceStatus.SUBMITTED
    actions = [e.action.value for e in repo.get_audit(invoice.id)]
    assert "recovered" in actions
    assert actions.count("extracted") == 1  # extraction was reused, not re-run


def test_extraction_failure_then_recover_reextracts():
    # LLM extraction failure → extraction_failed, no submission; recover() with a
    # working extractor re-extracts from the persisted source text and submits.
    repo = InMemoryRepository()
    stubs = _stubs()
    bad_llm = StubLLMClient(responses=[{"metadata": {"total_amount": "not-a-number"}}])
    invoice = process(_load("inv_clean_001.json"), repo,
                      llm=bad_llm, ref=stubs["ref"], clinrun=stubs["clinrun"])
    assert invoice.status is InvoiceStatus.FAILED
    assert any(e.type == "extraction_failed" for e in repo.get_exceptions(invoice.id))
    assert "submitted" not in [e.action.value for e in repo.get_audit(invoice.id)]

    recovered = recover(invoice.id, repo,
                        llm=PassthroughLLMClient(), ref=stubs["ref"], clinrun=stubs["clinrun"])
    assert recovered.status is InvoiceStatus.SUBMITTED
    assert recovered.metadata.invoice_number == "INV-1001"


def test_batch_shares_catalog_cache_across_same_scope():
    repo = InMemoryRepository()
    stubs = _stubs()
    ref = stubs["ref"]
    calls = {"n": 0}
    original = ref.get_catalog

    def counting(sponsor_id, study_id):
        calls["n"] += 1
        return original(sponsor_id, study_id)

    ref.get_catalog = counting  # type: ignore[method-assign]

    # three invoices, all Northwind / NW-CARDIO-1 → one catalog fetch for the batch
    samples = [_load("inv_clean_001.json"), _load("inv_body_003.json"),
               _load("inv_image_004.json")]
    results = process_all(samples, repo, llm=stubs["llm"], ref=ref, clinrun=stubs["clinrun"])

    assert all(r.status is InvoiceStatus.SUBMITTED for r in results)
    assert calls["n"] == 1
