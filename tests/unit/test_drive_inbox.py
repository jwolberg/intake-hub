"""Unit tests for the DriveInbox provider (feat: Drive folder intake, U2).

Uses ``StubDriveClient`` seeded with PDF bytes so the provider runs with no
network and no credentials. Verifies the emitted messages carry the Drive fileId,
a written temp ``.pdf`` path, and no ``document`` block (so the orchestrator takes
the real-PDF branch), and that a failed download is isolated rather than aborting
the poll.
"""

import pathlib

from backend.clients import StubDriveClient
from backend.inbox import message_to_sample
from backend.inbox.drive import DriveInbox

ROOT = "root-folder"


def _seeded_inbox(tmp_path: pathlib.Path) -> tuple[DriveInbox, StubDriveClient]:
    stub = StubDriveClient()
    stub.add_file("file-a", "invoice-a.pdf", b"%PDF-a", parent=ROOT)
    stub.add_file("file-b", "invoice-b.pdf", b"%PDF-b", parent=ROOT)
    return DriveInbox(stub, ROOT, tmp_path), stub


def test_fetch_emits_real_pdf_messages(tmp_path):
    inbox, _ = _seeded_inbox(tmp_path)
    messages = inbox.fetch_messages()

    assert {m.message_id for m in messages} == {"file-a", "file-b"}
    for m in messages:
        assert m.document is None  # real-PDF branch, not the offline document path
        assert m.attachment_path is not None
        p = pathlib.Path(m.attachment_path)
        assert p.exists() and p.suffix == ".pdf"
        assert p.read_bytes().startswith(b"%PDF")


def test_emitted_message_maps_to_sample_without_document(tmp_path):
    inbox, _ = _seeded_inbox(tmp_path)
    message = inbox.fetch_messages()[0]
    sample = message_to_sample(message)

    assert "document" not in sample  # parser takes the PDF path, not the doc path
    assert sample["source"]["message_id"] == message.message_id
    assert sample["source"]["attachment_path"] == message.attachment_path


def test_empty_folder_yields_no_messages(tmp_path):
    inbox = DriveInbox(StubDriveClient(), ROOT, tmp_path)
    assert inbox.fetch_messages() == []


def test_failed_download_is_skipped_without_half_built_message(tmp_path):
    inbox, stub = _seeded_inbox(tmp_path)
    stub.fail_download.add("file-a")

    messages = inbox.fetch_messages()

    # The bad file is isolated; the healthy file still comes through.
    assert [m.message_id for m in messages] == ["file-b"]
