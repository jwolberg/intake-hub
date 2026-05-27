"""Unit tests for strategy metric instrumentation (P2-B4; STRATEGY § Key metrics)."""

import json
import pathlib

from backend.audit import record
from backend.audit.metrics import compute_metrics
from backend.clients import PassthroughLLMClient, StubClinRunClient, StubMCPReferenceClient
from backend.db.repository import InMemoryRepository
from backend.domain import Actor, AuditAction
from backend.orchestrator import process

SAMPLES = pathlib.Path(__file__).resolve().parents[2] / "samples"


def _stubs():
    return {"llm": PassthroughLLMClient(), "ref": StubMCPReferenceClient(),
            "clinrun": StubClinRunClient()}


def _load(name):
    return json.loads((SAMPLES / name).read_text())


def _seed():
    """One auto-submit + one hold."""
    repo = InMemoryRepository()
    submitted = process(_load("inv_clean_001.json"), repo, **_stubs())
    held = process(_load("inv_hold_unmatched_002.json"), repo, **_stubs())
    return repo, submitted, held


def test_throughput_and_auto_submit_rate():
    repo, _submitted, _held = _seed()
    m = compute_metrics(repo)
    assert (m.total, m.submitted, m.held, m.failed) == (2, 1, 1, 0)
    assert m.auto_submit_rate == 0.5
    # no human has touched anything yet
    assert m.false_submit_rate == 0.0      # 0 of 1 submits known-wrong
    assert m.hold_precision is None        # no hold dispositioned yet → no data


def test_false_submit_and_hold_precision_from_human_outcomes():
    repo, submitted, held = _seed()
    # a human had to correct an auto-submitted invoice → it was a false submit
    record(repo, submitted.id, AuditAction.CORRECTED, actor=Actor.HUMAN,
           before={"sponsor_name": "X"}, after={"sponsor_name": "Y"}, reason="wrong sponsor")
    # a human escalated the held invoice → the hold genuinely needed a human
    record(repo, held.id, AuditAction.ESCALATED, actor=Actor.HUMAN, reason="needs finance")

    m = compute_metrics(repo)
    assert m.false_submit_rate == 1.0
    assert m.hold_precision == 1.0


def test_hold_reviewed_without_correction_lowers_precision():
    repo, _submitted, held = _seed()
    # process a second hold and have a human review it but take no corrective action
    held2 = process(_load("inv_hold_mismatch_005.json"), repo, **_stubs())
    record(repo, held.id, AuditAction.ESCALATED, actor=Actor.HUMAN, reason="needs finance")
    record(repo, held2.id, AuditAction.REVIEWED, actor=Actor.HUMAN, reason="false alarm")

    m = compute_metrics(repo)
    # 2 holds reviewed, 1 confirmed as genuinely needing a human
    assert m.hold_precision == 0.5


def test_empty_repository_has_no_rates():
    m = compute_metrics(InMemoryRepository())
    assert m.total == 0
    assert m.auto_submit_rate is None
    assert m.false_submit_rate is None
    assert m.hold_precision is None
