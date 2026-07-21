# Graph Module

`src/graph/` owns graph normalization, entity resolution, graph analysis helpers, and Cosmos DB Gremlin persistence.

## Responsibilities

- Normalize extracted node and edge labels into stable graph identifiers.
- Resolve duplicate entities and remap edges before final indexing.
- Preserve provenance, aliases, confidence, source file, and source location metadata.
- Sync final graph data to Cosmos DB Gremlin with per-case partitioning.
- Provide local graph utilities for NetworkX-based reporting and visualization.

## Current Components

- `cosmos_manager.py`: async Gremlin manager for Cosmos DB operations, traversal execution, and graph sync.
- `entity_resolver.py`: duplicate detection and merge logic for extracted entities.
- `graph_manager.py`: in-memory graph loading, merge coordination, and JSON export helpers.
- `local_graphify.py`: NetworkX graph construction, community detection, and visualization helpers.
- `normalizer.py`: ID, label, and Hebrew text normalization utilities.
- `ontology_enricher.py`: ontology-oriented enrichment and structural relationship helpers.

## Partitioning Rule

Cosmos graph access must stay scoped to the active investigation case. The active case ID is carried as `doc_id` in the pipeline and as `pk` on graph records/traversals.

Do not add graph reads or writes that scan globally unless the caller explicitly requests global diagnostics.

## Inputs And Outputs

Typical input:

- merged extraction JSON with `nodes`, `edges`, and resolution metadata.

Typical outputs:

- final graph JSON artifacts in Blob/Azurite storage.
- Cosmos vertices and edges for the same `doc_id`/`pk`.
- NetworkX graph objects consumed by reporting.

## Notes For Maintainers

- Keep graph metadata additive; downstream reports, protocol completeness, and frontend graph views depend on source references.
- Prefer deterministic normalization before LLM-based disambiguation.
- Keep Gremlin queries parameterized and partition scoped.
