# Investigative Graph Management Module (`src/graph/`)

This module manages the lifecycle of the **Knowledge Graph**, including entity resolution, multi-tier relationship preservation, legal ontology enrichment, and direct synchronization with **Azure Cosmos DB (Gremlin API)**.

---

## 🌌 Gremlin Graph Database Sync & Isolation

The system uses **Azure Cosmos DB** configured with the Gremlin API. All interactions with the database are routed through the `CosmosGremlinManager`.

> [!IMPORTANT]
> **Partition Key Isolation**: To ensure complete privacy and isolation between different files or cases, all nodes and edges are stored using the document ID (`doc_id`) as the partition key (`partitionKey`). Every Gremlin traversal (e.g., `g.V().has('partitionKey', doc_id)...`) must explicitly filter by this partition key context.

---

## ⚙️ Core Technical Pillars

### 1. Unified Graph Loader (`graph_manager.py`)
- Acts as the central ingestion endpoint for merging graph lists.
- Provides utility functions to export the in-memory NetworkX schema to standard JSON formats.

### 2. Multi-Pass Entity Resolution (`entity_resolver.py`)
To merge duplicates and alias occurrences of the same actor across a document, the entity resolver follows a structured process:
- **Blocking**: Candidates are grouped by surname, common patterns, or topological closeness to avoid quadratic scale computation.
- **String Similarity**: Evaluates label edit distances, handling Hebrew-specific variations (e.g. prefix `ה` or structural name parts).
- **Structural Similarity**: Computes **Jaccard Similarity** over the neighboring nodes in the graph to assess if two nodes share similar connection contexts.
- **Consolidation**: Alias nodes are merged into a single canonical node. All merged aliases are recorded inside `metadata.aliases` to maintain full provenance.

### 3. Edge Deduplication & Confidence Retention
- **Confidence Selection**: When duplicate relationships are detected for a pair of entities, the relationship with the highest confidence level is kept (`DIRECT_EVIDENCE` > `CIRCUMSTANTIAL` > `AMBIGUOUS`).
- **Provenance Retention**: Lower-confidence historical instances are not deleted; they are archived inside the edge's `metadata.all_relations_history` attribute.
- **Centrality Weights**: Edges are assigned weights based on connection frequency and confidence level, serving as inputs for downstream pathfinding metrics.

### 4. Ontology & Legal Enrichment (`ontology_enricher.py`)
- Enhances graph nodes with legal ontology definitions (e.g. mapping actors/actions to Offenses, Laws, or Document Types).
- Applies reasoning rules to flag potential conflicts of interest or suspicious transaction chains.

---

## 📂 Key Components

- **`cosmos_manager.py`**: Handles database initialization, connection string base64 adjustments, raw Gremlin script execution using the Python Gremlin client, and transactional database cleanups.
- **`entity_resolver.py`**: Implementation of the blocking, Jaccard comparison, and node consolidation logic.
- **`graph_manager.py`**: Central repository loader that coordinates resolving, clustering, and saving.
- **`normalizer.py`**: Sanitizes names, removes Hebrew-specific grammatical noise, and aligns node classes.
- **`ontology_enricher.py`**: Applies legal ontology rules to enrich the graph nodes and check structural relations.
