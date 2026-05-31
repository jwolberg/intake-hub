"""Unit tests for the inbox stage (P6-T6).

Covers the MockInbox message source, the message_to_sample adapter (the one new
piece of logic between "received email" and the existing pipeline), and the
repository idempotency primitives — all offline, no PDF rendering, no network.
"""

from backend.db.repository import InMemoryRepository
from backend.inbox import DEMO_STEMS, InboxMessage, MockInbox, message_to_sample


def test_mock_inbox_replays_demo_set_without_rendering():
    messages = MockInbox(render_pdf=False).fetch_messages()
    assert [m.message_id for m in messages] == DEMO_STEMS
    # Offline path carries the document block and no rendered PDF path.
    assert all(m.document is not None for m in messages)
    assert all(m.attachment_path is None for m in messages)


def test_mock_inbox_respects_explicit_stems():
    messages = MockInbox(render_pdf=False, stems=["inv_clean_001"]).fetch_messages()
    assert [m.message_id for m in messages] == ["inv_clean_001"]


def test_message_to_sample_shape():
    message = InboxMessage(
        message_id="inv_x",
        subject="Invoice INV-1",
        sender="billing@site.example",
        attachment="INV-1.pdf",
        attachment_path="/tmp/INV-1.pdf",
        document={"metadata": {"invoice_number": "INV-1"}},
    )
    sample = message_to_sample(message)
    assert sample["source"] == {
        "channel": "email",
        "message_id": "inv_x",
        "subject": "Invoice INV-1",
        "sender": "billing@site.example",
        "attachment": "INV-1.pdf",
        "attachment_path": "/tmp/INV-1.pdf",
    }
    assert sample["document"] == {"metadata": {"invoice_number": "INV-1"}}
    # A document-only message carries no body key (parser picks the attachment path).
    assert "body" not in sample


def test_message_to_sample_body_only():
    sample = message_to_sample(InboxMessage(message_id="inv_b", body="Invoice total $10"))
    assert sample["body"] == "Invoice total $10"
    assert "document" not in sample
    assert sample["source"]["channel"] == "email"


def test_repository_seen_message_idempotency():
    repo = InMemoryRepository()
    assert repo.is_seen("m1") is False
    repo.mark_seen("m1")
    repo.mark_seen("m1")  # idempotent
    assert repo.is_seen("m1") is True
    assert repo.is_seen("m2") is False
