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

    @classmethod
    def from_env(cls) -> Settings:
        return cls(
            database_url=os.environ.get(
                "DATABASE_URL",
                "postgresql+psycopg://invoicescreener:invoicescreener@db:5432/invoicescreener",
            ),
            mcp_reference_url=os.environ.get(
                "MCP_REFERENCE_URL", "http://mcp-reference:8100"
            ),
            clinrun_url=os.environ.get("CLINRUN_URL", "http://mock-clinrun:8200"),
        )


settings = Settings.from_env()
