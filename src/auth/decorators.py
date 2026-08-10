"""Decorator for enforcing Easy Auth identity and delegated scope authorization."""

import asyncio
import functools
import json
from typing import Any, Callable

import azure.functions as func
from src.auth.easy_auth import (
    PrincipalAuthenticationError,
    get_authenticated_principal,
    has_required_scope,
)
from src.utils.cors import cors_headers_for_request


def require_authenticated_scope(fn: Callable[..., Any]) -> Callable[..., Any]:
    """Require a trusted Easy Auth principal with ``user_impersonation``.

    Allows OPTIONS requests to pass through without authentication.
    Returns 401 for missing/invalid principals and 403 for insufficient scope.
    Attaches normalized claims to req.user_claims when authorized.
    """

    def _extract_req(args: tuple, kwargs: dict) -> Any:
        req = kwargs.get("req")
        if req is not None:
            return req
        for arg in args:
            if isinstance(arg, func.HttpRequest) or hasattr(arg, "headers"):
                return arg
        return args[0] if args else None

    def _error_response(
        req: Any, status_code: int, error: str, message: str
    ) -> func.HttpResponse:
        body = json.dumps({"error": error, "message": message})
        return func.HttpResponse(
            body,
            status_code=status_code,
            mimetype="application/json",
            headers=cors_headers_for_request(req),
        )

    def _authorize(req: Any) -> dict:
        principal = get_authenticated_principal(req)
        if not has_required_scope(principal):
            raise PermissionError("Required delegated scope is missing")
        return principal

    if asyncio.iscoroutinefunction(fn):

        @functools.wraps(fn)
        async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
            req = _extract_req(args, kwargs)
            if req and hasattr(req, "method") and req.method and req.method.upper() == "OPTIONS":
                return await fn(*args, **kwargs)

            if req is None:
                return _error_response(None, 401, "Unauthorized", "HTTP request object not found")

            try:
                principal = _authorize(req)
            except PrincipalAuthenticationError:
                return _error_response(
                    req, 401, "Unauthorized", "Invalid or missing authenticated principal"
                )
            except PermissionError:
                return _error_response(
                    req, 403, "Forbidden", "Required delegated scope is missing"
                )

            setattr(req, "user_claims", principal)
            return await fn(*args, **kwargs)

        return async_wrapper
    else:

        @functools.wraps(fn)
        def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
            req = _extract_req(args, kwargs)
            if req and hasattr(req, "method") and req.method and req.method.upper() == "OPTIONS":
                return fn(*args, **kwargs)

            if req is None:
                return _error_response(None, 401, "Unauthorized", "HTTP request object not found")

            try:
                principal = _authorize(req)
            except PrincipalAuthenticationError:
                return _error_response(
                    req, 401, "Unauthorized", "Invalid or missing authenticated principal"
                )
            except PermissionError:
                return _error_response(
                    req, 403, "Forbidden", "Required delegated scope is missing"
                )

            setattr(req, "user_claims", principal)
            return fn(*args, **kwargs)

        return sync_wrapper
