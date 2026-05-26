"""``mcp-reference`` stub service.

A minimal FastAPI app standing in for the MCP-wrapped reference API. Reuses the
``StubMCPReferenceClient`` so its responses match the in-process fixtures.
Run with: ``uvicorn backend.clients.stub_servers.mcp_app:app``.
"""

from __future__ import annotations

from fastapi import FastAPI, HTTPException

from backend.clients.errors import CatalogNotFound
from backend.clients.mcp_reference import StubMCPReferenceClient

app = FastAPI(title="mcp-reference (stub)", version="0.0.1")
_client = StubMCPReferenceClient()


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "service": "mcp-reference-stub"}


@app.post("/resolve")
def resolve(clues: dict) -> dict:
    candidates = _client.resolve_sponsor_study_site(clues)
    return {"candidates": [c.model_dump() for c in candidates]}


@app.get("/catalog")
def catalog(sponsor_id: str, study_id: str) -> dict:
    try:
        items = _client.get_catalog(sponsor_id, study_id)
    except CatalogNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"items": [item.model_dump(mode="json") for item in items]}
