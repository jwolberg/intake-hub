"""Runtime configuration, read from the environment.

Kept dependency-free (plain ``os.environ``) so importing config never pulls in
settings frameworks. Defaults target the local Docker Compose topology
(ARCHITECTURE.md §17).
"""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    database_url: str
    mcp_reference_url: str
    clinrun_url: str
    cors_origins: tuple[str, ...]
    # OD-2: real LLM provider. When ``anthropic_api_key`` is set, the pipeline
    # uses the live Anthropic API for extraction (model-derived per-field
    # confidence); otherwise it falls back to the offline stand-in (network-free).
    anthropic_api_key: str | None
    llm_model: str

    @classmethod
    def from_env(cls) -> Settings:
        origins = os.environ.get(
            "CORS_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173"
        )
        return cls(
            database_url=os.environ.get(
                "DATABASE_URL",
                "postgresql+psycopg://intakehub:intakehub@db:5432/intakehub",
            ),
            mcp_reference_url=os.environ.get(
                "MCP_REFERENCE_URL", "http://mcp-reference:8100"
            ),
            clinrun_url=os.environ.get("CLINRUN_URL", "http://mock-clinrun:8200"),
            cors_origins=tuple(o.strip() for o in origins.split(",") if o.strip()),
            anthropic_api_key=os.environ.get("ANTHROPIC_API_KEY") or None,
            llm_model=os.environ.get("LLM_MODEL", "claude-opus-4-7"),
        )


settings = Settings.from_env()
