"""Unit tests for the Gmail inbox provider (feat: solopreneur-ledger pivot, U8).

Uses ``StubGmailClient`` (U7) + ``InMemoryRepository`` as the injected
``GmailSyncState`` so the provider runs with no network, no credentials, and
no database. Covers: first-connect backfill vs. subsequent history-delta
fetch, the 404/expired-history fallback to a full backfill, the receipt
filter dropping non-receipts (id/sender/subject only, body never fetched) vs.
carrying receipts through (inline HTML and PDF-attachment) to
``message_to_sample``, ``_build_gmail_inbox``'s fail-fast config checks, and
the token-encryption round trip.
"""

from __future__ import annotations

import base64
import importlib.util
import types

import pytest
from backend.clients import GmailAttachment, GmailMessage, StubGmailClient
from backend.db.repository import InMemoryRepository
from backend.inbox import _build_gmail_inbox, message_to_sample
from backend.inbox.gmail import GmailInbox


class _CountingGmailClient(StubGmailClient):
    """Spies on call counts so tests can assert *which* sync path ran."""

    def __init__(self) -> None:
        super().__init__()
        self.list_calls = 0
        self.history_calls = 0
        self.full_fetch_ids: list[str] = []

    def list_messages(self, query: str, *, page_token: str | None = None):
        self.list_calls += 1
        return super().list_messages(query, page_token=page_token)

    def history_list(self, start_history_id: str):
        self.history_calls += 1
        return super().history_list(start_history_id)

    def get_message(self, message_id: str, *, fmt: str = "full") -> GmailMessage:
        if fmt == "full":
            self.full_fetch_ids.append(message_id)
        return super().get_message(message_id, fmt=fmt)


def _msg(
    message_id: str,
    *,
    subject: str = "",
    sender: str = "",
    body_html: str | None = None,
    body_text: str | None = None,
    attachments: list[GmailAttachment] | None = None,
) -> GmailMessage:
    return GmailMessage(
        id=message_id,
        thread_id=f"thread-{message_id}",
        subject=subject,
        sender=sender,
        body_html=body_html,
        body_text=body_text,
        attachments=attachments or [],
    )


def _receipt_message(message_id: str) -> GmailMessage:
    # "billing@" localpart is a clear heuristic receipt signal (backend.receipts).
    return _msg(
        message_id,
        subject="Your receipt",
        sender="billing@vendor.example",
        body_html="<p>Total: $10.00</p>",
    )


# --- backfill vs. incremental sync -------------------------------------------


def test_first_fetch_backfills_then_subsequent_uses_history_deltas_only():
    client = _CountingGmailClient()
    client.page_size = 1  # force pagination across two pages
    client.add_message(_receipt_message("r1"))
    client.add_message(_receipt_message("r2"))

    sync_state = InMemoryRepository()
    inbox = GmailInbox(client, tax_year=2026, sync_state=sync_state)

    first = inbox.fetch_messages()

    assert {m.message_id for m in first} == {"r1", "r2"}
    assert client.list_calls == 2  # paginated: page_size=1, 2 messages
    assert client.history_calls == 1  # the post-backfill bootstrap probe
    history_id = sync_state.get_sync_history_id()
    assert history_id is not None

    # A new message arrives; seed the delta from the bootstrapped historyId.
    client.add_message(_receipt_message("r3"))
    client.seed_history(history_id, ["r3"], "next-id")
    client.list_calls = 0
    client.history_calls = 0

    second = inbox.fetch_messages()

    assert [m.message_id for m in second] == ["r3"]
    assert client.list_calls == 0  # no re-scan — delta only
    assert client.history_calls == 1
    assert sync_state.get_sync_history_id() == "next-id"


def test_expired_history_id_falls_back_to_full_backfill():
    client = _CountingGmailClient()
    client.add_message(_receipt_message("r1"))
    client.expired_history_ids.add("stale-id")

    sync_state = InMemoryRepository()
    inbox = GmailInbox(client, tax_year=2026, sync_state=sync_state, history_id="stale-id")

    messages = inbox.fetch_messages()

    assert {m.message_id for m in messages} == {"r1"}
    assert client.list_calls == 1  # fell back to a full backfill
    # One call for the expired historyId, one for the post-backfill bootstrap.
    assert client.history_calls == 2
    assert sync_state.get_sync_history_id() is not None


def test_refetch_is_idempotent_via_is_seen():
    client = _CountingGmailClient()
    client.add_message(_receipt_message("r1"))
    sync_state = InMemoryRepository()
    inbox = GmailInbox(client, tax_year=2026, sync_state=sync_state)

    first = inbox.fetch_messages()
    assert {m.message_id for m in first} == {"r1"}

    sync_state.mark_seen("r1")  # the fetch route marks seen after processing
    second = inbox.fetch_messages()
    # r1 comes back from the (unchanged) history delta, but is_seen skips it.
    assert second == []


# --- receipt filter -----------------------------------------------------------


def test_non_receipt_is_dropped_marked_seen_and_body_never_fetched():
    client = _CountingGmailClient()
    client.add_message(_msg(
        "newsletter-1",
        subject="Weekly digest: what's new",
        sender="newsletter@list.example.com",
        body_html="<p>SECRET body content that must never surface</p>",
    ))
    sync_state = InMemoryRepository()
    inbox = GmailInbox(client, tax_year=2026, sync_state=sync_state)

    messages = inbox.fetch_messages()

    assert messages == []
    assert sync_state.is_seen("newsletter-1") is True
    assert "newsletter-1" not in client.full_fetch_ids  # body never fetched


