"""Integration test for inbox intake (Drive folder intake U3; ledger pivot).

Drives POST /api/inbox/fetch through FastAPI TestClient with the repository and
pipeline clients (offline LLM + Sheets stubs) overridden (no DB, no network).
The MockInbox cases verify the demo messages land in their actual ledger
terminal states (POSTED/HELD) and that a re-fetch is idempotent. The
DriveInbox cases verify the post-decision move hook files each processed PDF
into the right status subfolder and that idempotency survives a move failure
(KTD3).
"""

import json
import pathlib

import pytest
from backend.api.main import app, get_inbox, get_pipeline_clients, get_repo
from backend.clients import (
    GmailMessage,
    PassthroughLLMClient,
    StubDriveClient,
    StubGmailClient,
    StubSheetsClient,
)
from backend.db.repository import InMemoryRepository
from backend.inbox import DEMO_STEMS, MockInbox
from backend.inbox.drive import DriveInbox
from backend.inbox.gmail import GmailInbox
from fastapi.testclient import TestClient
from samples.generate_pdfs import render_invoice_pdf

SAMPLES = pathlib.Path(__file__).resolve().parents[2] / "samples"
DRIVE_ROOT = "root-folder"

# Expected terminal outcome per demo message — the ledger engine's actual
# decision, not the old clinical-sponsor-matching outcome the stems were
# originally named for (e.g. "inv_hold_unmatched_002" now auto-files: it has a
# vendor, a total, and a "travel" keyword line, so it categorizes cleanly).
EXPECTED = {
    "inv_clean_001": "posted",
    "inv_hold_unmatched_002": "posted",
    "inv_hold_mismatch_005": "held",
    "inv_uncertain_006": "held",
    "inv_ambiguous_008": "held",
    "inv_large_007": "held",
}


@pytest.fixture
def client():
    repo = InMemoryRepository()
    app.dependency_overrides[get_repo] = lambda: repo
    app.dependency_overrides[get_pipeline_clients] = lambda: {
        "llm": PassthroughLLMClient(),
        "sheets": StubSheetsClient(),
    }
    app.dependency_overrides[get_inbox] = lambda: MockInbox(render_pdf=False)
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_fetch_processes_demo_messages_to_expected_outcomes(client):
    body = client.post("/api/inbox/fetch").json()
    assert body["count"] == len(DEMO_STEMS)
    assert body["skipped"] == 0
    outcomes = {row["message_id"]: row["status"] for row in body["received"]}
    assert outcomes == EXPECTED
    # Every received invoice is listed in the hub.
    assert len(client.get("/api/invoices").json()) == len(DEMO_STEMS)


def test_refetch_is_idempotent(client):
    first = client.post("/api/inbox/fetch").json()
    assert first["count"] == len(DEMO_STEMS)

    second = client.post("/api/inbox/fetch").json()
    assert second["count"] == 0
    assert second["skipped"] == len(DEMO_STEMS)
    assert second["received"] == []

    # No duplicate invoices were created by the second fetch.
    assert len(client.get("/api/invoices").json()) == len(DEMO_STEMS)


# --- DriveInbox: post-decision move hook (U3) -------------------------------


def _pdf_bytes(stem: str, tmp_path: pathlib.Path) -> bytes:
    sample = json.loads((SAMPLES / f"{stem}.json").read_text())
    return pathlib.Path(render_invoice_pdf(sample, tmp_path / f"{stem}.pdf")).read_bytes()


def _drive_client(tmp_path: pathlib.Path, stub: StubDriveClient) -> TestClient:
    repo = InMemoryRepository()
    app.dependency_overrides[get_repo] = lambda: repo
    # PassthroughLLMClient is auto-swapped for the offline LayoutLLMClient on the
    # real-PDF branch (orchestrator._extraction_llm), so DriveInbox PDFs process
    # deterministically with no live model.
    app.dependency_overrides[get_pipeline_clients] = lambda: {
        "llm": PassthroughLLMClient(),
        "sheets": StubSheetsClient(),
    }
    app.dependency_overrides[get_inbox] = lambda: DriveInbox(
        stub, DRIVE_ROOT, tmp_path / "downloads"
    )
    return TestClient(app)


def test_drive_fetch_files_each_pdf_by_decision(tmp_path):
    """A clean, a hold, and an unreadable PDF file to their respective status
    subfolders; the loop completes for all three."""
    stub = StubDriveClient()
    stub.add_file("clean", "clean.pdf", _pdf_bytes("inv_clean_001", tmp_path), parent=DRIVE_ROOT)
    stub.add_file(
        "hold", "hold.pdf", _pdf_bytes("inv_hold_mismatch_005", tmp_path), parent=DRIVE_ROOT
    )
    bad_bytes = (SAMPLES / "pdf" / "inv_unreadable_009.pdf").read_bytes()
    stub.add_file("bad", "bad.pdf", bad_bytes, parent=DRIVE_ROOT)

    client = _drive_client(tmp_path, stub)
    body = client.post("/api/inbox/fetch").json()

    assert body["count"] == 3
    moves = dict(stub.moves)
    assert moves == {"clean": "posted", "hold": "needs-review", "bad": "failed"}

    outcomes = {row["message_id"]: row["status"] for row in body["received"]}
    assert outcomes["clean"] == "posted"
    assert outcomes["hold"] == "held"
    assert outcomes["bad"] == "failed"

    app.dependency_overrides.clear()


