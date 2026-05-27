"""Stage: orchestrator.

Sequences the pipeline stages, owns workflow state transitions, persists each
stage output, appends audit events, and isolates per-invoice failures so one
failure never blocks others (PRD FR11, §14; ARCHITECTURE.md §5-§6).

Stages are pure (they take/return domain objects); this module is the only place
that writes to the repository and advances state. A stage that raises marks the
invoice ``failed`` (retryable) with an exception record and does not propagate.
Low confidence / ambiguity flow to a ``hold`` decision, never a silent submit.
"""

from __future__ import annotations

from datetime import datetime, timezone

from backend.audit import record
from backend.catalog import CatalogCache, InMemoryCatalogCache, fetch
from backend.clients import get_clinrun_client, get_llm_client, get_reference_client
from backend.clients.clinrun import ClinRunClient
from backend.clients.errors import CatalogNotFound, ReferenceUnavailable, SubmissionFailed
from backend.clients.llm import LLMClient
from backend.clients.mcp_reference import MCPReferenceClient
from backend.context import resolve
from backend.db.repository import Repository
from backend.decision import decide
from backend.domain import (
    Actor,
    AuditAction,
    Decision,
    ExceptionRecord,
    Invoice,
    InvoiceStatus,
    Severity,
)
from backend.exceptions import from_decision
from backend.extraction import extract
from backend.intake import ingest
from backend.matching import match
from backend.parser import parse
from backend.submission import submit_invoice


def _advance(repo: Repository, invoice: Invoice, status: InvoiceStatus) -> None:
    invoice.status = status
    invoice.updated_at = datetime.now(timezone.utc)
    repo.save_invoice(invoice)


def process(
    sample: dict,
    repo: Repository,
    *,
    llm: LLMClient | None = None,
    ref: MCPReferenceClient | None = None,
    clinrun: ClinRunClient | None = None,
    catalog_cache: CatalogCache | None = None,
) -> Invoice:
    """Run one invoice end-to-end to a terminal state, persisting as it goes."""
    llm = llm or get_llm_client()
    ref = ref or get_reference_client()
    clinrun = clinrun or get_clinrun_client()

    invoice = ingest(sample)
    repo.save_invoice(invoice)
    record(repo, invoice.id, AuditAction.RECEIVED, actor=Actor.SYSTEM)

    try:
        parsed = parse(invoice.id, sample)
        repo.set_source_text(invoice.id, parsed.text)
        _advance(repo, invoice, InvoiceStatus.PARSED)
        record(repo, invoice.id, AuditAction.PARSED, actor=Actor.SYSTEM)

        extraction = extract(invoice.id, parsed, llm)
        invoice.metadata = extraction.metadata
        repo.replace_line_items(invoice.id, extraction.line_items)
        _advance(repo, invoice, InvoiceStatus.EXTRACTED)
        record(repo, invoice.id, AuditAction.EXTRACTED,
               details={"line_items": len(extraction.line_items)})

        ctx = resolve(invoice.id, extraction.metadata, sample.get("source", {}), ref)
        repo.save_context(ctx)
        _advance(repo, invoice, InvoiceStatus.CONTEXT_RESOLVED)
        record(repo, invoice.id, AuditAction.CONTEXT_RESOLVED, details={
            "sponsor_id": ctx.sponsor_id, "study_id": ctx.study_id,
            "site_id": ctx.site_id, "confidence": ctx.confidence,
            "warnings": ctx.warnings,
        })

        try:
            catalog = fetch(ctx, ref, cache=catalog_cache)
            catalog_available = True
        except CatalogNotFound:
            # scope known but no catalog → deliberate hold (catalog_unavailable)
            catalog, catalog_available = [], False
        except ReferenceUnavailable as exc:
            # transport/API error → retryable failure, not a hold (PRD §15)
            _fail(repo, invoice, "catalog_fetch_failed", str(exc))
            return invoice

        matches = match(extraction.line_items, catalog)
        repo.replace_matches(invoice.id, matches)
        _advance(repo, invoice, InvoiceStatus.CATALOG_MATCHED)
        record(repo, invoice.id, AuditAction.CATALOG_MATCHED, details={
            "catalog_available": catalog_available,
            "matched": sum(1 for m in matches if m.catalog_item_id),
            "total": len(matches),
        })

        decision = decide(
            extraction.metadata, ctx, extraction.line_items, matches, catalog_available
        )
        invoice.decision = decision.decision
        invoice.decision_confidence = decision.confidence

        if decision.decision is Decision.SUBMIT:
            _submit(repo, invoice, extraction.metadata, ctx, matches, clinrun, decision)
        else:
            repo.add_exceptions(from_decision(invoice.id, decision))
            _advance(repo, invoice, InvoiceStatus.HELD)
            record(repo, invoice.id, AuditAction.HELD, details={
                "rationale": decision.rationale,
                "risk_flags": [f.model_dump(mode="json") for f in decision.risk_flags],
            })
    except Exception as exc:  # stage failure: isolate, mark failed, never propagate
        _fail(repo, invoice, "stage_failure", str(exc))

    return invoice


def _submit(repo, invoice, metadata, ctx, matches, clinrun, decision) -> None:
    try:
        result = submit_invoice(invoice, metadata, ctx, matches, clinrun)
    except SubmissionFailed as exc:
        repo.add_exceptions([ExceptionRecord(
            invoice_id=invoice.id, type="submission_failed",
            severity=Severity.HIGH, message=str(exc),
        )])
        _fail(repo, invoice, "submission_failed", str(exc))
        return
    _advance(repo, invoice, InvoiceStatus.SUBMITTED)
    record(repo, invoice.id, AuditAction.SUBMITTED, details={
        "rationale": decision.rationale,
        "reference_id": result.reference_id,
    })


def _fail(repo: Repository, invoice: Invoice, kind: str, message: str) -> None:
    repo.add_exceptions([ExceptionRecord(
        invoice_id=invoice.id, type=kind, severity=Severity.HIGH, message=message,
    )])
    invoice.status = InvoiceStatus.FAILED
    invoice.updated_at = datetime.now(timezone.utc)
    repo.save_invoice(invoice)
    record(repo, invoice.id, AuditAction.FAILED, actor=Actor.SYSTEM,
           details={"kind": kind, "error": message})


def process_all(samples: list[dict], repo: Repository, **clients) -> list[Invoice]:
    """Process many invoices; a failure in one never stops the others (PRD §14).

    A single catalog cache is shared across the batch (unless the caller passes
    one) so invoices for the same sponsor/study fetch the catalog once.
    """
    clients.setdefault("catalog_cache", InMemoryCatalogCache())
    results: list[Invoice] = []
    for sample in samples:
        try:
            results.append(process(sample, repo, **clients))
        except Exception:  # defensive: even an unexpected error can't halt the batch
            continue
    return results
