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


# --- real LLM provider (OD-2) ------------------------------------------------
# Exercised with a fake SDK client so it runs offline (no anthropic install,
# no network). Verifies the request shape + that fenced JSON is parsed.


class _FakeBlock:
    def __init__(self, text):
        self.type = "text"
        self.text = text


class _FakeMessage:
    def __init__(self, text):
        self.content = [_FakeBlock(text)]


class _FakeMessages:
    def __init__(self, text):
        self._text = text
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return _FakeMessage(self._text)


class _FakeAnthropic:
    def __init__(self, text):
        self.messages = _FakeMessages(text)


def test_anthropic_client_parses_fenced_json():
    from backend.clients import AnthropicLLMClient

    client = AnthropicLLMClient(api_key="sk-test", model="claude-opus-4-7")
    fake = _FakeAnthropic('```json\n{"metadata": {"invoice_number": "INV-1"}}\n```')
    client._client = fake  # inject fake SDK client (skips the lazy import)

    out = client.complete_json(system="extract", user="INVOICE INV-1")
    assert out == {"metadata": {"invoice_number": "INV-1"}}

    sent = fake.messages.calls[0]
    assert sent["model"] == "claude-opus-4-7"
    # the (constant) system prompt is cached; per-invoice text is the user turn
    assert sent["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert "extract" in sent["system"][0]["text"]
    assert sent["messages"] == [{"role": "user", "content": "INVOICE INV-1"}]


def test_get_llm_client_selects_provider_by_key(monkeypatch):
    import types

    import backend.clients as clients
    from backend.clients import AnthropicLLMClient, PassthroughLLMClient

    # settings is a frozen dataclass; swap the whole reference per case.
    monkeypatch.setattr(
        clients, "settings",
        types.SimpleNamespace(anthropic_api_key=None, llm_model="claude-opus-4-7"),
    )
    assert isinstance(clients.get_llm_client(), PassthroughLLMClient)

    monkeypatch.setattr(
        clients, "settings",
        types.SimpleNamespace(anthropic_api_key="sk-test", llm_model="claude-opus-4-7"),
    )
    assert isinstance(clients.get_llm_client(), AnthropicLLMClient)
