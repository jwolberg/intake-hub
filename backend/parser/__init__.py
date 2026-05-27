"""Stage: parser.

Extract raw text + structure from an invoice (PRD §7 Step 2; ARCHITECTURE.md §4).

MVP: the sample's ``document`` block stands in for OCR/text output (PRD §4
non-goal: no real document intelligence). We serialise it to JSON so the offline
extraction stand-in can read it; a real parser would emit the invoice's natural
text here and nothing downstream changes.
"""

from __future__ import annotations

import json

from backend.domain import ParsedDocument


def parse(invoice_id: str, sample: dict) -> ParsedDocument:
    document = sample.get("document", {})
    source = sample.get("source", {})
    return ParsedDocument(
        invoice_id=invoice_id,
        source=source,
        text=json.dumps(document),
    )
