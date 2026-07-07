"""API tests (ledger pipeline).

Drive the routes through FastAPI ``TestClient`` with the repository and pipeline
clients overridden to in-process stubs — no DB, no network. Verifies the
process → list → detail flow, the human-QC routes, and the 404 paths against the
ledger taxonomy (posted/held/failed).
"""

import json
import pathlib

import pytest
from backend.api.main import app, get_pipeline_clients, get_repo
from backend.clients import PassthroughLLMClient, StubSheetsClient
from backend.db.repository import InMemoryRepository
from fastapi.testclient import TestClient

SAMPLES = pathlib.Path(__file__).resolve().parents[2] / "samples"

# inv_clean_001 auto-files under the ledger engine (vendor + total + a categorizable
# line); inv_body_003 holds (no line description matches a Schedule C keyword →
# low_category_confidence). Verified against the live engine.
POSTS = "inv_clean_001.json"
HOLDS = "inv_body_003.json"

# A document that holds only for a missing amount (a categorizable line + vendor, no
# total) — correcting the total on rerun clears the hold and it files.
HOLD_MISSING_TOTAL = {
    "source": {"channel": "email", "message_id": "m-missing-total",
               "subject": "Adobe receipt", "sender": "billing@adobe.com"},
    "document": {
        "metadata": {"vendor_name": "Adobe", "currency": "USD"},
        "line_items": [{"raw_description": "Creative Cloud subscription",
                        "quantity": "1", "unit_price": "52.99", "total": "52.99"}],
    },
}


@pytest.fixture
def client():
    repo = InMemoryRepository()
    app.dependency_overrides[get_repo] = lambda: repo
    app.dependency_overrides[get_pipeline_clients] = lambda: {
        "llm": PassthroughLLMClient(),
        "sheets": StubSheetsClient(),
    }
    yield TestClient(app)
    app.dependency_overrides.clear()


def _sample(name: str) -> dict:
    return json.loads((SAMPLES / name).read_text())


def test_process_list_and_detail(client):
    # process a clean expense → auto-files to the ledger
    resp = client.post("/api/invoices/process", json=_sample(POSTS))
    assert resp.status_code == 200
    summary = resp.json()
    assert summary["status"] == "posted"
    assert summary["decision"] == "submit"
    assert summary["exception_count"] == 0
    invoice_id = summary["id"]

    # it appears in the list
    listing = client.get("/api/invoices").json()
    assert [row["id"] for row in listing] == [invoice_id]

    # detail exposes every stage output + audit trail
    detail = client.get(f"/api/invoices/{invoice_id}").json()
    assert len(detail["line_items"]) == 4
    assert [e["action"] for e in detail["audit"]][-1] == "posted"


def test_process_hold_records_exception(client):
    resp = client.post("/api/invoices/process", json=_sample(HOLDS))
    summary = resp.json()
    assert summary["status"] == "held"
    assert summary["exception_count"] >= 1

    detail = client.get(f"/api/invoices/{summary['id']}").json()
    assert any(e["type"] == "low_category_confidence" for e in detail["exceptions"])


def test_list_view_filters(client):
    posted_id = client.post("/api/invoices/process", json=_sample(POSTS)).json()["id"]
    held_id = client.post("/api/invoices/process", json=_sample(HOLDS)).json()["id"]

    # every row carries triage tags (one source of truth for the chips)
    rows = {row["id"]: row for row in client.get("/api/invoices").json()}
    assert "posted" in rows[posted_id]["filter_tags"]
    assert {"held", "needs_review"} <= set(rows[held_id]["filter_tags"])

    # each filter narrows to the matching invoices
    def ids(filter_key):
        return {row["id"] for row in client.get(f"/api/invoices?filter={filter_key}").json()}

    assert ids("posted") == {posted_id}
    assert ids("held") == {held_id}
    assert ids("needs_review") == {held_id}


def test_unknown_filter_is_rejected(client):
    assert client.get("/api/invoices?filter=bogus").status_code == 422


