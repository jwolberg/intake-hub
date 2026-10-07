"""Shared domain types and enums (the cross-stage contract)."""

from .enums import (
    Actor,
    AuditAction,
    CitationStatus,
    Decision,
    DocumentType,
    InvoiceStatus,
    Severity,
)
from .models import (
    AuditEvent,
    BoundingBox,
    CategorizationResult,
    Citation,
    DecisionResult,
    ExceptionRecord,
    ExtractionResult,
    Invoice,
    InvoiceMetadata,
    LineItem,
    ParsedDocument,
    RiskFlag,
    WordBox,
)

__all__ = [
    "Actor",
    "AuditAction",
    "AuditEvent",
    "BoundingBox",
    "CategorizationResult",
    "Citation",
    "CitationStatus",
    "Decision",
    "DecisionResult",
    "DocumentType",
    "ExceptionRecord",
    "ExtractionResult",
    "Invoice",
    "InvoiceMetadata",
    "InvoiceStatus",
    "LineItem",
    "ParsedDocument",
    "RiskFlag",
    "Severity",
    "WordBox",
]
