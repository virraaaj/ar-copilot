"""
Validates incoming Bot Framework requests to POST /api/messages (added
2026-07-17, the HTTP edge bot.py's own docstring flagged as not existing
yet). Without this, anyone who discovers the endpoint URL could post
arbitrary activities and have them run through the real AgentLoop
(including write tools) as any claimed user -- so this has to be real
signature verification, not a shape check.

The bot resource is registered "Single Tenant" (see .env's
MICROSOFT_APP_TENANT_ID), so incoming Activity requests carry an Azure AD
v2 access token: RS256-signed, issued by
https://login.microsoftonline.com/{tenant_id}/v2.0, audience ==
MICROSOFT_APP_ID. Verification: fetch that tenant's JWKS, find the key
matching the token's `kid` header, verify the signature + aud + iss with
PyJWT (leaves clock-skew/expiry checking to PyJWT's own defaults).

JWKS keys are cached in-process (Azure AD rotates them rarely and always
publishes the new key before switching) with a fixed refresh interval --
not on every request, but not forever either, so a genuine key rotation
is picked up in bounded time without needing a restart.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

import jwt
from jwt import PyJWKClient
from jwt.exceptions import PyJWKClientError

_JWKS_CACHE_SECONDS = 3600

_jwk_client: Optional[PyJWKClient] = None
_jwk_client_tenant: Optional[str] = None


class TokenValidationError(Exception):
    """Raised when an incoming request's bearer token fails validation --
    message is safe to log but deliberately generic to the caller (never
    echo back *why* a token failed to an unauthenticated request)."""


def _get_jwk_client(tenant_id: str) -> PyJWKClient:
    global _jwk_client, _jwk_client_tenant
    if _jwk_client is None or _jwk_client_tenant != tenant_id:
        jwks_uri = f"https://login.microsoftonline.com/{tenant_id}/discovery/v2.0/keys"
        _jwk_client = PyJWKClient(jwks_uri, cache_keys=True, lifespan=_JWKS_CACHE_SECONDS)
        _jwk_client_tenant = tenant_id
    return _jwk_client


def validate_bot_framework_token(authorization_header: Optional[str]) -> Dict[str, Any]:
    """Verifies the Authorization header of an incoming POST /api/messages
    request. Returns the decoded token claims on success; raises
    TokenValidationError on any failure (missing header, bad signature,
    wrong audience/issuer, expired)."""
    from app.config import get_settings

    s = get_settings()
    if not s.MICROSOFT_APP_ID or not s.MICROSOFT_APP_TENANT_ID:
        raise TokenValidationError("Bot Framework credentials are not configured.")

    if not authorization_header or not authorization_header.startswith("Bearer "):
        raise TokenValidationError("Missing bearer token.")
    token = authorization_header[len("Bearer "):]

    try:
        signing_key = _get_jwk_client(s.MICROSOFT_APP_TENANT_ID).get_signing_key_from_jwt(token)
        claims = jwt.decode(
            token,
            signing_key.key,
            algorithms=["RS256"],
            audience=s.MICROSOFT_APP_ID,
            issuer=f"https://login.microsoftonline.com/{s.MICROSOFT_APP_TENANT_ID}/v2.0",
        )
    except PyJWKClientError as exc:
        raise TokenValidationError(f"Could not fetch signing keys: {exc}") from exc
    except jwt.PyJWTError as exc:
        raise TokenValidationError(f"Token validation failed: {exc}") from exc

    return claims
