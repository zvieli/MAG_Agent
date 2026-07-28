# Graph Module

`src/graph/` handles graph normalization, entity resolution, NetworkX topology analysis, and Cosmos DB Gremlin persistence.

## Responsibilities

- Normalize raw extracted node and edge identifiers into canonical graph symbols.
- Perform duplicate entity detection and edge re-mapping (`DefaultEntityResolver`).
- Assign topological community IDs to nodes prior to database synchronization.
- Sync final graph data to Cosmos DB Gremlin with strict per-case partition keys (`doc_id`/`pk`).
- Build NetworkX graph structures for local reporting, community clustering, and visualization.

## Components

- `cosmos_manager.py`: `CosmosGremlinManager` for async Gremlin traversals, parameterized query execution, vertex/edge upserts, and graph synchronization.
- `entity_resolver.py`: Entity resolution and duplicate merging based on configurable string distance and semantic label matching.
- `graph_manager.py`: In-memory graph loading, graph merging, and JSON export utilities.
- `local_graphify.py`: NetworkX graph construction (`build_from_json`) and community detection (`cluster`).
- `normalizer.py`: Text, ID, and Hebrew character normalization routines (`GraphNormalizer`).
- `ontology_enricher.py`: Structural relationship enrichment based on schema ontology definitions.

## Partitioning & Data Isolation Rule

Cosmos DB Gremlin graph access MUST remain strictly partitioned by investigation case:
- Every vertex and edge stored in Cosmos DB carries a partition key property `pk = doc_id`.
- All Gremlin queries and traversals must include `.has('pk', doc_id)` or pass `pk` parameters (e.g. `g.V().has('pk', pk)...`).
- Unpartitioned global scans are strictly prohibited in user-facing query agent paths.

## Pipeline Integration & Community Pre-Detection

During `activity_final_indexing`:
1. `local_graphify.cluster(G)` runs NetworkX community detection on the merged graph.
2. The detected `community` ID is mapped onto every node in `graph_data["nodes"]`.
3. `cosmos_mgr.sync_graph(graph_data)` syncs vertices and edges (including `community` properties) to Cosmos DB.
4. Final graph JSON is persisted to `graph/{doc_id}/final.json`.

## Maintainer Notes

- Keep graph node and edge metadata additive; downstream reporting and auditability rely on source references (`source_file`, `source_location`).
- Ensure all Gremlin queries use parameters (`bindings`) to prevent injection risks and enable query plan caching.
