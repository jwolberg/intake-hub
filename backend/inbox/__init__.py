"""Stage: inbox — simulated email receipt (P6; PRD §7 Step 1 "Mock inbox").

The system *receives* invoice messages from an ``InboxClient`` instead of being
seeded (``backend/tools/seed_hub``). ``MockInbox`` replays the curated demo
sample set as if each arrived by email; a real ``GmailClient``/``IMAPClient``
(OD-10) would implement the same protocol with zero pipeline changes — each
message is mapped to the intake ``sample`` the orchestrator already consumes
(``message_to_sample``), so nothing downstream changes.

Named ``inbox`` (not ``email``) on purpose: ``email`` would shadow the stdlib
package a real IMAP client imports.
"""

from __future__ import annotations

import json
import logging
import pathlib
from typing import TYPE_CHECKING, Protocol

from pydantic import BaseModel

from backend.config import settings

if TYPE_CHECKING:
    from backend.domain.models import Invoice

logger = logging.getLogger(__name__)

ROOT = pathlib.Path(__file__).resolve().parents[2]
SAMPLES = ROOT / "samples"

# The curated demo set (parity with ``backend/tools/seed_hub``): a clean submit,
# two distinct holds, a low-confidence line, an ambiguous-context hold, and a
# large invoice/catalog — covering submit / ambiguity / mismatch / large (PRD §19).
DEMO_STEMS = [
    "inv_clean_001", "inv_hold_unmatched_002", "inv_hold_mismatch_005",
    "inv_uncertain_006", "inv_ambiguous_008", "inv_large_007",
]


class InboxMessage(BaseModel):
    """An email-like message awaiting intake.

    Carries the envelope a real inbox exposes (id / subject / sender / attachment)
    plus the invoice payload — a parsed ``document`` block and/or a free-text
    ``body`` (PRD §7 Step 1). ``attachment_path`` points at a rendered PDF when one
    exists, so the reviewer overlay can rasterize the source (P4). ``attachment_b64``
    carries a PDF attachment's bytes inline (base64) when there is no local file to
    point at — the Gmail provider's case (U8) — taking the same cloud/inline-PDF
    branch ``backend.orchestrator``/``backend.parser`` already have for
    ``seed_cloud.py``. ``message_id`` is stable so a re-fetch is idempotent (P6-T3).
    """

    message_id: str
    subject: str | None = None
    sender: str | None = None
    attachment: str | None = None
    attachment_path: str | None = None
    attachment_b64: str | None = None
    document: dict | None = None
    body: str | None = None


class InboxClient(Protocol):
    """Source of incoming invoice messages (the ARCHITECTURE §7-§8 client seam)."""

    def fetch_messages(self) -> list[InboxMessage]: ...

    def on_processed(self, message: InboxMessage, invoice: Invoice) -> None:
        """Hook the fetch route calls after a message reaches a decision.

        Runs *after* ``repo.mark_seen`` so idempotency is already committed (a
        failure here can never cause reprocessing — KTD3). Sources that reflect
        decision state back to their origin (e.g. ``DriveInbox`` moving the file
        into a status subfolder) use this; sources with nowhere to reflect state
        (``MockInbox``) implement it as a no-op.
        """
        ...


def message_to_sample(message: InboxMessage) -> dict:
    """Map an inbox message to the intake ``sample`` the orchestrator consumes.

    ``orchestrator.process`` takes a ``{source, document?/body?}`` dict (PRD §7
    Step 1-2); this is the single adapter between "received email" and the existing
    pipeline, so nothing downstream changes. ``channel`` is fixed to ``email`` (the
    mock inbox simulates email receipt), and the message's stable ``message_id``
    rides in ``source`` for traceability. ``document`` / ``body`` are included only
    when present, so the parser picks the right path (parsed attachment vs body).
    """
    source: dict = {
        "channel": "email",
        "message_id": message.message_id,
        "subject": message.subject,
        "sender": message.sender,
        "attachment": message.attachment,
        "attachment_path": message.attachment_path,
    }
    if message.attachment_b64:
        source["attachment_b64"] = message.attachment_b64
    sample: dict = {"source": source}
    if message.document is not None:
        sample["document"] = message.document
    if message.body:
        sample["body"] = message.body
    return sample


