"""External I/O clients, isolated from pipeline logic (ARCHITECTURE.md §7-§8).

Holds the provider-agnostic LLM client, the MCP reference client, and the
ClinRun submission client, each with an in-process stub (tests) and an HTTP
implementation (the Compose topology). Stages call the ``get_*`` factories;
tests inject stubs directly.
"""

from __future__ import annotations

from backend.config import settings

from .clinrun import (
    ClinRunClient,
    HttpClinRunClient,
    StubClinRunClient,
    SubmissionResult,
)
from .errors import (
    CatalogNotFound,
    ClinRunClientError,
    ReferenceClientError,
    ReferenceUnavailable,
    SubmissionFailed,
)
from .llm import LLMClient, StubLLMClient, parse_json_or_raise
from .mcp_reference import (
    HttpMCPReferenceClient,
    MCPReferenceClient,
    StubMCPReferenceClient,
)

__all__ = [
    "CatalogNotFound",
    "ClinRunClient",
    "ClinRunClientError",
    "HttpClinRunClient",
    "HttpMCPReferenceClient",
    "LLMClient",
    "MCPReferenceClient",
    "ReferenceClientError",
    "ReferenceUnavailable",
    "StubClinRunClient",
    "StubLLMClient",
    "StubMCPReferenceClient",
    "SubmissionFailed",
    "SubmissionResult",
    "get_clinrun_client",
    "get_llm_client",
    "get_reference_client",
    "parse_json_or_raise",
]


def get_reference_client() -> MCPReferenceClient:
    """Default MCP reference client for the running app (HTTP -> mcp-reference)."""
    return HttpMCPReferenceClient(settings.mcp_reference_url)


def get_clinrun_client() -> ClinRunClient:
    """Default ClinRun client for the running app (HTTP -> mock-clinrun)."""
    return HttpClinRunClient(settings.clinrun_url)


def get_llm_client() -> LLMClient:
    """Default LLM client.

    Returns a stub until a real provider is wired (OD-2) by the first stage that
    needs it (extraction, P1-T3). Centralised here so that swap is one edit.
    """
    return StubLLMClient()
