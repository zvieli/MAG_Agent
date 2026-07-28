# Utilities Module

`src/utils/` contains cross-cutting helpers for settings management, dynamic prompt loading, JSON repair, Hebrew text normalization, and structured observability logging.

## Components

- `config.py`: Pydantic settings loading environment variables for Azure OpenAI, Azure AI Search, Azure AI Document Intelligence, Cosmos DB Gremlin, Azure Storage, and runtime execution flags (`settings`).
- `config_manager.py`: Async file configuration and system prompt loader (`config_manager`) reading templates from `prompts/`.
- `json_repair.py`: Defensive JSON extraction and node repair (`_extract_json_payload`, `_repair_json_nodes`) for malformed or truncated LLM completions.
- `model_helpers.py`: Model string sanitization and normalization utilities.
- `bidi_utils.py`: Right-To-Left (RTL) and Hebrew text display formatting (`fix_text`).
- `observability.py`: Structured `[OBS]` event and metric logging (`log_event`, `log_metric`), trace ID extraction (`trace_id_from_request`), secret redaction, and parameter truncation.

## Dynamic Prompt System (`config_manager.py` & `settings.get_prompt`)

System prompts and template instructions are externalized in `prompts/` and loaded dynamically:

```python
# Loading system prompts from prompts/
raw_template = await settings.get_prompt("extraction_prompt_full")
```

`settings.get_prompt` performs template substitution (e.g. replacing `{groundedness_instruction}` or `{legal_role_map}`) and caches loaded prompt templates in memory.

## Structured Observability (`observability.py`)

All Function App HTTP endpoints, Durable Functions activities, and LangGraph nodes emit JSON logs prefixed with `[OBS]`:

```python
log_event(
    "query.langgraph.route.selected",
    trace_id=trace_id,
    doc_id=doc_id,
    node="coordinator",
    route="parallel",
)
```

Metric events log numeric measurements:

```python
log_metric("query_duration_ms", elapsed_ms, unit="ms", doc_id=doc_id)
```

Trace IDs are extracted from the `x-mag-trace-id` request header using `trace_id_from_request`.

## Maintainer Notes

- **Secrets Safety**: `observability.py` automatically redacts secrets and connection strings. Do not print raw settings objects containing API keys.
- **Side Effects**: Keep utility helpers pure and deterministic so they can be safely invoked within Durable activities and test fixtures.
