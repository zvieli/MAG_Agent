import pytest
from unittest.mock import AsyncMock
from src.graph.cosmos_manager import (
    CosmosGremlinManager,
    cosmos_data_source_identity,
    duplicate_lookup_query,
)


def _manager() -> CosmosGremlinManager:
    return object.__new__(CosmosGremlinManager)


def test_cosmos_identity_is_stable_and_credential_free():
    first = cosmos_data_source_identity(
        "wss://Example.gremlin.cosmos.azure.com:443/", "graph-db", "case-graph"
    )
    second = cosmos_data_source_identity(
        "example.gremlin.cosmos.azure.com", "graph-db", "case-graph"
    )

    assert first == second
    assert first["host"] == "example.gremlin.cosmos.azure.com"
    assert first["data_source_id"].startswith("cosmos-")
    assert "key" not in first


def test_duplicate_lookup_query_is_allowlisted_and_projects_match_value():
    query = duplicate_lookup_query("pk")
    assert "requested_doc_id" in query
    assert "matched_value" in query

    with pytest.raises(ValueError, match="Unsupported duplicate match property"):
        duplicate_lookup_query("untrusted-property")


def test_node_sanitizer_compacts_investigative_metadata():
    manager = _manager()
    node = {
        "id": "canonical_person",
        "label": "אדם",
        "type": "PERSON",
        "forensic_summary": "סיכום חקירתי עשיר",
        "description": "duplicate description text",
        "pk": "some-pk",
        "event_date": "2026-01-05",
        "source_file": "case-1",
        "source_location": "Chunk 00001",
        "community": "c_1",
        "metadata": {
            "resolved_aliases_data": [
                {
                    "original_id": "person_1",
                    "original_label": "אדם",
                    "original_forensic_summary": "large nested text",
                    "source_file": "case-1",
                    "source_location": "Chunk 00001; Chunk 00002",
                },
                {
                    "original_id": "person_2",
                    "original_label": "א. אדם",
                    "source_file": "case-1",
                    "source_location": "Chunk 00003",
                },
            ],
            "semantic_class": "person",
        },
    }

    clean = manager._sanitize_for_cosmos(node, is_node=True)

    assert clean["meta_forensic_summary"] == "סיכום חקירתי עשיר"
    assert "description" not in clean
    assert "pk" not in clean
    assert "partitionKey" not in clean
    assert "source_file" not in clean
    assert clean["event_date"] == "2026-01-05"
    assert clean["community"] == "c_1"
    assert clean["source_location"] == "Chunk 00001"
    assert clean["alias_count"] == 2
    assert "person_1" in clean["alias_preview"]
    assert "original_forensic_summary" not in clean["alias_preview"]
    assert clean["meta_semantic_class"] == "person"
    assert "resolved_aliases_data" not in clean
    assert "forensic_summary" not in clean

    node_without_summary = {
        "id": "canonical_person",
        "label": "אדם",
        "type": "PERSON",
        "description": "תיאור בלבד",
    }
    clean_without_summary = manager._sanitize_for_cosmos(node_without_summary, is_node=True)
    assert clean_without_summary["meta_forensic_summary"] == "תיאור בלבד"
    assert "description" not in clean_without_summary


def test_edge_sanitizer_compacts_relation_history_and_source_files():
    manager = _manager()
    edge = {
        "source": "a",
        "target": "b",
        "relation": "RELATED_TO",
        "confidence": "EXTRACTED",
        "confidence_score": 0.95,
        "timestamp": "2026-01-05",
        "weight": 2.0,
        "metadata": {
            "all_relations_history": [
                {
                    "relation": "RELATED_TO",
                    "quality": 0.95,
                    "timestamp": "2026-01-05",
                    "source_file": "case-1",
                    "source_location": "Chunk 00001",
                    "description": "direct evidence",
                },
                {
                    "relation": "PART_OF",
                    "quality": 0.8,
                    "timestamp": "2026-01-06",
                    "source_file": "case-2",
                    "source_location": "Chunk 00002",
                    "description": "supporting evidence",
                },
            ],
            "source_files": ["case-1", "case-2"],
            "is_inferred": False,
        },
    }

    clean = manager._sanitize_for_cosmos(edge, is_node=False)

    assert clean["confidence"] == "EXTRACTED"
    assert clean["confidence_score"] == 0.95
    assert clean["timestamp"] == "2026-01-05"
    assert "weight" not in clean  # Excluded in favor of confidence_score
    assert clean["relation_history_count"] == 2
    assert "direct evidence" in clean["relation_history_preview"]
    assert "PART_OF" in clean["relation_history_preview"]  # Kept because it differed from RELATED_TO
    assert clean["source_files_count"] == 2
    assert "case-1" in clean["source_files_preview"]
    assert clean["meta_is_inferred"] is False
    assert "all_relations_history" not in clean
    assert "source_files" not in clean


