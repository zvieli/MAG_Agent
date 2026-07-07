import pytest
from unittest.mock import AsyncMock, MagicMock
from src.processing.cloud_knowledge_provider import CloudKnowledgeProvider
from src.query_agent.query_agent import QueryAgent, MapQuidProQuoParams, FindConnectionsParams
from src.graph.cosmos_manager import CosmosGremlinManager

@pytest.mark.asyncio
async def test_search_entities_scoring():
    # Mock CosmosGremlinManager
    mock_cosmos_mgr = MagicMock()
    # Mock execute_query return value
    # Let's return some vertices with type and description properties
    mock_cosmos_mgr.execute_query = AsyncMock(return_value=[
        {
            "id": "1",
            "label": "בנימין נתניהו",
            "properties": {
                "type": [{"value": "חשוד"}],
                "description": [{"value": "ראש הממשלה לשעבר, נאשם בפרשת 4000"}],
            }
        },
        {
            "id": "2",
            "label": "חברת בזק",
            "properties": {
                "type": [{"value": "חברה"}],
                "description": [{"value": "חברת תקשורת גדולה"}],
            }
        }
    ])
    
    kp = CloudKnowledgeProvider(search_mgr=None, cosmos_mgr=mock_cosmos_mgr, doc_id="global")
    
    # Term "החשודים" should match type "חשוד" (type matching +4)
    # Term "פרשת" should match description of entity 1 (description matching +2)
    # We query for "החשודים בפרשת" to ensure Entity 1 is top ranked over Entity 2
    results = await kp.search_entities("החשודים בפרשת")
    
    # Assert entity 1 is returned and scored higher due to type and description match
    assert len(results) > 0
    # Entity 1 ("בנימין נתניהו") should be the top result
    assert results[0]["id"] == "1"
    # Check that type and description are extracted properly
    assert results[0]["type"] == "חשוד"
    assert "ראש הממשלה" in results[0]["description"]


@pytest.mark.asyncio
async def test_map_quid_pro_quo_fallback():
    # Instantiate real CloudKnowledgeProvider and patch the required methods
    kp = CloudKnowledgeProvider(search_mgr=None, cosmos_mgr=None, doc_id="global")
    kp.get_quid_pro_quo_chains = AsyncMock(return_value=[])
    kp.search_edges = AsyncMock(return_value="RELATION: A --[בתמורה ל]--> B")
    
    agent = QueryAgent(knowledge_provider=kp, doc_id="global")
    
    result = await agent.map_quid_pro_quo_tool(MapQuidProQuoParams(), None)
    
    # Assert search_edges fallback was called
    kp.search_edges.assert_called_once_with("בתמורה ל", top_k=20)
    assert result == "RELATION: A --[בתמורה ל]--> B"


@pytest.mark.asyncio
async def test_stop_words_filtering():
    mock_cosmos_mgr = MagicMock()
    mock_cosmos_mgr.execute_query = AsyncMock(return_value=[])
    
    kp = CloudKnowledgeProvider(search_mgr=None, cosmos_mgr=mock_cosmos_mgr, doc_id="global")
    
    # Only stop words - should extract empty terms and return [] immediately without query execution
    results = await kp.search_entities("מי הם של את")
    assert results == []
    mock_cosmos_mgr.execute_query.assert_not_called()


@pytest.mark.asyncio
async def test_hebrew_word_boundary_matching():
    mock_cosmos_mgr = MagicMock()
    mock_cosmos_mgr.execute_query = AsyncMock(return_value=[
        {
            "id": "1",
            "label": "בנימין נתניהו",
            "properties": {}
        }
    ])
    
    kp = CloudKnowledgeProvider(search_mgr=None, cosmos_mgr=mock_cosmos_mgr, doc_id="global")
    
    # "מי" is a stop word and will be filtered out. Let's try searching for "ימין".
    # Since "ימין" is a substring of "בנימין" but doesn't match word boundaries, it should not get the word boundary +10 score.
    # Score should only be 2.0 (for substring match on label)
    results = await kp.search_entities("ימין")
    assert len(results) == 1
    assert results[0]["score"] == 2.0

    # Search for "נתניהו" which matches word boundaries in "בנימין נתניהו"
    # Should get +10.0 for boundary match, and it's not exact match, so score is 10.0.
    results = await kp.search_entities("נתניהו")
    assert len(results) == 1
    assert results[0]["score"] == 10.0


