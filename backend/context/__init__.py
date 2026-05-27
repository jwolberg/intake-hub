"""Stage: context resolution.

Resolve sponsor/study/site via the MCP reference client (PRD FR3, §7 Step 4;
ARCHITECTURE.md §7). Resolution is a *ranking*: the top candidate sets the
resolved ids and confidence.

Two failure modes are surfaced as warning codes (consumed by the decision
stage, audited, and shown in the hub):

- **Ambiguity** — a near-tied runner-up. ``ambiguous_context`` when the runner-up
  is a *different sponsor/study* (the clues disagree about who the invoice is
  for); ``multiple_site_candidates`` when it is a sibling *site* under the same
  study (PRD FR3, §15 ambiguous-metadata scenario).
- **Mismatch** — the invoice's stated sponsor/study/protocol/site contradicts the
  reference data for the resolved candidate, e.g. invoice names Sponsor A but the
  protocol maps to Sponsor B (PRD FR8, §15 mismatched-metadata scenario).

Warnings are stable string codes so the decision stage can match them and the
hub can list them; the specific conflicting values are visible in
``candidates``.
"""

from __future__ import annotations

from backend.clients.mcp_reference import MCPReferenceClient
from backend.domain import ContextCandidate, InvoiceMetadata, ResolvedContext

_AMBIGUITY_GAP = 0.1


def resolve(
    invoice_id: str,
    metadata: InvoiceMetadata,
    source: dict,
    ref: MCPReferenceClient,
) -> ResolvedContext:
    clues = {
        "sponsor_name": metadata.sponsor_name,
        "study_name": metadata.study_name,
        "protocol_number": metadata.protocol_number,
        "site_name": metadata.site_identifier,
        "vendor_name": metadata.vendor_name,
        "sender": source.get("sender"),
    }
    clues = {k: v for k, v in clues.items() if v}
    candidates = ref.resolve_sponsor_study_site(clues)
    ctx = ResolvedContext(invoice_id=invoice_id, candidates=candidates)

    if not candidates:
        ctx.warnings.append("no_context_match")
        return ctx

    top = candidates[0]
    ctx.sponsor_id = top.sponsor_id
    ctx.study_id = top.study_id
    ctx.site_id = top.site_id
    ctx.confidence = min(top.score, 1.0)

    _flag_ambiguity(ctx, candidates)
    _flag_mismatches(ctx, metadata, top)
    return ctx


def _flag_ambiguity(ctx: ResolvedContext, candidates: list[ContextCandidate]) -> None:
    """Flag a near-tied runner-up as ambiguity (PRD FR3, §15)."""
    if len(candidates) < 2:
        return
    top, runner_up = candidates[0], candidates[1]
    if runner_up.score < top.score - _AMBIGUITY_GAP:
        return
    if runner_up.sponsor_id != top.sponsor_id or runner_up.study_id != top.study_id:
        # the clues point at two different studies/sponsors — who is this for?
        ctx.warnings.append("ambiguous_context")
    elif runner_up.site_id != top.site_id:
        ctx.warnings.append("multiple_site_candidates")


def _flag_mismatches(
    ctx: ResolvedContext, metadata: InvoiceMetadata, top: ContextCandidate
) -> None:
    """Flag invoice values that contradict the resolved reference data (FR8)."""
    if _conflicts(metadata.sponsor_name, top.sponsor_name):
        ctx.warnings.append("sponsor_mismatch")
    if _conflicts(metadata.study_name, top.study_name):
        ctx.warnings.append("study_mismatch")
    if _conflicts(metadata.protocol_number, top.protocol_number):
        ctx.warnings.append("protocol_mismatch")
    if _conflicts(metadata.site_identifier, top.site_name):
        ctx.warnings.append("site_mismatch")


def _conflicts(invoice_value: str | None, reference_value: str | None) -> bool:
    """True when both values are present and neither contains the other.

    Substring/containment tolerates harmless variation ("Riverside Clinical
    Research" vs "...- West") while still catching genuine contradictions
    ("Acme Biosciences" vs "Northwind Therapeutics").
    """
    if not invoice_value or not reference_value:
        return False
    a = invoice_value.strip().lower()
    b = reference_value.strip().lower()
    if not a or not b:
        return False
    return a not in b and b not in a
