# Interfaces Module

`src/interfaces/` defines Python `Protocol` abstractions for decoupling pipeline stages, storage engines, retrieval providers, and reporters from concrete Azure implementations.

## Defined Protocols

- `KnowledgeProvider`: Contract for searching document chunks (`search_chunks_raw`), graph entities (`search_entities`), connections (`find_connections`), and entity context (`get_entity_context`).
- `ExtractionStrategy`: Async chunk extraction contract (`extract_from_chunk`).
- `AnalysisStrategy`: Graph clustering and topology analysis contract.
- `PersistenceProvider`: Storage persistence contract (`read_bytes`, `write_bytes`, `exists`, `delete`, `delete_folder`).
- `EntityResolver`: Node and edge deduplication contract (`resolve`).
- `StructuralInferrer`: Inferred relationship generation contract.
- `VectorIndexer`: Semantic chunk indexing contract (`upload_documents`).
- `Reporter`: Case report and visualization generator contract (`generate`).

## Why Protocol Interfaces Matter

These protocols allow unit tests, local test runners, and mock providers to be plugged into pipeline orchestration without changing code in `src/processing/` or `src/query_agent/`.

Concrete implementations are provided in:
- `src/processing/cloud_knowledge_provider.py` \(\rightarrow\) `CloudKnowledgeProvider`
- `src/storage/blob_storage.py` \(\rightarrow\) `AzureBlobStorage`
- `src/storage/search_manager.py` \(\rightarrow\) `SearchManager`
- `src/graph/cosmos_manager.py` \(\rightarrow\) `CosmosGremlinManager`
- `src/processing/strategies.py` \(\rightarrow\) `DefaultEntityResolver`, `DefaultReporter`

## Maintainer Notes

- Keep protocols small and behavior-focused.
- Avoid introducing cloud-vendor-specific types directly into protocol signature definitions.
- If a protocol method signature is updated, update all concrete implementations and mock fixtures in the same commit.
