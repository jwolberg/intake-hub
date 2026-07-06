"""Stage: decisioning (ledger pivot).

Full auto-file/hold policy for the income/expense ledger (R7; carried over from
the clinical-trial decision engine unchanged in spirit). Runs before any human QC
and never requires human approval to reach a decision.

The policy is data-driven and severity-graded:

- **High** — blocking. Any high-severity flag forces a ``hold``.
- **Medium** — visibility only. Surfaced on the result but does not block a submit.
- **Low** — informational (e.g. a missing optional field).

It consumes every prior stage's confidence via a weakest-link ``min()``: per-field
extraction confidence, the income/expense determination, and the Schedule C
category confidence. Low confidence anywhere resolves toward a hold — an auto-file
is never silent when the model is unsure. ``decision_confidence`` is that combined
stage confidence for both submit and hold, so a low value *explains* a hold (held
because uncertain) and is honest in the UI. The hold *reason* is a separate signal
carried in ``risk_flags``.

The categorize stage's ``adversarial`` flag forces a hold (R16): auto-filing a
document whose content looks spoofed/injection-style is exactly what we must not do.
"""

from __future__ import annotations

from decimal import Decimal

from backend.domain import (
    CategorizationResult,
    Decision,
    DecisionResult,
    DocumentType,
    ExtractionResult,
    RiskFlag,
    Severity,
)
from backend.domain.taxonomy import severity_of

_TOTAL_TOLERANCE = Decimal("0.01")

# Confidence thresholds (low blocks, medium is visibility-only).
_EXTRACTION_HOLD = 0.5
_EXTRACTION_WATCH = 0.8
_TYPE_HOLD = 0.7        # income/expense below this → ambiguous_income_expense (block)
_CATEGORY_HOLD = 0.7    # category confidence below this → low_category_confidence (block)
_DECISION_FLOOR = 0.8   # file only when confident; below this → hold for review

# The one header field a ledger row cannot do without is the amount. Unlike the
# clinical-trial engine we do not require an invoice number — solopreneur receipts
# frequently have none (see implementation-notes U4).
_CRITICAL_FIELDS = {"total_amount": "missing_total"}

_ACTIONS = {
    "suspected_adversarial": "Verify the source; the content looks spoofed before filing",
    "ambiguous_income_expense": "Confirm whether this is income or an expense",
    "low_category_confidence": "Confirm or correct the Schedule C category",
    "suspected_duplicate": "Confirm this is not a duplicate of an already-filed item",
    "sheet_write_failed": "Retry the Sheet append",
    "missing_total": "Add the missing amount",
    "low_extraction_confidence": "Re-extract or verify the low-confidence fields",
    "total_mismatch": "Reconcile line items against the total",
    "low_confidence": "Review the item; overall confidence is below the file threshold",
}


def decide(
    extraction: ExtractionResult,
    categorization: CategorizationResult,
) -> DecisionResult:
    metadata = extraction.metadata
    line_items = extraction.line_items
    flags: list[RiskFlag] = []

    def flag(code: str, message: str) -> None:
        # Severity comes from the canonical taxonomy — one source of truth.
        flags.append(RiskFlag(type=code, severity=severity_of(code), message=message))

    # --- Adversarial / spoofed content (R16): block regardless of confidence ---
    if categorization.adversarial:
        flag("suspected_adversarial",
             "document content contains instruction-like text; treated as untrusted")

    # --- Income vs expense (R5) ---
    if (categorization.document_type is DocumentType.UNKNOWN
            or categorization.document_type_confidence < _TYPE_HOLD):
        flag("ambiguous_income_expense",
             "could not confidently classify income vs expense "
             f"({round(categorization.document_type_confidence, 3)})")

    # --- Schedule C category (R6) ---
    if not categorization.category or categorization.category_confidence < _CATEGORY_HOLD:
        flag("low_category_confidence",
             "category could not be assigned with confidence "
             f"({round(categorization.category_confidence, 3)})")

    # --- Extraction completeness + confidence ---
    for field, flag_type in _CRITICAL_FIELDS.items():
        if field in extraction.missing_fields:
            flag(flag_type, f"required field '{field}' is missing")
    optional_missing = [f for f in extraction.missing_fields if f not in _CRITICAL_FIELDS]
    if optional_missing:
        flag("missing_optional_fields",
             "optional fields not present: " + ", ".join(optional_missing))

    extraction_conf = _extraction_confidence(extraction)
    if extraction_conf < _EXTRACTION_HOLD:
        flag("low_extraction_confidence",
             f"extraction confidence {extraction_conf} below the file threshold")
    elif extraction_conf < _EXTRACTION_WATCH:
        flag("moderate_extraction_confidence",
             f"some extracted fields are only moderately confident ({extraction_conf})")

    # --- Totals ---
    line_total = sum((li.total for li in line_items if li.total is not None), Decimal("0"))
    total = metadata.total_amount
    if total is not None and line_items and abs(line_total - total) > _TOTAL_TOLERANCE:
        flag("total_mismatch", "line items do not sum to the total")

    # --- Decide ---
    decision_confidence = round(
        min(
            extraction_conf,
            categorization.document_type_confidence,
            categorization.category_confidence,
        ),
        3,
    )
    high = [f for f in flags if f.severity is Severity.HIGH]
    if not high and decision_confidence < _DECISION_FLOOR:
        flag("low_confidence",
             f"overall decision confidence {decision_confidence} below the file threshold")
        high = [f for f in flags if f.severity is Severity.HIGH]

    if high:
        return DecisionResult(
            decision=Decision.HOLD,
            confidence=decision_confidence,
            rationale="; ".join(f.message for f in high),
            risk_flags=flags,
            required_human_actions=[_ACTIONS[f.type] for f in high if f.type in _ACTIONS],
        )

    return DecisionResult(
        decision=Decision.SUBMIT,
        confidence=decision_confidence,
        rationale="Classified and categorized with high confidence; filed to the ledger.",
        risk_flags=flags,
    )


def _extraction_confidence(extraction: ExtractionResult) -> float:
    """Weakest-link confidence over populated header fields + line items."""
    populated = [
        conf for field, conf in extraction.field_confidence.items()
        if field not in extraction.missing_fields
    ]
    items = [
        li.extraction_confidence for li in extraction.line_items
        if li.extraction_confidence is not None
    ]
    scores = populated + items
    return round(min(scores), 3) if scores else 0.0
