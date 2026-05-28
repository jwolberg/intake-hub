"""Shared domain types and enums (the cross-stage contract)."""

from .enums import Actor, AuditAction, Decision, InvoiceStatus, Severity
from .models import (
    AuditEvent,
    BoundingBox,
    CatalogItem,
    ContextCandidate,
    DecisionResult,
    ExceptionRecord,
    ExtractionResult,
    Invoice,
    InvoiceMetadata,
    LineItem,
    MatchResult,
    ParsedDocument,
    ResolvedContext,
    RiskFlag,
    WordBox,
)

__all__ = [
    "Actor",
    "AuditAction",
    "AuditEvent",
    "BoundingBox",
    "CatalogItem",
    "ContextCandidate",
    "Decision",
    "DecisionResult",
    "ExceptionRecord",
    "ExtractionResult",
    "Invoice",
    "InvoiceMetadata",
    "InvoiceStatus",
    "LineItem",
    "MatchResult",
    "ParsedDocument",
    "ResolvedContext",
    "RiskFlag",
    "Severity",
    "WordBox",
]
