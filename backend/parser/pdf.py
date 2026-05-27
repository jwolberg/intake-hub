"""Real PDF support for the parser stage (user-requested demo path).

The MVP parser reads a structured ``document`` block (a stand-in for OCR/parse
output). This module adds genuine PDF handling so an actual ``.pdf`` can flow
through the pipeline:

- ``extract_text`` pulls the text layer out of a PDF (via ``pypdf``).
- ``parse_invoice_text`` reverses the layout that ``samples/generate_pdfs.py``
  renders — labelled header lines plus a pipe-delimited line-item table — back
  into the ``{"metadata": ..., "line_items": ...}`` shape extraction expects.
- ``LayoutLLMClient`` exposes that parse behind the ``LLMClient`` protocol, so
  it is the *offline extraction stand-in* for the PDF path (no API key needed),
  exactly as ``PassthroughLLMClient`` is for the JSON path. A real provider
  (OD-2) replaces it to extract from arbitrary invoice text; nothing downstream
  changes.

The label map + table markers below are the single source of truth shared by
the generator and the parser, so the rendered PDF always round-trips.
"""

from __future__ import annotations

# Ordered metadata field -> human label, used to render and to parse headers.
FIELD_LABELS: dict[str, str] = {
    "invoice_number": "Invoice Number",
    "invoice_date": "Invoice Date",
    "due_date": "Due Date",
    "vendor_name": "Vendor",
    "sponsor_name": "Sponsor",
    "study_name": "Study",
    "protocol_number": "Protocol",
    "site_identifier": "Site",
    "billing_period": "Billing Period",
    "currency": "Currency",
    "total_amount": "Total Amount",
    "tax": "Tax",
    "payment_terms": "Payment Terms",
}

LINE_ITEMS_MARKER = "Line Items"
LINE_ITEM_COLUMNS = ("Description", "Qty", "Unit Price", "Line Total")
COLUMN_SEP = " | "

_LABEL_TO_FIELD = {label: field for field, label in FIELD_LABELS.items()}


def extract_text(source) -> str:
    """Return the concatenated text layer of a PDF (path, bytes, or stream)."""
    from pypdf import PdfReader  # lazy: only needed on the PDF path

    reader = PdfReader(source)
    return "\n".join((page.extract_text() or "") for page in reader.pages)


def parse_invoice_text(text: str) -> dict:
    """Parse rendered invoice text into ``{"metadata", "line_items"}``.

    Header lines look like ``Label: value``; the line-item table follows the
    ``Line Items`` marker as pipe-delimited ``desc | qty | unit | total`` rows.
    Unknown lines are ignored, so stray text never breaks extraction.
    """
    metadata: dict[str, str] = {}
    line_items: list[dict] = []
    in_items = False

    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line == LINE_ITEMS_MARKER:
            in_items = True
            continue

        if not in_items:
            label, sep, value = line.partition(":")
            if sep and value.strip():
                field = _LABEL_TO_FIELD.get(label.strip())
                if field:
                    metadata[field] = value.strip()
            continue

        if "|" not in line:
            continue
        cells = [c.strip() for c in line.split("|")]
        if len(cells) < 4 or cells[0].lower() == LINE_ITEM_COLUMNS[0].lower():
            continue  # malformed or the header row
        desc, qty, unit, total = cells[:4]
        if not desc:
            continue
        line_items.append({
            "raw_description": desc,
            "quantity": qty or None,
            "unit_price": unit or None,
            "total": total or None,
        })

    return {"metadata": metadata, "line_items": line_items}


class LayoutLLMClient:
    """Offline extraction stand-in for the PDF text layout (see module docstring)."""

    def complete_json(self, *, system: str, user: str) -> dict:
        return parse_invoice_text(user)
