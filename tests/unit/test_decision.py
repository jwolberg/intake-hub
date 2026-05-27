"""Unit tests for the decision policy (P2-B1; PRD FR6, §16, §18).

Exercises the severity model (low/medium/high), confidence consumption, and the
"low confidence → hold, never silent submit" rule, against the §16 policy table.
"""

from decimal import Decimal

from backend.decision import decide
from backend.domain import (
    Decision,
    ExtractionResult,
    InvoiceMetadata,
    LineItem,
    MatchResult,
    ResolvedContext,
    Severity,
)


def _extraction(missing=None, field_conf=None, item_conf=0.9, total="120.00"):
    md = InvoiceMetadata(invoice_number="INV-1", total_amount=total)
    li = LineItem(invoice_id="inv", raw_description="ECG", total=total,
                  extraction_confidence=item_conf)
    return ExtractionResult(
        metadata=md,
        line_items=[li],
        field_confidence=field_conf or {"invoice_number": 0.95, "total_amount": 0.95},
        missing_fields=missing or [],
    )


def _resolved_ctx(confidence=1.0, warnings=None):
    return ResolvedContext(invoice_id="inv", sponsor_id="s", study_id="st",
                           site_id="si", confidence=confidence, warnings=warnings or [])


def _good_match(conf=1.0):
    return MatchResult(line_item_id="line", catalog_item_id="cat",
                       catalog_description="ECG", confidence=conf,
                       amount_match=True, quantity_match=True)


def _flag_types(result):
    return {f.type for f in result.risk_flags}


def test_clean_inputs_submit():
    result = decide(_extraction(), _resolved_ctx(), [_good_match()], catalog_available=True)
    assert result.decision is Decision.SUBMIT
    assert result.required_human_actions == []
    assert 0 < result.confidence <= 1.0


def test_high_severity_flag_forces_hold():
    result = decide(_extraction(), _resolved_ctx(),
                    [MatchResult(line_item_id="line", catalog_item_id=None, confidence=0.0)],
                    catalog_available=True)
    assert result.decision is Decision.HOLD
    assert "unmatched_line_item" in _flag_types(result)
    assert result.required_human_actions  # at least one action surfaced


def test_missing_critical_field_holds():
    result = decide(_extraction(missing=["total_amount"], field_conf={"invoice_number": 0.95}),
                    _resolved_ctx(), [_good_match()], catalog_available=True)
    assert result.decision is Decision.HOLD
    assert "missing_total" in _flag_types(result)


def test_missing_optional_field_is_low_severity_and_still_submits():
    result = decide(
        _extraction(missing=["due_date"],
                    field_conf={"invoice_number": 0.95, "total_amount": 0.95}),
        _resolved_ctx(), [_good_match()], catalog_available=True,
    )
    assert result.decision is Decision.SUBMIT
    low = [f for f in result.risk_flags if f.severity is Severity.LOW]
    assert any(f.type == "missing_optional_fields" for f in low)


def test_low_extraction_confidence_holds():
    result = decide(_extraction(item_conf=0.3), _resolved_ctx(),
                    [_good_match()], catalog_available=True)
    assert result.decision is Decision.HOLD
    assert "low_extraction_confidence" in _flag_types(result)


def test_moderate_extraction_confidence_is_medium_and_submits():
    result = decide(_extraction(item_conf=0.7), _resolved_ctx(),
                    [_good_match()], catalog_available=True)
    assert result.decision is Decision.SUBMIT  # medium is visibility-only
    medium = [f for f in result.risk_flags if f.severity is Severity.MEDIUM]
    assert any(f.type == "moderate_extraction_confidence" for f in medium)


def test_weak_match_is_medium_and_submits():
    result = decide(_extraction(), _resolved_ctx(), [_good_match(conf=0.8)],
                    catalog_available=True)
    assert result.decision is Decision.SUBMIT
    assert any(f.type == "weak_match" and f.severity is Severity.MEDIUM
               for f in result.risk_flags)


def test_catalog_unavailable_holds():
    result = decide(_extraction(), _resolved_ctx(), [], catalog_available=False)
    assert result.decision is Decision.HOLD
    assert "catalog_unavailable" in _flag_types(result)


def test_total_mismatch_holds():
    extraction = _extraction(total="120.00")
    # invoice claims a different total than the single 120.00 line
    extraction.metadata.total_amount = Decimal("999.00")
    result = decide(extraction, _resolved_ctx(), [_good_match()], catalog_available=True)
    assert result.decision is Decision.HOLD
    assert "total_mismatch" in _flag_types(result)


def test_submit_confidence_is_weakest_link():
    # context 1.0, extraction min 0.9, match 0.9 → decision confidence 0.9
    result = decide(_extraction(item_conf=0.9), _resolved_ctx(confidence=1.0),
                    [_good_match(conf=0.9)], catalog_available=True)
    assert result.decision is Decision.SUBMIT
    assert result.confidence == 0.9
