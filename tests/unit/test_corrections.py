"""Unit tests for human correction overlays (P2-C3; ARCHITECTURE.md §11)."""

from decimal import Decimal

from backend.corrections import (
    apply_match_overlay,
    effective_metadata,
    match_overlay,
    metadata_overlay,
)
from backend.domain import (
    Actor,
    AuditAction,
    AuditEvent,
    InvoiceMetadata,
    MatchResult,
)


def _corrected(target, before, after, **extra):
    details = {"target": target, "before": before, "after": after, **extra}
    return AuditEvent(invoice_id="inv1", actor=Actor.HUMAN,
                      action=AuditAction.CORRECTED, details=details)


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


def test_match_overlay_and_apply_pins_human_choice():
    audit = [_corrected(
        "line_item",
        {"catalog_item_id": None},
        {"catalog_item_id": "cat_9", "catalog_description": "Mapped"},
        line_item_id="line1",
    )]
    assert match_overlay(audit) == {
        "line1": {"catalog_item_id": "cat_9", "catalog_description": "Mapped"}
    }

    matches = [
        MatchResult(line_item_id="line1", confidence=0.0, exceptions=["unmatched_line_item"],
                    requires_exception_review=True),
        MatchResult(line_item_id="line2", catalog_item_id="cat_2", confidence=0.9),
    ]
    applied = {m.line_item_id: m for m in apply_match_overlay(matches, audit)}
    # the corrected line is pinned to the human's choice, confident and clean
    assert applied["line1"].catalog_item_id == "cat_9"
    assert applied["line1"].confidence == 1.0
    assert applied["line1"].exceptions == []
    assert applied["line1"].requires_exception_review is False
    # an untouched line is left exactly as the matcher produced it
    assert applied["line2"].catalog_item_id == "cat_2"
