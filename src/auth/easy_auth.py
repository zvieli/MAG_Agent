"""Trusted Azure Easy Auth principal parsing and scope normalization."""

from __future__ import annotations

import base64
import binascii
import json
import logging
import os
from typing import Any, Dict, Iterable, List, Mapping, Sequence

import azure.functions as func

logger = logging.getLogger(__name__)

DEFAULT_REQUIRED_SCOPE = "user_impersonation"
CLIENT_PRINCIPAL_HEADER = "x-ms-client-principal"

_SCOPE_CLAIMS = (
    "scp",
    "http://schemas.microsoft.com/identity/claims/scope",
)
_TENANT_CLAIMS = (
    "tid",
    "http://schemas.microsoft.com/identity/claims/tenantid",
)
_OBJECT_ID_CLAIMS = (
    "oid",
    "http://schemas.microsoft.com/identity/claims/objectidentifier",
)
_CLIENT_APP_CLAIMS = ("azp", "appid")
_SUBJECT_CLAIMS = (
    "sub",
    "http://schemas.xmlsoap.org/ws/2005/05/identity/claims/nameidentifier",
)
_NAME_CLAIMS = (
    "name",
    "preferred_username",
    "upn",
    "http://schemas.xmlsoap.org/ws/2005/05/identity/claims/upn",
)


class PrincipalAuthenticationError(ValueError):
    """Raised when a trusted Easy Auth principal is absent or malformed."""


def entra_auth_required() -> bool:
    """Return whether production Easy Auth identity headers are required.

    Authentication fails closed when the setting is absent or unrecognized. Local
    environments must explicitly set REQUIRE_ENTRA_AUTH=false.
    """

    configured = os.getenv("REQUIRE_ENTRA_AUTH")
    if configured is None:
        return True
    return configured.strip().lower() not in {"0", "false", "no", "off"}


def _local_principal() -> Dict[str, Any]:
    return {
        "auth_typ": "local",
        "scp": DEFAULT_REQUIRED_SCOPE,
        "tid": "local",
        "oid": "dev-user",
        "azp": "local-development",
        "sub": "dev-user",
        "name": "Local Development",
        "claims": [],
    }


def _get_header(headers: Mapping[str, Any], name: str) -> str | None:
    """Read a header without relying on a framework's casing behavior."""

    direct = headers.get(name)
    if isinstance(direct, str):
        return direct

    wanted = name.casefold()
    for key, value in headers.items():
        if str(key).casefold() == wanted and isinstance(value, str):
            return value
    return None


def _claim_values(
    claims_by_type: Mapping[str, Sequence[str]], aliases: Iterable[str]
) -> List[str]:
    values: List[str] = []
    for alias in aliases:
        values.extend(claims_by_type.get(alias.casefold(), ()))
    return values


def _first_claim(
    claims_by_type: Mapping[str, Sequence[str]], aliases: Iterable[str]
) -> str | None:
    for value in _claim_values(claims_by_type, aliases):
        normalized = value.strip()
        if normalized:
            return normalized
    return None


def _normalize_claims(payload: Mapping[str, Any]) -> Dict[str, Any]:
    auth_type = payload.get("auth_typ")
    if not isinstance(auth_type, str) or auth_type.strip().casefold() != "aad":
        raise PrincipalAuthenticationError(
            "Easy Auth principal is not Microsoft Entra authenticated"
        )

    raw_claims = payload.get("claims")
    if not isinstance(raw_claims, list):
        raise PrincipalAuthenticationError("Easy Auth principal claims must be an array")

    normalized_claims: List[Dict[str, str]] = []
    claims_by_type: Dict[str, List[str]] = {}
    for item in raw_claims:
        if not isinstance(item, dict):
            raise PrincipalAuthenticationError("Easy Auth principal contains an invalid claim")
        claim_type = item.get("typ")
        claim_value = item.get("val")
        if (
            not isinstance(claim_type, str)
            or not claim_type.strip()
            or not isinstance(claim_value, str)
        ):
            raise PrincipalAuthenticationError("Easy Auth principal contains an invalid claim")

        claim_type = claim_type.strip()
        normalized_claims.append({"typ": claim_type, "val": claim_value})
        claims_by_type.setdefault(claim_type.casefold(), []).append(claim_value)

    scope_values = _claim_values(claims_by_type, _SCOPE_CLAIMS)
    scopes: List[str] = []
    for scope_value in scope_values:
        for scope in scope_value.split():
            if scope and scope not in scopes:
                scopes.append(scope)

    name_aliases = list(_NAME_CLAIMS)
    name_type = payload.get("name_typ")
    if isinstance(name_type, str) and name_type.strip():
        name_aliases.insert(0, name_type.strip())

    principal: Dict[str, Any] = {
        "auth_typ": "aad",
        "scp": " ".join(scopes),
        "claims": normalized_claims,
    }
    canonical_claims = {
        "tid": _first_claim(claims_by_type, _TENANT_CLAIMS),
        "oid": _first_claim(claims_by_type, _OBJECT_ID_CLAIMS),
        "azp": _first_claim(claims_by_type, _CLIENT_APP_CLAIMS),
        "sub": _first_claim(claims_by_type, _SUBJECT_CLAIMS),
        "name": _first_claim(claims_by_type, name_aliases),
    }
    principal.update(
        {key: value for key, value in canonical_claims.items() if value is not None}
    )
    return principal


def get_authenticated_principal(req: func.HttpRequest) -> Dict[str, Any]:
    """Return normalized trusted claims or raise a clean authentication error."""

    if not entra_auth_required():
        return _local_principal()

    encoded = _get_header(req.headers, CLIENT_PRINCIPAL_HEADER)
    if not encoded or not encoded.strip():
        raise PrincipalAuthenticationError("Missing Easy Auth client principal")

    try:
        decoded = base64.b64decode(encoded.strip(), validate=True)
        payload = json.loads(decoded.decode("utf-8"))
    except (binascii.Error, UnicodeDecodeError, json.JSONDecodeError, ValueError, TypeError) as exc:
        logger.warning("Rejected malformed Easy Auth client principal: %s", type(exc).__name__)
        raise PrincipalAuthenticationError("Malformed Easy Auth client principal") from exc

    if not isinstance(payload, dict):
        raise PrincipalAuthenticationError("Easy Auth client principal must be a JSON object")
    principal = _normalize_claims(payload)
    logger.info(
        "Accepted Easy Auth principal: tid=%s oid=%s scp=%s azp=%s",
        principal.get("tid"),
        principal.get("oid"),
        principal.get("scp"),
        principal.get("azp"),
    )
    return principal


def has_required_scope(
    principal: Mapping[str, Any], required_scope: str = DEFAULT_REQUIRED_SCOPE
) -> bool:
    granted_scopes = principal.get("scp")
    return isinstance(granted_scopes, str) and required_scope in granted_scopes.split()