@pytest.mark.asyncio
async def test_get_key_actors_projection():
    # Instantiate CosmosGremlinManager bypassing __init__
    mgr = object.__new__(CosmosGremlinManager)
    mgr.execute_query = AsyncMock(return_value=[])
    
    await CosmosGremlinManager.get_key_actors(mgr, limit=5, partition_key="test_partition")
    
    # Verify projected fields in query
    args, kwargs = mgr.execute_query.call_args
    query_str = args[0]
    assert "'type'" in query_str
    assert "'description'" in query_str
    assert "coalesce(values('type')" in query_str


@pytest.mark.asyncio
async def test_get_entity_context_neighbor_type():
    mock_cosmos_mgr = MagicMock()
    # get_node and get_neighbors return value
    mock_cosmos_mgr.get_node = AsyncMock(return_value={"id": "node_a", "label": "A"})
    mock_cosmos_mgr.get_neighbors = AsyncMock(return_value=[
        {
            "edge": {"label": "RELATED_TO"},
            "neighbor": {
                "id": "node_b",
                "label": "B",
                "properties": {
                    "type": [{"value": "COMPANY"}]
                }
            }
        }
    ])
    
    kp = CloudKnowledgeProvider(search_mgr=None, cosmos_mgr=mock_cosmos_mgr, doc_id="global")
    context = await kp.get_entity_context("node_a")
    
    # Assert neighbor type was extracted and appended
    relationships = context["relationships"]
    assert len(relationships) == 1
    assert relationships[0]["target_type"] == "COMPANY"


@pytest.mark.asyncio
async def test_find_connections_hop_expansion():
    kp = CloudKnowledgeProvider(search_mgr=None, cosmos_mgr=None, doc_id="global")
    # First search returns empty list, second search returns path
    kp.find_connections = AsyncMock(side_effect=[[], [["node_a", "node_b"]]])
    
    agent = QueryAgent(knowledge_provider=kp, doc_id="global")
    params = FindConnectionsParams(entity_a="node_a", entity_b="node_b", max_hops=3)
    
    result = await agent.find_connections_tool(params, None)
    
    # Assert find_connections was called twice (first with 3 hops, then with 4 hops)
    assert kp.find_connections.call_count == 2
    kp.find_connections.assert_any_call("node_a", "node_b", max_hops=3)
    kp.find_connections.assert_any_call("node_a", "node_b", max_hops=4)
    assert "node_a" in result


@pytest.mark.asyncio
async def test_get_analysis_metadata_isolation():
    from unittest.mock import patch
    mock_store = MagicMock()
    
    # Mock exists behavior
    mock_store.exists = MagicMock(side_effect=lambda path: path == "graph/case_123/community_summaries.json")
    mock_store.read_bytes = MagicMock(return_value=b'{"summaries": {"1": "Summary 1"}, "hierarchy": {}}')
    
    with patch("src.storage.factory.get_artifact_store", return_value=mock_store):
        # 1. Test case-specific isolation
        kp = CloudKnowledgeProvider(search_mgr=None, cosmos_mgr=None, doc_id="case_123")
        meta = kp.get_analysis_metadata()
        
        mock_store.exists.assert_called_once_with("graph/case_123/community_summaries.json")
        assert meta["community_summaries"] == {"1": "Summary 1"}
        
        # 2. Test global/default path checks
        mock_store.exists.reset_mock()
        kp_global = CloudKnowledgeProvider(search_mgr=None, cosmos_mgr=None, doc_id="global")
        kp_global.get_analysis_metadata()
        
        mock_store.exists.assert_any_call("community_summaries.json")


