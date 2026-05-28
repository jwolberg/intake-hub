"""Unit tests for vision extraction with word-index citations (P4-T3).

Covers the offline word-index matcher, the ``OfflineVisionLLMClient`` stand-in,
and ``extract_vision`` building schema-validated ``Citation`` objects. All
network/model-free (spec §7): the offline path reads OCR geometry from the
controlled sample PDF's text layer.
"""

import json
import pathlib

import pytest
from backend.clients import get_vision_llm_client
from backend.clients.vision import (
    OfflineVisionLLMClient,
    StubVisionLLMClient,
    VisionLLMClient,
    locate_value,
)
from backend.domain import BoundingBox, CitationStatus, WordBox
from backend.extraction import extract_vision
from backend.ocr import StubOCRClient
from backend.parser import parse

SAMPLES = pathlib.Path(__file__).resolve().parents[2] / "samples"
PDF = SAMPLES / "pdf" / "inv_clean_001.pdf"


def _words() -> list[WordBox]:
    return StubOCRClient().extract_words(str(PDF))


def _box() -> BoundingBox:
    return BoundingBox(x=0.1, y=0.1, width=0.1, height=0.05)


# --- locate_value (pure string matcher) --------------------------------------


def test_locate_single_token():
    words = [
        WordBox(page_number=1, index=0, text="Invoice", bbox=_box()),
        WordBox(page_number=1, index=1, text="INV-1001", bbox=_box()),
    ]
    assert locate_value("INV-1001", words) == (1, [1])


def test_locate_multi_token_contiguous():
    words = [
        WordBox(page_number=1, index=0, text="Vendor:", bbox=_box()),
        WordBox(page_number=1, index=1, text="Riverside", bbox=_box()),
        WordBox(page_number=1, index=2, text="Clinical", bbox=_box()),
        WordBox(page_number=1, index=3, text="Research", bbox=_box()),
    ]
    assert locate_value("Riverside Clinical Research", words) == (1, [1, 2, 3])


def test_locate_tolerates_punctuation_and_case():
    words = [WordBox(page_number=1, index=0, text="$1,234.00", bbox=_box())]
    assert locate_value("1234.00", words) == (1, [0])


def test_locate_returns_page_for_multipage():
    words = [
        WordBox(page_number=1, index=0, text="header", bbox=_box()),
        WordBox(page_number=2, index=0, text="TOTAL", bbox=_box()),
    ]
    assert locate_value("total", words) == (2, [0])


def test_locate_no_match_returns_none():
    words = [WordBox(page_number=1, index=0, text="INV-1001", bbox=_box())]
    assert locate_value("NOT-ON-PAGE", words) is None


# --- OfflineVisionLLMClient + factory ----------------------------------------


def test_default_vision_client_is_offline_stand_in():
    client = get_vision_llm_client()
    assert isinstance(client, OfflineVisionLLMClient)
    assert isinstance(client, VisionLLMClient)  # satisfies the protocol


def test_offline_client_annotates_values_with_indices():
    parsed = parse("inv_1", json.loads((SAMPLES / "inv_clean_001.json").read_text()))
    out = OfflineVisionLLMClient().complete_vision_json(
        system="s", user=parsed.text, image=b"", words=_words()
    )
    cell = out["metadata"]["invoice_number"]
    assert cell["value"] == "INV-1001"
    assert cell["word_indices"]  # anchored to a real OCR word
    assert cell["status"] == "extracted"
    assert out["line_items"][0]["description_citation"]["word_indices"]


def test_offline_client_returns_empty_for_non_json():
    out = OfflineVisionLLMClient().complete_vision_json(
        system="s", user="not json", image=b"", words=[]
    )
    assert out == {}


# --- extract_vision (citations) ----------------------------------------------


def test_extract_vision_builds_citations_for_sample_pdf():
    parsed = parse("inv_1", json.loads((SAMPLES / "inv_clean_001.json").read_text()))
    result = extract_vision("inv_1", parsed, _words(), b"", OfflineVisionLLMClient())

    # same downstream result as the text path, plus citations
    assert result.metadata.invoice_number == "INV-1001"
    assert len(result.line_items) == 4

    by_target = {c.target_id: c for c in result.citations}
    inv = by_target["metadata.invoice_number"]
    assert inv.status is CitationStatus.EXTRACTED
    assert inv.word_indices  # anchored to real OCR geometry
    assert inv.quote == "INV-1001"
    assert inv.bbox is None  # resolved later (P4-T4)

    # every line item is cited by its raw description
    line = result.line_items[0]
    assert f"line_item.{line.id}.raw_description" in by_target


def test_extract_vision_no_citation_for_missing_field():
    parsed = parse("inv_1", json.loads((SAMPLES / "inv_clean_001.json").read_text()))
    result = extract_vision("inv_1", parsed, _words(), b"", OfflineVisionLLMClient())

    assert "due_date" in result.missing_fields  # absent from the sample
    assert "metadata.due_date" not in {c.target_id for c in result.citations}


def test_extract_vision_unreadable_when_value_not_in_ocr():
    """A value present in extraction but absent from OCR words → no box."""
    vision = StubVisionLLMClient(responses=[{
        "metadata": {
            "invoice_number": {"value": "GHOST-9", "word_indices": [], "status": "unreadable"},
        },
        "line_items": [],
    }])
    parsed = parse("inv_x", {"document": {}, "source": {"attachment": "x.png"}})
    result = extract_vision("inv_x", parsed, [], b"", vision)

    cite = next(c for c in result.citations if c.target_id == "metadata.invoice_number")
    assert cite.status is CitationStatus.UNREADABLE
    assert cite.word_indices == []


def test_extract_vision_uncertain_when_low_confidence():
    """Anchored but low-confidence value is surfaced as uncertain (gates review)."""
    vision = StubVisionLLMClient(responses=[{
        "metadata": {
            "invoice_number": {
                "value": "INV-9", "confidence": 0.3,
                "word_indices": [0], "page": 1, "status": "extracted",
            },
        },
        "line_items": [],
    }])
    parsed = parse("inv_x", {"document": {}, "source": {"attachment": "x.png"}})
    result = extract_vision("inv_x", parsed, [], b"", vision)

    cite = next(c for c in result.citations if c.target_id == "metadata.invoice_number")
    assert cite.status is CitationStatus.UNCERTAIN
    assert cite.word_indices == [0]


def test_extract_vision_rejects_invalid_status():
    vision = StubVisionLLMClient(responses=[{
        "metadata": {
            "invoice_number": {"value": "INV-9", "word_indices": [0], "status": "bogus"},
        },
        "line_items": [],
    }])
    parsed = parse("inv_x", {"document": {}, "source": {"attachment": "x.png"}})
    with pytest.raises(ValueError):
        extract_vision("inv_x", parsed, [], b"", vision)


def test_extract_vision_rejects_non_integer_indices():
    vision = StubVisionLLMClient(responses=[{
        "metadata": {
            "invoice_number": {"value": "INV-9", "word_indices": ["x"], "status": "extracted"},
        },
        "line_items": [],
    }])
    parsed = parse("inv_x", {"document": {}, "source": {"attachment": "x.png"}})
    with pytest.raises(ValueError):
        extract_vision("inv_x", parsed, [], b"", vision)
