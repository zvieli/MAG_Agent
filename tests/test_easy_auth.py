"""Unit tests for Easy Auth principal parsing and scope authorization."""

import ast
import base64
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import azure.functions as func
import pytest

from src.auth.decorators import require_authenticated_scope
from src.auth.easy_auth import (
    PrincipalAuthenticationError,
    get_authenticated_principal,
    has_required_scope,
)


def create_mock_request(method="GET", headers=None):
    req = MagicMock(spec=func.HttpRequest)
    req.method = method
    req.headers = headers or {}
    return req


def encode_principal(claims, *, auth_type="aad", name_type="name"):
    payload = {
        "auth_typ": auth_type,
        "name_typ": name_type,
        "role_typ": "roles",
        "claims": [{"typ": typ, "val": val} for typ, val in claims],
    }
    return base64.b64encode(json.dumps(payload).encode("utf-8")).decode("ascii")


def test_auth_enabled_by_default_fails_closed(monkeypatch):
    monkeypatch.delenv("REQUIRE_ENTRA_AUTH", raising=False)
    with pytest.raises(PrincipalAuthenticationError, match="Missing"):
        get_authenticated_principal(create_mock_request())


def test_explicitly_disabled_auth_returns_stable_local_identity(monkeypatch):
    monkeypatch.setenv("REQUIRE_ENTRA_AUTH", "false")
    principal = get_authenticated_principal(
        create_mock_request(headers={"X-MS-CLIENT-PRINCIPAL": "not-used"})
    )
    assert principal == {
        "auth_typ": "local",
        "scp": "user_impersonation",
        "tid": "local",
        "oid": "dev-user",
        "azp": "local-development",
        "sub": "dev-user",
        "name": "Local Development",
        "claims": [],
    }
    assert has_required_scope(principal)


def test_valid_principal_normalizes_mapped_claim_variants(monkeypatch):
    monkeypatch.setenv("REQUIRE_ENTRA_AUTH", "true")
    header = encode_principal(
        [
            ("http://schemas.microsoft.com/identity/claims/scope", "openid user_impersonation"),
            ("scp", "profile user_impersonation"),
            ("http://schemas.microsoft.com/identity/claims/tenantid", "tenant-123"),
            ("http://schemas.microsoft.com/identity/claims/objectidentifier", "object-456"),
            ("appid", "sharepoint-client"),
            ("sub", "subject-789"),
            ("preferred_username", "alice@example.com"),
        ],
        name_type="preferred_username",
    )
    req = create_mock_request(headers={"X-Ms-Client-Principal": header})

    principal = get_authenticated_principal(req)

    assert principal["auth_typ"] == "aad"
    assert principal["scp"] == "profile user_impersonation openid"
    assert principal["tid"] == "tenant-123"
    assert principal["oid"] == "object-456"
    assert principal["azp"] == "sharepoint-client"
    assert principal["sub"] == "subject-789"
    assert principal["name"] == "alice@example.com"
    assert len(principal["claims"]) == 7
    assert has_required_scope(principal)


@pytest.mark.parametrize(
    "header,expected",
    [
        (None, "Missing"),
        ("%%%", "Malformed"),
        (base64.b64encode(b"\xff").decode("ascii"), "Malformed"),
        (base64.b64encode(b"not-json").decode("ascii"), "Malformed"),
        (base64.b64encode(b"[]").decode("ascii"), "JSON object"),
        (encode_principal([], auth_type="github"), "not Microsoft Entra"),
        (
            base64.b64encode(json.dumps({"auth_typ": "aad", "claims": {}}).encode()).decode(),
            "claims must be an array",
        ),
        (
            base64.b64encode(
                json.dumps({"auth_typ": "aad", "claims": [{"typ": "scp"}]}).encode()
            ).decode(),
            "invalid claim",
        ),
    ],
)
def test_invalid_principal_inputs_are_clean_authentication_errors(monkeypatch, header, expected):
    monkeypatch.setenv("REQUIRE_ENTRA_AUTH", "true")
    headers = {} if header is None else {"x-ms-client-principal": header}
    with pytest.raises(PrincipalAuthenticationError, match=expected):
        get_authenticated_principal(create_mock_request(headers=headers))


