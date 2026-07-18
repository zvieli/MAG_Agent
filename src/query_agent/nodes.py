import logging
import json
from typing import Dict, Any, List, Literal
from langgraph.graph import StateGraph, END, START
from langgraph.types import Command

from src.utils.config import settings
from src.extraction.extraction_engine import _build_azure_client
from src.processing.cloud_knowledge_provider import CloudKnowledgeProvider
from src.storage.search_manager import SearchManager
from src.graph.cosmos_manager import CosmosGremlinManager
from src.query_agent.ontology_engine import OntologyEngine
from src.models.models import SCHEMA_CONFIG
from langchain_core.runnables import RunnableConfig
from src.query_agent.state import (
    InvestigationState,
    InvestigationInputState,
    InvestigationOutputState,
    VerifiedFact,
    PendingFactApproval,
    AssistantMessage,
    ChatMessage
)

logger = logging.getLogger(__name__)

def trim_messages(messages: List[ChatMessage], max_history: int = 10) -> List[ChatMessage]:
    """Keeps all system messages and the last N user/assistant/tool messages."""
    system_msgs = [m for m in messages if (isinstance(m, dict) and m.get("role") == "system") or getattr(m, "role", "") == "system"]
    other_msgs = [m for m in messages if not ((isinstance(m, dict) and m.get("role") == "system") or getattr(m, "role", "") == "system")]
    return system_msgs + other_msgs[-max_history:]

# Core LLM call helper
async def call_llm(system_prompt: str, user_prompt: str, max_tokens: int = 1000, temperature: float = 0.0) -> str:
    client = _build_azure_client()
    model = settings.graphify_agent_model or "gpt-4o"
    try:
        response = await client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ],
            temperature=temperature,
            max_tokens=max_tokens,
            timeout=settings.llm_request_timeout_seconds,
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        logger.error(f"[nodes] LLM invocation failed: {e}")
        raise

async def rerank_chunks_llm(query: str, chunks: List[Dict[str, Any]], top_n: int = 5) -> List[Dict[str, Any]]:
    if not chunks:
        return []
    chunk_descriptions = []
    for i, chunk in enumerate(chunks):
        text_preview = chunk.get("text", "")[:400]
        chunk_descriptions.append(f"[{i}] Content: {text_preview}")
    chunks_text = "\n\n".join(chunk_descriptions)

    from src.utils.config_manager import config_manager
    system_prompt = await config_manager.get_config("rerank_chunks_system.md")
    user_prompt = f"Query: {query}\n\nChunks:\n{chunks_text}\n\nTop indices:"
    try:
        content = await call_llm(system_prompt, user_prompt, max_tokens=100)
        if content.startswith("```"):
            content = content.replace("```json", "").replace("```", "").strip()
        indices = json.loads(content)
        reranked = []
        for idx in indices:
            if isinstance(idx, int) and 0 <= idx < len(chunks):
                reranked.append(chunks[idx])
        for chunk in chunks:
            if chunk not in reranked:
                reranked.append(chunk)
        return reranked[:top_n]
    except Exception as e:
        logger.error(f"[nodes] Reranking failed, fallback to original order: {e}")
        return chunks[:top_n]


# Node 1: Hydrate Node (Validation)
async def hydrate_node(state: InvestigationState, config: RunnableConfig) -> Command[Literal["process_hitl", "coordinator"]]:
    node_name = config.get("metadata", {}).get("langgraph_node", "unknown")
    step = config.get("metadata", {}).get("langgraph_step", 1)
    logger.info(f"[GRAPH][STEP {step}][NODE {node_name}] Executing node. Current task: {state.current_task}")
    logger.info("[hydrate_node] Validating incoming state payload.")
    if not state.doc_id or not state.doc_id.strip():
        raise ValueError("doc_id partition context is required to ensure partition-level data isolation.")
    
    allowed_doc_ids = getattr(settings, "allowed_doc_ids", None)
    if allowed_doc_ids is not None and state.doc_id not in allowed_doc_ids:
        logger.error(f"[hydrate_node] Unauthorized access attempt to doc_id: {state.doc_id}")
        raise PermissionError(f"Unauthorized access to document partition: {state.doc_id}")

    goto = "coordinator"
    if state.pending_fact_approval is not None and state.user_feedback is not None:
        goto = "process_hitl"

    return Command(goto=goto)



