"""Unit tests for the audit trail (P2-B3; PRD §17, FR10/FR11).

Verifies actor attribution (AI vs system vs human), before/after capture on a
correction, the reason note, and that the trail is append-only and ordered.
"""

from backend.audit import record
from backend.db.repository import InMemoryRepository
from backend.domain import Actor, AuditAction


def test_record_folds_reason_into_details():
    repo = InMemoryRepository()
    event = record(repo, "inv_1", AuditAction.HELD, reason="catalog unavailable")
    assert event.details["reason"] == "catalog unavailable"
    assert event.actor is Actor.AI  # default


def test_record_without_optionals_keeps_details_clean():
    repo = InMemoryRepository()
    event = record(repo, "inv_1", AuditAction.PARSED, actor=Actor.SYSTEM,
                   details={"format": "pdf"})
    assert event.details == {"format": "pdf"}  # no before/after/reason keys added


def test_human_correction_captures_before_after_and_reason():
    repo = InMemoryRepository()
    event = record(
        repo, "inv_1", AuditAction.CORRECTED, actor=Actor.HUMAN,
        before={"sponsor_name": "Acme Biosciences"},
        after={"sponsor_name": "Northwind Therapeutics"},
        reason="sponsor was mis-extracted from the header",
    )
    assert event.actor is Actor.HUMAN
    assert event.details["before"] == {"sponsor_name": "Acme Biosciences"}
    assert event.details["after"] == {"sponsor_name": "Northwind Therapeutics"}
    assert "mis-extracted" in event.details["reason"]


def test_ai_and_human_events_are_distinguishable():
    repo = InMemoryRepository()
    record(repo, "inv_1", AuditAction.HELD, actor=Actor.AI, reason="held by policy")
    record(repo, "inv_1", AuditAction.CORRECTED, actor=Actor.HUMAN,
           before={"x": 1}, after={"x": 2}, reason="fixed")

    trail = repo.get_audit("inv_1")
    assert [e.actor for e in trail] == [Actor.AI, Actor.HUMAN]  # append-only, ordered
    human = [e for e in trail if e.actor is Actor.HUMAN]
    assert len(human) == 1 and human[0].action is AuditAction.CORRECTED
