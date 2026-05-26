"""Shared domain types and enums (the cross-stage contract)."""

from .enums import Actor, AuditAction, Decision, InvoiceStatus, Severity
from .models import (
    AuditEvent,
    CatalogItem,
    ContextCandidate,
    DecisionResult,
    ExceptionRecord,
    Invoice,
    InvoiceMetadata,
    LineItem,
    MatchResult,
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
    "Invoice",
    "InvoiceMetadata",
    "InvoiceStatus",
    "LineItem",
    "MatchResult",
    "ResolvedContext",
    "RiskFlag",
    "Severity",
]
