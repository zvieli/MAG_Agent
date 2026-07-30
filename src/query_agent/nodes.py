import logging
import json
import time
from datetime import UTC, datetime
from typing import Dict, Any, List, Literal
from langgraph.graph import StateGraph, END, START
from langgraph.types import Command

from src.utils.config import settings

def _build_azure_client():
    from src.extraction.extraction_engine import _build_azure_client as build_client
    return build_client()

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
from src.utils.observability import log_event, log_trajectory_step

logger = logging.getLogger(__name__)


def _metadata(config: RunnableConfig) -> Dict[str, Any]:
    return dict((config or {}).get("metadata", {}) or {})


def _task_preview(task: str | None, *, limit: int = 180) -> str:
    text = str(task or "").strip().replace("\n", " ")
    return text[:limit]


def _obs_fields(state: InvestigationState, config: RunnableConfig, node_name: str) -> Dict[str, Any]:
    metadata = _metadata(config)
    return {
        "trace_id": metadata.get("trace_id"),
        "doc_id": metadata.get("doc_id") or state.doc_id,
        "node": node_name,
        "task_preview": _task_preview(state.current_task),
    }


def _log_node_started(state: InvestigationState, config: RunnableConfig, node_name: str) -> None:
    log_event("query.langgraph.node.started", **_obs_fields(state, config, node_name))


def _hebrew_runtime_notice(message: str) -> AssistantMessage:
    return AssistantMessage(content=message)


def _initial_user_question(messages: List[ChatMessage]) -> str:
    for msg in messages:
        if msg.get("role") == "user":
            return str(msg.get("content", ""))
    return ""


def trim_messages(messages: List[ChatMessage], max_history: int = 10) -> List[ChatMessage]:
    """Keeps all system messages and the last N user/assistant/tool messages."""
    system_msgs = [m for m in messages if (isinstance(m, dict) and m.get("role") == "system") or getattr(m, "role", "") == "system"]
    other_msgs = [m for m in messages if not ((isinstance(m, dict) and m.get("role") == "system") or getattr(m, "role", "") == "system")]
    return system_msgs + other_msgs[-max_history:]

def _format_compact_source(source_id: str) -> str:
    """Format raw source IDs / chunk keys into clean page-based citations."""
    if not source_id:
        return "Unknown"
    sid = str(source_id).strip()
    if sid in ("synthesis_fallback", "doc_search", "graph_query"):
        return sid
    if sid.startswith("Page ") or sid.startswith("Pages ") or sid.startswith("עמוד "):
        return sid

    parts = [p for p in sid.replace("CHUNK", "").replace("chunk", "").split("_") if p.strip()]
    if parts:
        last_part = parts[-1]
        if last_part.isdigit():
            return f"Page {last_part}"
    return sid


# Core LLM call helper with token trajectory tracking
async def call_llm(
    system_prompt: str,
    user_prompt: str,
    max_tokens: int = 4096,
    temperature: float = 0.0,
    step_name: str = "llm_invocation",
) -> str:
    client = _build_azure_client()
    model = settings.graphify_agent_model or "gpt-4o"
    start_time = time.time()
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
        content = response.choices[0].message.content.strip()
        usage = getattr(response, "usage", None)
        prompt_tokens = getattr(usage, "prompt_tokens", 0) if usage else 0
        completion_tokens = getattr(usage, "completion_tokens", 0) if usage else 0
        duration_ms = round((time.time() - start_time) * 1000, 2)

        log_trajectory_step(
            step_name=step_name,
            model_name=model,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            duration_ms=duration_ms,
        )
        return content
    except Exception as e:
        duration_ms = round((time.time() - start_time) * 1000, 2)
        log_trajectory_step(
            step_name=step_name,
            model_name=model,
            error=str(e),
            duration_ms=duration_ms,
        )
        logger.error(f"[nodes] LLM invocation failed: {e}")
        raise


