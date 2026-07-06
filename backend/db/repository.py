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
    def get_context(self, invoice_id: str) -> ResolvedContext | None: ...
    def get_matches(self, invoice_id: str) -> list[MatchResult]: ...
    def add_exceptions(self, exceptions: list[ExceptionRecord]) -> None: ...
    def get_exceptions(self, invoice_id: str) -> list[ExceptionRecord]: ...
    def append_audit(self, event: AuditEvent) -> None: ...
    def get_audit(self, invoice_id: str) -> list[AuditEvent]: ...
    def get_detail(self, invoice_id: str) -> dict | None: ...
    def is_seen(self, message_id: str) -> bool: ...
    def mark_seen(self, message_id: str) -> None: ...
    def is_appended(self, idempotency_key: str) -> bool: ...
    def record_append(self, idempotency_key: str, sheet_row_ref: str) -> None: ...
    def get_append_ref(self, idempotency_key: str) -> str | None: ...


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
        self._seen_messages: set[str] = set()
        self._sheet_appends: dict[str, str] = {}

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

    def get_context(self, invoice_id: str) -> ResolvedContext | None:
        stored = self._context.get(invoice_id)
        return stored.model_copy(deep=True) if stored else None

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

    def is_seen(self, message_id: str) -> bool:
        return message_id in self._seen_messages

    def mark_seen(self, message_id: str) -> None:
        self._seen_messages.add(message_id)

    def is_appended(self, idempotency_key: str) -> bool:
        return idempotency_key in self._sheet_appends

    def record_append(self, idempotency_key: str, sheet_row_ref: str) -> None:
        self._sheet_appends[idempotency_key] = sheet_row_ref

    def get_append_ref(self, idempotency_key: str) -> str | None:
        return self._sheet_appends.get(idempotency_key)


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
        self.exceptions = Table("exceptions", md, autoload_with=engine)
        self.audit_events = Table("audit_events", md, autoload_with=engine)
        self.seen_messages = Table("seen_messages", md, autoload_with=engine)
        self.sheet_appends = Table("sheet_appends", md, autoload_with=engine)

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

    def get_context(self, invoice_id: str) -> ResolvedContext | None:
        # Context resolution was part of the clinical-trial pipeline (removed);
        # no `resolved_context` table exists anymore, so there is nothing to read.
        return None

    def get_matches(self, invoice_id: str) -> list[MatchResult]:
        # Catalog matching was part of the clinical-trial pipeline (removed); no
        # `match_results` table exists anymore, so there is nothing to read.
        return []

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

    def is_seen(self, message_id: str) -> bool:
        with self._engine.connect() as conn:
            row = conn.execute(
                select(self.seen_messages.c.message_id)
                .where(self.seen_messages.c.message_id == message_id)
            ).first()
        return row is not None

    def mark_seen(self, message_id: str) -> None:
        stmt = pg_insert(self.seen_messages).values(message_id=message_id).on_conflict_do_nothing(
            index_elements=[self.seen_messages.c.message_id],
        )
        with self._engine.begin() as conn:
            conn.execute(stmt)

    def is_appended(self, idempotency_key: str) -> bool:
        with self._engine.connect() as conn:
            row = conn.execute(
                select(self.sheet_appends.c.idempotency_key)
                .where(self.sheet_appends.c.idempotency_key == idempotency_key)
            ).first()
        return row is not None

    def record_append(self, idempotency_key: str, sheet_row_ref: str) -> None:
        stmt = pg_insert(self.sheet_appends).values(
            idempotency_key=idempotency_key, sheet_row_ref=sheet_row_ref,
        ).on_conflict_do_nothing(
            index_elements=[self.sheet_appends.c.idempotency_key],
        )
        with self._engine.begin() as conn:
            conn.execute(stmt)

    def get_append_ref(self, idempotency_key: str) -> str | None:
        with self._engine.connect() as conn:
            value = conn.execute(
                select(self.sheet_appends.c.sheet_row_ref)
                .where(self.sheet_appends.c.idempotency_key == idempotency_key)
            ).scalar()
        return value


@lru_cache(maxsize=1)
def get_repository() -> Repository:
    """Default repository for the running app (Postgres, schema reflected once)."""
    return PostgresRepository(get_engine())
