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

import base64
import binascii
import pathlib
from datetime import datetime, timezone

from backend import corrections
from backend.audit import latest_details, record
from backend.categorize import categorize
from backend.clients import get_llm_client, get_ocr_client, get_sheets_client
from backend.clients.errors import SheetsClientError
from backend.clients.llm import LLMClient, PassthroughLLMClient
from backend.clients.sheets import SheetsClient, append_filed_row, build_ledger_row
from backend.db.repository import Repository
from backend.decision import decide
from backend.domain import (
    Actor,
    AuditAction,
    AuditEvent,
    CategorizationResult,
    Decision,
    ExtractionResult,
    Invoice,
    InvoiceStatus,
    ParsedDocument,
)
from backend.exceptions import build as build_exception
from backend.exceptions import from_decision
from backend.extraction import extract
from backend.extraction.citations import resolve_citations, synthesize_citations
from backend.intake import ingest
from backend.ledger_integrity import find_duplicate, posted_items
from backend.ocr import OCRClient
from backend.parser import parse
from backend.parser.pdf import LayoutLLMClient
from backend.parser.raster import is_rasterizable, render_pages


def _advance(repo: Repository, invoice: Invoice, status: InvoiceStatus) -> None:
    invoice.status = status
    invoice.updated_at = datetime.now(timezone.utc)
    repo.save_invoice(invoice)


# Transient client calls (e.g. Sheet append) get a few in-process attempts
# before the invoice is failed — predictable recovery from a flaky dependency
# (PRD §14). Non-transient errors are not caught here and surface immediately.
_RETRY_ATTEMPTS = 3


def _retry(
    fn,
    *,
    on: type[Exception] | tuple[type[Exception], ...],
    attempts: int = _RETRY_ATTEMPTS,
):
    last: Exception | None = None
    for _ in range(attempts):
        try:
            return fn()
        except on as exc:
            last = exc
    raise last  # type: ignore[misc]  # attempts >= 1, so last is set


def _extraction_llm(parsed, llm: LLMClient) -> LLMClient:
    """Pick the extraction client for the parsed source.

    A real PDF (``attachment_path`` is a ``.pdf``) is parsed to *layout text*,
    which the offline JSON stand-in (``PassthroughLLMClient``) cannot read;
    substitute the offline PDF stand-in (``LayoutLLMClient``) so the controlled
    sample PDFs extract — and thus get source-anchored citations — through the
    normal pipeline. An injected real provider (OD-2) handles both and is left
    untouched. Keyed off the attachment being a real ``.pdf`` (a local
    ``attachment_path`` in dev, or inline ``attachment_b64`` in the cloud) — not
    ``parsed.format``, which is also "pdf" for a JSON sample whose attachment is
    merely *named* ``.pdf``.
    """
    if _is_real_pdf(parsed.source or {}) and isinstance(llm, PassthroughLLMClient):
        return LayoutLLMClient()
    return llm


def _attach_citations(
    extraction: ExtractionResult, source: dict, ocr: OCRClient
) -> ExtractionResult:
    """Add source-anchored citations when the invoice has a rasterizable source.

    OCRs the original document and string-matches each extracted value to the word
    boxes (the offline analogue of a vision model emitting indices, spec §7), then
    resolves the indices to highlight boxes (P4-T4). Citations are additive: any
    render/OCR failure leaves the extraction untouched rather than failing the
    invoice. Invoices with no rasterizable source (e.g. email-body) get none.
    """
    path = source.get("attachment_path")
    if not path or not is_rasterizable(path):
        return extraction
    try:
        pages = render_pages(path)
        words = ocr.extract_words(path, pages)
    except (FileNotFoundError, ValueError, RuntimeError):
        return extraction
    if not words:
        return extraction
    citations = resolve_citations(synthesize_citations(extraction, words), words)
    return extraction.model_copy(update={"citations": citations})


def _is_real_pdf(source: dict) -> bool:
    """True when the invoice carries an actual PDF — a local ``attachment_path``
    (dev) or inline ``attachment_b64`` (cloud)."""
    path = source.get("attachment_path")
    if path and str(path).lower().endswith(".pdf"):
        return True
    attachment = str(source.get("attachment") or "").lower()
    return bool(source.get("attachment_b64")) and attachment.endswith(".pdf")


