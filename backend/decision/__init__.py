"""Stage: decisioning.

Full submit/hold policy (PRD FR6, §9, §16; ARCHITECTURE.md §10). Runs before any
human QC and never requires human approval to reach a decision.

The policy is data-driven and severity-graded (PRD §16):

- **High** — blocking. Any high-severity flag forces a ``hold``.
- **Medium** — visibility only. Surfaced on the result but does not block a submit.
- **Low** — informational (e.g. a missing optional field).

It consumes every prior stage's confidence: context resolution, per-field
extraction confidence (P2-A1), and line-item match confidence. Low confidence
anywhere resolves toward a hold — a submit is never silent when the model is
unsure. ``decision_confidence`` on a submit is the combined (weakest-link) stage
confidence; on a hold it reflects confidence in deferring to a human.
"""

from __future__ import annotations

from decimal import Decimal

from backend.domain import (
    Decision,
    DecisionResult,
    ExtractionResult,
    MatchResult,
    ResolvedContext,
    RiskFlag,
    Severity,
)

_TOTAL_TOLERANCE = Decimal("0.01")

# Confidence thresholds (PRD §16: low blocks, medium is visibility-only).
_CONTEXT_HOLD = 0.6
_EXTRACTION_HOLD = 0.5
_EXTRACTION_WATCH = 0.8
_MATCH_HOLD = 0.5
_MATCH_WATCH = 0.85
_DECISION_FLOOR = 0.5  # overall floor → never a silent submit below this

# Header fields that must be present to submit (PRD FR8, §16).
_CRITICAL_FIELDS = {"invoice_number": "missing_invoice_number",
                    "total_amount": "missing_total"}

_HOLD_CONFIDENCE = 0.9  # confidence that deferring to a human is correct

_ACTIONS = {
    "context_unresolved": "Confirm the correct sponsor/study/site",
    "context_ambiguity": "Confirm the correct sponsor/study/site before submission",
    "context_mismatch": "Reconcile the invoice metadata against the reference data",
    "catalog_unavailable": "Retry catalog fetch or escalate",
    "missing_invoice_number": "Add the missing invoice number",
    "missing_total": "Add the missing invoice total",
    "low_extraction_confidence": "Re-extract or verify the low-confidence fields",
    "unmatched_line_item": "Map or reject the unmatched line item(s)",
    "amount_mismatch": "Reconcile the line amount against the catalog price",
    "quantity_mismatch": "Reconcile the line quantity/total against the catalog price",
    "low_match_confidence": "Verify the low-confidence line-item matches",
    "total_mismatch": "Reconcile line items against the invoice total",
    "low_confidence": "Review the invoice; overall confidence is below the submit threshold",
}

# Context warning codes (emitted by the context stage) grouped by decision flag.
_AMBIGUITY_WARNINGS = {"ambiguous_context", "multiple_site_candidates"}
_MISMATCH_WARNINGS = {
    "sponsor_mismatch", "study_mismatch", "protocol_mismatch", "site_mismatch",
}


