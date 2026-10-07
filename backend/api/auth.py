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

import base64
import json
import time

import httpx

IAP_HEADER = "x-goog-iap-jwt-assertion"
IAP_ISSUER = "https://cloud.google.com/iap"
IAP_CERTS_URL = "https://www.gstatic.com/iap/verify/public_key"
_CERTS_TTL_SECONDS = 3600
# After a failed fetch with no usable keys, fail fast for this long instead of
# hitting gstatic on every request; an unknown key id may force a refetch at
# most this often (key rotation without a request storm).
_FAILURE_BACKOFF_SECONDS = 30
_FORCED_REFRESH_MIN_SECONDS = 60
_CLOCK_SKEW_SECONDS = 30

AUTH_MODES = {"iap", "disabled"}

_certs_cache: dict = {"certs": None, "fetched_at": 0.0, "failed_at": None, "forced_at": None}


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


def _fetch_certs(*, force: bool = False) -> dict[str, str]:
    """IAP's ``{kid: public PEM}`` map, cached for an hour.

    ``force`` refetches early (an unknown key id after rotation), rate-limited.
    If the endpoint is down, previously fetched keys keep working (stale beats
    locking everyone out); with no keys at all, the failure is remembered for a
    short backoff so a gstatic outage doesn't add a network call to every request.
    """
    now = time.monotonic()
    cached = _certs_cache["certs"]
    age = now - _certs_cache["fetched_at"]
    if cached is not None:
        if force:
            forced_at = _certs_cache.get("forced_at")
            if forced_at is not None and now - forced_at < _FORCED_REFRESH_MIN_SECONDS:
                return cached
            _certs_cache["forced_at"] = now
        elif age < _CERTS_TTL_SECONDS:
            return cached
    failed_at = _certs_cache["failed_at"]
    if cached is None and failed_at is not None and now - failed_at < _FAILURE_BACKOFF_SECONDS:
        raise CertsUnavailable("IAP public keys unavailable (recent fetch failed)")
    try:
        resp = httpx.get(IAP_CERTS_URL, timeout=5.0)
        resp.raise_for_status()
        certs = resp.json()
    except (httpx.HTTPError, ValueError) as exc:
        _certs_cache["failed_at"] = now
        if cached is not None:
            return cached
        raise CertsUnavailable(str(exc)) from exc
    _certs_cache.update(certs=certs, fetched_at=now, failed_at=None)
    return certs


def _token_kid(token: str) -> str | None:
    """The unverified ``kid`` from a JWT header (only used to pick a key)."""
    try:
        header = token.split(".", 1)[0]
        return json.loads(base64.urlsafe_b64decode(header + "=" * (-len(header) % 4))).get("kid")
    except (ValueError, AttributeError):
        return None


def verify_iap_assertion(token: str, audience: str, *, certs: dict | None = None) -> str:
    """Verify an IAP JWT and return the signed-in user's email."""
    from google.auth import exceptions, jwt

    if certs is None:
        certs = _fetch_certs()
        kid = _token_kid(token)
        if kid and kid not in certs:
            certs = _fetch_certs(force=True)  # IAP may have rotated its keys
    try:
        claims = jwt.decode(
            token,
            certs=certs,
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