def test_detail_exposes_source_and_extraction_signals(client):
    invoice_id = client.post("/api/invoices/process", json=_sample(POSTS)).json()["id"]
    detail = client.get(f"/api/invoices/{invoice_id}").json()

    # Source section projected from the received audit event.
    assert detail["source"]["sender"] == "billing@riverside-cr.example"
    assert detail["source"]["attachment"] == "INV-1001.pdf"

    # Per-field extraction confidence + evidence surfaced.
    assert detail["extraction"]["field_confidence"]["invoice_number"] > 0
    assert "invoice_number" in detail["extraction"]["field_evidence"]

    # A filed item records its risk flags on the posted event (Decision section).
    posted = [e for e in detail["audit"] if e["action"] == "posted"][-1]
    assert "risk_flags" in posted["details"]


def test_detail_404_for_unknown_invoice(client):
    assert client.get("/api/invoices/nope").status_code == 404


# --- page images ------------------------------------------------------------

_PDF = SAMPLES / "pdf" / "inv_clean_001.pdf"


def test_pages_and_image_for_rasterizable_source(client):
    # Attach the committed PDF so the page-image endpoints have a source to render.
    sample = _sample(POSTS)
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
    # An email-body item has no original file: no preview, gracefully.
    invoice_id = client.post("/api/invoices/process", json=_sample(HOLDS)).json()["id"]

    assert client.get(f"/api/invoices/{invoice_id}/pages").json() == []
    assert client.get(f"/api/invoices/{invoice_id}/pages/1/image").status_code == 404


# --- original source PDF (download) -----------------------------------------


def test_source_pdf_served_and_flagged_for_pdf_invoice(client):
    sample = _sample(POSTS)
    sample["source"]["attachment_path"] = str(_PDF)
    invoice_id = client.post("/api/invoices/process", json=sample).json()["id"]

    detail = client.get(f"/api/invoices/{invoice_id}").json()
    assert detail["source"]["has_pdf"] is True

    pdf = client.get(f"/api/invoices/{invoice_id}/source.pdf")
    assert pdf.status_code == 200
    assert pdf.headers["content-type"] == "application/pdf"
    assert pdf.content.startswith(b"%PDF")


def test_source_pdf_404_for_body_only_invoice(client):
    invoice_id = client.post("/api/invoices/process", json=_sample(HOLDS)).json()["id"]

    detail = client.get(f"/api/invoices/{invoice_id}").json()
    assert detail["source"]["has_pdf"] is False
    assert client.get(f"/api/invoices/{invoice_id}/source.pdf").status_code == 404


# --- workflow trace (observability) -----------------------------------------


def test_trace_reconstructs_pipeline_path_for_posted(client):
    invoice_id = client.post("/api/invoices/process", json=_sample(POSTS)).json()["id"]

    trace = client.get(f"/api/invoices/{invoice_id}/trace").json()
    assert trace["status"] == "posted"
    stages = [s["stage"] for s in trace["steps"]]
    assert stages == ["received", "parsed", "extracted", "classified",
                      "categorized", "posted"]
    assert all(s["ok"] for s in trace["steps"])


def test_trace_marks_failed_stage_typed_and_retryable(client):
    # Override clients so the Sheet append always fails → a retryable failure.
    failing = StubSheetsClient()
    failing.fail_always = True
    app.dependency_overrides[get_pipeline_clients] = lambda: {
        "llm": PassthroughLLMClient(), "sheets": failing,
    }
    invoice_id = client.post("/api/invoices/process", json=_sample(POSTS)).json()["id"]

    trace = client.get(f"/api/invoices/{invoice_id}/trace").json()
    assert trace["status"] == "failed"
    last = trace["steps"][-1]
    assert last["ok"] is False
    assert last["kind"] == "sheet_write_failed"
    assert last["retryable"] is True