def decide(
    extraction: ExtractionResult,
    ctx: ResolvedContext,
    matches: list[MatchResult],
    catalog_available: bool,
) -> DecisionResult:
    metadata = extraction.metadata
    line_items = extraction.line_items
    flags: list[RiskFlag] = []

    def flag(type_: str, severity: Severity, message: str) -> None:
        flags.append(RiskFlag(type=type_, severity=severity, message=message))

    # --- Context ---
    context_resolved = bool(ctx.sponsor_id and ctx.study_id and ctx.site_id)
    if not context_resolved or ctx.confidence < _CONTEXT_HOLD:
        flag("context_unresolved", Severity.HIGH,
             "sponsor/study/site not confidently resolved")
    if any(w in _AMBIGUITY_WARNINGS for w in ctx.warnings):
        flag("context_ambiguity", Severity.HIGH,
             "multiple plausible sponsor/study/site candidates matched")
    mismatches = sorted(w for w in ctx.warnings if w in _MISMATCH_WARNINGS)
    if mismatches:
        flag("context_mismatch", Severity.HIGH,
             "invoice metadata conflicts with reference data: " + ", ".join(mismatches))

    # --- Catalog ---
    if not catalog_available:
        flag("catalog_unavailable", Severity.HIGH,
             "catalog could not be fetched for the resolved scope")

    # --- Extraction completeness + confidence ---
    for field, flag_type in _CRITICAL_FIELDS.items():
        if field in extraction.missing_fields:
            flag(flag_type, Severity.HIGH, f"required field '{field}' is missing")
    optional_missing = [f for f in extraction.missing_fields if f not in _CRITICAL_FIELDS]
    if optional_missing:
        flag("missing_optional_fields", Severity.LOW,
             "optional fields not present: " + ", ".join(optional_missing))

    extraction_conf = _extraction_confidence(extraction)
    if extraction_conf < _EXTRACTION_HOLD:
        flag("low_extraction_confidence", Severity.HIGH,
             f"extraction confidence {extraction_conf} below the submit threshold")
    elif extraction_conf < _EXTRACTION_WATCH:
        flag("moderate_extraction_confidence", Severity.MEDIUM,
             f"some extracted fields are only moderately confident ({extraction_conf})")

    # --- Matching ---
    if any(m.catalog_item_id is None for m in matches):
        n = sum(1 for m in matches if m.catalog_item_id is None)
        flag("unmatched_line_item", Severity.HIGH,
             f"{n} line item(s) could not be matched to the catalog")
    if any(m.amount_match is False for m in matches):
        flag("amount_mismatch", Severity.HIGH,
             "a line amount disagrees with the catalog price")
    if any(m.quantity_match is False for m in matches):
        flag("quantity_mismatch", Severity.HIGH,
             "a line quantity/total disagrees with the catalog price")

    match_conf = _match_confidence(matches)
    if match_conf < _MATCH_HOLD:
        flag("low_match_confidence", Severity.HIGH,
             f"line-item match confidence {match_conf} below the submit threshold")
    elif match_conf < _MATCH_WATCH:
        flag("weak_match", Severity.MEDIUM,
             f"some line items matched only weakly ({match_conf})")

    # --- Totals ---
    line_total = sum((li.total for li in line_items if li.total is not None), Decimal("0"))
    total = metadata.total_amount
    if total is not None and abs(line_total - total) > _TOTAL_TOLERANCE:
        flag("total_mismatch", Severity.HIGH, "line items do not sum to the invoice total")

    # --- Decide ---
    decision_confidence = round(min(ctx.confidence, extraction_conf, match_conf), 3)
    high = [f for f in flags if f.severity is Severity.HIGH]
    if not high and decision_confidence < _DECISION_FLOOR:
        flag("low_confidence", Severity.HIGH,
             f"overall decision confidence {decision_confidence} below the submit threshold")
        high = [f for f in flags if f.severity is Severity.HIGH]

    if high:
        return DecisionResult(
            decision=Decision.HOLD,
            confidence=_HOLD_CONFIDENCE,
            rationale="; ".join(f.message for f in high),
            risk_flags=flags,
            required_human_actions=[_ACTIONS[f.type] for f in high if f.type in _ACTIONS],
        )

    return DecisionResult(
        decision=Decision.SUBMIT,
        confidence=decision_confidence,
        rationale="Context resolved, all line items matched, and totals reconcile.",
        risk_flags=flags,
    )


def _extraction_confidence(extraction: ExtractionResult) -> float:
    """Weakest-link confidence over populated header fields + line items (P2-A1)."""
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


def _match_confidence(matches: list[MatchResult]) -> float:
    """Weakest confidence among *matched* items (unmatched is flagged separately)."""
    matched = [m.confidence for m in matches if m.catalog_item_id is not None]
    return round(min(matched), 3) if matched else 1.0
