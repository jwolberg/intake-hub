"""Strategy metric instrumentation (STRATEGY § Key metrics; USERS § Operations Lead).

Computes the three guardrail metrics from the persisted invoices + audit trail:

- **auto_submit_rate** — submit-decisions / decided (submit + hold). The
  throughput win: how much the AI carries with no human touch.
- **false_submit_rate** — of auto-submitted invoices, the share a human later had
  to correct or escalate (i.e. the AI was confidently wrong). The trust guardrail.
- **hold_precision** — of held invoices a human has dispositioned, the share they
  confirmed genuinely needed a human (corrected/escalated). Catches the AI crying
  wolf.

The rate buckets key off the AI **decision** (submit/hold), not the current
status: human QC actions move an invoice's status (held → escalated, submitted →
corrected) but never its original decision, so the trust metrics keep counting the
invoices a reviewer acted on. The throughput counts still reflect current status.

``false_submit_rate`` and ``hold_precision`` depend on human outcomes recorded in
the audit trail; they populate as reviewers act (the correction/escalation routes
landed with P2-C3). A rate is ``None`` when its denominator is zero (no data yet),
so the Operations Lead never reads a fabricated number.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import BaseModel

from backend.domain import Actor, AuditAction, Decision, InvoiceStatus

if TYPE_CHECKING:
    from backend.db.repository import Repository

# A human correcting or escalating an invoice is the signal that it genuinely
# needed a human (vs. merely being looked at).
_CONFIRMS_HUMAN_NEEDED = {AuditAction.CORRECTED, AuditAction.ESCALATED}


class WorkflowMetrics(BaseModel):
    """Aggregate workflow metrics for the Operations Lead (STRATEGY § Key metrics)."""

    total: int
    submitted: int
    held: int
    failed: int
    auto_submit_rate: float | None
    false_submit_rate: float | None
    hold_precision: float | None


def _rate(numerator: int, denominator: int) -> float | None:
    return round(numerator / denominator, 3) if denominator else None


def compute_metrics(repo: Repository) -> WorkflowMetrics:
    invoices = repo.list_invoices()

    # Throughput counts reflect *current* status (what the operator sees now).
    submitted = [i for i in invoices if i.status is InvoiceStatus.SUBMITTED]
    held = [i for i in invoices if i.status is InvoiceStatus.HELD]
    failed = [i for i in invoices if i.status is InvoiceStatus.FAILED]

    # Rates key off the AI *decision*, which is immutable — human QC actions move
    # an invoice's status (held → escalated, submitted → corrected) but never its
    # original decision, so decision-based buckets don't lose the very invoices
    # the trust metrics are measuring (P2-B4 ↔ P2-C3 interaction).
    submit_decisions = [i for i in invoices if i.decision is Decision.SUBMIT]
    hold_decisions = [i for i in invoices if i.decision is Decision.HOLD]
    decided = len(submit_decisions) + len(hold_decisions)

    def human_actions(invoice_id: str) -> set[AuditAction]:
        return {e.action for e in repo.get_audit(invoice_id) if e.actor is Actor.HUMAN}

    false_submits = sum(
        1 for inv in submit_decisions if _CONFIRMS_HUMAN_NEEDED & human_actions(inv.id)
    )

    holds_reviewed = 0
    holds_confirmed = 0
    for inv in hold_decisions:
        actions = human_actions(inv.id)
        if actions:
            holds_reviewed += 1
            if _CONFIRMS_HUMAN_NEEDED & actions:
                holds_confirmed += 1

    return WorkflowMetrics(
        total=len(invoices),
        submitted=len(submitted),
        held=len(held),
        failed=len(failed),
        auto_submit_rate=_rate(len(submit_decisions), decided),
        false_submit_rate=_rate(false_submits, len(submit_decisions)),
        hold_precision=_rate(holds_confirmed, holds_reviewed),
    )
