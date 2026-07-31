import json
import pytest
from src.query_agent.nodes import (
    extraction_node,
    process_hitl_node,
    hydrate_node,
    get_investigative_graph,
)
from src.query_agent.state import (
    InvestigationState,
    PendingFactApproval,
    VerifiedFact,
)


@pytest.mark.asyncio
async def test_hitl_approval_flow():
    """Verify that approving a pending fact admits it to verified_facts ledger and routes to planner."""
    pending = PendingFactApproval(
        fact="החשוד נצפה במקום האירוע בשעה 22:00",
        source_id="chunk_10",
        doc_id="case_100",
        grounding_score=0.60,
        audit_trace={"task": "check_location"}
    )
    
    state = InvestigationState(
        doc_id="case_100",
        pending_fact_approval=pending,
        pending_facts_queue=[pending],
        user_feedback="approved"
    )
    
    # 1. Hydrate detects feedback and routes to process_hitl
    cmd_hydrate = await hydrate_node(state, {})
    assert cmd_hydrate.goto == "process_hitl"
    
    # 2. Process HITL node processes approval
    cmd_process = await process_hitl_node(state, {})
    assert cmd_process.goto == "planner"
    assert len(cmd_process.update["verified_facts"]) == 1
    assert cmd_process.update["verified_facts"][0].fact == "החשוד נצפה במקום האירוע בשעה 22:00"
    assert cmd_process.update["pending_fact_approval"] is None
    assert cmd_process.update["pending_facts_queue"] == []


@pytest.mark.asyncio
async def test_hitl_rejection_flow():
    """Verify that rejecting a pending fact discards it and records a runtime notice without adding to verified_facts."""
    pending = PendingFactApproval(
        fact="החשוד הודה במיוחס לו",
        source_id="chunk_11",
        doc_id="case_100",
        grounding_score=0.55,
        audit_trace={"task": "check_confession"}
    )
    
    state = InvestigationState(
        doc_id="case_100",
        pending_fact_approval=pending,
        pending_facts_queue=[pending],
        user_feedback="rejected"
    )
    
    cmd_process = await process_hitl_node(state, {})
    assert cmd_process.goto == "planner"
    assert len(cmd_process.update["verified_facts"]) == 0
    assert "נדחתה" in cmd_process.update["messages"][-1].content
    assert cmd_process.update["pending_fact_approval"] is None


@pytest.mark.asyncio
async def test_hitl_edit_flow():
    """Verify that editing a pending fact admits the customized text to verified_facts."""
    pending = PendingFactApproval(
        fact="החשוד שהה בחו״ל בתאריך האירוע",
        source_id="chunk_12",
        doc_id="case_100",
        grounding_score=0.65,
        audit_trace={"task": "check_alibi"}
    )
    
    edited_text = "החשוד שהה בחו״ל בין התאריכים 01/01/2026 עד 05/01/2026 לפי דרכון מס׳ 12345"
    state = InvestigationState(
        doc_id="case_100",
        pending_fact_approval=pending,
        pending_facts_queue=[pending],
        user_feedback=edited_text
    )
    
    cmd_process = await process_hitl_node(state, {})
    assert cmd_process.goto == "planner"
    assert len(cmd_process.update["verified_facts"]) == 1
    assert cmd_process.update["verified_facts"][0].fact == edited_text
    assert cmd_process.update["verified_facts"][0].confidence == 1.0


