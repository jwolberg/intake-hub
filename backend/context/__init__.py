"""Stage: context resolution.

Resolve sponsor/study/site via the MCP reference client (PRD FR3, §7 Step 4;
ARCHITECTURE.md §7). Resolution is a *ranking*: the top candidate sets the
resolved ids, a near-tied sibling raises an ambiguity warning, and no match
leaves the context unresolved. Mismatch detection (invoice vs reference) is
deepened in P2-A2.
"""

from __future__ import annotations

from backend.clients.mcp_reference import MCPReferenceClient
from backend.domain import InvoiceMetadata, ResolvedContext

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

    runner_up = candidates[1] if len(candidates) > 1 else None
    if (
        runner_up is not None
        and runner_up.site_id != top.site_id
        and runner_up.score >= top.score - _AMBIGUITY_GAP
    ):
        ctx.warnings.append("multiple_site_candidates")

    return ctx
