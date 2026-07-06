"""Unit tests for the Google Sheets ledger client (feat: solopreneur-ledger
pivot, U3).

Covers ``build_ledger_row`` column ordering, ``StubSheetsClient`` behavior
(append + typed transient/persistent failures), and the ``HttpSheetsClient``
REST path (spreadsheet auto-create + header + append) against an injected
``httpx`` transport — mirrors ``tests/unit/test_drive_client.py``.
"""

from decimal import Decimal

import httpx
import pytest
from backend.clients import (
    LEDGER_HEADERS,
    HttpSheetsClient,
    StubSheetsClient,
    build_ledger_row,
)
from backend.clients.errors import SheetsClientError
from backend.clients.sheets import TAB_NAME
from backend.domain import CategorizationResult, DocumentType, Invoice, InvoiceMetadata

# --- build_ledger_row --------------------------------------------------------


def test_build_ledger_row_orders_columns_for_a_filed_expense():
    invoice = Invoice(
        metadata=InvoiceMetadata(
            vendor_name="Acme Software",
            invoice_date="2026-06-01",
            total_amount=Decimal("49.99"),
        )
    )
    categorization = CategorizationResult(
        invoice_id=invoice.id,
        document_type=DocumentType.EXPENSE,
        document_type_confidence=0.95,
        category="Office Expense",
        category_confidence=0.9,
    )
    row = build_ledger_row(invoice, categorization, "gmail:msg-1")
    assert row == [
        "Expense", "Office Expense", "Acme Software", "2026-06-01", "49.99", "gmail:msg-1",
    ]
    assert LEDGER_HEADERS == ["Type", "Category", "Vendor", "Date", "Amount", "Source"]


def test_build_ledger_row_uses_empty_strings_for_missing_fields():
    invoice = Invoice(metadata=InvoiceMetadata())
    categorization = CategorizationResult(invoice_id=invoice.id)
    row = build_ledger_row(invoice, categorization, None)
    assert row == ["Unknown", "", "", "", "", ""]


# --- StubSheetsClient --------------------------------------------------------


def test_stub_append_row_returns_incrementing_a1_range():
    stub = StubSheetsClient()
    ref1 = stub.append_row(["a", "b"])
    ref2 = stub.append_row(["c", "d"])
    assert stub.rows == [["a", "b"], ["c", "d"]]
    assert ref1 == f"{TAB_NAME}!A1:F1"
    assert ref2 == f"{TAB_NAME}!A2:F2"


def test_stub_fail_next_raises_transient_error_then_recovers():
    stub = StubSheetsClient()
    stub.fail_next = 1
    with pytest.raises(SheetsClientError):
        stub.append_row(["a"])
    # Recovers on the next call — no lingering failure state.
    stub.append_row(["a"])
    assert stub.rows == [["a"]]


def test_stub_fail_always_raises_persistently():
    stub = StubSheetsClient()
    stub.fail_always = True
    with pytest.raises(SheetsClientError):
        stub.append_row(["a"])
    with pytest.raises(SheetsClientError):
        stub.append_row(["a"])
    assert stub.rows == []


# --- HttpSheetsClient (injected transport + token seam) ---------------------


class _TokenSpy:
    def __init__(self, token: str = "test-token") -> None:
        self.token = token
        self.calls = 0

    def __call__(self) -> str:
        self.calls += 1
        return self.token


def test_http_append_row_creates_spreadsheet_writes_header_then_appends():
    calls: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, request.url.path))
        if request.url.path.endswith("/spreadsheets"):
            return httpx.Response(200, json={"spreadsheetId": "sheet-123"})
        if request.url.path.endswith(f"/values/{TAB_NAME}!A1:append"):
            return httpx.Response(
                200,
                json={"updates": {"updatedRange": f"{TAB_NAME}!A2:F2"}},
            )
        return httpx.Response(400)

    client = HttpSheetsClient(
        token_provider=_TokenSpy(),
        client=httpx.Client(
            base_url=HttpSheetsClient.BASE_URL, transport=httpx.MockTransport(handler)
        ),
    )
    ref = client.append_row(["Expense", "Office", "Acme", "2026-06-01", "49.99", "src"])

    assert ref == f"{TAB_NAME}!A2:F2"
    assert any(m == "POST" and p.endswith("/spreadsheets") for m, p in calls)
    # Header + data append both hit the values:append endpoint.
    append_calls = [c for c in calls if c[1].endswith(f"/values/{TAB_NAME}!A1:append")]
    assert len(append_calls) == 2


def test_http_append_row_reuses_existing_spreadsheet_id_no_create():
    calls: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, request.url.path))
        return httpx.Response(
            200, json={"updates": {"updatedRange": f"{TAB_NAME}!A2:F2"}}
        )

    client = HttpSheetsClient(
        spreadsheet_id="existing-sheet",
        token_provider=_TokenSpy(),
        client=httpx.Client(
            base_url=HttpSheetsClient.BASE_URL, transport=httpx.MockTransport(handler)
        ),
    )
    client.append_row(["a"])
    assert all(c[1] != "/spreadsheets" for c in calls)


def test_http_errors_are_wrapped_as_typed_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    client = HttpSheetsClient(
        spreadsheet_id="existing-sheet",
        token_provider=_TokenSpy(),
        client=httpx.Client(
            base_url=HttpSheetsClient.BASE_URL, transport=httpx.MockTransport(handler)
        ),
    )
    with pytest.raises(SheetsClientError):
        client.append_row(["a"])
