"""Unit tests for page rasterization (P4-T1).

Renders a committed sample PDF and a derived image, asserting page count, pixel
dimensions, and PNG output. Local rasterization only — no network — so the
offline-first constraint (spec §7) holds.
"""

import pathlib

import pytest
from backend.parser.raster import RASTERIZABLE_SUFFIXES, is_rasterizable, render_pages

PDF = pathlib.Path(__file__).resolve().parents[2] / "samples" / "pdf" / "inv_clean_001.pdf"
_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


def test_render_pdf_pages():
    pages = render_pages(PDF)

    assert [p.page_number for p in pages] == [1]  # the sample is single-page
    page = pages[0]
    assert page.width > 0 and page.height > 0
    assert page.height > page.width  # LETTER portrait
    assert page.image_png.startswith(_PNG_MAGIC)


def test_render_image_source(tmp_path):
    # Derive a real PNG from the PDF, then render that image through the same path.
    png_path = tmp_path / "page.png"
    png_path.write_bytes(render_pages(PDF)[0].image_png)

    pages = render_pages(png_path)

    assert len(pages) == 1
    assert pages[0].page_number == 1
    assert pages[0].image_png.startswith(_PNG_MAGIC)


def test_dpi_changes_resolution():
    low = render_pages(PDF, dpi=72)[0]
    high = render_pages(PDF, dpi=300)[0]
    assert high.width > low.width and high.height > low.height


def test_is_rasterizable():
    assert is_rasterizable("INV-1001.pdf")
    assert is_rasterizable("scan.PNG")  # case-insensitive
    assert not is_rasterizable("notes.txt")
    assert ".pdf" in RASTERIZABLE_SUFFIXES


def test_missing_file_raises():
    with pytest.raises(FileNotFoundError):
        render_pages("/no/such/invoice.pdf")


def test_unsupported_format_raises(tmp_path):
    txt = tmp_path / "invoice.txt"
    txt.write_text("not an image")
    with pytest.raises(ValueError):
        render_pages(txt)
