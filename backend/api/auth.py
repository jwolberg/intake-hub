"""Request authentication: Identity-Aware Proxy assertions (#0001).

Production runs behind an HTTPS load balancer with IAP, which signs every
request it lets through with an ES256 JWT in ``X-Goog-IAP-JWT-Assertion``. The
API verifies that assertion itself (signature, audience, issuer, expiry) so a
request that reaches Cloud Run *around* the load balancer is still refused —
IAP at the edge plus verification here, not either alone.

``AUTH_MODE`` is ``iap`` (the default — fail closed) or ``disabled`` (local
Compose / tests only). ``iap`` requires ``IAP_AUDIENCE``
(``/projects/<number>/global/backendServices/<id>``).
"""

from __future__ import annotations

import time

import httpx

IAP_HEADER = "x-goog-iap-jwt-assertion"
IAP_ISSUER = "https://cloud.google.com/iap"
IAP_CERTS_URL = "https://www.gstatic.com/iap/verify/public_key"
_CERTS_TTL_SECONDS = 3600
_CLOCK_SKEW_SECONDS = 30

AUTH_MODES = {"iap", "disabled"}

_certs_cache: dict = {"certs": None, "fetched_at": 0.0}


class AuthError(Exception):
    """The request is not authenticated (→ 401)."""


class CertsUnavailable(Exception):
    """IAP's public keys could not be fetched (→ 503; not the caller's fault)."""


def validate_config(settings) -> None:
    """Fail fast at startup on a config that would leave the API open or broken."""
    if settings.auth_mode not in AUTH_MODES:
        raise RuntimeError(f"AUTH_MODE must be one of {sorted(AUTH_MODES)}")
    if settings.auth_mode == "iap" and not settings.iap_audience:
        raise RuntimeError("AUTH_MODE=iap requires IAP_AUDIENCE")


def _fetch_certs() -> dict[str, str]:
    """IAP's ``{kid: public PEM}`` map, cached for an hour (keys rotate rarely)."""
    now = time.monotonic()
    cached = _certs_cache["certs"]
    if cached is not None and now - _certs_cache["fetched_at"] < _CERTS_TTL_SECONDS:
        return cached
    try:
        resp = httpx.get(IAP_CERTS_URL, timeout=5.0)
        resp.raise_for_status()
        certs = resp.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise CertsUnavailable(str(exc)) from exc
    _certs_cache.update(certs=certs, fetched_at=now)
    return certs


def verify_iap_assertion(token: str, audience: str, *, certs: dict | None = None) -> str:
    """Verify an IAP JWT and return the signed-in user's email."""
    from google.auth import exceptions, jwt

    try:
        claims = jwt.decode(
            token,
            certs=certs if certs is not None else _fetch_certs(),
            audience=audience,
            clock_skew_in_seconds=_CLOCK_SKEW_SECONDS,
        )
    except (ValueError, exceptions.GoogleAuthError) as exc:
        raise AuthError(f"invalid IAP assertion: {exc}") from exc
    if claims.get("iss") != IAP_ISSUER:
        raise AuthError("invalid IAP assertion: wrong issuer")
    email = claims.get("email")
    if not email:
        raise AuthError("invalid IAP assertion: no email claim")
    return email


def authenticate(headers, settings) -> str | None:
    """Return the caller's email (``None`` when auth is disabled) or raise."""
    if settings.auth_mode == "disabled":
        return None
    if settings.auth_mode != "iap" or not settings.iap_audience:
        raise AuthError("authentication is misconfigured")
    token = headers.get(IAP_HEADER)
    if not token:
        raise AuthError("missing IAP assertion")
    return verify_iap_assertion(token, settings.iap_audience)
