"""Stage: submission.

Send an approved invoice payload to ClinRun (or the mock) and return the result
(PRD FR7, §7 Step 8; ARCHITECTURE.md §4). Only called when the decision is
``submit``. A failed submission raises ``SubmissionFailed`` (retryable), which
the orchestrator records as a retryable failure state.
"""

from __future__ import annotations

from backend.clients.clinrun import ClinRunClient, SubmissionResult
from backend.domain import Invoice, InvoiceMetadata, MatchResult, ResolvedContext


def build_payload(
    invoice: Invoice,
    metadata: InvoiceMetadata,
    ctx: ResolvedContext,
    matches: list[MatchResult],
) -> dict:
    """Assemble the structured payload ClinRun receives (resolved context +
    metadata + matched line items)."""
    return {
        "invoice_id": invoice.id,
        "invoice_number": metadata.invoice_number,
        "sponsor_id": ctx.sponsor_id,
        "study_id": ctx.study_id,
        "site_id": ctx.site_id,
        "currency": metadata.currency,
        "total_amount": str(metadata.total_amount) if metadata.total_amount is not None else None,
        "line_items": [m.model_dump() for m in matches],
    }


def submit_invoice(
    invoice: Invoice,
    metadata: InvoiceMetadata,
    ctx: ResolvedContext,
    matches: list[MatchResult],
    clinrun: ClinRunClient,
) -> SubmissionResult:
    return clinrun.submit(build_payload(invoice, metadata, ctx, matches))
