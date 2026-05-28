"""Enumerations shared across pipeline stages.

These mirror the workflow state machine (ARCHITECTURE.md §6) and the decision /
risk vocabulary (ARCHITECTURE.md §10, PRD §9). Keeping them in one place stops
each stage from inventing its own string literals.
"""

from __future__ import annotations

from enum import Enum


class InvoiceStatus(str, Enum):
    """Workflow state of an invoice (ARCHITECTURE.md §6)."""

    RECEIVED = "received"
    PARSED = "parsed"
    EXTRACTED = "extracted"
    CONTEXT_RESOLVED = "context_resolved"
    CATALOG_MATCHED = "catalog_matched"
    SUBMITTED = "submitted"
    HELD = "held"
    FAILED = "failed"
    RERUN_REQUESTED = "rerun_requested"
    CORRECTED = "corrected"
    ESCALATED = "escalated"


class Decision(str, Enum):
    """Terminal AI decision (PRD FR6)."""

    SUBMIT = "submit"
    HOLD = "hold"


class Severity(str, Enum):
    """Risk-flag / exception severity (PRD §16).

    LOW is informational, MEDIUM requires visibility, HIGH blocks submission.
    """

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class CitationStatus(str, Enum):
    """Status of a source-anchored citation (Visual Document Review spec §3-§4).

    ``extracted`` = value confidently anchored to OCR words; ``uncertain`` =
    anchored but low confidence (gates a clean review); ``unreadable`` = a value
    was extracted but no OCR words back it (so no highlight box); ``missing`` =
    no value was extracted for the field.
    """

    EXTRACTED = "extracted"
    UNCERTAIN = "uncertain"
    UNREADABLE = "unreadable"
    MISSING = "missing"


class Actor(str, Enum):
    """Who produced an audit event (PRD §17)."""

    AI = "ai"
    SYSTEM = "system"
    HUMAN = "human"


class AuditAction(str, Enum):
    """Auditable actions (PRD §11 audit event, §17)."""

    RECEIVED = "received"
    PARSED = "parsed"
    EXTRACTED = "extracted"
    CONTEXT_RESOLVED = "context_resolved"
    CATALOG_MATCHED = "catalog_matched"
    MATCHED = "matched"
    SUBMITTED = "submitted"
    HELD = "held"
    FAILED = "failed"
    REVIEWED = "reviewed"
    CORRECTED = "corrected"
    RERUN = "rerun"
    ESCALATED = "escalated"
    NOTE = "note"
