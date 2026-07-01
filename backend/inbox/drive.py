"""Google Drive inbox provider (feat: Drive folder intake, U2).

``DriveInbox`` is a real ``InboxClient``: it treats a watched Drive folder as the
AI's inbox. ``fetch_messages`` lists the root PDFs, downloads each to a local temp
path, and emits an ``InboxMessage`` keyed by the stable Drive fileId with
``attachment_path`` set and **no** ``document`` block — which is exactly what
makes the orchestrator take its real-PDF branch (text extract → LLM → OCR
evidence) instead of the offline document path. Nothing downstream changes.

The post-decision file move lives in ``on_processed`` (added alongside the inbox
seam in U3), because the destination subfolder depends on the *decision*, which is
only known after processing.
"""

from __future__ import annotations

import logging
import pathlib

from backend.clients.drive import DriveClient
from backend.clients.errors import DriveClientError
from backend.domain.enums import Decision, InvoiceStatus
from backend.domain.models import Invoice

from . import InboxMessage

logger = logging.getLogger(__name__)

# Status subfolder names the processed file is moved into (KTD4). Kept as module
# constants so the runbook (U6) and tests reference the same literals.
SUBMITTED_DIR = "submitted"
NEEDS_REVIEW_DIR = "needs-review"
FAILED_DIR = "failed"


class DriveInbox:
    """Inbox backed by a watched Google Drive folder (``InboxClient``).

    ``download_dir`` is where each pulled PDF is written before the pipeline reads
    it; on Cloud Run this is the ephemeral local fs, which is fine — the file only
    needs to outlive one ``process`` call (KTD5).
    """

    def __init__(
        self,
        client: DriveClient,
        folder_id: str,
        download_dir: pathlib.Path | str,
    ) -> None:
        self._client = client
        self._folder_id = folder_id
        self._download_dir = pathlib.Path(download_dir)

    def fetch_messages(self) -> list[InboxMessage]:
        """Pull new root PDFs and emit pipeline-ready messages.

        Idempotency is *not* applied here — as with ``MockInbox``, every listed
        file becomes a message and the route dedups by ``message_id`` (the Drive
        fileId). A single file that fails to download is isolated (logged and
        skipped) so one bad file never aborts the whole poll; it stays in root and
        is retried on the next fetch.
        """
        self._download_dir.mkdir(parents=True, exist_ok=True)
        messages: list[InboxMessage] = []
        for file in self._client.list_pdfs(self._folder_id):
            try:
                data = self._client.download(file.id)
            except DriveClientError:
                logger.warning("drive: skipping %s (%s) — download failed", file.id, file.name)
                continue
            path = self._download_dir / f"{file.id}.pdf"
            path.write_bytes(data)
            messages.append(InboxMessage(
                message_id=file.id,
                subject=file.name,
                sender=None,
                attachment=file.name,
                attachment_path=str(path.resolve()),
                document=None,
                body=None,
            ))
        return messages

    def on_processed(self, message: InboxMessage, invoice: Invoice) -> None:
        """Move the processed file into its decision's status subfolder (KTD4).

        The destination reflects the decision **at processing time only** — the
        app does not chase later hub actions (R6). ``message_id`` is the Drive
        fileId. Called after ``mark_seen`` (KTD3), so a move failure can never
        cause reprocessing; a failed move is isolated here (logged, file left in
        root, retried-safe via ``is_seen``) so the generic fetch route needs no
        provider-specific error handling — mirroring ``fetch_messages``.
        """
        try:
            self._client.move(message.message_id, self._folder_id, _dest_for(invoice))
        except DriveClientError:
            logger.warning(
                "drive: could not move %s (%s) after processing; file left in root",
                message.message_id, message.subject,
            )


def _dest_for(invoice: Invoice) -> str:
    """Map a processed invoice to its status subfolder (KTD4).

    FAILED is checked first: a submit that fails at the ClinRun call ends FAILED
    with ``decision == SUBMIT``, and such a file was not actually submitted, so it
    belongs in ``failed`` rather than ``submitted``.
    """
    if invoice.status is InvoiceStatus.FAILED:
        return FAILED_DIR
    if invoice.decision is Decision.SUBMIT:
        return SUBMITTED_DIR
    return NEEDS_REVIEW_DIR
