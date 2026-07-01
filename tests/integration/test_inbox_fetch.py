"""Integration test for inbox intake (P6-T6; Drive folder intake U3).

Drives POST /api/inbox/fetch through FastAPI TestClient with the repository,
pipeline clients, and inbox all overridden to in-process offline stubs (no DB, no
network). The MockInbox cases verify the demo messages land in their expected
SUBMIT/HOLD states and that a re-fetch is idempotent. The DriveInbox cases verify
the post-decision move hook files each processed PDF into the right status
subfolder and that idempotency survives a move failure (KTD3).
"""

import json
import pathlib

import pytest
from backend.api.main import app, get_inbox, get_pipeline_clients, get_repo
from backend.clients import (
    PassthroughLLMClient,
    StubClinRunClient,
    StubDriveClient,
    StubMCPReferenceClient,
)
from backend.db.repository import InMemoryRepository
from backend.inbox import DEMO_STEMS, MockInbox
from backend.inbox.drive import DriveInbox
from fastapi.testclient import TestClient
from samples.generate_pdfs import render_invoice_pdf

SAMPLES = pathlib.Path(__file__).resolve().parents[2] / "samples"
DRIVE_ROOT = "root-folder"

# Expected terminal outcome per demo message (the curated submit / hold set, PRD §19).
EXPECTED = {
    "inv_clean_001": "submitted",
    "inv_hold_unmatched_002": "held",
    "inv_hold_mismatch_005": "held",
    "inv_uncertain_006": "held",
    "inv_ambiguous_008": "held",
    "inv_large_007": "submitted",
}


@pytest.fixture
def client():
    repo = InMemoryRepository()
    app.dependency_overrides[get_repo] = lambda: repo
    app.dependency_overrides[get_pipeline_clients] = lambda: {
        "llm": PassthroughLLMClient(),
        "ref": StubMCPReferenceClient(),
        "clinrun": StubClinRunClient(),
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
        "ref": StubMCPReferenceClient(),
        "clinrun": StubClinRunClient(),
    }
    app.dependency_overrides[get_inbox] = lambda: DriveInbox(
        stub, DRIVE_ROOT, tmp_path / "downloads"
    )
    return TestClient(app)


def test_drive_fetch_files_each_pdf_by_decision(tmp_path):
    """AE1/AE2/AE3: a clean, a hold, and an unreadable PDF file to their
    respective status subfolders; the loop completes for all three."""
    stub = StubDriveClient()
    stub.add_file("clean", "clean.pdf", _pdf_bytes("inv_clean_001", tmp_path), parent=DRIVE_ROOT)
    stub.add_file(
        "hold", "hold.pdf", _pdf_bytes("inv_hold_unmatched_002", tmp_path), parent=DRIVE_ROOT
    )
    stub.add_file("bad", "bad.pdf", b"not a real pdf at all", parent=DRIVE_ROOT)

    client = _drive_client(tmp_path, stub)
    body = client.post("/api/inbox/fetch").json()

    assert body["count"] == 3
    moves = dict(stub.moves)
    assert moves == {"clean": "submitted", "hold": "needs-review", "bad": "failed"}

    outcomes = {row["message_id"]: row["status"] for row in body["received"]}
    assert outcomes["clean"] == "submitted"
    assert outcomes["hold"] == "held"
    assert outcomes["bad"] == "failed"

    app.dependency_overrides.clear()


def test_drive_refetch_skips_moved_files(tmp_path):
    """AE5: files moved into status subfolders are not re-listed on the next
    fetch (root-only listing), so no double-processing."""
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
    """AE4: on_processed move raises after mark_seen -> the file is stranded in
    root but recorded as seen, so a re-fetch skips it by fileId (no double submit)."""
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
