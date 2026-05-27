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

from backend import corrections
from backend.audit import latest_details, record
from backend.catalog import CatalogCache, InMemoryCatalogCache, fetch
from backend.clients import get_clinrun_client, get_llm_client, get_reference_client
from backend.clients.clinrun import ClinRunClient
from backend.clients.errors import CatalogNotFound, ReferenceUnavailable, SubmissionFailed
from backend.clients.llm import LLMClient
from backend.clients.mcp_reference import MCPReferenceClient
from backend.context import resolve
from backend.corrections import apply_match_overlay
from backend.db.repository import Repository
from backend.decision import decide
from backend.domain import (
    Actor,
    AuditAction,
    AuditEvent,
    Decision,
    ExtractionResult,
    Invoice,
    InvoiceStatus,
)
from backend.exceptions import build as build_exception
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
    source = sample.get("source", {})
    record(repo, invoice.id, AuditAction.RECEIVED, actor=Actor.SYSTEM, details={
        "channel": source.get("channel"),
        "subject": source.get("subject"),
        "sender": source.get("sender"),
        "attachment": source.get("attachment"),
    })

    try:
        parsed = parse(invoice.id, sample)
        repo.set_source_text(invoice.id, parsed.text)
        _advance(repo, invoice, InvoiceStatus.PARSED)
        record(repo, invoice.id, AuditAction.PARSED, actor=Actor.SYSTEM, details={
            "format": parsed.format, "sections": parsed.sections,
        })

        extraction = extract(invoice.id, parsed, llm)
        invoice.metadata = extraction.metadata
        repo.replace_line_items(invoice.id, extraction.line_items)
        _advance(repo, invoice, InvoiceStatus.EXTRACTED)
        record(repo, invoice.id, AuditAction.EXTRACTED, details={
            "line_items": len(extraction.line_items),
            "invoice_number": extraction.metadata.invoice_number,
            "fields_extracted": len(extraction.field_confidence) - len(extraction.missing_fields),
            "missing_fields": extraction.missing_fields,
            # Per-field signals persisted here (no schema change) so the detail
            # view can show value/confidence/evidence (PRD §10) and rerun can
            # reconstruct the extraction (P2-C2/C4).
            "field_confidence": extraction.field_confidence,
            "field_evidence": extraction.field_evidence,
        })

        _resolve_match_decide(
            repo, invoice, extraction, source,
            llm=llm, ref=ref, clinrun=clinrun, catalog_cache=catalog_cache,
        )
    except Exception as exc:  # stage failure: isolate, mark failed, never propagate
        _fail(repo, invoice, "stage_failure", str(exc))

    return invoice