async def policy_validation_node(state: InvestigationState, config: RunnableConfig) -> Command[Literal["coordinator", END]]:
    """Deterministic policy validation gate to ensure partition isolation & safety rules."""
    node_name = "policy_validation"
    _log_node_started(state, config, node_name)
    logger.info(f"[policy_validation_node] Validating partition isolation for doc_id={state.doc_id}")

    if not state.doc_id or not state.doc_id.strip():
        err_msg = "MISSING_DOC_ID_PARTITION: doc_id partition key is mandatory."
        logger.error(f"[policy_validation] {err_msg}")
        state.errors.append(err_msg)
        log_trajectory_step("policy_validation", doc_id=state.doc_id, error=err_msg)
        return Command(goto=END, update={"errors": state.errors})

    # Validate that prompt injection attempt patterns aren't present in current task
    task = state.current_task or ""
    forbidden_tokens = ["IGNORE SYSTEM INSTRUCTIONS", "DROP TABLE", "DELETE VERTEX"]
    if any(token in task.upper() for token in forbidden_tokens):
        err_msg = "PROMPT_INJECTION_DETECTED: Forbidden instruction tokens found in request."
        logger.warning(f"[policy_validation] {err_msg}")
        state.errors.append(err_msg)
        log_trajectory_step("policy_validation", doc_id=state.doc_id, error=err_msg)
        return Command(goto=END, update={"errors": state.errors})

    log_trajectory_step("policy_validation", doc_id=state.doc_id, tool_status="passed")
    return Command(goto="coordinator")


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
async def hydrate_node(state: InvestigationState, config: RunnableConfig) -> Command[Literal["process_hitl", "policy_validation"]]:
    node_name = "hydrate"
    step = _metadata(config).get("langgraph_step", 1)
    _log_node_started(state, config, node_name)
    logger.info(f"[GRAPH][STEP {step}][NODE {node_name}] Executing node. Current task: {state.current_task}")
    logger.info("[hydrate_node] Validating incoming state payload.")
    if not state.doc_id or not state.doc_id.strip():
        raise ValueError("doc_id partition context is required to ensure partition-level data isolation.")
    
    allowed_doc_ids = getattr(settings, "allowed_doc_ids", None)
    if allowed_doc_ids is not None and state.doc_id not in allowed_doc_ids:
        logger.error(f"[hydrate_node] Unauthorized access attempt to doc_id: {state.doc_id}")
        raise PermissionError(f"Unauthorized access to document partition: {state.doc_id}")

    goto = "policy_validation"
    if state.pending_fact_approval is not None and state.user_feedback is not None:
        goto = "process_hitl"

    log_event("query.langgraph.route.selected", **_obs_fields(state, config, node_name), route=goto)
    return Command(goto=goto)



# Node 2: Process HITL Node
async def process_hitl_node(state: InvestigationState, config: RunnableConfig) -> Dict[str, Any]:
    node_name = "process_hitl"
    step = _metadata(config).get("langgraph_step", 1)
    _log_node_started(state, config, node_name)
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
            messages.append(_hebrew_runtime_notice("העובדה המוצעת נדחתה על ידי המשתמש."))
            hitl_action = "rejected"
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
            messages.append(_hebrew_runtime_notice("העובדה אושרה ונוספה ללדג'ר העובדות המאומתות."))
            hitl_action = "approved" if feedback_clean == "approved" else "edited"
        log_event(
            "query.langgraph.hitl.processed",
            **_obs_fields(state, config, node_name),
            action=hitl_action,
            source_id=pending.source_id,
        )

    return {
        "verified_facts": verified_facts,
        "messages": messages,
        "pending_fact_approval": None,
        "user_feedback": None
    }


