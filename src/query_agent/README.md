# Query Agent Module

`src/query_agent/` implements the investigative reasoning layer used by `POST /api/query`.

The query workload is Durable-orchestrated in `function_app.py`; the activity creates a stateless `QueryAgent`, answers against the active case, and returns the answer plus updated state.

## Responsibilities

- Answer Hebrew investigation questions from graph data and source chunks.
- Keep all retrieval scoped to the requested `doc_id`.
- Combine graph traversals, Azure AI Search chunk retrieval, ontology checks, and synthesis.
- Return JSON-serializable state so the frontend can continue a conversation.
- Emit query observability through the Function App trace ID.

## Current Components

- `query_agent.py`: outer agent runtime, tool definitions, and stateless answer API.
- `nodes.py`: graph/state-machine node functions used by the query flow.
- `state.py`: Pydantic state models for messages, verified facts, pending approvals, and transient retrieval data.
- `ontology_engine.py`: ontology and structural rule checks over graph data.
- `graph_query_builder.py`: helper for building graph queries in controlled cases.

## Runtime Flow

1. `POST /api/query` receives `{ "doc_id": "...", "question": "...", "state": ... }`.
2. The HTTP route reads or creates `x-mag-trace-id` and starts `query_orchestrator`.
3. The orchestrator sets custom status with `phase`, `doc_id`, `trace_id`, and `last_error`.
4. `activity_query_investigation` creates `QueryAgent(doc_id=..., stateless=True)`.
5. The agent retrieves graph/source evidence and returns `{ "answer": "...", "state": ..., "trace_id": "..." }`.

## Observability

Expected structured events:

- `query.started`
- `query.orchestrator_started`
- `query.completed`
- `query.failed`
- metric events: `query_success_count`, `query_duration_ms`, `query_failure_count`

Trace local logs with:

```bash
docker compose logs --tail=1000 function-app | rg "<query-trace-id>|query\\."
```

## Maintainer Notes

- Do not bypass `doc_id`; global graph queries are unsafe for normal user-facing answers.
- Keep large retrieval details in transient state fields and prune them before returning to the client.
- Keep final answers grounded in retrieved source text or graph facts.
