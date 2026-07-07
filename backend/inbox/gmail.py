"""Gmail inbox provider (feat: solopreneur-ledger pivot, U8).

``GmailInbox`` is a real ``InboxClient`` backed by a connected mailbox (the
``GmailClient`` contract from U7): a headline replacement for/companion to the
Drive folder-watcher and the offline ``MockInbox``. It runs a **receipt
filter** (``backend.receipts.classify_receipt``, U6) on each candidate message
*before* fetching its full body — a clear non-receipt is dropped with only its
id/sender/subject retained (R17 minimal retention; the body is never fetched
for a drop, let alone persisted) — and maps survivors to ``InboxMessage`` so
nothing downstream of ``message_to_sample`` changes.

Sync strategy (R3/R14):

* **First connect** (no stored ``historyId``): a full ``list_messages``
  backfill to the configured tax year's Jan 1, paginating until exhausted.
* **Subsequent fetches**: ``history_list`` deltas from the stored
  ``historyId``; a stale id (``GmailHistoryExpired``) falls back to a full
  backfill, so a missed sync window degrades to "re-scan" rather than
  "silently drop mail."

``on_processed`` is a documented no-op: labeling/archiving a message needs
Gmail's *modify* scope, and this integration deliberately only requests
``gmail.readonly`` (least privilege, R20) — see its docstring.
"""

from __future__ import annotations

import base64
import logging
from typing import TYPE_CHECKING, Protocol

from backend.clients.errors import GmailClientError, GmailHistoryExpired
from backend.receipts import classify_receipt

from . import InboxMessage

if TYPE_CHECKING:
    from backend.clients.gmail import GmailClient
    from backend.clients.llm import LLMClient
    from backend.domain.models import Invoice

logger = logging.getLogger(__name__)

# A non-receipt verdict at/above this confidence is dropped outright; anything
# below (including the "ambiguous, needs review" 0.4 fallback from
# classify_receipt) is treated as a receipt so it flows on to be held for
# human review rather than silently discarded (see backend.receipts).
_DROP_CONFIDENCE_THRESHOLD = 0.7

# Synthetic startHistoryId used only to *bootstrap* the incremental cursor
# right after a backfill (see ``_bootstrap_history_id``); never a message id.
_BOOTSTRAP_SENTINEL = "0"


class GmailSyncState(Protocol):
    """Persistence seam ``GmailInbox`` needs beyond fetch/classify/parse.

    Deliberately narrow (four methods) rather than the full ``Repository``
    Protocol, but shaped so the app's real repository satisfies it directly —
    ``is_seen``/``mark_seen`` already exist on ``Repository``, and
    ``get_sync_history_id``/``set_sync_history_id`` are added alongside this
    provider (``backend/db/repository.py``). Production wiring
    (``_build_gmail_inbox``) passes the app's repository as ``sync_state``
    with no adapter; tests can pass an ``InMemoryRepository()`` for the same
    reason, or any lightweight fake/dict-backed stand-in that implements these
    four methods.
    """

    def is_seen(self, message_id: str) -> bool: ...
    def mark_seen(self, message_id: str) -> None: ...
    def get_sync_history_id(self) -> str | None: ...
    def set_sync_history_id(self, history_id: str) -> None: ...


