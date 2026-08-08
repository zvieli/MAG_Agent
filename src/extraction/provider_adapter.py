"""Normalize OpenAI and Azure OpenAI extraction outcomes without inspecting source text."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from enum import Enum
from typing import Any, Iterable

try:
    from openai import APIStatusError, ContentFilterFinishReasonError
except ImportError:  # pragma: no cover - supports SDKs during a guarded preflight failure
    APIStatusError = ()  # type: ignore[assignment,misc]
    ContentFilterFinishReasonError = ()  # type: ignore[assignment,misc]


class ExtractionOutcomeCode(str, Enum):
    CONTENT_BLOCKED = "CONTENT_BLOCKED"
    OUTPUT_SCHEMA_INVALID = "OUTPUT_SCHEMA_INVALID"
    OUTPUT_LOGIC_INVALID = "OUTPUT_LOGIC_INVALID"
    OUTPUT_INCOMPLETE = "OUTPUT_INCOMPLETE"
    PROVIDER_SCHEMA_UNSUPPORTED = "PROVIDER_SCHEMA_UNSUPPORTED"
    PROVIDER_ERROR_UNCLASSIFIED = "PROVIDER_ERROR_UNCLASSIFIED"


@dataclass(frozen=True)
class ProviderClassification:
    code: ExtractionOutcomeCode
    request_id: str | None
    provider_signal: str
    sanitized_reason: str
    status_code: int | None = None
    discovered_codes: tuple[str, ...] = ()
    body_key_shape: tuple[str, ...] = ()


class ProviderOutcomeError(RuntimeError):
    def __init__(self, classification: ProviderClassification):
        self.classification = classification
        super().__init__(f"{classification.code.value}: {classification.sanitized_reason}")


_CONTENT_POLICY_CODES_VERSION = "azure-content-policy-v1"
_CONTENT_POLICY_CODES = {
    "contentfilter",
    "contentfiltered",
    "contentfiltererror",
    "contentpolicyviolation",
    "responsibleaipolicyviolation",
    "rai_policy_violation".replace("_", ""),
}
_SCHEMA_UNSUPPORTED_CODES = {
    "structuredoutputsunsupported",
    "responseformatunsupported",
    "unsupportedresponseformat",
}


def _normalize_code(value: Any) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value or "").lower())


def _as_mapping(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, (dict, list)):
        return value
    if isinstance(value, (bytes, bytearray)):
        try:
            return json.loads(value.decode("utf-8"))
        except Exception:
            return None
    if isinstance(value, str):
        try:
            return json.loads(value)
        except Exception:
            return None
    json_method = getattr(value, "json", None)
    if callable(json_method):
        try:
            return json_method()
        except Exception:
            return None
    return None


def _inspect_body(body: Any) -> tuple[list[tuple[str, str]], list[str], list[str]]:
    codes: list[tuple[str, str]] = []
    filtered_paths: list[str] = []
    shapes: list[str] = []

    def walk(value: Any, path: str) -> None:
        if isinstance(value, dict):
            shapes.append(f"{path}:{{{','.join(sorted(str(k) for k in value))}}}")
            for key, child in value.items():
                child_path = f"{path}.{key}"
                key_norm = _normalize_code(key)
                if key_norm in {"code", "type"} and isinstance(child, (str, int)):
                    codes.append((child_path, str(child)))
                policy_result_path = _normalize_code(child_path)
                if (
                    key_norm == "filtered"
                    and child is True
                    and (
                        "promptfilterresults" in policy_result_path
                        or "contentfilterresults" in policy_result_path
                    )
                ):
                    filtered_paths.append(child_path)
                walk(child, child_path)
        elif isinstance(value, list):
            shapes.append(f"{path}:[{len(value)}]")
            for index, child in enumerate(value):
                walk(child, f"{path}[{index}]")

    walk(body, "$")
    return codes, filtered_paths, shapes


def _request_id(exc: Any) -> str | None:
    direct = getattr(exc, "request_id", None)
    if direct:
        return str(direct)
    response = getattr(exc, "response", None)
    headers = getattr(response, "headers", {}) or {}
    return headers.get("x-request-id") or headers.get("apim-request-id")


def classify_provider_exception(exc: Exception) -> ProviderClassification | None:
    if ContentFilterFinishReasonError and isinstance(exc, ContentFilterFinishReasonError):
        return ProviderClassification(
            code=ExtractionOutcomeCode.CONTENT_BLOCKED,
            request_id=_request_id(exc),
            provider_signal="sdk_content_filter_finish_reason",
            sanitized_reason="Provider content policy blocked the chunk output.",
        )

    if not (APIStatusError and isinstance(exc, APIStatusError)):
        return None

    status = getattr(exc, "status_code", None)
    body = _as_mapping(getattr(exc, "body", None))
    if body is None:
        body = _as_mapping(getattr(exc, "response", None))
    return classify_azure_error_body(status, body, _request_id(exc))


def classify_azure_error_body(
    status_code: int | None, body: Any, request_id: str | None = None
) -> ProviderClassification:
    """Classify a decoded Azure error body using structured evidence only."""
    codes, filtered_paths, shape = _inspect_body(_as_mapping(body) if not isinstance(body, (dict, list)) else body)
    normalized = {_normalize_code(code) for _, code in codes}

    if normalized.intersection(_CONTENT_POLICY_CODES) or filtered_paths:
        signals = [path for path, code in codes if _normalize_code(code) in _CONTENT_POLICY_CODES]
        signals.extend(filtered_paths)
        return ProviderClassification(
            code=ExtractionOutcomeCode.CONTENT_BLOCKED,
            request_id=request_id,
            provider_signal=f"{_CONTENT_POLICY_CODES_VERSION}:" + ",".join(signals),
            sanitized_reason="Azure content policy blocked this chunk.",
            status_code=status_code,
            discovered_codes=tuple(f"{path}={code}" for path, code in codes),
            body_key_shape=tuple(shape),
        )

    if normalized.intersection(_SCHEMA_UNSUPPORTED_CODES):
        return ProviderClassification(
            code=ExtractionOutcomeCode.PROVIDER_SCHEMA_UNSUPPORTED,
            request_id=request_id,
            provider_signal="structured_provider_code",
            sanitized_reason="The deployment does not support the required Structured Outputs schema.",
            status_code=status_code,
            discovered_codes=tuple(f"{path}={code}" for path, code in codes),
            body_key_shape=tuple(shape),
        )

    return ProviderClassification(
        code=ExtractionOutcomeCode.PROVIDER_ERROR_UNCLASSIFIED,
        request_id=request_id,
        provider_signal="unclassified_api_status_error",
        sanitized_reason="Provider returned an unclassified structured error.",
        status_code=status_code,
        discovered_codes=tuple(f"{path}={code}" for path, code in codes),
        body_key_shape=tuple(shape),
    )


def classify_returned_response(response: Any) -> ProviderClassification | None:
    choices: Iterable[Any] = getattr(response, "choices", None) or []
    for choice in choices:
        message = getattr(choice, "message", None)
        refusal = getattr(message, "refusal", None)
        if refusal:
            return ProviderClassification(
                code=ExtractionOutcomeCode.CONTENT_BLOCKED,
                request_id=getattr(response, "_request_id", None),
                provider_signal="message.refusal",
                sanitized_reason="Provider refused to process this chunk.",
            )
        if getattr(choice, "finish_reason", None) == "content_filter":
            return ProviderClassification(
                code=ExtractionOutcomeCode.CONTENT_BLOCKED,
                request_id=getattr(response, "_request_id", None),
                provider_signal="finish_reason.content_filter",
                sanitized_reason="Provider content policy blocked the chunk output.",
            )
    return None
