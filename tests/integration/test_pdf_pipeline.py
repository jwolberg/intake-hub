"""End-to-end PDF round-trip test (user-requested demo path).

Renders a sample to a real PDF, runs it through the full pipeline with the
offline ``LayoutLLMClient``, and asserts the outcome matches the JSON path —
i.e. extracting from a real PDF reaches the same decision (PRD §7 Step 1-3).
"""

import json
import pathlib

import pytest
from backend.clients import (
    PassthroughLLMClient,
    StubClinRunClient,
    StubMCPReferenceClient,
)
from backend.db.repository import InMemoryRepository
from backend.orchestrator import process
from backend.parser.pdf import LayoutLLMClient
from samples.generate_pdfs import render_invoice_pdf

SAMPLES = pathlib.Path(__file__).resolve().parents[2] / "samples"

CASES = [
    ("inv_clean_001", "submitted", "submit"),
    ("inv_hold_unmatched_002", "held", "hold"),
    ("inv_body_003", "submitted", "submit"),
    ("inv_hold_mismatch_005", "held", "hold"),
]


def _process(sample, llm):
    return process(
        sample, InMemoryRepository(),
        llm=llm, ref=StubMCPReferenceClient(), clinrun=StubClinRunClient(),
    )


@pytest.mark.parametrize("stem,status,decision", CASES)
def test_pdf_reaches_expected_decision(tmp_path, stem, status, decision):
    sample = json.loads((SAMPLES / f"{stem}.json").read_text())
    pdf_path = render_invoice_pdf(sample, tmp_path / f"{stem}.pdf")

    pdf_sample = {"source": {"channel": "upload", "attachment": f"{stem}.pdf",
                            "attachment_path": str(pdf_path)}}
    invoice = _process(pdf_sample, LayoutLLMClient())

    assert invoice.status.value == status
    assert invoice.decision.value == decision


@pytest.mark.parametrize("stem,_status,_decision", CASES)
def test_pdf_path_agrees_with_json_path(tmp_path, stem, _status, _decision):
    sample = json.loads((SAMPLES / f"{stem}.json").read_text())

    json_invoice = _process(sample, PassthroughLLMClient())

    pdf_path = render_invoice_pdf(sample, tmp_path / f"{stem}.pdf")
    pdf_sample = {"source": {"channel": "upload", "attachment_path": str(pdf_path)}}
    pdf_invoice = _process(pdf_sample, LayoutLLMClient())

    assert pdf_invoice.status == json_invoice.status
    assert pdf_invoice.decision == json_invoice.decision
