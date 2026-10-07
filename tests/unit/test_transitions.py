"""The full QC status x action matrix (#0003), stated explicitly so any change to
the transition table is a deliberate, reviewed test change."""

import pytest
from backend.domain import InvoiceStatus as S
from backend.domain.transitions import ALLOWED_ACTIONS, allowed_actions

# Pipeline states an item only sits in if a run died midway (crash, DB loss
# inside _fail). They must stay recoverable: retry it, or take it over by hand.
STUCK = {
    S.RECEIVED,
    S.PARSED,
    S.EXTRACTED,
    S.CLASSIFIED,
    S.CATEGORIZED,
    S.RERUN_REQUESTED,
    S.CONTEXT_RESOLVED,
    S.CATALOG_MATCHED,
    S.SUBMITTED,
}

EXPECTED = {
    S.HELD: {"correct", "rerun", "reject", "escalate"},
    S.FAILED: {"correct", "retry", "reject", "escalate"},
    S.CORRECTED: {"correct", "rerun", "reject", "escalate"},
    S.ESCALATED: {"correct", "rerun", "reject"},
    S.POSTED: set(),
    S.REJECTED: set(),
    **{status: {"retry", "reject", "escalate"} for status in STUCK},
}


def test_every_status_is_covered():
    assert set(EXPECTED) == set(S)


@pytest.mark.parametrize("status", list(S), ids=lambda s: s.value)
def test_allowed_actions_matrix(status):
    assert set(allowed_actions(status)) == EXPECTED[status]


def test_no_unknown_actions():
    assert set(ALLOWED_ACTIONS) == {"correct", "rerun", "retry", "reject", "escalate"}
