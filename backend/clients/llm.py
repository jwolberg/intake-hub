"""Provider-agnostic LLM client (ARCHITECTURE.md §8).

Stages depend on the ``LLMClient`` protocol, never on a concrete provider, so the
model (OpenAI or equivalent — OD-2) is a configuration detail. ``complete_json``
is the only method stages need: extraction, match adjudication, and decision
rationale all request schema-shaped JSON.

The ``StubLLMClient`` returns canned responses and records calls, so pipeline
logic can be tested deterministically without live model calls.
"""

from __future__ import annotations

import json
from typing import Protocol, runtime_checkable


@runtime_checkable
class LLMClient(Protocol):
    def complete_json(self, *, system: str, user: str) -> dict:
        """Return the model's response parsed as a JSON object.

        Implementations must raise ``ValueError`` if the model does not return
        valid JSON, so callers never pass malformed output downstream.
        """
        ...


class StubLLMClient:
    """Deterministic stub for tests and offline dev.

    Pass ``responses`` (a list) to script successive ``complete_json`` returns;
    once exhausted it falls back to ``default``. Every call is recorded in
    ``calls`` for assertions.
    """

    def __init__(
        self,
        responses: list[dict] | None = None,
        default: dict | None = None,
    ) -> None:
        self._responses = list(responses or [])
        self._default = default if default is not None else {}
        self.calls: list[dict[str, str]] = []

    def complete_json(self, *, system: str, user: str) -> dict:
        self.calls.append({"system": system, "user": user})
        if self._responses:
            return self._responses.pop(0)
        return dict(self._default)


def parse_json_or_raise(raw: str) -> dict:
    """Parse a JSON object from raw model text or raise ``ValueError``.

    Shared by real provider implementations (added when a stage needs them) so
    schema-validation failures surface consistently (ARCHITECTURE.md §8).
    """
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"LLM did not return valid JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError("LLM returned valid JSON but not an object")
    return value
