"""Stage: catalog retrieval.

Fetch the sponsor+study-scoped billable catalog (PRD FR4, §7 Step 5;
ARCHITECTURE.md §7). Raises ``CatalogNotFound`` if context is unresolved or the
scope has no catalog — the caller (orchestrator) treats that as a hold reason.
Caching + large/empty/error robustness is deepened in P2-A3.
"""

from __future__ import annotations

from backend.clients.errors import CatalogNotFound
from backend.clients.mcp_reference import MCPReferenceClient
from backend.domain import CatalogItem, ResolvedContext


def fetch(ctx: ResolvedContext, ref: MCPReferenceClient) -> list[CatalogItem]:
    if not (ctx.sponsor_id and ctx.study_id):
        raise CatalogNotFound("context not resolved; cannot scope catalog")
    return ref.get_catalog(ctx.sponsor_id, ctx.study_id)
