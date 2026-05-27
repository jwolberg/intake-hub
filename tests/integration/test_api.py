"""API tests (P1-T10).

Drive the routes through FastAPI ``TestClient`` with the repository and pipeline
clients overridden to in-process stubs — no DB, no network. Verifies the
process → list → detail flow and the 404 path.
"""

import json
import pathlib

import pytest
from backend.api.main import app, get_pipeline_clients, get_repo
from backend.clients import PassthroughLLMClient, StubClinRunClient, StubMCPReferenceClient
from backend.db.repository import InMemoryRepository
from fastapi.testclient import TestClient

SAMPLES = pathlib.Path(__file__).resolve().parents[2] / "samples"


@pytest.fixture
def client():
    repo = InMemoryRepository()
    app.dependency_overrides[get_repo] = lambda: repo
    app.dependency_overrides[get_pipeline_clients] = lambda: {
        "llm": PassthroughLLMClient(),
        "ref": StubMCPReferenceClient(),
        "clinrun": StubClinRunClient(),
    }
    yield TestClient(app)
    app.dependency_overrides.clear()


def _sample(name: str) -> dict:
    return json.loads((SAMPLES / name).read_text())


def test_process_list_and_detail(client):
    # process a clean invoice
    resp = client.post("/api/invoices/process", json=_sample("inv_clean_001.json"))
    assert resp.status_code == 200
    summary = resp.json()
    assert summary["status"] == "submitted"
    assert summary["decision"] == "submit"
    assert summary["sponsor_id"] == "sponsor_001"
    assert summary["exception_count"] == 0
    invoice_id = summary["id"]

    # it appears in the list
    listing = client.get("/api/invoices").json()
    assert [row["id"] for row in listing] == [invoice_id]

    # detail exposes every stage output + audit trail
    detail = client.get(f"/api/invoices/{invoice_id}").json()
    assert len(detail["line_items"]) == 4
    assert detail["context"]["site_id"] == "site_001"
    assert [e["action"] for e in detail["audit"]][-1] == "submitted"


def test_process_hold_records_exception(client):
    resp = client.post("/api/invoices/process", json=_sample("inv_hold_unmatched_002.json"))
    summary = resp.json()
    assert summary["status"] == "held"
    assert summary["exception_count"] >= 1

    detail = client.get(f"/api/invoices/{summary['id']}").json()
    assert any(e["type"] == "unmatched_line_item" for e in detail["exceptions"])


def test_list_view_filters(client):
    # one submitted, one held (with an unmatched line item)
    submitted_id = client.post(
        "/api/invoices/process", json=_sample("inv_clean_001.json")
    ).json()["id"]
    held_id = client.post(
        "/api/invoices/process", json=_sample("inv_hold_unmatched_002.json")
    ).json()["id"]

    # every row carries triage tags (one source of truth for the chips)
    rows = {row["id"]: row for row in client.get("/api/invoices").json()}
    assert "submitted" in rows[submitted_id]["filter_tags"]
    assert {"held", "needs_review", "unmatched_line_items"} <= set(rows[held_id]["filter_tags"])

    # each filter narrows to the matching invoices
    def ids(filter_key):
        return {row["id"] for row in client.get(f"/api/invoices?filter={filter_key}").json()}

    assert ids("submitted") == {submitted_id}
    assert ids("held") == {held_id}
    assert ids("needs_review") == {held_id}
    assert ids("unmatched_line_items") == {held_id}


def test_unknown_filter_is_rejected(client):
    assert client.get("/api/invoices?filter=bogus").status_code == 422


def test_detail_exposes_source_and_extraction_signals(client):
    invoice_id = client.post(
        "/api/invoices/process", json=_sample("inv_clean_001.json")
    ).json()["id"]
    detail = client.get(f"/api/invoices/{invoice_id}").json()

    # Source section projected from the received audit event (PRD §10).
    assert detail["source"]["sender"] == "billing@riverside-cr.example"
    assert detail["source"]["attachment"] == "INV-1001.pdf"

    # Per-field extraction confidence + evidence surfaced (PRD §10).
    assert detail["extraction"]["field_confidence"]["invoice_number"] > 0
    assert "invoice_number" in detail["extraction"]["field_evidence"]

    # A submit records its risk flags on the submitted event (Decision section).
    submitted = [e for e in detail["audit"] if e["action"] == "submitted"][-1]
    assert "risk_flags" in submitted["details"]


def test_detail_404_for_unknown_invoice(client):
    assert client.get("/api/invoices/nope").status_code == 404


def test_metrics_endpoint(client):
    client.post("/api/invoices/process", json=_sample("inv_clean_001.json"))
    client.post("/api/invoices/process", json=_sample("inv_hold_unmatched_002.json"))

    metrics = client.get("/api/metrics").json()
    assert metrics["total"] == 2
    assert metrics["submitted"] == 1 and metrics["held"] == 1
    assert metrics["auto_submit_rate"] == 0.5
    assert metrics["hold_precision"] is None  # nothing dispositioned yet
