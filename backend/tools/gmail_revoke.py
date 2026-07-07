"""Gmail refresh-token revoke path (feat: solopreneur-ledger pivot, U8).

The explicit "kill switch" the risk table (docs plan §Risks: "Refresh token
leak") calls for: revokes the stored refresh token at Google (so it can no
longer mint access tokens for this mailbox, even if a copy leaked) and clears
this app's own persisted state for it (the encrypted ``oauth_tokens`` row and
``gmail_sync_state`` cursor), so a subsequent connect starts from a clean
slate and requires running ``gmail_oauth_setup.py`` again.

Deliberately a standalone script under ``backend/tools/`` (not an API route,
per U8's scope — the route-owning module is a concurrent unit's file) so an
operator runs it directly::

    python -m backend.tools.gmail_revoke

Reads the refresh token to revoke from ``GMAIL_REFRESH_TOKEN`` (the same env
var the running app reads) rather than decrypting the stored copy, so this
still works even if the encryption key has been lost/rotated — revoking the
known-good env value is the priority; clearing our own stored state is a
best-effort cleanup on top.

The ``httpx`` import is deferred into ``run()`` (not module-level) so this
module can be imported (e.g. by a test asserting it exists) without requiring
network access.
"""

from __future__ import annotations

import sys

from backend.config import settings

REVOKE_URL = "https://oauth2.googleapis.com/revoke"


def run(refresh_token: str | None = None) -> int:
    """POST the refresh token to Google's revoke endpoint, then clear local state.

    Returns a process exit code (0 on success). A missing token is a usage
    error (2); a revoke request that Google rejects is reported but does not
    stop local state from being cleared, since a token that's already invalid
    on Google's side still has no business remaining in our own store.
    """
    import httpx

    token = refresh_token or settings.gmail_refresh_token
    if not token:
        print(
            "No refresh token to revoke (set GMAIL_REFRESH_TOKEN or pass one).",
            file=sys.stderr,
        )
        return 2

    try:
        # Send the token in the form-encoded POST body (per Google's revoke docs),
        # NOT as a URL query param — query strings leak into access/proxy/APM logs,
        # and this is the one token we are trying to invalidate.
        resp = httpx.post(
            REVOKE_URL,
            data={"token": token},
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=30.0,
        )
        resp.raise_for_status()
        print("Revoked the Gmail refresh token with Google.")
    except httpx.HTTPError as exc:
        print(f"Warning: revoke request to Google failed ({exc}); clearing local state anyway.")

    _clear_local_state()
    print(
        "Cleared the stored oauth token and sync cursor. Re-run "
        "backend/tools/gmail_oauth_setup.py to reconnect."
    )
    return 0


def _clear_local_state() -> None:
    """Best-effort clear of this app's own persisted Gmail state.

    Lazily imports the DB-backed repository (KTD1-style: this script should
    still be importable, e.g. from a test, without a live database) so only
    actually *running* the revoke touches Postgres.
    """
    from sqlalchemy import delete

    from backend.db.repository import get_repository
    from backend.db.session import get_engine

    repo = get_repository()
    with get_engine().begin() as conn:
        conn.execute(delete(repo.oauth_tokens).where(repo.oauth_tokens.c.provider == "gmail"))
        conn.execute(delete(repo.gmail_sync_state).where(repo.gmail_sync_state.c.id == "gmail"))


if __name__ == "__main__":
    sys.exit(run())
