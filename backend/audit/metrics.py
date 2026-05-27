"""Strategy metric instrumentation (STRATEGY § Key metrics; USERS § Operations Lead).

Computes the three guardrail metrics from the persisted invoices + audit trail:

- **auto_submit_rate** — submitted / decided (submitted + held). The throughput
  win: how much the AI carries with no human touch.
- **false_submit_rate** — of auto-submitted invoices, the share a human later had
  to correct or escalate (i.e. the AI was confidently wrong). The trust guardrail.
- **hold_precision** — of held invoices a human has dispositioned, the share they
  confirmed genuinely needed a human (corrected/escalated). Catches the AI crying
  wolf.

``false_submit_rate`` and ``hold_precision`` depend on human outcomes recorded in
the audit trail; they populate as reviewers act (the correction/escalation routes
land with P2-C3). A rate is ``None`` when its denominator is zero (no data yet),
so the Operations Lead never reads a fabricated number.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import BaseModel

from backend.domain import Actor, AuditAction, InvoiceStatus

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
    submitted = [i for i in invoices if i.status is InvoiceStatus.SUBMITTED]
    held = [i for i in invoices if i.status is InvoiceStatus.HELD]
    failed = [i for i in invoices if i.status is InvoiceStatus.FAILED]
    decided = len(submitted) + len(held)

    def human_actions(invoice_id: str) -> list[AuditAction]:
        return [e.action for e in repo.get_audit(invoice_id) if e.actor is Actor.HUMAN]

    false_submits = sum(
        1 for inv in submitted
        if _CONFIRMS_HUMAN_NEEDED & set(human_actions(inv.id))
    )

    holds_reviewed = 0
    holds_confirmed = 0
    for inv in held:
        actions = human_actions(inv.id)
        if actions:
            holds_reviewed += 1
            if _CONFIRMS_HUMAN_NEEDED & set(actions):
                holds_confirmed += 1

    return WorkflowMetrics(
        total=len(invoices),
        submitted=len(submitted),
        held=len(held),
        failed=len(failed),
        auto_submit_rate=_rate(len(submitted), decided),
        false_submit_rate=_rate(false_submits, len(submitted)),
        hold_precision=_rate(holds_confirmed, holds_reviewed),
    )
