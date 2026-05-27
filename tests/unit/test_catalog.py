"""Unit tests for the catalog stage (P2-A3): caching + robustness (PRD FR4, §14)."""

import pytest
from backend.catalog import InMemoryCatalogCache, fetch
from backend.clients.errors import CatalogNotFound, ReferenceUnavailable
from backend.domain import CatalogItem, ResolvedContext

CTX = ResolvedContext(invoice_id="inv_x", sponsor_id="sponsor_001", study_id="study_001")


class CountingRef:
    """Reference client that counts catalog calls and serves a scripted result."""

    def __init__(self, items=None, error=None):
        self._items = items if items is not None else []
        self._error = error
        self.calls = 0

    def resolve_sponsor_study_site(self, clues):  # pragma: no cover - unused here
        return []

    def get_catalog(self, sponsor_id, study_id):
        self.calls += 1
        if self._error is not None:
            raise self._error
        return list(self._items)


def _items(n=2):
    return [
        CatalogItem(id=f"cat_{i}", sponsor_id="sponsor_001", study_id="study_001",
                    description=f"Service {i}", unit_price="10.00")
        for i in range(n)
    ]


def test_unresolved_context_raises_not_found():
    ref = CountingRef(_items())
    with pytest.raises(CatalogNotFound):
        fetch(ResolvedContext(invoice_id="inv_x"), ref)
    assert ref.calls == 0  # never reaches the reference


def test_missing_catalog_propagates_not_found():
    ref = CountingRef(error=CatalogNotFound("no catalog"))
    with pytest.raises(CatalogNotFound):
        fetch(CTX, ref)


def test_transport_error_propagates_as_unavailable():
    ref = CountingRef(error=ReferenceUnavailable("timeout"))
    with pytest.raises(ReferenceUnavailable):
        fetch(CTX, ref)


def test_empty_catalog_returns_empty_list():
    ref = CountingRef(items=[])
    assert fetch(CTX, ref) == []


def test_large_catalog_passes_through():
    ref = CountingRef(_items(2000))
    result = fetch(CTX, ref)
    assert len(result) == 2000


def test_cache_serves_repeat_fetch_without_recalling():
    ref = CountingRef(_items())
    cache = InMemoryCatalogCache()
    first = fetch(CTX, ref, cache=cache)
    second = fetch(CTX, ref, cache=cache)
    assert ref.calls == 1  # second fetch hit the cache
    assert first == second


def test_transport_error_is_not_cached():
    ref = CountingRef(error=ReferenceUnavailable("timeout"))
    cache = InMemoryCatalogCache()
    with pytest.raises(ReferenceUnavailable):
        fetch(CTX, ref, cache=cache)
    assert cache.get("sponsor_001", "study_001") is None  # retry can re-hit source
