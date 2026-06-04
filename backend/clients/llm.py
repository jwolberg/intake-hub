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
import logging
from typing import Protocol, runtime_checkable

logger = logging.getLogger(__name__)


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


class PassthroughLLMClient:
    """Offline stand-in that echoes JSON embedded in the prompt.

    The MVP parser renders the document as JSON, so this returns the structured
    invoice for the extraction stage without a real provider. A real provider
    (OD-2) replaces this at the ``complete_json`` boundary to extract from genuine
    invoice text; nothing else in the pipeline changes. Returns ``{}`` when the
    prompt is not JSON.
    """

    def complete_json(self, *, system: str, user: str) -> dict:
        try:
            return parse_json_or_raise(user)
        except ValueError:
            return {}


# Exception class names that signal the provider was unreachable — a transport
# failure, not a bad response. Matched by name so this module needs no eager
# anthropic/httpx import (the SDK stays lazy in ``AnthropicLLMClient``).
_CONNECTION_ERROR_NAMES = frozenset({
    "APIConnectionError",  # anthropic SDK; message is the "Connection error." we see
    "APITimeoutError",
    "ConnectError",  # httpx
    "ConnectTimeout",
    "ReadTimeout",
    "PoolTimeout",
})


def _is_connection_error(exc: BaseException) -> bool:
    """True if ``exc`` is a transport/connection failure (vs. a model/usage error).

    Covers Python's built-in socket errors and, by class name, the anthropic SDK /
    httpx connection and timeout errors. Auth, rate-limit, and bad-JSON errors are
    deliberately excluded so genuine misconfiguration still surfaces rather than
    silently degrading.
    """
    if isinstance(exc, (ConnectionError, TimeoutError)):
        return True
    return type(exc).__name__ in _CONNECTION_ERROR_NAMES


class FallbackLLMClient:
    """A primary LLM client with an offline fallback on connection failure.

    When a real provider is configured but unreachable, extracting a single
    invoice should *degrade* — not hard-fail with ``extraction_failed`` (the
    production regression documented in docs/DEPLOY.md, where Cloud Run egress
    cannot reach ``api.anthropic.com``). On a connection error we log a warning
    (so the degradation is observable, not silent) and retry the request against
    ``fallback``. Non-connection errors propagate unchanged.
    """

    def __init__(self, primary: LLMClient, fallback: LLMClient) -> None:
        self._primary = primary
        self._fallback = fallback

    def complete_json(self, *, system: str, user: str) -> dict:
        try:
            return self._primary.complete_json(system=system, user=user)
        except Exception as exc:  # noqa: BLE001 - re-raised below unless it's a connection error
            if not _is_connection_error(exc):
                raise
            logger.warning(
                "LLM provider unreachable (%s: %s); falling back to offline "
                "extraction for this request. See docs/DEPLOY.md.",
                type(exc).__name__,
                exc,
            )
            return self._fallback.complete_json(system=system, user=user)


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


# Appended to the caller's system prompt so the provider returns parseable JSON.
_JSON_ONLY = (
    "\n\nReturn ONLY a single JSON object. No prose, no explanation, "
    "no markdown code fences."
)


def _strip_fences(text: str) -> str:
    """Drop a leading ```json / ``` fence and trailing ``` if the model added one."""
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = stripped.split("\n", 1)[-1] if "\n" in stripped else stripped
        if stripped.endswith("```"):
            stripped = stripped[: -len("```")]
    return stripped.strip()


class AnthropicLLMClient:
    """Real LLM provider via the Anthropic API (OD-2; uses the official SDK).

    Implements the same ``LLMClient.complete_json`` contract as the offline
    stand-ins, so extraction (and match adjudication) get genuine model-derived
    output — including per-field confidence — without any other pipeline change.
    Selected by ``get_llm_client()`` only when an API key is configured; the
    offline ``PassthroughLLMClient`` remains the default so dev/tests stay
    network-free.

    The ``anthropic`` package is imported lazily, so it is required only when this
    client is actually used. The (constant) system prompt is cached
    (``cache_control: ephemeral``) so processing many invoices reuses the cached
    prefix; the per-invoice text goes in the user turn, after the cached prefix.
    """

    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str = "claude-opus-4-7",
        max_tokens: int = 4096,
    ) -> None:
        self._api_key = api_key
        self._model = model
        self._max_tokens = max_tokens
        self._client = None

    def _ensure_client(self):
        if self._client is None:
            import anthropic  # lazy: only the real-provider path needs the SDK

            self._client = (
                anthropic.Anthropic(api_key=self._api_key)
                if self._api_key
                else anthropic.Anthropic()
            )
        return self._client

    def complete_json(self, *, system: str, user: str) -> dict:
        client = self._ensure_client()
        message = client.messages.create(
            model=self._model,
            max_tokens=self._max_tokens,
            system=[{
                "type": "text",
                "text": system + _JSON_ONLY,
                "cache_control": {"type": "ephemeral"},
            }],
            messages=[{"role": "user", "content": user}],
        )
        text = "".join(block.text for block in message.content if block.type == "text")
        return parse_json_or_raise(_strip_fences(text))
