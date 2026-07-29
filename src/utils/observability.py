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
_EXEMPT_TOKEN_METRIC_KEYS = {
    "tokens_in",
    "tokens_out",
    "total_tokens",
    "prompt_tokens",
    "completion_tokens",
    "max_tokens",
    "token_count",
    "tokens",
    "tokens_used",
}


def _is_sensitive_key(key: Any) -> bool:
    normalized = str(key or "").lower().replace("-", "_")
    if normalized in _EXEMPT_TOKEN_METRIC_KEYS or normalized.endswith("_tokens") or normalized.startswith("tokens_"):
        return False
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


logger = logging.getLogger("src.utils.observability")


def calculate_estimated_cost(
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
    model_name: str = "gpt-4o",
) -> float:
    """Calculate estimated LLM USD cost based on token counts."""
    # Standard pricing benchmark (e.g. gpt-4o approx $2.50/1M input, $10.00/1M output)
    input_rate = 2.50 / 1_000_000
    output_rate = 10.00 / 1_000_000
    return round((prompt_tokens * input_rate) + (completion_tokens * output_rate), 6)


def log_event(event: str, *, level: int = logging.INFO, **fields: Any) -> None:
    payload = {
        "event": event,
        "timestamp": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        **sanitize_observability_value(fields),
    }
    logger.log(level, "[OBS] %s", json.dumps(payload, ensure_ascii=False, sort_keys=True))


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


def log_trajectory_step(
    step_name: str,
    *,
    trace_id: str | None = None,
    parent_span_id: str | None = None,
    doc_id: str | None = None,
    session_id: str | None = None,
    model_name: str = "gpt-4o",
    prompt_version: str = "v1",
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
    tool_name: str | None = None,
    tool_args: Any = None,
    tool_status: str | None = None,
    retry_count: int = 0,
    eval_score: float | None = None,
    duration_ms: float | None = None,
    error: str | None = None,
    level: int = logging.INFO,
    **extra: Any,
) -> None:
    """Logs a detailed trajectory step span with tokens, cost, and tool anatomy."""
    total_tokens = prompt_tokens + completion_tokens
    cost_usd = calculate_estimated_cost(prompt_tokens, completion_tokens, model_name=model_name)

    trajectory_payload = {
        "trajectory_step": step_name,
        "trace_id": trace_id,
        "parent_span_id": parent_span_id,
        "doc_id": doc_id,
        "session_id": session_id,
        "model_name": model_name,
        "prompt_version": prompt_version,
        "tokens_in": prompt_tokens,
        "tokens_out": completion_tokens,
        "total_tokens": total_tokens,
        "cost_usd": cost_usd,
        "tool_name": tool_name,
        "tool_args": sanitize_observability_value(tool_args) if tool_args is not None else None,
        "tool_status": tool_status,
        "retry_count": retry_count,
        "eval_score": eval_score,
        "duration_ms": duration_ms,
        "error": error,
        **extra,
    }

    log_event(
        f"trajectory.{step_name}",
        level=level,
        **{k: v for k, v in trajectory_payload.items() if v is not None},
    )

