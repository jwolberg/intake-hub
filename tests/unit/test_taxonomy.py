"""Unit tests for the hold-reason taxonomy (U1 ledger pivot).

Asserts the new ledger hold codes resolve to the right severity/title/retryable
via the registry, and that the shared helpers fail safe for unknown codes.
"""

from __future__ import annotations

import pytest
from backend.domain.enums import Severity
from backend.domain.taxonomy import REGISTRY, is_retryable, severity_of, title_of

# The five codes the pivot introduces (Key Decisions): they replace the
# sponsor/catalog codes as the classification / duplicate / adversarial /
# Sheet-write hold reasons.
_LEDGER_CODES = {
    "ambiguous_income_expense": (Severity.HIGH, False),
    "low_category_confidence": (Severity.HIGH, False),
    "suspected_duplicate": (Severity.HIGH, False),
    "suspected_adversarial": (Severity.HIGH, False),
    "sheet_write_failed": (Severity.HIGH, True),
}


@pytest.mark.parametrize("code,expected", _LEDGER_CODES.items())
def test_ledger_hold_codes_registered(code, expected):
    severity, retryable = expected
    reason = REGISTRY.get(code)
    assert reason is not None, f"{code} should be registered"
    assert reason.severity is severity
    assert reason.retryable is retryable
    # The shared lookups agree with the registry entry.
    assert severity_of(code) is severity
    assert is_retryable(code) is retryable
    assert title_of(code)  # non-empty human title


def test_sheet_write_failed_is_the_only_retryable_ledger_code():
    """Only a Sheet-append failure is a retryable hold (mirrors submission_failed);
    the classification/duplicate/adversarial holds are deliberate, not retryable."""
    retryable = {code for code in _LEDGER_CODES if is_retryable(code)}
    assert retryable == {"sheet_write_failed"}


def test_unknown_code_fails_safe():
    """An unregistered code defaults to HIGH severity and a humanised title, and is
    never treated as retryable — fail safe (severity_of docstring)."""
    assert severity_of("totally_unknown") is Severity.HIGH
    assert title_of("totally_unknown") == "Totally unknown"
    assert is_retryable("totally_unknown") is False