def _store_source_pdf(repo: Repository, invoice_id: str, source: dict) -> None:
    """Persist the original PDF bytes (P5-T1) so the hub can serve the actual
    document (PRD §10 download), independent of the local file path.

    Reads inline ``attachment_b64`` (cloud) first, else the local
    ``attachment_path`` (dev). Best-effort: a missing/unreadable/absent source
    simply leaves no stored PDF rather than failing intake.
    """
    if not _is_real_pdf(source):
        return
    b64 = source.get("attachment_b64")
    if b64:
        try:
            repo.set_source_pdf(invoice_id, base64.b64decode(b64))
        except (binascii.Error, ValueError):
            return
        return
    try:
        data = pathlib.Path(source["attachment_path"]).read_bytes()
    except OSError:
        return
    repo.set_source_pdf(invoice_id, data)


def process(
    sample: dict,
    repo: Repository,
    *,
    llm: LLMClient | None = None,
    sheets: SheetsClient | None = None,
    ocr: OCRClient | None = None,
) -> Invoice:
    """Run one item end-to-end to a terminal state, persisting as it goes."""
    llm = llm or get_llm_client()
    sheets = sheets or get_sheets_client()
    ocr = ocr or get_ocr_client()

    invoice = ingest(sample)
    repo.save_invoice(invoice)
    source = sample.get("source", {})
    record(repo, invoice.id, AuditAction.RECEIVED, actor=Actor.SYSTEM, details={
        "channel": source.get("channel"),
        "subject": source.get("subject"),
        "sender": source.get("sender"),
        "attachment": source.get("attachment"),
        # Path to the original file (when supplied), so the page-image endpoints
        # can rasterize the source for the reviewer overlay (P4-T1).
        "attachment_path": source.get("attachment_path"),
    })
    _store_source_pdf(repo, invoice.id, source)

    try:
        parsed = parse(invoice.id, sample)
        repo.set_source_text(invoice.id, parsed.text)
        _advance(repo, invoice, InvoiceStatus.PARSED)
        record(repo, invoice.id, AuditAction.PARSED, actor=Actor.SYSTEM, details={
            "format": parsed.format, "sections": parsed.sections,
        })

        try:
            extraction = extract(invoice.id, parsed, _extraction_llm(parsed, llm))
        except Exception as exc:  # PRD §15 LLM Extraction Failure: mark + don't submit
            _fail(repo, invoice, "extraction_failed", str(exc))
            return invoice
        extraction = _attach_citations(extraction, source, ocr)
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
            # Source-anchored highlight boxes for the reviewer overlay (P4-T4),
            # persisted on the extraction event (OD-9, no schema change).
            "citations": [c.model_dump(mode="json") for c in extraction.citations],
        })

        _categorize_and_file(repo, invoice, extraction, source, llm=llm, sheets=sheets)
    except Exception as exc:  # stage failure: isolate, mark failed, never propagate
        _fail(repo, invoice, "stage_failure", str(exc))

    return invoice


