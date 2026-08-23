import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import ValidationError

from src.extraction.provider_adapter import (
    ExtractionOutcomeCode,
    ProviderClassification,
    ProviderOutcomeError,
    classify_azure_error_body,
    classify_provider_exception,
    classify_returned_response,
)
from src.extraction.wire_schema import (
    ExtractionWireModel,
    create_wire_extraction_model,
    lint_structured_output_schema,
)
from src.models.models import RawGraphExtraction, SCHEMA_CONFIG
from src.processing import orchestration_activities as activities


def test_normal_refusal_is_content_blocked():
    response = SimpleNamespace(
        _request_id="req-refusal",
        choices=[SimpleNamespace(message=SimpleNamespace(refusal="no", parsed=None), finish_reason="stop")],
    )
    outcome = classify_returned_response(response)
    assert outcome is not None
    assert outcome.code is ExtractionOutcomeCode.CONTENT_BLOCKED
    assert outcome.request_id == "req-refusal"


def test_sdk_content_filter_finish_reason_is_content_blocked():
    from openai import ContentFilterFinishReasonError

    outcome = classify_provider_exception(ContentFilterFinishReasonError())
    assert outcome is not None
    assert outcome.code is ExtractionOutcomeCode.CONTENT_BLOCKED
    assert outcome.provider_signal == "sdk_content_filter_finish_reason"


def test_nested_azure_responsible_ai_body_is_content_blocked():
    body = {
        "error": {
            "code": "BadRequest",
            "innererror": {
                "code": "ResponsibleAIPolicyViolation",
                "details": [{"code": "content_filter"}],
            },
        }
    }
    outcome = classify_azure_error_body(400, body, "req-azure")
    assert outcome.code is ExtractionOutcomeCode.CONTENT_BLOCKED
    assert outcome.request_id == "req-azure"
    assert any("innererror.code" in item for item in outcome.discovered_codes)


def test_filtered_policy_result_is_content_blocked():
    body = {
        "prompt_filter_results": [
            {"content_filter_results": {"violence": {"filtered": True, "severity": "high"}}}
        ]
    }
    assert classify_azure_error_body(422, body).code is ExtractionOutcomeCode.CONTENT_BLOCKED


def test_invalid_schema_400_is_not_misclassified_as_filter():
    body = {
        "error": {
            "code": "invalid_request_error",
            "innererror": {"code": "invalid_json_schema"},
            "message": "The response format schema is invalid",
        }
    }
    outcome = classify_azure_error_body(400, body)
    assert outcome.code is ExtractionOutcomeCode.PROVIDER_ERROR_UNCLASSIFIED


def test_vague_text_and_status_do_not_classify_as_filter():
    body = {"error": {"code": "BadRequest", "message": "content filtering may apply"}}
    outcome = classify_azure_error_body(422, body)
    assert outcome.code is ExtractionOutcomeCode.PROVIDER_ERROR_UNCLASSIFIED


def test_wire_model_rejects_dangling_edge_duplicate_and_self_loop():
    valid_node = {
        "id": "a",
        "label": "A",
        "type": "SUSPECT",
        "forensic_summary": "summary",
        "event_date": None,
    }
    valid_edge = {
        "source": "a",
        "target": "missing",
        "relation": "BRIBERY",
        "description": "description",
        "confidence": "DIRECT",
        "confidence_score": 0.8,
    }
    with pytest.raises(ValidationError, match="absent node"):
        ExtractionWireModel.model_validate({"nodes": [valid_node], "edges": [valid_edge]})

    with pytest.raises(ValidationError, match="duplicate node"):
        ExtractionWireModel.model_validate({"nodes": [valid_node, valid_node], "edges": []})

    self_loop = {**valid_edge, "target": "a"}
    with pytest.raises(ValidationError, match="self-loop"):
        ExtractionWireModel.model_validate({"nodes": [valid_node], "edges": [self_loop]})


def test_ontology_wire_schema_passes_preflight_and_uses_enums():
    wire_model = create_wire_extraction_model(SCHEMA_CONFIG)
    schema = wire_model.model_json_schema()
    counts = lint_structured_output_schema(schema)
    assert counts["enum_values"] > 0
    assert schema["additionalProperties"] is False