# Node 2: Process HITL Node
async def process_hitl_node(state: InvestigationState, config: RunnableConfig) -> Dict[str, Any]:
    node_name = config.get("metadata", {}).get("langgraph_node", "unknown")
    step = config.get("metadata", {}).get("langgraph_step", 1)
    logger.info(f"[GRAPH][STEP {step}][NODE {node_name}] Executing node. Current task: {state.current_task}")
    logger.info(f"[process_hitl_node] Processing user feedback: '{state.user_feedback}'")
    
    # We must operate on copies to ensure correct state updates in LangGraph
    verified_facts = list(state.verified_facts)
    messages = list(state.messages)
    pending = state.pending_fact_approval
    feedback = state.user_feedback

    if pending:
        feedback_clean = feedback.strip().lower()
        if feedback_clean == "rejected":
            logger.info(f"[HITL_MONITOR] [REJECTED] Proposed fact '{pending.fact}' was rejected by user. Audit context: {pending.audit_trace}")
            messages.append(AssistantMessage(
                content=f"Fact proposal REJECTED by user: '{pending.fact}'"
            ))
        else:
            # If "approved", use the proposed fact; otherwise, use the modified text
            fact_text = pending.fact if feedback_clean == "approved" else feedback
            action_type = "APPROVED" if feedback_clean == "approved" else "EDITED"
            logger.info(f"[HITL_MONITOR] [{action_type}] Fact admitted to ledger. Text: '{fact_text}', Source: {pending.source_id}, Audit context: {pending.audit_trace}")
            new_fact = VerifiedFact(
                fact=fact_text,
                source_id=pending.source_id,
                doc_id=pending.doc_id,
                confidence=1.0,
                audit_trace=pending.audit_trace
            )
            verified_facts.append(new_fact)
            messages.append(AssistantMessage(
                content=f"Verified fact ADMITTED to ledger: '{fact_text}' (Source: {new_fact.source_id})"
            ))

    return {
        "verified_facts": verified_facts,
        "messages": messages,
        "pending_fact_approval": None,
        "user_feedback": None
    }


# Node 3: Coordinator Node
async def coordinator_node(state: InvestigationState, config: RunnableConfig) -> Command[Literal["doc_branch", "graph_branch", "finalizer"]]:
    node_name = config.get("metadata", {}).get("langgraph_node", "unknown")
    step = config.get("metadata", {}).get("langgraph_step", 1)
    logger.info(f"[GRAPH][STEP {step}][NODE {node_name}] Executing node. Current task: {state.current_task}")
    logger.info("[coordinator_node] Directing query routing and task planning.")
    
    plan = list(state.investigation_plan)
    messages = list(state.messages)
    current_task = state.current_task

    # 1. Initialize plan if empty
    if not plan and not current_task:
        # Initial user query is the last user message
        user_query = ""
        for msg in reversed(state.messages):
            if msg.get("role") == "user":
                user_query = msg.get("content", "")
                break
        
        if not user_query:
            user_query = "חקור את החשודים העיקריים" # fallback
        
        from src.utils.config_manager import config_manager
        system_prompt = await config_manager.get_config("coordinator_system.md")
        user_prompt = f"Initial Question: {user_query}"
        try:
            res_str = await call_llm(system_prompt, user_prompt)
            if res_str.startswith("```"):
                res_str = res_str.replace("```json", "").replace("```", "").strip()
            tasks = json.loads(res_str)
            plan = tasks
            messages.append(AssistantMessage(
                content=f"Created investigation plan: {', '.join(plan)}"
            ))
        except Exception as e:
            logger.warning(f"Failed to generate planning list, using fallback: {e}")
            plan = [user_query]

    # Pop next task if current_task is empty/complete
    if not current_task and plan:
        current_task = plan.pop(0)
        messages.append(AssistantMessage(
            content=f"Starting investigative task: '{current_task}'"
        ))

    # Determine branch to route to based on current task
    if not current_task:
        messages = trim_messages(messages)
        return Command(
            update={
                "investigation_plan": plan,
                "current_task": current_task,
                "messages": messages
            },
            goto="finalizer"
        )

    # Proactive recursion limit check
    if state.remaining_steps is not None and state.remaining_steps <= 2:
        logger.warning(f"[coordinator_node] Approaching recursion limit ({state.remaining_steps} steps left). Forcing fallback to finalizer.")
        messages.append(AssistantMessage(
            content=f"Approaching recursion limit ({state.remaining_steps} steps left). Transitioning to final report compilation."
        ))
        messages = trim_messages(messages)
        return Command(
            update={
                "investigation_plan": plan,
                "current_task": current_task,
                "messages": messages
            },
            goto="finalizer"
        )

    from src.utils.config_manager import config_manager
    system_prompt = await config_manager.get_config("route_coordinator_system.md")
    user_prompt = f"Current Task: {current_task}"
    try:
        decision = await call_llm(system_prompt, user_prompt)
        decision = decision.lower().strip()
        if "parallel" in decision:
            goto = ["doc_branch", "graph_branch"]
        elif "graph" in decision:
            goto = "graph_branch"
        else:
            goto = "doc_branch"
    except Exception:
        goto = ["doc_branch", "graph_branch"]

    messages = trim_messages(messages)
    return Command(
        update={
            "investigation_plan": plan,
            "current_task": current_task,
            "messages": messages
        },
        goto=goto
    )