# Node 3: Coordinator Node
async def coordinator_node(state: InvestigationState, config: RunnableConfig) -> Command[Literal["doc_branch", "graph_branch", "finalizer"]]:
    node_name = "coordinator"
    step = _metadata(config).get("langgraph_step", 1)
    _log_node_started(state, config, node_name)
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
            messages.append(_hebrew_runtime_notice("נבנתה תוכנית בדיקה ראשונית לשאלה."))
            log_event(
                "query.langgraph.plan.created",
                **_obs_fields(state, config, node_name),
                task_count=len(plan),
            )
        except Exception as e:
            logger.warning(f"Failed to generate planning list, using fallback: {e}")
            plan = [user_query]
            log_event(
                "query.langgraph.plan.fallback",
                **_obs_fields(state, config, node_name),
                error_type=type(e).__name__,
            )

    # Pop next task if current_task is empty/complete
    if not current_task and plan:
        current_task = plan.pop(0)
        messages.append(_hebrew_runtime_notice("מתחילה בדיקת משנה על בסיס חומרי התיק."))

    # Determine branch to route to based on current task
    if not current_task:
        messages = trim_messages(messages)
        log_event("query.langgraph.route.selected", **_obs_fields(state, config, node_name), route="finalizer")
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
        messages.append(_hebrew_runtime_notice("המערכת מתקרבת למגבלת צעדים ולכן עוברת לסיכום התשובה."))
        messages = trim_messages(messages)
        log_event(
            "query.langgraph.route.selected",
            **_obs_fields(state, config, node_name),
            route="finalizer",
            reason="recursion_limit",
            remaining_steps=state.remaining_steps,
        )
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
        decision = "parallel"

    route = "parallel" if isinstance(goto, list) else goto
    log_event(
        "query.langgraph.route.selected",
        **_obs_fields(state, config, node_name),
        route=route,
        decision=decision,
        remaining_plan_items=len(plan),
    )

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
    node_name = "doc_branch"
    step = _metadata(config).get("langgraph_step", 1)
    _log_node_started(state, config, node_name)
    logger.info(f"[GRAPH][STEP {step}][NODE {node_name}] Executing node. Current task: {state.current_task}")
    logger.info(f"[doc_branch_node] Querying vector search for: '{state.current_task}'")
    started_at = time.perf_counter()
    
    # 1. Generate optimal search query
    from src.utils.config_manager import config_manager
    system_prompt = await config_manager.get_config("doc_search_query_system.md")
    user_prompt = f"Task: {state.current_task}"
    res_str = await call_llm(system_prompt, user_prompt)
    
    semantic_query = res_str
    keyword_query = None
    
    try:
        clean_res = res_str.strip()
        if clean_res.startswith("```"):
            clean_res = clean_res.replace("```json", "").replace("```", "").strip()
        data = json.loads(clean_res)
        semantic_query = data.get("semantic_query", res_str)
        keyword_query = data.get("keyword_query", None)
    except Exception:
        semantic_query = res_str.strip().strip('"').strip("'").strip()
        keyword_query = semantic_query
    
    # 2. Call Doc Search (under strict partition context)
    kp = CloudKnowledgeProvider(search_mgr=SearchManager(), doc_id=state.doc_id)
    raw_chunks = await kp.search_chunks_raw(semantic_query, top_k=6, keyword_query=keyword_query)
    
    # 3. Rerank
    reranked = await rerank_chunks_llm(semantic_query, raw_chunks, top_n=3)
    
    results = []
    for chunk in reranked:
        results.append(f"CHUNK {chunk['id']}: {chunk['text']}")
        
    transient_doc_results = "\n\n".join(results) if results else "No matches found in vector search."
    elapsed_ms = int((time.perf_counter() - started_at) * 1000)
    log_event(
        "query.langgraph.doc_search.executed",
        **_obs_fields(state, config, node_name),
        search_query=semantic_query,
        raw_chunk_count=len(raw_chunks),
        reranked_chunk_count=len(reranked),
        duration_ms=elapsed_ms,
    )
    return {
        "transient_doc_results": transient_doc_results
    }


