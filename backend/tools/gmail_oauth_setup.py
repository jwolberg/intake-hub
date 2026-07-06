"""One-time interactive Gmail OAuth setup (feat: solopreneur-ledger pivot, U7).

Personal Gmail cannot use a service account (no domain-wide delegation), so
``HttpGmailClient`` authenticates as the mailbox owner via installed-app
user-OAuth. This script runs the local-server consent flow *once*, on a
developer/operator machine with a browser, and prints the resulting refresh
token so it can be stored as a long-lived env var (``GMAIL_REFRESH_TOKEN``) for
the headless runtime — which only ever needs to exchange that refresh token
for short-lived access tokens (see ``HttpGmailClient._mint_user_oauth_token``).

Usage::

    python -m backend.tools.gmail_oauth_setup /path/to/client_secret.json
    GMAIL_CLIENT_SECRETS_FILE=/path/to/client_secret.json python -m backend.tools.gmail_oauth_setup

The client-secrets JSON is the "OAuth client ID" (Desktop app type) downloaded
from the Google Cloud Console for this project — not the refresh token itself.

Keep the OAuth consent screen in single-user **"In production"** mode (not
"Testing", which expires refresh tokens after 7 days; not public, which
requires restricted-scope verification/CASA) — see docs/DEPLOY.md and the
plan's Key Technical Decisions.

This is a standalone operator script, not imported by library code — the
``google_auth_oauthlib`` dependency it needs is intentionally *not* a runtime
dependency of the Gmail client (see backend/requirements.txt).
"""

from __future__ import annotations

import os
import sys

from backend.clients.gmail import GMAIL_READONLY_SCOPE


def _client_secrets_path() -> str:
    if len(sys.argv) > 1:
        return sys.argv[1]
    path = os.environ.get("GMAIL_CLIENT_SECRETS_FILE")
    if not path:
        print(
            "Usage: python -m backend.tools.gmail_oauth_setup <client_secret.json>\n"
            "       (or set GMAIL_CLIENT_SECRETS_FILE)",
            file=sys.stderr,
        )
        sys.exit(2)
    return path


def run(client_secrets_file: str) -> int:
    try:
        from google_auth_oauthlib.flow import InstalledAppFlow
    except ImportError:
        print(
            "google_auth_oauthlib is not installed. It is only needed for this "
            "one-time setup script (not a runtime dependency) — install it with:\n"
            "    pip install google-auth-oauthlib",
            file=sys.stderr,
        )
        return 1

    flow = InstalledAppFlow.from_client_secrets_file(
        client_secrets_file, scopes=[GMAIL_READONLY_SCOPE]
    )
    # Opens a browser for the consent screen and runs a local redirect server.
    credentials = flow.run_local_server(port=0)

    if not credentials.refresh_token:
        print(
            "No refresh token was returned. Google only issues a refresh token on "
            "first consent per client/scope pair — revoke prior access at "
            "https://myaccount.google.com/permissions and re-run this script.",
            file=sys.stderr,
        )
        return 1

    print("\nConsent complete. Store this refresh token as an env var, e.g.:\n")
    print(f"    GMAIL_REFRESH_TOKEN={credentials.refresh_token}\n")
    print(
        "Keep the OAuth consent screen in single-user 'In production' mode so this "
        "token does not expire after 7 days (Testing-mode limitation)."
    )
    return 0


if __name__ == "__main__":
    sys.exit(run(_client_secrets_path()))
