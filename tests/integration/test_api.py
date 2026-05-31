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


# --- page images (P4-T1) ----------------------------------------------------

_PDF = SAMPLES / "pdf" / "inv_clean_001.pdf"


def test_pages_and_image_for_rasterizable_source(client):
    # Attach the committed PDF so the page-image endpoints have a source to render.
    sample = _sample("inv_clean_001.json")
    sample["source"]["attachment_path"] = str(_PDF)
    invoice_id = client.post("/api/invoices/process", json=sample).json()["id"]

    pages = client.get(f"/api/invoices/{invoice_id}/pages").json()
    assert len(pages) == 1
    assert pages[0]["page_number"] == 1
    assert pages[0]["width"] > 0 and pages[0]["height"] > 0

    img = client.get(f"/api/invoices/{invoice_id}/pages/1/image")
    assert img.status_code == 200
    assert img.headers["content-type"] == "image/png"
    assert img.content.startswith(b"\x89PNG\r\n\x1a\n")

    # out-of-range page → 404
    assert client.get(f"/api/invoices/{invoice_id}/pages/2/image").status_code == 404


def test_pages_empty_for_body_only_invoice(client):
    # An email-body invoice has no original file: no preview, gracefully.
    invoice_id = client.post(
        "/api/invoices/process", json=_sample("inv_body_003.json")
    ).json()["id"]

    assert client.get(f"/api/invoices/{invoice_id}/pages").json() == []
    assert client.get(f"/api/invoices/{invoice_id}/pages/1/image").status_code == 404


# --- original source PDF (P5-T1; PRD §10 download) --------------------------


def test_source_pdf_served_and_flagged_for_pdf_invoice(client):
    sample = _sample("inv_clean_001.json")
    sample["source"]["attachment_path"] = str(_PDF)
    invoice_id = client.post("/api/invoices/process", json=sample).json()["id"]

    # The detail payload flags that a PDF is available.
    detail = client.get(f"/api/invoices/{invoice_id}").json()
    assert detail["source"]["has_pdf"] is True

    pdf = client.get(f"/api/invoices/{invoice_id}/source.pdf")
    assert pdf.status_code == 200
    assert pdf.headers["content-type"] == "application/pdf"
    assert pdf.content.startswith(b"%PDF")


def test_source_pdf_404_for_body_only_invoice(client):
    invoice_id = client.post(
        "/api/invoices/process", json=_sample("inv_body_003.json")
    ).json()["id"]

    detail = client.get(f"/api/invoices/{invoice_id}").json()
    assert detail["source"]["has_pdf"] is False
    assert client.get(f"/api/invoices/{invoice_id}/source.pdf").status_code == 404


def test_inline_base64_pdf_is_extracted_served_and_rendered(client):
    # Cloud path (P5-T3): the PDF rides in the payload as base64, no local path.
    import base64

    sample = _sample("inv_clean_001.json")
    sample["source"].pop("attachment_path", None)
    sample["source"]["attachment"] = "inv_clean_001.pdf"
    sample["source"]["attachment_b64"] = base64.b64encode(_PDF.read_bytes()).decode()

    invoice_id = client.post("/api/invoices/process", json=sample).json()["id"]

    # Extraction ran from the inline PDF (offline LayoutLLMClient) → fields populate.
    listing = {r["id"]: r for r in client.get("/api/invoices").json()}
    assert listing[invoice_id]["vendor_name"] == "Riverside Clinical Research"

    # Raw PDF served from the persisted blob, and pages render from those bytes.
    pdf = client.get(f"/api/invoices/{invoice_id}/source.pdf")
    assert pdf.status_code == 200 and pdf.content.startswith(b"%PDF")
    assert len(client.get(f"/api/invoices/{invoice_id}/pages").json()) == 1


def test_pages_404_for_unknown_invoice(client):
    assert client.get("/api/invoices/nope/pages").status_code == 404
    assert client.get("/api/invoices/nope/pages/1/image").status_code == 404


# --- source-anchored citations (P4-T4) --------------------------------------


