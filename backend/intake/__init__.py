"""Stage: intake.

Receive or simulate emailed invoices and register a workflow record
(PRD FR1, §7 Step 1; ARCHITECTURE.md §4). Persistence of the record + source is
the orchestrator's job (P1-T9); this stage is a pure transformation.
"""

from __future__ import annotations

from backend.domain import Invoice, InvoiceStatus


def ingest(sample: dict) -> Invoice:
    """Create a fresh ``Invoice`` (status ``received``) for an incoming sample.

    ``sample`` is the raw intake payload (its ``source`` block carries channel /
    sender / subject / attachment). The detailed source and document blocks are
    consumed downstream by the parser.
    """
    channel = sample.get("source", {}).get("channel", "email")
    return Invoice(source=channel, status=InvoiceStatus.RECEIVED)
