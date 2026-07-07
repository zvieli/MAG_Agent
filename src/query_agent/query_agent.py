from __future__ import annotations
from src.utils.bidi_utils import fix_text
import asyncio
import json
import re
import logging
from collections import defaultdict
from typing import Any, Optional


# Bypassing CopilotClient and using AzureOpenAI/OpenAI SDK directly
from pydantic import BaseModel, Field
from src.utils.config import settings
from src.query_agent.ontology_engine import OntologyEngine
from src.query_agent.graph_query_builder import GraphQueryBuilder
from src.models.models import SCHEMA_CONFIG, RELATION_LABELS, normalize_relation_label
from src.interfaces.protocols import KnowledgeProvider
from src.processing.cloud_knowledge_provider import CloudKnowledgeProvider
from src.storage.search_manager import SearchManager
from src.graph.cosmos_manager import CosmosGremlinManager

SESSION_IDLE_TIMEOUT_SECONDS = settings.session_idle_timeout_seconds
_COMMUNITY_EMBEDDING_CACHE: dict[str, list[float]] = {}


def _get_tool_desc(name: str, default: str) -> str:
    if name in settings.tool_description_overrides:
        return settings.tool_description_overrides[name]
    return default


# --- Params Classes ---
class QueryKnowledgeGraphParams(BaseModel):
    question: str = Field(description="Investigative question to query the graph with")


class BuildTimelineParams(BaseModel):
    start_date: str | None = Field(default=None, description="Start date (YYYY-MM-DD)")
    end_date: str | None = Field(default=None, description="End date (YYYY-MM-DD)")
    entity_id: str | None = Field(default=None, description="Entity ID to filter by")


class FindConnectionsParams(BaseModel):
    entity_a: str = Field(description="Source entity ID")
    entity_b: str = Field(description="Target entity ID")
    max_hops: int = Field(default=3, description="Maximum hops to traverse")


class AnalyzeInferredParams(BaseModel):
    confidence_threshold: float = Field(
        default=0.3, description="Confidence threshold (0.0 to 1.0)"
    )
    relation_filter: str | None = Field(
        default=None, description="Optional relation type filter"
    )


class MapQuidProQuoParams(BaseModel):
    pass


class SearchSemanticActionsParams(BaseModel):
    query: str = Field(description="Semantic action search query")
    top_k: int = Field(default=30, description="Number of results")


class GlobalGraphSearchParams(BaseModel):
    query: str = Field(description="Context query for global search")
    top_k: int = Field(default=30, description="Number of results")


class FindIsolatedEntitiesParams(BaseModel):
    seed_entity_ids: list[str] = Field(
        default_factory=list, description="Anchor entity IDs"
    )
    max_hops: int = Field(default=1, description="Hops from anchors")
    limit: int = Field(default=30, description="Number of results")


class GetCentralActorsParams(BaseModel):
    limit: int = Field(default=10, description="Number of key actors to retrieve")


class SearchOriginalDocumentParams(BaseModel):
    query: str = Field(description="Text to search in document chunks")

# --- Helper Functions (Pure) ---


def _keyword_overlap_score(query: str, text: str) -> float:
    query_terms = {
        term
        for term in re.findall("[\\w\\u0590-\\u05FF]+", query.lower())
        if len(term) > 2
    }
    text_terms = {
        term
        for term in re.findall("[\\w\\u0590-\\u05FF]+", text.lower())
        if len(term) > 2
    }
    return (
        len(query_terms & text_terms) / max(len(query_terms), 1)
        if query_terms and text_terms
        else 0.0
    )


def _community_membership_maps(
    communities: Any,
) -> tuple[dict[str, list[str]], dict[str, str]]:
    c2n, n2c = {}, {}
    if not isinstance(communities, dict):
        return c2n, n2c
    for k, v in communities.items():
        cid = str(k)
        members = [str(m) for m in v] if isinstance(v, (list, tuple, set)) else []
        c2n[cid] = members
        for m in members:
            n2c[m] = cid
    return c2n, n2c


def _localize_relation_value(value: str) -> str:
    norm = normalize_relation_label(value)
    return RELATION_LABELS.get(norm, RELATION_LABELS.get(norm.upper(), value))


