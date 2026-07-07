import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from src.query_agent.graph_query_builder import GraphQueryBuilder


@pytest.fixture
def mock_schema():
    return {
        "entity_types": {
            "SUSPECT": "...",
            "WITNESS": "...",
            "ORGANIZATION": "...",
            "DEFENDANT": "..."
        },
        "relations": {
            "INVOLVED_IN": "...",
            "COMMUNICATED_WITH": "..."
        }
    }


@pytest.mark.asyncio
async def test_query_translation_positive_path(mock_schema):
    """
    Test Query Translation (Positive Path):
    Input: 'Who are the suspects in this case?'
    Ensure generated Gremlin query has partitionKey, canonical label, and limit(50).
    """
    mock_client = MagicMock()
    mock_chat = AsyncMock()
    mock_choices = MagicMock()
    mock_message = MagicMock()
    
    mock_message.content = "g.V().has('partitionKey', 'test_doc_id').has('type', 'SUSPECT').limit(50)"
    mock_choices.message = mock_message
    mock_chat.create.return_value = MagicMock(choices=[mock_choices])
    mock_client.chat.completions = mock_chat

    with patch("src.query_agent.graph_query_builder._build_azure_client", return_value=mock_client):
        builder = GraphQueryBuilder(mock_schema, doc_id="test_doc_id")
        query = await builder.build_gremlin_query("Who are the suspects in this case?", safety_limit=50)

        assert "test_doc_id" in query
        assert "has('partitionKey', 'test_doc_id')" in query
        assert "has('type', 'SUSPECT')" in query
        assert "חשוד" not in query
        assert ".limit(50)" in query
        assert query == "g.V().has('partitionKey', 'test_doc_id').has('type', 'SUSPECT').limit(50)"


@pytest.mark.asyncio
async def test_partition_enforcement_security(mock_schema):
    """
    Test Partition Enforcement (Security):
    Input: Any arbitrary query.
    Validate that the builder always injects the partitionKey filter, even if the LLM fails to include it.
    """
    mock_client = MagicMock()
    mock_chat = AsyncMock()
    mock_choices = MagicMock()
    mock_message = MagicMock()
    
    # LLM output missing the partitionKey check and limit
    mock_message.content = "g.V().has('type', 'ORGANIZATION')"
    mock_choices.message = mock_message
    mock_chat.create.return_value = MagicMock(choices=[mock_choices])
    mock_client.chat.completions = mock_chat

    with patch("src.query_agent.graph_query_builder._build_azure_client", return_value=mock_client):
        builder = GraphQueryBuilder(mock_schema, doc_id="test_doc_id")
        query = await builder.build_gremlin_query("Any arbitrary query", safety_limit=50)

        # Enforce that the builder injected the partitionKey and limit steps
        assert "test_doc_id" in query
        assert "g.V().has('partitionKey', 'test_doc_id')" in query
        assert ".limit(50)" in query


def test_empty_null_handling(mock_schema):
    """
    Test Empty/Null Handling:
    Input: A query about entities that do not exist (resulting in empty list from database).
    Verify that the builder returns the specific structural signal [QUERY_RESULT_EMPTY].
    """
    builder = GraphQueryBuilder(mock_schema, doc_id="test_doc_id")
    # Format empty raw results
    formatted = builder.format_results([])
    assert formatted == "[QUERY_RESULT_EMPTY]"


@pytest.mark.asyncio
async def test_canonical_mapping(mock_schema):
    """
    Test Canonical Mapping:
    Input: A query in Hebrew using colloquial terms (e.g., 'מי הנאשמים?').
    Verify that the builder maps these terms to the English canonical labels (e.g., DEFENDANT)
    before finalizing the Gremlin query.
    """
    mock_client = MagicMock()
    mock_chat = AsyncMock()
    mock_choices = MagicMock()
    mock_message = MagicMock()
    
    # Simulating LLM correctly translating colloquial Hebrew term to DEFENDANT
    mock_message.content = "g.V().has('partitionKey', 'test_doc_id').has('type', 'DEFENDANT').limit(50)"
    mock_choices.message = mock_message
    mock_chat.create.return_value = MagicMock(choices=[mock_choices])
    mock_client.chat.completions = mock_chat

    with patch("src.query_agent.graph_query_builder._build_azure_client", return_value=mock_client):
        builder = GraphQueryBuilder(mock_schema, doc_id="test_doc_id")
        query = await builder.build_gremlin_query("מי הנאשמים?", safety_limit=50)

        assert "DEFENDANT" in query
        assert "הנאשמים" not in query
        assert "נאשם" not in query
        assert "test_doc_id" in query


