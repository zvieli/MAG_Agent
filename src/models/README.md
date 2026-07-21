# Models Module

`src/models/` defines the Pydantic models used by extraction, graph merge, reporting, and ontology configuration loading.

## Responsibilities

- Validate extracted nodes and edges before merge/indexing.
- Load and expose `schemas/ontology_schema.json` as runtime configuration.
- Normalize confidence labels, entity/relation identifiers, and provenance fields.
- Keep DTOs stable across pipeline activities, tests, and reporting.

## Current Components

- `base.py`: ontology/config models such as schema config, thresholds, analysis configuration, and runtime constants.
- `models.py`: graph DTOs such as `Node`, `Edge`, and extraction containers.

## Ontology Configuration

The ontology schema is the shared source of truth for:

- entity types and semantic classes.
- relation mappings and confidence levels.
- extraction guidance and deterministic patterns.
- entity resolution thresholds.
- graph analysis settings.
- `protocol_completeness`, the curated checklist used by the missing-evidence analyzer.

Do not duplicate protocol-completeness or ontology rules in code unless they are pure execution mechanics. Legal/product rules should live in `schemas/ontology_schema.json`.

## Validation Expectations

Every persisted graph item should carry:

- stable ID.
- typed label/type.
- source file or case reference.
- source location when available.
- confidence or evidence strength where applicable.
- metadata needed for reports and auditability.
