"""Unit tests for citation synthesis + bbox resolution (P4-T4).

Covers the offline synthesizer (value → OCR word indices) and the resolver
(indices → union bbox, dropping out-of-range), the no-hallucination guarantee
(spec §3). Network/model-free.
"""

import pytest
from backend.domain import (
    BoundingBox,
    Citation,
    CitationStatus,
    ExtractionResult,
    InvoiceMetadata,
    LineItem,
    WordBox,
)
from backend.extraction.citations import (
    resolve_citations,
    synthesize_citations,
)


def _wb(index: int, text: str, x: float, w: float = 0.1) -> WordBox:
    return WordBox(
        page_number=1, index=index, text=text,
        bbox=BoundingBox(x=x, y=0.2, width=w, height=0.05),
    )


# --- resolve_citations -------------------------------------------------------


def test_resolve_unions_cited_word_boxes():
    words = [_wb(0, "Riverside", 0.10), _wb(1, "Clinical", 0.22), _wb(2, "Research", 0.34)]
    cite = Citation(page_number=1, target_id="metadata.vendor_name",
                    quote="Riverside Clinical Research", word_indices=[0, 1, 2])

    [resolved] = resolve_citations([cite], words)
    assert resolved.bbox is not None
    assert resolved.bbox.x == pytest.approx(0.10)  # leftmost
    assert resolved.bbox.y == pytest.approx(0.20)
    # spans from x=0.10 to x=0.34+0.10=0.44
    assert resolved.bbox.width == pytest.approx(0.44 - 0.10)
    assert resolved.bbox.height == pytest.approx(0.05)


def test_resolve_drops_out_of_range_indices():
    words = [_wb(0, "INV-1001", 0.10)]
    cite = Citation(page_number=1, target_id="metadata.invoice_number",
                    quote="INV-1001", word_indices=[0, 99])  # 99 doesn't exist

    [resolved] = resolve_citations([cite], words)
    assert resolved.bbox is not None  # resolved from the valid index only
    assert resolved.bbox.x == 0.10


def test_resolve_no_box_when_no_valid_words():
    words = [_wb(0, "INV-1001", 0.10)]
    cite = Citation(page_number=1, target_id="metadata.invoice_number",
                    quote="GHOST", word_indices=[99])  # all out of range

    [resolved] = resolve_citations([cite], words)
    assert resolved.bbox is None  # no hallucinated highlight (spec §3)


def test_resolve_respects_page_scoping():
    box1 = BoundingBox(x=0.1, y=0.1, width=0.1, height=0.1)
    box2 = BoundingBox(x=0.5, y=0.5, width=0.1, height=0.1)
    words = [
        WordBox(page_number=1, index=0, text="A", bbox=box1),
        WordBox(page_number=2, index=0, text="B", bbox=box2),
    ]
    cite = Citation(page_number=2, target_id="metadata.x", quote="B", word_indices=[0])

    [resolved] = resolve_citations([cite], words)
    assert resolved.bbox.x == 0.5  # the page-2 word, not page-1 index 0


# --- synthesize_citations ----------------------------------------------------


def _extraction(**meta) -> ExtractionResult:
    return ExtractionResult(
        metadata=InvoiceMetadata(**{k: v for k, v in meta.items() if k != "field_confidence"}),
        field_confidence=meta.get("field_confidence", {}),
    )


def test_synthesize_anchors_values_to_words():
    words = [_wb(0, "INV-1001", 0.10), _wb(1, "Acme", 0.30)]
    extraction = _extraction(invoice_number="INV-1001", vendor_name="Acme",
                             field_confidence={"invoice_number": 0.95, "vendor_name": 0.95})

    cites = {c.target_id: c for c in synthesize_citations(extraction, words)}
    assert cites["metadata.invoice_number"].word_indices == [0]
    assert cites["metadata.invoice_number"].status is CitationStatus.EXTRACTED
    assert cites["metadata.vendor_name"].word_indices == [1]


def test_synthesize_marks_unanchored_value_unreadable():
    words = [_wb(0, "INV-1001", 0.10)]
    extraction = _extraction(invoice_number="INV-1001", vendor_name="Nowhere Inc",
                             field_confidence={"invoice_number": 0.95, "vendor_name": 0.95})

    cites = {c.target_id: c for c in synthesize_citations(extraction, words)}
    vendor = cites["metadata.vendor_name"]
    assert vendor.status is CitationStatus.UNREADABLE
    assert vendor.word_indices == []


def test_synthesize_marks_low_confidence_uncertain():
    words = [_wb(0, "INV-1001", 0.10)]
    extraction = _extraction(invoice_number="INV-1001",
                             field_confidence={"invoice_number": 0.3})

    [cite] = synthesize_citations(extraction, words)
    assert cite.status is CitationStatus.UNCERTAIN
    assert cite.word_indices == [0]


def test_synthesize_cites_line_items():
    words = [_wb(0, "ECG", 0.10)]
    extraction = ExtractionResult(
        line_items=[LineItem(invoice_id="i", id="line_a", raw_description="ECG",
                             extraction_confidence=0.9)],
    )
    [cite] = synthesize_citations(extraction, words)
    assert cite.target_id == "line_item.line_a.raw_description"
    assert cite.word_indices == [0]
