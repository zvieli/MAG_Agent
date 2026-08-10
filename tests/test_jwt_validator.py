"""Unit tests for JWT validation and @require_token_auth decorator."""

import json
from unittest.mock import MagicMock, patch
import pytest
import azure.functions as func
import jwt

from src.auth.jwt_validator import get_jwks_client, validate_bearer_token
from src.auth.decorators import require_token_auth


def create_mock_request(method="GET", headers=None):
    """Helper to create a mock Azure Functions HttpRequest."""
    req = MagicMock(spec=func.HttpRequest)
    req.method = method
    req.headers = headers or {}
    return req


def test_get_jwks_client_caching():
    """Test that get_jwks_client creates and caches PyJWKClient per tenant."""
    client1 = get_jwks_client("tenant-abc")
    client2 = get_jwks_client("tenant-abc")
    client3 = get_jwks_client("tenant-xyz")

    assert client1 is client2
    assert client1 is not client3
    assert client1.uri == "https://login.microsoftonline.com/tenant-abc/discovery/v2.0/keys"


def test_validate_bearer_token_require_entra_auth_false(monkeypatch):
    """Test that REQUIRE_ENTRA_AUTH=false returns dev user claims for backward compatibility."""
    monkeypatch.setenv("REQUIRE_ENTRA_AUTH", "false")
    req = create_mock_request()

    result = validate_bearer_token(req)
    assert result == {"sub": "dev-user", "name": "Local Development"}


def test_validate_bearer_token_missing_config(monkeypatch):
    """Test that validate_bearer_token returns None if tenant or client ID is missing."""
    monkeypatch.setenv("REQUIRE_ENTRA_AUTH", "true")
    monkeypatch.delenv("AZURE_Tenant_ID", raising=False)
    monkeypatch.delenv("AZURE_CLIENT_ID", raising=False)

    req = create_mock_request(headers={"Authorization": "Bearer some-token"})
    result = validate_bearer_token(req)
    assert result is None


def test_validate_bearer_token_missing_header(monkeypatch):
    """Test missing Authorization header returns None."""
    monkeypatch.setenv("REQUIRE_ENTRA_AUTH", "true")
    monkeypatch.setenv("AZURE_Tenant_ID", "tenant-123")
    monkeypatch.setenv("AZURE_CLIENT_ID", "client-123")

    req = create_mock_request(headers={})
    assert validate_bearer_token(req) is None


def test_validate_bearer_token_invalid_header_format(monkeypatch):
    """Test malformed Authorization header returns None."""
    monkeypatch.setenv("REQUIRE_ENTRA_AUTH", "true")
    monkeypatch.setenv("AZURE_Tenant_ID", "tenant-123")
    monkeypatch.setenv("AZURE_CLIENT_ID", "client-123")

    req1 = create_mock_request(headers={"Authorization": "Basic abcdef"})
    assert validate_bearer_token(req1) is None

    req2 = create_mock_request(headers={"Authorization": "Bearer"})
    assert validate_bearer_token(req2) is None


@patch("src.auth.jwt_validator.get_jwks_client")
@patch("jwt.decode")
def test_validate_bearer_token_valid(mock_jwt_decode, mock_get_jwks, monkeypatch):
    """A valid v2 delegated token returns its claims payload."""
    monkeypatch.setenv("REQUIRE_ENTRA_AUTH", "true")
    monkeypatch.setenv("AZURE_Tenant_ID", "tenant-123")
    monkeypatch.setenv("AZURE_CLIENT_ID", "client-123")
    monkeypatch.setenv("AZURE_Application_ID_URI", "api://client-123")

    mock_jwks_client = MagicMock()
    mock_signing_key = MagicMock()
    mock_signing_key.key = "fake-public-key"
    mock_jwks_client.get_signing_key_from_jwt.return_value = mock_signing_key
    mock_get_jwks.return_value = mock_jwks_client

    expected_payload = {
        "sub": "user-456",
        "name": "Jane Doe",
        "iss": "https://login.microsoftonline.com/tenant-123/v2.0",
        "aud": "client-123",
        "scp": "openid user_impersonation profile",
        "tid": "tenant-123",
        "oid": "object-456",
        "ver": "2.0",
    }
    mock_jwt_decode.return_value = expected_payload

    req = create_mock_request(headers={"Authorization": "Bearer valid-token"})
    result = validate_bearer_token(req)

    assert result == expected_payload
    mock_get_jwks.assert_called_with("tenant-123")
    mock_jwks_client.get_signing_key_from_jwt.assert_called_with("valid-token")
    mock_jwt_decode.assert_called_once_with(
        "valid-token",
        "fake-public-key",
        algorithms=["RS256"],
        audience=["client-123", "api://client-123"],
        issuer=[
            "https://sts.windows.net/tenant-123/",
            "https://login.microsoftonline.com/tenant-123/v2.0",
            "https://login.microsoftonline.com/tenant-123/v2.0/",
        ],
    )


