"""End-to-end pipeline test over the sample invoices (P1-T1..T8).

Chains the pure stage functions with the in-process stub clients (no DB, no
network) and asserts the two terminal outcomes the MVP must demonstrate: a clean
invoice submits, an invoice with an unmatched line item is held with a specific
reason. The orchestrator (P1-T9) will encapsulate this chaining + persistence.
"""

import json
import pathlib

from backend.catalog import fetch
from backend.clients import PassthroughLLMClient, StubClinRunClient, StubMCPReferenceClient
from backend.clients.errors import CatalogNotFound
from backend.context import resolve
from backend.decision import decide
from backend.domain import Decision
from backend.exceptions import from_decision
from backend.extraction import extract
from backend.intake import ingest
from backend.matching import match
from backend.parser import parse
from backend.submission import submit_invoice

SAMPLES = pathlib.Path(__file__).resolve().parents[2] / "samples"


def _run(sample: dict):
    llm = PassthroughLLMClient()
    ref = StubMCPReferenceClient()

    invoice = ingest(sample)
    parsed = parse(invoice.id, sample)
    extraction = extract(invoice.id, parsed, llm)
    ctx = resolve(invoice.id, extraction.metadata, sample.get("source", {}), ref)
    try:
        catalog = fetch(ctx, ref)
        catalog_available = True
    except CatalogNotFound:
        catalog, catalog_available = [], False
    matches = match(extraction.line_items, catalog)
    decision = decide(extraction, ctx, matches, catalog_available)
    return invoice, extraction, ctx, matches, decision


def _load(name: str) -> dict:
    return json.loads((SAMPLES / name).read_text())


def test_clean_invoice_submits():
    invoice, extraction, ctx, matches, decision = _run(_load("inv_clean_001.json"))

    assert len(extraction.line_items) == 4
    assert ctx.sponsor_id == "sponsor_001"
    assert ctx.study_id == "study_001"
    assert ctx.site_id == "site_001"
    assert "multiple_site_candidates" not in ctx.warnings
    assert all(m.catalog_item_id is not None for m in matches)
    assert decision.decision is Decision.SUBMIT

    # the submit path produces an accepted ClinRun result
    result = submit_invoice(invoice, extraction.metadata, ctx, matches, StubClinRunClient())
    assert result.accepted is True


def test_email_body_invoice_submits():
    """An invoice delivered in the email body (no attachment) flows to submit."""
    invoice, extraction, _ctx, matches, decision = _run(_load("inv_body_003.json"))

    assert len(extraction.line_items) == 2
    assert all(m.catalog_item_id is not None for m in matches)
    assert decision.decision is Decision.SUBMIT


def test_image_attachment_invoice_submits():
    """A scanned-image attachment flows to submit just like a PDF."""
    _invoice, extraction, _ctx, matches, decision = _run(_load("inv_image_004.json"))

    assert len(extraction.line_items) == 3
    assert all(m.catalog_item_id is not None for m in matches)
    assert decision.decision is Decision.SUBMIT


def test_unmatched_invoice_holds():
    invoice, _extraction, _ctx, matches, decision = _run(_load("inv_hold_unmatched_002.json"))

    assert any(m.catalog_item_id is None for m in matches)
    assert decision.decision is Decision.HOLD

    exceptions = from_decision(invoice.id, decision)
    assert any(e.type == "unmatched_line_item" and e.severity.value == "high" for e in exceptions)


def test_mismatched_metadata_invoice_holds():
    """Invoice names Sponsor A but protocol maps to Sponsor B → hold (§15)."""
    invoice, _extraction, ctx, _matches, decision = _run(_load("inv_hold_mismatch_005.json"))

    assert "sponsor_mismatch" in ctx.warnings
    assert decision.decision is Decision.HOLD
    assert any(f.type == "context_mismatch" for f in decision.risk_flags)

    exceptions = from_decision(invoice.id, decision)
    assert any(e.type == "context_mismatch" for e in exceptions)
