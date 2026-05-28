"""Render the JSON invoice samples to real PDFs (user-requested demo path).

Dev tooling only — uses ``reportlab`` (a dev dependency) so the generated PDFs
round-trip through ``backend.parser.pdf`` (which uses ``pypdf`` at runtime). The
layout (labelled header lines + a pipe-delimited line-item table) is defined by
the shared constants in ``backend.parser.pdf``, so generator and parser never
drift.

Usage:
    python -m samples.generate_pdfs            # writes samples/pdf/*.pdf

Then watch one flow through the pipeline:
    python -m backend.tools.process_pdf samples/pdf/inv_clean_001.pdf
"""

from __future__ import annotations

import json
import pathlib

from backend.parser.pdf import COLUMN_SEP, FIELD_LABELS, LINE_ITEM_COLUMNS, LINE_ITEMS_MARKER
from reportlab.lib.pagesizes import LETTER
from reportlab.pdfgen import canvas

SAMPLES_DIR = pathlib.Path(__file__).resolve().parent
PDF_DIR = SAMPLES_DIR / "pdf"

# JSON samples that have structured invoice content worth rendering as a PDF.
_RENDERABLE = ["inv_clean_001", "inv_hold_unmatched_002", "inv_body_003",
               "inv_hold_mismatch_005", "inv_uncertain_006"]

_MARGIN = 72
_TOP = 720
_LINE_H = 16


def _invoice_block(sample: dict) -> dict:
    """The structured invoice content lives under ``document`` or ``body``."""
    return sample.get("document") or sample.get("body") or {}


def render_invoice_pdf(sample: dict, path: pathlib.Path | str) -> pathlib.Path:
    """Render one sample's invoice content to a PDF at ``path``."""
    block = _invoice_block(sample)
    metadata = block.get("metadata", {})
    line_items = block.get("line_items", [])
    path = pathlib.Path(path)

    c = canvas.Canvas(str(path), pagesize=LETTER)
    y = _TOP

    c.setFont("Helvetica-Bold", 16)
    c.drawString(_MARGIN, y, "INVOICE")
    y -= _LINE_H * 2

    c.setFont("Helvetica", 11)
    for field, label in FIELD_LABELS.items():
        value = metadata.get(field)
        if value not in (None, ""):
            c.drawString(_MARGIN, y, f"{label}: {value}")
            y -= _LINE_H

    y -= _LINE_H
    c.setFont("Helvetica-Bold", 11)
    c.drawString(_MARGIN, y, LINE_ITEMS_MARKER)
    y -= _LINE_H

    # Monospaced table so the text layer extracts cleanly.
    c.setFont("Courier", 10)
    c.drawString(_MARGIN, y, COLUMN_SEP.join(LINE_ITEM_COLUMNS))
    y -= _LINE_H
    for item in line_items:
        row = COLUMN_SEP.join([
            str(item.get("raw_description", "")),
            str(item.get("quantity", "") or ""),
            str(item.get("unit_price", "") or ""),
            str(item.get("total", "") or ""),
        ])
        c.drawString(_MARGIN, y, row)
        y -= _LINE_H

    c.showPage()
    c.save()
    return path


def main() -> None:
    PDF_DIR.mkdir(exist_ok=True)
    for stem in _RENDERABLE:
        sample = json.loads((SAMPLES_DIR / f"{stem}.json").read_text())
        out = render_invoice_pdf(sample, PDF_DIR / f"{stem}.pdf")
        print(f"wrote {out.relative_to(SAMPLES_DIR.parent)}")


if __name__ == "__main__":
    main()