@patch("src.auth.jwt_validator.get_jwks_client")
@patch("jwt.decode")
def test_validate_bearer_token_app_id_uri_normalization(
    mock_jwt_decode, mock_get_jwks, monkeypatch
):
    """Scope-form Application ID URIs are normalized before audience validation."""
    monkeypatch.setenv("REQUIRE_ENTRA_AUTH", "true")
    monkeypatch.setenv("AZURE_Tenant_ID", "tenant-azure-alt")
    monkeypatch.setenv("AZURE_CLIENT_ID", "client-azure-alt")
    monkeypatch.setenv(
        "AZURE_Application_ID_URI", "api://custom-uri/user_impersonation/.default"
    )

    mock_jwks_client = MagicMock()
    mock_signing_key = MagicMock()
    mock_signing_key.key = "fake-key"
    mock_jwks_client.get_signing_key_from_jwt.return_value = mock_signing_key
    mock_get_jwks.return_value = mock_jwks_client

    mock_jwt_decode.return_value = {"sub": "user-1", "scp": "user_impersonation"}

    req = create_mock_request(headers={"Authorization": "Bearer valid-token"})
    validate_bearer_token(req)

    mock_get_jwks.assert_called_with("tenant-azure-alt")
    call_kwargs = mock_jwt_decode.call_args.kwargs
    assert call_kwargs["audience"] == ["client-azure-alt", "api://custom-uri"]
    assert all("user_impersonation" not in audience for audience in call_kwargs["audience"])


@patch("src.auth.jwt_validator.get_jwks_client")
@patch("jwt.decode")
def test_validate_bearer_token_missing_scope(mock_jwt_decode, mock_get_jwks, monkeypatch):
    """A signed delegated token without the required scope is rejected."""
    monkeypatch.setenv("REQUIRE_ENTRA_AUTH", "true")
    monkeypatch.setenv("AZURE_Tenant_ID", "tenant-123")
    monkeypatch.setenv("AZURE_CLIENT_ID", "client-123")
    mock_get_jwks.return_value.get_signing_key_from_jwt.return_value.key = "key"
    mock_jwt_decode.return_value = {"sub": "user-1", "scp": "openid profile"}

    req = create_mock_request(headers={"Authorization": "Bearer valid-token"})
    assert validate_bearer_token(req) is None


@patch("src.auth.jwt_validator.get_jwks_client")
@patch("jwt.decode")
def test_validate_bearer_token_wrong_scope(mock_jwt_decode, mock_get_jwks, monkeypatch):
    """A similarly named scope does not satisfy the exact scope check."""
    monkeypatch.setenv("REQUIRE_ENTRA_AUTH", "true")
    monkeypatch.setenv("AZURE_Tenant_ID", "tenant-123")
    monkeypatch.setenv("AZURE_CLIENT_ID", "client-123")
    mock_get_jwks.return_value.get_signing_key_from_jwt.return_value.key = "key"
    mock_jwt_decode.return_value = {"sub": "user-1", "scp": "user_impersonation.read"}

    req = create_mock_request(headers={"Authorization": "Bearer valid-token"})
    assert validate_bearer_token(req) is None


@patch("src.auth.jwt_validator.get_jwks_client")
@patch("jwt.decode")
def test_validate_bearer_token_expired(mock_jwt_decode, mock_get_jwks, monkeypatch):
    """Test expired JWT token returns None."""
    monkeypatch.setenv("REQUIRE_ENTRA_AUTH", "true")
    monkeypatch.setenv("AZURE_Tenant_ID", "tenant-123")
    monkeypatch.setenv("AZURE_CLIENT_ID", "client-123")

    mock_jwks_client = MagicMock()
    mock_get_jwks.return_value = mock_jwks_client
    mock_jwt_decode.side_effect = jwt.ExpiredSignatureError("Signature has expired")

    req = create_mock_request(headers={"Authorization": "Bearer expired-token"})
    result = validate_bearer_token(req)
    assert result is None


