"""Gmail read client (feat: solopreneur-ledger pivot, U7).

Reads a connected mailbox as one of the AI's inbox sources — a headline
replacement for/companion to the Drive folder-watcher. The ``GmailClient``
protocol exposes the four operations the intake path needs — list/backfill
message ids, incrementally sync via ``history.list``, fetch one message's
parsed body/attachment metadata, and fetch one attachment's bytes — mirroring
the ``Protocol`` + ``Http…``/``Stub…`` shape used by the other external
clients (``backend/clients/drive.py``).

Personal Gmail cannot use a service account (no domain-wide delegation), so
``HttpGmailClient`` authenticates as the mailbox owner via installed-app
user-OAuth: a refresh token (minted once, interactively, by
``backend/tools/gmail_oauth_setup.py``) is exchanged for short-lived access
tokens at runtime. Both the token provider and the ``httpx`` transport are
injectable, so tests exercise the client with no live credentials and no
network, and ``google-auth`` is imported lazily (only the real-provider path
pulls it in).

The MIME part-walker (``walk_parts``) and the base64url decoder
(``decode_b64url``) are the classic Gmail-API pain point: message bodies are
base64url-encoded, missing their padding, and nested arbitrarily deep across
``multipart/mixed``/``multipart/alternative``/``multipart/related`` parts.
Both are plain functions, independently testable against fixture payloads.
"""

from __future__ import annotations

import base64
import binascii
from collections.abc import Callable
from typing import Protocol

import httpx
from pydantic import BaseModel, Field

from .errors import GmailClientError, GmailHistoryExpired

# Gmail API restricted scope: read-only access to the mailbox.
GMAIL_READONLY_SCOPE = "https://www.googleapis.com/auth/gmail.readonly"


class GmailAttachment(BaseModel):
    """Attachment metadata only — bytes are fetched separately via
    :meth:`GmailClient.get_attachment` so listing/parsing a message never pulls
    down large payloads it doesn't need."""

    filename: str
    attachment_id: str
    mime_type: str


class GmailMessage(BaseModel):
    """One parsed Gmail message (headers + bodies + attachment metadata)."""

    id: str
    thread_id: str
    subject: str = ""
    sender: str = ""
    date: str = ""
    body_text: str | None = None
    body_html: str | None = None
    attachments: list[GmailAttachment] = Field(default_factory=list)


# --- MIME helpers -------------------------------------------------------------


def decode_b64url(data: str) -> bytes:
    """Decode a Gmail base64url string, re-adding the padding Gmail omits.

    Gmail's API returns ``body.data``/attachment ``data`` as unpadded
    base64url — ``base64.urlsafe_b64decode`` requires a length that is a
    multiple of 4, so this pads with ``'='`` before decoding.
    """
    padding = (-len(data)) % 4
    return base64.urlsafe_b64decode(data + "=" * padding)


def walk_parts(payload: dict) -> tuple[str | None, str | None, list[dict]]:
    """Recursively walk a Gmail message ``payload``, collecting bodies + attachments.

    Returns ``(body_text, body_html, attachments)``: the first decoded
    ``text/plain`` part as ``body_text``, the first decoded ``text/html`` part
    as ``body_html`` (either may be ``None`` if absent), and every part with a
    ``filename`` and a ``body.attachmentId`` as an attachment descriptor
    ``{"filename", "attachment_id", "mime_type"}`` (bytes fetched separately via
    ``get_attachment``). Handles arbitrarily nested ``multipart/*`` parts; a
    single-part (non-multipart) message is walked as its own leaf.
    """
    body_text: str | None = None
    body_html: str | None = None
    attachments: list[dict] = []

    def _walk(part: dict) -> None:
        nonlocal body_text, body_html
        mime_type = part.get("mimeType", "")
        body = part.get("body") or {}
        filename = part.get("filename") or ""

        if filename and body.get("attachmentId"):
            attachments.append(
                {
                    "filename": filename,
                    "attachment_id": body["attachmentId"],
                    "mime_type": mime_type,
                }
            )
        elif mime_type == "text/plain" and body_text is None and body.get("data"):
            body_text = decode_b64url(body["data"]).decode("utf-8", errors="replace")
        elif mime_type == "text/html" and body_html is None and body.get("data"):
            body_html = decode_b64url(body["data"]).decode("utf-8", errors="replace")

        for sub_part in part.get("parts") or []:
            _walk(sub_part)

    _walk(payload)
    return body_text, body_html, attachments


# --- protocol -------------------------------------------------------------