class GmailInbox:
    """Inbox backed by a connected Gmail mailbox (``InboxClient``).

    ``client`` is the U7 ``GmailClient`` (``StubGmailClient`` in tests,
    ``HttpGmailClient`` in production). ``llm`` is passed straight through to
    ``classify_receipt`` for the ambiguous-band advisory call (``None`` is
    fine — ambiguous messages then default to a low-confidence "needs
    review" verdict, never a drop). ``tax_year`` anchors the first-connect
    backfill query (``after:<tax_year>/01/01``). ``label`` is the Gmail label
    ``on_processed`` would apply if the integration had *modify* scope (it
    doesn't yet — see ``on_processed``); kept as a constructor field so the
    intent is configurable even though it isn't actionable today.

    ``history_id`` optionally seeds the incremental cursor at construction
    (mainly for tests that want to force the "subsequent fetch" branch
    without a prior real backfill); when omitted, the cursor is read from
    ``sync_state.get_sync_history_id()`` on first use.
    """

    def __init__(
        self,
        client: GmailClient,
        *,
        llm: LLMClient | None = None,
        tax_year: int,
        label: str | None = None,
        sync_state: GmailSyncState,
        history_id: str | None = None,
    ) -> None:
        self._client = client
        self._llm = llm
        self._tax_year = tax_year
        self._label = label
        self._sync_state = sync_state
        self._history_id = history_id

    def fetch_messages(self) -> list[InboxMessage]:
        """Backfill (first connect) or delta-sync (subsequent), then classify.

        Every candidate id is checked against ``sync_state.is_seen`` up front
        so a re-fetch is idempotent even for ids a delta happens to repeat.
        Non-receipts are dropped here (marked seen, body never fetched); the
        rest are fetched in full and returned as ``InboxMessage``s for the
        generic fetch route to process (which applies its own
        ``is_seen``/``mark_seen`` around ``process()`` — see
        ``backend/api/main.py``).
        """
        stored_history_id = self._history_id or self._sync_state.get_sync_history_id()
        if stored_history_id is None:
            candidate_ids, new_history_id = self._backfill()
        else:
            try:
                candidate_ids, new_history_id = self._client.history_list(stored_history_id)
            except GmailHistoryExpired:
                logger.info(
                    "gmail: historyId %s expired; falling back to a full backfill",
                    stored_history_id,
                )
                candidate_ids, new_history_id = self._backfill()

        messages: list[InboxMessage] = []
        for message_id in candidate_ids:
            if self._sync_state.is_seen(message_id):
                continue
            message = self._classify_and_fetch(message_id)
            if message is not None:
                messages.append(message)

        if new_history_id:
            self._sync_state.set_sync_history_id(new_history_id)
            self._history_id = new_history_id
        return messages

    def on_processed(self, message: InboxMessage, invoice: Invoice) -> None:
        """No-op: labeling requires Gmail *modify* scope, which this integration
        does not request.

        ``GmailClient`` is deliberately read-only (``gmail.readonly``, least
        privilege — R20): a modify/label call is not available on the
        contract, and requesting broader access just to move a processed
        message into a label is not worth the added scope for an MVP. This is
        the Gmail analog of ``DriveInbox.on_processed`` (which *does* move the
        file, because the Drive integration already holds write access), but
        deliberately does **not** claim to label — it only logs, so a reader
        of the fetch route's output is never misled into thinking the mailbox
        was touched.
        """
        logger.info(
            "gmail: %s processed (status=%s) — label '%s' not applied "
            "(gmail.readonly has no modify scope)",
            message.message_id, invoice.status, self._label,
        )

    # --- sync -----------------------------------------------------------

    def _backfill(self) -> tuple[list[str], str | None]:
        """Full ``list_messages`` backfill to the configured tax year's Jan 1.

        Paginates until ``next_page_token`` is ``None``. Afterwards attempts
        to bootstrap the incremental cursor (see ``_bootstrap_history_id``) so
        the *next* fetch can use ``history_list`` deltas instead of repeating
        this full scan.
        """
        query = f"after:{self._tax_year}/01/01"
        ids: list[str] = []
        page_token: str | None = None
        while True:
            page_ids, next_token = self._client.list_messages(query, page_token=page_token)
            ids.extend(page_ids)
            if next_token is None:
                break
            page_token = next_token
        return ids, self._bootstrap_history_id()

    def _bootstrap_history_id(self) -> str | None:
        """Learn the mailbox's current ``historyId`` right after a backfill.

        The ``GmailClient`` contract (U7) has no direct "what's the current
        historyId" operation — only ``history_list(start_history_id)``. This
        probes it with a synthetic sentinel purely to read back its returned
        cursor; the changed-ids it reports are discarded (the backfill just
        performed already covers them). If the sentinel is itself treated as
        expired, this degrades gracefully to ``None`` — the next fetch simply
        backfills again rather than raising, which is safe (never misses
        mail) though not maximally efficient. A real Gmail mailbox's history
        retention window (~1 week) means this sentinel-based bootstrap is not
        guaranteed to succeed in production; see the U8 implementation notes
        for why a dedicated "current historyId" operation on ``GmailClient``
        would be a cleaner long-term fix.
        """
        try:
            _, history_id = self._client.history_list(_BOOTSTRAP_SENTINEL)
        except GmailHistoryExpired:
            logger.info(
                "gmail: could not bootstrap a historyId after backfill; "
                "next fetch will backfill again"
            )
            return None
        return history_id

    # --- classify + fetch -------------------------------------------------

    def _classify_and_fetch(self, message_id: str) -> InboxMessage | None:
        """Cheap metadata fetch + classify; drop non-receipts before a full fetch.

        A transient ``GmailClientError`` on the metadata call isolates this
        one message (logged, skipped) rather than aborting the whole poll —
        mirroring ``DriveInbox.fetch_messages``' per-file isolation. Because
        Gmail's incremental sync is cursor-based (not a re-listable folder), a
        message dropped this way will not reappear via ``history_list`` once
        the cursor advances past it; it *would* reappear on a future full
        backfill (e.g. after a resync).
        """
        try:
            meta = self._client.get_message(message_id, fmt="metadata")
        except GmailClientError:
            logger.warning("gmail: skipping %s (metadata fetch failed)", message_id)
            return None

        attachment_name = meta.attachments[0].filename if meta.attachments else None
        sample = {
            "source": {
                "message_id": meta.id,
                "subject": meta.subject or None,
                "sender": meta.sender or None,
                "attachment": attachment_name,
            }
        }
        verdict = classify_receipt(sample, llm=self._llm)

        if not verdict.is_receipt and verdict.confidence >= _DROP_CONFIDENCE_THRESHOLD:
            # R17 minimal retention: only id/sender/subject/verdict are ever
            # touched for a drop — the body is never fetched, let alone kept.
            logger.info(
                "gmail: dropping non-receipt id=%s sender=%s subject=%r "
                "confidence=%.2f reason=%s",
                verdict.message_id, verdict.sender, verdict.subject,
                verdict.confidence, verdict.reason,
            )
            self._sync_state.mark_seen(message_id)
            return None

        return self._fetch_full(message_id)

    def _fetch_full(self, message_id: str) -> InboxMessage | None:
        """Fetch the full message and map it to an ``InboxMessage``.

        ``body`` carries the HTML body if present (falling back to plain
        text) so an inline-HTML-only receipt still parses. A PDF attachment's
        bytes are fetched and carried as base64 in ``attachment_b64`` — the
        same field the orchestrator's cloud path already reads
        (``backend.orchestrator._is_real_pdf``/``_store_source_pdf``,
        ``backend.parser.parse``) — so a Gmail PDF attachment takes the exact
        same real-PDF extraction branch a Drive/cloud-seeded PDF does.
        """
        try:
            full = self._client.get_message(message_id, fmt="full")
        except GmailClientError:
            logger.warning("gmail: skipping %s (full fetch failed)", message_id)
            return None

        body = full.body_html or full.body_text
        attachment_name: str | None = None
        attachment_b64: str | None = None
        pdf_attachment = next(
            (
                a for a in full.attachments
                if a.mime_type == "application/pdf" or a.filename.lower().endswith(".pdf")
            ),
            None,
        )
        if pdf_attachment is not None:
            try:
                data = self._client.get_attachment(message_id, pdf_attachment.attachment_id)
            except GmailClientError:
                logger.warning(
                    "gmail: could not fetch attachment %s on %s (falling back to body)",
                    pdf_attachment.filename, message_id,
                )
            else:
                attachment_name = pdf_attachment.filename
                attachment_b64 = base64.b64encode(data).decode()

        return InboxMessage(
            message_id=full.id,
            subject=full.subject or None,
            sender=full.sender or None,
            attachment=attachment_name,
            attachment_b64=attachment_b64,
            body=body,
        )