def test_detail_exposes_pages_and_resolved_citations(client):
    # The controlled PDF extracts via the offline layout stand-in, so the detail
    # payload carries page rasters + source-anchored highlight boxes.
    sample = _sample("inv_clean_001.json")
    sample["source"]["attachment_path"] = str(_PDF)
    invoice_id = client.post("/api/invoices/process", json=sample).json()["id"]

    detail = client.get(f"/api/invoices/{invoice_id}").json()
    assert len(detail["pages"]) == 1

    citations = {c["target_id"]: c for c in detail["citations"]}
    inv = citations["metadata.invoice_number"]
    assert inv["quote"] == "INV-1001"
    assert inv["status"] == "extracted"
    assert inv["page_number"] == 1
    # box computed from real OCR geometry, normalized to [0,1]
    bbox = inv["bbox"]
    assert bbox is not None
    assert 0.0 <= bbox["x"] <= 1.0 and 0.0 < bbox["width"] <= 1.0
    # at least one line item is cited too
    assert any(t.startswith("line_item.") for t in citations)


def test_detail_has_no_citations_without_rasterizable_source(client):
    # An email-body invoice has no page image, so no highlight boxes.
    invoice_id = client.post(
        "/api/invoices/process", json=_sample("inv_body_003.json")
    ).json()["id"]

    detail = client.get(f"/api/invoices/{invoice_id}").json()
    assert detail["pages"] == []
    assert detail["citations"] == []


# --- human QC actions (P2-C3) -----------------------------------------------

def _process(client, name):
    return client.post("/api/invoices/process", json=_sample(name)).json()["id"]


def test_correct_metadata_records_overlay_and_reflects_state(client):
    invoice_id = _process(client, "inv_clean_001.json")

    resp = client.post(
        f"/api/invoices/{invoice_id}/corrections/metadata",
        json={"updates": {"vendor_name": "Riverside CR — West"}, "reason": "typo"},
    )
    assert resp.status_code == 200
    detail = resp.json()

    # overlay recorded; AI original preserved (not mutated in place)
    assert detail["corrections"]["metadata"]["vendor_name"] == "Riverside CR — West"
    assert detail["invoice"]["metadata"]["vendor_name"] == "Riverside Clinical Research"
    assert detail["invoice"]["status"] == "corrected"

    # human-attributed CORRECTED event with before/after (PRD §17, ARCHITECTURE §11)
    corrected = [e for e in detail["audit"] if e["action"] == "corrected"][-1]
    assert corrected["actor"] == "human"
    assert corrected["details"]["before"]["vendor_name"] == "Riverside Clinical Research"
    assert corrected["details"]["after"]["vendor_name"] == "Riverside CR — West"

    # reflected in the list row (PRD FR10) without mutating AI output
    row = next(r for r in client.get("/api/invoices").json() if r["id"] == invoice_id)
    assert row["vendor_name"] == "Riverside CR — West"


def test_correct_metadata_rejects_unknown_field(client):
    invoice_id = _process(client, "inv_clean_001.json")
    resp = client.post(
        f"/api/invoices/{invoice_id}/corrections/metadata",
        json={"updates": {"nonsense": "x"}},
    )
    assert resp.status_code == 422


def test_correct_line_item_match(client):
    invoice_id = _process(client, "inv_hold_unmatched_002.json")
    detail = client.get(f"/api/invoices/{invoice_id}").json()
    line_id = detail["line_items"][0]["id"]

    resp = client.post(
        f"/api/invoices/{invoice_id}/corrections/line-item",
        json={"line_item_id": line_id, "catalog_item_id": "cat_999",
              "catalog_description": "Manually mapped service"},
    )
    assert resp.status_code == 200
    assert resp.json()["corrections"]["line_items"][line_id]["catalog_item_id"] == "cat_999"
    assert resp.json()["invoice"]["status"] == "corrected"


def test_reviewed_note_and_escalate_record_human_events(client):
    invoice_id = _process(client, "inv_hold_unmatched_002.json")

    client.post(f"/api/invoices/{invoice_id}/reviewed", json={"note": "looks right"})
    client.post(f"/api/invoices/{invoice_id}/note", json={"note": "pinged sponsor"})
    escalated = client.post(f"/api/invoices/{invoice_id}/escalate", json={"reason": "ambiguous"})

    detail = escalated.json()
    assert detail["invoice"]["status"] == "escalated"
    by_action = {e["action"]: e for e in detail["audit"]}
    assert by_action["reviewed"]["actor"] == "human"
    assert by_action["note"]["details"]["reason"] == "pinged sponsor"
    assert by_action["escalated"]["details"]["reason"] == "ambiguous"

    # escalating a held invoice confirms the hold was genuinely needed (metrics)
    metrics = client.get("/api/metrics").json()
    assert metrics["hold_precision"] == 1.0


