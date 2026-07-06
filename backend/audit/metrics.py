"""Strategy metric instrumentation (STRATEGY § Key metrics; USERS § Operations Lead).

Computes the three guardrail metrics from the persisted invoices + audit trail:

- **auto_submit_rate** — submit-decisions / decided (submit + hold). The
  throughput win: how much the AI carries with no human touch.
- **false_submit_rate** — of auto-submitted invoices, the share a human later had
  to correct or escalate (i.e. the AI was confidently wrong). The trust guardrail.
- **hold_precision** — of held invoices a human has dispositioned, the share they
  confirmed genuinely needed a human (corrected/escalated). Catches the AI crying
  wolf.

The rate buckets key off the AI's **first (autonomous) decision** recorded in the
audit trail, not the current status or decision. Human QC mutates both — a hold a
reviewer corrects and reruns ends up *submitted* — so bucketing on the current
state would credit the AI with an autonomous submit it never made and flag a false
submit that never happened. The first decision is immutable, so auto-submit rate
measures only what the AI carried on its own. The throughput counts still reflect
current status (what the operator sees now).

``false_submit_rate`` and ``hold_precision`` depend on human outcomes recorded in
the audit trail; they populate as reviewers act (the correction/escalation routes
landed with P2-C3). A rate is ``None`` when its denominator is zero (no data yet),
so the Operations Lead never reads a fabricated number.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import BaseModel

from backend.domain import Actor, AuditAction, AuditEvent, InvoiceStatus

if TYPE_CHECKING:
    from backend.db.repository import Repository

# A human correcting or escalating an invoice is the signal that it genuinely
# needed a human (vs. merely being looked at).
_CONFIRMS_HUMAN_NEEDED = {AuditAction.CORRECTED, AuditAction.ESCALATED}
# The AI's autonomous decision is the first terminal outcome it recorded.
_DECISION_ACTIONS = {AuditAction.POSTED, AuditAction.HELD}


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


def _first_decision(audit: list[AuditEvent]) -> AuditAction | None:
    """The AI's autonomous decision: the first ``submitted``/``held`` event it
    recorded. A later human rerun may flip an invoice's status, but its first
    decision is what the AI did on its own."""
    for event in audit:
        if event.action in _DECISION_ACTIONS:
            return event.action
    return None


def compute_metrics(repo: Repository) -> WorkflowMetrics:
    invoices = repo.list_invoices()

    # Throughput counts reflect *current* status (what the operator sees now).
    submitted = [i for i in invoices if i.status is InvoiceStatus.POSTED]
    held = [i for i in invoices if i.status is InvoiceStatus.HELD]
    failed = [i for i in invoices if i.status is InvoiceStatus.FAILED]

    # Rates key off the AI's *first* (autonomous) decision from the audit trail.
    # Using the first decision keeps the metrics honest through human QC: a hold a
    # reviewer corrected and reran to submit was NOT an autonomous submit (so it
    # must not inflate auto-submit rate), and it was NOT a false submit (the human
    # fixed it before submission, during the hold) — it is a hold the human
    # confirmed was needed (P2-B4 ↔ P2-C3/C4 interaction).
    auto_submits = 0
    hold_decisions = 0
    false_submits = 0
    holds_reviewed = 0
    holds_confirmed = 0

    for inv in invoices:
        audit = repo.get_audit(inv.id)
        first = _first_decision(audit)
        human = {e.action for e in audit if e.actor is Actor.HUMAN}
        if first is AuditAction.POSTED:
            auto_submits += 1
            # An autonomous submit a human later had to correct/escalate was wrong.
            if _CONFIRMS_HUMAN_NEEDED & human:
                false_submits += 1
        elif first is AuditAction.HELD:
            hold_decisions += 1
            if human:
                holds_reviewed += 1
                if _CONFIRMS_HUMAN_NEEDED & human:
                    holds_confirmed += 1

    decided = auto_submits + hold_decisions
    return WorkflowMetrics(
        total=len(invoices),
        submitted=len(submitted),
        held=len(held),
        failed=len(failed),
        auto_submit_rate=_rate(auto_submits, decided),
        false_submit_rate=_rate(false_submits, auto_submits),
        hold_precision=_rate(holds_confirmed, holds_reviewed),
    )
