import logging
import asyncio
import json
from typing import Any, Dict, List, Optional
from pathlib import Path
from src.storage.search_manager import SearchManager
from src.graph.cosmos_manager import CosmosGremlinManager
from src.processing.vector_indexer import generate_embeddings
from src.utils.config import settings

logger = logging.getLogger(__name__)


class CloudKnowledgeProvider:
    """
    Cloud-native implementation of the investigative knowledge provider.
    Combines Azure AI Search for retrieval and Cosmos DB for graph traversal.

    Principles:
    1. Lazy Loading: Only fetches what is needed per query.
    2. Graceful Fallback: Checks availability of managers before querying.
    3. Verbose Observability: Detailed logging of all cloud operations.
    """

    def __init__(
        self,
        search_mgr: Optional[SearchManager] = None,
        cosmos_mgr: Optional[CosmosGremlinManager] = None,
        doc_id: Optional[str] = None,
    ):
        self.search_mgr = search_mgr
        self.cosmos_mgr = cosmos_mgr
        self.doc_id = doc_id

    def _check_availability(self, service: str) -> bool:
        """Checks if a required service manager is initialized and available."""
        if service == "search" and not self.search_mgr:
            logging.warning(
                "[KNOWLEDGE_PROVIDER] [AVAILABILITY_CHECK] [FAILURE] | Message: SearchManager not initialized"
            )
            return False
        if service == "cosmos" and not self.cosmos_mgr:
            logging.warning(
                "[KNOWLEDGE_PROVIDER] [AVAILABILITY_CHECK] [FAILURE] | Message: CosmosGremlinManager not initialized"
            )
            return False
        return True

    def _enforce_doc_id(self) -> None:
        if not self.doc_id:
            raise ValueError(
                "doc_id partition context is required to ensure partition-level data isolation. "
                "Pass 'global' explicitly if global query scope is required."
            )

    async def get_graph_metadata(self) -> Dict[str, Any]:
        """
        Retrieves high-level metadata about the cloud graph from Cosmos DB.
        """
        self._enforce_doc_id()
        if not self._check_availability("cosmos"):
            return {"available": False, "error": "Cosmos DB not available"}

        logging.info(
            "[KNOWLEDGE_PROVIDER] [METADATA] [START] | Message: Fetching graph statistics from Cosmos DB"
        )

        # Optimized counts using bindings
        if self.doc_id and self.doc_id != "global":
            node_count_query = "g.V().has('pk', pk).count()"
            edge_count_query = "g.E().has('pk', pk).count()"
            node_types_query = "g.V().has('pk', pk).label().dedup()"
            bindings = {"pk": self.doc_id}
        else:
            node_count_query = "g.V().count()"
            edge_count_query = "g.E().count()"
            node_types_query = "g.V().label().dedup()"
            bindings = {}

        try:
            results = await asyncio.gather(
                self.cosmos_mgr.execute_query(node_count_query, bindings),
                self.cosmos_mgr.execute_query(edge_count_query, bindings),
                self.cosmos_mgr.execute_query(node_types_query, bindings),
            )

            metadata = {
                "available": True,
                "node_count": results[0][0] if results[0] else 0,
                "edge_count": results[1][0] if results[1] else 0,
                "node_types": results[2] if results[2] else [],
                "source": "Azure Cosmos DB",
            }
            logging.info(
                f"[KNOWLEDGE_PROVIDER] [METADATA] [SUCCESS] | Metadata: Nodes={metadata['node_count']}, Edges={metadata['edge_count']} | Message: Metadata retrieved successfully"
            )
            return metadata
        except Exception as e:
            logging.error(
                f"[KNOWLEDGE_PROVIDER] [METADATA] [ERROR] | Metadata: Error={str(e)} | Message: Failed to query graph metadata"
            )
            return {"available": False, "error": str(e)}

    async def search_entities(self, query: str, top_k: int = 5) -> List[Dict[str, Any]]:
        """
        Performs a search for entities directly in Cosmos DB since the search index is deleted.
        """
        self._enforce_doc_id()
        if not self._check_availability("cosmos"):
            return []

        logging.info(
            f"[KNOWLEDGE_PROVIDER] [ENTITY_SEARCH] [START] | Metadata: Query={query}, TopK={top_k} | Message: Searching for entities directly in Cosmos DB"
        )

        try:
            import re
            from src.models.models import SCHEMA_CONFIG
            hebrew_stopwords = set(SCHEMA_CONFIG.graph_analysis.hebrew_stopwords)
            terms = [
                t for t in re.findall(r"[\w\u0590-\u05FF]+", query)
                if len(t) > 1 and t.lower() not in hebrew_stopwords
            ]
            if not terms:
                return []

            if self.doc_id and self.doc_id != "global":
                gremlin_query = "g.V().has('pk', pk)"
                bindings = {"pk": self.doc_id}
            else:
                gremlin_query = "g.V()"
                bindings = {}

            vertices = await self.cosmos_mgr.execute_query(gremlin_query, bindings)
            
            scored_entities = []
            for v in vertices:
                v_clean = {
                    "id": v.get("id"),
                    "label": v.get("label"),
                    "type": "Entity"
                }
                
                # Extract properties from Cosmos DB format
                properties = v.get("properties", {})
                for prop_name, prop_list in properties.items():
                    if isinstance(prop_list, list) and len(prop_list) > 0:
                        val = prop_list[0].get("value")
                        if prop_name == "type":
                            v_clean["type"] = val
                        else:
                            v_clean[prop_name] = val

                v_label = str(v_clean.get("label", "")).lower()
                v_id_str = str(v_clean.get("id", "")).lower()
                v_type_lower = str(v_clean.get("type", "")).lower()
                v_desc = v_clean.get("description") or v_clean.get("meta_forensic_summary") or ""
                v_desc_lower = str(v_desc).lower()
                
                score = 0.0
                for term in terms:
                    term_lower = term.lower()
                    
                    # Word boundary matching with optional Hebrew prefixes
                    pattern = r'(?:\b|^)[הבלמכוש]?{}(?:\b|$)'.format(re.escape(term_lower))
                    
                    # Score on label
                    if re.search(pattern, v_label):
                        score += 10.0
                        if term_lower == v_label:
                            score += 5.0
                    elif term_lower in v_label:
                        score += 2.0
                    
                    # Score on ID
                    if re.search(pattern, v_id_str):
                        score += 5.0
                    elif term_lower in v_id_str:
                        score += 1.0
                    
                    # Score on Type
                    if v_type_lower and (v_type_lower in term_lower or term_lower in v_type_lower):
                        score += 4.0
                    
                    # Score on Description
                    if v_desc_lower and term_lower in v_desc_lower:
                        score += 2.0

                if score > 0:
                    scored_entities.append((score, v_clean))

            scored_entities.sort(key=lambda x: x[0], reverse=True)
            
            entities = []
            for score, v in scored_entities[:top_k]:
                entities.append({
                    "id": v.get("id"),
                    "label": v.get("label") or v.get("id"),
                    "type": v.get("type") or v.get("label"),
                    "description": v.get("description") or v.get("meta_forensic_summary") or "",
                    "score": score
                })
                
            logging.info(
                f"[KNOWLEDGE_PROVIDER] [ENTITY_SEARCH] [SUCCESS] | Metadata: ResultCount={len(entities)} | Message: Entities retrieved from Cosmos DB"
            )
            return entities
        except Exception as e:
            logging.error(
                f"[KNOWLEDGE_PROVIDER] [ENTITY_SEARCH] [ERROR] | Metadata: Error={str(e)} | Message: Failed to perform Cosmos-based entity search"
            )
            return []

    async def get_entity_context(self, entity_id: str) -> Dict[str, Any]:
        """
        Retrieves full context for an entity: its properties and its immediate relationships from Cosmos DB.
        """
        self._enforce_doc_id()
        if not self._check_availability("cosmos"):
            return {"entity": None, "relationships": []}

        logging.info(
            f"[KNOWLEDGE_PROVIDER] [ENTITY_CONTEXT] [START] | Metadata: EntityID={entity_id} | Message: Traversing graph context in Cosmos DB"
        )

        try:
            partition_key = self.doc_id if self.doc_id != "global" else None
            node_task = self.cosmos_mgr.get_node(entity_id, partition_key=partition_key)
            neighbors_task = self.cosmos_mgr.get_neighbors(entity_id, partition_key=partition_key)

            node, neighbors = await asyncio.gather(node_task, neighbors_task)

            context = {"entity": node, "relationships": []}

            if neighbors:
                for n in neighbors:
                    edge = n.get("edge", {})
                    neighbor = n.get("neighbor", {})
                    neighbor_properties = neighbor.get("properties", {})
                    target_type = "Entity"
                    if "type" in neighbor and not isinstance(neighbor["type"], list):
                        target_type = neighbor["type"]
                    elif isinstance(neighbor_properties.get("type"), list) and len(neighbor_properties["type"]) > 0:
                        target_type = neighbor_properties["type"][0].get("value", "Entity")
                    elif isinstance(neighbor_properties.get("type"), str):
                        target_type = neighbor_properties["type"]

                    context["relationships"].append(
                        {
                            "relation": edge.get("label"),
                            "target_id": neighbor.get("id"),
                            "target_label": neighbor.get("label") or neighbor.get("id"),
                            "target_type": target_type,
                            "metadata": {
                                k: v
                                for k, v in edge.items()
                                if k not in ["id", "label"]
                            },
                        }
                    )

            logging.info(
                f"[KNOWLEDGE_PROVIDER] [ENTITY_CONTEXT] [SUCCESS] | Metadata: RelCount={len(context['relationships'])} | Message: Entity context retrieved successfully"
            )
            return context
        except Exception as e:
            logging.error(
                f"[KNOWLEDGE_PROVIDER] [ENTITY_CONTEXT] [ERROR] | Metadata: EntityID={entity_id}, Error={str(e)} | Message: Failed to retrieve entity context"
            )
            return {"entity": None, "relationships": []}

    async def find_connections(
        self, entity_a: str, entity_b: str, max_hops: int = 3
    ) -> List[List[Dict[str, Any]]]:
        """
        Finds paths between two entities in Cosmos DB using Gremlin.
        """
        self._enforce_doc_id()
        if not self._check_availability("cosmos"):
            return []

        logging.info(
            f"[KNOWLEDGE_PROVIDER] [FIND_CONNECTIONS] [START] | Metadata: From={entity_a}, To={entity_b}, Hops={max_hops} | Message: Searching for investigative paths"
        )

        if self.doc_id and self.doc_id != "global":
            query = (
                "g.V(source).has('pk', pk).repeat(outE().as('e').inV().as('v').has('pk', pk).simplePath())"
                ".until(hasId(target).or().loops().is(gt(hops)))"
                ".hasId(target).path().by(elementMap()).limit(5)"
            )
            bindings = {"source": entity_a, "target": entity_b, "hops": max_hops, "pk": self.doc_id}
        else:
            # Shortest path query using repeat..until
            query = (
                "g.V(source).repeat(outE().as('e').inV().as('v').simplePath())"
                ".until(hasId(target).or().loops().is(gt(hops)))"
                ".hasId(target).path().by(elementMap()).limit(5)"
            )
            bindings = {"source": entity_a, "target": entity_b, "hops": max_hops}

        try:
            paths = await self.cosmos_mgr.execute_query(query, bindings)
            logging.info(
                f"[KNOWLEDGE_PROVIDER] [FIND_CONNECTIONS] [SUCCESS] | Metadata: PathCount={len(paths)} | Message: Pathfinding complete"
            )
            return paths
        except Exception as e:
            logging.error(
                f"[KNOWLEDGE_PROVIDER] [FIND_CONNECTIONS] [ERROR] | Metadata: Error={str(e)}"
            )
            return []

    async def get_quid_pro_quo_chains(self) -> List[Dict[str, Any]]:
        """
        Retrieves potential Quid Pro Quo patterns from the cloud graph.
        """
        self._enforce_doc_id()
        if not self._check_availability("cosmos"):
            return []
        partition_key = self.doc_id if self.doc_id != "global" else None
        return await self.cosmos_mgr.get_quid_pro_quo_chains(partition_key=partition_key)

    async def get_key_actors(self, limit: int = 10) -> List[Dict[str, Any]]:
        """
        Retrieves the most central entities from the cloud graph.
        """
        self._enforce_doc_id()
        if not self._check_availability("cosmos"):
            return []
        partition_key = self.doc_id if self.doc_id != "global" else None
        return await self.cosmos_mgr.get_key_actors(limit=limit, partition_key=partition_key)

    async def get_low_confidence_patterns(
        self, threshold: float = 0.3
    ) -> List[Dict[str, Any]]:
        """
        Retrieves inferred relations with low confidence from the cloud graph.
        """
        self._enforce_doc_id()
        if not self._check_availability("cosmos"):
            return []
        partition_key = self.doc_id if self.doc_id != "global" else None
        return await self.cosmos_mgr.get_low_confidence_patterns(threshold=threshold, partition_key=partition_key)

    async def search_chunks(self, query: str, top_k: int = 10) -> str:
        """
        Searches raw document chunks in Azure AI Search using Hybrid Vector Search.
        """
        self._enforce_doc_id()
        if not self._check_availability("search"):
            return "Search service not available."

        logging.info(
            f"[KNOWLEDGE_PROVIDER] [CHUNK_SEARCH] [START] | Metadata: Query={query}, TopK={top_k} | Message: Searching raw chunks in cloud"
        )

        try:
            # Ensure the chunks index exists before querying (self-healing)
            await self.search_mgr.ensure_investigation_indices()

            query_embs = await generate_embeddings([query])
            query_vec = query_embs[0] if query_embs else []

            from azure.search.documents.aio import SearchClient
            from azure.search.documents.models import VectorizedQuery

            from src.storage.search_manager import SearchManager
            sanitized_doc_id = SearchManager._sanitize_id(self.doc_id) if self.doc_id else None

            async with SearchClient(
                endpoint=self.search_mgr.endpoint,
                index_name=settings.azure_search_index,
                credential=self.search_mgr.credential,
                connection_timeout=10,
                read_timeout=30,
            ) as client:
                # Request up to 100 candidate chunks to ensure sufficient matches after prefix filtering
                vector_query = VectorizedQuery(
                    vector=query_vec, k_nearest_neighbors=100, fields="embedding"
                )
                search_results = await client.search(
                    search_text=query, vector_queries=[vector_query], top=100
                )

                results = []
                async for result in search_results:
                    cid = result.get("id", "unknown")
                    # Enforce strict partition isolation in-memory
                    if sanitized_doc_id and sanitized_doc_id != "global":
                        if not cid.startswith(sanitized_doc_id):
                            continue
                    score = result.get("@search.score", 0)
                    text = result.get("text", "")
                    results.append(
                        f"CHUNK: {cid} (Score: {score:.3f})\nTEXT: {text[:1200]}..."
                    )

                # Return only the requested top_k results
                sliced_results = results[:top_k]
                output = (
                    "\n\n".join(sliced_results)
                    if sliced_results
                    else "No direct matches found in Azure AI Search."
                )
                logging.info(
                    f"[KNOWLEDGE_PROVIDER] [CHUNK_SEARCH] [SUCCESS] | Metadata: ResultCount={len(sliced_results)} | Message: Chunk search complete"
                )
                return output
        except Exception as e:
            logging.error(
                f"[KNOWLEDGE_PROVIDER] [CHUNK_SEARCH] [ERROR] | Metadata: Error={str(e)} | Message: Hybrid chunk search failed"
            )
            return f"Error searching Azure AI Search: {str(e)}"

    async def search_chunks_raw(self, query: str, top_k: int = 10) -> List[Dict[str, Any]]:
        """
        Searches raw document chunks in Azure AI Search and returns raw results.
        """
        self._enforce_doc_id()
        if not self._check_availability("search"):
            return []

        try:
            # Ensure the chunks index exists before querying (self-healing)
            await self.search_mgr.ensure_investigation_indices()

            query_embs = await generate_embeddings([query])
            query_vec = query_embs[0] if query_embs else []

            from azure.search.documents.aio import SearchClient
            from azure.search.documents.models import VectorizedQuery

            from src.storage.search_manager import SearchManager
            sanitized_doc_id = SearchManager._sanitize_id(self.doc_id) if self.doc_id else None

            async with SearchClient(
                endpoint=self.search_mgr.endpoint,
                index_name=settings.azure_search_index,
                credential=self.search_mgr.credential,
                connection_timeout=10,
                read_timeout=30,
            ) as client:
                # Request up to 100 candidate chunks to ensure sufficient matches after prefix filtering
                vector_query = VectorizedQuery(
                    vector=query_vec, k_nearest_neighbors=100, fields="embedding"
                )
                search_results = await client.search(
                    search_text=query, vector_queries=[vector_query], top=100
                )

                results = []
                async for result in search_results:
                    chunk_id = result.get("id", "unknown")
                    # Enforce strict partition isolation in-memory
                    if sanitized_doc_id and sanitized_doc_id != "global":
                        if not chunk_id.startswith(sanitized_doc_id):
                            continue
                    results.append({
                        "id": chunk_id,
                        "score": result.get("@search.score", 0),
                        "text": result.get("text", "")
                    })
                
                # Return the top_k requested results
                return results[:top_k]
        except Exception as e:
            logging.error(f"[KNOWLEDGE_PROVIDER] [CHUNK_SEARCH_RAW] [ERROR] | Error={str(e)}")
            return []

    async def search_edges(self, query: str, top_k: int = 10) -> str:
        """
        Searches graph relationships (edges) in Cosmos DB directly since the search index is deleted.
        """
        self._enforce_doc_id()
        if not self._check_availability("cosmos"):
            return "Cosmos DB not available."

        logging.info(
            f"[KNOWLEDGE_PROVIDER] [EDGE_SEARCH] [START] | Metadata: Query={query}, TopK={top_k}"
        )

        try:
            import re
            from src.models.models import SCHEMA_CONFIG
            hebrew_stopwords = set(SCHEMA_CONFIG.graph_analysis.hebrew_stopwords)
            terms = [
                t for t in re.findall(r"[\w\u0590-\u05FF]+", query)
                if len(t) > 1 and t.lower() not in hebrew_stopwords
            ]
            if not terms:
                return "No relevant relation matches found."

            if self.doc_id and self.doc_id != "global":
                gremlin_query = "g.E().has('pk', pk)"
                bindings = {"pk": self.doc_id}
            else:
                gremlin_query = "g.E()"
                bindings = {}

            edges = await self.cosmos_mgr.execute_query(gremlin_query, bindings)
            
            scored_edges = []
            for e in edges:
                e_clean = {
                    "id": e.get("id"),
                    "label": e.get("label"),
                }
                
                src = str(e.get("outV", ""))
                tgt = str(e.get("inV", ""))
                
                properties = e.get("properties", {})
                for prop_name, val in properties.items():
                    e_clean[prop_name] = val
                
                relation = e_clean.get("label") or "RELATED_TO"
                desc = e_clean.get("description") or ""
                
                score = 0.0
                text_to_match = f"{relation} {desc} {src} {tgt}".lower()
                for term in terms:
                    term_lower = term.lower()
                    if term_lower in text_to_match:
                        score += 1.0
                        if term_lower in relation.lower():
                            score += 2.0
                
                if score > 0:
                    scored_edges.append((score, src, tgt, relation, desc))
            
            scored_edges.sort(key=lambda x: x[0], reverse=True)
            
            results = []
            for score, src, tgt, relation, desc in scored_edges[:top_k]:
                results.append(
                    f"RELATION: {src} --[{relation}]--> {tgt} (Score: {score:.3f})\nDESCRIPTION: {desc}"
                )

            output = (
                "\n\n".join(results)
                if results
                else "No relevant relations found in Cosmos DB."
            )
            logging.info(
                f"[KNOWLEDGE_PROVIDER] [EDGE_SEARCH] [SUCCESS] | Metadata: ResultCount={len(results)} | Message: Edge search complete"
            )
            return output
        except Exception as e:
            logging.error(
                f"[KNOWLEDGE_PROVIDER] [EDGE_SEARCH] [ERROR] | Metadata: Error={str(e)} | Message: Direct edge search failed"
            )
            return f"Error searching Cosmos DB: {str(e)}"

    # --- KnowledgeProvider Protocol Implementation ---
    def get_graph_data(self) -> Dict[str, Any]:
        """[STUB] The cloud provider uses Gremlin for graph access, not a full local JSON."""
        logging.warning("[KNOWLEDGE_PROVIDER] [GET_GRAPH_DATA] [STUB] | Message: Returning empty graph. Cloud mode uses real-time Gremlin queries.")
        return {}

    def get_analysis_metadata(self) -> Dict[str, Any]:
        """
        Attempts to retrieve community summaries and other analysis artifacts from Blob storage.
        """
        logging.info("[KNOWLEDGE_PROVIDER] [ANALYSIS_METADATA] [START] | Message: Fetching analysis artifacts")
        
        from src.storage.factory import get_artifact_store
        store = get_artifact_store()
        
        # Build search path order based on active doc_id context to ensure data isolation
        paths = []
        if self.doc_id and self.doc_id != "global":
            paths.append(f"graph/{self.doc_id}/community_summaries.json")
        else:
            paths.extend(["community_summaries.json", "graph/latest/community_summaries.json"])
            
        try:
            for path in paths:
                if store.exists(path):
                    logging.info(f"[KNOWLEDGE_PROVIDER] [ANALYSIS_METADATA] [SUCCESS] | Path={path} | Message: Loaded community summaries")
                    data = json.loads(store.read_bytes(path).decode("utf-8"))
                    if isinstance(data, dict) and "summaries" in data and "hierarchy" in data:
                        return {
                            "community_summaries": data["summaries"],
                            "community_hierarchy": data["hierarchy"],
                        }
                    return {"community_summaries": data, "community_hierarchy": {}}
        except Exception as e:
            logging.warning(f"[KNOWLEDGE_PROVIDER] [ANALYSIS_METADATA] [ERROR] | Message: Could not load summaries: {e}")

        logging.warning(f"[KNOWLEDGE_PROVIDER] [ANALYSIS_METADATA] [STUB] | DocID={self.doc_id} | Message: No analysis metadata found in cloud storage.")
        return {"community_summaries": {}, "community_hierarchy": {}}

    def get_structural_report(self) -> str:
        """[STUB] Use specific cloud analysis tools for investigative health."""
        logging.warning("[KNOWLEDGE_PROVIDER] [STRUCTURAL_REPORT] [STUB] | Message: Returning placeholder report.")
        return "Structural report not available in cloud-native mode. Use 'get_graph_metadata' for real-time stats."

    def get_vector_index_path(self, index_type: str) -> Path:
        """[STUB] Vector indices reside in Azure AI Search, not on disk."""
        logging.warning(f"[KNOWLEDGE_PROVIDER] [VECTOR_PATH] [STUB] | Metadata: IndexType={index_type} | Message: Returning null path.")
        return Path("/dev/null")

    def get_raw_document_path(self) -> Path:
        """[STUB] Documents reside in Azure Blob Storage."""
        logging.warning("[KNOWLEDGE_PROVIDER] [DOCUMENT_PATH] [STUB] | Message: Returning null path.")
        return Path("/dev/null")

    def stream_nodes(self, chunk_size: int = 1000):
        """[STUB] Direct Gremlin iteration is preferred for large-scale cloud graphs."""
        logging.warning("[KNOWLEDGE_PROVIDER] [STREAM_NODES] [STUB] | Message: Yielding empty list.")
        yield []

    def stream_edges(self, chunk_size: int = 1000):
        """[STUB] Direct Gremlin iteration is preferred for large-scale cloud graphs."""
        logging.warning("[KNOWLEDGE_PROVIDER] [STREAM_EDGES] [STUB] | Message: Yielding empty list.")
        yield []

    def query_nodes(self, filter_params: Dict[str, Any]) -> List[Dict[str, Any]]:
        """[STUB] Use 'search_entities' for cloud-native discovery."""
        logging.warning(f"[KNOWLEDGE_PROVIDER] [QUERY_NODES] [STUB] | Metadata: Params={filter_params} | Message: Returning empty list.")
        return []

    def query_edges(self, filter_params: Dict[str, Any]) -> List[Dict[str, Any]]:
        """[STUB] Use 'search_edges' for cloud-native discovery."""
        logging.warning(f"[KNOWLEDGE_PROVIDER] [QUERY_EDGES] [STUB] | Metadata: Params={filter_params} | Message: Returning empty list.")
        return []
