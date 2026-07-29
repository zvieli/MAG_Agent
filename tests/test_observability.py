import json
import logging
from unittest.mock import MagicMock

import pytest

from src.utils.observability import (
    TRACE_HEADER,
    log_event,
    normalize_trace_id,
    sanitize_observability_value,
    trace_id_from_request,
)


def test_sanitize_observability_value_redacts_sensitive_fields_and_truncates():
    payload = {
        "doc_id": "case_1",
        "api_key": "secret-value",
        "nested": {"Authorization": "Bearer secret"},
        "long": "x" * 1300,
    }

    sanitized = sanitize_observability_value(payload)

    assert sanitized["doc_id"] == "case_1"
    assert sanitized["api_key"] == "[redacted]"
    assert sanitized["nested"]["Authorization"] == "[redacted]"
    assert "truncated" in sanitized["long"]


def test_sanitize_observability_value_preserves_token_count_metrics():
    payload = {
        "tokens_in": 150,
        "tokens_out": 45,
        "total_tokens": 195,
        "prompt_tokens": 150,
        "completion_tokens": 45,
        "max_tokens": 1000,
        "token": "secret-auth-token",
        "access_token": "secret-access-token",
    }

    sanitized = sanitize_observability_value(payload)

    assert sanitized["tokens_in"] == 150
    assert sanitized["tokens_out"] == 45
    assert sanitized["total_tokens"] == 195
    assert sanitized["prompt_tokens"] == 150
    assert sanitized["completion_tokens"] == 45
    assert sanitized["max_tokens"] == 1000
    assert sanitized["token"] == "[redacted]"
    assert sanitized["access_token"] == "[redacted]"


def test_trace_id_from_request_uses_header_and_normalizes():
    req = MagicMock()
    req.headers = {TRACE_HEADER: "query abc/123"}

    assert trace_id_from_request(req, prefix="query") == "query-abc-123"


def test_normalize_trace_id_generates_prefixed_fallback():
    trace_id = normalize_trace_id("", prefix="pipeline")

    assert trace_id.startswith("pipeline-")


def test_log_event_emits_redacted_json(caplog):
    caplog.set_level(logging.INFO)

    log_event("test.event", trace_id="trace-1", token="secret")

    assert caplog.records
    message = caplog.records[-1].getMessage()
    assert message.startswith("[OBS] ")
    payload = json.loads(message.replace("[OBS] ", "", 1))
    assert payload["event"] == "test.event"
    assert payload["trace_id"] == "trace-1"
    assert payload["token"] == "[redacted]"


def test_pipeline_status_payload_includes_trace_id():
    from function_app import _pipeline_status_payload

    status = _pipeline_status_payload(
        phase="extracting",
        message="Extracting",
        blob_path="uploads/case.pdf",
        doc_id="case",
        trace_id="pipeline-123",
        total_chunks=10,
        completed_chunks=4,
        current_batch=2,
        last_error=None,
    )

    assert status["trace_id"] == "pipeline-123"
    assert status["phase"] == "extracting"
    assert status["completed_chunks"] == 4


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("uploads/case.pdf", {"blob_path": "uploads/case.pdf", "doc_id": "case", "trace_id": ""}),
        (
            {"blob_path": "uploads/case.pdf", "doc_id": "case-x", "trace_id": "trace-x"},
            {"blob_path": "uploads/case.pdf", "doc_id": "case-x", "trace_id": "trace-x"},
        ),
    ],
)
def test_pipeline_input_payload_supports_legacy_and_structured_inputs(value, expected):
    from function_app import _pipeline_input_payload

    assert _pipeline_input_payload(value) == expected


def test_ingestion_summary_event_accumulation(caplog):
    from src.utils.observability import calculate_estimated_cost, log_event

    caplog.set_level(logging.INFO, logger="src.utils.observability")

    extraction_results = [
        {"token_usage": {"prompt_tokens": 1000, "completion_tokens": 200}},
        {"token_usage": {"prompt_tokens": 1500, "completion_tokens": 300}},
    ]

    total_prompt_tokens = sum(r["token_usage"]["prompt_tokens"] for r in extraction_results)
    total_completion_tokens = sum(r["token_usage"]["completion_tokens"] for r in extraction_results)
    total_tokens = total_prompt_tokens + total_completion_tokens
    total_cost_usd = calculate_estimated_cost(total_prompt_tokens, total_completion_tokens, model_name="gpt-4o")

    log_event(
        "pipeline.ingestion.summary",
        doc_id="case_summary_test",
        total_prompt_tokens=total_prompt_tokens,
        total_completion_tokens=total_completion_tokens,
        total_tokens=total_tokens,
        total_cost_usd=total_cost_usd,
        chunks_processed_count=len(extraction_results),
    )

    assert caplog.records
    message = caplog.records[-1].getMessage()
    payload = json.loads(message.replace("[OBS] ", "", 1))
    assert payload["event"] == "pipeline.ingestion.summary"
    assert payload["doc_id"] == "case_summary_test"
    assert payload["total_prompt_tokens"] == 2500
    assert payload["total_completion_tokens"] == 500
    assert payload["total_tokens"] == 3000
    assert payload["total_cost_usd"] > 0
    assert payload["chunks_processed_count"] == 2
