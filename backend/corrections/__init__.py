"""Human correction overlays (PRD FR10; ARCHITECTURE.md §11).

Corrections never mutate the AI's original output in place. Each one is recorded
as a ``corrected`` audit event (actor ``human``) carrying the field(s) changed
with ``before``/``after`` values, so the trail always shows what the AI produced
versus what a human changed (ARCHITECTURE.md §11, PRD §17).

The *effective* value of a field is the AI value with the latest human override
applied on top. These helpers replay the append-only audit trail to derive that
overlay — there is no separate correction store to keep in sync. The overlay is
applied for the detail view and consumed by rerun as fixed inputs (P2-C4).

Event shapes (in ``AuditEvent.details``):
- metadata:  ``{"target": "metadata", "before": {field: old}, "after": {field: new}}``
- line item: ``{"target": "line_item", "line_item_id": id,
                 "before": {...}, "after": {"catalog_item_id", "catalog_description"}}``
"""

from __future__ import annotations

from backend.domain import Actor, AuditAction, AuditEvent, InvoiceMetadata, MatchResult


def _is_correction(event: AuditEvent, target: str) -> bool:
    return (
        event.actor is Actor.HUMAN
        and event.action is AuditAction.CORRECTED
        and event.details.get("target") == target
    )


def metadata_overlay(audit: list[AuditEvent]) -> dict[str, object]:
    """Latest human override per metadata field (field → new value)."""
    overlay: dict[str, object] = {}
    for event in audit:  # chronological order → latest correction wins
        if _is_correction(event, "metadata"):
            overlay.update(event.details.get("after", {}))
    return overlay


def category_overlay(audit: list[AuditEvent]) -> str | None:
    """Latest human Schedule C category override (or ``None``).

    A ledger category correction is recorded as
    ``{"target": "category", "after": {"category": <name>}}``; the pipeline tail
    pins the latest one as a fixed input on rerun (R10/AE2), and the AI's original
    category is preserved on the CATEGORIZED audit event.
    """
    latest: str | None = None
    for event in audit:  # chronological → latest wins
        if _is_correction(event, "category"):
            value = event.details.get("after", {}).get("category")
            if value:
                latest = value
    return latest


def match_overlay(audit: list[AuditEvent]) -> dict[str, dict]:
    """Latest human match override per line item (line_item_id → {id, description})."""
    overlay: dict[str, dict] = {}
    for event in audit:
        if _is_correction(event, "line_item"):
            line_item_id = event.details.get("line_item_id")
            if line_item_id:
                overlay[line_item_id] = event.details.get("after", {})
    return overlay


def effective_metadata(metadata: InvoiceMetadata, audit: list[AuditEvent]) -> InvoiceMetadata:
    """AI metadata with human overrides applied, re-validated to coerce types."""
    overlay = {k: v for k, v in metadata_overlay(audit).items() if v not in (None, "")}
    if not overlay:
        return metadata
    merged = {**metadata.model_dump(), **overlay}
    return InvoiceMetadata(**merged)


def apply_match_overlay(
    matches: list[MatchResult], audit: list[AuditEvent]
) -> list[MatchResult]:
    """Overlay human match corrections onto freshly computed matches.

    A corrected line keeps the human's chosen catalog item as a fixed input;
    its rationale records that it was set by a reviewer.
    """
    overlay = match_overlay(audit)
    if not overlay:
        return matches
    result: list[MatchResult] = []
    for match in matches:
        override = overlay.get(match.line_item_id)
        if override is None:
            result.append(match)
            continue
        result.append(
            match.model_copy(
                update={
                    "catalog_item_id": override.get("catalog_item_id"),
                    "catalog_description": override.get("catalog_description"),
                    "confidence": 1.0,  # human-confirmed
                    "requires_exception_review": False,
                    "exceptions": [],
                    "rationale": "corrected by reviewer",
                }
            )
        )
    return result