@pytest.mark.asyncio
async def test_arabic_pages_skipped_from_di_ocr():
    from src.processing.document_processor import _load_document_pages_via_di
    from unittest.mock import patch, MagicMock, AsyncMock
    
    # Mock settings.azure_document_intelligence_endpoint and key
    with patch("src.processing.document_processor.settings") as mock_settings:
        mock_settings.azure_document_intelligence_endpoint = "http://endpoint"
        mock_settings.azure_document_intelligence_key = "key"
        mock_settings.document_intelligence_timeout_seconds = 30.0
        
        # Mock PdfReader
        mock_reader = MagicMock()
        # Page 1: Arabic text (should skip OCR)
        page_1 = MagicMock()
        page_1.extract_text.return_value = "هذا نص باللغة العربية"
        # Page 2: Hebrew text (should be OCR'ed)
        page_2 = MagicMock()
        page_2.extract_text.return_value = "זהו טקסט בעברית"
        
        mock_reader.pages = [page_1, page_2]
        
        with patch("src.processing.document_processor.PdfReader", return_value=mock_reader), \
             patch("src.processing.document_processor.PdfWriter") as mock_pdf_writer_cls:
            
            mock_writer = MagicMock()
            mock_pdf_writer_cls.return_value = mock_writer
            
            mock_client = AsyncMock()
            mock_poller = AsyncMock()
            mock_result = MagicMock()
            
            mock_page_ocr = MagicMock()
            mock_page_ocr.lines = [MagicMock(content="זהו טקסט בעברית")]
            mock_result.pages = [mock_page_ocr]
            
            mock_poller.result.return_value = mock_result
            mock_client.begin_analyze_document.return_value = mock_poller
            
            async_client_context = AsyncMock()
            async_client_context.__aenter__.return_value = mock_client
            
            with patch("src.processing.document_processor.DocumentIntelligenceClient", return_value=async_client_context):
                pages = await _load_document_pages_via_di(b"mock_pdf_bytes")
                
                # Check results
                assert len(pages) == 2
                assert pages[0]["page_number"] == 1
                assert pages[0]["text"] == ""
                
                assert pages[1]["page_number"] == 2
                assert pages[1]["text"] == "זהו טקסט בעברית"
                
                # Check that PdfWriter was called only for page_2
                mock_writer.add_page.assert_called_once_with(page_2)


@pytest.mark.asyncio
async def test_arabic_mixed_pages_post_processed():
    from src.processing.document_processor import _load_document_pages_via_di
    from unittest.mock import patch, MagicMock, AsyncMock
    
    with patch("src.processing.document_processor.settings") as mock_settings:
        mock_settings.azure_document_intelligence_endpoint = "http://endpoint"
        mock_settings.azure_document_intelligence_key = "key"
        mock_settings.document_intelligence_timeout_seconds = 30.0
        
        mock_reader = MagicMock()
        # Page 1: Mixed Hebrew and Arabic text (should NOT skip OCR because it has Hebrew)
        page_1 = MagicMock()
        page_1.extract_text.return_value = "זהו מסמך\nזהו משפט בערבית سلام\nסוף דבר"
        
        mock_reader.pages = [page_1]
        
        with patch("src.processing.document_processor.PdfReader", return_value=mock_reader), \
             patch("src.processing.document_processor.PdfWriter") as mock_pdf_writer_cls:
            
            mock_writer = MagicMock()
            mock_pdf_writer_cls.return_value = mock_writer
            
            mock_client = AsyncMock()
            mock_poller = AsyncMock()
            mock_result = MagicMock()
            
            mock_page_ocr = MagicMock()
            mock_page_ocr.lines = [
                MagicMock(content="זהו מסמך"),
                MagicMock(content="זהו משפט בערבית سلام"),
                MagicMock(content="סוף דבר"),
            ]
            mock_result.pages = [mock_page_ocr]
            
            mock_poller.result.return_value = mock_result
            mock_client.begin_analyze_document.return_value = mock_poller
            
            async_client_context = AsyncMock()
            async_client_context.__aenter__.return_value = mock_client
            
            with patch("src.processing.document_processor.DocumentIntelligenceClient", return_value=async_client_context):
                pages = await _load_document_pages_via_di(b"mock_pdf_bytes")
                
                assert len(pages) == 1
                assert pages[0]["page_number"] == 1
                assert pages[0]["text"] == "זהו מסמך\nסוף דבר"
                
                mock_writer.add_page.assert_called_once_with(page_1)


