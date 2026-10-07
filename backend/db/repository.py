"""Persistence repository (ARCHITECTURE.md §12).

Stages are pure; the orchestrator persists their outputs through a ``Repository``.
The interface is a ``Protocol`` so the orchestrator and API depend on the
contract, not a storage engine: ``InMemoryRepository`` backs tests and offline
dev, ``PostgresRepository`` backs the running app.

In-memory values are deep-copied on read/write so callers can't mutate persisted
state by holding a reference — matching how a real datastore behaves.
"""

from __future__ import annotations

import copy
import threading
from collections.abc import Collection, Iterator, Mapping
from contextlib import AbstractContextManager, contextmanager
from contextvars import ContextVar
from functools import lru_cache
from typing import Protocol

from sqlalchemy import Engine, MetaData, Table, delete, func, insert, select, text
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
    def add_exceptions(self, exceptions: list[ExceptionRecord]) -> None: ...
    def get_exceptions(self, invoice_id: str) -> list[ExceptionRecord]: ...
    def clear_exceptions(self, invoice_id: str) -> None: ...
    def append_audit(self, event: AuditEvent) -> None: ...
    def get_audit(self, invoice_id: str) -> list[AuditEvent]: ...
    def get_exceptions_by_invoice(
        self, invoice_ids: Collection[str]
    ) -> dict[str, list[ExceptionRecord]]:
        """Exceptions for many items in one read (#0012); missing ids → no key."""
        ...

    def get_audit_by_invoice(
        self, invoice_ids: Collection[str], actions: Collection[AuditAction] | None = None
    ) -> dict[str, list[AuditEvent]]:
        """Chronological audit events for many items in one read, optionally only
        the given actions (#0012); missing ids → no key."""
        ...

    def get_detail(self, invoice_id: str) -> dict | None: ...
    def is_seen(self, message_id: str) -> bool: ...
    def mark_seen(self, message_id: str) -> None: ...
    def transaction(self) -> AbstractContextManager[None]:
        """Group writes so they commit or roll back together (#0006). Nested
        calls join the outer transaction."""
        ...

    def inbox_fetch_lock(self) -> AbstractContextManager[bool]:
        """Non-blocking exclusive lock around an inbox fetch (#0005); yields
        whether it was acquired. Released when the block exits, even on error."""
        ...

    def is_appended(self, idempotency_key: str) -> bool: ...
    def record_append(self, idempotency_key: str, sheet_row_ref: str) -> None: ...
    def get_append_ref(self, idempotency_key: str) -> str | None: ...
    def get_oauth_token(self, provider: str) -> str | None: ...
    def set_oauth_token(self, provider: str, encrypted: str) -> None: ...
    def get_sync_history_id(self) -> str | None: ...
    def set_sync_history_id(self, history_id: str) -> None: ...