def _categorize_and_file(
    repo: Repository,
    invoice: Invoice,
    extraction: ExtractionResult,
    source: dict,
    *,
    llm: LLMClient,
    sheets: SheetsClient,
    check_duplicates: bool = True,
) -> None:
    """Pipeline tail shared by ``process``, ``rerun``, and ``recover``: classify
    income/expense, assign a Schedule C category, decide, then file to the Sheet
    (via the Postgres dedup ledger) or hold.

    Categorization is re-run each pass (it is cheap and deterministic offline), so
    a rerun after a metadata correction re-derives the type/category from the
    corrected extraction. Category corrections as pinned overlays are a later unit.

    ``check_duplicates`` guards auto-file against posting the same transaction twice
    (R19). It is on for a first pass and *off* for ``rerun``/``recover``, so a
    reviewer who judges a held suspected-duplicate to be distinct can rerun it to
    force the post (the minimal duplicate-resolution path; v1 only holds dups).
    """
    categorization = categorize(invoice.id, extraction, llm, source=source)
    _advance(repo, invoice, InvoiceStatus.CLASSIFIED)
    record(repo, invoice.id, AuditAction.CLASSIFIED, details={
        "document_type": categorization.document_type.value,
        "confidence": categorization.document_type_confidence,
        "evidence": categorization.document_type_evidence,
        "adversarial": categorization.adversarial,
    })
    _advance(repo, invoice, InvoiceStatus.CATEGORIZED)
    record(repo, invoice.id, AuditAction.CATEGORIZED, details={
        "category": categorization.category,
        "confidence": categorization.category_confidence,
        "evidence": categorization.category_evidence,
        "alternates": categorization.alternates,
        "rationale": categorization.rationale,
    })

    decision = decide(extraction, categorization)
    invoice.decision = decision.decision
    invoice.decision_confidence = decision.confidence

    if decision.decision is not Decision.SUBMIT:
        _hold(repo, invoice, decision.rationale, decision.confidence, decision.risk_flags,
              from_decision(invoice.id, decision))
        return

    # An auto-file candidate that duplicates an already-posted item is held rather
    # than double-posted (R19) — checked only on the first pass (see docstring).
    if check_duplicates:
        dup = find_duplicate(invoice, posted_items(repo.list_invoices()))
        if dup is not None:
            _hold_duplicate(repo, invoice, dup, decision)
            return

    _file_to_sheet(repo, invoice, categorization, source, sheets, decision)


def _hold(repo, invoice, rationale, confidence, risk_flags, exceptions) -> None:
    """Record a hold: persist its exceptions, advance to HELD, and audit it."""
    repo.add_exceptions(exceptions)
    _advance(repo, invoice, InvoiceStatus.HELD)
    record(repo, invoice.id, AuditAction.HELD, reason=rationale, details={
        "confidence": confidence,
        "risk_flags": [f.model_dump(mode="json") for f in risk_flags],
    })


def _hold_duplicate(repo: Repository, invoice: Invoice, dup: Invoice, decision) -> None:
    """Hold an item that duplicates an already-posted one (R19), naming the match."""
    reason = (
        f"looks like a duplicate of already-posted item {dup.id} "
        f"({dup.metadata.vendor_name}, {dup.metadata.total_amount})"
    )
    repo.add_exceptions([build_exception(invoice.id, "suspected_duplicate", message=reason)])
    invoice.decision = Decision.HOLD
    _advance(repo, invoice, InvoiceStatus.HELD)
    record(repo, invoice.id, AuditAction.HELD, reason=reason, details={
        "confidence": decision.confidence,
        "duplicate_of": dup.id,
        "risk_flags": [f.model_dump(mode="json") for f in decision.risk_flags],
    })


def _source_ref(source: dict) -> str | None:
    """A human-facing reference for the Sheet's Source column — a message id,
    attachment name, or subject. (A Gmail permalink is added by the Gmail path.)"""
    return source.get("message_id") or source.get("attachment") or source.get("subject")