def test_inline_base64_pdf_is_extracted_served_and_rendered(client):
    # Cloud path: the PDF rides in the payload as base64, no local path.
    import base64

    sample = _sample(POSTS)
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


# --- source-anchored citations ----------------------------------------------


def test_detail_exposes_pages_and_resolved_citations(client):
    # The controlled PDF extracts via the offline layout stand-in, so the detail
    # payload carries page rasters + source-anchored highlight boxes.
    sample = _sample(POSTS)
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
    invoice_id = client.post("/api/invoices/process", json=_sample(HOLDS)).json()["id"]

    detail = client.get(f"/api/invoices/{invoice_id}").json()
    assert detail["pages"] == []
    assert detail["citations"] == []


# --- human QC actions -------------------------------------------------------

def _process(client, name):
    return client.post("/api/invoices/process", json=_sample(name)).json()["id"]


def test_correct_metadata_records_overlay_and_reflects_state(client):
    invoice_id = _process(client, POSTS)

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

    # human-attributed CORRECTED event with before/after
    corrected = [e for e in detail["audit"] if e["action"] == "corrected"][-1]
    assert corrected["actor"] == "human"
    assert corrected["details"]["before"]["vendor_name"] == "Riverside Clinical Research"
    assert corrected["details"]["after"]["vendor_name"] == "Riverside CR — West"

    # reflected in the list row without mutating AI output
    row = next(r for r in client.get("/api/invoices").json() if r["id"] == invoice_id)
    assert row["vendor_name"] == "Riverside CR — West"


def test_correct_metadata_rejects_unknown_field(client):
    invoice_id = _process(client, POSTS)
    resp = client.post(
        f"/api/invoices/{invoice_id}/corrections/metadata",
        json={"updates": {"nonsense": "x"}},
    )
    assert resp.status_code == 422


def test_reviewed_note_and_escalate_record_human_events(client):
    invoice_id = _process(client, HOLDS)  # a held item

    client.post(f"/api/invoices/{invoice_id}/reviewed", json={"note": "looks right"})
    client.post(f"/api/invoices/{invoice_id}/note", json={"note": "checked the vendor"})
    escalated = client.post(f"/api/invoices/{invoice_id}/escalate", json={"reason": "ambiguous"})

    detail = escalated.json()
    assert detail["invoice"]["status"] == "escalated"
    by_action = {e["action"]: e for e in detail["audit"]}
    assert by_action["reviewed"]["actor"] == "human"
    assert by_action["note"]["details"]["reason"] == "checked the vendor"
    assert by_action["escalated"]["details"]["reason"] == "ambiguous"

    # escalating a held item confirms the hold was genuinely needed (metrics)
    metrics = client.get("/api/metrics").json()
    assert metrics["hold_precision"] == 1.0


def test_qc_action_404_for_unknown_invoice(client):
    assert client.post("/api/invoices/nope/reviewed", json={}).status_code == 404


# --- rerun with corrected data ----------------------------------------------

def test_rerun_after_metadata_correction_resolves_hold(client):
    # holds for a missing amount (missing_total, and a weak income/expense call
    # without the total), but the category is clear
    resp = client.post("/api/invoices/process", json=HOLD_MISSING_TOTAL)
    invoice_id = resp.json()["id"]
    assert resp.json()["status"] == "held"

    # a reviewer supplies the missing total
    client.post(
        f"/api/invoices/{invoice_id}/corrections/metadata",
        json={"updates": {"total_amount": "52.99"}, "reason": "read off the receipt"},
    )

    detail = client.post(f"/api/invoices/{invoice_id}/rerun", json={}).json()
    # the corrected total clears the hold → the item now files
    assert detail["invoice"]["status"] == "posted"

    # the rerun is recorded as a human action naming the corrected field
    rerun_event = [e for e in detail["audit"] if e["action"] == "rerun"][-1]
    assert rerun_event["actor"] == "human"
    assert rerun_event["details"]["corrected_fields"] == ["total_amount"]
    # after rerun the invoice's effective metadata reflects the correction, so the
    # filed Sheet row shows the corrected amount (not the stale AI value)
    assert detail["invoice"]["metadata"]["total_amount"] == "52.99"
    # the correction is still recorded as a human overlay on the audit trail
    assert detail["corrections"]["metadata"]["total_amount"] == "52.99"
    correction = [e for e in detail["audit"]
                  if e["action"] == "corrected" and e["details"].get("target") == "metadata"][-1]
    assert correction["details"]["before"]["total_amount"] is None  # AI original preserved