# Node 4: Doc Branch Node
async def doc_branch_node(state: InvestigationState, config: RunnableConfig) -> Dict[str, Any]:
    node_name = config.get("metadata", {}).get("langgraph_node", "unknown")
    step = config.get("metadata", {}).get("langgraph_step", 1)
    logger.info(f"[GRAPH][STEP {step}][NODE {node_name}] Executing node. Current task: {state.current_task}")
    logger.info(f"[doc_branch_node] Querying vector search for: '{state.current_task}'")
    
    # 1. Generate optimal search query
    from src.utils.config_manager import config_manager
    system_prompt = await config_manager.get_config("doc_search_query_system.md")
    user_prompt = f"Task: {state.current_task}"
    search_query = await call_llm(system_prompt, user_prompt)
    
    # 2. Call Doc Search (under strict partition context)
    kp = CloudKnowledgeProvider(search_mgr=SearchManager(), doc_id=state.doc_id)
    raw_chunks = await kp.search_chunks_raw(search_query, top_k=6)
    
    # 3. Rerank
    reranked = await rerank_chunks_llm(search_query, raw_chunks, top_n=3)
    
    results = []
    for chunk in reranked:
        results.append(f"CHUNK {chunk['id']}: {chunk['text']}")
        
    transient_doc_results = "\n\n".join(results) if results else "No matches found in vector search."
    return {
        "transient_doc_results": transient_doc_results
    }


# Node 5: Graph Branch Node
async def graph_branch_node(state: InvestigationState, config: RunnableConfig) -> Dict[str, Any]:
    node_name = config.get("metadata", {}).get("langgraph_node", "unknown")
    step = config.get("metadata", {}).get("langgraph_step", 1)
    logger.info(f"[GRAPH][STEP {step}][NODE {node_name}] Executing node. Current task: {state.current_task}")
    logger.info(f"[graph_branch_node] Querying graph traversal for: '{state.current_task}'")
    
    # 1. LLM decision on graph action
    from src.utils.config_manager import config_manager
    system_prompt = await config_manager.get_config("graph_action_decision_system.md")
    user_prompt = f"Task: {state.current_task}"
    
    action = "ontology"
    entity_a = ""
    entity_b = ""
    try:
        res_str = await call_llm(system_prompt, user_prompt)
        if res_str.startswith("```"):
            res_str = res_str.replace("```json", "").replace("```", "").strip()
        data = json.loads(res_str)
        action = data.get("action", "ontology")
        entity_a = data.get("entity_a", "")
        entity_b = data.get("entity_b", "")
    except Exception:
        pass

    kp = CloudKnowledgeProvider(cosmos_mgr=CosmosGremlinManager(), doc_id=state.doc_id)
    results = []

    # 2. Execute programmatic tools with hardcoded partition filters
    if action == "connections" and entity_a and entity_b:
        paths = await kp.find_connections(entity_a, entity_b, max_hops=3)
        results.append(f"Paths between {entity_a} and {entity_b}:\n{json.dumps(paths, indent=2, ensure_ascii=False)}")
    elif action == "timeline" and entity_a:
        ctx = await kp.get_entity_context(entity_a)
        events = []
        for rel in ctx.get("relationships", []):
            ts = rel.get("metadata", {}).get("meta_timestamp") or rel.get("metadata", {}).get("timestamp")
            if ts:
                events.append(f"{ts}: {rel['relation']} with {rel['target_label']}")
        events.sort()
        results.append(f"Timeline for {entity_a}:\n" + "\n".join(events))
    else:
        # Default: check ontology rules in Cosmos DB using pushdown
        engine = OntologyEngine(SCHEMA_CONFIG.model_dump())
        flags = await engine.evaluate_pushdown(kp.cosmos_mgr, state.doc_id)
        results.append("Ontology rule violations:\n" + ("\n".join(flags) if flags else "None"))

    transient_graph_results = "\n\n".join(results)
    return {
        "transient_graph_results": transient_graph_results
    }