# --- Main Agent Class ---
class QueryAgent:
    """
    Orchestrates investigative reasoning using a knowledge graph and document chunks.

    This agent uses a RARR (Reasoning and Retrieval) cycle to answer complex queries
    by navigating graph structures and verifying facts against original document fragments.
    """

    def __init__(
        self,
        knowledge_provider: Optional[KnowledgeProvider] = None,
        doc_id: Optional[str] = None,
        stateless: bool = True,
    ):
        """
        Initializes the QueryAgent.

        Args:
            knowledge_provider: Provider for graph data and document indices.
                Defaults to CloudKnowledgeProvider if Azure Search is enabled.
            doc_id: Optional investigation document ID to restrict query scope.
            stateless: Whether to run in stateless mode (bypassing CopilotClient).
        """
        self.model_name = settings.graphify_agent_model
        self.provider = None
        self.session = None
        self.client = None
        self.doc_id = doc_id
        self.stateless = True  # Always stateless in Azure OpenAI SDK mode
        self.ontology_engine = OntologyEngine(SCHEMA_CONFIG.model_dump())

        if knowledge_provider:
            self.kp = knowledge_provider
        elif settings.azure_search_endpoint or settings.cosmos_gremlin_endpoint:
            # Lazy initialization of cloud managers
            self.kp = CloudKnowledgeProvider(SearchManager(), CosmosGremlinManager(), doc_id=doc_id)
        else:
            raise RuntimeError(
                "QueryAgent requires Azure Search and/or Cosmos DB configuration. Local fallback has been removed."
            )

        self._past_calls = set()
        self._call_counts = defaultdict(int)

    async def __aenter__(self):
        """Asynchronous entry point for the agent."""
        return self

    async def __aexit__(self, et, ev, tb):
        """Asynchronous exit point for the agent."""
        pass

    async def start(self, route: Optional[str] = None) -> None:
        """
        Starts the agent session and initializes tools.
        No-op for backward compatibility in stateless mode.
        """
        pass

    async def stop(self):
        """Gracefully disconnects the session and client."""
        pass

    async def _build_system_prompt(self) -> str:
        """Constructs the system prompt using schema and settings."""
        entity_types = ", ".join(SCHEMA_CONFIG.entity_types)
        relation_types = ", ".join(SCHEMA_CONFIG.relation_types.values())
        guidelines = "\n".join(SCHEMA_CONFIG.extraction_guidelines)
        raw_prompt = ""

        try:
            raw_prompt = await settings.get_prompt("agent_system_prompt")
            routing_instructions = await settings.get_prompt(
                "query_routing_instructions"
            )
            groundedness_instruction = await settings.get_prompt(
                "query_groundedness_instruction"
            )
            no_apologies_rule = await settings.get_prompt("query_no_apologies_rule")
            return raw_prompt.format(
                entity_types=entity_types,
                relation_types=relation_types,
                guidelines=guidelines,
                routing_instructions=routing_instructions,
                groundedness_instruction=groundedness_instruction,
                no_apologies_rule=no_apologies_rule,
            )
        except Exception as e:
            logging.error(f"[QueryAgent] Error formatting agent system prompt: {e}")
            return raw_prompt

    def _build_context_text(self) -> str:
        """Gathers structural reports and community summaries for RAG context."""
        ctx = ""
        meta = self.kp.get_analysis_metadata()
        summaries = meta.get("community_summaries", {})
        if summaries:
            # Only include top 5 summaries if too many
            top_keys = list(summaries.keys())[:5]
            trimmed = {k: summaries[k] for k in top_keys}
            ctx += "\n\nKey Community Summaries:\n" + json.dumps(
                trimmed, indent=2, ensure_ascii=False
            )
            if len(summaries) > 5:
                ctx += (
                    f"\n... ({len(summaries) - 5} more summaries available via tools)"
                )

        report = self.kp.get_structural_report()
        if report:
            ctx += "\n\nStructural Report (Overview):\n" + report[:2000]
        return ctx

    def _get_metadata(self) -> dict[str, Any]:
        """Internal helper to gather graph metadata."""
        try:
            logging.debug("[QueryAgent] get_graph_data start")
            graph_data = self.kp.get_graph_data()
            logging.debug("[QueryAgent] get_graph_data end")
            if not graph_data:
                return {"available": False}

            logging.debug("[QueryAgent] get_analysis_metadata start")
            analysis = self.kp.get_analysis_metadata()
            logging.debug("[QueryAgent] get_analysis_metadata end")

            nodes = graph_data.get("nodes", [])
            links = graph_data.get("edges", graph_data.get("links", []))

            return {
                "available": True,
                "node_count": len(nodes),
                "edge_count": len(links),
                "node_types": list(
                    set(str(n.get("type")) for n in nodes if n.get("type"))
                ),
                "communities": list(analysis.get("communities", {}).keys()),
                "has_summaries": bool(analysis.get("community_summaries")),
            }
        except Exception as e:
            logging.error(f"[QueryAgent] [METADATA ERROR] {e}")
            return {"available": False, "error": str(e)}

    # --- Tool Implementations ---
    def _check_loop(self, name: str, **kwargs) -> str | None:
        """Prevents the agent from entering tool-calling loops."""
        sig = f"{name}({sorted(kwargs.items())})"
        if sig in self._past_calls:
            return "Loop detected."
        self._past_calls.add(sig)
        self._call_counts[name] += 1

        # Increase limit to allow for extensive 'vacuuming' of fragments
        limit = max(20, settings.max_tool_calls_per_question)
        if self._call_counts[name] > limit:
            return f"Call limit reached for {name} ({limit} calls)."
        return None

    async def get_graph_metadata_tool(self, invocation: ToolInvocation) -> str:
        """
        Returns high-level metadata about the knowledge graph.
        """
        try:
            if isinstance(self.kp, CloudKnowledgeProvider):
                metadata = await self.kp.get_graph_metadata()
            else:
                metadata = await asyncio.to_thread(self._get_metadata)
            return json.dumps(metadata, indent=2)
        except Exception as e:
            return f"Error retrieving metadata: {str(e)}"

    async def global_graph_search_tool(
        self, params: GlobalGraphSearchParams, invocation: ToolInvocation
    ) -> str:
        """
        Performs a global search across the knowledge graph.

        Uses semantic embeddings and keyword overlap to identify relevant clusters.
        Best for finding relevant starting points or answering broad context questions.

        Args:
            params: Parameters including search query and top_k results.

        Returns:
            JSON string with ranked results.
        """
        err = self._check_loop("global_graph_search", q=params.query)
        if err:
            return err

        if not isinstance(self.kp, CloudKnowledgeProvider):
            return "Global search requires CloudKnowledgeProvider configuration."

        # Parallel search in cloud
        entities_task = self.kp.search_entities(params.query, top_k=params.top_k // 2)
        edges_text_task = self.kp.search_edges(params.query, top_k=params.top_k // 2)

        entities, edges_report = await asyncio.gather(entities_task, edges_text_task)

        combined = {"top_entities": entities, "relevant_relationships": edges_report}
        return json.dumps(combined, indent=2, ensure_ascii=False)

    async def query_knowledge_graph_tool(
        self, params: QueryKnowledgeGraphParams, invocation: ToolInvocation
    ) -> str:
        """
        Detailed factual extraction from the graph via traversal.
        """
        if self._check_loop("query_knowledge_graph", question=params.question):
            return "Loop detected."

        if not isinstance(self.kp, CloudKnowledgeProvider) or not self.kp.cosmos_mgr:
            return "QueryAgent is in cloud-native mode. Cosmos DB Gremlin configuration required."

        # 1. Initialize GraphQueryBuilder using schema configuration and active doc_id context
        schema_dict = SCHEMA_CONFIG.model_dump() if hasattr(SCHEMA_CONFIG, "model_dump") else dict(SCHEMA_CONFIG)
        builder = GraphQueryBuilder(schema_dict, self.doc_id or "global")

        # 2. Build the Gremlin traversal query from natural language question
        gremlin_query = await builder.build_gremlin_query(params.question)

        try:
            # 3. Execute traversal against Cosmos DB
            raw_results = await self.kp.cosmos_mgr.execute_query(gremlin_query)
            # 4. Format results back for LLM consumption
            formatted_results = builder.format_results(raw_results)

            # --- PHASE 2: EVIDENCE ANCHORING (Auto-fetch verbatim text for any returned nodes/edges) ---
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
                    # Extract from top-level key
                    val = extract_val(item.get(key))
                    if isinstance(val, str) and val.strip():
                        seen_locations.add(val.strip())
                    
                    # Extract from properties dictionary
                    val = extract_val(properties.get(key))
                    if isinstance(val, str) and val.strip():
                        seen_locations.add(val.strip())

            # Fetch the raw evidence for all identified source locations/IDs
            evidence_texts = []
            for loc in sorted(seen_locations):
                try:
                    # Programmatically call search_original_document_tool to anchor findings
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

    async def build_timeline_tool(
        self, params: BuildTimelineParams, invocation: ToolInvocation
    ) -> str:
        """
        Generates a chronological timeline of events for specific entities.
        """
        if self._check_loop("build_timeline", entity=params.entity_id):
            return "Loop detected."

        if not isinstance(self.kp, CloudKnowledgeProvider):
            return "Timeline analysis is only supported in Cloud-Native mode."

        if not params.entity_id:
            return "Please provide an entity_id for cloud-based timeline analysis."
        ctx = await self.kp.get_entity_context(params.entity_id)
        events = []
        for rel in ctx.get("relationships", []):
            ts = rel.get("metadata", {}).get("meta_timestamp") or rel.get(
                "metadata", {}
            ).get("timestamp")
            if ts:
                events.append(
                    {
                        "timestamp": ts,
                        "event": f"{rel['relation']} with {rel['target_label']}",
                    }
                )
        events.sort(key=lambda x: x["timestamp"])
        return json.dumps(events, indent=2, ensure_ascii=False)

    async def find_connections_tool(
        self, params: FindConnectionsParams, invocation: ToolInvocation
    ) -> str:
        """
        Finds the shortest path or all connections between two entities.
        """
        if self._check_loop("find_connections", a=params.entity_a, b=params.entity_b):
            return "Loop detected."

        if not isinstance(self.kp, CloudKnowledgeProvider):
            return "Connection discovery is only supported in Cloud-Native mode."

        paths = await self.kp.find_connections(
            params.entity_a, params.entity_b, max_hops=params.max_hops
        )
        if not paths and params.max_hops < 5:
            logging.info(
                f"[QueryAgent] find_connections: No paths found at {params.max_hops} hops. Trying {params.max_hops + 1} hops."
            )
            paths = await self.kp.find_connections(
                params.entity_a, params.entity_b, max_hops=params.max_hops + 1
            )
        return json.dumps(paths, indent=2, ensure_ascii=False)

    async def analyze_inferred_patterns_tool(
        self, params: AnalyzeInferredParams, invocation: ToolInvocation
    ) -> str:
        """
        Analyzes the graph for inferred or synthetic connections.
        """
        if self._check_loop("analyze_inferred_patterns"):
            return "Loop detected."

        if not isinstance(self.kp, CloudKnowledgeProvider):
            return "Pattern inference is only supported in Cloud-Native mode."

        patterns = await self.kp.get_low_confidence_patterns(
            threshold=params.confidence_threshold
        )
        return json.dumps(patterns, indent=2, ensure_ascii=False)

    async def map_quid_pro_quo_tool(
        self, params: MapQuidProQuoParams, invocation: ToolInvocation
    ) -> str:
        """
        Identifies potential 'Quid Pro Quo' patterns in the graph.
        """
        if self._check_loop("map_quid_pro_quo"):
            return "Loop detected."

        if not isinstance(self.kp, CloudKnowledgeProvider):
            return "Quid Pro Quo mapping is only supported in Cloud-Native mode."

        chains = await self.kp.get_quid_pro_quo_chains()
        if not chains:
            return await self.kp.search_edges("בתמורה ל", top_k=20)
        return json.dumps(chains, indent=2, ensure_ascii=False)

    async def search_semantic_actions_tool(
        self, params: SearchSemanticActionsParams, invocation: ToolInvocation
    ) -> str:
        """
        Searches for specific actions or relations using semantic similarity in the cloud.
        """
        if self._check_loop("search_semantic_actions", q=params.query):
            return "Loop detected."

        if not isinstance(self.kp, CloudKnowledgeProvider):
            return (
                "Semantic action search requires CloudKnowledgeProvider configuration."
            )

        return await self.kp.search_edges(params.query, top_k=params.top_k)

    async def get_central_actors_tool(
        self, params: GetCentralActorsParams, invocation: ToolInvocation
    ) -> str:
        """
        Identifies the most central entities (God Nodes) in the investigation.
        """
        if self._check_loop("get_central_actors", limit=params.limit):
            return "Loop detected."

        if not isinstance(self.kp, CloudKnowledgeProvider):
            return "Centrality analysis is only supported in Cloud-Native mode."

        actors = await self.kp.get_key_actors(limit=params.limit)
        return json.dumps(actors, indent=2, ensure_ascii=False)

    async def _rerank_chunks(self, query: str, chunks: list[dict[str, Any]], top_n: int = 5) -> list[dict[str, Any]]:
        """
        Uses the LLM to rerank search results based on relevance to the query.
        Returns the top_n most relevant chunks.
        """
        if not chunks:
            return []
            
        from src.extraction.extraction_engine import _build_azure_client
        client = _build_azure_client()
        
        chunk_descriptions = []
        for i, chunk in enumerate(chunks):
            text_preview = chunk.get("text", "")[:400]
            chunk_descriptions.append(f"[{i}] Content: {text_preview}")
            
        chunks_text = "\n\n".join(chunk_descriptions)
        
        system_prompt = (
            "You are a legal document reranking assistant. Your job is to evaluate a list of document chunks and "
            "determine which ones are most relevant to answering the user's investigative query. "
            "Return a JSON list of indices of the top most relevant chunks, ordered from most relevant to least relevant. "
            "Format the response exactly as a JSON array of integers, for example: [2, 0, 4]. "
            "Do not return any other text, explanations, or formatting."
        )
        
        user_prompt = f"Investigative Query: {query}\n\nList of Chunks:\n{chunks_text}\n\nTop indices:"
        
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
            logging.error(f"[QueryAgent] Error during LLM reranking: {e}")
            return chunks[:top_n]

    async def search_original_document_tool(
        self, params: SearchOriginalDocumentParams, invocation: ToolInvocation
    ) -> str:
        """
        Searches the raw document chunks using a Hybrid Vector Search strategy.
        Reranks the results using an LLM model to return the top 5 most relevant.
        """
        if self._check_loop("search_original_document", q=params.query):
            return "Loop detected."

        if not isinstance(self.kp, CloudKnowledgeProvider):
            return "Document search requires CloudKnowledgeProvider configuration."

        # Diagnostic print/log
        index_name = settings.azure_search_index
        logging.info(f"[DIAGNOSTIC] Calling search_original_document_tool | index_name: '{index_name}' | query: '{params.query}'")

        # Fetch top 10 chunks from Azure AI Search
        raw_chunks = await self.kp.search_chunks_raw(params.query, top_k=10)
        
        logging.info(f"[DIAGNOSTIC] search_original_document_tool received {len(raw_chunks)} raw chunks from search service.")
        
        if not raw_chunks:
            return "No direct matches found in Azure AI Search."

        # Rerank using LLM and get top 5
        reranked_chunks = await self._rerank_chunks(params.query, raw_chunks, top_n=5)
        
        formatted_results = []
        for chunk in reranked_chunks:
            cid = chunk["id"]
            score = chunk["score"]
            text = chunk["text"]
            
            # Format clean source and fragment for LLM to cite easily
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
    def route_query(self, user_query: str) -> str:
        """
        Routes the query to Cosmos DB (graph), Azure Search (search), or Combined.
        """
        query_lower = user_query.lower()
        
        graph_keywords = [
            "קשר", "קשורים", "נתיב", "היררכיה", "מי מחובר", "חיבור", "מחוברת",
            "מתווכים", "שרשרת", "תפקיד", "ציר זמן", "סמיכות זמנים", "גרף", "בגרף",
            "מפה", "למפות", "מסלול", "צומת", "צמתים", "ישות", "ישויות", "מעורבים",
            "חשוד", "חשודים", "connect", "path", "hierarchy", "timeline",
            "intermediary", "chain", "graph"
        ]
        search_keywords = [
            "נאמר", "הוכחה", "ציטוט", "פרטי שיחה", "מה נאמר", "ציטוטים", "ראיה",
            "ראיות", "מסמך", "כתוב", "טקסט", "בטקסט", "ציטוטים מדויקים", "ציטוט מדויק",
            "בסעיף", "סעיף", "say", "said", "proof", "quote", "verbatim", "text", "document"
        ]
        
        has_graph = any(kw in query_lower for kw in graph_keywords)
        has_search = any(kw in query_lower for kw in search_keywords)
        
        if has_graph and has_search:
            return "combined"
        elif has_graph:
            return "graph"
        elif has_search:
            return "search"
        else:
            return "combined"

    async def answer_question(self, question: str) -> str:
        return await self.answer_question_stateless(question)

    async def answer_question_stateless(self, question: str) -> str:
        self._past_calls.clear()
        self._call_counts.clear()

        # Route query
        route = self.route_query(question)
        logging.info(f"[QueryAgent] [Stateless] Routing question to: {route}")

        system_prompt = await self._build_system_prompt()
        routing_prompt = ""
        if route == "graph":
            routing_prompt = (
                "\n\n[ROUTING DIRECTION: GRAPH MODE]\n"
                "The user query is structural/connections-based. You MUST go straight to Cosmos DB. "
                "Use query_knowledge_graph, find_connections, or other graph tools. Do NOT use search_original_document unless needed for final quote verification."
            )
        elif route == "search":
            routing_prompt = (
                "\n\n[ROUTING DIRECTION: SEARCH MODE]\n"
                "The user query is content/details-based. You MUST go straight to Azure AI Search. "
                "Use search_original_document to find the relevant chunks, then verify and retrieve quotes using fetch_raw_evidence."
            )
        elif route == "combined":
            routing_prompt = (
                "\n\n[ROUTING DIRECTION: COMBINED MODE]\n"
                "The user query is combined (both structure and content/details). You MUST perform a two-stage retrieval:\n"
                "1. Search Azure AI Search using search_original_document to find the source chunk.\n"
                "2. Extract the chunk ID, and 'jump' to the graph in Cosmos DB using query_knowledge_graph or find_connections to map the relationships."
            )

        context_text = await asyncio.to_thread(self._build_context_text)
        full_system_message = system_prompt + routing_prompt + context_text
        logging.debug(f"[QueryAgent] [Stateless] System message size: {len(full_system_message)} chars")

        # ONTOLOGY ENHANCEMENT: Legacy map_query translation has been deprecated.
        enhanced_question = question

        # Helper to convert Pydantic schema to OpenAI format
        def pydantic_to_openai(name: str, desc: str, params_cls=None):
            func_def = {
                "name": name,
                "description": desc,
            }
            if params_cls:
                schema = params_cls.model_json_schema()
                properties = {}
                for prop_name, prop_val in schema.get("properties", {}).items():
                    prop_type = "string"
                    if "type" in prop_val:
                        prop_type = prop_val["type"]
                    elif "anyOf" in prop_val:
                        types = [t.get("type") for t in prop_val["anyOf"] if t.get("type") != "null"]
                        if types:
                            prop_type = types[0]
                    
                    prop_data = {
                        "type": prop_type,
                        "description": prop_val.get("description", "")
                    }
                    if "enum" in prop_val:
                        prop_data["enum"] = prop_val["enum"]
                    if prop_type == "array" and "items" in prop_val:
                        prop_data["items"] = prop_val["items"]
                        
                    properties[prop_name] = prop_data
                
                func_def["parameters"] = {
                    "type": "object",
                    "properties": properties,
                    "required": schema.get("required", [])
                }
            else:
                func_def["parameters"] = {
                    "type": "object",
                    "properties": {}
                }
            return {"type": "function", "function": func_def}

        openai_tools = [
            pydantic_to_openai("get_graph_metadata", "Returns high-level metadata about the knowledge graph. Use this to understand the schema and the size of the graph."),
            pydantic_to_openai("query_knowledge_graph", "Detailed factual extraction from the graph via traversal. Pass the query or entity names to search for relationships.", QueryKnowledgeGraphParams),
            pydantic_to_openai("build_timeline", "Generates a chronological timeline of events for specific entities.", BuildTimelineParams),
            pydantic_to_openai("find_connections", "Finds the shortest path or all connections between two entities.", FindConnectionsParams),
            pydantic_to_openai("analyze_inferred_patterns", "Analyzes the graph for inferred or synthetic connections.", AnalyzeInferredParams),
            pydantic_to_openai("map_quid_pro_quo", "Identifies potential 'Quid Pro Quo' patterns in the graph.", MapQuidProQuoParams),
            pydantic_to_openai("get_central_actors", "Identifies the most central entities (God Nodes) in the investigation.", GetCentralActorsParams),
            pydantic_to_openai("global_graph_search", "Performs a global search across the knowledge graph.", GlobalGraphSearchParams),
            pydantic_to_openai("search_semantic_actions", "Searches for specific actions or relations using semantic similarity in the cloud.", SearchSemanticActionsParams),
            pydantic_to_openai("search_original_document", "Searches the raw document chunks using a Hybrid Vector Search strategy. Reranks the results using an LLM model to return the top 5 most relevant.", SearchOriginalDocumentParams),
        ]

        from src.extraction.extraction_engine import _build_azure_client
        client = _build_azure_client()

        messages = [
            {"role": "system", "content": full_system_message},
            {"role": "user", "content": enhanced_question}
        ]

        max_turns = 10
        for turn in range(max_turns):
            try:
                response = await client.chat.completions.create(
                    model=self.model_name,
                    messages=messages,
                    tools=openai_tools,
                    tool_choice="auto",
                    temperature=0.0,
                    max_tokens=2000,
                    timeout=settings.llm_request_timeout_seconds,
                )
            except Exception as e:
                logging.error(f"[QueryAgent] [Stateless] LLM call error: {e}")
                return f"Error during LLM invocation: {e}"

            response_message = response.choices[0].message
            # Append assistant message to history
            messages.append(response_message)

            if not response_message.tool_calls:
                return response_message.content or ""

            for tool_call in response_message.tool_calls:
                tool_name = tool_call.function.name
                tool_args_str = tool_call.function.arguments

                try:
                    tool_args = json.loads(tool_args_str)
                except Exception as e:
                    tool_result = f"Error parsing arguments as JSON: {e}"
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tool_call.id,
                        "name": tool_name,
                        "content": tool_result
                    })
                    continue

                try:
                    if tool_name == "get_graph_metadata":
                        res_str = await self.get_graph_metadata_tool(None)
                    elif tool_name == "query_knowledge_graph":
                        res_str = await self.query_knowledge_graph_tool(QueryKnowledgeGraphParams(**tool_args), None)
                    elif tool_name == "build_timeline":
                        res_str = await self.build_timeline_tool(BuildTimelineParams(**tool_args), None)
                    elif tool_name == "find_connections":
                        res_str = await self.find_connections_tool(FindConnectionsParams(**tool_args), None)
                    elif tool_name == "analyze_inferred_patterns":
                        res_str = await self.analyze_inferred_patterns_tool(AnalyzeInferredParams(**tool_args), None)
                    elif tool_name == "map_quid_pro_quo":
                        res_str = await self.map_quid_pro_quo_tool(MapQuidProQuoParams(**tool_args), None)
                    elif tool_name == "get_central_actors":
                        res_str = await self.get_central_actors_tool(GetCentralActorsParams(**tool_args), None)
                    elif tool_name == "global_graph_search":
                        res_str = await self.global_graph_search_tool(GlobalGraphSearchParams(**tool_args), None)
                    elif tool_name == "search_semantic_actions":
                        res_str = await self.search_semantic_actions_tool(SearchSemanticActionsParams(**tool_args), None)
                    elif tool_name == "search_original_document":
                        res_str = await self.search_original_document_tool(SearchOriginalDocumentParams(**tool_args), None)
                    else:
                        res_str = f"Unknown tool: {tool_name}"
                except Exception as e:
                    res_str = f"Error executing tool {tool_name}: {e}"

                messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "name": tool_name,
                    "content": res_str
                })

        return "Error: Maximum reasoning turns reached without a final answer."


async def run_chat_loop():
    async with QueryAgent() as agent:
        print(fix_text("Investigative Query Agent Ready. Type 'exit' to quit."))
        while True:
            q = input("Question> ").strip()
            if q.lower() in ["exit", "quit"]:
                break
            if not q:
                continue
            print(fix_text(await agent.answer_question(q)))


if __name__ == "__main__":
    asyncio.run(run_chat_loop())
