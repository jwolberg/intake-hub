"""Ledger-integrity tests (U9): duplicate holds, notifications, spot-check.

Covers the pure helpers, the orchestrator's duplicate-hold guard (R19), and the
notification (R18) + spot-check (R15) API surfaces.
"""

from __future__ import annotations

import random

from backend.api.main import app, get_pipeline_clients, get_repo
from backend.clients import PassthroughLLMClient, StubSheetsClient
from backend.db.repository import InMemoryRepository
from backend.domain import Invoice, InvoiceMetadata, InvoiceStatus
from backend.ledger_integrity import find_duplicate, sample_posted
from backend.orchestrator import process, recover
from fastapi.testclient import TestClient


def _posted(vendor, total, date, id="inv"):
    return Invoice(id=id, status=InvoiceStatus.POSTED,
                   metadata=InvoiceMetadata(vendor_name=vendor, total_amount=total,
                                            invoice_date=date))


# --- pure helpers -----------------------------------------------------------

def test_find_duplicate_matches_same_vendor_amount_and_close_date():
    candidate = _posted("Adobe", "52.99", "2026-03-02", id="cand")
    prior = _posted("adobe", "52.99", "2026-03-01", id="prior")
    assert find_duplicate(candidate, [prior]) is prior


def test_find_duplicate_ignores_different_amount():
    candidate = _posted("Adobe", "52.99", "2026-03-01", id="cand")
    prior = _posted("Adobe", "99.00", "2026-03-01", id="prior")
    assert find_duplicate(candidate, [prior]) is None


def test_find_duplicate_ignores_distant_date():
    candidate = _posted("Adobe", "52.99", "2026-03-20", id="cand")
    prior = _posted("Adobe", "52.99", "2026-03-01", id="prior")
    assert find_duplicate(candidate, [prior]) is None


def test_find_duplicate_needs_vendor_and_total():
    no_vendor = _posted(None, "52.99", "2026-03-01")
    no_total = _posted("A", None, "2026-03-01")
    assert find_duplicate(no_vendor, [_posted("A", "52.99", "x")]) is None
    assert find_duplicate(no_total, [_posted("A", None, "x")]) is None


def test_sample_posted_is_bounded_and_deterministic():
    posted = [_posted("V", "1.00", "2026-01-01", id=f"i{n}") for n in range(10)]
    picked = sample_posted(posted, 3, rng=random.Random(1))
    assert len(picked) == 3
    assert sample_posted(posted, 3, rng=random.Random(1)) == picked  # deterministic
    assert sample_posted(posted, 99) == posted  # k >= len returns all
    assert sample_posted(posted, 0) == []


# --- orchestrator duplicate hold (R19) --------------------------------------

def _dup_sample(message_id):
    return {
        "source": {"channel": "email", "message_id": message_id,
                   "subject": "Adobe receipt", "sender": "billing@adobe.com"},
        "document": {
            "metadata": {"vendor_name": "Adobe", "invoice_date": "2026-03-01",
                         "currency": "USD", "total_amount": "52.99"},
            "line_items": [{"raw_description": "Creative Cloud subscription",
                            "quantity": "1", "unit_price": "52.99", "total": "52.99"}],
        },
    }


def test_second_receipt_for_same_charge_is_held_not_double_posted():
    repo = InMemoryRepository()
    sheets = StubSheetsClient()
    first = process(_dup_sample("m1"), repo, llm=PassthroughLLMClient(), sheets=sheets)
    assert first.status is InvoiceStatus.POSTED
    assert len(sheets.rows) == 1

    second = process(_dup_sample("m2"), repo, llm=PassthroughLLMClient(), sheets=sheets)
    assert second.status is InvoiceStatus.HELD
    assert len(sheets.rows) == 1  # not double-posted
    exc = {e.type for e in repo.get_exceptions(second.id)}
    assert "suspected_duplicate" in exc


def test_recover_runs_the_duplicate_check_when_first_pass_failed_before_it():
    # B1 regression: an item can FAIL before the dup guard runs (e.g. its Sheet
    # write failed). If a duplicate gets posted in the meantime, recovering the
    # failed item must still hold it rather than double-post.
    repo = InMemoryRepository()
    good = StubSheetsClient()
    failing = StubSheetsClient()
    failing.fail_always = True

    # B fails its Sheet write before any duplicate exists → FAILED (dup guard passed).
    b = process(_dup_sample("m1"), repo, llm=PassthroughLLMClient(), sheets=failing)
    assert b.status is InvoiceStatus.FAILED

    # A duplicate of B is then posted.
    a = process(_dup_sample("m2"), repo, llm=PassthroughLLMClient(), sheets=good)
    assert a.status is InvoiceStatus.POSTED

    # Recovering B (Sheet now healthy) must detect the duplicate and hold it.
    recover(b.id, repo, llm=PassthroughLLMClient(), sheets=good)
    recovered = repo.get_invoice(b.id)
    assert recovered.status is InvoiceStatus.HELD
    assert len(good.rows) == 1  # B was not double-posted
    assert "suspected_duplicate" in {e.type for e in repo.get_exceptions(b.id)}


def test_distinct_charge_same_vendor_different_amount_still_posts():
    repo = InMemoryRepository()
    sheets = StubSheetsClient()
    process(_dup_sample("m1"), repo, llm=PassthroughLLMClient(), sheets=sheets)
    other = _dup_sample("m2")
    other["document"]["metadata"]["total_amount"] = "99.00"
    other["document"]["line_items"][0]["total"] = "99.00"
    other["document"]["line_items"][0]["unit_price"] = "99.00"
    second = process(other, repo, llm=PassthroughLLMClient(), sheets=sheets)
    assert second.status is InvoiceStatus.POSTED
    assert len(sheets.rows) == 2


# --- notification + spot-check API ------------------------------------------

def _client(repo):
    app.dependency_overrides[get_repo] = lambda: repo
    app.dependency_overrides[get_pipeline_clients] = lambda: {
        "llm": PassthroughLLMClient(), "sheets": StubSheetsClient(),
    }
    return TestClient(app)


def test_notifications_report_held_count_by_reason():
    repo = InMemoryRepository()
    client = _client(repo)
    try:
        # two duplicates of one charge → one posts, the second holds
        client.post("/api/invoices/process", json=_dup_sample("m1"))
        client.post("/api/invoices/process", json=_dup_sample("m2"))
        notif = client.get("/api/notifications").json()
        assert notif["held_count"] == 1
        assert notif["by_reason"].get("suspected_duplicate") == 1
    finally:
        app.dependency_overrides.clear()


def test_spot_check_samples_posted_and_records_event():
    repo = InMemoryRepository()
    client = _client(repo)
    try:
        client.post("/api/invoices/process", json=_dup_sample("m1"))  # posts
        resp = client.post("/api/spot-check", json={"k": 5}).json()
        assert resp["count"] == 1
        sampled_id = resp["sampled"][0]["id"]
        # the spot-check is recorded on the sampled item's audit trail
        trace = client.get(f"/api/invoices/{sampled_id}/trace").json()
        assert any(s["stage"] == "note" for s in trace["steps"])
    finally:
        app.dependency_overrides.clear()
