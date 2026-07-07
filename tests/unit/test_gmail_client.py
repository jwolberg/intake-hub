"""Unit tests for the Gmail read client (feat: solopreneur-ledger pivot, U7).

Covers the recursive MIME part-walker + base64url decoder (the classic pain
point — written characterization-first against a few real-shaped fixtures),
``StubGmailClient`` behavior (backfill pagination, history-based incremental
sync, the 404-resync signal, typed transient errors), and the ``HttpGmailClient``
REST path against an injected ``httpx`` transport, mirroring
``tests/unit/test_drive_client.py``.
"""

from __future__ import annotations

import base64

import httpx
import pytest
from backend.clients import (
    GmailClientError,
    GmailMessage,
    HttpGmailClient,
    StubGmailClient,
    decode_b64url,
    walk_parts,
)
from backend.clients.errors import GmailHistoryExpired


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode()


def _b64url_no_padding(raw: bytes) -> str:
    return _b64url(raw).rstrip("=")


# --- decode_b64url ------------------------------------------------------------


def test_decode_b64url_roundtrips_padded_input():
    raw = b"hello world"
    assert decode_b64url(_b64url(raw)) == raw


def test_decode_b64url_re_adds_missing_padding():
    raw = b"a receipt body that is not a multiple of 3 bytes!"
    stripped = _b64url_no_padding(raw)
    assert "=" not in stripped  # confirms the fixture actually needs padding back
    assert decode_b64url(stripped) == raw


# --- walk_parts -----------------------------------------------------------


def test_walk_parts_inline_html_receipt_no_attachment():
    # Covers AE6: multipart/alternative with text + html, no attachment.
    html = b"<html><body>Vendor: Acme Co, Total: $42.00</body></html>"
    text = b"Vendor: Acme Co, Total: $42.00"
    payload = {
        "mimeType": "multipart/alternative",
        "body": {},
        "parts": [
            {"mimeType": "text/plain", "body": {"data": _b64url(text)}},
            {"mimeType": "text/html", "body": {"data": _b64url(html)}},
        ],
    }
    body_text, body_html, attachments = walk_parts(payload)
    assert body_text == text.decode()
    assert body_html == html.decode()
    assert attachments == []


def test_walk_parts_pdf_attachment_message():
    payload = {
        "mimeType": "multipart/mixed",
        "body": {},
        "parts": [
            {
                "mimeType": "multipart/alternative",
                "body": {},
                "parts": [
                    {"mimeType": "text/plain", "body": {"data": _b64url(b"receipt attached")}},
                ],
            },
            {
                "mimeType": "application/pdf",
                "filename": "receipt.pdf",
                "body": {"attachmentId": "att-1", "size": 1234},
            },
        ],
    }
    body_text, body_html, attachments = walk_parts(payload)
    assert body_text == "receipt attached"
    assert body_html is None
    assert attachments == [
        {"filename": "receipt.pdf", "attachment_id": "att-1", "mime_type": "application/pdf"}
    ]


def test_walk_parts_text_plain_only_message():
    payload = {
        "mimeType": "text/plain",
        "body": {"data": _b64url(b"plain text receipt, no html, no attachment")},
    }
    body_text, body_html, attachments = walk_parts(payload)
    assert body_text == "plain text receipt, no html, no attachment"
    assert body_html is None
    assert attachments == []


def test_walk_parts_nested_multipart_mixed_with_missing_base64_padding():
    html = b"<html>nested alternative body that needs padding re-added</html>"
    stripped_html_data = _b64url_no_padding(html)
    payload = {
        "mimeType": "multipart/mixed",
        "body": {},
        "parts": [
            {
                "mimeType": "multipart/related",
                "body": {},
                "parts": [
                    {
                        "mimeType": "multipart/alternative",
                        "body": {},
                        "parts": [
                            {"mimeType": "text/html", "body": {"data": stripped_html_data}},
                        ],
                    },
                ],
            },
            {
                "mimeType": "image/png",
                "filename": "logo.png",
                "body": {"attachmentId": "att-logo", "size": 99},
            },
        ],
    }
    body_text, body_html, attachments = walk_parts(payload)
    assert body_html == html.decode()
    assert body_text is None
    assert len(attachments) == 1
    assert attachments[0]["attachment_id"] == "att-logo"


# --- StubGmailClient ------------------------------------------------------


def _msg(msg_id: str, *, body_html: str | None = "<p>hi</p>") -> GmailMessage:
    return GmailMessage(
        id=msg_id,
        thread_id=f"thread-{msg_id}",
        subject="Your receipt",
        sender="billing@vendor.example",
        date="Mon, 1 Jan 2026 00:00:00 -0800",
        body_text=None,
        body_html=body_html,
        attachments=[],
    )


def test_stub_get_message_and_attachment_roundtrip():
    stub = StubGmailClient()
    stub.add_message(_msg("m1"))
    msg = stub.get_message("m1")
    assert msg.id == "m1"
    assert msg.body_html == "<p>hi</p>"

    pdf_msg = GmailMessage(
        id="m2",
        thread_id="thread-m2",
        subject="Receipt with attachment",
        sender="billing@vendor.example",
        date="Tue, 2 Jan 2026 00:00:00 -0800",
        body_text="see attached",
        body_html=None,
        attachments=[
            {"filename": "receipt.pdf", "attachment_id": "att-1", "mime_type": "application/pdf"}
        ],
    )
    stub.add_message(pdf_msg, attachment_bytes={"att-1": b"%PDF-fake-bytes"})
    assert stub.get_attachment("m2", "att-1") == b"%PDF-fake-bytes"


