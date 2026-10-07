"""Unit tests for human correction overlays (P2-C3; ARCHITECTURE.md §11)."""

from decimal import Decimal

from backend.corrections import (
    effective_metadata,
    metadata_overlay,
)
from backend.domain import (
    Actor,
    AuditAction,
    AuditEvent,
    InvoiceMetadata,
)


def _corrected(target, before, after, **extra):
    details = {"target": target, "before": before, "after": after, **extra}
    return AuditEvent(
        invoice_id="inv1", actor=Actor.HUMAN, action=AuditAction.CORRECTED, details=details
    )


def test_metadata_overlay_latest_wins():
    audit = [
        _corrected("metadata", {"vendor_name": "A"}, {"vendor_name": "B"}),
        _corrected("metadata", {"vendor_name": "B"}, {"vendor_name": "C"}),
    ]
    assert metadata_overlay(audit) == {"vendor_name": "C"}


def test_effective_metadata_applies_overlay_and_coerces_types():
    meta = InvoiceMetadata(vendor_name="A")  # total missing
    audit = [_corrected("metadata", {"total_amount": None}, {"total_amount": "1320.00"})]

    effective = effective_metadata(meta, audit)
    # the human's string is coerced to Decimal so downstream stages can use it
    assert effective.total_amount == Decimal("1320.00")
    assert effective.vendor_name == "A"  # untouched fields preserved


def test_effective_metadata_no_overlay_returns_original():
    meta = InvoiceMetadata(vendor_name="A")
    assert effective_metadata(meta, []) is meta
