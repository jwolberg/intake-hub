"""End-to-end scenario suite (P3-T2; PRD §18 Scenario Tests).

One test per required scenario, run through the real orchestrator against the
in-process stub MCP + mock ClinRun (no DB, no network). Each asserts the
terminal status, decision, and the defining signal (matches / warnings /
exception type), so the eight PRD §18 scenarios are demonstrably covered.
"""

import json
import pathlib

import pytest
from backend.clients import PassthroughLLMClient, StubClinRunClient, StubMCPReferenceClient
from backend.clients.errors import ReferenceUnavailable, SubmissionFailed
from backend.db.repository import InMemoryRepository
from backend.domain import Decision, InvoiceStatus
from backend.orchestrator import process

SAMPLES = pathlib.Path(__file__).resolve().parents[2] / "samples"


def _load(name):
    return json.loads((SAMPLES / name).read_text())


def _run(sample, *, ref=None, clinrun=None):
    repo = InMemoryRepository()
    invoice = process(
        sample, repo,
        llm=PassthroughLLMClient(),
        ref=ref or StubMCPReferenceClient(),
        clinrun=clinrun or StubClinRunClient(),
    )
    return repo, invoice


# --- happy-path scale: simple / medium / large -----------------------------

def test_simple_invoice_submits():
    """Simple invoice with clear metadata → submit."""
    _repo, invoice = _run(_load("inv_body_003.json"))
    assert invoice.status is InvoiceStatus.SUBMITTED
    assert invoice.decision is Decision.SUBMIT


def test_medium_invoice_multiple_line_items_submits():
    """Medium invoice with multiple line items → all match → submit."""
    repo, invoice = _run(_load("inv_clean_001.json"))
    matches = repo.get_matches(invoice.id)
    assert len(matches) == 4 and all(m.catalog_item_id for m in matches)
    assert invoice.status is InvoiceStatus.SUBMITTED


def test_large_invoice_larger_catalog_submits_without_failure():
    """Large invoice matched against a larger catalog → all match, no failure."""
    repo, invoice = _run(_load("inv_large_007.json"))
    matches = repo.get_matches(invoice.id)
    assert len(matches) == 15 and all(m.catalog_item_id for m in matches)
    assert invoice.status is InvoiceStatus.SUBMITTED


# --- holds: ambiguous / mismatched / unmatched -----------------------------

def test_ambiguous_metadata_holds():
    """Ambiguous sponsor/study/site candidates → hold with the conflict surfaced."""
    repo, invoice = _run(_load("inv_ambiguous_008.json"))
    assert invoice.status is InvoiceStatus.HELD
    assert "multiple_site_candidates" in repo.get_context(invoice.id).warnings
    assert any(e.type == "context_ambiguity" for e in repo.get_exceptions(invoice.id))


def test_mismatched_metadata_holds():
    """Invoice content disagrees with reference data → hold with specific mismatch."""
    repo, invoice = _run(_load("inv_hold_mismatch_005.json"))
    assert invoice.status is InvoiceStatus.HELD
    assert any(e.type == "context_mismatch" for e in repo.get_exceptions(invoice.id))


def test_unmatched_line_item_holds():
    """A line item with no catalog match → hold."""
    repo, invoice = _run(_load("inv_hold_unmatched_002.json"))
    assert invoice.status is InvoiceStatus.HELD
    assert any(e.type == "unmatched_line_item" for e in repo.get_exceptions(invoice.id))


# --- operational failures: catalog fetch / backend submission --------------

class _CatalogDown:
    """Resolves context but the catalog endpoint is unavailable (retryable)."""

    def __init__(self):
        self._inner = StubMCPReferenceClient()

    def resolve_sponsor_study_site(self, clues):
        return self._inner.resolve_sponsor_study_site(clues)

    def get_catalog(self, sponsor_id, study_id):
        raise ReferenceUnavailable("catalog endpoint down")


class _SubmissionDown:
    def submit(self, payload):
        raise SubmissionFailed("backend 503")


def test_failed_catalog_fetch_marks_failed():
    """Catalog fetch transport error → failed (retryable), not a silent submit."""
    repo, invoice = _run(_load("inv_clean_001.json"), ref=_CatalogDown())
    assert invoice.status is InvoiceStatus.FAILED
    assert any(e.type == "catalog_fetch_failed" for e in repo.get_exceptions(invoice.id))


def test_failed_backend_submission_marks_failed():
    """An otherwise-submittable invoice whose ClinRun submission fails → failed."""
    repo, invoice = _run(_load("inv_clean_001.json"), clinrun=_SubmissionDown())
    assert invoice.status is InvoiceStatus.FAILED
    assert any(e.type == "submission_failed" for e in repo.get_exceptions(invoice.id))


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
