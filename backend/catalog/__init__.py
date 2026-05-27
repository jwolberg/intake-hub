"""Stage: catalog retrieval.

Fetch the sponsor+study-scoped billable catalog (PRD FR4, §7 Step 5;
ARCHITECTURE.md §7, §12).

Robustness (PRD FR4, §14, §15 catalog-failure):

- **Missing catalog** for a resolved scope raises ``CatalogNotFound`` — the
  orchestrator treats it as a *hold* reason (``catalog_unavailable``), not a crash.
- **Transport/API errors** surface as ``ReferenceUnavailable`` — the orchestrator
  treats them as a *retryable* failure (distinct from a deliberate hold).
- **Empty catalog** (scope known but no items) returns ``[]`` — matching then flags
  every line item unmatched; not conflated with a missing catalog.
- **Large catalogs** pass straight through; the matcher handles list size.

Results are cached by ``(sponsor_id, study_id)`` (ARCHITECTURE.md §7, §12
``catalog_cache``) so a batch of invoices for the same study fetches once. Only
successful fetches are cached — a transient ``ReferenceUnavailable`` is never
cached, so a retry re-hits the source.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from backend.clients.errors import CatalogNotFound
from backend.clients.mcp_reference import MCPReferenceClient
from backend.domain import CatalogItem, ResolvedContext


@runtime_checkable
class CatalogCache(Protocol):
    def get(self, sponsor_id: str, study_id: str) -> list[CatalogItem] | None:
        """Return the cached catalog for the scope, or ``None`` on a miss."""
        ...

    def put(self, sponsor_id: str, study_id: str, items: list[CatalogItem]) -> None:
        """Store the catalog for the scope."""
        ...


class InMemoryCatalogCache:
    """Process-local catalog cache keyed by ``(sponsor_id, study_id)``.

    Shared across a batch by the orchestrator so repeated same-scope invoices
    reuse one fetch. A persistent (``catalog_cache`` table) implementation can
    replace this behind the same protocol once validated against Postgres.
    """

    def __init__(self) -> None:
        self._store: dict[tuple[str, str], list[CatalogItem]] = {}

    def get(self, sponsor_id: str, study_id: str) -> list[CatalogItem] | None:
        return self._store.get((sponsor_id, study_id))

    def put(self, sponsor_id: str, study_id: str, items: list[CatalogItem]) -> None:
        self._store[(sponsor_id, study_id)] = items


def fetch(
    ctx: ResolvedContext,
    ref: MCPReferenceClient,
    cache: CatalogCache | None = None,
) -> list[CatalogItem]:
    if not (ctx.sponsor_id and ctx.study_id):
        raise CatalogNotFound("context not resolved; cannot scope catalog")

    if cache is not None:
        cached = cache.get(ctx.sponsor_id, ctx.study_id)
        if cached is not None:
            return cached

    # may raise CatalogNotFound (hold) or ReferenceUnavailable (retryable)
    items = ref.get_catalog(ctx.sponsor_id, ctx.study_id)

    if cache is not None:
        cache.put(ctx.sponsor_id, ctx.study_id, items)
    return items