@patch("src.auth.jwt_validator.get_jwks_client")
@patch("jwt.decode")
def test_validate_bearer_token_wrong_audience(mock_jwt_decode, mock_get_jwks, monkeypatch):
    """Test JWT token with wrong audience returns None."""
    monkeypatch.setenv("REQUIRE_ENTRA_AUTH", "true")
    monkeypatch.setenv("AZURE_Tenant_ID", "tenant-123")
    monkeypatch.setenv("AZURE_CLIENT_ID", "client-123")

    mock_jwks_client = MagicMock()
    mock_get_jwks.return_value = mock_jwks_client
    mock_jwt_decode.side_effect = jwt.InvalidAudienceError("Audience invalid")

    req = create_mock_request(headers={"Authorization": "Bearer wrong-aud-token"})
    result = validate_bearer_token(req)
    assert result is None


@patch("src.auth.jwt_validator.get_jwks_client")
@patch("jwt.decode")
def test_validate_bearer_token_wrong_issuer(mock_jwt_decode, mock_get_jwks, monkeypatch):
    """A token issued by another tenant is rejected."""
    monkeypatch.setenv("REQUIRE_ENTRA_AUTH", "true")
    monkeypatch.setenv("AZURE_Tenant_ID", "tenant-123")
    monkeypatch.setenv("AZURE_CLIENT_ID", "client-123")
    mock_get_jwks.return_value.get_signing_key_from_jwt.return_value.key = "key"
    mock_jwt_decode.side_effect = jwt.InvalidIssuerError("Issuer invalid")

    req = create_mock_request(headers={"Authorization": "Bearer wrong-issuer-token"})
    assert validate_bearer_token(req) is None


# --- Decorator Tests ---

def test_require_token_auth_options_passthrough():
    """Test that OPTIONS method passes through handler without authentication."""
    handler_called = False

    @require_token_auth
    def dummy_handler(req):
        nonlocal handler_called
        handler_called = True
        return func.HttpResponse("OK", status_code=200)

    req = create_mock_request(method="OPTIONS")
    resp = dummy_handler(req)

    assert handler_called is True
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_require_token_auth_options_passthrough_async():
    """Test that OPTIONS method passes through async handler."""
    handler_called = False

    @require_token_auth
    async def dummy_handler(req):
        nonlocal handler_called
        handler_called = True
        return func.HttpResponse("OK", status_code=200)

    req = create_mock_request(method="OPTIONS")
    resp = await dummy_handler(req)

    assert handler_called is True
    assert resp.status_code == 200


@patch("src.auth.decorators.validate_bearer_token")
def test_require_token_auth_unauthorized(mock_validate):
    """Test that invalid token returns 401 response with CORS headers."""
    mock_validate.return_value = None
    handler_called = False

    @require_token_auth
    def dummy_handler(req):
        nonlocal handler_called
        handler_called = True
        return func.HttpResponse("OK", status_code=200)

    req = create_mock_request(method="GET", headers={"Origin": "https://example.com"})
    resp = dummy_handler(req)

    assert handler_called is False
    assert resp.status_code == 401
    assert resp.mimetype == "application/json"
    body_data = json.loads(resp.get_body().decode("utf-8"))
    assert body_data["error"] == "Unauthorized"
    assert resp.headers.get("Access-Control-Allow-Origin") == "https://example.com"
    assert "Authorization" in resp.headers.get("Access-Control-Allow-Headers")


@patch("src.auth.decorators.validate_bearer_token")
def test_require_token_auth_authorized(mock_validate):
    """Test that valid token attaches user_claims and invokes handler."""
    claims = {"sub": "user-789", "name": "Alice"}
    mock_validate.return_value = claims
    captured_req = None

    @require_token_auth
    def dummy_handler(req):
        nonlocal captured_req
        captured_req = req
        return func.HttpResponse("Success", status_code=200)

    req = create_mock_request(method="GET", headers={"Authorization": "Bearer valid-token"})
    resp = dummy_handler(req)

    assert resp.status_code == 200
    assert captured_req is req
    assert getattr(req, "user_claims", None) == claims
