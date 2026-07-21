"""Lightweight observability helpers for Azure Functions runtime logs."""

from __future__ import annotations

from datetime import UTC, datetime
import json
import logging
import re
import uuid
from typing import Any, Mapping


TRACE_HEADER = "x-mag-trace-id"
MAX_FIELD_LENGTH = 1200
_TRACE_ID_RE = re.compile(r"[^A-Za-z0-9_.:-]+")
_SENSITIVE_KEY_FRAGMENTS = (
    "api_key",
    "apikey",
    "authorization",
    "code",
    "connection_string",
    "functionkey",
    "key",
    "password",
    "secret",
    "sig",
    "signature",
    "token",
    "x-functions-key",
)


def _is_sensitive_key(key: Any) -> bool:
    normalized = str(key or "").lower().replace("-", "_")
    return any(fragment in normalized for fragment in _SENSITIVE_KEY_FRAGMENTS)


def sanitize_observability_value(value: Any) -> Any:
    """Redact secrets and keep log fields bounded."""
    if isinstance(value, Mapping):
        return {
            str(key): "[redacted]" if _is_sensitive_key(key) else sanitize_observability_value(item)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple, set)):
        return [sanitize_observability_value(item) for item in value]
    if isinstance(value, str):
        if len(value) > MAX_FIELD_LENGTH:
            return f"{value[:MAX_FIELD_LENGTH]}... [truncated; original length={len(value)} chars]"
        return value
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return str(value)


def normalize_trace_id(value: Any, *, prefix: str = "trace") -> str:
    text = _TRACE_ID_RE.sub("-", str(value or "").strip())[:128].strip("-")
    return text or f"{prefix}-{uuid.uuid4().hex}"


def trace_id_from_request(req: Any, *, prefix: str = "request") -> str:
    headers = getattr(req, "headers", {}) or {}
    trace_id = ""
    try:
        trace_id = headers.get(TRACE_HEADER) or headers.get(TRACE_HEADER.title()) or ""
    except AttributeError:
        trace_id = ""
    return normalize_trace_id(trace_id, prefix=prefix)


def log_event(event: str, *, level: int = logging.INFO, **fields: Any) -> None:
    payload = {
        "event": event,
        "timestamp": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        **sanitize_observability_value(fields),
    }
    logging.log(level, "[OBS] %s", json.dumps(payload, ensure_ascii=False, sort_keys=True))


def log_metric(
    name: str,
    value: int | float = 1,
    *,
    unit: str = "count",
    **dimensions: Any,
) -> None:
    log_event(
        "metric",
        metric_name=name,
        metric_value=value,
        metric_unit=unit,
        **dimensions,
    )
