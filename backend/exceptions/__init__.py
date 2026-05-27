"""Stage: exceptions.

Turn a hold decision's risk flags into typed exception records (PRD FR8;
ARCHITECTURE.md §12). Each carries type + severity + a specific message so the
reviewer can see exactly why the invoice was held. The full hold-reason taxonomy
is expanded in P2-B2.
"""

from __future__ import annotations

from backend.domain import DecisionResult, ExceptionRecord, Severity

_REPORTABLE = {Severity.MEDIUM, Severity.HIGH}


def from_decision(invoice_id: str, decision: DecisionResult) -> list[ExceptionRecord]:
    """Build exception records for the medium/high-severity flags on a decision."""
    return [
        ExceptionRecord(
            invoice_id=invoice_id,
            type=flag.type,
            severity=flag.severity,
            message=flag.message,
        )
        for flag in decision.risk_flags
        if flag.severity in _REPORTABLE
    ]
