from __future__ import annotations
from src.utils.bidi_utils import fix_text
import asyncio
import json
import logging
import sys
from typing import Any, Optional, Dict, List

from pydantic import BaseModel, Field
from src.utils.config import settings
from src.query_agent.ontology_engine import OntologyEngine
from src.query_agent.graph_query_builder import GraphQueryBuilder
from src.models.models import SCHEMA_CONFIG, RELATION_LABELS, normalize_relation_label
from src.interfaces.protocols import KnowledgeProvider
from src.processing.cloud_knowledge_provider import CloudKnowledgeProvider
from src.storage.search_manager import SearchManager
from src.graph.cosmos_manager import CosmosGremlinManager

from src.query_agent.state import InvestigationState, VerifiedFact, PendingFactApproval
from src.query_agent.nodes import get_investigative_graph

logger = logging.getLogger(__name__)

# --- Params Classes (for legacy/backward compatibility in tests) ---
class QueryKnowledgeGraphParams(BaseModel):
    question: str

class BuildTimelineParams(BaseModel):
    entity_id: str | None = None

class FindConnectionsParams(BaseModel):
    entity_a: str
    entity_b: str
    max_hops: int = 3

class MapQuidProQuoParams(BaseModel):
    pass

class SearchOriginalDocumentParams(BaseModel):
    query: str

