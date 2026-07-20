"""Tests: validate_bot_framework_token (added 2026-07-17). Real RS256
signing/verification against a locally generated RSA key -- the JWKS HTTP
fetch (PyJWKClient.fetch_data, which uses urllib, not httpx) is
monkeypatched to return that key's JWK rather than hitting Azure AD, but
the actual cryptographic verification (signature, aud, iss, exp) is real,
not mocked."""
from __future__ import annotations

import time

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from app.channels.teams.auth import TokenValidationError, validate_bot_framework_token
import app.channels.teams.auth as auth_module

TENANT_ID = "test-tenant-id"
APP_ID = "test-app-id"
KID = "test-key-id"


@pytest.fixture(autouse=True)
def _reset_jwk_client_cache():
    auth_module._jwk_client = None
    auth_module._jwk_client_tenant = None
    yield
    auth_module._jwk_client = None
    auth_module._jwk_client_tenant = None


@pytest.fixture
def rsa_keypair():
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return private_key, private_key.public_key()


@pytest.fixture
def configured_settings(monkeypatch):
    monkeypatch.setenv("MICROSOFT_APP_ID", APP_ID)
    monkeypatch.setenv("MICROSOFT_APP_TENANT_ID", TENANT_ID)
    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", "https://placeholder/")
    monkeypatch.setenv("AZURE_OPENAI_KEY", "placeholder")
    import app.config as config_module

    monkeypatch.setattr(config_module, "_settings", config_module.Settings())


def _mock_jwks(monkeypatch, public_key):
    jwk_dict = jwt.algorithms.RSAAlgorithm.to_jwk(public_key, as_dict=True)
    jwk_dict["kid"] = KID
    jwk_dict["use"] = "sig"
    jwk_dict["alg"] = "RS256"
    monkeypatch.setattr(
        "jwt.PyJWKClient.fetch_data", lambda self: {"keys": [jwk_dict]}
    )


def _make_token(private_key, *, aud=APP_ID, iss=None, exp_delta=3600, kid=KID):
    iss = iss or f"https://login.microsoftonline.com/{TENANT_ID}/v2.0"
    now = int(time.time())
    payload = {"aud": aud, "iss": iss, "iat": now, "nbf": now, "exp": now + exp_delta, "sub": "user-object-id"}
    return jwt.encode(payload, private_key, algorithm="RS256", headers={"kid": kid})


def test_valid_token_is_accepted_and_claims_returned(configured_settings, monkeypatch, rsa_keypair):
    private_key, public_key = rsa_keypair
    _mock_jwks(monkeypatch, public_key)
    token = _make_token(private_key)

    claims = validate_bot_framework_token(f"Bearer {token}")

    assert claims["aud"] == APP_ID
    assert claims["sub"] == "user-object-id"


def test_missing_header_is_rejected(configured_settings):
    with pytest.raises(TokenValidationError):
        validate_bot_framework_token(None)


def test_non_bearer_header_is_rejected(configured_settings):
    with pytest.raises(TokenValidationError):
        validate_bot_framework_token("Basic abc123")


def test_wrong_audience_is_rejected(configured_settings, monkeypatch, rsa_keypair):
    private_key, public_key = rsa_keypair
    _mock_jwks(monkeypatch, public_key)
    token = _make_token(private_key, aud="some-other-app-id")

    with pytest.raises(TokenValidationError):
        validate_bot_framework_token(f"Bearer {token}")


def test_wrong_issuer_is_rejected(configured_settings, monkeypatch, rsa_keypair):
    private_key, public_key = rsa_keypair
    _mock_jwks(monkeypatch, public_key)
    token = _make_token(private_key, iss="https://login.microsoftonline.com/some-other-tenant/v2.0")

    with pytest.raises(TokenValidationError):
        validate_bot_framework_token(f"Bearer {token}")


def test_expired_token_is_rejected(configured_settings, monkeypatch, rsa_keypair):
    private_key, public_key = rsa_keypair
    _mock_jwks(monkeypatch, public_key)
    token = _make_token(private_key, exp_delta=-3600)

    with pytest.raises(TokenValidationError):
        validate_bot_framework_token(f"Bearer {token}")


def test_signature_from_wrong_key_is_rejected(configured_settings, monkeypatch, rsa_keypair):
    _, public_key = rsa_keypair
    _mock_jwks(monkeypatch, public_key)
    other_private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    token = _make_token(other_private_key)

    with pytest.raises(TokenValidationError):
        validate_bot_framework_token(f"Bearer {token}")


def test_missing_bot_credentials_configured_is_rejected(monkeypatch):
    monkeypatch.setenv("MICROSOFT_APP_ID", "")
    monkeypatch.setenv("MICROSOFT_APP_TENANT_ID", "")
    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", "https://placeholder/")
    monkeypatch.setenv("AZURE_OPENAI_KEY", "placeholder")
    import app.config as config_module

    monkeypatch.setattr(config_module, "_settings", config_module.Settings())

    with pytest.raises(TokenValidationError):
        validate_bot_framework_token("Bearer whatever")
