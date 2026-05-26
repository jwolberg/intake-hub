"""ClinRun submission client (ARCHITECTURE.md §2, §4; PRD FR7).

Sends approved invoice payloads to ClinRun. The ``StubClinRunClient`` accepts
everything in-process (tests); ``HttpClinRunClient`` posts to the ``mock-clinrun``
service. A failed submission raises ``SubmissionFailed`` (retryable).
"""

from __future__ import annotations

from typing import Protocol

import httpx
from pydantic import BaseModel

from .errors import SubmissionFailed


class SubmissionResult(BaseModel):
    accepted: bool
    reference_id: str | None = None
    message: str | None = None


class ClinRunClient(Protocol):
    def submit(self, payload: dict) -> SubmissionResult:
        """Submit an approved invoice payload, or raise ``SubmissionFailed``."""
        ...


class StubClinRunClient:
    """In-process stub that accepts submissions and records them."""

    def __init__(self) -> None:
        self.submissions: list[dict] = []

    def submit(self, payload: dict) -> SubmissionResult:
        self.submissions.append(payload)
        ref = payload.get("invoice_id", f"sub_{len(self.submissions)}")
        return SubmissionResult(accepted=True, reference_id=f"clinrun_{ref}")


class HttpClinRunClient:
    """Submission client that posts to the ``mock-clinrun`` service."""

    def __init__(self, base_url: str, client: httpx.Client | None = None) -> None:
        self._client = client or httpx.Client(base_url=base_url, timeout=10.0)

    def submit(self, payload: dict) -> SubmissionResult:
        try:
            resp = self._client.post("/invoices", json=payload)
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            raise SubmissionFailed(str(exc)) from exc
        return SubmissionResult(**resp.json())