def test_qc_action_404_for_unknown_invoice(client):
    assert client.post("/api/invoices/nope/reviewed", json={}).status_code == 404


# --- rerun with corrected data (P2-C4) --------------------------------------

def test_rerun_after_metadata_correction_resolves_hold(client):
    # holds for context_mismatch: invoice says "Acme Biosciences" but the protocol
    # maps to Northwind in the reference data
    invoice_id = _process(client, "inv_hold_mismatch_005.json")
    assert client.get(f"/api/invoices/{invoice_id}").json()["invoice"]["status"] == "held"

    # a reviewer corrects the sponsor to the reference's canonical name
    client.post(
        f"/api/invoices/{invoice_id}/corrections/metadata",
        json={"updates": {"sponsor_name": "Northwind Therapeutics"}, "reason": "wrong sponsor"},
    )

    detail = client.post(f"/api/invoices/{invoice_id}/rerun", json={}).json()
    # the corrected metadata clears the mismatch → the invoice now submits
    assert detail["invoice"]["status"] == "submitted"

    # the rerun is recorded as a human action naming the corrected field (PRD §17)
    rerun_event = [e for e in detail["audit"] if e["action"] == "rerun"][-1]
    assert rerun_event["actor"] == "human"
    assert rerun_event["details"]["corrected_fields"] == ["sponsor_name"]
    # AI original preserved; correction stays an overlay (ARCHITECTURE §11)
    assert detail["invoice"]["metadata"]["sponsor_name"] == "Acme Biosciences"
    assert detail["corrections"]["metadata"]["sponsor_name"] == "Northwind Therapeutics"


def test_rerun_after_line_item_correction_resolves_hold(client):
    # holds for an unmatched material line ("Investigator travel reimbursement")
    invoice_id = _process(client, "inv_hold_unmatched_002.json")
    detail = client.get(f"/api/invoices/{invoice_id}").json()
    assert detail["invoice"]["status"] == "held"
    unmatched = next(
        m["line_item_id"] for m in detail["matches"] if m["catalog_item_id"] is None
    )

    # a reviewer maps the unmatched line to a catalog item
    client.post(
        f"/api/invoices/{invoice_id}/corrections/line-item",
        json={"line_item_id": unmatched, "catalog_item_id": "cat_004",
              "catalog_description": "Pharmacy Dispensing Fee"},
    )

    detail = client.post(f"/api/invoices/{invoice_id}/rerun", json={}).json()
    # the pinned match clears the unmatched-line hold → submits
    assert detail["invoice"]["status"] == "submitted"
    pinned = next(m for m in detail["matches"] if m["line_item_id"] == unmatched)
    assert pinned["catalog_item_id"] == "cat_004"
    assert pinned["confidence"] == 1.0


def test_rerun_unknown_invoice_404(client):
    assert client.post("/api/invoices/nope/rerun", json={}).status_code == 404


# --- confirm citation (P4-T6) -----------------------------------------------


def test_confirm_citation_records_human_event(client):
    sample = _sample("inv_clean_001.json")
    sample["source"]["attachment_path"] = str(_PDF)
    invoice_id = client.post("/api/invoices/process", json=sample).json()["id"]

    resp = client.post(
        f"/api/invoices/{invoice_id}/citations/confirm",
        json={"target_id": "metadata.invoice_number", "reason": "matches the page"},
    )
    assert resp.status_code == 200
    detail = resp.json()

    confirmed = [e for e in detail["audit"] if e["action"] == "confirmed"][-1]
    assert confirmed["actor"] == "human"
    assert confirmed["details"]["target_id"] == "metadata.invoice_number"
    assert confirmed["details"]["reason"] == "matches the page"


def test_confirm_citation_unknown_invoice_404(client):
    resp = client.post(
        "/api/invoices/nope/citations/confirm", json={"target_id": "metadata.x"}
    )
    assert resp.status_code == 404


def test_metrics_endpoint(client):
    client.post("/api/invoices/process", json=_sample("inv_clean_001.json"))
    client.post("/api/invoices/process", json=_sample("inv_hold_unmatched_002.json"))

    metrics = client.get("/api/metrics").json()
    assert metrics["total"] == 2
    assert metrics["submitted"] == 1 and metrics["held"] == 1
    assert metrics["auto_submit_rate"] == 0.5
    assert metrics["hold_precision"] is None  # nothing dispositioned yet