class GmailClient(Protocol):
    """The four Gmail operations the intake path depends on."""

    def list_messages(
        self, query: str, *, page_token: str | None = None
    ) -> tuple[list[str], str | None]:
        """Return one page of message ids matching ``query`` (e.g. ``after:``
        for backfill), as ``(message_ids, next_page_token)``. ``next_page_token``
        is ``None`` on the last page."""
        ...

    def history_list(self, start_history_id: str) -> tuple[list[str], str | None]:
        """Return ``(changed_message_ids, new_history_id)`` since
        ``start_history_id``. Raises ``GmailClientError`` with ``.resync=True``
        (a ``GmailHistoryExpired``) when Gmail 404s a stale ``start_history_id``,
        so the caller can fall back to a full ``list_messages`` resync."""
        ...

    def get_message(self, message_id: str, *, fmt: str = "full") -> GmailMessage:
        """Fetch and parse one message. ``fmt="metadata"`` is cheap (headers
        only, for the receipt-detection filter); ``fmt="full"`` includes bodies
        and attachment metadata for survivors."""
        ...

    def get_attachment(self, message_id: str, attachment_id: str) -> bytes:
        """Return the raw bytes of one attachment, or raise ``GmailClientError``."""
        ...


# --- stub -------------------------------------------------------------------


class StubGmailClient:
    """In-memory Gmail fake for tests — no network, no credentials.

    Seed messages with :meth:`add_message`; :meth:`list_messages` paginates the
    seeded ids in insertion order (:attr:`page_size` controls the page cap, so
    tests can exercise multi-page backfill with as few as two messages).
    :meth:`history_list` looks up deltas seeded via :meth:`seed_history`, or
    raises a resync-signalling ``GmailHistoryExpired`` for any id in
    :attr:`expired_history_ids`. Set :attr:`fail_next`/:attr:`fail_always` to
    make the next call(s) raise a plain ``GmailClientError`` (transient-error
    tests) — checked before the resync/lookup logic on every method.
    """

    def __init__(self) -> None:
        self._messages: dict[str, GmailMessage] = {}
        self._order: list[str] = []
        self._attachment_bytes: dict[tuple[str, str], bytes] = {}
        self._history_deltas: dict[str, tuple[list[str], str]] = {}
        self.expired_history_ids: set[str] = set()
        self.current_history_id: str = "1"
        self.page_size: int = 50
        self.fail_next: int = 0
        self.fail_always: bool = False

    def add_message(
        self, message: GmailMessage, *, attachment_bytes: dict[str, bytes] | None = None
    ) -> None:
        if message.id not in self._messages:
            self._order.append(message.id)
        self._messages[message.id] = message
        for attachment_id, data in (attachment_bytes or {}).items():
            self._attachment_bytes[(message.id, attachment_id)] = data

    def seed_history(
        self, start_history_id: str, changed_message_ids: list[str], new_history_id: str
    ) -> None:
        self._history_deltas[start_history_id] = (list(changed_message_ids), new_history_id)

    def _maybe_fail(self) -> None:
        if self.fail_always:
            raise GmailClientError("stub: forced failure")
        if self.fail_next > 0:
            self.fail_next -= 1
            raise GmailClientError("stub: forced failure")

    def list_messages(
        self, query: str, *, page_token: str | None = None
    ) -> tuple[list[str], str | None]:
        self._maybe_fail()
        start = int(page_token) if page_token else 0
        end = start + self.page_size
        page_ids = self._order[start:end]
        next_token = str(end) if end < len(self._order) else None
        return page_ids, next_token

    def history_list(self, start_history_id: str) -> tuple[list[str], str | None]:
        self._maybe_fail()
        if start_history_id in self.expired_history_ids:
            raise GmailHistoryExpired(f"stub: historyId {start_history_id} expired")
        changed, new_history_id = self._history_deltas.get(
            start_history_id, ([], self.current_history_id)
        )
        return changed, new_history_id

    def get_message(self, message_id: str, *, fmt: str = "full") -> GmailMessage:
        self._maybe_fail()
        try:
            return self._messages[message_id]
        except KeyError as exc:
            raise GmailClientError(f"stub: no such message {message_id}") from exc

    def get_attachment(self, message_id: str, attachment_id: str) -> bytes:
        self._maybe_fail()
        try:
            return self._attachment_bytes[(message_id, attachment_id)]
        except KeyError as exc:
            raise GmailClientError(
                f"stub: no such attachment {attachment_id} on {message_id}"
            ) from exc


# --- http ---------------------------------------------------------------