# Node 5: Graph Branch Node
async def graph_branch_node(state: InvestigationState, config: RunnableConfig) -> Dict[str, Any]:
    node_name = "graph_branch"
    step = _metadata(config).get("langgraph_step", 1)
    _log_node_started(state, config, node_name)
    logger.info(f"[GRAPH][STEP {step}][NODE {node_name}] Executing node. Current task: {state.current_task}")
    logger.info(f"[graph_branch_node] Querying graph traversal for: '{state.current_task}'")
    started_at = time.perf_counter()
    
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
    
    # Resolve entity names to correct Cosmos DB node IDs
    async def resolve_id(name: str) -> str:
        if not name:
            return ""
        name_str = name.strip()
        if "_" in name_str or name_str.isascii():
            return name_str
        matches = await kp.search_entities(name_str, top_k=3)
        if matches:
            for m in matches:
                if m.get("label") == name_str:
                    return m.get("id")
            return matches[0].get("id")
        return name_str

    resolved_a = await resolve_id(entity_a)
    resolved_b = await resolve_id(entity_b)

    results = []

    # 2. Execute programmatic tools with hardcoded partition filters
    if action == "connections" and resolved_a and resolved_b:
        paths = await kp.find_connections(resolved_a, resolved_b, max_hops=3)
        results.append(f"Paths between {entity_a} and {entity_b}:\n{json.dumps(paths, indent=2, ensure_ascii=False)}")
        result_count = len(paths)
    elif action == "timeline" and resolved_a:
        ctx = await kp.get_entity_context(resolved_a)
        events = []
        for rel in ctx.get("relationships", []):
            meta = rel.get("metadata", {}) if isinstance(rel.get("metadata"), dict) else {}
            ts = meta.get("meta_timestamp") or meta.get("timestamp")
            
            # Extract description from relation_history_preview if available
            desc = ""
            rel_hist = meta.get("relation_history_preview")
            if rel_hist and isinstance(rel_hist, str):
                try:
                    hist_list = json.loads(rel_hist)
                    if hist_list and isinstance(hist_list, list) and hist_list[0].get("description"):
                        desc = hist_list[0]["description"]
                except Exception:
                    pass
            
            ts_prefix = f"[{ts}] " if ts else ""
            desc_suffix = f" ({desc})" if desc else ""
            events.append(f"{ts_prefix}{rel.get('relation', 'RELATED_TO')} -> {rel.get('target_label', 'Unknown')}{desc_suffix}")
            
        events.sort()
        if events:
            results.append(f"Graph Relationships & Timeline for {entity_a}:\n" + "\n".join(events))
            result_count = len(events)
        else:
            results.append(f"No relationships found for entity: {entity_a}")
            result_count = 0
    else:
        # Default: check ontology rules in Cosmos DB using pushdown
        engine = OntologyEngine(SCHEMA_CONFIG.model_dump())
        flags = await engine.evaluate_pushdown(kp.cosmos_mgr, state.doc_id)
        results.append("Ontology rule violations:\n" + ("\n".join(flags) if flags else "None"))
        result_count = len(flags)

    transient_graph_results = "\n\n".join(results)
    elapsed_ms = int((time.perf_counter() - started_at) * 1000)
    log_event(
        "query.langgraph.graph_action.selected",
        **_obs_fields(state, config, node_name),
        action=action,
        entity_a=entity_a,
        entity_b=entity_b,
        result_count=result_count,
        duration_ms=elapsed_ms,
    )
    return {
        "transient_graph_results": transient_graph_results
    }


async def check_fact_conflict_llm(new_fact: str, verified_facts: List[VerifiedFact]) -> bool:
    if not verified_facts:
        return False
    facts_text = "\n".join([f"- {vf.fact}" for vf in verified_facts[-10:]])
    from src.utils.config_manager import config_manager
    system_prompt = await config_manager.get_config("fact_consistency_auditor_system.md")
    user_prompt = f"Existing Verified Facts:\n{facts_text}\n\nNew Fact:\n{new_fact}"
    try:
        res_str = await call_llm(system_prompt, user_prompt)
        if res_str.startswith("```"):
            res_str = res_str.replace("```json", "").replace("```", "").strip()
        data = json.loads(res_str)
        return bool(data.get("conflict", False))
    except Exception as e:
        logger.warning(f"Conflict check failed: {e}")
        return False