@pytest.mark.asyncio
async def test_query_agent_auto_evidence_anchoring(mock_schema):
    """
    Verify that query_knowledge_graph_tool automatically triggers Phase 2 evidence anchoring
    by parsing returned nodes/edges and invoking fetch_raw_evidence.
    """
    mock_cosmos_mgr = MagicMock()
    # Cosmos DB returns a vertex with a source_location property
    mock_cosmos_mgr.execute_query = AsyncMock(return_value=[
        {
            "id": "meeting_1",
            "label": "EVENT",
            "type": "vertex",
            "properties": {
                "source_location": [{"value": "Chunk 00042"}],
                "description": [{"value": "פגישה סודית"}]
            }
        }
    ])
    
    # Mock CloudKnowledgeProvider
    from src.processing.cloud_knowledge_provider import CloudKnowledgeProvider
    kp = CloudKnowledgeProvider(search_mgr=None, cosmos_mgr=mock_cosmos_mgr, doc_id="test_doc_id")
    
    from src.query_agent.query_agent import QueryAgent, QueryKnowledgeGraphParams
    agent = QueryAgent(knowledge_provider=kp, doc_id="test_doc_id")
    
    # Mock GraphQueryBuilder to bypass LLM query generation
    mock_builder = MagicMock()
    mock_builder.build_gremlin_query = AsyncMock(return_value="g.V().hasLabel('EVENT')")
    mock_builder.format_results = MagicMock(return_value="Node [meeting_1] | Type: EVENT")
    
    # Mock search_original_document_tool to return custom verbatim evidence text
    agent.search_original_document_tool = AsyncMock(return_value="VERBATIM: Netanyahu met Elovitch in Jerusalem.")
    
    with patch("src.query_agent.query_agent.GraphQueryBuilder", return_value=mock_builder):
        params = QueryKnowledgeGraphParams(question="When did they meet?")
        result = await agent.query_knowledge_graph_tool(params, None)
        
        # Verify that search_original_document_tool was called programmatically for Chunk 00042
        agent.search_original_document_tool.assert_called_once()
        call_arg = agent.search_original_document_tool.call_args[0][0]
        assert call_arg.query == "test_doc_id Chunk 00042"
        
        # Verify result contains the formatted query results and the verbatim evidence block
        assert "Node [meeting_1]" in result
        assert "AUTOMATICALLY ANCHORED VERBATIM EVIDENCE CHUNKS" in result
        assert "VERBATIM: Netanyahu met Elovitch in Jerusalem." in result


def test_format_results_rich_metadata(mock_schema):
    """
    Test that format_results correctly outputs enriched node and edge properties.
    """
    builder = GraphQueryBuilder(mock_schema, doc_id="test_doc_id")
    
    # Mock node from Cosmos with enriched properties
    raw_node = {
        "id": "canonical_suspect_inas_ahlawi",
        "label": "אינאס אחלאוי",
        "type": "vertex",
        "properties": {
            "type": [{"value": "SUSPECT"}],
            "alias_count": [{"value": 3}],
            "alias_preview": [{"value": "[{\"id\":\"suspect_1\"}]"}],
            "community": [{"value": "c_1"}],
            "source_location": [{"value": "Chunk 00001"}],
            "source_file": [{"value": "case-1"}]
        }
    }
    
    # Mock edge from Cosmos with enriched properties
    raw_edge = {
        "id": "edge_123",
        "label": "OTHER_RELATION",
        "type": "edge",
        "outV": "investigator_bar_cohen",
        "inV": "canonical_suspect_inas_ahlawi",
        "properties": {
            "description": "bar cohen arrested inas",
            "confidence": "EXTRACTED",
            "relation_history_count": 1,
            "relation_history_preview": "[{\"relation\":\"SEIZED_FROM\"}]",
            "source_files_count": 1,
            "source_files_preview": "[\"case-1\"]",
            "source_location": "Chunk 00001"
        }
    }
    
    formatted = builder.format_results([raw_node, raw_edge])
    
    # Node checks
    assert "Node [canonical_suspect_inas_ahlawi]" in formatted
    assert "Type: SUSPECT" in formatted
    assert "Alias Count: 3" in formatted
    assert "Alias Preview: [{\"id\":\"suspect_1\"}]" in formatted
    assert "Community: c_1" in formatted
    assert "Source Location: Chunk 00001" in formatted
    assert "Source File: case-1" in formatted
    
    # Edge checks
    assert "Relation: [investigator_bar_cohen] --[OTHER_RELATION]--> [canonical_suspect_inas_ahlawi]" in formatted
    assert "Details: bar cohen arrested inas" in formatted
    assert "Confidence: EXTRACTED" in formatted
    assert "Relation History Count: 1" in formatted
    assert "Relation History Preview: [{\"relation\":\"SEIZED_FROM\"}]" in formatted
    assert "Source Files Count: 1" in formatted
    assert "Source Files Preview: [\"case-1\"]" in formatted
    assert "Source Location: Chunk 00001" in formatted