class HttpGmailClient:
    """Gmail v1 REST client authenticated as the mailbox owner (user-OAuth).

    ``token_provider`` is a ``Callable[[], str]`` returning a bearer token; it
    defaults to a lazily-built ``google-auth`` user-OAuth provider (refresh
    token → short-lived access token) so constructing the client never imports
    ``google-auth`` or touches credentials. ``client`` is an optional
    ``httpx.Client`` so tests can inject an in-process transport. Any
    transport/HTTP error is re-raised as ``GmailClientError`` (429/5xx included
    — retry is the caller's responsibility, mirroring ``HttpDriveClient``).
    """

    BASE_URL = "https://gmail.googleapis.com/gmail/v1"
    _TOKEN_URI = "https://oauth2.googleapis.com/token"
    _SCOPES = (GMAIL_READONLY_SCOPE,)

    def __init__(
        self,
        *,
        refresh_token: str | None = None,
        client_id: str | None = None,
        client_secret: str | None = None,
        token_provider: Callable[[], str] | None = None,
        client: httpx.Client | None = None,
    ) -> None:
        self._refresh_token = refresh_token
        self._client_id = client_id
        self._client_secret = client_secret
        self._token_provider = token_provider
        self._user_credentials = None  # built lazily on first token mint
        self._client = client or httpx.Client(base_url=self.BASE_URL, timeout=30.0)

    # --- auth ---------------------------------------------------------------

    def _token(self) -> str:
        if self._token_provider is not None:
            return self._token_provider()
        return self._mint_user_oauth_token()

    def _mint_user_oauth_token(self) -> str:
        # Lazy import: only the real user-OAuth path needs google-auth.
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials

        if self._user_credentials is None:
            if not (self._refresh_token and self._client_id and self._client_secret):
                raise GmailClientError(
                    "HttpGmailClient needs refresh_token, client_id, and client_secret "
                    "to mint a user-OAuth token"
                )
            self._user_credentials = Credentials(
                token=None,
                refresh_token=self._refresh_token,
                client_id=self._client_id,
                client_secret=self._client_secret,
                token_uri=self._TOKEN_URI,
                scopes=list(self._SCOPES),
            )
        # Reuse the access token until it expires; only refresh when missing or
        # expired, so one poll of N messages does not mint N tokens.
        if not self._user_credentials.valid:
            self._user_credentials.refresh(Request())
        return self._user_credentials.token

    def _auth_headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._token()}"}

    # --- operations ---------------------------------------------------------

    def list_messages(
        self, query: str, *, page_token: str | None = None
    ) -> tuple[list[str], str | None]:
        params: dict[str, str] = {"q": query}
        if page_token:
            params["pageToken"] = page_token
        try:
            resp = self._client.get(
                "/users/me/messages", params=params, headers=self._auth_headers()
            )
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            raise GmailClientError(f"list_messages failed: {exc}") from exc
        data = resp.json()
        ids = [m["id"] for m in data.get("messages", [])]
        return ids, data.get("nextPageToken")

    def history_list(self, start_history_id: str) -> tuple[list[str], str | None]:
        try:
            resp = self._client.get(
                "/users/me/history",
                params={"startHistoryId": start_history_id},
                headers=self._auth_headers(),
            )
            if resp.status_code == 404:
                raise GmailHistoryExpired(
                    f"history_list: startHistoryId {start_history_id} expired (404)"
                )
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            raise GmailClientError(f"history_list failed: {exc}") from exc
        data = resp.json()
        ids: list[str] = []
        seen: set[str] = set()
        for entry in data.get("history", []):
            for m in entry.get("messages", []):
                mid = m["id"]
                if mid not in seen:
                    seen.add(mid)
                    ids.append(mid)
        return ids, data.get("historyId")

    def get_message(self, message_id: str, *, fmt: str = "full") -> GmailMessage:
        params: dict[str, str] = {"format": fmt}
        if fmt == "metadata":
            params["metadataHeaders"] = "Subject,From,Date"
        try:
            resp = self._client.get(
                f"/users/me/messages/{message_id}",
                params=params,
                headers=self._auth_headers(),
            )
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            raise GmailClientError(f"get_message failed for {message_id}: {exc}") from exc
        # Parsing/decoding can fail on a malformed message (bad base64, missing keys);
        # surface it as GmailClientError so the fetch loop isolates this one message
        # instead of aborting the whole poll.
        try:
            data = resp.json()
            payload = data.get("payload", {}) or {}
            headers = {h["name"]: h["value"] for h in payload.get("headers", [])}
            body_text, body_html, attachments = walk_parts(payload)
        except (ValueError, KeyError, TypeError, binascii.Error) as exc:
            raise GmailClientError(f"get_message parse failed for {message_id}: {exc}") from exc
        return GmailMessage(
            id=data.get("id", message_id),
            thread_id=data.get("threadId", ""),
            subject=headers.get("Subject", ""),
            sender=headers.get("From", ""),
            date=headers.get("Date", ""),
            body_text=body_text,
            body_html=body_html,
            attachments=attachments,
        )

    def get_attachment(self, message_id: str, attachment_id: str) -> bytes:
        try:
            resp = self._client.get(
                f"/users/me/messages/{message_id}/attachments/{attachment_id}",
                headers=self._auth_headers(),
            )
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            raise GmailClientError(
                f"get_attachment failed for {message_id}/{attachment_id}: {exc}"
            ) from exc
        return decode_b64url(resp.json()["data"])
