"""Unit tests for the shared domain types (P0-T3).

These pin the cross-stage contract: default workflow state, id prefixes, decimal
amounts, and the structured-decision shape (PRD §11; ARCHITECTURE.md §12).
"""

from decimal import Decimal

from backend.domain import (
    Decision,
    DecisionResult,
    Invoice,
    InvoiceStatus,
    LineItem,
    RiskFlag,
    Severity,
)


def test_invoice_defaults():
    inv = Invoice()
    assert inv.status is InvoiceStatus.RECEIVED
    assert inv.decision is None
    assert inv.id.startswith("invoice_")
    # metadata is always present so downstream stages never None-check the header
    assert inv.metadata.invoice_number is None


def test_line_item_keeps_decimal_amounts():
    item = LineItem(invoice_id="invoice_1", raw_description="Screening visit",
                    quantity=Decimal("2"), unit_price=Decimal("300.00"),
                    total=Decimal("600.00"))
    assert item.total == Decimal("600.00")
    assert item.id.startswith("line_")


def test_decision_result_structure():
    result = DecisionResult(
        decision=Decision.HOLD,
        confidence=0.42,
        rationale="Two possible sites matched the invoice metadata.",
        risk_flags=[
            RiskFlag(type="context_ambiguity", severity=Severity.MEDIUM,
                     message="Two possible sites matched."),
        ],
        required_human_actions=["Confirm correct site before submission"],
    )
    assert result.decision is Decision.HOLD
    assert result.risk_flags[0].severity is Severity.MEDIUM
    assert result.required_human_actions == ["Confirm correct site before submission"]


def test_status_values_cover_state_machine():
    # Guards against accidental drift from ARCHITECTURE.md §6 states. The ledger
    # states (classified/categorized/posted) coexist with the clinical-trial
    # states until U5 removes the latter.
    expected = {
        "received", "parsed", "extracted",
        "classified", "categorized", "posted",
        "context_resolved", "catalog_matched",
        "submitted", "held", "failed", "rerun_requested", "corrected", "escalated",
        "rejected",
    }
    assert {s.value for s in InvoiceStatus} == expected
