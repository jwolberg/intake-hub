"""Persistence repository (ARCHITECTURE.md §12).

Stages are pure; the orchestrator persists their outputs through a ``Repository``.
The interface is a ``Protocol`` so the orchestrator and API depend on the
contract, not a storage engine: ``InMemoryRepository`` backs tests and offline
dev, ``PostgresRepository`` backs the running app.

In-memory values are deep-copied on read/write so callers can't mutate persisted
state by holding a reference — matching how a real datastore behaves.
"""

from __future__ import annotations

from collections.abc import Mapping
from functools import lru_cache
from typing import Protocol

from sqlalchemy import Engine, MetaData, Table, delete, insert, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from backend.db.session import get_engine
from backend.domain import (
    Actor,
    AuditAction,
    AuditEvent,
    ContextCandidate,
    Decision,
    ExceptionRecord,
    Invoice,
    InvoiceMetadata,
    InvoiceStatus,
    LineItem,
    MatchResult,
    ResolvedContext,
    Severity,
)


class Repository(Protocol):
    def save_invoice(self, invoice: Invoice) -> None: ...
    def get_invoice(self, invoice_id: str) -> Invoice | None: ...
    def list_invoices(self) -> list[Invoice]: ...
    def set_source_text(self, invoice_id: str, text: str) -> None: ...
    def set_source_pdf(self, invoice_id: str, pdf: bytes) -> None: ...
    def get_source_pdf(self, invoice_id: str) -> bytes | None: ...
    def replace_line_items(self, invoice_id: str, items: list[LineItem]) -> None: ...
    def get_line_items(self, invoice_id: str) -> list[LineItem]: ...
    def save_context(self, ctx: ResolvedContext) -> None: ...
    def get_context(self, invoice_id: str) -> ResolvedContext | None: ...
    def replace_matches(self, invoice_id: str, matches: list[MatchResult]) -> None: ...
    def get_matches(self, invoice_id: str) -> list[MatchResult]: ...
    def add_exceptions(self, exceptions: list[ExceptionRecord]) -> None: ...
    def get_exceptions(self, invoice_id: str) -> list[ExceptionRecord]: ...
    def append_audit(self, event: AuditEvent) -> None: ...
    def get_audit(self, invoice_id: str) -> list[AuditEvent]: ...
    def get_detail(self, invoice_id: str) -> dict | None: ...


