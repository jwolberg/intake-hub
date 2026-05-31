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
from backend.clients.vision import VISION_SYSTEM, VisionLLMClient
from backend.domain import (
    Citation,
    CitationStatus,
    ExtractionResult,
    InvoiceMetadata,
    LineItem,
    ParsedDocument,
    WordBox,
)
from backend.extraction.citations import UNCERTAIN_BELOW

# The exact metadata keys the schema expects, derived from the domain model so
# the prompt can never drift from it. Pinning these in the prompt is essential:
# without them the model invents synonyms from the document labels (e.g. "vendor"
# instead of "vendor_name"), which _build_metadata then drops as missing.
_METADATA_FIELDS = ", ".join(InvoiceMetadata.model_fields)

EXTRACTION_SYSTEM = (
    "Extract clinical-trial invoice header metadata and line items from the "
    "document text into a JSON object with keys 'metadata' and 'line_items'. "
    "The 'metadata' object MUST use exactly these field keys, verbatim — do not "
    "rename, abbreviate, or substitute synonyms: " + _METADATA_FIELDS + ". "
    "For each metadata field return an object "
    '{"value": <string or null>, "confidence": <number 0..1>, '
    '"evidence": <short snippet of the source text you read it from>}; '
    "use null/0 when the field is absent. 'confidence' must reflect how certain "
    "you are that you read the value correctly from this document — lower it for "
    "smudged, ambiguous, or inferred values. Each line item is an object with "
    "raw_description, quantity, unit_price, total, raw_source_text, and "
    "extraction_confidence (number 0..1)."
)

_VALID_STATUS = {status.value for status in CitationStatus}

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
    metadata, field_confidence, field_evidence, missing_fields = _build_metadata(
        metadata_raw, parsed
    )
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


def extract_vision(
    invoice_id: str,
    parsed: ParsedDocument,
    words: list[WordBox],
    image: bytes,
    vision: VisionLLMClient,
) -> ExtractionResult:
    """Vision extraction (P4-T3): same result as ``extract`` plus source-anchored
    ``citations``.

    The vision client returns each value annotated with the OCR ``word_indices``
    that back it; here we validate those annotations into ``Citation`` objects
    (``bbox`` left unresolved for P4-T4). Header values, confidence, and evidence
    are derived exactly as the text path, so downstream stages are unaffected.
    """
    raw = vision.complete_vision_json(
        system=VISION_SYSTEM, user=parsed.text, image=image, words=words
    )
    metadata_raw = raw.get("metadata", {})
    metadata, field_confidence, field_evidence, missing_fields = _build_metadata(
        metadata_raw, parsed
    )
    line_raw = raw.get("line_items", [])
    line_items = [_read_line_item(invoice_id, item) for item in line_raw]
    citations = _build_citations(metadata_raw, line_items, line_raw, field_confidence)
    return ExtractionResult(
        metadata=metadata,
        line_items=line_items,
        field_confidence=field_confidence,
        field_evidence=field_evidence,
        missing_fields=missing_fields,
        citations=citations,
    )


def _build_metadata(
    metadata_raw: dict, parsed: ParsedDocument
) -> tuple[InvoiceMetadata, dict[str, float], dict[str, str], list[str]]:
    """Resolve header fields to a typed metadata model + per-field signals.

    Shared by the text and vision paths so both derive value/confidence/evidence
    and explicit ``missing_fields`` identically (PRD FR2; ARCHITECTURE.md §8).
    """
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

    return InvoiceMetadata(**values), field_confidence, field_evidence, missing_fields


def _build_citations(
    metadata_raw: dict,
    line_items: list[LineItem],
    line_raw: list[dict],
    field_confidence: dict[str, float],
) -> list[Citation]:
    """Turn the vision client's word-index annotations into ``Citation`` objects."""
    citations: list[Citation] = []
    for field in InvoiceMetadata.model_fields:
        citation = _citation_from_cell(
            f"metadata.{field}",
            metadata_raw.get(field),
            field_confidence.get(field, 0.0),
        )
        if citation is not None:
            citations.append(citation)

    for line_item, item in zip(line_items, line_raw, strict=True):
        cell = item.get("description_citation") if isinstance(item, dict) else None
        citation = _citation_from_cell(
            f"line_item.{line_item.id}.raw_description",
            cell,
            line_item.extraction_confidence or 0.0,
            value_override=line_item.raw_description,
        )
        if citation is not None:
            citations.append(citation)
    return citations


def _citation_from_cell(
    target_id: str,
    cell: object,
    confidence: float,
    *,
    value_override: object | None = None,
) -> Citation | None:
    """Validate one vision annotation into a ``Citation`` (or ``None`` if absent).

    Raises ``ValueError`` on malformed indices/status — invalid model output is a
    failure, never silently passed downstream (ARCHITECTURE.md §8).
    """
    if not isinstance(cell, dict):
        return None
    if "word_indices" not in cell and "status" not in cell:
        return None  # not a vision-annotated cell (e.g. plain text-path field)

    value = value_override if value_override is not None else cell.get("value")
    if value in (None, ""):
        return None

    raw_indices = cell.get("word_indices") or []
    try:
        indices = [int(i) for i in raw_indices]
    except (TypeError, ValueError) as exc:
        raise ValueError(f"citation word_indices must be ints: {raw_indices!r}") from exc

    raw_status = cell.get("status")
    if raw_status is not None and raw_status not in _VALID_STATUS:
        raise ValueError(f"invalid citation status: {raw_status!r}")

    return Citation(
        page_number=int(cell.get("page", 1)),
        target_id=target_id,
        quote=str(value),
        word_indices=indices,
        status=_refine_status(raw_status, indices, confidence),
    )


def _refine_status(raw_status: str | None, indices: list[int], confidence: float) -> CitationStatus:
    """Apply the confidence policy on top of the client's anchoring status.

    No backing words → ``unreadable`` (no box). Anchored but low confidence →
    ``uncertain`` (gates a clean review). Otherwise the client's status stands.
    """
    if raw_status == CitationStatus.MISSING.value:
        return CitationStatus.MISSING
    if not indices or raw_status == CitationStatus.UNREADABLE.value:
        return CitationStatus.UNREADABLE
    if confidence < UNCERTAIN_BELOW:
        return CitationStatus.UNCERTAIN
    return CitationStatus.EXTRACTED


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
