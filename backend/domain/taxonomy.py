"""Canonical hold-reason taxonomy (PRD FR8, §16; ARCHITECTURE.md §12).

Single source of truth mapping each risk-flag / exception ``code`` to its default
severity and a human-readable title, so the decision stage, the exceptions stage,
and the orchestrator all agree on what a reason means and how serious it is.

Covers every PRD FR8 hold reason (``fr8=True``) plus the operational failures and
confidence-band signals the pipeline also raises. Lives in the domain layer so the
stages depend on it downward (never sideways on each other).
"""

from __future__ import annotations

from dataclasses import dataclass

from .enums import Severity


@dataclass(frozen=True)
class HoldReason:
    code: str
    title: str
    severity: Severity
    fr8: bool = True  # listed explicitly in PRD FR8
    retryable: bool = False  # an operational failure a retry can recover (P3-T1)


_REASONS: list[HoldReason] = [
    # Context resolution (FR8: unresolved sponsor/study/site; multiple candidates;
    # mismatched metadata).
    HoldReason("unresolved_sponsor", "Unresolved sponsor", Severity.HIGH),
    HoldReason("unresolved_study", "Unresolved study", Severity.HIGH),
    HoldReason("unresolved_site", "Unresolved site", Severity.HIGH),
    HoldReason("context_unresolved", "Context resolved with low confidence", Severity.HIGH),
    HoldReason("context_ambiguity", "Multiple likely context candidates", Severity.HIGH),
    HoldReason("context_mismatch", "Mismatched metadata vs reference data", Severity.HIGH),
    # Extraction completeness + confidence (FR8: missing invoice number/total;
    # low extraction confidence).
    HoldReason("missing_invoice_number", "Missing invoice number", Severity.HIGH),
    HoldReason("missing_total", "Missing invoice total", Severity.HIGH),
    HoldReason("low_extraction_confidence", "Low extraction confidence", Severity.HIGH),
    HoldReason("moderate_extraction_confidence", "Moderate extraction confidence",
               Severity.MEDIUM, fr8=False),
    HoldReason("missing_optional_fields", "Missing optional fields", Severity.LOW, fr8=False),
    # Matching (FR8: unmatched line items; amount mismatch).
    HoldReason("unmatched_line_item", "Unmatched line item", Severity.HIGH),
    HoldReason("amount_mismatch", "Line amount mismatch", Severity.HIGH),
    HoldReason("quantity_mismatch", "Line quantity mismatch", Severity.HIGH, fr8=False),
    HoldReason("low_match_confidence", "Low line-item match confidence", Severity.HIGH, fr8=False),
    HoldReason("weak_match", "Weak line-item match", Severity.MEDIUM, fr8=False),
    HoldReason("total_mismatch", "Invoice total mismatch", Severity.HIGH, fr8=False),
    # Catalog (FR8: catalog unavailable).
    HoldReason("catalog_unavailable", "Catalog unavailable", Severity.HIGH),
    # Overall + operational failures (FR8: backend submission failure).
    HoldReason("low_confidence", "Low overall decision confidence", Severity.HIGH, fr8=False),
    HoldReason("submission_failed", "Backend submission failure", Severity.HIGH, retryable=True),
    HoldReason("catalog_fetch_failed", "Catalog fetch failed (retryable)", Severity.HIGH,
               fr8=False, retryable=True),
    HoldReason("extraction_failed", "LLM extraction failed (retryable)", Severity.HIGH,
               fr8=False, retryable=True),
    HoldReason("stage_failure", "Processing stage failed (retryable)", Severity.HIGH,
               fr8=False, retryable=True),
]

REGISTRY: dict[str, HoldReason] = {r.code: r for r in _REASONS}


def severity_of(code: str) -> Severity:
    """Default severity for a code (HIGH for anything unregistered — fail safe)."""
    reason = REGISTRY.get(code)
    return reason.severity if reason else Severity.HIGH


def title_of(code: str) -> str:
    """Human-readable title for a code, falling back to a humanised code."""
    reason = REGISTRY.get(code)
    return reason.title if reason else code.replace("_", " ").capitalize()


def is_retryable(code: str) -> bool:
    """Whether a failure ``code`` is an operational error a retry can recover
    (P3-T1) — transient catalog/submission/extraction failures, not deliberate
    holds or terminal data problems."""
    reason = REGISTRY.get(code)
    return bool(reason and reason.retryable)
