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
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from backend.audit.metrics import WorkflowMetrics, compute_metrics
from backend.clients import get_clinrun_client, get_llm_client, get_reference_client
from backend.config import settings
from backend.db import get_engine, init_schema
from backend.db.repository import Repository, get_repository
from backend.domain import Decision, InvoiceStatus
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
    total = invoice.metadata.total_amount
    return {
        "id": invoice.id,
        "status": invoice.status.value,
        "decision": invoice.decision.value if invoice.decision else None,
        "decision_confidence": invoice.decision_confidence,
        "invoice_number": invoice.metadata.invoice_number,
        "vendor_name": invoice.metadata.vendor_name,
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


@app.get("/api/invoices/{invoice_id}")
def get_invoice(invoice_id: str, repo: RepoDep) -> dict:
    detail = repo.get_detail(invoice_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="invoice not found")
    return detail
