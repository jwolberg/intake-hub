"""Shared domain types and enums (the cross-stage contract)."""

from .enums import Actor, AuditAction, Decision, InvoiceStatus, Severity
from .models import (
    AuditEvent,
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
)

__all__ = [
    "Actor",
    "AuditAction",
    "AuditEvent",
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
]
