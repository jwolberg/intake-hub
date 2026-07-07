"""Google Sheets ledger output client (feat: solopreneur-ledger pivot, U3).

Filed items are appended as rows to a user-owned Google Sheet — the ledger's
human-facing projection. The ``SheetsClient`` protocol exposes the one
operation the pipeline tail needs — append one row — mirroring the
``Protocol`` + ``Http…``/``Stub…`` shape used by the other external clients
(``backend/clients/drive.py``).

``HttpSheetsClient`` calls the Sheets v4 REST API over the existing ``httpx``
dependency and mints a service-account bearer token with ``google-auth``, using
the exact same injectable ``token_provider``/``client`` seam as
``HttpDriveClient`` so tests exercise it with no live credentials and no
network, and ``google-auth`` is imported lazily.

The Sheet itself is never the source of truth for "was this already posted" —
that's ``backend.db.repository``'s ``sheet_appends`` table (see
``append_filed_row`` below). A row can be recreated from the ledger; the
ledger cannot be reconstructed from the Sheet.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

import httpx

from .errors import SheetsClientError

if TYPE_CHECKING:
    from backend.domain import CategorizationResult, Invoice

# Column order for every ledger row (PRD §11 Ledger row); also written as the
# header row on first use of a fresh spreadsheet.
LEDGER_HEADERS = ["Type", "Category", "Vendor", "Date", "Amount", "Source"]
TAB_NAME = "Ledger"


def build_ledger_row(
    invoice: Invoice,
    categorization: CategorizationResult,
    source_ref: str | None,
) -> list[str]:
    """Build one ledger row for a filed item, columns matching ``LEDGER_HEADERS``.

    All values are rendered as strings (Sheets cells are text here); missing
    fields become empty strings rather than ``"None"``.
    """
    return [
        categorization.document_type.value.title(),
        categorization.category or "",
        invoice.metadata.vendor_name or "",
        invoice.metadata.invoice_date or "",
        str(invoice.metadata.total_amount) if invoice.metadata.total_amount is not None else "",
        source_ref or "",
    ]


class SheetsClient(Protocol):
    """The one Sheets operation the filing path depends on."""

    def append_row(self, values: list[str]) -> str:
        """Append ``values`` as a new row and return a row reference (e.g. an A1
        range like ``"Ledger!A5:F5"``). The client lazily ensures the spreadsheet
        and its header row exist on first append, or raises ``SheetsClientError``.
        """
        ...


class StubSheetsClient:
    """In-memory Sheets fake for tests — no network, no credentials.

    Appended rows land in :attr:`rows` (header not included). Set
    :attr:`fail_next` to make the next *N* :meth:`append_row` calls raise
    ``SheetsClientError`` (transient-failure/retry tests); set
    :attr:`fail_always` to make every call raise.
    """

    def __init__(self) -> None:
        self.rows: list[list[str]] = []
        self.fail_next: int = 0
        self.fail_always: bool = False

    def append_row(self, values: list[str]) -> str:
        if self.fail_always or self.fail_next > 0:
            if self.fail_next > 0:
                self.fail_next -= 1
            raise SheetsClientError("stub: append failed")
        self.rows.append(list(values))
        n = len(self.rows)
        return f"{TAB_NAME}!A{n}:F{n}"


class HttpSheetsClient:
    """Sheets v4 REST client authenticated as a service account.

    ``token_provider`` is a ``Callable[[], str]`` returning a bearer token; it
    defaults to a lazily-built ``google-auth`` service-account provider so
    constructing the client never imports ``google-auth`` or touches
    credentials. ``client`` is an optional ``httpx.Client`` so tests can inject
    an in-process transport. Any transport/HTTP error is re-raised as
    ``SheetsClientError``.

    If ``spreadsheet_id`` is ``None``, the first :meth:`append_row` creates a
    new spreadsheet (titled "Solopreneur Ledger") with a ``Ledger`` tab, caches
    the id, and writes the header row before appending the data row.
    """

    BASE_URL = "https://sheets.googleapis.com/v4"
    _SCOPES = ("https://www.googleapis.com/auth/drive.file",)

    def __init__(
        self,
        *,
        spreadsheet_id: str | None = None,
        credentials_file: str | None = None,
        credentials_info: dict | None = None,
        token_provider: Callable[[], str] | None = None,
        client: httpx.Client | None = None,
    ) -> None:
        self._spreadsheet_id = spreadsheet_id
        self._credentials_file = credentials_file
        self._credentials_info = credentials_info
        self._token_provider = token_provider
        self._sa_credentials = None  # built lazily on first token mint
        self._client = client or httpx.Client(base_url=self.BASE_URL, timeout=30.0)

    # --- auth ---------------------------------------------------------------

    def _token(self) -> str:
        if self._token_provider is not None:
            return self._token_provider()
        return self._mint_service_account_token()

    def _mint_service_account_token(self) -> str:
        # Lazy import: only the real service-account path needs google-auth.
        from google.auth.transport.requests import Request
        from google.oauth2 import service_account

        if self._sa_credentials is None:
            if self._credentials_info is not None:
                self._sa_credentials = service_account.Credentials.from_service_account_info(
                    self._credentials_info, scopes=list(self._SCOPES)
                )
            elif self._credentials_file is not None:
                self._sa_credentials = service_account.Credentials.from_service_account_file(
                    self._credentials_file, scopes=list(self._SCOPES)
                )
            else:
                raise SheetsClientError(
                    "HttpSheetsClient needs credentials_file or credentials_info "
                    "to mint a service-account token"
                )
        # Reuse the token for its ~1h lifetime; only refresh when it is missing or
        # expired, so one poll of N rows does not mint N tokens.
        if not self._sa_credentials.valid:
            self._sa_credentials.refresh(Request())
        return self._sa_credentials.token

    def _auth_headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._token()}"}

    # --- operations ---------------------------------------------------------

    def append_row(self, values: list[str]) -> str:
        self._ensure_spreadsheet()
        try:
            resp = self._client.post(
                f"/spreadsheets/{self._spreadsheet_id}/values/{TAB_NAME}!A1:append",
                params={"valueInputOption": "USER_ENTERED", "insertDataOption": "INSERT_ROWS"},
                json={"values": [values]},
                headers=self._auth_headers(),
            )
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            raise SheetsClientError(f"append_row failed: {exc}") from exc
        updates = resp.json().get("updates", {})
        return updates.get("updatedRange") or f"{TAB_NAME}!A:F"

    def _ensure_spreadsheet(self) -> None:
        # The header row is written exactly once, when this client *creates* a new
        # spreadsheet. A pre-existing spreadsheet (configured ``spreadsheet_id``)
        # already has its header, so we must NOT re-write it — a fresh client is
        # built per fetch request, and a per-append header write would append a
        # duplicate header row on every poll cycle.
        if self._spreadsheet_id is None:
            self._create_spreadsheet()

    def _create_spreadsheet(self) -> None:
        try:
            resp = self._client.post(
                "/spreadsheets",
                json={
                    "properties": {"title": "Solopreneur Ledger"},
                    "sheets": [{"properties": {"title": TAB_NAME}}],
                },
                headers=self._auth_headers(),
            )
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            raise SheetsClientError(f"create spreadsheet failed: {exc}") from exc
        self._spreadsheet_id = resp.json()["spreadsheetId"]
        self._write_header()

    def _write_header(self) -> None:
        try:
            resp = self._client.post(
                f"/spreadsheets/{self._spreadsheet_id}/values/{TAB_NAME}!A1:append",
                params={"valueInputOption": "USER_ENTERED", "insertDataOption": "INSERT_ROWS"},
                json={"values": [LEDGER_HEADERS]},
                headers=self._auth_headers(),
            )
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            raise SheetsClientError(f"write header failed: {exc}") from exc


@dataclass
class AppendOutcome:
    sheet_row_ref: str
    deduped: bool


def append_filed_row(
    repo,
    client: SheetsClient,
    *,
    idempotency_key: str,
    row: list[str],
) -> AppendOutcome:
    """Idempotent Sheet append gated by the Postgres dedup ledger. If the key is
    already recorded, returns the stored row ref as a no-op (deduped=True) — no
    second row. Otherwise appends (may raise SheetsClientError, retryable), then
    records the ref. The ledger, not the Sheet, is the source of truth.
    """
    if repo.is_appended(idempotency_key):
        existing = repo.get_append_ref(idempotency_key)
        return AppendOutcome(sheet_row_ref=existing or "", deduped=True)
    ref = client.append_row(row)
    repo.record_append(idempotency_key, ref)
    return AppendOutcome(sheet_row_ref=ref, deduped=False)
