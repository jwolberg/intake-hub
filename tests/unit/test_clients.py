"""Unit tests for the in-process client stubs (P0-T4).

These pin the client contracts stages depend on (ARCHITECTURE.md §7-§8) without
any network: resolution ranking, catalog scoping + not-found, submission, and
the deterministic LLM stub.
"""

import pytest
from backend.clients import (
    CatalogNotFound,
    StubClinRunClient,
    StubLLMClient,
    StubMCPReferenceClient,
)
from backend.clients.llm import parse_json_or_raise


def test_resolve_ranks_best_candidate_first():
    client = StubMCPReferenceClient()
    candidates = client.resolve_sponsor_study_site(
        {"sponsor_name": "Northwind Therapeutics", "protocol_number": "NWT-101"}
    )
    assert candidates, "expected at least one candidate"
    top = candidates[0]
    assert top.sponsor_id == "sponsor_001"
    assert top.study_id == "study_001"
    # scores are sorted descending
    assert all(
        candidates[i].score >= candidates[i + 1].score
        for i in range(len(candidates) - 1)
    )


def test_get_catalog_is_scoped():
    client = StubMCPReferenceClient()
    items = client.get_catalog("sponsor_001", "study_001")
    assert {i.description for i in items} >= {"Screening Visit", "ECG"}
    assert all(i.sponsor_id == "sponsor_001" for i in items)


def test_get_catalog_missing_scope_raises():
    client = StubMCPReferenceClient()
    with pytest.raises(CatalogNotFound):
        client.get_catalog("sponsor_001", "study_999")


def test_clinrun_stub_accepts_and_records():
    client = StubClinRunClient()
    result = client.submit({"invoice_id": "invoice_abc"})
    assert result.accepted is True
    assert result.reference_id == "clinrun_invoice_abc"
    assert client.submissions == [{"invoice_id": "invoice_abc"}]


def test_llm_stub_scripts_and_records_calls():
    client = StubLLMClient(responses=[{"ok": 1}], default={"fallback": True})
    first = client.complete_json(system="s", user="u1")
    second = client.complete_json(system="s", user="u2")
    assert first == {"ok": 1}
    assert second == {"fallback": True}
    assert [c["user"] for c in client.calls] == ["u1", "u2"]


def test_parse_json_or_raise_rejects_non_object():
    assert parse_json_or_raise('{"a": 1}') == {"a": 1}
    with pytest.raises(ValueError):
        parse_json_or_raise("not json")
    with pytest.raises(ValueError):
        parse_json_or_raise("[1, 2, 3]")