def test_edge_sanitizer_omits_single_file_previews_and_dummy_timestamps():
    manager = _manager()
    edge = {
        "source": "x",
        "target": "y",
        "relation": "INVOLVED_IN",
        "confidence_score": 1.0,
        "weight": 1.0,
        "timestamp": "1900-01-01",
        "pk": "case-100",
        "metadata": {
            "all_relations_history": [
                {
                    "relation": "INVOLVED_IN",
                    "quality": 1.0,
                    "timestamp": "1900-01-01",
                    "source_file": "case-100",
                    "description": "sample description",
                }
            ],
            "source_files": ["case-100"],
        },
    }

    clean = manager._sanitize_for_cosmos(edge, is_node=False)

    assert "weight" not in clean
    assert "timestamp" not in clean
    assert "source_files_count" not in clean
    assert "source_files_preview" not in clean
    assert clean["relation_history_count"] == 1
    assert "INVOLVED_IN" not in clean.get("relation_history_preview", "")
    assert "sample description" in clean["relation_history_preview"]


@pytest.mark.asyncio
async def test_sync_graph_connected_component_filtering():
    manager = _manager()
    # Mock settings and upsert methods
    manager.upsert_node = AsyncMock()
    manager.upsert_edge = AsyncMock()
    
    # Component 1 (size 3): node_a, node_b, node_c
    # Component 2 (size 2): node_d, node_e
    # Component 3 (size 1): node_f (isolated)
    graph_data = {
        "metadata": {"doc_id": "sample_page_1"},
        "nodes": [
            {"id": "node_a", "type": "Entity", "pk": "stale-uuid"},
            {"id": "node_b", "type": "Entity"},
            {"id": "node_c", "type": "Entity"},
            {"id": "node_d", "type": "Entity"},
            {"id": "node_e", "type": "Entity"},
            {"id": "node_f", "type": "Entity"},
        ],
        "edges": [
            {"source": "node_a", "target": "node_b", "relation": "rel_1"},
            {"source": "node_b", "target": "node_c", "relation": "rel_2"},
            {"source": "node_d", "target": "node_e", "relation": "rel_3"},
        ]
    }
    
    await manager.sync_graph(graph_data)
    
    # Assert upsert_node was called ONLY for component 1 (node_a, node_b, node_c)
    called_node_ids = {args[0]["id"] for args, kwargs in manager.upsert_node.call_args_list}
    assert called_node_ids == {"node_a", "node_b", "node_c"}
    assert all(
        args[0]["pk"] == "sample_page_1"
        and args[0]["source_file"] == "sample_page_1"
        for args, kwargs in manager.upsert_node.call_args_list
    )
    
    # Assert upsert_edge was called ONLY for component 1 edges (rel_1, rel_2)
    called_edges = {(args[0]["source"], args[0]["target"]) for args, kwargs in manager.upsert_edge.call_args_list}
    assert called_edges == {("node_a", "node_b"), ("node_b", "node_c")}
    assert all(
        args[0]["pk"] == "sample_page_1"
        and args[0]["source_file"] == "sample_page_1"
        for args, kwargs in manager.upsert_edge.call_args_list
    )


@pytest.mark.asyncio
async def test_sync_graph_requires_authoritative_doc_id():
    manager = _manager()
    manager.upsert_node = AsyncMock()
    manager.upsert_edge = AsyncMock()

    with pytest.raises(ValueError, match="metadata doc_id is required"):
        await manager.sync_graph({"nodes": [], "edges": []})
