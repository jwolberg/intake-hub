"""Unit tests for OCR word-box extraction (P4-T2).

Covers the offline ``StubOCRClient`` (PDF text-layer geometry, deterministic, no
Tesseract) and the pure Tesseract TSV parser. Network/Tesseract-free, so the
offline-first constraint (spec §7) holds.
"""

import pathlib

import pytest
from backend.clients import get_ocr_client
from backend.ocr import OCRClient, StubOCRClient, parse_tesseract_tsv

PDF = pathlib.Path(__file__).resolve().parents[2] / "samples" / "pdf" / "inv_clean_001.pdf"


def test_stub_extracts_word_boxes_from_pdf():
    words = StubOCRClient().extract_words(str(PDF))

    assert len(words) > 0
    # all on page 1, contiguous 0-based indices, normalized boxes in range
    assert {w.page_number for w in words} == {1}
    assert [w.index for w in words] == list(range(len(words)))
    for w in words:
        b = w.bbox
        assert 0.0 <= b.x <= 1.0 and 0.0 <= b.y <= 1.0
        assert 0.0 <= b.width <= 1.0 and 0.0 <= b.height <= 1.0

    # the invoice number is a real word the vision extractor can later cite
    assert any(w.text == "INV-1001" for w in words)


def test_stub_returns_empty_for_non_pdf():
    assert StubOCRClient().extract_words("scan.png") == []


def test_stub_returns_empty_for_missing_file():
    assert StubOCRClient().extract_words("/no/such/invoice.pdf") == []


def test_default_ocr_client_is_offline_stub():
    client = get_ocr_client()
    assert isinstance(client, StubOCRClient)
    assert isinstance(client, OCRClient)  # satisfies the protocol


# --- Tesseract TSV parsing (pure; no Tesseract binary needed) ----------------

# Minimal image_to_data TSV: a header, two structural rows (conf -1), one blank
# token, and two real words at known pixel positions on a 1000x2000 page.
_TSV = "\n".join([
    "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext",
    "1\t1\t0\t0\t0\t0\t0\t0\t1000\t2000\t-1\t",
    "4\t1\t1\t1\t1\t0\t100\t200\t300\t40\t-1\t",
    "5\t1\t1\t1\t1\t1\t100\t200\t140\t40\t96\tINV-1001",
    "5\t1\t1\t1\t1\t2\t250\t200\t100\t40\t91\tRiverside",
    "5\t1\t1\t1\t1\t3\t400\t200\t10\t40\t0\t   ",
])


def test_parse_tesseract_tsv_normalizes_and_skips_non_words():
    words = parse_tesseract_tsv(_TSV, page_number=1, page_width=1000, page_height=2000)

    assert [w.text for w in words] == ["INV-1001", "Riverside"]  # structural/blank skipped
    assert [w.index for w in words] == [0, 1]

    first = words[0]
    assert first.bbox.x == pytest.approx(0.1)      # 100/1000
    assert first.bbox.y == pytest.approx(0.1)      # 200/2000
    assert first.bbox.width == pytest.approx(0.14)  # 140/1000
    assert first.bbox.height == pytest.approx(0.02)  # 40/2000


def test_parse_tesseract_tsv_empty_input():
    assert parse_tesseract_tsv("", page_number=1, page_width=100, page_height=100) == []


def test_parse_tesseract_tsv_bad_header_raises():
    with pytest.raises(ValueError):
        parse_tesseract_tsv("a\tb\tc\n1\t2\t3", page_number=1, page_width=10, page_height=10)