def _resolve_match_decide(
    repo: Repository,
    invoice: Invoice,
    extraction: ExtractionResult,
    source: dict,
    *,
    llm: LLMClient,
    ref: MCPReferenceClient,
    clinrun: ClinRunClient,
    catalog_cache: CatalogCache | None,
    match_corrections: list[AuditEvent] | None = None,
) -> None:
    """Pipeline tail shared by ``process`` and ``rerun``: resolve context, fetch
    catalog, match line items, decide, then submit or hold.

    ``match_corrections`` (rerun only) pins human-corrected line matches as fixed
    inputs; on a first pass there are none and matching runs unmodified.
    """
    ctx = resolve(invoice.id, extraction.metadata, source, ref)
    repo.save_context(ctx)
    _advance(repo, invoice, InvoiceStatus.CONTEXT_RESOLVED)
    record(repo, invoice.id, AuditAction.CONTEXT_RESOLVED, details={
        "sponsor_id": ctx.sponsor_id, "study_id": ctx.study_id,
        "site_id": ctx.site_id, "confidence": ctx.confidence,
        "warnings": ctx.warnings, "candidates": len(ctx.candidates),
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
        return

    matches = match(extraction.line_items, catalog, llm)
    if match_corrections:
        matches = apply_match_overlay(matches, match_corrections)
    repo.replace_matches(invoice.id, matches)
    _advance(repo, invoice, InvoiceStatus.CATALOG_MATCHED)
    record(repo, invoice.id, AuditAction.CATALOG_MATCHED, details={
        "catalog_available": catalog_available,
        "catalog_size": len(catalog),
        "matched": sum(1 for m in matches if m.catalog_item_id),
        "total": len(matches),
    })

    decision = decide(extraction, ctx, matches, catalog_available)
    invoice.decision = decision.decision
    invoice.decision_confidence = decision.confidence

    if decision.decision is Decision.SUBMIT:
        _submit(repo, invoice, extraction.metadata, ctx, matches, clinrun, decision)
    else:
        repo.add_exceptions(from_decision(invoice.id, decision))
        _advance(repo, invoice, InvoiceStatus.HELD)
        record(repo, invoice.id, AuditAction.HELD, reason=decision.rationale, details={
            "confidence": decision.confidence,
            "risk_flags": [f.model_dump(mode="json") for f in decision.risk_flags],
        })


def _submit(repo, invoice, metadata, ctx, matches, clinrun, decision) -> None:
    try:
        result = submit_invoice(invoice, metadata, ctx, matches, clinrun)
    except SubmissionFailed as exc:
        _fail(repo, invoice, "submission_failed", str(exc))
        return
    _advance(repo, invoice, InvoiceStatus.SUBMITTED)
    # Record risk flags on submit too (symmetric with held) so the detail view's
    # Decision section can show visibility-only medium/low flags (PRD §10).
    record(repo, invoice.id, AuditAction.SUBMITTED, reason=decision.rationale, details={
        "reference_id": result.reference_id,
        "risk_flags": [f.model_dump(mode="json") for f in decision.risk_flags],
    })


def _fail(repo: Repository, invoice: Invoice, kind: str, message: str) -> None:
    repo.add_exceptions([build_exception(invoice.id, kind, message=message)])
    invoice.status = InvoiceStatus.FAILED
    invoice.updated_at = datetime.now(timezone.utc)
    repo.save_invoice(invoice)
    record(repo, invoice.id, AuditAction.FAILED, actor=Actor.SYSTEM,
           reason=message, details={"kind": kind})


def rerun(
    invoice_id: str,
    repo: Repository,
    *,
    llm: LLMClient | None = None,
    ref: MCPReferenceClient | None = None,
    clinrun: ClinRunClient | None = None,
    catalog_cache: CatalogCache | None = None,
) -> Invoice:
    """Re-enter the pipeline for an existing invoice using corrected data as fixed
    inputs (PRD FR10; ARCHITECTURE.md §6, §11).

    Parsing and extraction are *not* re-run: their outputs (source text, metadata,
    line items) are reused, with human corrections applied on top — corrected
    metadata fields become verified inputs (confidence 1.0, no longer missing) and
    corrected line matches are pinned. Context, catalog, matching, and the decision
    recompute from those inputs, so a correction that resolves the original hold
    reason lets the invoice submit on rerun.
    """
    llm = llm or get_llm_client()
    ref = ref or get_reference_client()
    clinrun = clinrun or get_clinrun_client()

    invoice = repo.get_invoice(invoice_id)
    if invoice is None:
        raise KeyError(invoice_id)

    audit = repo.get_audit(invoice_id)
    extraction = _reconstruct_extraction(repo, invoice, audit)
    received = latest_details(audit, AuditAction.RECEIVED)
    source = {k: received.get(k) for k in ("channel", "subject", "sender", "attachment")}

    record(repo, invoice_id, AuditAction.RERUN, actor=Actor.HUMAN,
           reason="rerun with corrected data",
           details={"corrected_fields": sorted(corrections.metadata_overlay(audit))})
    _advance(repo, invoice, InvoiceStatus.RERUN_REQUESTED)

    try:
        _resolve_match_decide(
            repo, invoice, extraction, source,
            llm=llm, ref=ref, clinrun=clinrun, catalog_cache=catalog_cache,
            match_corrections=audit,
        )
    except Exception as exc:  # same isolation contract as process
        _fail(repo, invoice, "stage_failure", str(exc))

    return invoice


def _reconstruct_extraction(
    repo: Repository, invoice: Invoice, audit: list[AuditEvent]
) -> ExtractionResult:
    """Rebuild the extraction from persisted outputs + human metadata corrections.

    Corrected fields are treated as reviewer-verified: confidence 1.0 and no longer
    counted missing, so the decision stage no longer holds on them.
    """
    line_items = repo.get_line_items(invoice.id)
    extracted = latest_details(audit, AuditAction.EXTRACTED)
    field_confidence = dict(extracted.get("field_confidence", {}))
    field_evidence = dict(extracted.get("field_evidence", {}))
    missing_fields = list(extracted.get("missing_fields", []))

    metadata = corrections.effective_metadata(invoice.metadata, audit)
    for field, value in corrections.metadata_overlay(audit).items():
        if value in (None, ""):
            continue
        field_confidence[field] = 1.0
        field_evidence[field] = "corrected by reviewer"
        if field in missing_fields:
            missing_fields.remove(field)

    return ExtractionResult(
        metadata=metadata,
        line_items=line_items,
        field_confidence=field_confidence,
        field_evidence=field_evidence,
        missing_fields=missing_fields,
    )


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