@pytest.mark.asyncio
async def test_content_blocked_activity_returns_sanitized_union_without_text():
    chunk = {
        "text": "sensitive source text",
        "doc_id": "case-1",
        "chunk_index": 2,
        "token_count": 25,
        "page_range": [4, 5],
    }
    classification = ProviderClassification(
        code=ExtractionOutcomeCode.CONTENT_BLOCKED,
        request_id="req-1",
        provider_signal="message.refusal",
        sanitized_reason="Provider refused to process this chunk.",
    )
    store = MagicMock()
    with patch.object(activities, "_get_artifact_store", return_value=store), patch.object(
        activities,
        "extract_from_chunk",
        new=AsyncMock(side_effect=ProviderOutcomeError(classification)),
    ):
        result = await activities.activity_extract_chunk(chunk)

    assert result["outcome"] == "content_blocked"
    assert result["page_range"] == [4, 5]
    assert result["token_count"] == 25
    assert "text" not in result
    assert "extraction" not in result
    assert "sensitive" not in json.dumps(result)
    store.write_bytes.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "outcome_code",
    [
        ExtractionOutcomeCode.OUTPUT_LOGIC_INVALID,
        ExtractionOutcomeCode.OUTPUT_SCHEMA_INVALID,
    ],
)
async def test_extraction_failed_activity_returns_graceful_payload(outcome_code):
    chunk = {
        "text": "bad extraction source text",
        "doc_id": "case-1",
        "chunk_index": 3,
        "token_count": 42,
        "page_range": [6, 7],
    }
    classification = ProviderClassification(
        code=outcome_code,
        request_id="req-err",
        provider_signal="wire_validation_error",
        sanitized_reason="Extraction validation failed.",
    )
    store = MagicMock()
    with patch.object(activities, "_get_artifact_store", return_value=store), patch.object(
        activities,
        "extract_from_chunk",
        new=AsyncMock(side_effect=ProviderOutcomeError(classification)),
    ), patch.object(activities, "log_event") as mock_log_event, patch.object(
        activities, "log_metric"
    ) as mock_log_metric:
        result = await activities.activity_extract_chunk(chunk)

    assert result["outcome"] == "extraction_failed"
    assert result["doc_id"] == "case-1"
    assert result["chunk_id"] == "00003"
    assert result["page_range"] == [6, 7]
    assert result["token_count"] == 42
    assert result["request_id"] == "req-err"
    assert result["provider_signal"] == "wire_validation_error"
    assert result["reason"] == "Extraction validation failed."
    assert "text" not in result
    assert "extraction" not in result
    mock_log_event.assert_called_once()
    assert mock_log_event.call_args[0][0] == "pipeline.extraction.chunk_failed"
    mock_log_metric.assert_called_once_with(
        "pipeline_failed_chunks_count", 1, doc_id="case-1"
    )


@pytest.mark.asyncio
async def test_activity_extract_chunk_reraises_infrastructure_errors():
    chunk = {
        "text": "infra failure text",
        "doc_id": "case-1",
        "chunk_index": 1,
    }
    classification = ProviderClassification(
        code=ExtractionOutcomeCode.PROVIDER_ERROR_UNCLASSIFIED,
        request_id="req-auth",
        provider_signal="invalid_api_key",
        sanitized_reason="Authentication failed.",
    )
    store = MagicMock()
    with patch.object(activities, "_get_artifact_store", return_value=store), patch.object(
        activities,
        "extract_from_chunk",
        new=AsyncMock(side_effect=ProviderOutcomeError(classification)),
    ):
        with pytest.raises(RuntimeError, match="PROVIDER_ERROR_UNCLASSIFIED"):
            await activities.activity_extract_chunk(chunk)


@pytest.mark.asyncio
async def test_partial_merge_is_token_weighted_and_persists_manifest():
    writes = {}
    store = MagicMock()
    store.write_bytes.side_effect = lambda path, data: writes.__setitem__(path, data)
    store.read_bytes.return_value = json.dumps(
        {"chunk_index": 1, "label": "Page 1", "text": "successful text"}
    ).encode("utf-8")
    results = [
        {
            "outcome": "success",
            "doc_id": "case-1",
            "chunk_id": "00001",
            "page_range": [1],
            "token_count": 75,
            "source_label": "Page 1",
            "extraction": {
                "nodes": [{"id": "a", "label": "A", "type": "SUSPECT"}],
                "edges": [],
            },
        },
        {
            "outcome": "content_blocked",
            "doc_id": "case-1",
            "chunk_id": "00002",
            "page_range": [2, 3],
            "token_count": 25,
            "request_id": "req-2",
            "provider_signal": "azure-content-policy-v1:$.error.code",
            "reason": "Azure content policy blocked this chunk.",
        },
    ]
    resolver = AsyncMock()
    resolver.resolve.return_value = ([{"id": "a"}], [], {})

    async def immediate(func, *args, **kwargs):
        return func(*args, **kwargs)

    with patch.object(activities, "_get_artifact_store", return_value=store), patch.object(
        activities.asyncio, "to_thread", new=immediate
    ), patch.object(activities, "DefaultEntityResolver", return_value=resolver), patch.object(
        activities, "_index_chunks", new=AsyncMock()
    ), patch.object(activities, "_delete_indexed_chunks", new=AsyncMock()):
        result = await activities.activity_merge_partials(
            {"results": results, "doc_id": "case-1", "trace_id": "trace-1"}
        )

    assert result["status"] == "completed_partial"
    assert result["coverage_ratio"] == pytest.approx(0.75)
    assert result["blocked_page_ranges"] == [[2, 3]]
    manifest = json.loads(writes[result["manifest_path"]].decode("utf-8"))
    graph = json.loads(writes[result["merged_graph_path"]].decode("utf-8"))
    assert manifest["review_required"] is True
    assert graph["metadata"]["coverage_ratio"] == pytest.approx(0.75)


