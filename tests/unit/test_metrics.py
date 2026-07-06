"""Unit tests for strategy metric instrumentation (STRATEGY § Key metrics)."""

from backend.audit import record
from backend.audit.metrics import compute_metrics
from backend.clients import PassthroughLLMClient, StubSheetsClient
from backend.db.repository import InMemoryRepository
from backend.domain import Actor, AuditAction
from backend.orchestrator import process

# A clean expense: clear vendor + a categorizable line + a total → posted.
CLEAN_EXPENSE = {
    "source": {"channel": "email", "message_id": "m-clean",
               "subject": "Your receipt from Notion", "sender": "billing@notion.so"},
    "document": {
        "metadata": {"invoice_number": "N-1", "vendor_name": "Notion",
                     "currency": "USD", "total_amount": "10.00"},
        "line_items": [{"raw_description": "Notion subscription",
                        "quantity": "1", "unit_price": "10.00", "total": "10.00"}],
    },
}

# No categorizable keyword → held (no Sheet write).
UNCATEGORIZABLE = {
    "source": {"channel": "email", "message_id": "m-hold", "subject": "note",
               "sender": "someone@example.com"},
    "document": {
        "metadata": {"vendor_name": "Bob", "total_amount": "10.00"},
        "line_items": [{"raw_description": "thing", "quantity": "1",
                        "unit_price": "10.00", "total": "10.00"}],
    },
}


def _seed():
    """One auto-posted item + one hold, against the same repository."""
    repo = InMemoryRepository()
    posted = process(CLEAN_EXPENSE, repo, llm=PassthroughLLMClient(), sheets=StubSheetsClient())
    held = process(UNCATEGORIZABLE, repo, llm=PassthroughLLMClient(), sheets=StubSheetsClient())
    return repo, posted, held


def test_throughput_and_auto_submit_rate():
    repo, _posted, _held = _seed()
    m = compute_metrics(repo)
    assert (m.total, m.submitted, m.held, m.failed) == (2, 1, 1, 0)
    assert m.auto_submit_rate == 0.5
    # no human has touched anything yet
    assert m.false_submit_rate == 0.0      # 0 of 1 posts known-wrong
    assert m.hold_precision is None        # no hold dispositioned yet → no data


def test_false_submit_and_hold_precision_from_human_outcomes():
    repo, posted, held = _seed()
    # a human had to correct an auto-posted invoice → it was a false submit
    record(repo, posted.id, AuditAction.CORRECTED, actor=Actor.HUMAN,
           before={"vendor_name": "X"}, after={"vendor_name": "Y"}, reason="wrong vendor")
    # a human escalated the held invoice → the hold genuinely needed a human
    record(repo, held.id, AuditAction.ESCALATED, actor=Actor.HUMAN, reason="needs finance")

    m = compute_metrics(repo)
    assert m.false_submit_rate == 1.0
    assert m.hold_precision == 1.0


def test_hold_reviewed_without_correction_lowers_precision():
    repo, _posted, held = _seed()
    # process a second hold and have a human review it but take no corrective action
    held2 = process(UNCATEGORIZABLE, repo, llm=PassthroughLLMClient(), sheets=StubSheetsClient())
    record(repo, held.id, AuditAction.ESCALATED, actor=Actor.HUMAN, reason="needs finance")
    record(repo, held2.id, AuditAction.REVIEWED, actor=Actor.HUMAN, reason="false alarm")

    m = compute_metrics(repo)
    # 2 holds reviewed, 1 confirmed as genuinely needing a human
    assert m.hold_precision == 0.5


def test_empty_repository_has_no_rates():
    m = compute_metrics(InMemoryRepository())
    assert m.total == 0
    assert m.auto_submit_rate is None
    assert m.false_submit_rate is None
    assert m.hold_precision is None
