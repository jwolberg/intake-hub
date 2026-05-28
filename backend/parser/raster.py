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


@lru_cache(maxsize=16)
def _render_cached(resolved_path: str, _mtime_ns: int, dpi: int) -> tuple[RenderedPage, ...]:
    import fitz  # lazy: only the rasterization path pulls in PyMuPDF

    pages: list[RenderedPage] = []
    with fitz.open(resolved_path) as doc:
        for number, page in enumerate(doc, start=1):
            pixmap = page.get_pixmap(dpi=dpi)
            pages.append(
                RenderedPage(
                    page_number=number,
                    width=pixmap.width,
                    height=pixmap.height,
                    image_png=pixmap.tobytes("png"),
                )
            )
    return tuple(pages)
