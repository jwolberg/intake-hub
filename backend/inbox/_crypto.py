"""Refresh-token encryption at rest (feat: solopreneur-ledger pivot, U8).

The Gmail refresh token is the key to the mailbox (R20) — it must never be
persisted in cleartext. ``encrypt_token``/``decrypt_token`` wrap
``cryptography.fernet.Fernet`` (symmetric, single env-key — appropriate for a
single-tenant deploy) so ``backend/db/repository.py``'s ``oauth_tokens`` table
only ever stores ciphertext.

``cryptography`` is imported lazily (mirroring the ``google-auth``/KTD1
convention used throughout ``backend/clients/``) so constructing a
``GmailInbox`` or importing this module never requires the dependency unless a
token is actually being encrypted or decrypted. See ``backend.inbox.gmail``
for the fallback path taken when the dependency is not installed.
"""

from __future__ import annotations


class TokenCryptoUnavailable(Exception):
    """Raised when ``cryptography`` is not installed.

    Callers (``backend.inbox.gmail``) catch this and fall back to reading the
    refresh token from configuration on every use instead of persisting an
    encrypted copy — never a cleartext one (see ``_build_gmail_inbox``).
    """


def _fernet(key: str):
    try:
        from cryptography.fernet import Fernet
    except ModuleNotFoundError as exc:
        raise TokenCryptoUnavailable(
            "the 'cryptography' package is not installed; cannot encrypt/decrypt "
            "the stored Gmail refresh token"
        ) from exc
    return Fernet(key.encode() if isinstance(key, str) else key)


def encrypt_token(plaintext: str, key: str) -> str:
    """Encrypt ``plaintext`` (the refresh token) with the Fernet ``key``.

    Returns the ciphertext as a ``str`` (Fernet tokens are already
    urlsafe-base64, so this is what ``oauth_tokens.encrypted_token`` stores).
    """
    return _fernet(key).encrypt(plaintext.encode()).decode()


def decrypt_token(ciphertext: str, key: str) -> str:
    """Decrypt a ``str`` previously produced by :func:`encrypt_token`."""
    return _fernet(key).decrypt(ciphertext.encode()).decode()