# Node 6: Synthesis Node
async def synthesis_node(state: InvestigationState, config: RunnableConfig) -> Dict[str, Any]:
    node_name = "synthesis"
    step = _metadata(config).get("langgraph_step", 1)
    _log_node_started(state, config, node_name)
    logger.info(f"[GRAPH][STEP {step}][NODE {node_name}] Executing node. Current task: {state.current_task}")
    logger.info("[synthesis_node] Merging graph and vector search evidence.")
    started_at = time.perf_counter()
    
    doc_res = state.transient_doc_results or "No vector search results."
    graph_res = state.transient_graph_results or "No graph search results."
    tool_path = []
    
    doc_has_content = bool(
        state.transient_doc_results
        and len(state.transient_doc_results.strip()) > 20
        and "No matches found" not in state.transient_doc_results
        and "No vector search results" not in state.transient_doc_results
    )
    graph_has_content = bool(
        state.transient_graph_results
        and len(state.transient_graph_results.strip()) > 20
        and "No graph search results" not in state.transient_graph_results
        and "No relationships found" not in state.transient_graph_results
        and "No nodes found" not in state.transient_graph_results
    )
    
    if state.transient_doc_results:
        tool_path.append("doc_search")
    if state.transient_graph_results:
        tool_path.append("graph_query")
        
    doc_score = 0.85 if doc_has_content else 0.30
    graph_score = 0.90 if graph_has_content else 0.30
    
    if doc_has_content and graph_has_content:
        grounding_score = max(doc_score, graph_score)
    elif doc_has_content:
        grounding_score = doc_score
    elif graph_has_content:
        grounding_score = graph_score
    else:
        grounding_score = 0.30
    
    from src.utils.config_manager import config_manager
    base_system_prompt = await config_manager.get_config("synthesis_system.md")
    groundedness_instr = await config_manager.get_config("query_groundedness_instruction.md")
    system_prompt = f"{base_system_prompt}\n\n{groundedness_instr}"
    user_prompt = (
        "ענה בעברית בלבד. שמור מזהי מקורות כפי שהם מופיעים בחומר.\n\n"
        f"Vector Results:\n{doc_res}\n\nGraph Results:\n{graph_res}"
    )
    
    synthesis = await call_llm(system_prompt, user_prompt, max_tokens=4096, step_name="synthesis")
    elapsed_ms = int((time.perf_counter() - started_at) * 1000)
    log_event(
        "query.langgraph.synthesis.completed",
        **_obs_fields(state, config, node_name),
        tool_path=tool_path,
        synthesis_length=len(synthesis),
        duration_ms=elapsed_ms,
    )
    
    # Sanitization: Prune raw transient results to prevent payload state bloat
    return {
        "transient_synthesis_results": synthesis,
        "transient_doc_results": None,
        "transient_graph_results": None,
        "transient_grounding_score": grounding_score,
        "last_tool_path": tool_path,
    }