class InMemoryRepository:
    """Process-local repository backed by dicts. Default for tests and offline dev."""

    def __init__(self) -> None:
        self._invoices: dict[str, Invoice] = {}
        self._source_text: dict[str, str] = {}
        self._source_pdf: dict[str, bytes] = {}
        self._line_items: dict[str, list[LineItem]] = {}
        self._context: dict[str, ResolvedContext] = {}
        self._matches: dict[str, list[MatchResult]] = {}
        self._exceptions: dict[str, list[ExceptionRecord]] = {}
        self._audit: dict[str, list[AuditEvent]] = {}

    def save_invoice(self, invoice: Invoice) -> None:
        self._invoices[invoice.id] = invoice.model_copy(deep=True)

    def get_invoice(self, invoice_id: str) -> Invoice | None:
        stored = self._invoices.get(invoice_id)
        return stored.model_copy(deep=True) if stored else None

    def list_invoices(self) -> list[Invoice]:
        return [inv.model_copy(deep=True) for inv in self._invoices.values()]

    def set_source_text(self, invoice_id: str, text: str) -> None:
        self._source_text[invoice_id] = text

    def set_source_pdf(self, invoice_id: str, pdf: bytes) -> None:
        self._source_pdf[invoice_id] = pdf

    def get_source_pdf(self, invoice_id: str) -> bytes | None:
        return self._source_pdf.get(invoice_id)

    def replace_line_items(self, invoice_id: str, items: list[LineItem]) -> None:
        self._line_items[invoice_id] = [i.model_copy(deep=True) for i in items]

    def get_line_items(self, invoice_id: str) -> list[LineItem]:
        return [i.model_copy(deep=True) for i in self._line_items.get(invoice_id, [])]

    def save_context(self, ctx: ResolvedContext) -> None:
        self._context[ctx.invoice_id] = ctx.model_copy(deep=True)

    def get_context(self, invoice_id: str) -> ResolvedContext | None:
        stored = self._context.get(invoice_id)
        return stored.model_copy(deep=True) if stored else None

    def replace_matches(self, invoice_id: str, matches: list[MatchResult]) -> None:
        self._matches[invoice_id] = [m.model_copy(deep=True) for m in matches]

    def get_matches(self, invoice_id: str) -> list[MatchResult]:
        return [m.model_copy(deep=True) for m in self._matches.get(invoice_id, [])]

    def add_exceptions(self, exceptions: list[ExceptionRecord]) -> None:
        for exc in exceptions:
            self._exceptions.setdefault(exc.invoice_id, []).append(exc.model_copy(deep=True))

    def get_exceptions(self, invoice_id: str) -> list[ExceptionRecord]:
        return [e.model_copy(deep=True) for e in self._exceptions.get(invoice_id, [])]

    def append_audit(self, event: AuditEvent) -> None:
        self._audit.setdefault(event.invoice_id, []).append(event.model_copy(deep=True))

    def get_audit(self, invoice_id: str) -> list[AuditEvent]:
        return [e.model_copy(deep=True) for e in self._audit.get(invoice_id, [])]

    def get_detail(self, invoice_id: str) -> dict | None:
        invoice = self.get_invoice(invoice_id)
        if invoice is None:
            return None
        return {
            "invoice": invoice,
            "source_text": self._source_text.get(invoice_id),
            "line_items": self.get_line_items(invoice_id),
            "context": self.get_context(invoice_id),
            "matches": self.get_matches(invoice_id),
            "exceptions": self.get_exceptions(invoice_id),
            "audit": self.get_audit(invoice_id),
        }


# --- Postgres implementation ------------------------------------------------
# Reflects the schema created by db.session.init_schema (the canonical DDL), so
# there is no second copy of the table definitions to drift.


def _to_invoice(m: Mapping) -> Invoice:
    return Invoice(
        id=m["id"],
        source=m["source"],
        status=InvoiceStatus(m["status"]),
        decision=Decision(m["decision"]) if m["decision"] else None,
        decision_confidence=m["decision_confidence"],
        metadata=InvoiceMetadata(**(m["metadata"] or {})),
        created_at=m["created_at"],
        updated_at=m["updated_at"],
    )