class QueryAgent:
    """
    Orchestrates investigative reasoning using a stateless LangGraph state machine.
    """

    def __init__(
        self,
        knowledge_provider: Optional[KnowledgeProvider] = None,
        doc_id: Optional[str] = None,
        stateless: bool = True,
    ):
        self.doc_id = doc_id or "global"
        self.ontology_engine = OntologyEngine(SCHEMA_CONFIG.model_dump())
        
        if knowledge_provider:
            self.kp = knowledge_provider
        else:
            self.kp = CloudKnowledgeProvider(SearchManager(), CosmosGremlinManager(), doc_id=self.doc_id)

    async def __aenter__(self):
        return self

    async def __aexit__(self, et, ev, tb):
        pass

    async def start(self, route: Optional[str] = None) -> None:
        pass

    async def stop(self):
        pass

    # --- Specialized Tools ---

    async def graph_query(self, entity_id: str, target_id: Optional[str] = None, max_hops: int = 3) -> str:
        """
        Retrieves path connections between entity_id and target_id, or gets neighbor context of entity_id.
        Enforces strict partition isolation at the query level.
        """
        doc_id = self.doc_id or "global"
        if target_id:
            logger.info(f"[graph_query] Pathfinding: {entity_id} -> {target_id} (hops={max_hops}) in partition {doc_id}")
            paths = await self.kp.find_connections(entity_id, target_id, max_hops=max_hops)
            return json.dumps(paths, indent=2, ensure_ascii=False)
        else:
            logger.info(f"[graph_query] Neighbor query: {entity_id} in partition {doc_id}")
            ctx = await self.kp.get_entity_context(entity_id)
            return json.dumps(ctx, indent=2, ensure_ascii=False)

    async def graph_timeline(self, entity_id: str) -> str:
        """
        Retrieves chronological timeline for a specific entity.
        Enforces strict partition isolation.
        """
        doc_id = self.doc_id or "global"
        logger.info(f"[graph_timeline] timeline query: {entity_id} in partition {doc_id}")
        ctx = await self.kp.get_entity_context(entity_id)
        events = []
        for rel in ctx.get("relationships", []):
            ts = rel.get("metadata", {}).get("meta_timestamp") or rel.get("metadata", {}).get("timestamp")
            if ts:
                events.append({
                    "timestamp": ts,
                    "event": f"{rel['relation']} with {rel['target_label']} ({rel['target_type']})"
                })
        events.sort(key=lambda x: x["timestamp"])
        return json.dumps(events, indent=2, ensure_ascii=False)

    async def check_ontology_rules(self) -> str:
        """
        Runs direct Cosmos DB pushdown queries to identify legal rule violations.
        Enforces strict partition isolation.
        """
        doc_id = self.doc_id or "global"
        logger.info(f"[check_ontology_rules] Evaluating pushdown rules in partition {doc_id}")
        flags = await self.ontology_engine.evaluate_pushdown(self.kp.cosmos_mgr, doc_id)
        if not flags:
            return "No ontology rule violations detected."
        return "\n".join(flags)

    async def doc_search(self, query: str, top_k: int = 5) -> str:
        """
        Searches raw document chunks in Azure AI Search using Hybrid Vector Search.
        Enforces strict partition isolation.
        """
        doc_id = self.doc_id or "global"
        logger.info(f"[doc_search] Vector search: '{query}' in partition {doc_id}")
        raw_chunks = await self.kp.search_chunks_raw(query, top_k=top_k * 2)
        if not raw_chunks:
            return "No direct matches found in Azure AI Search."
            
        reranked = await self._rerank_chunks(query, raw_chunks, top_n=top_k)
        formatted_results = []
        for chunk in reranked:
            cid = chunk["id"]
            score = chunk["score"]
            text = chunk["text"]
            parts = cid.split("_")
            if len(parts) >= 2:
                source_file = parts[0]
                try:
                    frag_id = f"Chunk {int(parts[1])}"
                except ValueError:
                    frag_id = parts[1]
                source_str = f"Source: {source_file}, Fragment: {frag_id}"
            else:
                source_str = f"Source: {cid}"
            formatted_results.append(
                f"CHUNK {cid} ({source_str}) (Score: {score:.3f})\nTEXT: {text[:1200]}..."
            )
        return "\n\n".join(formatted_results)

    async def _rerank_chunks(self, query: str, chunks: list[dict[str, Any]], top_n: int = 5) -> list[dict[str, Any]]:
        from src.extraction.extraction_engine import _build_azure_client
        client = _build_azure_client()
        chunk_descriptions = []
        for i, chunk in enumerate(chunks):
            text_preview = chunk.get("text", "")[:400]
            chunk_descriptions.append(f"[{i}] Content: {text_preview}")
        chunks_text = "\n\n".join(chunk_descriptions)
        
        system_prompt = (
            "You are a legal document reranking assistant. Evaluate the list of chunks "
            "and return a JSON list of indices of the top most relevant chunks for the query. "
            "Format: [2, 0, 4]."
        )
        user_prompt = f"Query: {query}\n\nChunks:\n{chunks_text}\n\nTop indices:"
        try:
            model = settings.graphify_agent_model or "gpt-4o"
            response = await client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                temperature=0.0,
                max_tokens=50,
                timeout=settings.llm_request_timeout_seconds,
            )
            content = response.choices[0].message.content.strip()
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
            logger.error(f"[QueryAgent] Reranking failed: {e}")
            return chunks[:top_n]

    # --- Backward-compatible wrappers for legacy tests ---

    async def query_knowledge_graph_tool(self, params: QueryKnowledgeGraphParams, invocation: Any) -> str:
        schema_dict = SCHEMA_CONFIG.model_dump() if hasattr(SCHEMA_CONFIG, "model_dump") else dict(SCHEMA_CONFIG)
        builder = GraphQueryBuilder(schema_dict, self.doc_id or "global")
        gremlin_query = await builder.build_gremlin_query(params.question)
        try:
            raw_results = await self.kp.cosmos_mgr.execute_query(gremlin_query)
            formatted_results = builder.format_results(raw_results)
            def extract_val(val):
                if isinstance(val, list) and len(val) > 0 and isinstance(val[0], dict):
                    return val[0].get("value")
                return val
            seen_locations = set()
            for item in raw_results:
                if not isinstance(item, dict):
                    continue
                properties = item.get("properties", {})
                for key in ["source_location", "evidence_id", "source_doc_id", "source_file"]:
                    val = extract_val(item.get(key))
                    if isinstance(val, str) and val.strip():
                        seen_locations.add(val.strip())
                    val = extract_val(properties.get(key))
                    if isinstance(val, str) and val.strip():
                        seen_locations.add(val.strip())
            evidence_texts = []
            for loc in sorted(seen_locations):
                try:
                    search_query = f"{self.doc_id or 'files_03.01.2021'} {loc}"
                    evidence_str = await self.search_original_document_tool(
                        SearchOriginalDocumentParams(query=search_query), None
                    )
                    if evidence_str and "Error" not in evidence_str and "No direct matches" not in evidence_str:
                        evidence_texts.append(evidence_str)
                except Exception as e:
                    logging.warning(f"[QueryAgent] Auto-fetching raw evidence for '{loc}' failed: {e}")
            if evidence_texts:
                formatted_results += "\n\n=== AUTOMATICALLY ANCHORED VERBATIM EVIDENCE CHUNKS ===\n" + "\n\n".join(evidence_texts)
            return formatted_results
        except Exception as e:
            logging.error(f"[QueryAgent] Gremlin query execution failed: {e}", exc_info=True)
            return f"Error executing Gremlin query: {str(e)}"

    async def find_connections_tool(self, params: FindConnectionsParams, invocation: Any) -> str:
        paths = await self.kp.find_connections(
            params.entity_a, params.entity_b, max_hops=params.max_hops
        )
        if not paths and params.max_hops < 5:
            paths = await self.kp.find_connections(
                params.entity_a, params.entity_b, max_hops=params.max_hops + 1
            )
        return json.dumps(paths, indent=2, ensure_ascii=False)

    async def build_timeline_tool(self, params: BuildTimelineParams, invocation: Any) -> str:
        return await self.graph_timeline(entity_id=params.entity_id or "")

    async def map_quid_pro_quo_tool(self, params: MapQuidProQuoParams, invocation: Any) -> str:
        chains = await self.kp.get_quid_pro_quo_chains()
        if not chains:
            return await self.kp.search_edges("בתמורה ל", top_k=20)
        return json.dumps(chains, indent=2, ensure_ascii=False)

    async def search_original_document_tool(self, params: SearchOriginalDocumentParams, invocation: Any) -> str:
        return await self.doc_search(query=params.query)


    # --- Execution Loop ---

    async def answer_question(self, question: str) -> str:
        """
        Executes the LangGraph state machine.
        For automated non-interactive testing, automatically approves all proposed facts.
        """
        state = InvestigationState(doc_id=self.doc_id)
        state.messages.append({"role": "user", "content": question})
        
        graph = get_investigative_graph()
        
        max_turns = 15
        for _ in range(max_turns):
            state_dict = state.model_dump()
            result = await graph.ainvoke(state_dict)
            state = InvestigationState(**result)
            
            # Check for final report
            if state.final_answer:
                return state.final_answer
            
            # Auto-approve facts if we hit an interactive pause during benchmark/tests
            if state.pending_fact_approval:
                logger.info(f"[answer_question] Auto-approving fact: '{state.pending_fact_approval.fact}'")
                state.user_feedback = "approved"
                
        return "Error: Maximum execution turns reached without final response."