# Node 7: Extraction Node
async def extraction_node(state: InvestigationState, config: RunnableConfig) -> Command[Literal["halt_for_hitl", "planner"]]:
    node_name = "extraction"
    step = _metadata(config).get("langgraph_step", 1)
    _log_node_started(state, config, node_name)
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
    user_prompt = f"ענה בעברית בלבד. Evidence:\n{synthesis}"
    
    extracted_pendings: List[PendingFactApproval] = []
    HIGH_GROUNDING_THRESHOLD = 0.70
    GRAY_AREA_THRESHOLD = 0.50

    try:
        res_str = await call_llm(system_prompt, user_prompt, max_tokens=4096, step_name="extraction")
        if res_str.startswith("```"):
            res_str = res_str.replace("```json", "").replace("```", "").strip()
        data = json.loads(res_str)
        
        # Support both 'facts' (list) and 'fact' (single item fallback)
        raw_facts = data.get("facts") if isinstance(data.get("facts"), list) else []
        if not raw_facts and data.get("fact_found") and data.get("fact"):
            raw_facts = [{
                "fact": data["fact"],
                "source_id": data.get("source_id", "source_chunk"),
                "grounding_score": data.get("grounding_score")
            }]
            
        for item in raw_facts:
            fact_text = item.get("fact") if isinstance(item, dict) else str(item)
            quote_text = item.get("quote") if isinstance(item, dict) else None
            source_id = item.get("source_id", "source_chunk") if isinstance(item, dict) else "source_chunk"
            item_score = item.get("grounding_score") if isinstance(item, dict) else None
            
            if not fact_text:
                continue

            grounding_score = float(item_score or state.transient_grounding_score or 0.85)
            if quote_text and len(quote_text.strip()) > 10 and quote_text.strip() in synthesis:
                grounding_score = max(grounding_score, 0.85)
            source_type = "graph_query" if "graph_query" in (state.last_tool_path or []) else "doc_search"
            
            if grounding_score >= HIGH_GROUNDING_THRESHOLD:
                conflict_detected = False
            elif GRAY_AREA_THRESHOLD <= grounding_score < HIGH_GROUNDING_THRESHOLD:
                conflict_detected = await check_fact_conflict_llm(fact_text, state.verified_facts)
            else:
                conflict_detected = True
                
            audit_trace = {
                "investigative_task": state.current_task,
                "synthesized_evidence_context": synthesis[:800],
                "tool_path": state.last_tool_path or ["search"],
                "timestamp": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            }
            
            extracted_pendings.append(
                PendingFactApproval(
                    fact=fact_text,
                    source_id=source_id,
                    doc_id=state.doc_id,
                    grounding_score=grounding_score,
                    source_type=source_type,
                    conflict_detected=conflict_detected,
                    quote=quote_text,
                    audit_trace=audit_trace
                )
            )
    except Exception as e:
        logger.warning(f"Fact extraction failed: {e}")

    # Resilient Synthesis Fallback: Ensure evidence is NEVER dropped if synthesis exists
    if not extracted_pendings and len(synthesis.strip()) > 30 and "No vector search" not in synthesis and "No graph search" not in synthesis:
        logger.warning("[extraction_node] LLM returned 0 facts for non-empty synthesis. Invoking resilient synthesis fallback.")
        fallback_lines = [line.strip("- *#").strip() for line in synthesis.split("\n") if len(line.strip("- *#").strip()) > 15]
        for idx, line in enumerate(fallback_lines[:5], 1):
            audit_trace = {
                "investigative_task": state.current_task,
                "synthesized_evidence_context": synthesis[:800],
                "tool_path": state.last_tool_path or ["search"],
                "timestamp": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            }
            extracted_pendings.append(
                PendingFactApproval(
                    fact=line,
                    source_id="synthesis_fallback",
                    doc_id=state.doc_id,
                    grounding_score=float(state.transient_grounding_score or 0.85),
                    source_type="doc_search",
                    conflict_detected=False,
                    quote=line,
                    audit_trace=audit_trace
                )
            )

    # Sanitization: Prune transient_synthesis_results
    updates: Dict[str, Any] = {
        "transient_synthesis_results": None
    }
    
    verified_facts = list(state.verified_facts)
    first_hitl_pending = None
    
    for pending in extracted_pendings:
        requires_hitl = (pending.grounding_score < HIGH_GROUNDING_THRESHOLD or pending.conflict_detected) and not state.autonomous_mode
        
        if requires_hitl:
            if first_hitl_pending is None:
                first_hitl_pending = pending
        else:
            # Auto-approve high-grounding / autonomous facts directly into verified_facts ledger
            new_fact = VerifiedFact(
                fact=pending.fact,
                source_id=pending.source_id,
                doc_id=pending.doc_id,
                confidence=pending.grounding_score,
                quote=pending.quote,
                audit_trace=pending.audit_trace
            )
            verified_facts.append(new_fact)
            
            if state.autonomous_mode and (pending.grounding_score < HIGH_GROUNDING_THRESHOLD or pending.conflict_detected):
                updates["errors"] = list(updates.get("errors", state.errors)) + [
                    f"Autonomous Warning: Fact '{pending.fact[:50]}...' auto-admitted with grounding {pending.grounding_score:.2f}."
                ]
    
    updates["verified_facts"] = verified_facts
    
    if first_hitl_pending:
        updates["pending_fact_approval"] = first_hitl_pending
        goto = "halt_for_hitl"
        log_event(
            "query.langgraph.hitl.pending",
            **_obs_fields(state, config, node_name),
            source_id=first_hitl_pending.source_id,
            tool_path=state.last_tool_path,
            grounding_score=first_hitl_pending.grounding_score,
            conflict_detected=first_hitl_pending.conflict_detected,
        )
    else:
        updates["pending_fact_approval"] = None
        updates["current_task"] = None
        goto = "planner"
        log_event(
            "query.langgraph.extraction.completed",
            **_obs_fields(state, config, node_name),
            fact_count=len(extracted_pendings),
            verified_added=len(verified_facts) - len(state.verified_facts),
            route=goto,
        )
        
    return Command(
        update=updates,
        goto=goto
    )



