"""Runtime configuration, read from the environment.

Kept dependency-free (plain ``os.environ``) so importing config never pulls in
settings frameworks. Defaults target the local Docker Compose topology
(ARCHITECTURE.md §17).
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class Settings:
    database_url: str
    cors_origins: tuple[str, ...]
    # OD-2: real LLM provider. When ``anthropic_api_key`` is set, the pipeline
    # uses the live Anthropic API for extraction (model-derived per-field
    # confidence); otherwise it falls back to the offline stand-in (network-free).
    anthropic_api_key: str | None
    llm_model: str
    # Drive folder intake (feat: Drive folder intake). ``inbox_provider`` selects
    # the invoice source: ``mock`` (default, offline demo set) or ``drive`` (a
    # watched Google Drive folder). The two drive vars are only required when
    # ``drive`` is selected; ``google_application_credentials`` is either a path to
    # a service-account key file or the inline JSON of that key.
    inbox_provider: str
    drive_folder_id: str | None
    google_application_credentials: str | None
    # Google Sheets ledger output (feat: solopreneur-ledger pivot). When both a
    # service-account credential (``google_application_credentials``) and a
    # spreadsheet id are set, filed items append to that user-owned Sheet;
    # otherwise the offline ``StubSheetsClient`` is used (network-free dev/tests).
    sheets_spreadsheet_id: str | None
    # Gmail inbox provider (feat: solopreneur-ledger pivot, U7/U8). Required only
    # when ``inbox_provider == "gmail"``: a user-OAuth app (client id/secret) plus
    # the one-time refresh token minted by ``backend/tools/gmail_oauth_setup.py``.
    # ``gmail_token_enc_key`` is the Fernet key the refresh token is encrypted with
    # at rest (``backend/inbox/_crypto.py``); without it the token falls back to
    # config-only (never persisted, never cleartext). ``gmail_label`` names the
    # label ``GmailInbox.on_processed`` would apply if the integration ever gains
    # modify scope (today it's read-only, so this is not yet actionable — see
    # ``backend/inbox/gmail.py``). ``gmail_tax_year`` anchors the first-connect
    # backfill query (``after:<year>/01/01``).
    gmail_client_id: str | None
    gmail_client_secret: str | None
    gmail_refresh_token: str | None
    gmail_token_enc_key: str | None
    gmail_label: str | None
    gmail_tax_year: int

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
            cors_origins=tuple(o.strip() for o in origins.split(",") if o.strip()),
            anthropic_api_key=os.environ.get("ANTHROPIC_API_KEY") or None,
            llm_model=os.environ.get("LLM_MODEL", "claude-opus-4-7"),
            inbox_provider=os.environ.get("INBOX_PROVIDER", "mock"),
            drive_folder_id=os.environ.get("DRIVE_FOLDER_ID") or None,
            google_application_credentials=(
                os.environ.get("GOOGLE_APPLICATION_CREDENTIALS") or None
            ),
            sheets_spreadsheet_id=os.environ.get("SHEETS_SPREADSHEET_ID") or None,
            gmail_client_id=os.environ.get("GMAIL_CLIENT_ID") or None,
            gmail_client_secret=os.environ.get("GMAIL_CLIENT_SECRET") or None,
            gmail_refresh_token=os.environ.get("GMAIL_REFRESH_TOKEN") or None,
            gmail_token_enc_key=os.environ.get("GMAIL_TOKEN_ENC_KEY") or None,
            gmail_label=os.environ.get("GMAIL_LABEL") or None,
            gmail_tax_year=int(
                os.environ.get("GMAIL_TAX_YEAR") or date.today().year
            ),
        )


settings = Settings.from_env()
