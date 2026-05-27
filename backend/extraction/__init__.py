"""Stage: extraction.

Extract invoice metadata + line items via the LLM client, validated into the
domain models before downstream use (PRD FR2, §7 Step 3; ARCHITECTURE.md §8).

The LLM returns a JSON object ``{"metadata": {...}, "line_items": [...]}``;
Pydantic validation is the schema gate — malformed fields raise rather than
flowing downstream. Baseline confidence is attached per line item (P2-A1
deepens per-field confidence + source evidence).
"""

from __future__ import annotations

from backend.clients.llm import LLMClient
from backend.domain import ExtractionResult, InvoiceMetadata, LineItem, ParsedDocument

EXTRACTION_SYSTEM = (
    "Extract clinical-trial invoice header metadata and line items as a JSON "
    "object with keys 'metadata' and 'line_items'."
)

_BASELINE_CONFIDENCE = 0.9


def extract(invoice_id: str, parsed: ParsedDocument, llm: LLMClient) -> ExtractionResult:
    raw = llm.complete_json(system=EXTRACTION_SYSTEM, user=parsed.text)
    metadata = InvoiceMetadata(**raw.get("metadata", {}))
    line_items = []
    for item in raw.get("line_items", []):
        fields = dict(item)
        fields.setdefault("extraction_confidence", _BASELINE_CONFIDENCE)
        line_items.append(LineItem(invoice_id=invoice_id, **fields))
    return ExtractionResult(metadata=metadata, line_items=line_items)