class MockInbox:
    """Replays the demo sample set as inbox messages (PRD §7 Step 1 "Mock inbox").

    Each sample JSON becomes one message keyed by its file stem (a stable
    ``message_id`` for idempotency). With ``render_pdf`` (default), each sample's
    invoice content is rendered to a real PDF — parity with ``seed_hub`` — so the
    received invoice has a rasterizable source for the page-image overlay; the
    structured ``document`` block still rides along for offline extraction. Set
    ``render_pdf=False`` for a no-dependency offline path (document block only).
    """

    def __init__(
        self,
        samples_dir: pathlib.Path | str = SAMPLES,
        *,
        stems: list[str] | None = None,
        render_pdf: bool = True,
    ) -> None:
        self._dir = pathlib.Path(samples_dir)
        self._stems = list(stems) if stems is not None else list(DEMO_STEMS)
        self._render_pdf = render_pdf

    def fetch_messages(self) -> list[InboxMessage]:
        messages: list[InboxMessage] = []
        for stem in self._stems:
            sample = json.loads((self._dir / f"{stem}.json").read_text())
            source = sample.get("source", {})
            attachment_path: str | None = None
            if self._render_pdf:
                attachment_path = str(self._render(stem, sample).resolve())
            messages.append(InboxMessage(
                message_id=stem,
                subject=source.get("subject"),
                sender=source.get("sender"),
                attachment=source.get("attachment"),
                attachment_path=attachment_path,
                document=sample.get("document"),
                body=sample.get("body") or source.get("body"),
            ))
        return messages

    def on_processed(self, message: InboxMessage, invoice: Invoice) -> None:
        """No-op: the mock inbox has no external folder to reflect state in."""

    def _render(self, stem: str, sample: dict) -> pathlib.Path:
        # Lazy import: the PDF renderer (reportlab) is only needed on the demo path.
        from samples.generate_pdfs import render_invoice_pdf

        pdf_dir = self._dir / "pdf"
        pdf_dir.mkdir(exist_ok=True)
        return render_invoice_pdf(sample, pdf_dir / f"{stem}.pdf")


def get_inbox_client() -> InboxClient:
    """Select the inbox provider from configuration (default ``MockInbox``).

    ``INBOX_PROVIDER=mock`` (default) replays the offline demo set (PRD §7 Step 1).
    ``INBOX_PROVIDER=drive`` reads real PDFs from a watched Google Drive folder
    (feat: Drive folder intake). ``INBOX_PROVIDER=gmail`` reads a connected Gmail
    mailbox (feat: solopreneur-ledger pivot, U8). Selecting ``drive``/``gmail``
    without their required config fails fast rather than silently falling back to
    the mock, so a misconfigured deploy is loud instead of quietly serving demo
    data.
    """
    provider = (settings.inbox_provider or "mock").strip().lower()
    if provider == "mock":
        return MockInbox()
    if provider == "drive":
        return _build_drive_inbox(settings)
    if provider == "gmail":
        return _build_gmail_inbox(settings)
    raise ValueError(
        f"unknown INBOX_PROVIDER '{settings.inbox_provider}' "
        "(expected 'mock', 'drive', or 'gmail')"
    )


