import pytest
from src.query_agent.state import InvestigationState, VerifiedFact, PendingFactApproval
from langgraph.types import Command
from src.query_agent.nodes import (
    hydrate_node,
    process_hitl_node,
    coordinator_node,
    synthesis_node,
    extraction_node,
    planner_node,
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
    assert isinstance(res, Command)
    assert res.goto == "coordinator"

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
    res_allowed = await hydrate_node(state_allowed)
    assert isinstance(res_allowed, Command)
    assert res_allowed.goto == "coordinator"
    
    # Unauthorized doc_id
    state_unauthorized = InvestigationState(doc_id="case_456")
    with pytest.raises(PermissionError, match="Unauthorized access"):
        await hydrate_node(state_unauthorized)


@pytest.mark.asyncio
async def test_hydrate_node_routing():
    # Routed to coordinator if no HITL pending
    state_new = InvestigationState(doc_id="case_123")
    res = await hydrate_node(state_new)
    assert isinstance(res, Command)
    assert res.goto == "coordinator"

    # Routed to process_hitl if fact pending and user feedback is present
    state_hitl = InvestigationState(
        doc_id="case_123",
        pending_fact_approval=PendingFactApproval(
            fact="test fact", source_id="src_1", doc_id="case_123", audit_trace={}
        ),
        user_feedback="approved"
    )
    res_hitl = await hydrate_node(state_hitl)
    assert isinstance(res_hitl, Command)
    assert res_hitl.goto == "process_hitl"


@pytest.mark.asyncio
async def test_planner_node():
    # Route to coordinator if there is an investigation plan left
    state_plan = InvestigationState(doc_id="case_123", investigation_plan=["task_1"])
    res_plan = await planner_node(state_plan)
    assert isinstance(res_plan, Command)
    assert res_plan.goto == "coordinator"

    # Route to finalizer if plan is empty
    state_empty = InvestigationState(doc_id="case_123", investigation_plan=[])
    res_empty = await planner_node(state_empty)
    assert isinstance(res_empty, Command)
    assert res_empty.goto == "finalizer"


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

@pytest.mark.asyncio
async def test_coordinator_node_command(monkeypatch):
    from src.query_agent import nodes
    from src.utils.config_manager import config_manager
    
    async def mock_call(sys, user, **kw):
        if "coordinator_system" in sys or "Initial Question" in user:
            return '["task_1", "task_2"]'
        return "parallel"
    
    async def mock_config(name):
        return "system_prompt_mock"
        
    monkeypatch.setattr(nodes, "call_llm", mock_call)
    monkeypatch.setattr(config_manager, "get_config", mock_config)
    
    # 1. Test empty plan and empty current_task (should initialize plan and pop first task, then route)
    state = InvestigationState(
        doc_id="case_123",
        messages=[{"role": "user", "content": "test query"}]
    )
    res = await coordinator_node(state)
    assert isinstance(res, Command)
    assert res.goto == ["doc_branch", "graph_branch"]
    assert res.update["current_task"] == "task_1"
    assert res.update["investigation_plan"] == ["task_2"]
    
    # 2. Test when plan is empty and current_task is empty (routes to finalizer)
    async def mock_call_empty(sys, user, **kw):
        return "[]"
    monkeypatch.setattr(nodes, "call_llm", mock_call_empty)
    
    state_final = InvestigationState(
        doc_id="case_123",
        messages=[{"role": "user", "content": "test query"}]
    )
    res_final = await coordinator_node(state_final)
    assert isinstance(res_final, Command)
    assert res_final.goto == "finalizer"
    assert res_final.update["current_task"] is None


@pytest.mark.asyncio
async def test_extraction_node_command(monkeypatch):
    from src.query_agent import nodes
    from src.utils.config_manager import config_manager
    
    # Scenario 1: No synthesis results
    state_no_synth = InvestigationState(doc_id="case_123")
    res = await extraction_node(state_no_synth)
    assert isinstance(res, Command)
    assert res.goto == "planner"
    assert res.update["current_task"] is None
    assert res.update["transient_synthesis_results"] is None
    
    # Scenario 2: Fact found
    async def mock_call_fact(sys, user, **kw):
        return '{"fact_found": true, "fact": "John Doe is a suspect", "source_id": "doc_1"}'
    async def mock_config(name):
        return "system_prompt_mock"
        
    monkeypatch.setattr(nodes, "call_llm", mock_call_fact)
    monkeypatch.setattr(config_manager, "get_config", mock_config)
    
    state_fact = InvestigationState(
        doc_id="case_123",
        current_task="investigate suspects",
        transient_synthesis_results="John Doe is a suspect based on docs."
    )
    res_fact = await extraction_node(state_fact)
    assert isinstance(res_fact, Command)
    assert res_fact.goto == "halt_for_hitl"
    assert res_fact.update["pending_fact_approval"] is not None
    assert res_fact.update["pending_fact_approval"].fact == "John Doe is a suspect"
    assert res_fact.update["transient_synthesis_results"] is None
    
    # Scenario 3: No fact found
    async def mock_call_no_fact(sys, user, **kw):
        return '{"fact_found": false}'
    monkeypatch.setattr(nodes, "call_llm", mock_call_no_fact)
    
    state_no_fact = InvestigationState(
        doc_id="case_123",
        current_task="investigate suspects",
        transient_synthesis_results="No suspects found."
    )
    res_no_fact = await extraction_node(state_no_fact)
    assert isinstance(res_no_fact, Command)
    assert res_no_fact.goto == "planner"
    assert res_no_fact.update["current_task"] is None
    assert res_no_fact.update["transient_synthesis_results"] is None
