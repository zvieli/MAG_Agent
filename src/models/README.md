# Models Module

`src/models/` defines Pydantic DTOs for graph structures, extraction schemas, investigation agent state, and runtime ontology configuration models.

## Responsibilities

- Validate graph nodes, edges, claims, and evidence fragments.
- Load and parse `schemas/ontology_schema.json` into runtime Pydantic configuration (`SCHEMA_CONFIG`).
- Define state containers for the LangGraph query agent (`InvestigationState`, `VerifiedFact`, `PendingFactApproval`).
- Provide strong typing for pipeline activities, HTTP payloads, and test fixtures.

## Components

- `models.py`: Graph entity DTOs (`Node`, `Edge`, `Claim`, `EvidenceFragment`, `RawGraphExtraction`) and schema configuration instance (`SCHEMA_CONFIG`).
- `base.py`: Pydantic models for ontology configuration loading (`SchemaConfigModel`, `EntityTypesConfig`, `RelationTypesConfig`, `ProtocolCompletenessConfig`, `GraphAnalysisConfig`).

## Query Agent State DTOs (`src/query_agent/state.py`)

- `InvestigationState`: Main Pydantic state container carrying `doc_id`, `current_task`, `investigation_plan`, `verified_facts`, `pending_fact_approval`, `messages`, `remaining_steps`, and transient search results.
- `VerifiedFact`: Admitted fact entry in the facts ledger (`fact`, `source_id`, `doc_id`, `confidence`, `audit_trace`).
- `PendingFactApproval`: Proposed fact awaiting user approval in HITL mode.
- `ChatMessage` / `AssistantMessage`: Structured chat history items.

## Ontology Configuration (`schemas/ontology_schema.json`)

The ontology schema is loaded into `SCHEMA_CONFIG` and serves as the runtime source of truth for:
- Allowed entity types and relation definitions.
- Legal role mappings and confidence levels.
- Extraction behavioral guidelines and TZR guidelines tracking.
- Protocol completeness checklist items (`protocol_completeness`).
- Graph analysis and community summary configuration flags.

## Maintainer Notes

- Legal rules, entity types, and protocol checklists belong in `schemas/ontology_schema.json`, not hardcoded in Python model definitions.
- All persisted graph items must validate `id`, `label`, `source_file` (`doc_id`), and `source_location`.
