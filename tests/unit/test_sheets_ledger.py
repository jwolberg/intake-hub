"""Unit tests for the idempotent Sheet-append helper (feat: solopreneur-ledger
pivot, U3).

Drives the Postgres-gated dedup path: ``append_filed_row`` must never post a
second row for the same idempotency key, and a transient client failure must
propagate (not be swallowed) so the caller's retry/hold logic can act on it.
"""

import pytest
from backend.clients.errors import SheetsClientError
from backend.clients.sheets import AppendOutcome, StubSheetsClient, append_filed_row
from backend.db.repository import InMemoryRepository

KEY = "gmail:msg-1"
ROW = ["Expense", "Office Expense", "Acme Software", "2026-06-01", "49.99", KEY]


def test_append_filed_row_happy_path_records_ref_in_ledger():
    repo = InMemoryRepository()
    client = StubSheetsClient()

    outcome = append_filed_row(repo, client, idempotency_key=KEY, row=ROW)

    assert isinstance(outcome, AppendOutcome)
    assert outcome.deduped is False
    assert client.rows == [ROW]
    assert repo.is_appended(KEY) is True
    assert repo.get_append_ref(KEY) == outcome.sheet_row_ref


def test_append_filed_row_dedups_on_retry_no_duplicate_row():
    repo = InMemoryRepository()
    client = StubSheetsClient()

    first = append_filed_row(repo, client, idempotency_key=KEY, row=ROW)
    second = append_filed_row(repo, client, idempotency_key=KEY, row=ROW)

    assert len(client.rows) == 1  # only one row ever appended
    assert second.deduped is True
    assert second.sheet_row_ref == first.sheet_row_ref


def test_append_filed_row_transient_failure_propagates_for_caller_retry():
    repo = InMemoryRepository()
    client = StubSheetsClient()
    client.fail_next = 1

    with pytest.raises(SheetsClientError):
        append_filed_row(repo, client, idempotency_key=KEY, row=ROW)

    # The helper does not retry internally and does not record a partial
    # append — the ledger has no entry for this key after the failure.
    assert repo.is_appended(KEY) is False
    assert client.rows == []

    # A subsequent call (the caller's retry) succeeds and posts exactly once.
    outcome = append_filed_row(repo, client, idempotency_key=KEY, row=ROW)
    assert outcome.deduped is False
    assert client.rows == [ROW]
