"""Unit tests for the layered matcher (P2-A4): candidate generation, LLM
adjudication, and deterministic verification (PRD FR5; ARCHITECTURE.md §9)."""

from backend.clients.llm import StubLLMClient
from backend.domain import CatalogItem, LineItem
from backend.matching import match

CATALOG = [
    CatalogItem(id="cat_001", sponsor_id="s", study_id="st",
                description="Screening Visit", unit_price="300.00"),
    CatalogItem(id="cat_002", sponsor_id="s", study_id="st",
                description="Randomization Visit", unit_price="450.00"),
    CatalogItem(id="cat_003", sponsor_id="s", study_id="st",
                description="ECG", unit_price="120.00"),
]


def _line(desc, qty="1", unit="300.00", total="300.00"):
    return LineItem(invoice_id="inv", raw_description=desc,
                    quantity=qty, unit_price=unit, total=total)


def test_exact_and_containment_match():
    items = [_line("Patient screening visit", qty="2", total="600.00")]
    [m] = match(items, CATALOG)
    assert m.catalog_item_id == "cat_001"
    assert m.amount_match is True
    assert m.quantity_match is True  # 2 * 300 == 600
    assert m.exceptions == []


def test_unmatched_line_item_flagged():
    [m] = match([_line("Investigator travel reimbursement")], CATALOG)
    assert m.catalog_item_id is None
    assert m.requires_exception_review is True
    assert "unmatched_line_item" in m.exceptions


def test_amount_mismatch_downgrades_and_flags():
    # right service, wrong unit price
    [m] = match([_line("Screening Visit", qty="1", unit="999.00", total="999.00")], CATALOG)
    assert m.catalog_item_id == "cat_001"
    assert m.amount_match is False
    assert "amount_mismatch" in m.exceptions
    assert m.confidence < 1.0  # downgraded by verification


def test_quantity_mismatch_flagged():
    # 2 screening visits should total 600 at catalog price, but total says 300
    [m] = match([_line("Screening Visit", qty="2", unit="300.00", total="300.00")], CATALOG)
    assert m.quantity_match is False
    assert "quantity_mismatch" in m.exceptions


def test_ambiguous_candidates_adjudicated_by_llm():
    catalog = [
        CatalogItem(id="cat_a", sponsor_id="s", study_id="st",
                    description="Follow-up Visit Month 3", unit_price="200.00"),
        CatalogItem(id="cat_b", sponsor_id="s", study_id="st",
                    description="Follow-up Visit Month 6", unit_price="200.00"),
    ]
    llm = StubLLMClient(responses=[{"catalog_item_id": "cat_b", "rationale": "month 6 per notes"}])
    [m] = match([_line("Follow-up visit", unit="200.00", total="200.00")], catalog, llm)

    assert m.catalog_item_id == "cat_b"  # LLM broke the tie
    assert "month 6" in m.rationale
    assert "Follow-up Visit Month 3" in m.alternates  # the loser is recorded


def test_adjudication_falls_back_when_llm_unusable():
    catalog = [
        CatalogItem(id="cat_a", sponsor_id="s", study_id="st",
                    description="Follow-up Visit Month 3", unit_price="200.00"),
        CatalogItem(id="cat_b", sponsor_id="s", study_id="st",
                    description="Follow-up Visit Month 6", unit_price="200.00"),
    ]
    llm = StubLLMClient(default={})  # no catalog_item_id → fall back to deterministic top
    [m] = match([_line("Follow-up visit", unit="200.00", total="200.00")], catalog, llm)
    assert m.catalog_item_id in {"cat_a", "cat_b"}  # a deterministic top, not a crash


def test_large_catalog_matches_correct_item():
    big = [
        CatalogItem(id=f"cat_{i}", sponsor_id="s", study_id="st",
                    description=f"Procedure {i}", unit_price="10.00")
        for i in range(2000)
    ]
    big.append(CatalogItem(id="ecg", sponsor_id="s", study_id="st",
                           description="ECG", unit_price="120.00"))
    [m] = match([_line("ECG", unit="120.00", total="120.00")], big)
    assert m.catalog_item_id == "ecg"
