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
import pathlib
from typing import Protocol

from pydantic import BaseModel

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
    exists, so the reviewer overlay can rasterize the source (P4). ``message_id``
    is stable so a re-fetch is idempotent (P6-T3).
    """

    message_id: str
    subject: str | None = None
    sender: str | None = None
    attachment: str | None = None
    attachment_path: str | None = None
    document: dict | None = None
    body: str | None = None


class InboxClient(Protocol):
    """Source of incoming invoice messages (the ARCHITECTURE §7-§8 client seam)."""

    def fetch_messages(self) -> list[InboxMessage]: ...


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

    def _render(self, stem: str, sample: dict) -> pathlib.Path:
        # Lazy import: the PDF renderer (reportlab) is only needed on the demo path.
        from samples.generate_pdfs import render_invoice_pdf

        pdf_dir = self._dir / "pdf"
        pdf_dir.mkdir(exist_ok=True)
        return render_invoice_pdf(sample, pdf_dir / f"{stem}.pdf")


def get_inbox_client() -> InboxClient:
    """Default inbox for the running app: the offline ``MockInbox`` (OD-10).

    PRD §7 Step 1 authorizes a mock inbox; a real Gmail/IMAP client slots in here
    with zero pipeline changes once available ("if time allows").
    """
    return MockInbox()
