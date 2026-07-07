"""Ledger integrity helpers: duplicate detection + spot-check sampling (U9).

Auto-filing is only trustworthy if the ledger it produces stays trustworthy
*after* the fact. Two guards live here (pure functions over ``Invoice`` lists, so
they test without a repository):

- **Duplicate detection (R19)** — an order-confirmation email and a separate
  receipt for the *same* charge must not both post. ``find_duplicate`` flags a
  candidate that matches an already-posted item on vendor + amount within a small
  date window; the orchestrator turns a match into a ``suspected_duplicate`` hold
  rather than double-posting. v1 only *holds* suspected duplicates (Scope
  Boundaries) — it never auto-merges.
- **Spot-check sampling (R15)** — ``sample_posted`` returns a bounded random
  subset of already-posted items so a reviewer can periodically confirm the AI's
  autonomous posts were right.
"""

from __future__ import annotations

import random
from datetime import date

from backend.domain import Invoice, InvoiceStatus

# A same-transaction duplicate normally arrives within a few days (the order
# confirmation, then the emailed receipt). Beyond this window, a same-vendor
# same-amount charge is more likely a distinct recurring/repeat purchase.
_DATE_WINDOW_DAYS = 3


def _norm(value: str | None) -> str:
    return (value or "").strip().lower()


def _parse_date(value: str | None) -> date | None:
    """Parse an ISO ``YYYY-MM-DD`` date, or ``None`` if absent/unparseable."""
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _dates_close(a: str | None, b: str | None) -> bool:
    """Whether two invoice dates are within the duplicate window.

    If either date is missing we cannot separate the two on time, so a matching
    vendor + exact amount is treated as close (conservative — surfaces it for
    review rather than silently posting a possible duplicate).
    """
    da, db = _parse_date(a), _parse_date(b)
    if da is None or db is None:
        return True
    return abs((da - db).days) <= _DATE_WINDOW_DAYS


def find_duplicate(candidate: Invoice, posted: list[Invoice]) -> Invoice | None:
    """Return an already-posted item that looks like the same transaction as
    ``candidate`` (same vendor + exact same total within the date window), or
    ``None``.

    Requires both a vendor and a total on the candidate — without them there is
    not enough signal to call a duplicate, so we never hold on a guess.
    """
    vendor = _norm(candidate.metadata.vendor_name)
    total = candidate.metadata.total_amount
    if not vendor or total is None:
        return None
    for other in posted:
        if other.id == candidate.id:
            continue
        if _norm(other.metadata.vendor_name) != vendor:
            continue
        if other.metadata.total_amount != total:
            continue
        if _dates_close(candidate.metadata.invoice_date, other.metadata.invoice_date):
            return other
    return None


def posted_items(invoices: list[Invoice]) -> list[Invoice]:
    """The already-filed items (status ``posted``) among ``invoices``."""
    return [i for i in invoices if i.status is InvoiceStatus.POSTED]


def sample_posted(
    posted: list[Invoice], k: int, *, rng: random.Random | None = None
) -> list[Invoice]:
    """A bounded random subset (up to ``k``) of posted items for spot-checking.

    Returns all of them when there are ``k`` or fewer. ``rng`` is injectable so
    tests are deterministic.
    """
    if k <= 0 or not posted:
        return []
    if k >= len(posted):
        return list(posted)
    picker = rng or random
    return picker.sample(posted, k)