def test_rerun_unknown_invoice_404(client):
    assert client.post("/api/invoices/nope/rerun", json={}).status_code == 404


# --- confirm citation -------------------------------------------------------


def test_confirm_citation_records_human_event(client):
    sample = _sample(POSTS)
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


# --- category correction, reject, review queue (U10) ------------------------

# An ambiguous expense (office vs supplies) holds for low_category_confidence with
# the runner-up offered as a candidate — the AE2 review scenario.
AMBIGUOUS = {
    "source": {"channel": "email", "message_id": "m-amb", "subject": "Depot order",
               "sender": "orders@depot.example"},
    "document": {
        "metadata": {"vendor_name": "Depot", "invoice_date": "2026-03-01",
                     "currency": "USD", "total_amount": "30.00"},
        "line_items": [{"raw_description": "office supplies", "quantity": "1",
                        "unit_price": "30.00", "total": "30.00"}],
    },
}


def test_held_item_shows_candidate_categories_and_correction_files_it(client):
    # AE2: a held ambiguous item surfaces candidate categories; correcting the
    # category posts it, with the AI original preserved as an overlay.
    invoice_id = client.post("/api/invoices/process", json=AMBIGUOUS).json()["id"]
    detail = client.get(f"/api/invoices/{invoice_id}").json()
    assert detail["invoice"]["status"] == "held"
    assert detail["categorization"]["alternates"]  # candidate categories shown

    corrected = client.post(
        f"/api/invoices/{invoice_id}/corrections/category",
        json={"category": "Office expense", "reason": "it's software/office"},
    ).json()
    assert corrected["invoice"]["status"] == "corrected"
    assert corrected["corrections"]["category"] == "Office expense"

    reran = client.post(f"/api/invoices/{invoice_id}/rerun", json={}).json()
    assert reran["invoice"]["status"] == "posted"


def test_reject_non_receipt_removes_from_queue_and_writes_no_row(client):
    # AE4: rejecting a non-receipt takes it out of the queue and never files it.
    invoice_id = client.post("/api/invoices/process", json=_sample(HOLDS)).json()["id"]
    rejected = client.post(f"/api/invoices/{invoice_id}/reject", json={"note": "spam"}).json()
    assert rejected["invoice"]["status"] == "rejected"

    # gone from the review queue; the audit shows the human rejection
    queue_ids = {r["id"] for r in client.get("/api/review-queue").json()}
    assert invoice_id not in queue_ids
    assert any(e["action"] == "rejected" for e in rejected["audit"])


def test_review_queue_lists_held_items_by_reason(client):
    client.post("/api/invoices/process", json=_sample(HOLDS))
    client.post("/api/invoices/process", json=AMBIGUOUS)
    queue = client.get("/api/review-queue").json()
    assert len(queue) == 2
    assert all(row["reason"] for row in queue)  # each carries its primary hold reason
    # grouped/ordered by reason
    assert [r["reason"] for r in queue] == sorted(r["reason"] for r in queue)


def test_metrics_endpoint(client):
    client.post("/api/invoices/process", json=_sample(POSTS))
    client.post("/api/invoices/process", json=_sample(HOLDS))

    metrics = client.get("/api/metrics").json()
    assert metrics["total"] == 2
    assert metrics["submitted"] == 1 and metrics["held"] == 1
    assert metrics["auto_submit_rate"] == 0.5
    assert metrics["hold_precision"] is None  # nothing dispositioned yet