class InMemoryRepository:
    """Process-local repository backed by dicts. Default for tests and offline dev."""

    def __init__(self) -> None:
        self._invoices: dict[str, Invoice] = {}
        self._source_text: dict[str, str] = {}
        self._source_pdf: dict[str, bytes] = {}
        self._line_items: dict[str, list[LineItem]] = {}
        self._exceptions: dict[str, list[ExceptionRecord]] = {}
        self._audit: dict[str, list[AuditEvent]] = {}
        self._seen_messages: set[str] = set()
        self._sheet_appends: dict[str, str] = {}
        self._oauth_tokens: dict[str, str] = {}
        self._gmail_history_id: str | None = None
        self._inbox_fetch_lock = threading.Lock()
        self._tx_depth = 0

    # Data attributes snapshotted by ``transaction`` (everything but the locks).
    _STATE = (
        "_invoices",
        "_source_text",
        "_source_pdf",
        "_line_items",
        "_exceptions",
        "_audit",
        "_seen_messages",
        "_sheet_appends",
        "_oauth_tokens",
        "_gmail_history_id",
    )

    @contextmanager
    def transaction(self) -> Iterator[None]:
        if self._tx_depth:
            self._tx_depth += 1
            try:
                yield
            finally:
                self._tx_depth -= 1
            return
        snapshot = {name: copy.deepcopy(getattr(self, name)) for name in self._STATE}
        self._tx_depth = 1
        try:
            yield
        except BaseException:
            for name, value in snapshot.items():
                setattr(self, name, value)
            raise
        finally:
            self._tx_depth = 0

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

    def add_exceptions(self, exceptions: list[ExceptionRecord]) -> None:
        for exc in exceptions:
            self._exceptions.setdefault(exc.invoice_id, []).append(exc.model_copy(deep=True))

    def get_exceptions(self, invoice_id: str) -> list[ExceptionRecord]:
        return [e.model_copy(deep=True) for e in self._exceptions.get(invoice_id, [])]

    def clear_exceptions(self, invoice_id: str) -> None:
        self._exceptions.pop(invoice_id, None)

    def append_audit(self, event: AuditEvent) -> None:
        self._audit.setdefault(event.invoice_id, []).append(event.model_copy(deep=True))

    def get_audit(self, invoice_id: str) -> list[AuditEvent]:
        return [e.model_copy(deep=True) for e in self._audit.get(invoice_id, [])]

    def get_exceptions_by_invoice(
        self, invoice_ids: Collection[str]
    ) -> dict[str, list[ExceptionRecord]]:
        return {i: self.get_exceptions(i) for i in invoice_ids if self._exceptions.get(i)}

    def get_audit_by_invoice(
        self, invoice_ids: Collection[str], actions: Collection[AuditAction] | None = None
    ) -> dict[str, list[AuditEvent]]:
        out: dict[str, list[AuditEvent]] = {}
        for i in invoice_ids:
            events = [e for e in self.get_audit(i) if actions is None or e.action in actions]
            if events:
                out[i] = events
        return out

    def get_detail(self, invoice_id: str) -> dict | None:
        invoice = self.get_invoice(invoice_id)
        if invoice is None:
            return None
        return {
            "invoice": invoice,
            "source_text": self._source_text.get(invoice_id),
            "line_items": self.get_line_items(invoice_id),
            "exceptions": self.get_exceptions(invoice_id),
            "audit": self.get_audit(invoice_id),
        }

    def is_seen(self, message_id: str) -> bool:
        return message_id in self._seen_messages

    @contextmanager
    def inbox_fetch_lock(self) -> Iterator[bool]:
        acquired = self._inbox_fetch_lock.acquire(blocking=False)
        try:
            yield acquired
        finally:
            if acquired:
                self._inbox_fetch_lock.release()

    def mark_seen(self, message_id: str) -> None:
        self._seen_messages.add(message_id)

    def is_appended(self, idempotency_key: str) -> bool:
        return idempotency_key in self._sheet_appends

    def record_append(self, idempotency_key: str, sheet_row_ref: str) -> None:
        self._sheet_appends[idempotency_key] = sheet_row_ref

    def get_append_ref(self, idempotency_key: str) -> str | None:
        return self._sheet_appends.get(idempotency_key)

    def get_oauth_token(self, provider: str) -> str | None:
        return self._oauth_tokens.get(provider)

    def set_oauth_token(self, provider: str, encrypted: str) -> None:
        self._oauth_tokens[provider] = encrypted

    def get_sync_history_id(self) -> str | None:
        return self._gmail_history_id

    def set_sync_history_id(self, history_id: str) -> None:
        self._gmail_history_id = history_id


# --- Postgres implementation ------------------------------------------------
# Reflects the schema built by the migrations in backend/db/migrations/, so
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


def _to_exception(m: Mapping) -> ExceptionRecord:
    return ExceptionRecord(
        id=m["id"],
        invoice_id=m["invoice_id"],
        type=m["type"],
        severity=Severity(m["severity"]),
        message=m["message"],
        created_at=m["created_at"],
    )


def _to_audit(m: Mapping) -> AuditEvent:
    return AuditEvent(
        id=m["id"],
        invoice_id=m["invoice_id"],
        actor=Actor(m["actor"]),
        action=AuditAction(m["action"]),
        details=m["details"],
        timestamp=m["timestamp"],
    )