# Node 8: Halt for HITL Node
async def halt_for_hitl_node(state: InvestigationState, config: RunnableConfig) -> Dict[str, Any]:
    node_name = "halt_for_hitl"
    step = _metadata(config).get("langgraph_step", 1)
    _log_node_started(state, config, node_name)
    logger.info(f"[GRAPH][STEP {step}][NODE {node_name}] Executing node. Current task: {state.current_task}")
    logger.info("[halt_for_hitl_node] Halting graph execution. Awaiting user fact approval.")
    return {}


# Node 9: Planner Node
async def planner_node(state: InvestigationState, config: RunnableConfig) -> Command[Literal["coordinator", "finalizer"]]:
    node_name = "planner"
    step = _metadata(config).get("langgraph_step", 1)
    _log_node_started(state, config, node_name)
    logger.info(f"[GRAPH][STEP {step}][NODE {node_name}] Executing node. Current task: {state.current_task}")
    goto = "coordinator" if state.investigation_plan else "finalizer"
    if state.remaining_steps is not None and state.remaining_steps <= 2:
        logger.warning(f"[planner_node] Approaching recursion limit ({state.remaining_steps} steps left). Forcing fallback to finalizer.")
        goto = "finalizer"
    log_event(
        "query.langgraph.route.selected",
        **_obs_fields(state, config, node_name),
        route=goto,
        remaining_plan_items=len(state.investigation_plan),
    )
    return Command(goto=goto)


# Node 10: Finalizer Node (Sanitizer)
async def finalizer_node(state: InvestigationState, config: RunnableConfig) -> Dict[str, Any]:
    node_name = "finalizer"
    step = _metadata(config).get("langgraph_step", 1)
    _log_node_started(state, config, node_name)
    logger.info(f"[GRAPH][STEP {step}][NODE {node_name}] Executing node. Current task: {state.current_task}")
    logger.info("[finalizer_node] Generating final answer strictly based on Verified Facts ledger.")
    started_at = time.perf_counter()
    
    ledger_content = []
    for idx, fact in enumerate(state.verified_facts, 1):
        compact_src = _format_compact_source(fact.source_id)
        line = f"Fact #{idx}: {fact.fact} [Source: {compact_src}]"
        if fact.quote:
            line += f"\n   Verbatim Quote/Evidence: \"{fact.quote}\""
        ledger_content.append(line)
        
    ledger_text = "\n\n".join(ledger_content) if ledger_content else "No verified facts in ledger."
    original_question = _initial_user_question(list(state.messages))
    
    from src.utils.config_manager import config_manager
    system_prompt = await config_manager.get_config("finalizer_system.md")
    user_prompt = (
        "ענה בעברית בלבד לשאלת המשתמש המקורית, בצורה מפורטת ומקיפה, על בסיס עובדות הלדג'ר המאומתות והציטוטים המדויקים.\n\n"
        f"Original Question:\n{original_question}\n\n"
        f"Verified Facts Ledger:\n{ledger_text}"
    )
    
    final_answer = await call_llm(system_prompt, user_prompt, max_tokens=4096, step_name="finalizer")
    
    # Append best-effort notice if execution was forced to finalize due to step exhaustion
    if state.remaining_steps is not None and state.remaining_steps <= 2:
        final_answer = final_answer + "\n\n⚠️ הערה: תשובה זו הופקה במאמץ מיטבי בשל מגבלת צעדי עיבוד. ייתכן שחלק ממשימות הבדיקה לא מוצו."
    
    messages = list(state.messages)
    messages = trim_messages(messages)
    elapsed_ms = int((time.perf_counter() - started_at) * 1000)
    log_event(
        "query.langgraph.finalized",
        **_obs_fields(state, config, node_name),
        verified_fact_count=len(state.verified_facts),
        answer_length=len(final_answer),
        duration_ms=elapsed_ms,
    )

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
    builder.add_node("policy_validation", policy_validation_node)
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
