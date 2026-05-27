"""Unit tests for the context-resolution stage (P2-A2).

Cover ranked candidates, close-second ambiguity, and invoice-vs-reference
mismatch detection (PRD FR3, FR8, §15).
"""

from backend.clients import StubMCPReferenceClient
from backend.context import resolve
from backend.domain import InvoiceMetadata

REF = StubMCPReferenceClient()


def _resolve(metadata: InvoiceMetadata, source: dict | None = None):
    return resolve("inv_x", metadata, source or {}, REF)


def test_clean_metadata_resolves_with_ranked_candidates():
    ctx = _resolve(InvoiceMetadata(
        sponsor_name="Northwind Therapeutics",
        protocol_number="NWT-101",
        study_name="NW-CARDIO-1",
        site_identifier="Riverside Clinical Research",
    ))
    assert ctx.sponsor_id == "sponsor_001"
    assert ctx.study_id == "study_001"
    assert ctx.site_id == "site_001"
    assert ctx.warnings == []
    # candidates are returned ranked, carrying canonical names for the hub
    assert len(ctx.candidates) >= 1
    assert ctx.candidates[0].sponsor_name == "Northwind Therapeutics"
    scores = [c.score for c in ctx.candidates]
    assert scores == sorted(scores, reverse=True)


def test_sponsor_mismatch_is_flagged():
    """Invoice names Sponsor A but protocol/study/site map to Sponsor B (§15)."""
    ctx = _resolve(InvoiceMetadata(
        sponsor_name="Acme Biosciences",      # contradicts the reference
        protocol_number="NWT-101",            # maps to Northwind
        study_name="NW-CARDIO-1",
        site_identifier="Riverside Clinical Research",
    ))
    assert ctx.sponsor_id == "sponsor_001"  # resolved to Northwind via protocol/site
    assert "sponsor_mismatch" in ctx.warnings
    assert "ambiguous_context" not in ctx.warnings  # the runner-up is far behind


def test_sponsor_only_clue_resolves_without_false_mismatch():
    """A lone, correct sponsor-name clue resolves to that sponsor, no mismatch."""
    ctx = _resolve(InvoiceMetadata(sponsor_name="Acme Biosciences"))
    assert ctx.sponsor_id == "sponsor_002"
    assert "sponsor_mismatch" not in ctx.warnings


def test_no_match_warns():
    ctx = _resolve(InvoiceMetadata(sponsor_name="Unknown Pharma Inc"))
    assert ctx.sponsor_id is None
    assert "no_context_match" in ctx.warnings


def test_sibling_site_ambiguity():
    """Two sites under the same study tie → site-level ambiguity, not mismatch."""
    ctx = _resolve(InvoiceMetadata(
        sponsor_name="Northwind Therapeutics",
        protocol_number="NWT-101",
        study_name="NW-CARDIO-1",
        # no site_identifier → both Riverside sites score equally
    ))
    assert ctx.study_id == "study_001"
    assert "multiple_site_candidates" in ctx.warnings
    assert "ambiguous_context" not in ctx.warnings
