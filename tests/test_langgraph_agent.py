import pytest
from src.query_agent.state import InvestigationState, VerifiedFact, PendingFactApproval
from src.query_agent.nodes import (
    hydrate_node,
    process_hitl_node,
    entry_router,
    route_coordinator,
    route_extraction,
    synthesis_node,
    extraction_node,
    finalizer_node
)

def test_pydantic_serialization():
    # 1. Test valid serialization
    state = InvestigationState(
        doc_id="case_123",
        investigation_plan=["task1", "task2"],
        verified_facts=[
            VerifiedFact(
                fact="הנאשם קיבל שוחד",
                source_id="chunk_0",
                doc_id="case_123",
                confidence=1.0,
                audit_trace={"task": "test"}
            )
        ]
    )
    
    state_dict = state.model_dump()
    assert state_dict["doc_id"] == "case_123"
    assert len(state_dict["verified_facts"]) == 1
    assert state_dict["verified_facts"][0]["fact"] == "הנאשם קיבל שוחד"

    # 2. Test deserialization
    hydrated = InvestigationState(**state_dict)
    assert hydrated.doc_id == "case_123"
    assert len(hydrated.verified_facts) == 1
    assert isinstance(hydrated.verified_facts[0], VerifiedFact)


@pytest.mark.asyncio
async def test_hydrate_node_security_checks(monkeypatch):
    # Test valid doc_id
    state = InvestigationState(doc_id="case_123")
    res = await hydrate_node(state)
    assert res == {}

    # Test missing doc_id
    with pytest.raises(ValueError, match="doc_id partition context is required"):
        state_invalid = InvestigationState(doc_id="")
        await hydrate_node(state_invalid)

    # Test unauthorized doc_id against allowed_doc_ids
    from src.query_agent import nodes
    class MockSettings:
        allowed_doc_ids = ["case_123"]
    monkeypatch.setattr(nodes, "settings", MockSettings())
    
    # Allowed doc_id
    state_allowed = InvestigationState(doc_id="case_123")
    await hydrate_node(state_allowed)
    
    # Unauthorized doc_id
    state_unauthorized = InvestigationState(doc_id="case_456")
    with pytest.raises(PermissionError, match="Unauthorized access"):
        await hydrate_node(state_unauthorized)


def test_entry_router():
    # Routed to coordinator if no HITL pending
    state_new = InvestigationState(doc_id="case_123")
    assert entry_router(state_new) == "coordinator"

    # Routed to process_hitl if fact pending and user feedback is present
    state_hitl = InvestigationState(
        doc_id="case_123",
        pending_fact_approval=PendingFactApproval(
            fact="test fact", source_id="src_1", doc_id="case_123", audit_trace={}
        ),
        user_feedback="approved"
    )
    assert entry_router(state_hitl) == "process_hitl"


@pytest.mark.asyncio
async def test_process_hitl_node():
    pending = PendingFactApproval(
        fact="עדות עד המפתח",
        source_id="chunk_99",
        doc_id="case_123",
        audit_trace={"task": "test_hitl"}
    )
    
    # Case A: Approved
    state_approved = InvestigationState(
        doc_id="case_123",
        pending_fact_approval=pending,
        user_feedback="approved"
    )
    res = await process_hitl_node(state_approved)
    assert len(res["verified_facts"]) == 1
    assert res["verified_facts"][0].fact == "עדות עד המפתח"
    assert res["verified_facts"][0].source_id == "chunk_99"
    assert res["pending_fact_approval"] is None
    assert res["user_feedback"] is None

    # Case B: Rejected
    state_rejected = InvestigationState(
        doc_id="case_123",
        pending_fact_approval=pending,
        user_feedback="rejected"
    )
    res = await process_hitl_node(state_rejected)
    assert len(res["verified_facts"]) == 0
    assert "REJECTED" in res["messages"][-1]["content"]

    # Case C: Modified Text
    state_modified = InvestigationState(
        doc_id="case_123",
        pending_fact_approval=pending,
        user_feedback="עדות משוכתבת של עד המפתח"
    )
    res = await process_hitl_node(state_modified)
    assert len(res["verified_facts"]) == 1
    assert res["verified_facts"][0].fact == "עדות משוכתבת של עד המפתח"


@pytest.mark.asyncio
async def test_state_bloat_pruning_and_sanitization():
    # Test Synthesis Node Sanitization
    state_synthesis = InvestigationState(
        doc_id="case_123",
        transient_doc_results="large doc vector text",
        transient_graph_results="large graph traversal nodes"
    )
    # We mock LLM call inside synthesis
    from src.query_agent import nodes
    async def mock_call(sys_p, user_p, **kwargs):
        return "merged summary"
    nodes.call_llm = mock_call

    res_synthesis = await synthesis_node(state_synthesis)
    assert res_synthesis["transient_synthesis_results"] == "merged summary"
    # Ensure raw inputs are pruned
    assert res_synthesis["transient_doc_results"] is None
    assert res_synthesis["transient_graph_results"] is None

    # Test Finalizer Node Sanitization
    state_final = InvestigationState(
        doc_id="case_123",
        investigation_plan=["task1"],
        explored_entities=["entity_1"],
        transient_doc_results="doc",
        transient_graph_results="graph",
        transient_synthesis_results="synthesis"
    )
    res_final = await finalizer_node(state_final)
    # Check that pruners clear all transient variables
    assert res_final["investigation_plan"] == []
    assert res_final["explored_entities"] == []
    assert res_final["current_task"] is None
    assert res_final["transient_doc_results"] is None
    assert res_final["transient_graph_results"] is None
    assert res_final["transient_synthesis_results"] is None
    assert res_final["pending_fact_approval"] is None
    assert res_final["user_feedback"] is None
