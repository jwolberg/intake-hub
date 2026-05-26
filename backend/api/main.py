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

from fastapi import FastAPI
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from backend.db import get_engine, init_schema

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
