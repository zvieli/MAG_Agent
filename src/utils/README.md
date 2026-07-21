# Utilities Module

`src/utils/` contains cross-cutting helpers used by ingestion, reporting, querying, and operations.

## Current Components

- `config.py`: Pydantic settings for Azure OpenAI, Azure Search, Cosmos DB, Storage, Document Intelligence, and runtime flags.
- `config_manager.py`: schema/config loading helpers.
- `json_repair.py`: defensive JSON repair for malformed or truncated LLM output.
- `model_helpers.py`: normalization helpers used by Pydantic models and extraction post-processing.
- `bidi_utils.py`: Hebrew/right-to-left text utilities for readable output.
- `observability.py`: structured `[OBS]` event/metric logging, trace ID extraction, secret redaction, and field truncation.

## Observability Helpers

Use `log_event` for structured operational events:

```python
log_event("query.completed", trace_id=trace_id, doc_id=doc_id, duration_ms=elapsed_ms)
```

Use `log_metric` for count or latency measurements:

```python
log_metric("query_duration_ms", elapsed_ms, unit="ms", doc_id=doc_id)
```

Trace IDs should be read from `x-mag-trace-id` with `trace_id_from_request`. Values are normalized and generated with a prefix when absent.

## Maintainer Notes

- Do not log secrets, keys, tokens, SAS URLs, or full connection strings.
- Keep helper behavior deterministic; this package is used in tests and Durable activities.
- Prefer adding shared parsing/normalization here only when it is reused across modules.
