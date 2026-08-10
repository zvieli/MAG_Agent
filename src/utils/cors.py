"""Shared CORS policy for application-generated HTTP responses."""

from __future__ import annotations

import json
import logging
import os
from functools import lru_cache
from typing import Any, Dict, Iterable

logger = logging.getLogger(__name__)

DEFAULT_ALLOWED_ORIGINS = ("https://tikshuv.sharepoint.com",)
ALLOWED_HEADERS = (
    "Authorization",
    "Content-Type",
    "Content-Disposition",
    "x-mag-file-name",
    "x-mag-trace-id",
    "Cache-Control",
    "Pragma",
)
ALLOWED_METHODS = ("GET", "POST", "OPTIONS")


def _normalize_origins(values: Iterable[Any]) -> tuple[str, ...]:
    origins = []
    for value in values:
        if not isinstance(value, str):
            continue
        origin = value.strip().rstrip("/")
        if not origin or origin == "*":
            continue
        if origin not in origins:
            origins.append(origin)
    return tuple(origins)


@lru_cache(maxsize=8)
def _parse_allowed_origins(configured: str | None) -> tuple[str, ...]:
    if not configured or not configured.strip():
        return DEFAULT_ALLOWED_ORIGINS

    try:
        parsed = json.loads(configured)
        if isinstance(parsed, list):
            origins = _normalize_origins(parsed)
        elif isinstance(parsed, str):
            origins = _normalize_origins([parsed])
        else:
            origins = ()
    except json.JSONDecodeError:
        origins = _normalize_origins(configured.split(","))

    if not origins:
        logger.warning("CORS_ALLOWED_ORIGINS contained no safe explicit origins")
    return origins


def allowed_origins() -> tuple[str, ...]:
    return _parse_allowed_origins(os.getenv("CORS_ALLOWED_ORIGINS"))


def cors_headers_for_request(req: Any = None) -> Dict[str, str]:
    headers = {
        "Access-Control-Allow-Methods": ", ".join(ALLOWED_METHODS),
        "Access-Control-Allow-Headers": ", ".join(ALLOWED_HEADERS),
        "Access-Control-Max-Age": "86400",
    }
    if req is None or not hasattr(req, "headers"):
        return headers

    origin = req.headers.get("origin") or req.headers.get("Origin")
    if isinstance(origin, str) and origin.rstrip("/") in allowed_origins():
        headers["Access-Control-Allow-Origin"] = origin.rstrip("/")
        headers["Vary"] = "Origin"
    return headers