def _build_drive_inbox(settings) -> InboxClient:
    """Construct a ``DriveInbox`` + ``HttpDriveClient`` from settings (fail-fast).

    Drive-specific imports are lazy so the default mock path never pulls in httpx
    transport or ``google-auth`` (KTD1). ``GOOGLE_APPLICATION_CREDENTIALS`` is
    treated as inline JSON when it starts with ``{``, otherwise as a file path.
    """
    import json
    import tempfile

    from backend.clients.drive import HttpDriveClient

    from .drive import DriveInbox

    if not settings.drive_folder_id:
        raise ValueError("INBOX_PROVIDER=drive requires DRIVE_FOLDER_ID")
    creds = settings.google_application_credentials
    if not creds:
        raise ValueError(
            "INBOX_PROVIDER=drive requires GOOGLE_APPLICATION_CREDENTIALS "
            "(a service-account key path or inline JSON)"
        )
    creds = creds.strip()
    if creds.startswith("{"):
        client = HttpDriveClient(credentials_info=json.loads(creds))
    else:
        client = HttpDriveClient(credentials_file=creds)
    download_dir = pathlib.Path(tempfile.gettempdir()) / "intakehub-drive"
    return DriveInbox(client, settings.drive_folder_id, download_dir)


def _build_gmail_inbox(settings, repo=None) -> InboxClient:
    """Construct a ``GmailInbox`` + ``HttpGmailClient`` from settings (fail-fast).

    Gmail-specific imports are lazy (KTD1) so the default mock path never pulls
    in ``httpx``/``google-auth``. ``repo`` is injectable so tests can exercise
    this factory's token/sync-state wiring against an ``InMemoryRepository()``
    with no database; ``get_inbox_client()`` (the running app) leaves it unset
    and gets the app's Postgres-backed ``get_repository()`` singleton, which
    satisfies ``GmailSyncState`` with no adapter (``is_seen``/``mark_seen``
    already existed on ``Repository``; ``get_sync_history_id``/
    ``set_sync_history_id`` are added alongside this provider).
    """
    from backend.clients import get_llm_client
    from backend.clients.gmail import HttpGmailClient
    from backend.db.repository import get_repository

    from .gmail import GmailInbox

    if not settings.gmail_client_id:
        raise ValueError("INBOX_PROVIDER=gmail requires GMAIL_CLIENT_ID")
    if not settings.gmail_client_secret:
        raise ValueError("INBOX_PROVIDER=gmail requires GMAIL_CLIENT_SECRET")
    if not settings.gmail_refresh_token:
        raise ValueError("INBOX_PROVIDER=gmail requires GMAIL_REFRESH_TOKEN")

    repo = repo if repo is not None else get_repository()
    refresh_token = _load_refresh_token(repo, settings)

    client = HttpGmailClient(
        refresh_token=refresh_token,
        client_id=settings.gmail_client_id,
        client_secret=settings.gmail_client_secret,
    )
    return GmailInbox(
        client,
        llm=get_llm_client(),
        tax_year=settings.gmail_tax_year,
        label=settings.gmail_label,
        sync_state=repo,
    )


def _load_refresh_token(repo, settings) -> str:
    """Decrypt-on-use the stored refresh token, or encrypt-and-store it on
    first use (R20: never persisted in cleartext).

    Falls back to reading ``settings.gmail_refresh_token`` straight from
    config (no DB persistence at all — nothing is ever written in cleartext,
    it just isn't written) when ``GMAIL_TOKEN_ENC_KEY`` is unset or the
    ``cryptography`` dependency is not installed. Both cases log loudly, since
    it means a restart re-reads from the environment instead of the encrypted
    store.
    """
    from . import _crypto

    key = settings.gmail_token_enc_key
    if not key:
        logger.warning(
            "gmail: GMAIL_TOKEN_ENC_KEY not set; refresh token will not be "
            "persisted at rest (falling back to GMAIL_REFRESH_TOKEN each run)"
        )
        return settings.gmail_refresh_token

    try:
        stored = repo.get_oauth_token("gmail")
        if stored is not None:
            return _crypto.decrypt_token(stored, key)
        repo.set_oauth_token(
            "gmail", _crypto.encrypt_token(settings.gmail_refresh_token, key)
        )
        return settings.gmail_refresh_token
    except _crypto.TokenCryptoUnavailable:
        logger.warning(
            "gmail: 'cryptography' is not installed; refresh token will not be "
            "persisted at rest (falling back to GMAIL_REFRESH_TOKEN each run)"
        )
        return settings.gmail_refresh_token