def _file_to_sheet(
    repo: Repository,
    invoice: Invoice,
    categorization: CategorizationResult,
    source: dict,
    sheets: SheetsClient,
    decision,
) -> None:
    """Append one row to the ledger Sheet, gated by the Postgres dedup ledger.

    The idempotency key is the invoice id — stable across retries, rerun, and
    recover — so a transient append failure retried in-process (or a later
    recover) never posts the same item twice (document-level, one row per item).
    A persistent append failure marks the item ``failed`` with a retryable
    ``sheet_write_failed`` exception (mirrors the old submission-failure path), so
    the item is preserved and recoverable via ``/retry`` rather than silently lost.
    """
    row = build_ledger_row(invoice, categorization, _source_ref(source))
    try:
        outcome = _retry(
            lambda: append_filed_row(repo, sheets, idempotency_key=invoice.id, row=row),
            on=SheetsClientError,
        )
    except SheetsClientError as exc:
        _fail(repo, invoice, "sheet_write_failed", str(exc))
        return
    _advance(repo, invoice, InvoiceStatus.POSTED)
    # Record risk flags on the POSTED event too (symmetric with held) so the detail
    # view's Decision section can show visibility-only medium/low flags.
    record(repo, invoice.id, AuditAction.POSTED, reason=decision.rationale, details={
        "sheet_row_ref": outcome.sheet_row_ref,
        "deduped": outcome.deduped,
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
    sheets: SheetsClient | None = None,
) -> Invoice:
    """Re-enter the pipeline for an existing item using corrected data as fixed
    inputs (R10; PRD FR10).

    Parsing and extraction are *not* re-run: their outputs (source text, metadata,
    line items) are reused, with human metadata corrections applied on top —
    corrected fields become verified inputs (confidence 1.0, no longer missing).
    Categorization and the decision recompute from those inputs, so a correction
    that resolves the original hold reason lets the item file on rerun.
    """
    llm = llm or get_llm_client()
    sheets = sheets or get_sheets_client()

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
        # Rerun bypasses duplicate detection: a reviewer rerunning a held item has
        # judged it distinct, so the dup guard must not re-hold it (R19 resolution).
        _categorize_and_file(repo, invoice, extraction, source,
                             llm=llm, sheets=sheets, check_duplicates=False)
    except Exception as exc:  # same isolation contract as process
        _fail(repo, invoice, "stage_failure", str(exc))

    return invoice


def recover(
    invoice_id: str,
    repo: Repository,
    *,
    llm: LLMClient | None = None,
    sheets: SheetsClient | None = None,
) -> Invoice:
    """Resume a FAILED item from where it failed, reusing successful upstream
    outputs (P3-T1; PRD §15 "provide retry option", §14 retry/recovery).

    If extraction had succeeded (an ``EXTRACTED`` event exists), its persisted
    metadata + line items are reused — so a Sheet-write failure retries *without*
    re-extracting. If extraction itself failed, it is re-run from the persisted
    source text. Then categorize → decide → file/hold runs again, with the
    in-process retry on transient Sheet errors.
    """
    llm = llm or get_llm_client()
    sheets = sheets or get_sheets_client()

    invoice = repo.get_invoice(invoice_id)
    if invoice is None:
        raise KeyError(invoice_id)

    audit = repo.get_audit(invoice_id)
    received = latest_details(audit, AuditAction.RECEIVED)
    source = {k: received.get(k) for k in ("channel", "subject", "sender", "attachment")}

    record(repo, invoice_id, AuditAction.RECOVERED, actor=Actor.SYSTEM,
           reason="retry failed stage", details={"from_status": invoice.status.value})
    _advance(repo, invoice, InvoiceStatus.RERUN_REQUESTED)

    try:
        if latest_details(audit, AuditAction.EXTRACTED):
            extraction = _reconstruct_extraction(repo, invoice, audit)
        else:
            extraction = _reextract(repo, invoice_id, audit, llm)
            invoice.metadata = extraction.metadata
            repo.replace_line_items(invoice_id, extraction.line_items)
        # Recovery re-drives a failed post; the dup guard already ran on the first
        # pass, so skip it (a Sheet-write failure is not a duplicate signal).
        _categorize_and_file(repo, invoice, extraction, source,
                             llm=llm, sheets=sheets, check_duplicates=False)
    except Exception as exc:  # same isolation contract as process
        _fail(repo, invoice, "stage_failure", str(exc))

    return invoice


def _reextract(
    repo: Repository, invoice_id: str, audit: list[AuditEvent], llm: LLMClient
) -> ExtractionResult:
    """Re-run extraction from the persisted source text (recovery from an
    extraction failure). The original document isn't re-fetched — the parsed text
    captured at PARSED time is the input."""
    detail = repo.get_detail(invoice_id) or {}
    source = {k: latest_details(audit, AuditAction.RECEIVED).get(k)
              for k in ("channel", "subject", "sender", "attachment", "attachment_path")}
    parsed = ParsedDocument(
        invoice_id=invoice_id,
        source=source,
        text=detail.get("source_text") or "",
        format=latest_details(audit, AuditAction.PARSED).get("format", "unknown"),
    )
    return extract(invoice_id, parsed, _extraction_llm(parsed, llm))


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
    """Process many items; a failure in one never stops the others (PRD §14)."""
    results: list[Invoice] = []
    for sample in samples:
        try:
            results.append(process(sample, repo, **clients))
        except Exception:  # defensive: even an unexpected error can't halt the batch
            continue
    return results
