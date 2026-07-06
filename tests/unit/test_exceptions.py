"""Unit tests for the exception taxonomy + factory (P2-B2; PRD FR8, §16).

Guarantees every FR8 hold reason is in the taxonomy, that the decision stage and
orchestrator only emit registered codes, and that severities are consistent.
"""

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
from backend.domain.taxonomy import REGISTRY, severity_of, title_of
from backend.exceptions import build, from_decision

# The PRD FR8 hold reasons, mapped to taxonomy codes.
FR8_CODES = {
    "unresolved_sponsor", "unresolved_study", "unresolved_site",
    "context_ambiguity", "missing_invoice_number", "missing_total",
    "unmatched_line_item", "amount_mismatch", "catalog_unavailable",
    "low_extraction_confidence", "submission_failed", "context_mismatch",
}

# Codes the orchestrator raises directly on failure paths.
ORCHESTRATOR_CODES = {"submission_failed", "catalog_fetch_failed", "stage_failure"}


def test_all_fr8_reasons_are_registered():
    for code in FR8_CODES:
        assert code in REGISTRY, f"FR8 reason {code} missing from taxonomy"


def test_orchestrator_failure_codes_are_registered():
    for code in ORCHESTRATOR_CODES:
        assert code in REGISTRY


def test_build_defaults_from_registry():
    rec = build("inv_1", "catalog_unavailable")
    assert rec.severity is severity_of("catalog_unavailable") is Severity.HIGH
    assert rec.message == title_of("catalog_unavailable") == "Catalog unavailable"


def test_build_respects_overrides():
    rec = build("inv_1", "submission_failed", message="ClinRun 503", severity=Severity.MEDIUM)
    assert rec.message == "ClinRun 503"
    assert rec.severity is Severity.MEDIUM


def test_unknown_code_fails_safe_to_high():
    assert severity_of("not_a_real_code") is Severity.HIGH
    assert title_of("not_a_real_code") == "Not a real code"


def _hold_decision():
    """A decision that trips several flags across severities."""
    extraction = ExtractionResult(
        metadata=InvoiceMetadata(invoice_number="INV-9"),  # total_amount missing
        line_items=[LineItem(invoice_id="inv", raw_description="x", extraction_confidence=0.9)],
        field_confidence={"invoice_number": 0.95},
        missing_fields=["total_amount", "due_date"],  # critical + optional
    )
    # Low-confidence categorization (unresolved document type, no category) so
    # the decision holds on ambiguous_income_expense + low_category_confidence
    # in addition to the missing-total/missing-optional-fields flags above.
    categorization = CategorizationResult(
        invoice_id="inv",
        document_type=DocumentType.UNKNOWN,
        document_type_confidence=0.0,
        category=None,
        category_confidence=0.0,
    )
    return decide(extraction, categorization)


def test_decision_only_emits_registered_codes_with_consistent_severity():
    decision = _hold_decision()
    assert decision.decision is Decision.HOLD
    for flag in decision.risk_flags:
        assert flag.type in REGISTRY, f"decision emitted unregistered code {flag.type}"
        assert flag.severity is severity_of(flag.type)


def test_from_decision_reports_medium_and_high_only():
    decision = _hold_decision()
    records = from_decision("inv", decision)
    severities = {r.severity for r in records}
    assert Severity.LOW not in severities  # informational flags create no exception
    # the LOW missing_optional_fields flag exists on the decision but is not reported
    assert any(f.type == "missing_optional_fields" and f.severity is Severity.LOW
               for f in decision.risk_flags)
    assert all(r.type in REGISTRY for r in records)
