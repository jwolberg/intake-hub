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

from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from backend.audit.metrics import WorkflowMetrics, compute_metrics
from backend.clients import get_clinrun_client, get_llm_client, get_reference_client
from backend.config import settings
from backend.db import get_engine, init_schema
from backend.db.repository import Repository, get_repository
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


def _summary(invoice, repo: Repository) -> dict:
    """List-row view of an invoice (PRD §10 Invoice List View)."""
    ctx = repo.get_context(invoice.id)
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
        "exception_count": len(repo.get_exceptions(invoice.id)),
        "updated_at": invoice.updated_at,
    }


# --- invoice routes (PRD §13) -----------------------------------------------

@app.post("/api/invoices/process")
def process_invoice(sample: dict, repo: RepoDep, clients: ClientsDep) -> dict:
    """Run a sample invoice through the AI pipeline and return its outcome."""
    invoice = process(sample, repo, **clients)
    return _summary(invoice, repo)


@app.get("/api/invoices")
def list_invoices(repo: RepoDep) -> list[dict]:
    return [_summary(inv, repo) for inv in repo.list_invoices()]


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