async def run_chat_loop():
    doc_id = "global"
    # Detect doc_id context from environment or settings
    from src.storage.factory import get_cosmos_manager
    try:
        cosmos_mgr = get_cosmos_manager()
        pks = await cosmos_mgr.execute_query("g.V().values('partitionKey').dedup()")
        if pks:
            doc_id = pks[0]
    except Exception:
        pass

    agent = QueryAgent(doc_id=doc_id)
    graph = get_investigative_graph()
    
    print(fix_text(f"Investigative Query Agent Ready. Partition context: '{doc_id}'"))
    
    while True:
        try:
            q = input("Question> ").strip()
        except (KeyboardInterrupt, EOFError):
            break
            
        if q.lower() in ["exit", "quit"]:
            break
        if not q:
            continue
            
        state = InvestigationState(doc_id=doc_id)
        state.messages.append({"role": "user", "content": q})
        
        while True:
            state_dict = state.model_dump()
            result = await graph.ainvoke(state_dict)
            state = InvestigationState(**result)
            
            # Check if final report is ready
            if state.final_answer:
                print("\n=== FINAL LEGAL ANALYSIS REPORT ===")
                print(fix_text(state.final_answer))
                print("===================================\n")
                break
                
            # Interactive HITL breakpoint
            if state.pending_fact_approval:
                print("\n--- HUMAN IN THE LOOP FACT REVIEW ---")
                print(f"Proposed Fact: {fix_text(state.pending_fact_approval.fact)}")
                print(f"Source ID:     {state.pending_fact_approval.source_id}")
                print(f"Audit Trace:   {state.pending_fact_approval.audit_trace.get('investigative_task')}")
                print("-------------------------------------")
                
                feedback = input("Approve? [y (approve) / n (reject) / or type edited fact]: ").strip()
                feedback_lower = feedback.lower()
                if feedback_lower in ["y", "yes", "approve", ""]:
                    state.user_feedback = "approved"
                elif feedback_lower in ["n", "no", "reject"]:
                    state.user_feedback = "rejected"
                else:
                    state.user_feedback = feedback  # Custom text edit
                
                print("Resuming execution...")


if __name__ == "__main__":
    asyncio.run(run_chat_loop())
