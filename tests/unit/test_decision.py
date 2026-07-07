"""Unit tests for the ledger decision policy (R7; ledger pivot).

Exercises the severity model (low/medium/high), weakest-link confidence over
extraction + income/expense + category, and the "low confidence → hold, never a
silent auto-file" rule — plus the adversarial-content block (R16).
"""

from decimal import Decimal

from backend.decision import decide
from backend.domain import (
    CategorizationResult,
    Decision,
    DocumentType,
    ExtractionResult,
    InvoiceMetadata,
    LineItem,
    Severity,
)


def _extraction(missing=None, field_conf=None, item_conf=0.9, total="120.00"):
    md = InvoiceMetadata(vendor_name="Acme", total_amount=total)
    li = LineItem(invoice_id="inv", raw_description="Office supplies", total=total,
                  extraction_confidence=item_conf)
    return ExtractionResult(
        metadata=md,
        line_items=[li],
        field_confidence=field_conf or {"vendor_name": 0.95, "total_amount": 0.95},
        missing_fields=missing or [],
    )


def _categorization(doc_type=DocumentType.EXPENSE, type_conf=0.9,
                    category="Office expense", cat_conf=0.9, adversarial=False):
    return CategorizationResult(
        invoice_id="inv",
        document_type=doc_type,
        document_type_confidence=type_conf,
        category=category,
        category_confidence=cat_conf,
        adversarial=adversarial,
    )


def _flag_types(result):
    return {f.type for f in result.risk_flags}


def test_clean_inputs_file():
    result = decide(_extraction(), _categorization())
    assert result.decision is Decision.SUBMIT
    assert result.required_human_actions == []
    assert 0 < result.confidence <= 1.0


def test_adversarial_content_forces_hold():
    result = decide(_extraction(), _categorization(adversarial=True))
    assert result.decision is Decision.HOLD
    assert "suspected_adversarial" in _flag_types(result)
    assert result.required_human_actions


def test_ambiguous_income_expense_holds():
    result = decide(_extraction(), _categorization(doc_type=DocumentType.UNKNOWN, type_conf=0.0))
    assert result.decision is Decision.HOLD
    assert "ambiguous_income_expense" in _flag_types(result)


def test_low_category_confidence_holds():
    result = decide(_extraction(), _categorization(category="Supplies", cat_conf=0.5))
    assert result.decision is Decision.HOLD
    assert "low_category_confidence" in _flag_types(result)


def test_missing_amount_holds():
    result = decide(
        _extraction(missing=["total_amount"], field_conf={"vendor_name": 0.95}),
        _categorization(),
    )
    assert result.decision is Decision.HOLD
    assert "missing_total" in _flag_types(result)


def test_missing_optional_field_is_low_severity_and_still_files():
    result = decide(
        _extraction(missing=["due_date"],
                    field_conf={"vendor_name": 0.95, "total_amount": 0.95}),
        _categorization(),
    )
    assert result.decision is Decision.SUBMIT
    low = [f for f in result.risk_flags if f.severity is Severity.LOW]
    assert any(f.type == "missing_optional_fields" for f in low)


def test_low_extraction_confidence_holds():
    result = decide(_extraction(item_conf=0.3), _categorization())
    assert result.decision is Decision.HOLD
    assert "low_extraction_confidence" in _flag_types(result)


def test_moderate_extraction_confidence_holds_below_floor():
    # The file floor is 0.8: a moderately-confident extraction (0.5-0.8) no longer
    # auto-files — it holds, so "posted" implies the AI was confident.
    result = decide(_extraction(item_conf=0.7), _categorization())
    assert result.decision is Decision.HOLD
    assert "moderate_extraction_confidence" in _flag_types(result)
    assert "low_confidence" in _flag_types(result)


def test_total_mismatch_holds():
    extraction = _extraction(total="120.00")
    extraction.metadata.total_amount = Decimal("999.00")
    result = decide(extraction, _categorization())
    assert result.decision is Decision.HOLD
    assert "total_mismatch" in _flag_types(result)


def test_file_confidence_is_weakest_link():
    # extraction min 0.9, type 0.95, category 0.85 → decision confidence 0.85
    result = decide(
        _extraction(item_conf=0.9),
        _categorization(type_conf=0.95, cat_conf=0.85),
    )
    assert result.decision is Decision.SUBMIT
    assert result.confidence == 0.85