def test_scope_matching_is_exact():
    assert has_required_scope({"scp": "openid user_impersonation profile"})
    assert not has_required_scope({"scp": "user_impersonation.read"})
    assert not has_required_scope({"scp": ["user_impersonation"]})
    assert not has_required_scope({})


def test_decorator_options_passthrough():
    handler_called = False

    @require_authenticated_scope
    def handler(req):
        nonlocal handler_called
        handler_called = True
        return func.HttpResponse("OK", status_code=200)

    response = handler(create_mock_request(method="OPTIONS"))
    assert handler_called is True
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_decorator_options_passthrough_async():
    handler_called = False

    @require_authenticated_scope
    async def handler(req):
        nonlocal handler_called
        handler_called = True
        return func.HttpResponse("OK", status_code=200)

    response = await handler(create_mock_request(method="OPTIONS"))
    assert handler_called is True
    assert response.status_code == 200


@patch("src.auth.decorators.get_authenticated_principal")
def test_decorator_returns_401_for_invalid_principal(mock_principal):
    mock_principal.side_effect = PrincipalAuthenticationError("bad principal")

    @require_authenticated_scope
    def handler(req):
        raise AssertionError("handler must not run")

    req = create_mock_request(headers={"Origin": "https://tikshuv.sharepoint.com"})
    response = handler(req)
    body = json.loads(response.get_body().decode("utf-8"))
    assert response.status_code == 401
    assert body["error"] == "Unauthorized"
    assert response.headers["Access-Control-Allow-Origin"] == "https://tikshuv.sharepoint.com"
    assert "x-functions-key" not in response.headers["Access-Control-Allow-Headers"]


@patch("src.auth.decorators.get_authenticated_principal")
def test_decorator_returns_403_for_missing_scope(mock_principal):
    mock_principal.return_value = {"auth_typ": "aad", "scp": "openid profile"}

    @require_authenticated_scope
    def handler(req):
        raise AssertionError("handler must not run")

    response = handler(create_mock_request())
    body = json.loads(response.get_body().decode("utf-8"))
    assert response.status_code == 403
    assert body["error"] == "Forbidden"


@patch("src.auth.decorators.get_authenticated_principal")
def test_decorator_attaches_normalized_claims(mock_principal):
    principal = {"auth_typ": "aad", "scp": "user_impersonation", "oid": "user-1"}
    mock_principal.return_value = principal
    captured = None

    @require_authenticated_scope
    def handler(req):
        nonlocal captured
        captured = req.user_claims
        return func.HttpResponse("OK", status_code=200)

    response = handler(create_mock_request())
    assert response.status_code == 200
    assert captured == principal


def test_all_http_routes_except_ping_require_scope_decorator():
    source_path = Path(__file__).parents[1] / "function_app.py"
    module = ast.parse(source_path.read_text(encoding="utf-8"))
    routes = {}

    for node in module.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        route = None
        decorators = []
        for decorator in node.decorator_list:
            target = decorator.func if isinstance(decorator, ast.Call) else decorator
            if isinstance(target, ast.Attribute):
                decorators.append(target.attr)
            elif isinstance(target, ast.Name):
                decorators.append(target.id)
            if isinstance(decorator, ast.Call) and isinstance(decorator.func, ast.Attribute):
                if decorator.func.attr == "route":
                    route_kw = next((kw for kw in decorator.keywords if kw.arg == "route"), None)
                    if route_kw and isinstance(route_kw.value, ast.Constant):
                        route = route_kw.value.value
        if route:
            routes[route] = decorators

    assert routes["ping"].count("require_authenticated_scope") == 0
    protected = {route for route, decorators in routes.items() if "require_authenticated_scope" in decorators}
    assert protected == set(routes) - {"ping"}