@pytest.mark.asyncio
async def test_hitl_multi_fact_queue_flow(monkeypatch):
    """Verify that when extraction produces multiple facts needing HITL, all items are queued and reviewed sequentially."""
    synthesis_text = "תחקירי שטח וחקירות ראשוניות."
    state = InvestigationState(
        doc_id="case_200",
        current_task="investigate facts",
        transient_synthesis_results=synthesis_text,
        autonomous_mode=False
    )
    
    # Mock LLM returning 2 facts with low grounding scores
    async def mock_call_llm(*args, **kwargs):
        return json.dumps({
            "fact_found": True,
            "facts": [
                {
                    "fact": "עובדה 1: החשוד מסר גרסה ראשונית בשטח",
                    "source_id": "chunk_20",
                    "grounding_score": 0.60
                },
                {
                    "fact": "עובדה 2: נתפס מוצג מס׳ 1 ברכב החשוד",
                    "source_id": "chunk_21",
                    "grounding_score": 0.55
                }
            ]
        })
        
    monkeypatch.setattr("src.query_agent.nodes.call_llm", mock_call_llm)
    
    # 1. Extraction node runs
    cmd_ext = await extraction_node(state, {})
    assert cmd_ext.goto == "halt_for_hitl"
    assert len(cmd_ext.update["pending_facts_queue"]) == 2
    assert cmd_ext.update["pending_fact_approval"].fact == "עובדה 1: החשוד מסר גרסה ראשונית בשטח"
    
    # Create state reflecting graph halt after extraction
    state_halt1 = InvestigationState(
        doc_id="case_200",
        pending_facts_queue=cmd_ext.update["pending_facts_queue"],
        pending_fact_approval=cmd_ext.update["pending_fact_approval"],
        user_feedback="approved"
    )
    
    # 2. Turn 1: User approves Fact 1 -> System should pop Fact 1 and halt again for Fact 2
    cmd_process1 = await process_hitl_node(state_halt1, {})
    assert cmd_process1.goto == "halt_for_hitl"
    assert len(cmd_process1.update["verified_facts"]) == 1
    assert cmd_process1.update["verified_facts"][0].fact == "עובדה 1: החשוד מסר גרסה ראשונית בשטח"
    assert len(cmd_process1.update["pending_facts_queue"]) == 1
    assert cmd_process1.update["pending_fact_approval"].fact == "עובדה 2: נתפס מוצג מס׳ 1 ברכב החשוד"
    
    # Create state reflecting second graph halt
    state_halt2 = InvestigationState(
        doc_id="case_200",
        verified_facts=cmd_process1.update["verified_facts"],
        pending_facts_queue=cmd_process1.update["pending_facts_queue"],
        pending_fact_approval=cmd_process1.update["pending_fact_approval"],
        user_feedback="approved"
    )
    
    # 3. Turn 2: User approves Fact 2 -> Queue becomes empty -> Routes to planner
    cmd_process2 = await process_hitl_node(state_halt2, {})
    assert cmd_process2.goto == "planner"
    assert len(cmd_process2.update["verified_facts"]) == 2
    assert cmd_process2.update["pending_facts_queue"] == []
    assert cmd_process2.update["pending_fact_approval"] is None


@pytest.mark.asyncio
async def test_hitl_auto_approval_bypass(monkeypatch):
    """Verify that high-grounding facts bypass HITL queue completely and auto-admit."""
    synthesis_text = "החשוד נעצר לפי צו שופט."
    state = InvestigationState(
        doc_id="case_300",
        current_task="test_auto_approval",
        transient_synthesis_results=synthesis_text,
        autonomous_mode=False
    )
    
    async def mock_call_llm(*args, **kwargs):
        return json.dumps({
            "fact_found": True,
            "facts": [
                {
                    "fact": "החשוד נעצר לפי צו שופט",
                    "quote": "החשוד נעצר לפי צו שופט.",
                    "source_id": "chunk_30",
                    "grounding_score": 0.95
                }
            ]
        })
        
    monkeypatch.setattr("src.query_agent.nodes.call_llm", mock_call_llm)
    
    cmd_ext = await extraction_node(state, {})
    assert cmd_ext.goto == "planner"
    assert len(cmd_ext.update["verified_facts"]) == 1
    assert cmd_ext.update["pending_facts_queue"] == []
    assert cmd_ext.update["pending_fact_approval"] is None
