"""Decorator for enforcing JWT Bearer token authentication on Azure Functions handlers."""

import asyncio
import functools
import json
from typing import Any, Callable, Dict

import azure.functions as func
from src.auth.jwt_validator import validate_bearer_token


def require_token_auth(fn: Callable[..., Any]) -> Callable[..., Any]:
    """Decorator for Azure Functions HTTP handlers to validate JWT tokens.

    Allows OPTIONS requests to pass through without authentication.
    Validates token via validate_bearer_token. Returns 401 JSON response with CORS headers if invalid.
    Attaches claims to req.user_claims if valid.
    """
    def _extract_req(args: tuple, kwargs: dict) -> Any:
        req = kwargs.get("req")
        if req is not None:
            return req
        for arg in args:
            if isinstance(arg, func.HttpRequest) or hasattr(arg, "headers"):
                return arg
        return args[0] if args else None

    def _build_cors_headers(req: Any) -> Dict[str, str]:
        origin = "*"
        if req and hasattr(req, "headers"):
            origin = req.headers.get("origin") or req.headers.get("Origin") or "*"
        return {
            "Access-Control-Allow-Origin": origin,
            "Access-Control-Allow-Headers": "Authorization, Content-Type, x-functions-key",
            "Access-Control-Allow-Methods": "GET, POST, OPTIONS, PUT, DELETE",
        }

    def _unauthorized_response(req: Any, message: str = "Invalid or missing Bearer token") -> func.HttpResponse:
        body = json.dumps({"error": "Unauthorized", "message": message})
        headers = _build_cors_headers(req)
        return func.HttpResponse(
            body,
            status_code=401,
            mimetype="application/json",
            headers=headers,
        )

    if asyncio.iscoroutinefunction(fn):
        @functools.wraps(fn)
        async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
            req = _extract_req(args, kwargs)
            if req and hasattr(req, "method") and req.method and req.method.upper() == "OPTIONS":
                return await fn(*args, **kwargs)

            if req is None:
                return _unauthorized_response(None, "HTTP request object not found")

            payload = validate_bearer_token(req)
            if payload is None:
                return _unauthorized_response(req)

            setattr(req, "user_claims", payload)
            return await fn(*args, **kwargs)

        return async_wrapper
    else:
        @functools.wraps(fn)
        def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
            req = _extract_req(args, kwargs)
            if req and hasattr(req, "method") and req.method and req.method.upper() == "OPTIONS":
                return fn(*args, **kwargs)

            if req is None:
                return _unauthorized_response(None, "HTTP request object not found")

            payload = validate_bearer_token(req)
            if payload is None:
                return _unauthorized_response(req)

            setattr(req, "user_claims", payload)
            return fn(*args, **kwargs)

        return sync_wrapper
