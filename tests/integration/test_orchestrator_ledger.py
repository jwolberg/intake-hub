"""End-to-end orchestrator tests for the ledger pipeline (U4).

Drives ``process``/``rerun``/``recover`` through the repointed tail
(extract → classify+categorize → decide → Sheet-append-or-hold) against the
in-memory repository and stub clients — no DB, no network. Asserts the two
terminal outcomes the ledger must demonstrate (auto-file to the Sheet, or hold)
plus the idempotency, failure, and recovery guarantees.
"""

from __future__ import annotations

from backend.clients import PassthroughLLMClient, StubSheetsClient
from backend.db.repository import InMemoryRepository
from backend.domain import AuditAction, Decision, InvoiceStatus
from backend.orchestrator import process, recover, rerun

# A clean office-software expense: clear vendor + a categorizable line + a total,
# so classification and categorization are both confident and it auto-files.
CLEAN_EXPENSE = {
    "source": {
        "channel": "email", "message_id": "m-clean",
        "subject": "Your receipt from Adobe", "sender": "billing@adobe.com",
        "attachment": "receipt.pdf",
    },
    "document": {
        "metadata": {
            "invoice_number": "A-1", "invoice_date": "2026-03-01",
            "vendor_name": "Adobe", "currency": "USD", "total_amount": "52.99",
        },
        "line_items": [
            {"raw_description": "Creative Cloud subscription",
             "quantity": "1", "unit_price": "52.99", "total": "52.99"},
        ],
    },
}

# A document with a total but no recognizable category keyword → category cannot be
# assigned with confidence → held for the reviewer (no Sheet write).
UNCATEGORIZABLE = {
    "source": {"channel": "email", "message_id": "m-hold", "subject": "note",
               "sender": "someone@example.com"},
    "document": {
        "metadata": {"vendor_name": "Bob", "total_amount": "10.00"},
        "line_items": [{"raw_description": "thing", "quantity": "1",
                        "unit_price": "10.00", "total": "10.00"}],
    },
}


def _process(sample, sheets=None):
    repo = InMemoryRepository()
    sheets = sheets or StubSheetsClient()
    invoice = process(sample, repo, llm=PassthroughLLMClient(), sheets=sheets)
    return repo, sheets, invoice


def _actions(repo, invoice_id):
    return [e.action for e in repo.get_audit(invoice_id)]


def test_clean_expense_auto_files_to_sheet():
    repo, sheets, invoice = _process(CLEAN_EXPENSE)
    assert invoice.status is InvoiceStatus.POSTED
    assert invoice.decision is Decision.SUBMIT
    # exactly one ledger row was appended
    assert len(sheets.rows) == 1
    row = sheets.rows[0]
    assert row[0] == "Expense"                 # Type
    assert row[2] == "Adobe"                   # Vendor
    assert row[4] == "52.99"                   # Amount
    # the terminal audit event records the Sheet row reference (like the old submit)
    actions = _actions(repo, invoice.id)
    assert AuditAction.CLASSIFIED in actions
    assert AuditAction.CATEGORIZED in actions
    assert AuditAction.POSTED in actions
    from backend.audit import latest_details
    posted = latest_details(repo.get_audit(invoice.id), AuditAction.POSTED)
    assert posted.get("sheet_row_ref")


def test_uncategorizable_item_holds_without_writing_the_sheet():
    repo, sheets, invoice = _process(UNCATEGORIZABLE)
    assert invoice.status is InvoiceStatus.HELD
    assert invoice.decision is Decision.HOLD
    assert sheets.rows == []  # nothing filed
    exc_types = {e.type for e in repo.get_exceptions(invoice.id)}
    assert "low_category_confidence" in exc_types


def test_persistent_sheet_failure_marks_failed_and_preserves_the_item():
    sheets = StubSheetsClient()
    sheets.fail_always = True
    repo, sheets, invoice = _process(CLEAN_EXPENSE, sheets=sheets)
    assert invoice.status is InvoiceStatus.FAILED
    assert sheets.rows == []
    exc_types = {e.type for e in repo.get_exceptions(invoice.id)}
    assert "sheet_write_failed" in exc_types
    # not recorded in the dedup ledger, so a later recover can re-file it
    assert repo.is_appended(invoice.id) is False


def test_transient_sheet_failure_is_retried_then_files():
    sheets = StubSheetsClient()
    sheets.fail_next = 2  # first two append attempts fail, third succeeds
    repo, sheets, invoice = _process(CLEAN_EXPENSE, sheets=sheets)
    assert invoice.status is InvoiceStatus.POSTED
    assert len(sheets.rows) == 1


def test_rerun_after_file_is_idempotent_no_duplicate_row():
    repo, sheets, invoice = _process(CLEAN_EXPENSE)
    assert len(sheets.rows) == 1
    rerun(invoice.id, repo, llm=PassthroughLLMClient(), sheets=sheets)
    # dedup ledger gates the second append — still exactly one row
    assert len(sheets.rows) == 1
    posted = [e for e in repo.get_audit(invoice.id) if e.action is AuditAction.POSTED]
    assert posted[-1].details.get("deduped") is True


def test_recover_refiles_after_a_failed_sheet_write():
    sheets = StubSheetsClient()
    sheets.fail_always = True
    repo, sheets, invoice = _process(CLEAN_EXPENSE, sheets=sheets)
    assert invoice.status is InvoiceStatus.FAILED
    # the Sheet becomes reachable; recovery re-drives the tail without re-extracting
    sheets.fail_always = False
    recover(invoice.id, repo, llm=PassthroughLLMClient(), sheets=sheets)
    recovered = repo.get_invoice(invoice.id)
    assert recovered.status is InvoiceStatus.POSTED
    assert len(sheets.rows) == 1
