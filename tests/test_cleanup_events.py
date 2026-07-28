import pytest
import json
from unittest.mock import MagicMock, patch, AsyncMock
from src.processing import orchestration_activities as activities


async def immediate_to_thread(func, *args, **kwargs):
    return func(*args, **kwargs)


@pytest.mark.asyncio
async def test_activity_extract_chunk_in_memory():
    # Setup
    chunk_data = {
        "text": "sample text",
        "doc_id": "test_doc",
        "chunk_index": 0
    }
    
    mock_store = MagicMock()
    
    # Mock extract_from_chunk to return a dummy extraction
    mock_extraction = MagicMock()
    mock_extraction.model_dump.return_value = {"nodes": [], "edges": []}
    
    with patch("src.processing.orchestration_activities._get_artifact_store", return_value=mock_store), \
         patch("src.processing.orchestration_activities.extract_from_chunk", new_callable=AsyncMock) as mock_extract:
        
        mock_extract.return_value = (mock_extraction, {"tokens": 10})
        
        # Act
        result_payload = await activities.activity_extract_chunk(chunk_data)
        
        # Assert
        assert result_payload["doc_id"] == "test_doc"
        assert result_payload["chunk_id"] == "00000"
        assert result_payload["extraction"] == {"nodes": [], "edges": []}
        assert result_payload["token_usage"] == {"tokens": 10}
        
        # Verify no file operations were performed for chunk extraction
        mock_store.write_bytes.assert_not_called()
        mock_store.delete.assert_not_called()


@pytest.mark.asyncio
async def test_activity_merge_partials_saves_to_storage():
    # Setup
    extraction_results = [
        {
            "doc_id": "test_doc",
            "chunk_id": "00000",
            "extraction": {"nodes": [], "edges": []},
            "token_usage": {"tokens": 10}
        }
    ]
    
    mock_store = MagicMock()
    
    # Mock the resolver instance and its resolve method
    mock_resolver_instance = AsyncMock()
    mock_resolver_instance.resolve.return_value = ([], [], "report")
    
    with patch("src.processing.orchestration_activities._get_artifact_store", return_value=mock_store), \
         patch("src.processing.orchestration_activities.asyncio.to_thread", new=immediate_to_thread), \
         patch("src.processing.orchestration_activities.DefaultEntityResolver", return_value=mock_resolver_instance):
        
        # Act
        merged_path = await activities.activity_merge_partials(extraction_results)
        
        # Assert
        assert merged_path == "graph/test_doc/partial.json"
        
        # Verify the merged graph was written to storage
        mock_store.write_bytes.assert_called_once()


@pytest.mark.asyncio
async def test_activity_final_indexing_cleans_up_merged_path():
    # Setup
    mock_store = MagicMock()
    mock_store.read_bytes.return_value = json.dumps({
        "metadata": {"doc_id": "test_doc"},
        "nodes": [
            {"id": "a", "label": "א", "description": "תיאור א"},
            {"id": "b", "label": "ב", "description": "תיאור ב"},
        ],
        "edges": [{"source": "a", "target": "b", "relation": "RELATED_TO"}]
    }).encode("utf-8")
    mock_store.exists.return_value = False
    
    merged_graph_path = "graph/test_doc/partial.json"
    
    mock_cosmos_mgr = MagicMock()
    mock_cosmos_mgr.sync_graph = AsyncMock()
    
    mock_reporter = MagicMock()
    mock_reporter.generate = AsyncMock()

    with patch("src.processing.orchestration_activities._get_artifact_store", return_value=mock_store), \
         patch("src.processing.orchestration_activities.get_cosmos_manager", return_value=mock_cosmos_mgr), \
         patch("src.processing.orchestration_activities.asyncio.to_thread", new=immediate_to_thread), \
         patch("src.processing.orchestration_activities._index_nodes", new_callable=AsyncMock), \
         patch("src.processing.orchestration_activities._index_edge_labels", new_callable=AsyncMock), \
         patch("src.processing.orchestration_activities.AzureBlobStorage", return_value=MagicMock()), \
         patch("src.processing.orchestration_activities.PersistenceManager", return_value=MagicMock()), \
         patch("src.processing.orchestration_activities.DefaultReporter", return_value=mock_reporter):
        
        # Act
        final_path = await activities.activity_final_indexing(merged_graph_path)
        
        # Assert
        assert final_path == "cosmos://test_doc"
        # Verify the temporary partial graph was deleted
        mock_store.delete.assert_called_with(merged_graph_path)
        # Verify delete_folder calls preserve extracted/ folder
        deleted_folders = [call.args[0] for call in mock_store.delete_folder.call_args_list]
        assert "chunks/test_doc" in deleted_folders
        assert "uploads" in deleted_folders
        assert "extracted/test_doc" not in deleted_folders
