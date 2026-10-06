"""Atomic status + audit writes (#0006).

The audit trail is the source of truth, so a status change and the audit event
(and exceptions) that explain it must commit together. Each test injects a
failure *between* those writes and checks no half-written state survives —
against real Postgres and the in-memory repo.
"""

import json
import pathlib

import pytest
from backend.api.main import app, get_pipeline_clients, get_repo
from backend.clients import PassthroughLLMClient, StubSheetsClient
from backend.db.repository import InMemoryRepository
from backend.domain import AuditAction, InvoiceStatus
from backend.orchestrator import process
from fastapi.testclient import TestClient

SAMPLES = pathlib.Path(__file__).resolve().parents[2] / "samples"
HOLDS = "inv_body_003.json"


def _fail_once_on(repo, monkeypatch, action: AuditAction):
    """Make the first audit append for ``action`` raise (a crash mid-write)."""
    original = repo.append_audit
    state = {"tripped": False}

    def flaky(event):
        if event.action is action and not state["tripped"]:
            state["tripped"] = True
            raise RuntimeError("db connection lost")
        return original(event)

    monkeypatch.setattr(repo, "append_audit", flaky)
    return state


@pytest.fixture(params=["postgres", "memory"])
def repo(request):
    if request.param == "postgres":
        return request.getfixturevalue("pg_repo")
    return InMemoryRepository()


def test_failed_hold_write_leaves_no_orphaned_hold_state(repo, monkeypatch):
    sample = json.loads((SAMPLES / HOLDS).read_text())
    state = _fail_once_on(repo, monkeypatch, AuditAction.HELD)

    invoice = process(sample, repo, llm=PassthroughLLMClient(), sheets=StubSheetsClient())

    assert state["tripped"]
    # The pipeline's isolation marks it failed; the half-written hold (status +
    # exceptions without their HELD event) must have been rolled back.
    stored = repo.get_invoice(invoice.id)
    assert stored.status is InvoiceStatus.FAILED
    types = {e.type for e in repo.get_exceptions(invoice.id)}
    assert types == {"stage_failure"}
    actions = [e.action for e in repo.get_audit(invoice.id)]
    assert AuditAction.HELD not in actions
    assert actions[-1] is AuditAction.FAILED


def test_every_status_change_has_its_audit_event(repo):
    sample = json.loads((SAMPLES / HOLDS).read_text())
    invoice = process(sample, repo, llm=PassthroughLLMClient(), sheets=StubSheetsClient())
    actions = {e.action for e in repo.get_audit(invoice.id)}
    assert AuditAction(repo.get_invoice(invoice.id).status.value) in actions


def test_failed_correction_audit_leaves_status_unchanged(repo, monkeypatch):
    sample = json.loads((SAMPLES / HOLDS).read_text())
    invoice = process(sample, repo, llm=PassthroughLLMClient(), sheets=StubSheetsClient())
    assert repo.get_invoice(invoice.id).status is InvoiceStatus.HELD

    original_save = repo.save_invoice

    def crash(inv):
        raise RuntimeError("db connection lost")

    app.dependency_overrides[get_repo] = lambda: repo
    app.dependency_overrides[get_pipeline_clients] = lambda: {
        "llm": PassthroughLLMClient(),
        "sheets": StubSheetsClient(),
    }
    monkeypatch.setattr(repo, "save_invoice", crash)
    try:
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.post(
            f"/api/invoices/{invoice.id}/corrections/metadata",
            json={"updates": {"vendor_name": "X"}},
        )
    finally:
        app.dependency_overrides.clear()
        monkeypatch.setattr(repo, "save_invoice", original_save)

    assert resp.status_code == 500
    assert repo.get_invoice(invoice.id).status is InvoiceStatus.HELD
    assert AuditAction.CORRECTED not in {e.action for e in repo.get_audit(invoice.id)}


def test_nested_transactions_join_the_outer_one(repo):
    sample = json.loads((SAMPLES / HOLDS).read_text())
    invoice = process(sample, repo, llm=PassthroughLLMClient(), sheets=StubSheetsClient())
    before = repo.get_invoice(invoice.id).status

    with pytest.raises(RuntimeError):
        with repo.transaction():
            stored = repo.get_invoice(invoice.id)
            stored.status = InvoiceStatus.ESCALATED
            with repo.transaction():
                repo.save_invoice(stored)
            raise RuntimeError("outer fails after inner block finished")

    assert repo.get_invoice(invoice.id).status is before
