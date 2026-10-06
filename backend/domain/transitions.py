"""Which human QC actions each workflow status allows (#0003).

The single source of truth for both the API (a disallowed action is a 409) and
the hub (``allowed_actions`` on the detail payload hides buttons that would
fail). Notes, "mark reviewed", and citation confirms don't change state, so
they are allowed on any status and aren't listed here.

- ``posted`` allows nothing: the row is already in the user's Sheet, and the
  Sheet append is deduped by item id, so a correction + rerun could never reach
  it — editing would only make the hub disagree with the ledger.
- ``rejected`` is terminal: a non-receipt must never be filed (AE4).
- In-flight pipeline states allow nothing.
"""

from __future__ import annotations

from backend.domain.enums import InvoiceStatus

_S = InvoiceStatus

ALLOWED_ACTIONS: dict[str, frozenset[InvoiceStatus]] = {
    "correct": frozenset({_S.HELD, _S.FAILED, _S.CORRECTED, _S.ESCALATED}),
    "rerun": frozenset({_S.HELD, _S.CORRECTED, _S.ESCALATED}),
    "retry": frozenset({_S.FAILED}),
    "reject": frozenset({_S.HELD, _S.FAILED, _S.CORRECTED, _S.ESCALATED}),
    "escalate": frozenset({_S.HELD, _S.FAILED, _S.CORRECTED}),
}


def is_allowed(action: str, status: InvoiceStatus) -> bool:
    return status in ALLOWED_ACTIONS[action]


def allowed_actions(status: InvoiceStatus) -> list[str]:
    return sorted(a for a, statuses in ALLOWED_ACTIONS.items() if status in statuses)
