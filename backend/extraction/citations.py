"""Citation synthesis + bounding-box resolution (P4-T4; spec §3-§4).

Two steps, mirroring the reference word-index pipeline:

- ``synthesize_citations`` is the **offline analogue** of a vision model emitting
  word indices (spec §7): it string-matches each extracted value against the OCR
  words via ``locate_value``, producing citations that carry ``word_indices`` (no
  ``bbox`` yet). This derives from the *extraction result*, so it works whichever
  offline LLM stand-in produced the values (Passthrough JSON or PDF layout).
- ``resolve_citations`` is the server-side **resolver**: it unions each citation's
  cited word boxes into a single ``bbox`` (dropping out-of-range indices), so a
  value with no backing OCR words yields no box — the no-hallucination guarantee
  (spec §3). A real vision provider (OD-7) skips synthesis and feeds its emitted
  citations straight into ``resolve_citations``.
"""

from __future__ import annotations

from backend.clients.vision import locate_value
from backend.domain import (
    BoundingBox,
    Citation,
    CitationStatus,
    ExtractionResult,
    InvoiceMetadata,
    WordBox,
)

# An anchored value below this confidence is surfaced as uncertain so the reviewer
# must confirm it before a clean review (Visual Document Review spec §4).
UNCERTAIN_BELOW = 0.7


def synthesize_citations(
    extraction: ExtractionResult, words: list[WordBox]
) -> list[Citation]:
    """Build word-index citations for an extraction by matching values to OCR words.

    One citation per populated header field (``metadata.<field>``) and per line
    item (``line_item.<id>.raw_description``). ``bbox`` is left unresolved — call
    ``resolve_citations`` next.
    """
    citations: list[Citation] = []
    for field in InvoiceMetadata.model_fields:
        value = getattr(extraction.metadata, field, None)
        citation = _synthesize(
            f"metadata.{field}", value, extraction.field_confidence.get(field, 0.0), words
        )
        if citation is not None:
            citations.append(citation)

    for line_item in extraction.line_items:
        citation = _synthesize(
            f"line_item.{line_item.id}.raw_description",
            line_item.raw_description,
            line_item.extraction_confidence or 0.0,
            words,
        )
        if citation is not None:
            citations.append(citation)
    return citations


def _synthesize(
    target_id: str, value: object, confidence: float, words: list[WordBox]
) -> Citation | None:
    if value in (None, ""):
        return None
    located = locate_value(value, words)
    if located is None:
        # value extracted but no OCR words back it → no box (spec §3)
        return Citation(
            page_number=1,
            target_id=target_id,
            quote=str(value),
            word_indices=[],
            status=CitationStatus.UNREADABLE,
        )
    page, indices = located
    status = (
        CitationStatus.UNCERTAIN if confidence < UNCERTAIN_BELOW else CitationStatus.EXTRACTED
    )
    return Citation(
        page_number=page,
        target_id=target_id,
        quote=str(value),
        word_indices=indices,
        status=status,
    )


def resolve_citations(citations: list[Citation], words: list[WordBox]) -> list[Citation]:
    """Resolve each citation's ``word_indices`` to a union ``bbox`` from OCR geometry.

    Out-of-range indices are dropped defensively; a citation that resolves to no
    valid words keeps ``bbox = None`` (no highlight) — the model can only point at
    words that actually exist (spec §3).
    """
    by_page: dict[int, dict[int, WordBox]] = {}
    for word in words:
        by_page.setdefault(word.page_number, {})[word.index] = word

    resolved: list[Citation] = []
    for citation in citations:
        page_words = by_page.get(citation.page_number, {})
        boxes = [page_words[i].bbox for i in citation.word_indices if i in page_words]
        bbox = _union(boxes) if boxes else None
        resolved.append(citation.model_copy(update={"bbox": bbox}))
    return resolved


def _union(boxes: list[BoundingBox]) -> BoundingBox:
    """Smallest box enclosing all ``boxes`` (all already in normalized [0,1])."""
    x0 = min(b.x for b in boxes)
    y0 = min(b.y for b in boxes)
    x1 = max(b.x + b.width for b in boxes)
    y1 = max(b.y + b.height for b in boxes)
    return BoundingBox(x=x0, y=y0, width=x1 - x0, height=y1 - y0)