def test_drive_refetch_skips_moved_files(tmp_path):
    """Files moved into status subfolders are not re-listed on the next fetch
    (root-only listing), so no double-processing."""
    stub = StubDriveClient()
    stub.add_file("clean", "clean.pdf", _pdf_bytes("inv_clean_001", tmp_path), parent=DRIVE_ROOT)

    client = _drive_client(tmp_path, stub)
    assert client.post("/api/inbox/fetch").json()["count"] == 1
    invoices_after_first = len(client.get("/api/invoices").json())

    second = client.post("/api/inbox/fetch").json()
    assert second["count"] == 0  # moved out of root, nothing to list
    assert len(client.get("/api/invoices").json()) == invoices_after_first

    app.dependency_overrides.clear()


def test_drive_move_failure_after_mark_seen_no_double_submit(tmp_path):
    """on_processed move raises after mark_seen -> the file is stranded in root
    but recorded as seen, so a re-fetch skips it by fileId (no double filing)."""
    stub = StubDriveClient()
    stub.add_file("clean", "clean.pdf", _pdf_bytes("inv_clean_001", tmp_path), parent=DRIVE_ROOT)
    stub.fail_move.add("clean")  # move raises DriveClientError after mark_seen

    client = _drive_client(tmp_path, stub)
    first = client.post("/api/inbox/fetch").json()
    assert first["count"] == 1  # processed despite the move failure
    invoices_after_first = len(client.get("/api/invoices").json())

    # File never moved, so it is still listed in root — but is_seen skips it.
    second = client.post("/api/inbox/fetch").json()
    assert second["count"] == 0
    assert second["skipped"] == 1
    assert len(client.get("/api/invoices").json()) == invoices_after_first

    app.dependency_overrides.clear()


# --- GmailInbox: end-to-end fetch -> pipeline -> posted/held (U8) -----------


def _gmail_client(repo: InMemoryRepository, gmail: StubGmailClient) -> TestClient:
    app.dependency_overrides[get_repo] = lambda: repo
    app.dependency_overrides[get_pipeline_clients] = lambda: {
        "llm": PassthroughLLMClient(),
        "sheets": StubSheetsClient(),
    }
    # The repo doubles as GmailInbox's sync_state, mirroring production
    # (_build_gmail_inbox passes the same repo for both) — this is also what
    # lets GmailInbox's own is_seen guard cooperate with the route's.
    app.dependency_overrides[get_inbox] = lambda: GmailInbox(
        gmail, tax_year=2026, sync_state=repo
    )
    return TestClient(app)


def test_gmail_fetch_processes_a_receipt_to_a_terminal_state():
    """A seeded Gmail receipt flows fetch -> receipt-filter -> pipeline ->
    a terminal ledger status (posted or held — either demonstrates the
    end-to-end wiring; the offline PassthroughLLMClient marks a free-text
    body's fields uncertain, so held is the expected deterministic outcome,
    but the assertion doesn't over-specify the categorization result)."""
    repo = InMemoryRepository()
    gmail = StubGmailClient()
    gmail.add_message(GmailMessage(
        id="gm-1",
        thread_id="t-1",
        subject="Your receipt from Acme",
        sender="billing@acme.example",  # heuristic receipt signal
        body_html="<p>Vendor: Acme Co<br>Total: $42.00<br>Date: 2026-03-01</p>",
    ))

    client = _gmail_client(repo, gmail)
    body = client.post("/api/inbox/fetch").json()

    assert body["count"] == 1
    assert body["skipped"] == 0
    assert body["received"][0]["message_id"] == "gm-1"
    assert body["received"][0]["status"] in {"posted", "held"}
    assert len(client.get("/api/invoices").json()) == 1

    app.dependency_overrides.clear()


def test_gmail_refetch_is_idempotent():
    repo = InMemoryRepository()
    gmail = StubGmailClient()
    gmail.add_message(GmailMessage(
        id="gm-1", thread_id="t-1", subject="Your receipt", sender="billing@acme.example",
        body_html="<p>Total: $42.00</p>",
    ))

    client = _gmail_client(repo, gmail)
    first = client.post("/api/inbox/fetch").json()
    assert first["count"] == 1
    invoices_after_first = len(client.get("/api/invoices").json())

    # A later delta re-reports the same message (e.g. a label change) — both
    # GmailInbox's own is_seen guard and the route's dedup keep this a no-op.
    history_id = repo.get_sync_history_id()
    gmail.seed_history(history_id, ["gm-1"], "next-id")

    second = client.post("/api/inbox/fetch").json()
    assert second["count"] == 0
    assert len(client.get("/api/invoices").json()) == invoices_after_first

    app.dependency_overrides.clear()


def test_gmail_non_receipt_never_reaches_the_pipeline():
    """A newsletter is dropped by GmailInbox before ``fetch_messages`` even
    returns it — it never becomes an invoice, and is marked seen so it is
    never re-offered."""
    repo = InMemoryRepository()
    gmail = StubGmailClient()
    gmail.add_message(GmailMessage(
        id="newsletter-1", thread_id="t-2", subject="Weekly digest: what's new",
        sender="newsletter@list.example.com", body_html="<p>secret body</p>",
    ))

    client = _gmail_client(repo, gmail)
    body = client.post("/api/inbox/fetch").json()

    assert body["count"] == 0
    assert body["received"] == []
    assert client.get("/api/invoices").json() == []
    assert repo.is_seen("newsletter-1") is True

    app.dependency_overrides.clear()
