# Interfaces Module

`src/interfaces/` defines Python `Protocol` contracts for decoupling pipeline stages from concrete implementations.

## Current Protocols

- `KnowledgeProvider`: graph/analysis/document access abstraction.
- `ExtractionStrategy`: async chunk-to-graph extraction contract.
- `AnalysisStrategy`: NetworkX graph analysis contract.
- `PersistenceProvider`: artifact persistence abstraction.
- `EntityResolver`: node/edge deduplication contract.
- `StructuralInferrer`: inferred relationship contract.
- `VectorIndexer`: semantic indexing contract.
- `Reporter`: Markdown/report generation contract.

## Why This Exists

These protocols let tests and local workflows substitute implementations without rewriting pipeline orchestration. Concrete implementations currently live mostly under:

- `src/processing/`
- `src/graph/`
- `src/reporting/`
- `src/storage/`

## Maintainer Notes

- Keep protocols small and behavior-oriented.
- Do not add Azure-specific types to protocol signatures unless the abstraction intentionally becomes Azure-only.
- If a protocol changes, update implementations and tests in the same change.
