"""Stage: decisioning.

Baseline submit/hold policy (PRD FR6, §9-§10; ARCHITECTURE.md §10). Runs before
any human QC. Any HIGH-severity risk flag forces a hold; with none, the invoice
submits. Low confidence and ambiguity resolve toward hold, never a silent
submit. The full severity model + required-action mapping is deepened in P2-B1.
"""

from __future__ import annotations

from decimal import Decimal

from backend.domain import (
    Decision,
    DecisionResult,
    InvoiceMetadata,
    LineItem,
    MatchResult,
    ResolvedContext,
    RiskFlag,
    Severity,
)

_TOTAL_TOLERANCE = Decimal("0.01")
_MIN_CONTEXT_CONFIDENCE = 0.6

_ACTIONS = {
    "context_unresolved": "Confirm the correct sponsor/study/site",
    "context_ambiguity": "Confirm the correct sponsor/study/site before submission",
    "context_mismatch": "Reconcile the invoice metadata against the reference data",
    "catalog_unavailable": "Retry catalog fetch or escalate",
    "unmatched_line_item": "Map or reject the unmatched line item(s)",
    "amount_mismatch": "Reconcile the line amount against the catalog price",
    "total_mismatch": "Reconcile line items against the invoice total",
}

# Context warning codes (emitted by the context stage) grouped by decision flag.
_AMBIGUITY_WARNINGS = {"ambiguous_context", "multiple_site_candidates"}
_MISMATCH_WARNINGS = {
    "sponsor_mismatch", "study_mismatch", "protocol_mismatch", "site_mismatch",
}


def decide(
    metadata: InvoiceMetadata,
    ctx: ResolvedContext,
    line_items: list[LineItem],
    matches: list[MatchResult],
    catalog_available: bool,
) -> DecisionResult:
    flags: list[RiskFlag] = []

    context_resolved = bool(ctx.sponsor_id and ctx.study_id and ctx.site_id)
    if not context_resolved or ctx.confidence < _MIN_CONTEXT_CONFIDENCE:
        flags.append(RiskFlag(
            type="context_unresolved", severity=Severity.HIGH,
            message="sponsor/study/site not confidently resolved",
        ))
    if any(w in _AMBIGUITY_WARNINGS for w in ctx.warnings):
        flags.append(RiskFlag(
            type="context_ambiguity", severity=Severity.HIGH,
            message="multiple plausible sponsor/study/site candidates matched",
        ))
    mismatches = [w for w in ctx.warnings if w in _MISMATCH_WARNINGS]
    if mismatches:
        flags.append(RiskFlag(
            type="context_mismatch", severity=Severity.HIGH,
            message="invoice metadata conflicts with reference data: "
                    + ", ".join(sorted(mismatches)),
        ))
    if not catalog_available:
        flags.append(RiskFlag(
            type="catalog_unavailable", severity=Severity.HIGH,
            message="catalog could not be fetched for the resolved scope",
        ))

    unmatched = [m for m in matches if m.catalog_item_id is None]
    if unmatched:
        flags.append(RiskFlag(
            type="unmatched_line_item", severity=Severity.HIGH,
            message=f"{len(unmatched)} line item(s) could not be matched to the catalog",
        ))
    if any(m.amount_match is False for m in matches):
        flags.append(RiskFlag(
            type="amount_mismatch", severity=Severity.HIGH,
            message="a line amount disagrees with the catalog price",
        ))

    line_total = sum((li.total for li in line_items if li.total is not None), Decimal("0"))
    total = metadata.total_amount
    if total is not None and abs(line_total - total) > _TOTAL_TOLERANCE:
        flags.append(RiskFlag(
            type="total_mismatch", severity=Severity.HIGH,
            message="line items do not sum to the invoice total",
        ))

    high = [f for f in flags if f.severity is Severity.HIGH]
    if high:
        return DecisionResult(
            decision=Decision.HOLD,
            confidence=0.9,
            rationale="; ".join(f.message for f in high),
            risk_flags=flags,
            required_human_actions=[
                _ACTIONS[f.type] for f in high if f.type in _ACTIONS
            ],
        )

    return DecisionResult(
        decision=Decision.SUBMIT,
        confidence=round(ctx.confidence, 3),
        rationale="Context resolved, all line items matched, and totals reconcile.",
        risk_flags=flags,
    )