def test_inline_html_receipt_is_returned_with_body():
    client = _CountingGmailClient()
    client.add_message(_receipt_message("r1"))
    sync_state = InMemoryRepository()
    inbox = GmailInbox(client, tax_year=2026, sync_state=sync_state)

    messages = inbox.fetch_messages()

    assert len(messages) == 1
    message = messages[0]
    assert message.body == "<p>Total: $10.00</p>"
    assert message.attachment_b64 is None
    assert "r1" in client.full_fetch_ids


def test_pdf_attachment_receipt_carries_attachment_b64_into_sample():
    client = _CountingGmailClient()
    client.add_message(
        _msg(
            "receipt-1",
            subject="Your statement",
            sender="ops@random.example",
            attachments=[
                GmailAttachment(
                    filename="receipt.pdf", attachment_id="att-1", mime_type="application/pdf"
                )
            ],
        ),
        attachment_bytes={"att-1": b"%PDF-1.4 fake pdf bytes"},
    )
    sync_state = InMemoryRepository()
    inbox = GmailInbox(client, tax_year=2026, sync_state=sync_state)

    messages = inbox.fetch_messages()

    assert len(messages) == 1
    message = messages[0]
    assert message.attachment == "receipt.pdf"
    assert message.attachment_b64 == base64.b64encode(b"%PDF-1.4 fake pdf bytes").decode()

    sample = message_to_sample(message)
    assert sample["source"]["attachment"] == "receipt.pdf"
    assert sample["source"]["attachment_b64"] == message.attachment_b64


def test_ambiguous_message_is_not_dropped_without_an_llm():
    """No heuristic signal either way, no LLM configured -> classify_receipt's
    low-confidence "needs review" fallback -> flows on to be held downstream,
    never silently discarded."""
    client = _CountingGmailClient()
    client.add_message(_msg(
        "ambiguous-1", subject="Following up", sender="pat@example.com",
        body_html="<p>Here is what we discussed.</p>",
    ))
    sync_state = InMemoryRepository()
    inbox = GmailInbox(client, tax_year=2026, sync_state=sync_state)

    messages = inbox.fetch_messages()

    assert [m.message_id for m in messages] == ["ambiguous-1"]


# --- on_processed (read-only scope) ------------------------------------------


def test_on_processed_is_a_documented_no_op(caplog):
    from backend.domain import Invoice

    client = StubGmailClient()
    sync_state = InMemoryRepository()
    inbox = GmailInbox(client, tax_year=2026, sync_state=sync_state, label="processed")
    from backend.inbox import InboxMessage

    inbox_message = InboxMessage(message_id="r1", subject="s", sender="billing@vendor.example")
    invoice = Invoice(source="email")

    with caplog.at_level("INFO"):
        inbox.on_processed(inbox_message, invoice)

    assert any("not applied" in record.message for record in caplog.records)


# --- _build_gmail_inbox fail-fast + wiring -----------------------------------


def _fake_settings(**overrides) -> types.SimpleNamespace:
    base = dict(
        gmail_client_id="cid",
        gmail_client_secret="secret",
        gmail_refresh_token="refresh-token",
        gmail_token_enc_key="fake-key",
        gmail_label="processed",
        gmail_tax_year=2026,
    )
    base.update(overrides)
    return types.SimpleNamespace(**base)


def test_build_gmail_inbox_missing_client_id_fails_fast():
    with pytest.raises(ValueError, match="GMAIL_CLIENT_ID"):
        _build_gmail_inbox(_fake_settings(gmail_client_id=None), repo=InMemoryRepository())


def test_build_gmail_inbox_missing_client_secret_fails_fast():
    with pytest.raises(ValueError, match="GMAIL_CLIENT_SECRET"):
        _build_gmail_inbox(_fake_settings(gmail_client_secret=None), repo=InMemoryRepository())


def test_build_gmail_inbox_missing_refresh_token_fails_fast():
    with pytest.raises(ValueError, match="GMAIL_REFRESH_TOKEN"):
        _build_gmail_inbox(_fake_settings(gmail_refresh_token=None), repo=InMemoryRepository())


def test_get_inbox_client_gmail_missing_config_fails_fast(monkeypatch):
    from backend.inbox import get_inbox_client

    monkeypatch.setattr(
        "backend.inbox.settings",
        types.SimpleNamespace(inbox_provider="gmail", **vars(_fake_settings(gmail_client_id=None))),
    )
    with pytest.raises(ValueError, match="GMAIL_CLIENT_ID"):
        get_inbox_client()


def test_build_gmail_inbox_constructs_client_with_injected_repo():
    repo = InMemoryRepository()
    settings = _fake_settings()

    inbox = _build_gmail_inbox(settings, repo=repo)

    assert isinstance(inbox, GmailInbox)
    assert inbox._sync_state is repo  # production wiring: repo doubles as sync_state
    assert inbox._tax_year == 2026
    assert inbox._label == "processed"

    stored = repo.get_oauth_token("gmail")
    if importlib.util.find_spec("cryptography") is None:
        # Documented fallback (cryptography not installed in this env): the
        # refresh token is read from config each time, never persisted.
        assert stored is None
    else:
        assert stored is not None
        assert stored != settings.gmail_refresh_token


# --- token encryption ---------------------------------------------------------


def test_token_encryption_roundtrips_and_is_not_plaintext():
    pytest.importorskip("cryptography")
    from backend.inbox._crypto import decrypt_token, encrypt_token
    from cryptography.fernet import Fernet

    key = Fernet.generate_key().decode()
    plaintext = "1//0g-super-secret-refresh-token"

    ciphertext = encrypt_token(plaintext, key)

    assert ciphertext != plaintext
    assert decrypt_token(ciphertext, key) == plaintext
