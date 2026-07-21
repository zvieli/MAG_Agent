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
