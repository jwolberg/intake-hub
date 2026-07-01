"""Unit tests for the inbox stage (P6-T6).

Covers the MockInbox message source, the message_to_sample adapter (the one new
piece of logic between "received email" and the existing pipeline), and the
repository idempotency primitives — all offline, no PDF rendering, no network.
"""

import types

import pytest
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


# --- provider selection (U4) ------------------------------------------------


def _settings(**overrides):
    base = {
        "inbox_provider": "mock",
        "drive_folder_id": None,
        "google_application_credentials": None,
    }
    base.update(overrides)
    return types.SimpleNamespace(**base)


def _patch_settings(monkeypatch, **overrides):
    # get_inbox_client does `from backend.config import settings` at call time, so
    # patching the config singleton is what takes effect.
    monkeypatch.setattr("backend.config.settings", _settings(**overrides))


def test_get_inbox_client_defaults_to_mock(monkeypatch):
    from backend.inbox import get_inbox_client

    _patch_settings(monkeypatch, inbox_provider="mock")
    assert isinstance(get_inbox_client(), MockInbox)


def test_get_inbox_client_selects_drive_when_configured(monkeypatch):
    from backend.inbox import get_inbox_client
    from backend.inbox.drive import DriveInbox

    _patch_settings(
        monkeypatch,
        inbox_provider="drive",
        drive_folder_id="folder-123",
        google_application_credentials="/path/to/sa.json",
    )
    client = get_inbox_client()
    # A DriveInbox is built; no network is touched (token minting is lazy).
    assert isinstance(client, DriveInbox)


def test_get_inbox_client_drive_missing_config_fails_fast(monkeypatch):
    from backend.inbox import get_inbox_client

    # drive selected but no folder id -> clear error, not a silent mock fallback.
    _patch_settings(
        monkeypatch,
        inbox_provider="drive",
        drive_folder_id=None,
        google_application_credentials="/path/to/sa.json",
    )
    with pytest.raises(ValueError, match="DRIVE_FOLDER_ID"):
        get_inbox_client()

    # drive selected with a folder but no credentials -> clear error.
    _patch_settings(
        monkeypatch,
        inbox_provider="drive",
        drive_folder_id="folder-123",
        google_application_credentials=None,
    )
    with pytest.raises(ValueError, match="GOOGLE_APPLICATION_CREDENTIALS"):
        get_inbox_client()


def test_get_inbox_client_unknown_provider_raises(monkeypatch):
    from backend.inbox import get_inbox_client

    _patch_settings(monkeypatch, inbox_provider="imap")
    with pytest.raises(ValueError, match="unknown INBOX_PROVIDER"):
        get_inbox_client()
