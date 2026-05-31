"""Integration test for inbox intake (P6-T6).

Drives POST /api/inbox/fetch through FastAPI TestClient with the repository,
pipeline clients, and inbox all overridden to in-process offline stubs (no DB, no
network, no PDF rendering). Verifies the demo messages land in their expected
SUBMIT/HOLD states and that a re-fetch is idempotent (no duplicate invoices).
"""

import pytest
from backend.api.main import app, get_inbox, get_pipeline_clients, get_repo
from backend.clients import PassthroughLLMClient, StubClinRunClient, StubMCPReferenceClient
from backend.db.repository import InMemoryRepository
from backend.inbox import DEMO_STEMS, MockInbox
from fastapi.testclient import TestClient

# Expected terminal outcome per demo message (the curated submit / hold set, PRD §19).
EXPECTED = {
    "inv_clean_001": "submitted",
    "inv_hold_unmatched_002": "held",
    "inv_hold_mismatch_005": "held",
    "inv_uncertain_006": "held",
    "inv_ambiguous_008": "held",
    "inv_large_007": "submitted",
}


@pytest.fixture
def client():
    repo = InMemoryRepository()
    app.dependency_overrides[get_repo] = lambda: repo
    app.dependency_overrides[get_pipeline_clients] = lambda: {
        "llm": PassthroughLLMClient(),
        "ref": StubMCPReferenceClient(),
        "clinrun": StubClinRunClient(),
    }
    app.dependency_overrides[get_inbox] = lambda: MockInbox(render_pdf=False)
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_fetch_processes_demo_messages_to_expected_outcomes(client):
    body = client.post("/api/inbox/fetch").json()
    assert body["count"] == len(DEMO_STEMS)
    assert body["skipped"] == 0
    outcomes = {row["message_id"]: row["status"] for row in body["received"]}
    assert outcomes == EXPECTED
    # Every received invoice is listed in the hub.
    assert len(client.get("/api/invoices").json()) == len(DEMO_STEMS)


def test_refetch_is_idempotent(client):
    first = client.post("/api/inbox/fetch").json()
    assert first["count"] == len(DEMO_STEMS)

    second = client.post("/api/inbox/fetch").json()
    assert second["count"] == 0
    assert second["skipped"] == len(DEMO_STEMS)
    assert second["received"] == []

    # No duplicate invoices were created by the second fetch.
    assert len(client.get("/api/invoices").json()) == len(DEMO_STEMS)
