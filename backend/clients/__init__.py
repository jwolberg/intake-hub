"""External I/O clients, isolated from pipeline logic (ARCHITECTURE.md §7-§8).

Holds the provider-agnostic LLM client, the MCP reference client, and the
ClinRun submission client, each with an in-process stub (tests) and an HTTP
implementation (the Compose topology). Stages call the ``get_*`` factories;
tests inject stubs directly.
"""

from __future__ import annotations

from backend.config import settings

# OCR clients live in backend.ocr (their own stage); re-exported here so stages
# resolve every external seam through the same clients factory layer.
from backend.ocr import OCRClient, StubOCRClient, TesseractOCRClient

from .clinrun import (
    ClinRunClient,
    HttpClinRunClient,
    StubClinRunClient,
    SubmissionResult,
)
from .drive import (
    DriveClient,
    DriveFile,
    HttpDriveClient,
    StubDriveClient,
)
from .errors import (
    CatalogNotFound,
    ClinRunClientError,
    DriveClientError,
    ReferenceClientError,
    ReferenceUnavailable,
    SheetsClientError,
    SubmissionFailed,
)
from .llm import (
    AnthropicLLMClient,
    FallbackLLMClient,
    LLMClient,
    PassthroughLLMClient,
    StubLLMClient,
    parse_json_or_raise,
)
from .mcp_reference import (
    HttpMCPReferenceClient,
    MCPReferenceClient,
    StubMCPReferenceClient,
)
from .sheets import (
    LEDGER_HEADERS,
    HttpSheetsClient,
    SheetsClient,
    StubSheetsClient,
    build_ledger_row,
)
from .vision import (
    OfflineVisionLLMClient,
    StubVisionLLMClient,
    VisionLLMClient,
    locate_value,
)

__all__ = [
    "AnthropicLLMClient",
    "CatalogNotFound",
    "ClinRunClient",
    "ClinRunClientError",
    "DriveClient",
    "DriveClientError",
    "DriveFile",
    "FallbackLLMClient",
    "HttpClinRunClient",
    "HttpDriveClient",
    "HttpMCPReferenceClient",
    "HttpSheetsClient",
    "LEDGER_HEADERS",
    "LLMClient",
    "MCPReferenceClient",
    "OCRClient",
    "OfflineVisionLLMClient",
    "PassthroughLLMClient",
    "ReferenceClientError",
    "ReferenceUnavailable",
    "SheetsClient",
    "SheetsClientError",
    "StubClinRunClient",
    "StubDriveClient",
    "StubLLMClient",
    "StubMCPReferenceClient",
    "StubOCRClient",
    "StubSheetsClient",
    "StubVisionLLMClient",
    "SubmissionFailed",
    "SubmissionResult",
    "TesseractOCRClient",
    "VisionLLMClient",
    "build_ledger_row",
    "get_clinrun_client",
    "get_llm_client",
    "get_ocr_client",
    "get_reference_client",
    "get_vision_llm_client",
    "locate_value",
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

    When ``ANTHROPIC_API_KEY`` is configured, returns the live Anthropic provider
    (OD-2) so extraction confidence is genuinely model-derived. Otherwise returns
    the offline passthrough stand-in (echoes the structured document the parser
    renders) so dev/tests run without a provider key. ``StubLLMClient`` remains
    the tool for scripted unit tests.

    The live provider is wrapped in ``FallbackLLMClient`` so that if it is
    unreachable (e.g. Cloud Run egress can't reach ``api.anthropic.com`` — see
    docs/DEPLOY.md), extraction degrades to the offline path instead of
    hard-failing the invoice. A bound key can therefore never break production.
    """
    if settings.anthropic_api_key:
        primary = AnthropicLLMClient(
            api_key=settings.anthropic_api_key, model=settings.llm_model
        )
        return FallbackLLMClient(primary, PassthroughLLMClient())
    return PassthroughLLMClient()


def get_ocr_client() -> OCRClient:
    """Default OCR client: the offline text-layer stand-in (``StubOCRClient``).

    Returns deterministic word boxes for the controlled sample PDFs with no
    network and no Tesseract binary, preserving the offline-first path (spec §7).
    Swapping in ``TesseractOCRClient`` (OD-6) is a one-line change here.
    """
    return StubOCRClient()


def get_vision_llm_client() -> VisionLLMClient:
    """Default vision LLM client: the offline stand-in (``OfflineVisionLLMClient``).

    Synthesizes word-index citations by string-matching extracted values against
    the OCR words — no model, no network (spec §7). Wiring a real vision provider
    (OD-7) is a one-line change here.
    """
    return OfflineVisionLLMClient()
