"""Stage: parser.

Extract raw text + structure from an invoice (PRD §7 Step 2; ARCHITECTURE.md §4).

An invoice can arrive in several shapes (PRD §7 Step 1): a parsed PDF/image
attachment or details inline in the email body. This stage locates the invoice
payload, records which format it came from (so extraction can cite it as source
evidence), and emits the text the extraction stage reads.

MVP: a structured ``document``/``body`` block stands in for OCR/text output
(PRD §4 non-goal: no real document intelligence). We serialise it to JSON so the
offline extraction stand-in can read it; a real parser would emit the invoice's
natural text here and nothing downstream changes. A free-text body is passed
through verbatim — offline extraction then marks its fields uncertain.
"""

from __future__ import annotations

import json

from backend.domain import ParsedDocument

_IMAGE_SUFFIXES = (".png", ".jpg", ".jpeg", ".tif", ".tiff", ".gif", ".bmp")


def parse(invoice_id: str, sample: dict) -> ParsedDocument:
    source = sample.get("source", {})
    payload, location = _locate_payload(sample)
    fmt = _detect_format(source, location)

    if isinstance(payload, dict):
        text = json.dumps(payload)
        sections = [key for key in ("metadata", "line_items") if payload.get(key)]
    elif isinstance(payload, str):
        text = payload
        sections = []
    else:
        text = ""
        sections = []

    return ParsedDocument(
        invoice_id=invoice_id,
        source=source,
        text=text,
        sections=sections,
        format=fmt,
    )


def _locate_payload(sample: dict) -> tuple[object, str]:
    """Return ``(payload, location)`` for the invoice content.

    Prefers a parsed attachment (``document``) over an inline ``body``. The
    location drives format detection so evidence reads "PDF attachment" vs
    "email body".
    """
    if sample.get("document"):
        return sample["document"], "attachment"
    body = sample.get("body")
    if body is None:
        body = sample.get("source", {}).get("body")
    if body:
        return body, "body"
    return None, "none"


def _detect_format(source: dict, location: str) -> str:
    if location == "attachment":
        attachment = (source.get("attachment") or "").lower()
        if attachment.endswith(".pdf"):
            return "pdf"
        if attachment.endswith(_IMAGE_SUFFIXES):
            return "image"
        return "attachment"
    if location == "body":
        return "email_body"
    return "unknown"
