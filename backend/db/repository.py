"""Persistence repository (ARCHITECTURE.md §12).

Stages are pure; the orchestrator persists their outputs through a ``Repository``.
The interface is a ``Protocol`` so the orchestrator and API depend on the
contract, not a storage engine: ``InMemoryRepository`` backs tests and offline
dev, and a ``PostgresRepository`` (P1-T10) backs the running app.

Stored values are deep-copied on write and read so callers can't mutate persisted
state by holding a reference — matching how a real datastore behaves.
"""

from __future__ import annotations

from typing import Protocol

from backend.domain import (
    AuditEvent,
    ExceptionRecord,
    Invoice,
    LineItem,
    MatchResult,
    ResolvedContext,
)


class Repository(Protocol):
    def save_invoice(self, invoice: Invoice) -> None: ...
    def get_invoice(self, invoice_id: str) -> Invoice | None: ...
    def list_invoices(self) -> list[Invoice]: ...
    def set_source_text(self, invoice_id: str, text: str) -> None: ...
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
