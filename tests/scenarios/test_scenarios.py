"""End-to-end ledger scenario suite (feat: solopreneur-ledger pivot).

One test per required scenario, run through the real orchestrator against the
in-process stub LLM + Sheets clients (no DB, no network). Each asserts the
terminal status/decision and the defining signal (Sheet row / exception type),
covering the ledger's three terminal outcomes: auto-file, hold, and a
persistent Sheet-write failure.
"""

from __future__ import annotations

from backend.clients import PassthroughLLMClient, StubSheetsClient
from backend.db.repository import InMemoryRepository
from backend.domain import Decision, InvoiceStatus
from backend.orchestrator import process

# A clean expense: clear vendor + a categorizable line + a total, so
# classification and categorization are both confident and it auto-files.
CLEAN_EXPENSE = {
    "source": {
        "channel": "email", "message_id": "m-clean",
        "subject": "Your receipt from Notion", "sender": "billing@notion.so",
        "attachment": "receipt.pdf",
    },
    "document": {
        "metadata": {
            "invoice_number": "N-1", "invoice_date": "2026-03-01",
            "vendor_name": "Notion", "currency": "USD", "total_amount": "10.00",
        },
        "line_items": [
            {"raw_description": "Notion subscription",
             "quantity": "1", "unit_price": "10.00", "total": "10.00"},
        ],
    },
}

# A document with a total but no recognizable category keyword → category
# cannot be assigned with confidence → held for the reviewer (no Sheet write).
UNCATEGORIZABLE = {
    "source": {"channel": "email", "message_id": "m-hold", "subject": "note",
               "sender": "someone@example.com"},
    "document": {
        "metadata": {"vendor_name": "Bob", "total_amount": "10.00"},
        "line_items": [{"raw_description": "thing", "quantity": "1",
                        "unit_price": "10.00", "total": "10.00"}],
    },
}


def _run(sample, sheets=None):
    repo = InMemoryRepository()
    sheets = sheets or StubSheetsClient()
    invoice = process(sample, repo, llm=PassthroughLLMClient(), sheets=sheets)
    return repo, sheets, invoice


def test_clean_expense_auto_files_to_the_ledger():
    """A clean expense with a categorizable line and a total → posted."""
    repo, sheets, invoice = _run(CLEAN_EXPENSE)
    assert invoice.status is InvoiceStatus.POSTED
    assert invoice.decision is Decision.SUBMIT
    assert len(sheets.rows) == 1
    assert repo.get_exceptions(invoice.id) == []


def test_uncategorizable_item_holds_without_writing_the_sheet():
    """No categorizable keyword → held for review, nothing filed."""
    repo, sheets, invoice = _run(UNCATEGORIZABLE)
    assert invoice.status is InvoiceStatus.HELD
    assert invoice.decision is Decision.HOLD
    assert sheets.rows == []
    exc_types = {e.type for e in repo.get_exceptions(invoice.id)}
    assert "low_category_confidence" in exc_types


def test_persistent_sheet_failure_marks_failed():
    """A Sheet append that never recovers → failed, item preserved for retry."""
    sheets = StubSheetsClient()
    sheets.fail_always = True
    repo, sheets, invoice = _run(CLEAN_EXPENSE, sheets=sheets)
    assert invoice.status is InvoiceStatus.FAILED
    assert sheets.rows == []
    exc_types = {e.type for e in repo.get_exceptions(invoice.id)}
    assert "sheet_write_failed" in exc_types