# Node 6: Synthesis Node
async def synthesis_node(state: InvestigationState, config: RunnableConfig) -> Dict[str, Any]:
    node_name = config.get("metadata", {}).get("langgraph_node", "unknown")
    step = config.get("metadata", {}).get("langgraph_step", 1)
    logger.info(f"[GRAPH][STEP {step}][NODE {node_name}] Executing node. Current task: {state.current_task}")
    logger.info("[synthesis_node] Merging graph and vector search evidence.")
    
    doc_res = state.transient_doc_results or "No vector search results."
    graph_res = state.transient_graph_results or "No graph search results."
    
    from src.utils.config_manager import config_manager
    system_prompt = await config_manager.get_config("synthesis_system.md")
    user_prompt = f"Vector Results:\n{doc_res}\n\nGraph Results:\n{graph_res}"
    
    synthesis = await call_llm(system_prompt, user_prompt)
    
    # Sanitization: Prune raw transient results to prevent payload state bloat
    return {
        "transient_synthesis_results": synthesis,
        "transient_doc_results": None,
        "transient_graph_results": None
    }


# Node 7: Extraction Node
async def extraction_node(state: InvestigationState, config: RunnableConfig) -> Command[Literal["halt_for_hitl", "planner"]]:
    node_name = config.get("metadata", {}).get("langgraph_node", "unknown")
    step = config.get("metadata", {}).get("langgraph_step", 1)
    logger.info(f"[GRAPH][STEP {step}][NODE {node_name}] Executing node. Current task: {state.current_task}")
    logger.info("[extraction_node] Extracting admissible facts from synthesized evidence.")
    
    synthesis = state.transient_synthesis_results or ""
    if not synthesis:
        # Flow direct to planner if no synthesis results
        return Command(
            update={
                "current_task": None,
                "transient_synthesis_results": None
            },
            goto="planner"
        )

    # 1. Propose fact extraction
    from src.utils.config_manager import config_manager
    system_prompt = await config_manager.get_config("fact_extraction_system.md")
    user_prompt = f"Evidence:\n{synthesis}"
    
    pending = None
    try:
        res_str = await call_llm(system_prompt, user_prompt)
        if res_str.startswith("```"):
            res_str = res_str.replace("```json", "").replace("```", "").strip()
        data = json.loads(res_str)
        if data.get("fact_found") and data.get("fact") and data.get("source_id"):
            from datetime import datetime
            # Construct Audit Trace with full provenance details
            audit_trace = {
                "investigative_task": state.current_task,
                "synthesized_evidence_context": synthesis[:800],
                "tool_path": ["doc_search", "graph_query"] if state.transient_doc_results and state.transient_graph_results else ["search"],
                "timestamp": datetime.utcnow().isoformat() + "Z"
            }
            pending = PendingFactApproval(
                fact=data["fact"],
                source_id=data["source_id"],
                doc_id=state.doc_id,
                audit_trace=audit_trace
            )
    except Exception as e:
        logger.warning(f"Fact extraction failed: {e}")

    # Sanitization: Prune transient_synthesis_results
    updates: Dict[str, Any] = {
        "transient_synthesis_results": None
    }
    
    if pending:
        updates["pending_fact_approval"] = pending
        goto = "halt_for_hitl"
    else:
        # Mark current task complete since no new fact was found
        updates["current_task"] = None
        goto = "planner"
        
    return Command(
        update=updates,
        goto=goto
    )



