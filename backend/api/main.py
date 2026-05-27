"""FastAPI application entrypoint.

Phase 0 exposes only ``/health`` and bootstraps the schema on startup. The
invoice routes (``/process``, ``/invoices``, ...) arrive in Phase 1 (P1-T10).

Schema bootstrap is best-effort: if the database is not reachable at startup the
app still boots and ``/health`` reports ``db: down`` rather than crashing — one
unavailable dependency should be visible, not fatal (ARCHITECTURE.md §15).
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from backend import corrections
from backend.audit import latest_details, record
from backend.audit.metrics import WorkflowMetrics, compute_metrics
from backend.clients import get_clinrun_client, get_llm_client, get_reference_client
from backend.config import settings
from backend.db import get_engine, init_schema
from backend.db.repository import Repository, get_repository
from backend.domain import Actor, AuditAction, Decision, InvoiceMetadata, InvoiceStatus
from backend.orchestrator import process

logger = logging.getLogger("invoicescreener.api")


@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        init_schema()
        logger.info("schema initialized")
    except SQLAlchemyError as exc:  # pragma: no cover - depends on live DB
        logger.warning("schema init skipped, database unavailable: %s", exc)
    yield


app = FastAPI(title="InvoiceScreener", version="0.0.1", lifespan=lifespan)

# The hub is served from a different origin (Vite :5173) than the API (:8000),
# so browser fetches need CORS. Origins are configurable via CORS_ORIGINS.
app.add_middleware(
    CORSMiddleware,
    allow_origins=list(settings.cors_origins),
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health() -> dict:
    """Liveness probe plus a best-effort database connectivity check."""
    db_status = "up"
    try:
        with get_engine().connect() as conn:
            conn.execute(text("SELECT 1"))
    except SQLAlchemyError:
        db_status = "down"
    return {"status": "ok", "service": "invoicescreener-api", "db": db_status}


# --- dependencies (overridable in tests) ------------------------------------

def get_repo() -> Repository:
    return get_repository()


def get_pipeline_clients() -> dict:
    return {
        "llm": get_llm_client(),
        "ref": get_reference_client(),
        "clinrun": get_clinrun_client(),
    }


RepoDep = Annotated[Repository, Depends(get_repo)]
ClientsDep = Annotated[dict, Depends(get_pipeline_clients)]


# --- list-view filters (PRD §10 Invoice List View) --------------------------
# One source of truth for both the ?filter= query param and the hub's filter
# chips: each invoice carries a set of tags derived from its status + the signals
# the reviewer triages on (PRD §10 Suggested filters; USERS § Reviewer).

# Held/failed invoices are the ones still awaiting a human (needs review).
_NEEDS_REVIEW_STATUSES = {InvoiceStatus.HELD, InvoiceStatus.FAILED}
# Exception codes that signal the AI was unsure (decision/extraction/match/context).
_LOW_CONFIDENCE_FLAGS = {
    "low_extraction_confidence", "moderate_extraction_confidence",
    "low_match_confidence", "weak_match", "low_confidence", "context_unresolved",
}
# A submit below this confidence is worth a second look even though it cleared the floor.
_SUBMIT_CONFIDENCE_WATCH = 0.75

# Filter keys the API accepts (PRD §10). Statuses double as tags so the same
# membership test serves every chip.
FILTER_KEYS = {
    "submitted", "held", "failed", "needs_review",
    "low_confidence", "mismatched_metadata", "unmatched_line_items",
}


def _filter_tags(invoice, exceptions, matches, ctx) -> list[str]:
    """Triage tags for an invoice (PRD §10 Suggested filters)."""
    tags = {invoice.status.value}
    if invoice.status in _NEEDS_REVIEW_STATUSES:
        tags.add("needs_review")

    exc_types = {e.type for e in exceptions}
    if exc_types & _LOW_CONFIDENCE_FLAGS or (
        invoice.decision is Decision.SUBMIT
        and invoice.decision_confidence is not None
        and invoice.decision_confidence < _SUBMIT_CONFIDENCE_WATCH
    ):
        tags.add("low_confidence")

    # Context warnings ending in "_mismatch" are invoice-vs-reference contradictions.
    if "context_mismatch" in exc_types or (
        ctx and any(w.endswith("_mismatch") for w in ctx.warnings)
    ):
        tags.add("mismatched_metadata")

    if "unmatched_line_item" in exc_types or any(m.catalog_item_id is None for m in matches):
        tags.add("unmatched_line_items")

    return sorted(tags)


def _summary(invoice, repo: Repository) -> dict:
    """List-row view of an invoice (PRD §10 Invoice List View)."""
    ctx = repo.get_context(invoice.id)
    exceptions = repo.get_exceptions(invoice.id)
    matches = repo.get_matches(invoice.id)
    # Reflect human metadata corrections in the listed fields (PRD FR10) without
    # mutating the AI output: apply the overlay from the audit trail.
    meta = corrections.effective_metadata(invoice.metadata, repo.get_audit(invoice.id))
    total = meta.total_amount
    return {
        "id": invoice.id,
        "status": invoice.status.value,
        "decision": invoice.decision.value if invoice.decision else None,
        "decision_confidence": invoice.decision_confidence,
        "invoice_number": meta.invoice_number,
        "vendor_name": meta.vendor_name,
        "total_amount": str(total) if total is not None else None,
        "sponsor_id": ctx.sponsor_id if ctx else None,
        "study_id": ctx.study_id if ctx else None,
        "site_id": ctx.site_id if ctx else None,
        "exception_count": len(exceptions),
        "filter_tags": _filter_tags(invoice, exceptions, matches, ctx),
        "updated_at": invoice.updated_at,
    }


# --- invoice routes (PRD §13) -----------------------------------------------

@app.post("/api/invoices/process")
def process_invoice(sample: dict, repo: RepoDep, clients: ClientsDep) -> dict:
    """Run a sample invoice through the AI pipeline and return its outcome."""
    invoice = process(sample, repo, **clients)
    return _summary(invoice, repo)


@app.get("/api/invoices")
def list_invoices(
    repo: RepoDep,
    filter: str | None = Query(default=None, description="One of FILTER_KEYS; omit for all."),
) -> list[dict]:
    """List invoices, optionally narrowed to one triage filter (PRD §10)."""
    rows = [_summary(inv, repo) for inv in repo.list_invoices()]
    if filter is None:
        return rows
    if filter not in FILTER_KEYS:
        raise HTTPException(status_code=422, detail=f"unknown filter '{filter}'")
    return [row for row in rows if filter in row["filter_tags"]]


@app.get("/api/metrics")
def metrics(repo: RepoDep) -> WorkflowMetrics:
    """Aggregate strategy metrics for the Operations Lead (STRATEGY § Key metrics)."""
    return compute_metrics(repo)


def _build_detail(invoice_id: str, repo: Repository) -> dict | None:
    """Detail payload with Source/Extracted-Metadata sections + human corrections.

    The AI's original outputs stay untouched; human corrections are exposed as an
    overlay (``corrections``) so the hub can show AI value vs. human change
    (ARCHITECTURE.md §11).
    """
    detail = repo.get_detail(invoice_id)
    if detail is None:
        return None
    audit = detail["audit"]
    received = latest_details(audit, AuditAction.RECEIVED)
    extracted = latest_details(audit, AuditAction.EXTRACTED)
    detail["source"] = {
        "channel": received.get("channel"),
        "subject": received.get("subject"),
        "sender": received.get("sender"),
        "attachment": received.get("attachment"),
    }
    detail["extraction"] = {
        "field_confidence": extracted.get("field_confidence", {}),
        "field_evidence": extracted.get("field_evidence", {}),
        "missing_fields": extracted.get("missing_fields", []),
    }
    detail["corrections"] = {
        "metadata": corrections.metadata_overlay(audit),
        "line_items": corrections.match_overlay(audit),
    }
    return detail


def _require_detail(invoice_id: str, repo: Repository) -> dict:
    detail = _build_detail(invoice_id, repo)
    if detail is None:
        raise HTTPException(status_code=404, detail="invoice not found")
    return detail


@app.get("/api/invoices/{invoice_id}")
def get_invoice(invoice_id: str, repo: RepoDep) -> dict:
    return _require_detail(invoice_id, repo)


# --- human QC actions (PRD FR10, §13; ARCHITECTURE.md §11) ------------------
# Every action records a human-attributed audit event so AI decisions and human
# edits stay distinguishable. Corrections are overlays (not in-place mutation):
# the AI output is preserved and the change is recorded with before/after.


class MetadataCorrection(BaseModel):
    updates: dict[str, str]
    reason: str | None = None


class LineItemCorrection(BaseModel):
    line_item_id: str
    catalog_item_id: str | None = None
    catalog_description: str | None = None
    reason: str | None = None


class ReviewNote(BaseModel):
    note: str | None = None


class EscalateBody(BaseModel):
    reason: str | None = None


class NoteBody(BaseModel):
    note: str


def _get_invoice_or_404(invoice_id: str, repo: Repository):
    invoice = repo.get_invoice(invoice_id)
    if invoice is None:
        raise HTTPException(status_code=404, detail="invoice not found")
    return invoice


def _set_status(repo: Repository, invoice, status: InvoiceStatus) -> None:
    invoice.status = status
    invoice.updated_at = datetime.now(timezone.utc)
    repo.save_invoice(invoice)


@app.post("/api/invoices/{invoice_id}/corrections/metadata")
def correct_metadata(invoice_id: str, body: MetadataCorrection, repo: RepoDep) -> dict:
    """Overlay a human correction onto extracted metadata (PRD FR10)."""
    invoice = _get_invoice_or_404(invoice_id, repo)
    unknown = set(body.updates) - set(InvoiceMetadata.model_fields)
    if unknown:
        raise HTTPException(status_code=422, detail=f"unknown field(s): {sorted(unknown)}")

    current = corrections.effective_metadata(invoice.metadata, repo.get_audit(invoice_id))
    before = {field: _as_str(getattr(current, field)) for field in body.updates}
    record(
        repo, invoice_id, AuditAction.CORRECTED, actor=Actor.HUMAN,
        details={"target": "metadata"}, before=before, after=body.updates,
        reason=body.reason,
    )
    _set_status(repo, invoice, InvoiceStatus.CORRECTED)
    return _require_detail(invoice_id, repo)


@app.post("/api/invoices/{invoice_id}/corrections/line-item")
def correct_line_item(invoice_id: str, body: LineItemCorrection, repo: RepoDep) -> dict:
    """Overlay a human correction onto a line-item catalog match (PRD FR10)."""
    invoice = _get_invoice_or_404(invoice_id, repo)
    if all(li.id != body.line_item_id for li in repo.get_line_items(invoice_id)):
        raise HTTPException(status_code=404, detail="line item not found")

    prior = next(
        (m for m in repo.get_matches(invoice_id) if m.line_item_id == body.line_item_id), None
    )
    before = {
        "catalog_item_id": prior.catalog_item_id if prior else None,
        "catalog_description": prior.catalog_description if prior else None,
    }
    after = {
        "catalog_item_id": body.catalog_item_id,
        "catalog_description": body.catalog_description,
    }
    record(
        repo, invoice_id, AuditAction.CORRECTED, actor=Actor.HUMAN,
        details={"target": "line_item", "line_item_id": body.line_item_id},
        before=before, after=after, reason=body.reason,
    )
    _set_status(repo, invoice, InvoiceStatus.CORRECTED)
    return _require_detail(invoice_id, repo)


@app.post("/api/invoices/{invoice_id}/reviewed")
def mark_reviewed(invoice_id: str, body: ReviewNote, repo: RepoDep) -> dict:
    """Acknowledge an outcome without changing it (PRD FR10; ARCHITECTURE.md §11)."""
    _get_invoice_or_404(invoice_id, repo)
    record(repo, invoice_id, AuditAction.REVIEWED, actor=Actor.HUMAN, reason=body.note)
    return _require_detail(invoice_id, repo)


@app.post("/api/invoices/{invoice_id}/escalate")
def escalate(invoice_id: str, body: EscalateBody, repo: RepoDep) -> dict:
    """Route the invoice to manual exception handling (PRD FR10)."""
    invoice = _get_invoice_or_404(invoice_id, repo)
    record(repo, invoice_id, AuditAction.ESCALATED, actor=Actor.HUMAN, reason=body.reason)
    _set_status(repo, invoice, InvoiceStatus.ESCALATED)
    return _require_detail(invoice_id, repo)


@app.post("/api/invoices/{invoice_id}/note")
def add_note(invoice_id: str, body: NoteBody, repo: RepoDep) -> dict:
    """Attach a reviewer note (PRD FR10)."""
    _get_invoice_or_404(invoice_id, repo)
    record(repo, invoice_id, AuditAction.NOTE, actor=Actor.HUMAN, reason=body.note)
    return _require_detail(invoice_id, repo)


def _as_str(value) -> str | None:
    return None if value is None else str(value)
