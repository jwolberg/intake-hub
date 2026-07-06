"""Typed client errors.

Failures are distinct because they map to different decisions downstream
(ARCHITECTURE.md §7): a transport/timeout error is a *retryable* failure, while a
missing catalog for a resolved scope is a *hold* reason.
"""

from __future__ import annotations


class ReferenceClientError(Exception):
    """Base error for the MCP reference client."""


class ReferenceUnavailable(ReferenceClientError):
    """Transport error or timeout reaching the reference API (retryable)."""


class CatalogNotFound(ReferenceClientError):
    """No catalog exists for the resolved sponsor+study scope (hold reason)."""


class ClinRunClientError(Exception):
    """Base error for the ClinRun submission client."""


class SubmissionFailed(ClinRunClientError):
    """Backend rejected or could not accept the submission (retryable)."""


class DriveClientError(Exception):
    """Base error for the Google Drive intake client.

    A list/download/move failure is surfaced as this typed error (never a raw
    ``httpx``/transport exception) so the fetch loop can isolate a single bad file
    without aborting the whole poll (see ``fetch_inbox``).
    """


class SheetsClientError(Exception):
    """Base error for the Google Sheets ledger client.

    A transient transport/HTTP error surfaced here is retryable (mirrors
    ``SubmissionFailed``), so the orchestrator can retry an append before holding
    the item.
    """
