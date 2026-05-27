"""Stage: audit.

Append immutable audit events for every stage and human action, attributed by
actor so AI/system work stays distinguishable from human edits (PRD §17, FR11;
ARCHITECTURE.md §12).

Per PRD §17 each event carries a timestamp, an actor (ai/system/human), an
action, optional before/after values (for corrections), and an optional reason.
``before``/``after``/``reason`` are stored under standard keys inside the event's
JSONB ``details`` so no schema change is needed; ``record`` is the single writer.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from backend.domain import Actor, AuditAction, AuditEvent

if TYPE_CHECKING:
    from backend.db.repository import Repository


def record(
    repo: Repository,
    invoice_id: str,
    action: AuditAction,
    *,
    actor: Actor = Actor.AI,
    details: dict | None = None,
    before: object | None = None,
    after: object | None = None,
    reason: str | None = None,
) -> AuditEvent:
    """Build an audit event and append it to the repository.

    ``before``/``after`` capture a value change (e.g. a human correction);
    ``reason`` is a human-readable note (e.g. a hold rationale or a correction
    justification). They are folded into ``details`` under standard keys.
    """
    payload = dict(details or {})
    if before is not None:
        payload["before"] = before
    if after is not None:
        payload["after"] = after
    if reason is not None:
        payload["reason"] = reason

    event = AuditEvent(
        invoice_id=invoice_id,
        actor=actor,
        action=action,
        details=payload,
    )
    repo.append_audit(event)
    return event
