"""Typed client errors.

Failures are distinct because they map to different decisions downstream
(ARCHITECTURE.md §7): a transport/timeout error is a *retryable* failure.
"""

from __future__ import annotations


class DriveClientError(Exception):
    """Base error for the Google Drive intake client.

    A list/download/move failure is surfaced as this typed error (never a raw
    ``httpx``/transport exception) so the fetch loop can isolate a single bad file
    without aborting the whole poll (see ``fetch_inbox``).
    """


class SheetsClientError(Exception):
    """Base error for the Google Sheets ledger client.

    A transient transport/HTTP error surfaced here is retryable, so the
    orchestrator can retry an append before holding the item.
    """


class GmailClientError(Exception):
    """Base error for the Gmail read client.

    A transient transport/HTTP error is surfaced here so the fetch loop can
    isolate one bad message without aborting the whole poll (retryable). Carries
    a ``resync`` flag (default ``False``) so callers can detect the specific
    "the incremental sync position is gone, fall back to a full backfill" case
    either by checking ``.resync`` or by catching :class:`GmailHistoryExpired`.
    """

    def __init__(self, message: str, *, resync: bool = False) -> None:
        super().__init__(message)
        self.resync = resync


class GmailHistoryExpired(GmailClientError):
    """Raised by ``history_list`` when Gmail 404s a stale ``startHistoryId``.

    Gmail only retains history records for ~1 week; once a stored ``historyId``
    ages out, the API returns 404 and the caller must fall back to a full
    ``messages.list`` resync rather than treating this as a generic transient
    failure. Always carries ``resync=True``.
    """

    def __init__(self, message: str) -> None:
        super().__init__(message, resync=True)
