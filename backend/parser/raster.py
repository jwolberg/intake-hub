"""Rasterize an invoice source (PDF or image) to per-page images + dimensions.

P4-T1 of Visual Document Review (`/docs/specs/visual-document-review.md`): the
reviewer hub needs the *original page image* so later tickets can overlay
extracted-field highlights on it. This module renders each page of a source
document to a normalized PNG and reports its pixel dimensions, backing the
``GET /pages`` (count + dims) and ``GET /pages/:n/image`` (the raster) endpoints.

Rasterization is **local** (no network), so it preserves the offline-first
constraint (spec §7). One backend — PyMuPDF (``fitz``, OD-8) — covers both PDFs
and image attachments (MuPDF opens an image as a one-page document), so no second
imaging dependency is needed. The heavy import is lazy (matching
``backend/parser/pdf.py``) so the offline import path stays light; swapping raster
backends (e.g. ``pdf2image``+poppler) touches only this module.
"""

from __future__ import annotations

import pathlib
from dataclasses import dataclass
from functools import lru_cache

# Formats we can rasterize. PDFs render page-by-page; images are a single page.
PDF_SUFFIXES = (".pdf",)
IMAGE_SUFFIXES = (".png", ".jpg", ".jpeg", ".tif", ".tiff", ".gif", ".bmp")
RASTERIZABLE_SUFFIXES = PDF_SUFFIXES + IMAGE_SUFFIXES

# Render resolution: legible for review without bloating the served bytes. Boxes
# are normalized to [0,1] downstream (spec §3), so the exact DPI never matters to
# the overlay — only to image sharpness.
RENDER_DPI = 150


@dataclass(frozen=True)
class RenderedPage:
    """One rasterized page: 1-based number, pixel size, and PNG bytes."""

    page_number: int
    width: int
    height: int
    image_png: bytes


def is_rasterizable(source) -> bool:
    """True if ``source``'s extension is a format we can render."""
    return pathlib.Path(source).suffix.lower() in RASTERIZABLE_SUFFIXES


def render_pages(source, *, dpi: int = RENDER_DPI) -> list[RenderedPage]:
    """Render every page of ``source`` (a PDF or image path) to PNG + dims.

    Raises ``FileNotFoundError`` if the path is missing and ``ValueError`` for an
    unsupported format, so callers can map those to a 404/empty response. Results
    are cached per (path, mtime, dpi) so serving ``/pages`` then ``/pages/:n/image``
    rasterizes the document only once.
    """
    path = pathlib.Path(source)
    if not path.is_file():
        raise FileNotFoundError(source)
    if not is_rasterizable(path):
        raise ValueError(f"unsupported source format: {path.suffix!r}")
    # mtime in the key invalidates the cache if the file is regenerated.
    return list(_render_cached(str(path.resolve()), path.stat().st_mtime_ns, dpi))


def render_pdf_bytes(data: bytes, *, dpi: int = RENDER_DPI) -> list[RenderedPage]:
    """Render every page of an in-memory PDF (``data``) to PNG + dims.

    The cloud path (P5-T3): the source PDF is persisted as bytes (Cloud Run has no
    local file to point at), so pages render straight from the stored blob.
    PDF-only — ``fitz`` opens the stream with ``filetype="pdf"``.
    """
    return list(_render_cached_bytes(data, dpi))


@lru_cache(maxsize=16)
def _render_cached(resolved_path: str, _mtime_ns: int, dpi: int) -> tuple[RenderedPage, ...]:
    import fitz  # lazy: only the rasterization path pulls in PyMuPDF

    with fitz.open(resolved_path) as doc:
        return _render_doc(doc, dpi)


@lru_cache(maxsize=16)
def _render_cached_bytes(data: bytes, dpi: int) -> tuple[RenderedPage, ...]:
    import fitz  # lazy: only the rasterization path pulls in PyMuPDF

    with fitz.open(stream=data, filetype="pdf") as doc:
        return _render_doc(doc, dpi)


def _render_doc(doc, dpi: int) -> tuple[RenderedPage, ...]:
    return tuple(_rasterize(page, number, dpi) for number, page in enumerate(doc, start=1))


def _rasterize(page, number: int, dpi: int) -> RenderedPage:
    """Render one page — the only expensive step (the tests count calls to it)."""
    pixmap = page.get_pixmap(dpi=dpi)
    return RenderedPage(
        page_number=number,
        width=pixmap.width,
        height=pixmap.height,
        image_png=pixmap.tobytes("png"),
    )


# --- lazy per-page access (#0012) --------------------------------------------
# The detail view needs only page sizes, and an image request only its own page,
# so neither should rasterize the whole document.


@dataclass(frozen=True)
class PageDims:
    """A page's 1-based number and the pixel size it renders to at ``dpi``."""

    page_number: int
    width: int
    height: int


def _dims(doc, dpi: int) -> list[PageDims]:
    import fitz

    zoom = fitz.Matrix(dpi / 72, dpi / 72)
    out = []
    for number, page in enumerate(doc, start=1):
        box = (page.rect * zoom).irect  # the same bbox get_pixmap(dpi=) renders
        out.append(PageDims(page_number=number, width=box.width, height=box.height))
    return out


def _open_bytes(data: bytes):
    import fitz  # lazy: only the rasterization path pulls in PyMuPDF

    return fitz.open(stream=data, filetype="pdf")


def _open_path(source):
    import fitz

    path = pathlib.Path(source)
    if not path.is_file():
        raise FileNotFoundError(source)
    if not is_rasterizable(path):
        raise ValueError(f"unsupported source format: {path.suffix!r}")
    return fitz.open(path)


def pdf_page_dims(data: bytes, *, dpi: int = RENDER_DPI) -> list[PageDims]:
    """Page sizes of an in-memory PDF without rasterizing anything."""
    with _open_bytes(data) as doc:
        return _dims(doc, dpi)


def page_dims(source, *, dpi: int = RENDER_DPI) -> list[PageDims]:
    """Page sizes of a PDF/image file without rasterizing anything."""
    with _open_path(source) as doc:
        return _dims(doc, dpi)


def render_pdf_page(data: bytes, page_number: int, *, dpi: int = RENDER_DPI) -> RenderedPage | None:
    """Rasterize one 1-based page of an in-memory PDF; ``None`` if out of range."""
    with _open_bytes(data) as doc:
        if not 1 <= page_number <= doc.page_count:
            return None
        return _rasterize(doc[page_number - 1], page_number, dpi)


def render_page(source, page_number: int, *, dpi: int = RENDER_DPI) -> RenderedPage | None:
    """Rasterize one 1-based page of a PDF/image file; ``None`` if out of range."""
    with _open_path(source) as doc:
        if not 1 <= page_number <= doc.page_count:
            return None
        return _rasterize(doc[page_number - 1], page_number, dpi)
