"""JWT Validation module for Azure AD / Entra ID tokens."""

import functools
import logging
import os
from typing import Any, Dict, Optional

import azure.functions as func
import jwt
from jwt import PyJWKClient

logger = logging.getLogger(__name__)

DEFAULT_REQUIRED_SCOPE = "user_impersonation"


def _normalize_app_id_uri(value: str) -> str:
    """Normalize a configured Application ID URI to a token audience value."""
    normalized = value.strip().rstrip("/")
    removed_suffix = True
    while removed_suffix:
        removed_suffix = False
        for suffix in ("/user_impersonation", "/.default"):
            if normalized.lower().endswith(suffix.lower()):
                normalized = normalized[: -len(suffix)].rstrip("/")
                removed_suffix = True
                break
    return normalized


@functools.lru_cache(maxsize=16)
def get_jwks_client(tenant_id: str) -> PyJWKClient:
    """Get and cache PyJWKClient for the specified tenant ID."""
    jwks_url = f"https://login.microsoftonline.com/{tenant_id}/discovery/v2.0/keys"
    return PyJWKClient(jwks_url)


def validate_bearer_token(req: func.HttpRequest) -> Optional[Dict[str, Any]]:
    """Validate Bearer token from HttpRequest header against Entra ID JWKS.

    If REQUIRE_ENTRA_AUTH environment variable is not 'true', returns default dev user payload.
    Otherwise, decodes token and verifies RS256 signature, audience, issuer,
    and the required user_impersonation delegated scope.
    Returns claims dict if valid, or None if invalid/expired/missing.
    """
    require_auth = os.getenv("REQUIRE_ENTRA_AUTH", "false").lower() == "true"
    if not require_auth:
        return {"sub": "dev-user", "name": "Local Development"}

    tenant_id = os.getenv("AZURE_Tenant_ID")
    client_id = os.getenv("AZURE_CLIENT_ID")
    app_id_uri = os.getenv("AZURE_Application_ID_URI") or f"api://{client_id}"

    if not tenant_id or not client_id:
        logger.warning(
            "Entra authentication settings are incomplete; expected "
            "AZURE_Tenant_ID and AZURE_CLIENT_ID"
        )
        return None

    normalized_app_id_uri = _normalize_app_id_uri(app_id_uri)
    if not normalized_app_id_uri:
        logger.warning(
            "AZURE_Application_ID_URI is invalid after audience normalization"
        )
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
        valid_audiences = list(dict.fromkeys([client_id, normalized_app_id_uri]))

        payload = jwt.decode(
            token,
            signing_key.key,
            algorithms=["RS256"],
            audience=valid_audiences,
            issuer=valid_issuers,
        )

        granted_scopes = payload.get("scp")
        if (
            not isinstance(granted_scopes, str)
            or DEFAULT_REQUIRED_SCOPE not in granted_scopes.split()
        ):
            logger.warning("JWT validation failed: required delegated scope is missing")
            return None

        logger.info(
            "Validated Entra token claims: aud=%s iss=%s tid=%s scp=%s oid=%s ver=%s",
            payload.get("aud"),
            payload.get("iss"),
            payload.get("tid"),
            granted_scopes,
            payload.get("oid"),
            payload.get("ver"),
        )
        return payload
    except jwt.PyJWTError as e:
        logger.warning(f"JWT validation failed: {str(e)}")
        return None
    except Exception as e:
        logger.warning(f"Unexpected error during JWT validation: {str(e)}")
        return None
