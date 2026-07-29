import pytest
from unittest.mock import patch, AsyncMock, MagicMock
from src.utils.config import settings
from src.query_agent.state import InvestigationState
from src.query_agent.nodes import synthesis_node, finalizer_node


@pytest.mark.asyncio
async def test_get_prompt_formats_groundedness():
    # Test template variable replacement in config.py
    prompt = await settings.get_prompt("agent_system_prompt")
    assert "{groundedness_instruction}" not in prompt
    assert "CITATION" in prompt


@pytest.mark.asyncio
async def test_synthesis_and_finalizer_include_groundedness_instructions():
    # Setup test state and config
    state = InvestigationState(
        doc_id="doc_123",
        transient_doc_results="CHUNK 1: test info",
        verified_facts=[]
    )
    config = {"metadata": {"trace_id": "t1"}}

    # Mock call_llm inside src.query_agent.nodes
    with patch("src.query_agent.nodes.call_llm", new_callable=AsyncMock) as mock_llm:
        mock_llm.return_value = "Synthesized answer"
        await synthesis_node(state, config)
        assert mock_llm.called
        sys_prompt = mock_llm.call_args[0][0]
        assert "CITATION" in sys_prompt

    with patch("src.query_agent.nodes.call_llm", new_callable=AsyncMock) as mock_llm:
        mock_llm.return_value = "Final report"
        await finalizer_node(state, config)
        assert mock_llm.called
        sys_prompt = mock_llm.call_args[0][0]
        assert "CITATION" not in sys_prompt and "CITATION RULES" not in sys_prompt


@pytest.mark.asyncio
async def test_doc_branch_node_strips_quotes():
    from src.query_agent.nodes import doc_branch_node
    state = InvestigationState(
        doc_id="doc_123",
        current_task="Find case info"
    )
    config = {"metadata": {"trace_id": "t1"}}
    
    with patch("src.query_agent.nodes.call_llm", new_callable=AsyncMock) as mock_llm, \
         patch("src.query_agent.nodes.CloudKnowledgeProvider") as mock_kp:
        mock_llm.return_value = '"search query with quotes"'
        
        mock_provider_instance = MagicMock()
        mock_provider_instance.search_chunks_raw = AsyncMock(return_value=[])
        mock_kp.return_value = mock_provider_instance
        
        await doc_branch_node(state, config)
        
        mock_provider_instance.search_chunks_raw.assert_called_once()
        called_query = mock_provider_instance.search_chunks_raw.call_args[0][0]
        assert called_query == "search query with quotes"


@pytest.mark.asyncio
async def test_graph_branch_node_resolves_entity_labels():
    from src.query_agent.nodes import graph_branch_node
    state = InvestigationState(
        doc_id="doc_123",
        current_task="Find context"
    )
    config = {"metadata": {"trace_id": "t1"}}
    
    with patch("src.query_agent.nodes.call_llm", new_callable=AsyncMock) as mock_llm, \
         patch("src.query_agent.nodes.CloudKnowledgeProvider") as mock_kp:
        mock_llm.return_value = '{"action": "timeline", "entity_a": "אינאס אחלאוי"}'
        
        mock_provider_instance = MagicMock()
        mock_provider_instance.search_entities = AsyncMock(return_value=[
            {"id": "canonical_suspect_inas_akhlawi", "label": "אינאס מוחמד יוסף אחלאוי"}
        ])
        mock_provider_instance.get_entity_context = AsyncMock(return_value={"relationships": []})
        mock_kp.return_value = mock_provider_instance
        
        await graph_branch_node(state, config)
        
        mock_provider_instance.search_entities.assert_called_once_with("אינאס אחלאוי", top_k=3)
        mock_provider_instance.get_entity_context.assert_called_once_with("canonical_suspect_inas_akhlawi")


@pytest.mark.asyncio
async def test_doc_branch_node_parses_json_query():
    from src.query_agent.nodes import doc_branch_node
    state = InvestigationState(
        doc_id="doc_123",
        current_task="Find case info"
    )
    config = {"metadata": {"trace_id": "t1"}}
    
    with patch("src.query_agent.nodes.call_llm", new_callable=AsyncMock) as mock_llm, \
         patch("src.query_agent.nodes.CloudKnowledgeProvider") as mock_kp:
        mock_llm.return_value = '{"semantic_query": "semantic text", "keyword_query": "keyword text"}'
        
        mock_provider_instance = MagicMock()
        mock_provider_instance.search_chunks_raw = AsyncMock(return_value=[])
        mock_kp.return_value = mock_provider_instance
        
        await doc_branch_node(state, config)
        
        mock_provider_instance.search_chunks_raw.assert_called_once_with(
            "semantic text", top_k=6, keyword_query="keyword text"
        )


@pytest.mark.asyncio
async def test_build_prompt_loading_and_structure_printing():
    """
    Test prompt loading structure for graph extraction in _build_prompt,
    printing the complete formatted prompt to stdout and asserting static prefix vs dynamic suffix ordering.
    """
    from src.extraction.extraction_engine import _build_prompt
    from src.models.models import SCHEMA_CONFIG

    sample_chunk_text = "בנימין נתניהו נערכה פגישה עם שאול אלוביץ' בתיק 4000."
    deterministic_entities = [{"label": "בנימין נתניהו", "type": "PERSON"}]
    chunk_label = "Chunk 00001"

    prompt = await _build_prompt(
        sample_chunk_text,
        SCHEMA_CONFIG,
        chunk_label=chunk_label,
        deterministic_entities=deterministic_entities,
    )

    # Print full structured prompt to stdout for visibility
    print("\n" + "=" * 80)
    print("FULL STRUCTURED EXTRACTION PROMPT:")
    print("=" * 80)
    print(prompt)
    print("=" * 80 + "\n")

    # Assert static prefix vs dynamic suffix structure ordering for Azure OpenAI Prompt Caching
    entity_idx = prompt.find("### ENTITY TYPES DEFINITIONS")
    relation_idx = prompt.find("### RELATION TYPES DEFINITIONS")
    confidence_idx = prompt.find("### CONFIDENCE LEVELS DEFINITIONS")
    deterministic_idx = prompt.find("PRE-EXTRACTED ENTITIES")
    context_idx = prompt.find(f"CONTEXT: CHUNK WINDOW: {chunk_label}")
    text_idx = prompt.find(f"TEXT:\n{sample_chunk_text}")

    assert entity_idx != -1, "Missing ### ENTITY TYPES DEFINITIONS in prompt"
    assert relation_idx != -1, "Missing ### RELATION TYPES DEFINITIONS in prompt"
    assert confidence_idx != -1, "Missing ### CONFIDENCE LEVELS DEFINITIONS in prompt"
    assert deterministic_idx != -1, "Missing PRE-EXTRACTED ENTITIES in prompt"
    assert context_idx != -1, "Missing CONTEXT block in prompt"
    assert text_idx != -1, "Missing TEXT block in prompt"

    # Verify Static Prefix precedes Dynamic Suffix (deterministic findings, chunk context, chunk text)
    assert entity_idx < relation_idx < confidence_idx < deterministic_idx < context_idx < text_idx, (
        "Prompt structure violated: Static ontology definitions must precede dynamic per-chunk elements"
    )
