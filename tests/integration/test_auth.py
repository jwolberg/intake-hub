"""IAP authentication (#0001).

Production sits behind an HTTPS load balancer with Identity-Aware Proxy; the
API additionally verifies IAP's signed ``X-Goog-IAP-JWT-Assertion`` header on
every ``/api/*`` route (defense in depth if ingress is ever misconfigured).

The verifier tests sign real ES256 JWTs with a throwaway key, so the actual
signature/audience/issuer/expiry checks run — no mocked-out crypto.
"""

import dataclasses
import time

import pytest
from backend.api import auth
from backend.api import main as api
from backend.clients import PassthroughLLMClient, StubSheetsClient
from backend.db.repository import InMemoryRepository
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi.testclient import TestClient
from google.auth import crypt, jwt

AUDIENCE = "/projects/123/global/backendServices/456"
KID = "test-kid"


@pytest.fixture(scope="module")
def keypair():
    key = ec.generate_private_key(ec.SECP256R1())
    private_pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    public_pem = (
        key.public_key()
        .public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
        .decode()
    )
    signer = crypt.ES256Signer.from_string(private_pem, key_id=KID)
    return signer, {KID: public_pem}


def _token(signer, **overrides) -> str:
    now = int(time.time())
    claims = {
        "iss": auth.IAP_ISSUER,
        "aud": AUDIENCE,
        "sub": "accounts.google.com:1",
        "email": "jay@example.com",
        "iat": now,
        "exp": now + 600,
    }
    claims.update(overrides)
    return jwt.encode(signer, claims).decode()


# --- verifier ---------------------------------------------------------------


def test_valid_assertion_returns_email(keypair):
    signer, certs = keypair
    assert auth.verify_iap_assertion(_token(signer), AUDIENCE, certs=certs) == "jay@example.com"


@pytest.mark.parametrize(
    "overrides",
    [
        {"aud": "/projects/123/global/backendServices/999"},
        {"iss": "https://accounts.google.com"},
        {"exp": int(time.time()) - 3600, "iat": int(time.time()) - 7200},
        {"email": None},
    ],
    ids=["wrong-audience", "wrong-issuer", "expired", "no-email"],
)
def test_bad_claims_are_rejected(keypair, overrides):
    signer, certs = keypair
    with pytest.raises(auth.AuthError):
        auth.verify_iap_assertion(_token(signer, **overrides), AUDIENCE, certs=certs)


def test_token_signed_by_another_key_is_rejected(keypair):
    _, certs = keypair
    other = ec.generate_private_key(ec.SECP256R1()).private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    forged = _token(crypt.ES256Signer.from_string(other, key_id=KID))
    with pytest.raises(auth.AuthError):
        auth.verify_iap_assertion(forged, AUDIENCE, certs=certs)


def test_garbage_token_is_rejected(keypair):
    _, certs = keypair
    with pytest.raises(auth.AuthError):
        auth.verify_iap_assertion("not-a-jwt", AUDIENCE, certs=certs)


# --- config -----------------------------------------------------------------


def test_iap_mode_without_audience_fails_fast():
    cfg = dataclasses.replace(api.settings, auth_mode="iap", iap_audience=None)
    with pytest.raises(RuntimeError):
        auth.validate_config(cfg)


def test_unknown_auth_mode_fails_fast():
    cfg = dataclasses.replace(api.settings, auth_mode="none", iap_audience=None)
    with pytest.raises(RuntimeError):
        auth.validate_config(cfg)


# --- middleware -------------------------------------------------------------


@pytest.fixture
def iap_client(monkeypatch, keypair):
    _, certs = keypair
    monkeypatch.setattr(
        api, "settings", dataclasses.replace(api.settings, auth_mode="iap", iap_audience=AUDIENCE)
    )
    monkeypatch.setattr(auth, "_fetch_certs", lambda: certs)
    repo = InMemoryRepository()
    api.app.dependency_overrides[api.get_repo] = lambda: repo
    api.app.dependency_overrides[api.get_pipeline_clients] = lambda: {
        "llm": PassthroughLLMClient(),
        "sheets": StubSheetsClient(),
    }
    yield TestClient(api.app)
    api.app.dependency_overrides.clear()


def test_read_route_requires_assertion(iap_client):
    resp = iap_client.get("/api/invoices")
    assert resp.status_code == 401


def test_mutating_route_requires_assertion(iap_client):
    resp = iap_client.post("/api/invoices/process", json={"source": {"channel": "email"}})
    assert resp.status_code == 401


def test_invalid_assertion_is_401(iap_client, keypair):
    signer, _ = keypair
    resp = iap_client.get("/api/invoices", headers={auth.IAP_HEADER: _token(signer, aud="wrong")})
    assert resp.status_code == 401


def test_valid_assertion_is_allowed(iap_client, keypair):
    signer, _ = keypair
    resp = iap_client.get("/api/invoices", headers={auth.IAP_HEADER: _token(signer)})
    assert resp.status_code == 200
    assert resp.json() == []


def test_health_stays_public(iap_client):
    assert iap_client.get("/health").status_code == 200


def test_cert_fetch_failure_is_503(iap_client, keypair, monkeypatch):
    signer, _ = keypair

    def boom():
        raise auth.CertsUnavailable("gstatic down")

    monkeypatch.setattr(auth, "_fetch_certs", boom)
    resp = iap_client.get("/api/invoices", headers={auth.IAP_HEADER: _token(signer)})
    assert resp.status_code == 503


def test_cors_preflight_is_answered_before_auth(iap_client):
    # CORS is the outermost middleware, so a cross-origin preflight (which never
    # carries credentials) gets CORS headers instead of a bare 401.
    resp = iap_client.options(
        "/api/invoices",
        headers={
            "Origin": "http://localhost:5173",
            "Access-Control-Request-Method": "GET",
        },
    )
    assert resp.status_code == 200
    assert resp.headers["access-control-allow-origin"] == "http://localhost:5173"
