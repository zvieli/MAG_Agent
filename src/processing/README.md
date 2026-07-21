# Processing Module

`src/processing/` contains the ingestion pipeline activities and cloud retrieval/indexing adapters used by the Function App.

The Durable orchestrator itself is defined in `function_app.py`; the activity implementations live here.

## Responsibilities

- Parse PDFs with Azure AI Document Intelligence.
- Normalize Hebrew/right-to-left text and split documents into chunks.
- Generate embeddings and index chunks in Azure AI Search.
- Run chunk-level graph extraction.
- Merge partial graph outputs and run entity resolution.
- Generate final graph/report/protocol-completeness artifacts.
- Sync graph records to Cosmos DB Gremlin.
- Provide cloud-backed graph/source retrieval for the query agent.

## Current Components

- `document_processor.py`: PDF/Markdown parsing, DI integration, Hebrew cleanup, chunk generation.
- `orchestration_activities.py`: Durable activity implementations and pipeline observability events.
- `vector_indexer.py`: embedding generation and Azure AI Search indexing.
- `cloud_knowledge_provider.py`: Cosmos/Search-backed retrieval methods for the query agent.
- `persistence_manager.py`: artifact persistence coordination.
- `strategies.py`: strategy wrappers used by pipeline orchestration.

## Pipeline Flow

```mermaid
sequenceDiagram
    participant HTTP as POST /api/pipelines
    participant O as pipeline_orchestrator
    participant A as orchestration_activities.py
    participant S as Blob/Azurite
    participant Search as Azure AI Search
    participant C as Cosmos Gremlin

    HTTP->>S: write uploaded PDF
    HTTP->>O: start with blob_path, doc_id, trace_id
    O->>A: activity_preflight_check
    O->>A: activity_chunking
    A->>S: write raw text and chunks
    A->>Search: index chunk embeddings
    O->>A: activity_extract_chunk per chunk
    A->>S: write partial extraction artifacts
    O->>A: activity_merge_partials
    A->>S: write merged graph artifact
    O->>A: activity_final_indexing
    A->>C: sync final graph
    A->>S: write reports and protocol completeness
```

## Observability

Activities emit structured `[OBS]` logs and metrics for phase completion, failures, durations, protocol completeness, and LLM cost-control decisions. The trace ID is passed from the HTTP request into Durable input and custom status.

## Maintainer Notes

- Activity inputs/outputs should remain JSON-serializable.
- Keep artifact paths explicit; Durable history should not contain large payloads.
- Keep cleanup scoped to generated intermediate artifacts only.
- Preserve `doc_id`/`trace_id` through every activity boundary.
