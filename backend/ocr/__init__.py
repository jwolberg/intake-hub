"""Stage: OCR word-box extraction (P4-T2; OD-6).

Turns an invoice's pages into a flat list of ``WordBox`` — each OCR word with a
0-based per-page ``index`` and a normalized ``[0,1]`` bounding box. The vision
extractor (P4-T3) receives these words tagged with their indices and cites
*indices* (never coordinates), so the citation resolver (P4-T4) can union real
OCR geometry into highlight boxes that cannot be hallucinated (spec §3).

Two implementations behind one ``OCRClient`` protocol (mirroring the LLM seam):

- ``StubOCRClient`` — the **offline default**. Reads each word's box from the
  PDF *text layer* via PyMuPDF: we render the controlled sample PDFs ourselves
  (RUNBOOK Path D), so their geometry is exact and deterministic — a
  network/Tesseract-free analogue of OCR output (spec §7).
- ``TesseractOCRClient`` — the real engine (OD-6): runs Tesseract on each
  rendered page image and parses its TSV. Heavy deps (pytesseract, the Tesseract
  binary, Pillow) import lazily, so they are never needed on the default path;
  swapping it in is a one-line change in ``get_ocr_client``.

The interface takes both the original ``source`` path and the rendered ``pages``
(both produced by the parser/raster stage): the stub reads the text-layer source,
the real engine OCRs the page images. Each uses what it needs.
"""

from __future__ import annotations

import pathlib
from typing import Protocol, runtime_checkable

from backend.domain import BoundingBox, WordBox
from backend.parser.raster import RenderedPage


@runtime_checkable
class OCRClient(Protocol):
    def extract_words(self, source: str, pages: list[RenderedPage]) -> list[WordBox]:
        """Return every page's words as ``WordBox`` (index 0-based per page)."""
        ...


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))


def _normalized_box(
    x: float, y: float, w: float, h: float, page_w: float, page_h: float
) -> BoundingBox:
    """Scale a pixel/point box by the page size into the unit square."""
    return BoundingBox(
        x=_clamp01(x / page_w),
        y=_clamp01(y / page_h),
        width=_clamp01(w / page_w),
        height=_clamp01(h / page_h),
    )


class StubOCRClient:
    """Offline OCR stand-in: per-word boxes from the PDF text layer (spec §7).

    Deterministic and network-free, with no Tesseract binary. A non-PDF or
    text-less source (e.g. a flat scanned image) yields no words — that case
    needs the real ``TesseractOCRClient``.
    """

    def extract_words(self, source: str, pages: list[RenderedPage] | None = None) -> list[WordBox]:
        path = pathlib.Path(source)
        if path.suffix.lower() != ".pdf" or not path.is_file():
            return []

        import fitz  # lazy: only the OCR/raster path pulls in PyMuPDF

        words: list[WordBox] = []
        with fitz.open(path) as doc:
            for page_number, page in enumerate(doc, start=1):
                rect = page.rect
                if not rect.width or not rect.height:
                    continue
                # (x0, y0, x1, y1, text, block, line, word_no), reading order.
                for index, word in enumerate(page.get_text("words")):
                    x0, y0, x1, y1, text = word[0], word[1], word[2], word[3], word[4]
                    if not text.strip():
                        continue
                    words.append(WordBox(
                        page_number=page_number,
                        index=index,
                        text=text,
                        bbox=_normalized_box(x0, y0, x1 - x0, y1 - y0, rect.width, rect.height),
                    ))
        return words


# Tesseract TSV columns (word rows are level 5 with conf >= 0).
_TSV_REQUIRED = ("left", "top", "width", "height", "conf", "text")


def parse_tesseract_tsv(
    tsv: str, *, page_number: int, page_width: float, page_height: float
) -> list[WordBox]:
    """Parse Tesseract ``image_to_data`` TSV into normalized ``WordBox`` (pure).

    Skips non-word rows (``conf < 0``) and blank tokens; coordinates are pixels,
    normalized by the page image size. Kept separate from the client so it is
    testable without Tesseract installed.
    """
    lines = tsv.splitlines()
    if not lines:
        return []
    columns = {name: i for i, name in enumerate(lines[0].split("\t"))}
    if not all(name in columns for name in _TSV_REQUIRED):
        raise ValueError("unexpected Tesseract TSV header")

    words: list[WordBox] = []
    index = 0
    for raw in lines[1:]:
        cells = raw.split("\t")
        if len(cells) <= columns["text"]:
            continue
        text = cells[columns["text"]].strip()
        if not text:
            continue
        try:
            conf = float(cells[columns["conf"]])
        except ValueError:
            continue
        if conf < 0:  # -1 marks structural (non-word) rows
            continue
        left = float(cells[columns["left"]])
        top = float(cells[columns["top"]])
        width = float(cells[columns["width"]])
        height = float(cells[columns["height"]])
        words.append(WordBox(
            page_number=page_number,
            index=index,
            text=text,
            bbox=_normalized_box(left, top, width, height, page_width, page_height),
        ))
        index += 1
    return words


class TesseractOCRClient:
    """Real OCR via Tesseract on the rendered page images (OD-6).

    Lazily imports ``pytesseract``/Pillow so neither they nor the Tesseract binary
    are required on the offline default path. Enable by returning this from
    ``get_ocr_client`` (and ``pip install pytesseract pillow`` + the binary).
    """

    def extract_words(self, source: str, pages: list[RenderedPage]) -> list[WordBox]:
        import io

        import pytesseract
        from PIL import Image

        words: list[WordBox] = []
        for page in pages:
            with Image.open(io.BytesIO(page.image_png)) as image:
                tsv = pytesseract.image_to_data(image)
            words.extend(parse_tesseract_tsv(
                tsv, page_number=page.page_number,
                page_width=page.width, page_height=page.height,
            ))
        return words
