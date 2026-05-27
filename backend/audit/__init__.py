"""Stage: audit.

Append immutable audit events for every stage and human action, attributed by
actor so AI/system work stays distinguishable from human edits (PRD §17, FR11;
ARCHITECTURE.md §12). Completed (before/after on corrections) in P2-B3.
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
) -> AuditEvent:
    """Build an audit event and append it to the repository."""
    event = AuditEvent(
        invoice_id=invoice_id,
        actor=actor,
        action=action,
        details=details or {},
    )
    repo.append_audit(event)
    return event