@pytest.mark.asyncio
async def test_all_blocked_publishes_manifest_but_no_graph():
    writes = {}
    store = MagicMock()
    store.write_bytes.side_effect = lambda path, data: writes.__setitem__(path, data)

    async def immediate(func, *args, **kwargs):
        return func(*args, **kwargs)

    blocked = {
        "outcome": "content_blocked",
        "doc_id": "case-2",
        "chunk_id": "00001",
        "page_range": [1],
        "token_count": 10,
        "reason": "blocked",
    }
    with patch.object(activities, "_get_artifact_store", return_value=store), patch.object(
        activities.asyncio, "to_thread", new=immediate
    ), patch.object(
        activities, "_delete_indexed_chunks", new=AsyncMock()
    ):
        result = await activities.activity_merge_partials(
            {"results": [blocked], "doc_id": "case-2", "trace_id": "trace-2"}
        )

    assert result["status"] == "content_blocked"
    assert result["merged_graph_path"] is None
    assert result["manifest_path"] in writes
    assert not any(path.endswith("partial.json") for path in writes)


def test_raw_graph_cache_contract_rejects_dangling_edge():
    with pytest.raises(ValidationError, match="dangling edge"):
        RawGraphExtraction.model_validate(
            {
                "nodes": [{"id": "a"}],
                "edges": [{"source": "a", "target": "missing", "relation": "BRIBERY"}],
            }
        )


@pytest.mark.asyncio
async def test_partial_manifest_bypasses_completed_duplicate_detection():
    import function_app

    manifest = {
        "status": "completed_partial",
        "is_partial": True,
        "coverage_ratio": 0.8,
    }
    store = MagicMock()
    store.exists.return_value = True
    store.read_bytes.return_value = json.dumps(manifest).encode("utf-8")
    cosmos = MagicMock()
    cosmos.execute_query = AsyncMock(return_value=[{"id": "existing"}])

    with patch.object(function_app, "get_artifact_store", return_value=store), patch.object(
        function_app, "get_cosmos_manager", return_value=cosmos
    ):
        result = await function_app._case_duplicate_preflight("case-partial.pdf")

    assert result["status"] == "new"
    assert result["reprocess_partial"] is True
    assert result["live_query"] is False
    cosmos.execute_query.assert_not_awaited()


@pytest.mark.asyncio
async def test_duplicate_preflight_always_requeries_cosmos():
    import function_app

    store = MagicMock()
    store.exists.return_value = False
    vertex = {
        "id": "document-1",
        "label": "DOCUMENT",
        "properties": {"label": ["Document 1"], "type": ["DOCUMENT"]},
        "matched_value": "case-1",
    }
    cosmos = MagicMock()
    cosmos.data_source_identity = {"data_source_id": "cosmos-test"}
    cosmos.execute_query = AsyncMock(side_effect=[[vertex], [], [], []])

    with patch.object(function_app, "get_artifact_store", return_value=store), patch.object(
        function_app, "get_cosmos_manager", return_value=cosmos
    ):
        first = await function_app._case_duplicate_preflight("case-1.pdf")
        second = await function_app._case_duplicate_preflight("case-1.pdf")

    assert first["status"] == "duplicate"
    assert first["matched_by"] == "pk"
    assert first["data_source_id"] == "cosmos-test"
    assert second["status"] == "new"
    assert second["live_query"] is True
    assert cosmos.execute_query.await_count == 4
    assert all(
        call.args[1] == {"requested_doc_id": "case-1"}
        for call in cosmos.execute_query.await_args_list
    )


@pytest.mark.asyncio
async def test_duplicate_preflight_rejects_mismatched_lookup_value():
    import function_app

    store = MagicMock()
    store.exists.return_value = False
    unrelated = {
        "id": "unrelated",
        "label": "DOCUMENT",
        "properties": {"type": ["DOCUMENT"]},
        "matched_value": "different-case",
    }
    cosmos = MagicMock()
    cosmos.data_source_identity = {"data_source_id": "cosmos-test"}
    cosmos.execute_query = AsyncMock(side_effect=[[unrelated], [], []])

    with patch.object(function_app, "get_artifact_store", return_value=store), patch.object(
        function_app, "get_cosmos_manager", return_value=cosmos
    ):
        result = await function_app._case_duplicate_preflight("case-1.pdf")

    assert result["status"] == "new"
    assert result["matched_by"] == "none"
    assert result["warning"] == "lookup_value_mismatch:pk"


def test_preflight_response_disables_http_caching():
    import function_app

    response = function_app._preflight_json_response({"status": "new"})

    assert response.headers["Cache-Control"] == "no-store, max-age=0"
    assert response.headers["Pragma"] == "no-cache"
