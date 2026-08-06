"""JWT Validation module for Azure AD / Entra ID tokens."""

import functools
import logging
import os
from typing import Any, Dict, Optional

import azure.functions as func
import jwt
from jwt import PyJWKClient

logger = logging.getLogger(__name__)


@functools.lru_cache(maxsize=16)
def get_jwks_client(tenant_id: str) -> PyJWKClient:
    """Get and cache PyJWKClient for the specified tenant ID."""
    jwks_url = f"https://login.microsoftonline.com/{tenant_id}/discovery/v2.0/keys"
    return PyJWKClient(jwks_url)


def validate_bearer_token(req: func.HttpRequest) -> Optional[Dict[str, Any]]:
    """Validate Bearer token from HttpRequest header against Entra ID JWKS.

    If REQUIRE_ENTRA_AUTH environment variable is not 'true', returns default dev user payload.
    Otherwise, decodes token, verifies RS256 signature, audience, and issuer.
    Returns claims dict if valid, or None if invalid/expired/missing.
    """
    require_auth = os.getenv("REQUIRE_ENTRA_AUTH", "false").lower() == "true"
    if not require_auth:
        return {"sub": "dev-user", "name": "Local Development"}

    tenant_id = os.getenv("ENTRA_TENANT_ID") or os.getenv("AZURE_Tenant_ID") or os.getenv("AZURE_TENANT_ID")
    client_id = os.getenv("ENTRA_BACKEND_CLIENT_ID") or os.getenv("AZURE_CLIENT_ID")

    if not tenant_id or not client_id:
        logger.warning("ENTRA_TENANT_ID or ENTRA_BACKEND_CLIENT_ID / AZURE_CLIENT_ID not configured")
        return None

    auth_header = req.headers.get("authorization") or req.headers.get("Authorization")
    if not auth_header:
        logger.debug("Missing Authorization header")
        return None

    parts = auth_header.strip().split(" ", 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        logger.debug("Invalid Authorization header format")
        return None

    token = parts[1].strip()
    if not token:
        logger.debug("Empty Bearer token")
        return None

    try:
        jwks_client = get_jwks_client(tenant_id)
        signing_key = jwks_client.get_signing_key_from_jwt(token)

        valid_issuers = [
            f"https://sts.windows.net/{tenant_id}/",
            f"https://login.microsoftonline.com/{tenant_id}/v2.0",
            f"https://login.microsoftonline.com/{tenant_id}/v2.0/",
        ]
        valid_audiences = [client_id, f"api://{client_id}"]

        payload = jwt.decode(
            token,
            signing_key.key,
            algorithms=["RS256"],
            audience=valid_audiences,
            issuer=valid_issuers,
        )
        return payload
    except jwt.PyJWTError as e:
        logger.warning(f"JWT validation failed: {str(e)}")
        return None
    except Exception as e:
        logger.warning(f"Unexpected error during JWT validation: {str(e)}")
        return None
