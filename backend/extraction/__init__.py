"""Stage: extraction.

Extract invoice metadata + line items via the LLM client, validated into the
domain models before downstream use (PRD FR2, §7 Step 3; ARCHITECTURE.md §8).

The LLM returns a JSON object ``{"metadata": {...}, "line_items": [...]}``;
Pydantic validation is the schema gate — malformed fields raise rather than
flowing downstream (ARCHITECTURE.md §8: invalid output is a failure, never
silently passed on).

Per ARCHITECTURE.md §8, confidence is captured *per field/item* (not just per
call) so the hub can highlight exactly which value is uncertain. Each header
field may arrive flat (``"vendor_name": "Acme"``) or annotated
(``{"value": "Acme", "confidence": 0.8, "evidence": "..."}``); the annotated
shape lets a real provider supply its own confidence, while the offline path
derives it from whether the value is corroborated in the parsed source text.
Fields the extractor could not populate are listed in ``missing_fields`` —
explicit uncertainty marking (PRD FR2).
"""

from __future__ import annotations

import json

from backend.clients.llm import LLMClient
from backend.domain import ExtractionResult, InvoiceMetadata, LineItem, ParsedDocument

EXTRACTION_SYSTEM = (
    "Extract clinical-trial invoice header metadata and line items as a JSON "
    "object with keys 'metadata' and 'line_items'."
)

# Confidence for derived (un-annotated) values: corroborated in source text vs
# merely present. A complete line item vs one missing amount/description.
_CONFIDENCE_CORROBORATED = 0.95
_CONFIDENCE_PRESENT = 0.9
_CONFIDENCE_LINE_COMPLETE = 0.9
_CONFIDENCE_LINE_PARTIAL = 0.6

_FORMAT_LABELS = {
    "pdf": "PDF attachment",
    "image": "scanned image",
    "attachment": "attachment",
    "email_body": "email body",
}


def extract(invoice_id: str, parsed: ParsedDocument, llm: LLMClient) -> ExtractionResult:
    raw = llm.complete_json(system=EXTRACTION_SYSTEM, user=parsed.text)
    metadata_raw = raw.get("metadata", {})

    values: dict[str, object] = {}
    field_confidence: dict[str, float] = {}
    field_evidence: dict[str, str] = {}
    missing_fields: list[str] = []

    for field in InvoiceMetadata.model_fields:
        value, confidence, evidence = _read_field(metadata_raw.get(field), parsed)
        if value is None:
            missing_fields.append(field)
            field_confidence[field] = 0.0
            continue
        values[field] = value
        field_confidence[field] = confidence
        if evidence:
            field_evidence[field] = evidence

    metadata = InvoiceMetadata(**values)
    line_items = [
        _read_line_item(invoice_id, item) for item in raw.get("line_items", [])
    ]
    return ExtractionResult(
        metadata=metadata,
        line_items=line_items,
        field_confidence=field_confidence,
        field_evidence=field_evidence,
        missing_fields=missing_fields,
    )


def _read_field(cell: object, parsed: ParsedDocument) -> tuple[object, float, str | None]:
    """Resolve one header field to ``(value, confidence, evidence)``.

    ``value`` is ``None`` when missing/empty (caller marks it uncertain).
    """
    if isinstance(cell, dict) and "value" in cell:
        value = cell.get("value")
        confidence = cell.get("confidence")
        evidence = cell.get("evidence")
    else:
        value, confidence, evidence = cell, None, None

    if value is None or value == "":
        return None, 0.0, None

    if confidence is None:
        corroborated = str(value) in parsed.text
        confidence = _CONFIDENCE_CORROBORATED if corroborated else _CONFIDENCE_PRESENT
    if evidence is None:
        evidence = _evidence(parsed.format, value)
    return value, float(confidence), evidence


def _read_line_item(invoice_id: str, item: dict) -> LineItem:
    fields = dict(item)
    raw_source = fields.get("raw_source_text") or json.dumps(item, sort_keys=True)
    confidence = fields.get("extraction_confidence")
    if confidence is None:
        complete = bool(item.get("raw_description")) and item.get("total") not in (None, "")
        confidence = _CONFIDENCE_LINE_COMPLETE if complete else _CONFIDENCE_LINE_PARTIAL
    fields["extraction_confidence"] = confidence
    fields["raw_source_text"] = raw_source
    return LineItem(invoice_id=invoice_id, **fields)


def _evidence(fmt: str, value: object) -> str:
    where = _FORMAT_LABELS.get(fmt, "source document")
    return f'{where}: "{value}"'