def test_stub_list_messages_pagination():
    stub = StubGmailClient()
    stub.page_size = 1
    stub.add_message(_msg("m1"))
    stub.add_message(_msg("m2"))
    stub.add_message(_msg("m3"))

    ids1, token1 = stub.list_messages("after:2026/01/01")
    assert ids1 == ["m1"]
    assert token1 is not None

    ids2, token2 = stub.list_messages("after:2026/01/01", page_token=token1)
    assert ids2 == ["m2"]
    assert token2 is not None

    ids3, token3 = stub.list_messages("after:2026/01/01", page_token=token2)
    assert ids3 == ["m3"]
    assert token3 is None


def test_stub_history_list_returns_changed_ids_and_new_history_id():
    stub = StubGmailClient()
    stub.add_message(_msg("m1"))
    stub.seed_history("100", ["m1"], "101")
    changed, new_history_id = stub.history_list("100")
    assert changed == ["m1"]
    assert new_history_id == "101"


def test_stub_history_list_raises_resync_error_for_stale_history_id():
    stub = StubGmailClient()
    stub.expired_history_ids.add("stale-1")
    with pytest.raises(GmailClientError) as exc_info:
        stub.history_list("stale-1")
    assert exc_info.value.resync is True
    assert isinstance(exc_info.value, GmailHistoryExpired)


def test_stub_fail_toggle_raises_transient_error():
    stub = StubGmailClient()
    stub.add_message(_msg("m1"))
    stub.fail_next = 1
    with pytest.raises(GmailClientError):
        stub.list_messages("q")
    # fail_next is consumed; the next call succeeds.
    ids, _ = stub.list_messages("q")
    assert ids == ["m1"]

    stub.fail_always = True
    with pytest.raises(GmailClientError):
        stub.get_message("m1")
    with pytest.raises(GmailClientError):
        stub.get_attachment("m1", "att-x")


# --- HttpGmailClient (injected transport + token seam) ---------------------


class _TokenSpy:
    def __init__(self, token: str = "test-token") -> None:
        self.token = token
        self.calls = 0

    def __call__(self) -> str:
        self.calls += 1
        return self.token


def test_http_token_minting_is_lazy_and_needs_no_credentials():
    spy = _TokenSpy()
    HttpGmailClient(token_provider=spy)
    assert spy.calls == 0
    HttpGmailClient()


def test_http_list_messages_parses_response_and_sends_bearer_token():
    spy = _TokenSpy()
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers.get("Authorization")
        seen["q"] = request.url.params.get("q")
        return httpx.Response(
            200, json={"messages": [{"id": "m1"}, {"id": "m2"}], "nextPageToken": "tok-2"}
        )

    client = HttpGmailClient(
        token_provider=spy,
        client=httpx.Client(
            base_url=HttpGmailClient.BASE_URL, transport=httpx.MockTransport(handler)
        ),
    )
    ids, next_token = client.list_messages("after:2026/01/01")
    assert ids == ["m1", "m2"]
    assert next_token == "tok-2"
    assert spy.calls == 1
    assert seen["auth"] == "Bearer test-token"
    assert seen["q"] == "after:2026/01/01"


def test_http_history_list_collects_message_ids():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "history": [
                    {"messages": [{"id": "m1"}]},
                    {"messages": [{"id": "m2"}, {"id": "m1"}]},
                ],
                "historyId": "202",
            },
        )

    client = HttpGmailClient(
        token_provider=_TokenSpy(),
        client=httpx.Client(
            base_url=HttpGmailClient.BASE_URL, transport=httpx.MockTransport(handler)
        ),
    )
    ids, new_history_id = client.history_list("100")
    assert ids == ["m1", "m2"]
    assert new_history_id == "202"


def test_http_history_list_404_raises_resync_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404)

    client = HttpGmailClient(
        token_provider=_TokenSpy(),
        client=httpx.Client(
            base_url=HttpGmailClient.BASE_URL, transport=httpx.MockTransport(handler)
        ),
    )
    with pytest.raises(GmailClientError) as exc_info:
        client.history_list("stale-history-id")
    assert exc_info.value.resync is True
    assert isinstance(exc_info.value, GmailHistoryExpired)


def test_http_get_message_builds_gmail_message_from_payload():
    html = b"<html>Vendor: Acme, Total: $10.00</html>"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "id": "m1",
                "threadId": "t1",
                "payload": {
                    "headers": [
                        {"name": "Subject", "value": "Your receipt"},
                        {"name": "From", "value": "billing@vendor.example"},
                        {"name": "Date", "value": "Mon, 1 Jan 2026 00:00:00 -0800"},
                    ],
                    "mimeType": "text/html",
                    "body": {"data": _b64url(html)},
                },
            },
        )

    client = HttpGmailClient(
        token_provider=_TokenSpy(),
        client=httpx.Client(
            base_url=HttpGmailClient.BASE_URL, transport=httpx.MockTransport(handler)
        ),
    )
    msg = client.get_message("m1")
    assert msg.id == "m1"
    assert msg.thread_id == "t1"
    assert msg.subject == "Your receipt"
    assert msg.sender == "billing@vendor.example"
    assert msg.body_html == html.decode()


def test_http_get_attachment_decodes_bytes():
    raw = b"%PDF-1.4 fake bytes for testing"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": _b64url_no_padding(raw), "size": len(raw)})

    client = HttpGmailClient(
        token_provider=_TokenSpy(),
        client=httpx.Client(
            base_url=HttpGmailClient.BASE_URL, transport=httpx.MockTransport(handler)
        ),
    )
    assert client.get_attachment("m1", "att-1") == raw


def test_http_errors_are_wrapped_as_typed_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    client = HttpGmailClient(
        token_provider=_TokenSpy(),
        client=httpx.Client(
            base_url=HttpGmailClient.BASE_URL, transport=httpx.MockTransport(handler)
        ),
    )
    with pytest.raises(GmailClientError):
        client.list_messages("q")