# Node 8: Halt for HITL Node
async def halt_for_hitl_node(state: InvestigationState, config: RunnableConfig) -> Dict[str, Any]:
    node_name = config.get("metadata", {}).get("langgraph_node", "unknown")
    step = config.get("metadata", {}).get("langgraph_step", 1)
    logger.info(f"[GRAPH][STEP {step}][NODE {node_name}] Executing node. Current task: {state.current_task}")
    logger.info("[halt_for_hitl_node] Halting graph execution. Awaiting user fact approval.")
    return {}


# Node 9: Planner Node
async def planner_node(state: InvestigationState, config: RunnableConfig) -> Command[Literal["coordinator", "finalizer"]]:
    node_name = config.get("metadata", {}).get("langgraph_node", "unknown")
    step = config.get("metadata", {}).get("langgraph_step", 1)
    logger.info(f"[GRAPH][STEP {step}][NODE {node_name}] Executing node. Current task: {state.current_task}")
    goto = "coordinator" if state.investigation_plan else "finalizer"
    if state.remaining_steps is not None and state.remaining_steps <= 2:
        logger.warning(f"[planner_node] Approaching recursion limit ({state.remaining_steps} steps left). Forcing fallback to finalizer.")
        goto = "finalizer"
    return Command(goto=goto)


# Node 10: Finalizer Node (Sanitizer)
async def finalizer_node(state: InvestigationState, config: RunnableConfig) -> Dict[str, Any]:
    node_name = config.get("metadata", {}).get("langgraph_node", "unknown")
    step = config.get("metadata", {}).get("langgraph_step", 1)
    logger.info(f"[GRAPH][STEP {step}][NODE {node_name}] Executing node. Current task: {state.current_task}")
    logger.info("[finalizer_node] Generating final answer strictly based on Verified Facts ledger.")
    
    ledger_content = []
    for idx, fact in enumerate(state.verified_facts, 1):
        ledger_content.append(f"Fact #{idx}: {fact.fact} [Source: {fact.source_id}]")
        
    ledger_text = "\n".join(ledger_content) if ledger_content else "No verified facts in ledger."
    
    from src.utils.config_manager import config_manager
    system_prompt = await config_manager.get_config("finalizer_system.md")
    user_prompt = f"Verified Facts Ledger:\n{ledger_text}"
    
    final_answer = await call_llm(system_prompt, user_prompt)
    
    # Append best-effort notice if execution was forced to finalize due to step exhaustion
    if state.remaining_steps is not None and state.remaining_steps <= 2:
        final_answer = final_answer + "\n\n⚠️ Note: This report was generated on a best-effort basis due to execution step limits. Some investigative tasks may not have been fully explored."
    
    messages = list(state.messages)
    messages = trim_messages(messages)

    # Sanitization: Prune payload state data to keep serialized size minimum
    return {
        "final_answer": final_answer,
        "messages": messages,
        "investigation_plan": [],
        "explored_entities": [],
        "current_task": None,
        "transient_doc_results": None,
        "transient_graph_results": None,
        "transient_synthesis_results": None,
        "pending_fact_approval": None,
        "user_feedback": None
    }


# Compile the LangGraph
def get_investigative_graph():
    builder = StateGraph(
        InvestigationState,
        input_schema=InvestigationInputState,
        output_schema=InvestigationOutputState
    )
    
    builder.add_node("hydrate", hydrate_node)
    builder.add_node("process_hitl", process_hitl_node)
    builder.add_node("coordinator", coordinator_node)
    builder.add_node("doc_branch", doc_branch_node)
    builder.add_node("graph_branch", graph_branch_node)
    builder.add_node("synthesis", synthesis_node)
    builder.add_node("extraction", extraction_node)
    builder.add_node("halt_for_hitl", halt_for_hitl_node)
    builder.add_node("planner", planner_node)
    builder.add_node("finalizer", finalizer_node)
    
    # Edges
    builder.add_edge(START, "hydrate")
    
    builder.add_edge("process_hitl", "planner")
    
    # Sync parallel execution results
    builder.add_edge("doc_branch", "synthesis")
    builder.add_edge("graph_branch", "synthesis")
    
    builder.add_edge("synthesis", "extraction")
    
    builder.add_edge("halt_for_hitl", END)
    
    builder.add_edge("finalizer", END)
    
    return builder.compile()
