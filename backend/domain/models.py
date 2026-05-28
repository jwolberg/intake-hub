"""Shared domain types for the invoice pipeline.

One typed contract per stage output, matching the data model in PRD §11 and
ARCHITECTURE.md §12. Stages depend on these types, never on each other
(ARCHITECTURE.md §4 dependency rule).

All monetary values are modelled as ``Decimal`` to avoid float rounding on
amounts; timestamps are timezone-aware ``datetime``.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4

from pydantic import BaseModel, Field

from .enums import Actor, AuditAction, Decision, InvoiceStatus, Severity


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex[:12]}"


class BoundingBox(BaseModel):
    """A rectangle in normalized page coordinates — all four values in ``[0,1]``.

    Normalizing to the unit square (rather than pixels) means a box computed at
    OCR resolution overlays correctly at any display resolution: the reviewer's
    SVG overlay uses ``viewBox="0 0 1 1"`` and needs no scaling math (Visual
    Document Review spec §3).
    """

    x: float
    y: float
    width: float
    height: float


class WordBox(BaseModel):
    """One OCR word with its normalized location on a page (spec §3).

    ``index`` is the word's 0-based position within its ``page_number``. The
    vision extractor cites these indices (never raw coordinates), so highlights
    are anchored to real OCR geometry and cannot be hallucinated (P4-T3/T4).
    """

    page_number: int
    index: int
    text: str
    bbox: BoundingBox


class ParsedDocument(BaseModel):
    """Output of the parser stage: raw text + source metadata (PRD §7 Step 2).

    ``format`` records where the invoice content came from (PDF/image attachment
    or email body) so extraction can cite it as source evidence (PRD §7 Step 1,
    §10 detail view).
    """

    invoice_id: str
    source: dict = Field(default_factory=dict)
    text: str = ""
    sections: list[str] = Field(default_factory=list)
    format: str = "unknown"


class LineItem(BaseModel):
    """An extracted invoice line item (PRD §11 Line Item, §7 Step 3)."""

    id: str = Field(default_factory=lambda: _new_id("line"))
    invoice_id: str
    raw_description: str
    normalized_description: str | None = None
    quantity: Decimal | None = None
    unit_price: Decimal | None = None
    total: Decimal | None = None
    service_period: str | None = None
    raw_source_text: str | None = None
    extraction_confidence: float | None = None


class InvoiceMetadata(BaseModel):
    """Header-level fields extracted from an invoice (PRD §7 Step 3)."""

    invoice_number: str | None = None
    invoice_date: str | None = None
    due_date: str | None = None
    vendor_name: str | None = None
    sponsor_name: str | None = None
    study_name: str | None = None
    protocol_number: str | None = None
    site_identifier: str | None = None
    billing_period: str | None = None
    currency: str | None = None
    total_amount: Decimal | None = None
    tax: Decimal | None = None
    payment_terms: str | None = None


class ExtractionResult(BaseModel):
    """Output of the extraction stage (PRD FR2): header + line items.

    Confidence and source evidence are captured *per field*, not just per call,
    so the reviewer hub can highlight exactly which value is uncertain
    (ARCHITECTURE.md §8). ``missing_fields`` lists header fields the extractor
    could not populate, marking uncertainty explicitly (PRD FR2).
    """

    metadata: InvoiceMetadata = Field(default_factory=InvoiceMetadata)
    line_items: list[LineItem] = Field(default_factory=list)
    field_confidence: dict[str, float] = Field(default_factory=dict)
    field_evidence: dict[str, str] = Field(default_factory=dict)
    missing_fields: list[str] = Field(default_factory=list)


class ContextCandidate(BaseModel):
    """A ranked sponsor/study/site candidate (ARCHITECTURE.md §7).

    Carries the reference's *canonical* names alongside the ids so the context
    stage can detect invoice-vs-reference contradictions (PRD FR8, §15) and the
    hub can show the candidate conflict (PRD §10).
    """

    sponsor_id: str | None = None
    study_id: str | None = None
    site_id: str | None = None
    score: float
    sponsor_name: str | None = None
    study_name: str | None = None
    protocol_number: str | None = None
    site_name: str | None = None


class ResolvedContext(BaseModel):
    """Resolved business context (PRD §11 Resolved Context, FR3)."""

    invoice_id: str
    sponsor_id: str | None = None
    study_id: str | None = None
    site_id: str | None = None
    confidence: float = 0.0
    candidates: list[ContextCandidate] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class CatalogItem(BaseModel):
    """A sponsor+study-scoped billable catalog item (PRD FR4)."""

    id: str
    sponsor_id: str
    study_id: str
    description: str
    unit_price: Decimal | None = None


class MatchResult(BaseModel):
    """Result of matching one line item to the catalog (PRD §11, FR5)."""

    line_item_id: str
    catalog_item_id: str | None = None
    catalog_description: str | None = None
    confidence: float = 0.0
    amount_match: bool | None = None
    quantity_match: bool | None = None
    rationale: str | None = None
    requires_exception_review: bool = False
    alternates: list[str] = Field(default_factory=list)
    exceptions: list[str] = Field(default_factory=list)


class RiskFlag(BaseModel):
    """A risk flag attached to a decision (PRD §9)."""

    type: str
    severity: Severity
    message: str


class DecisionResult(BaseModel):
    """Structured AI decision (PRD §9, ARCHITECTURE.md §10)."""

    decision: Decision
    confidence: float = 0.0
    rationale: str = ""
    risk_flags: list[RiskFlag] = Field(default_factory=list)
    required_human_actions: list[str] = Field(default_factory=list)


class ExceptionRecord(BaseModel):
    """A typed exception for a held/failed invoice (PRD §11 Exception, FR8)."""

    id: str = Field(default_factory=lambda: _new_id("exc"))
    invoice_id: str
    type: str
    severity: Severity
    message: str
    created_at: datetime = Field(default_factory=_utcnow)


class AuditEvent(BaseModel):
    """An append-only audit event (PRD §11 Audit Event, §17).

    ``actor`` distinguishes AI/system work from human edits.
    """

    id: str = Field(default_factory=lambda: _new_id("evt"))
    invoice_id: str
    actor: Actor
    action: AuditAction
    details: dict = Field(default_factory=dict)
    timestamp: datetime = Field(default_factory=_utcnow)


class Invoice(BaseModel):
    """Top-level invoice workflow record (PRD §11 Invoice)."""

    id: str = Field(default_factory=lambda: _new_id("invoice"))
    source: str = "email"
    status: InvoiceStatus = InvoiceStatus.RECEIVED
    decision: Decision | None = None
    decision_confidence: float | None = None
    metadata: InvoiceMetadata = Field(default_factory=InvoiceMetadata)
    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)
