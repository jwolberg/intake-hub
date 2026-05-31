"""Unit tests for the parser + extraction stages (P2-A1).

Cover multi-format detection, per-field confidence + source evidence, explicit
uncertainty marking, and line-item source-text preservation (PRD FR2;
ARCHITECTURE.md §8).
"""

import json
import pathlib

from backend.clients import PassthroughLLMClient
from backend.clients.llm import StubLLMClient
from backend.domain import InvoiceMetadata
from backend.extraction import EXTRACTION_SYSTEM, extract
from backend.parser import parse

SAMPLES = pathlib.Path(__file__).resolve().parents[2] / "samples"


def _load(name: str) -> dict:
    return json.loads((SAMPLES / name).read_text())


def test_extraction_prompt_pins_every_schema_field():
    """The system prompt must name every metadata field verbatim, or a real
    provider invents synonyms (e.g. "vendor" vs "vendor_name") that
    ``_build_metadata`` drops as missing — the blank Vendor/Sponsor/Study bug.
    """
    for field in InvoiceMetadata.model_fields:
        assert field in EXTRACTION_SYSTEM, f"prompt omits schema field {field!r}"


def test_parse_detects_pdf_attachment():
    parsed = parse("inv_1", _load("inv_clean_001.json"))
    assert parsed.format == "pdf"
    assert parsed.sections == ["metadata", "line_items"]


def test_parse_detects_image_attachment():
    parsed = parse("inv_4", _load("inv_image_004.json"))
    assert parsed.format == "image"


def test_parse_detects_email_body():
    parsed = parse("inv_3", _load("inv_body_003.json"))
    assert parsed.format == "email_body"
    # the body payload is rendered to text the extractor can read
    assert "INV-1003" in parsed.text


def test_extract_captures_per_field_confidence_and_evidence():
    sample = _load("inv_clean_001.json")
    parsed = parse("inv_1", sample)
    result = extract("inv_1", parsed, PassthroughLLMClient())

    assert result.metadata.invoice_number == "INV-1001"
    # present + corroborated in source text → high confidence, with evidence
    assert result.field_confidence["invoice_number"] >= 0.95
    assert "PDF attachment" in result.field_evidence["invoice_number"]
    assert "INV-1001" in result.field_evidence["invoice_number"]


def test_extract_marks_missing_fields_uncertain():
    sample = _load("inv_clean_001.json")  # has no due_date / payment_terms / tax
    parsed = parse("inv_1", sample)
    result = extract("inv_1", parsed, PassthroughLLMClient())

    assert "due_date" in result.missing_fields
    assert result.field_confidence["due_date"] == 0.0
    assert "due_date" not in result.field_evidence


def test_extract_preserves_line_item_source_text():
    sample = _load("inv_clean_001.json")
    parsed = parse("inv_1", sample)
    result = extract("inv_1", parsed, PassthroughLLMClient())

    assert len(result.line_items) == 4
    for item in result.line_items:
        assert item.raw_source_text  # source text preserved per line (FR2)
        assert item.raw_description in item.raw_source_text
        assert item.extraction_confidence == 0.9  # complete: has description + total


def test_extract_honors_annotated_field_confidence():
    """A provider may return ``{value, confidence, evidence}`` per field."""
    llm = StubLLMClient(responses=[{
        "metadata": {
            "invoice_number": {"value": "INV-9", "confidence": 0.42, "evidence": "smudged scan"},
        },
        "line_items": [],
    }])
    parsed = parse("inv_9", {"document": {}, "source": {"attachment": "x.png"}})
    result = extract("inv_9", parsed, llm)

    assert result.metadata.invoice_number == "INV-9"
    assert result.field_confidence["invoice_number"] == 0.42
    assert result.field_evidence["invoice_number"] == "smudged scan"


def test_extract_partial_line_item_gets_lower_confidence():
    llm = StubLLMClient(responses=[{
        "metadata": {},
        "line_items": [{"raw_description": "Consultation"}],  # no total
    }])
    parsed = parse("inv_p", {"document": {}})
    result = extract("inv_p", parsed, llm)

    assert result.line_items[0].extraction_confidence == 0.6
