"""MCP-wrapped reference client (ARCHITECTURE.md §7).

The only module that knows the reference API exists. Stages depend on the
``MCPReferenceClient`` protocol, so the source can be swapped for a fixture in
tests. Resolution returns a *ranking* of candidates (with scores), not a single
lookup — the ``context`` stage decides confidence and ambiguity.

Two implementations:
- ``StubMCPReferenceClient`` — in-process, serves ``fixtures`` (tests / offline).
- ``HttpMCPReferenceClient`` — talks to the ``mcp-reference`` service over HTTP.
"""

from __future__ import annotations

from typing import Protocol

import httpx

from backend.domain import CatalogItem, ContextCandidate

from .errors import CatalogNotFound, ReferenceUnavailable
from .fixtures import CATALOGS, SITES, SPONSORS, STUDIES


class MCPReferenceClient(Protocol):
    def resolve_sponsor_study_site(self, clues: dict) -> list[ContextCandidate]:
        """Return ranked (sponsor, study, site) candidates for the given clues."""
        ...

    def get_catalog(self, sponsor_id: str, study_id: str) -> list[CatalogItem]:
        """Return the sponsor+study-scoped catalog, or raise ``CatalogNotFound``."""
        ...


def _score(clues: dict, sponsor: dict, study: dict, site: dict) -> float:
    """Naive overlap score over the clue fields the fixtures can match.

    Deliberately simple — the real ranking lives behind the MCP server; the stub
    only needs to produce a defensible ordering for dev and tests.
    """
    score = 0.0
    name_clues = " ".join(
        str(clues.get(k, "")) for k in ("sponsor_name", "vendor_name", "site_name")
    ).lower()
    if sponsor["name"].lower() in name_clues:
        score += 0.4
    if site["name"].lower() in name_clues:
        score += 0.3
    protocol = (clues.get("protocol_number") or "").lower()
    study_name = (clues.get("study_name") or "").lower()
    if protocol and protocol == study["protocol"].lower():
        score += 0.4
    if study_name and study_name == study["name"].lower():
        score += 0.2
    # Not clamped to 1.0: study-level clues (sponsor/protocol/study) are shared by
    # every site under a study, so only the site-name match can break a tie
    # between sibling sites. Clamping would erase that signal.
    return round(score, 3)


class StubMCPReferenceClient:
    """In-process reference client backed by the shared fixtures."""

    def resolve_sponsor_study_site(self, clues: dict) -> list[ContextCandidate]:
        candidates: list[ContextCandidate] = []
        for site_id, site in SITES.items():
            study = STUDIES[site["study_id"]]
            sponsor_id = study["sponsor_id"]
            sponsor = SPONSORS[sponsor_id]
            score = _score(clues, sponsor, study, site)
            if score > 0:
                candidates.append(
                    ContextCandidate(
                        sponsor_id=sponsor_id,
                        study_id=site["study_id"],
                        site_id=site_id,
                        score=score,
                    )
                )
        candidates.sort(key=lambda c: c.score, reverse=True)
        return candidates

    def get_catalog(self, sponsor_id: str, study_id: str) -> list[CatalogItem]:
        rows = CATALOGS.get((sponsor_id, study_id))
        if not rows:
            raise CatalogNotFound(f"no catalog for {sponsor_id}/{study_id}")
        return [
            CatalogItem(
                id=row["id"],
                sponsor_id=sponsor_id,
                study_id=study_id,
                description=row["description"],
                unit_price=row["unit_price"],
            )
            for row in rows
        ]


class HttpMCPReferenceClient:
    """Reference client that calls the ``mcp-reference`` service.

    Accepts an optional ``httpx.Client`` so tests can inject an in-process
    transport. Transport/timeout errors become ``ReferenceUnavailable``; a 404 on
    the catalog endpoint becomes ``CatalogNotFound``.
    """

    def __init__(self, base_url: str, client: httpx.Client | None = None) -> None:
        self._client = client or httpx.Client(base_url=base_url, timeout=10.0)

    def resolve_sponsor_study_site(self, clues: dict) -> list[ContextCandidate]:
        try:
            resp = self._client.post("/resolve", json=clues)
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            raise ReferenceUnavailable(str(exc)) from exc
        return [ContextCandidate(**c) for c in resp.json()["candidates"]]

    def get_catalog(self, sponsor_id: str, study_id: str) -> list[CatalogItem]:
        try:
            resp = self._client.get(
                "/catalog", params={"sponsor_id": sponsor_id, "study_id": study_id}
            )
            if resp.status_code == 404:
                raise CatalogNotFound(f"no catalog for {sponsor_id}/{study_id}")
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            raise ReferenceUnavailable(str(exc)) from exc
        return [CatalogItem(**item) for item in resp.json()["items"]]
