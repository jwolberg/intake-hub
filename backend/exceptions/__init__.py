"""Stage: exceptions.

Turn hold reasons into typed exception records drawn from the canonical taxonomy
(PRD FR8; ARCHITECTURE.md §12). Each record carries a stable ``type``, a severity,
and a specific message so the reviewer can see exactly why an invoice was held.

``from_decision`` maps a hold decision's reportable (medium/high) risk flags to
records; ``build`` is the shared factory used both here and by the orchestrator's
failure paths, so every exception in the system comes from one taxonomy.
"""

from __future__ import annotations

from backend.domain import DecisionResult, ExceptionRecord, Severity
from backend.domain.taxonomy import severity_of, title_of

_REPORTABLE = {Severity.MEDIUM, Severity.HIGH}


def build(
    invoice_id: str,
    code: str,
    *,
    message: str | None = None,
    severity: Severity | None = None,
) -> ExceptionRecord:
    """Create an ``ExceptionRecord`` for a taxonomy code, defaulting severity and
    message from the registry when not supplied."""
    return ExceptionRecord(
        invoice_id=invoice_id,
        type=code,
        severity=severity or severity_of(code),
        message=message or title_of(code),
    )


def from_decision(invoice_id: str, decision: DecisionResult) -> list[ExceptionRecord]:
    """Build exception records for the medium/high-severity flags on a decision.

    Low-severity flags are informational (PRD §16) and create no exception.
    """
    return [
        build(invoice_id, flag.type, message=flag.message, severity=flag.severity)
        for flag in decision.risk_flags
        if flag.severity in _REPORTABLE
    ]