# Arbitrary, stable advisory-lock id for "an inbox fetch is running".
_INBOX_FETCH_LOCK_KEY = 0x1A7EF37C


class PostgresRepository:
    """SQLAlchemy-backed repository for the running app.

    Tables are reflected on construction, so the schema migrations
    (``python -m backend.db.migrate``) must have been applied first — the API no
    longer does this on startup (#0013).
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
        self.oauth_tokens = Table("oauth_tokens", md, autoload_with=engine)
        self.gmail_sync_state = Table("gmail_sync_state", md, autoload_with=engine)
        # The connection of the transaction active in this context, if any.
        self._tx: ContextVar = ContextVar(f"pg_tx_{id(self)}", default=None)

    def _summary_columns(self):
        """Every invoices column except the heavy blobs (#0012: list reads must not
        pull each stored PDF and its source text)."""
        heavy = {"source_pdf", "source_text"}
        return [c for c in self.invoices.c if c.name not in heavy]

    @contextmanager
    def transaction(self) -> Iterator[None]:
        if self._tx.get() is not None:
            yield  # nested: join the outer transaction
            return
        with self._engine.begin() as conn:
            token = self._tx.set(conn)
            try:
                yield
            finally:
                self._tx.reset(token)

    @contextmanager
    def _begin(self):
        """A write connection: the active transaction's, else a fresh one."""
        active = self._tx.get()
        if active is not None:
            yield active
            return
        with self._engine.begin() as conn:
            yield conn

    @contextmanager
    def _connect(self):
        """A read connection; inside a transaction it reads its uncommitted writes."""
        active = self._tx.get()
        if active is not None:
            yield active
            return
        with self._engine.connect() as conn:
            yield conn

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
        update = {
            k: values[k]
            for k in (
                "source",
                "status",
                "decision",
                "decision_confidence",
                "metadata",
                "updated_at",
            )
        }
        stmt = (
            pg_insert(self.invoices)
            .values(**values)
            .on_conflict_do_update(
                index_elements=[self.invoices.c.id],
                set_=update,
            )
        )
        with self._begin() as conn:
            conn.execute(stmt)

    def get_invoice(self, invoice_id: str) -> Invoice | None:
        with self._connect() as conn:
            row = (
                conn.execute(
                    select(*self._summary_columns()).where(self.invoices.c.id == invoice_id)
                )
                .mappings()
                .first()
            )
        return _to_invoice(row) if row else None

    def list_invoices(self) -> list[Invoice]:
        with self._connect() as conn:
            rows = (
                conn.execute(select(*self._summary_columns()).order_by(self.invoices.c.created_at))
                .mappings()
                .all()
            )
        return [_to_invoice(r) for r in rows]

    def set_source_text(self, invoice_id: str, text: str) -> None:
        with self._begin() as conn:
            conn.execute(
                self.invoices.update()
                .where(self.invoices.c.id == invoice_id)
                .values(source_text=text)
            )

    def set_source_pdf(self, invoice_id: str, pdf: bytes) -> None:
        with self._begin() as conn:
            conn.execute(
                self.invoices.update()
                .where(self.invoices.c.id == invoice_id)
                .values(source_pdf=pdf)
            )

    def get_source_pdf(self, invoice_id: str) -> bytes | None:
        with self._connect() as conn:
            value = conn.execute(
                select(self.invoices.c.source_pdf).where(self.invoices.c.id == invoice_id)
            ).scalar()
        # psycopg returns BYTEA as memoryview/bytes; normalize to bytes.
        return bytes(value) if value is not None else None

    def replace_line_items(self, invoice_id: str, items: list[LineItem]) -> None:
        rows = [
            {
                "id": i.id,
                "invoice_id": invoice_id,
                "raw_description": i.raw_description,
                "normalized_description": i.normalized_description,
                "quantity": i.quantity,
                "unit_price": i.unit_price,
                "total": i.total,
                "service_period": i.service_period,
                "raw_source_text": i.raw_source_text,
                "extraction_confidence": i.extraction_confidence,
            }
            for i in items
        ]
        with self._begin() as conn:
            conn.execute(delete(self.line_items).where(self.line_items.c.invoice_id == invoice_id))
            if rows:
                conn.execute(insert(self.line_items), rows)

    def get_line_items(self, invoice_id: str) -> list[LineItem]:
        with self._connect() as conn:
            rows = (
                conn.execute(
                    select(self.line_items).where(self.line_items.c.invoice_id == invoice_id)
                )
                .mappings()
                .all()
            )
        return [LineItem(**dict(r)) for r in rows]

    def add_exceptions(self, exceptions: list[ExceptionRecord]) -> None:
        if not exceptions:
            return
        rows = [
            {
                "id": e.id,
                "invoice_id": e.invoice_id,
                "type": e.type,
                "severity": e.severity.value,
                "message": e.message,
                "created_at": e.created_at,
            }
            for e in exceptions
        ]
        with self._begin() as conn:
            conn.execute(insert(self.exceptions), rows)

    def clear_exceptions(self, invoice_id: str) -> None:
        with self._begin() as conn:
            conn.execute(delete(self.exceptions).where(self.exceptions.c.invoice_id == invoice_id))

    def get_exceptions(self, invoice_id: str) -> list[ExceptionRecord]:
        with self._connect() as conn:
            rows = (
                conn.execute(
                    select(self.exceptions).where(self.exceptions.c.invoice_id == invoice_id)
                )
                .mappings()
                .all()
            )
        return [_to_exception(r) for r in rows]

    def get_exceptions_by_invoice(
        self, invoice_ids: Collection[str]
    ) -> dict[str, list[ExceptionRecord]]:
        if not invoice_ids:
            return {}
        with self._connect() as conn:
            rows = (
                conn.execute(
                    select(self.exceptions)
                    .where(self.exceptions.c.invoice_id.in_(list(invoice_ids)))
                    .order_by(self.exceptions.c.created_at)
                )
                .mappings()
                .all()
            )
        out: dict[str, list[ExceptionRecord]] = {}
        for r in rows:
            out.setdefault(r["invoice_id"], []).append(_to_exception(r))
        return out

    def append_audit(self, event: AuditEvent) -> None:
        with self._begin() as conn:
            conn.execute(
                insert(self.audit_events),
                {
                    "id": event.id,
                    "invoice_id": event.invoice_id,
                    "actor": event.actor.value,
                    "action": event.action.value,
                    "details": event.details,
                    "timestamp": event.timestamp,
                },
            )

    def get_audit(self, invoice_id: str) -> list[AuditEvent]:
        with self._connect() as conn:
            rows = (
                conn.execute(
                    select(self.audit_events)
                    .where(self.audit_events.c.invoice_id == invoice_id)
                    .order_by(self.audit_events.c.timestamp)
                )
                .mappings()
                .all()
            )
        return [_to_audit(r) for r in rows]

    def get_audit_by_invoice(
        self, invoice_ids: Collection[str], actions: Collection[AuditAction] | None = None
    ) -> dict[str, list[AuditEvent]]:
        if not invoice_ids:
            return {}
        query = select(self.audit_events).where(
            self.audit_events.c.invoice_id.in_(list(invoice_ids))
        )
        if actions is not None:
            query = query.where(self.audit_events.c.action.in_([a.value for a in actions]))
        with self._connect() as conn:
            rows = conn.execute(query.order_by(self.audit_events.c.timestamp)).mappings().all()
        out: dict[str, list[AuditEvent]] = {}
        for r in rows:
            out.setdefault(r["invoice_id"], []).append(_to_audit(r))
        return out

    def get_detail(self, invoice_id: str) -> dict | None:
        invoice = self.get_invoice(invoice_id)
        if invoice is None:
            return None
        with self._connect() as conn:
            source_text = conn.execute(
                select(self.invoices.c.source_text).where(self.invoices.c.id == invoice_id)
            ).scalar()
        return {
            "invoice": invoice,
            "source_text": source_text,
            "line_items": self.get_line_items(invoice_id),
            "exceptions": self.get_exceptions(invoice_id),
            "audit": self.get_audit(invoice_id),
        }

    def is_seen(self, message_id: str) -> bool:
        with self._connect() as conn:
            row = conn.execute(
                select(self.seen_messages.c.message_id).where(
                    self.seen_messages.c.message_id == message_id
                )
            ).first()
        return row is not None

    @contextmanager
    def inbox_fetch_lock(self) -> Iterator[bool]:
        # A session-level advisory lock on a dedicated connection: serializes
        # fetches across processes and Cloud Run instances. Unlocked explicitly
        # because a pooled connection keeps session locks after it's returned
        # (if the connection dies instead, Postgres drops the lock with it).
        with self._engine.connect() as conn:
            acquired = bool(
                conn.execute(
                    text("SELECT pg_try_advisory_lock(:key)"), {"key": _INBOX_FETCH_LOCK_KEY}
                ).scalar()
            )
            try:
                yield acquired
            finally:
                if acquired:
                    conn.execute(
                        text("SELECT pg_advisory_unlock(:key)"), {"key": _INBOX_FETCH_LOCK_KEY}
                    )

    def mark_seen(self, message_id: str) -> None:
        stmt = (
            pg_insert(self.seen_messages)
            .values(message_id=message_id)
            .on_conflict_do_nothing(
                index_elements=[self.seen_messages.c.message_id],
            )
        )
        with self._begin() as conn:
            conn.execute(stmt)

    def is_appended(self, idempotency_key: str) -> bool:
        with self._connect() as conn:
            row = conn.execute(
                select(self.sheet_appends.c.idempotency_key).where(
                    self.sheet_appends.c.idempotency_key == idempotency_key
                )
            ).first()
        return row is not None

    def record_append(self, idempotency_key: str, sheet_row_ref: str) -> None:
        stmt = (
            pg_insert(self.sheet_appends)
            .values(
                idempotency_key=idempotency_key,
                sheet_row_ref=sheet_row_ref,
            )
            .on_conflict_do_nothing(
                index_elements=[self.sheet_appends.c.idempotency_key],
            )
        )
        with self._begin() as conn:
            conn.execute(stmt)

    def get_append_ref(self, idempotency_key: str) -> str | None:
        with self._connect() as conn:
            value = conn.execute(
                select(self.sheet_appends.c.sheet_row_ref).where(
                    self.sheet_appends.c.idempotency_key == idempotency_key
                )
            ).scalar()
        return value

    def get_oauth_token(self, provider: str) -> str | None:
        with self._connect() as conn:
            value = conn.execute(
                select(self.oauth_tokens.c.encrypted_token).where(
                    self.oauth_tokens.c.provider == provider
                )
            ).scalar()
        return value

    def set_oauth_token(self, provider: str, encrypted: str) -> None:
        stmt = (
            pg_insert(self.oauth_tokens)
            .values(
                provider=provider,
                encrypted_token=encrypted,
            )
            .on_conflict_do_update(
                index_elements=[self.oauth_tokens.c.provider],
                set_={"encrypted_token": encrypted, "updated_at": func.now()},
            )
        )
        with self._begin() as conn:
            conn.execute(stmt)

    def get_sync_history_id(self) -> str | None:
        with self._connect() as conn:
            value = conn.execute(
                select(self.gmail_sync_state.c.history_id).where(
                    self.gmail_sync_state.c.id == "gmail"
                )
            ).scalar()
        return value

    def set_sync_history_id(self, history_id: str) -> None:
        stmt = (
            pg_insert(self.gmail_sync_state)
            .values(
                id="gmail",
                history_id=history_id,
            )
            .on_conflict_do_update(
                index_elements=[self.gmail_sync_state.c.id],
                set_={"history_id": history_id, "updated_at": func.now()},
            )
        )
        with self._begin() as conn:
            conn.execute(stmt)


@lru_cache(maxsize=1)
def get_repository() -> Repository:
    """Default repository for the running app (Postgres, schema reflected once)."""
    return PostgresRepository(get_engine())