class PostgresRepository:
    """SQLAlchemy-backed repository for the running app.

    Tables are reflected on construction, so ``db.session.init_schema`` must have
    run first (the app lifespan does this on startup).
    """

    def __init__(self, engine: Engine) -> None:
        self._engine = engine
        md = MetaData()
        self.invoices = Table("invoices", md, autoload_with=engine)
        self.line_items = Table("line_items", md, autoload_with=engine)
        self.resolved_context = Table("resolved_context", md, autoload_with=engine)
        self.match_results = Table("match_results", md, autoload_with=engine)
        self.exceptions = Table("exceptions", md, autoload_with=engine)
        self.audit_events = Table("audit_events", md, autoload_with=engine)

    def save_invoice(self, invoice: Invoice) -> None:
        values = {
            "id": invoice.id,
            "source": invoice.source,
            "status": invoice.status.value,
            "decision": invoice.decision.value if invoice.decision else None,
            "decision_confidence": invoice.decision_confidence,
            "metadata": invoice.metadata.model_dump(mode="json"),
            "created_at": invoice.created_at,
            "updated_at": invoice.updated_at,
        }
        # on_conflict set_ excludes source_text so set_source_text() is preserved.
        update = {k: values[k] for k in
                  ("source", "status", "decision", "decision_confidence", "metadata", "updated_at")}
        stmt = pg_insert(self.invoices).values(**values).on_conflict_do_update(
            index_elements=[self.invoices.c.id], set_=update,
        )
        with self._engine.begin() as conn:
            conn.execute(stmt)

    def get_invoice(self, invoice_id: str) -> Invoice | None:
        with self._engine.connect() as conn:
            row = conn.execute(
                select(self.invoices).where(self.invoices.c.id == invoice_id)
            ).mappings().first()
        return _to_invoice(row) if row else None

    def list_invoices(self) -> list[Invoice]:
        with self._engine.connect() as conn:
            rows = conn.execute(
                select(self.invoices).order_by(self.invoices.c.created_at)
            ).mappings().all()
        return [_to_invoice(r) for r in rows]

    def set_source_text(self, invoice_id: str, text: str) -> None:
        with self._engine.begin() as conn:
            conn.execute(
                self.invoices.update()
                .where(self.invoices.c.id == invoice_id)
                .values(source_text=text)
            )

    def set_source_pdf(self, invoice_id: str, pdf: bytes) -> None:
        with self._engine.begin() as conn:
            conn.execute(
                self.invoices.update()
                .where(self.invoices.c.id == invoice_id)
                .values(source_pdf=pdf)
            )

    def get_source_pdf(self, invoice_id: str) -> bytes | None:
        with self._engine.connect() as conn:
            value = conn.execute(
                select(self.invoices.c.source_pdf).where(self.invoices.c.id == invoice_id)
            ).scalar()
        # psycopg returns BYTEA as memoryview/bytes; normalize to bytes.
        return bytes(value) if value is not None else None

    def replace_line_items(self, invoice_id: str, items: list[LineItem]) -> None:
        rows = [{
            "id": i.id, "invoice_id": invoice_id, "raw_description": i.raw_description,
            "normalized_description": i.normalized_description, "quantity": i.quantity,
            "unit_price": i.unit_price, "total": i.total, "service_period": i.service_period,
            "raw_source_text": i.raw_source_text, "extraction_confidence": i.extraction_confidence,
        } for i in items]
        with self._engine.begin() as conn:
            conn.execute(delete(self.line_items).where(self.line_items.c.invoice_id == invoice_id))
            if rows:
                conn.execute(insert(self.line_items), rows)

    def get_line_items(self, invoice_id: str) -> list[LineItem]:
        with self._engine.connect() as conn:
            rows = conn.execute(
                select(self.line_items).where(self.line_items.c.invoice_id == invoice_id)
            ).mappings().all()
        return [LineItem(**dict(r)) for r in rows]

    def save_context(self, ctx: ResolvedContext) -> None:
        values = {
            "invoice_id": ctx.invoice_id, "sponsor_id": ctx.sponsor_id,
            "study_id": ctx.study_id, "site_id": ctx.site_id, "confidence": ctx.confidence,
            "candidates": [c.model_dump(mode="json") for c in ctx.candidates],
            "warnings": list(ctx.warnings),
        }
        update = {k: v for k, v in values.items() if k != "invoice_id"}
        stmt = pg_insert(self.resolved_context).values(**values).on_conflict_do_update(
            index_elements=[self.resolved_context.c.invoice_id], set_=update,
        )
        with self._engine.begin() as conn:
            conn.execute(stmt)

    def get_context(self, invoice_id: str) -> ResolvedContext | None:
        with self._engine.connect() as conn:
            row = conn.execute(
                select(self.resolved_context).where(
                    self.resolved_context.c.invoice_id == invoice_id)
            ).mappings().first()
        if not row:
            return None
        return ResolvedContext(
            invoice_id=row["invoice_id"], sponsor_id=row["sponsor_id"],
            study_id=row["study_id"], site_id=row["site_id"], confidence=row["confidence"],
            candidates=[ContextCandidate(**c) for c in row["candidates"]],
            warnings=list(row["warnings"]),
        )

    def replace_matches(self, invoice_id: str, matches: list[MatchResult]) -> None:
        rows = [{
            "line_item_id": m.line_item_id, "invoice_id": invoice_id,
            "catalog_item_id": m.catalog_item_id, "catalog_description": m.catalog_description,
            "confidence": m.confidence, "amount_match": m.amount_match,
            "quantity_match": m.quantity_match, "rationale": m.rationale,
            "requires_exception_review": m.requires_exception_review,
            "alternates": list(m.alternates), "exceptions": list(m.exceptions),
        } for m in matches]
        with self._engine.begin() as conn:
            conn.execute(
                delete(self.match_results).where(self.match_results.c.invoice_id == invoice_id)
            )
            if rows:
                conn.execute(insert(self.match_results), rows)

    def get_matches(self, invoice_id: str) -> list[MatchResult]:
        with self._engine.connect() as conn:
            rows = conn.execute(
                select(self.match_results).where(self.match_results.c.invoice_id == invoice_id)
            ).mappings().all()
        return [MatchResult(
            line_item_id=r["line_item_id"], catalog_item_id=r["catalog_item_id"],
            catalog_description=r["catalog_description"], confidence=r["confidence"],
            amount_match=r["amount_match"], quantity_match=r["quantity_match"],
            rationale=r["rationale"], requires_exception_review=r["requires_exception_review"],
            alternates=list(r["alternates"]), exceptions=list(r["exceptions"]),
        ) for r in rows]

    def add_exceptions(self, exceptions: list[ExceptionRecord]) -> None:
        if not exceptions:
            return
        rows = [{
            "id": e.id, "invoice_id": e.invoice_id, "type": e.type,
            "severity": e.severity.value, "message": e.message, "created_at": e.created_at,
        } for e in exceptions]
        with self._engine.begin() as conn:
            conn.execute(insert(self.exceptions), rows)

    def get_exceptions(self, invoice_id: str) -> list[ExceptionRecord]:
        with self._engine.connect() as conn:
            rows = conn.execute(
                select(self.exceptions).where(self.exceptions.c.invoice_id == invoice_id)
            ).mappings().all()
        return [ExceptionRecord(
            id=r["id"], invoice_id=r["invoice_id"], type=r["type"],
            severity=Severity(r["severity"]), message=r["message"], created_at=r["created_at"],
        ) for r in rows]

    def append_audit(self, event: AuditEvent) -> None:
        with self._engine.begin() as conn:
            conn.execute(insert(self.audit_events), {
                "id": event.id, "invoice_id": event.invoice_id, "actor": event.actor.value,
                "action": event.action.value, "details": event.details,
                "timestamp": event.timestamp,
            })

    def get_audit(self, invoice_id: str) -> list[AuditEvent]:
        with self._engine.connect() as conn:
            rows = conn.execute(
                select(self.audit_events)
                .where(self.audit_events.c.invoice_id == invoice_id)
                .order_by(self.audit_events.c.timestamp)
            ).mappings().all()
        return [AuditEvent(
            id=r["id"], invoice_id=r["invoice_id"], actor=Actor(r["actor"]),
            action=AuditAction(r["action"]), details=r["details"], timestamp=r["timestamp"],
        ) for r in rows]

    def get_detail(self, invoice_id: str) -> dict | None:
        invoice = self.get_invoice(invoice_id)
        if invoice is None:
            return None
        with self._engine.connect() as conn:
            source_text = conn.execute(
                select(self.invoices.c.source_text).where(self.invoices.c.id == invoice_id)
            ).scalar()
        return {
            "invoice": invoice,
            "source_text": source_text,
            "line_items": self.get_line_items(invoice_id),
            "context": self.get_context(invoice_id),
            "matches": self.get_matches(invoice_id),
            "exceptions": self.get_exceptions(invoice_id),
            "audit": self.get_audit(invoice_id),
        }


@lru_cache(maxsize=1)
def get_repository() -> Repository:
    """Default repository for the running app (Postgres, schema reflected once)."""
    return PostgresRepository(get_engine())
