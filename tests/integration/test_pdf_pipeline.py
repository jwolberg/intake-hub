"""End-to-end PDF round-trip test (user-requested demo path).

Renders a sample to a real PDF, runs it through the full ledger pipeline with
the offline ``LayoutLLMClient``, and asserts the outcome matches the JSON
path — i.e. extracting from a real PDF reaches the same terminal decision as
the structured document (PRD §7 Step 1-3). Ledger pivot: the terminal states
are POSTED/HELD (not the old submit/hold sponsor-matching outcomes), and the
assertion is "the two paths agree", not a hardcoded clinical outcome.
"""

import json
import pathlib

import pytest
from backend.clients import PassthroughLLMClient, StubSheetsClient
from backend.db.repository import InMemoryRepository
from backend.domain import InvoiceStatus
from backend.orchestrator import process
from backend.parser.pdf import LayoutLLMClient
from samples.generate_pdfs import render_invoice_pdf

SAMPLES = pathlib.Path(__file__).resolve().parents[2] / "samples"

# Every sample stem that generate_pdfs can render as a real PDF (the same set
# samples/generate_pdfs.py rasterizes).
STEMS = [
    "inv_clean_001", "inv_hold_unmatched_002", "inv_body_003",
    "inv_hold_mismatch_005", "inv_uncertain_006", "inv_ambiguous_008",
    "inv_large_007",
]


def _process(sample, llm):
    return process(sample, InMemoryRepository(), llm=llm, sheets=StubSheetsClient())


@pytest.mark.parametrize("stem", STEMS)
def test_pdf_reaches_a_terminal_state(tmp_path, stem):
    sample = json.loads((SAMPLES / f"{stem}.json").read_text())
    pdf_path = render_invoice_pdf(sample, tmp_path / f"{stem}.pdf")

    pdf_sample = {"source": {"channel": "upload", "attachment": f"{stem}.pdf",
                            "attachment_path": str(pdf_path)}}
    invoice = _process(pdf_sample, LayoutLLMClient())

    assert invoice.status in (InvoiceStatus.POSTED, InvoiceStatus.HELD)


@pytest.mark.parametrize("stem", STEMS)
def test_pdf_path_agrees_with_json_path(tmp_path, stem):
    sample = json.loads((SAMPLES / f"{stem}.json").read_text())

    json_invoice = _process(sample, PassthroughLLMClient())

    pdf_path = render_invoice_pdf(sample, tmp_path / f"{stem}.pdf")
    pdf_sample = {"source": {"channel": "upload", "attachment_path": str(pdf_path)}}
    pdf_invoice = _process(pdf_sample, LayoutLLMClient())

    assert pdf_invoice.status == json_invoice.status
    assert pdf_invoice.decision == json_invoice.decision
