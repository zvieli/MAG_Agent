import asyncio
import json
import logging
from typing import Any, Dict, List, Optional
from gremlin_python.driver import client, serializer
from src.utils.config import settings


MAX_COMPACT_PREVIEW_ITEMS = 3
MAX_COMPACT_PREVIEW_CHARS = 1000
MAX_COMPACT_TEXT_CHARS = 2000


class CosmosGremlinManager:
    """
    Manages connectivity and data synchronization with Azure Cosmos DB (Gremlin API).

    Handles upserting nodes and edges to ensure the investigative graph is
    persisted in a queryable graph database.
    """

    def __init__(self):
        self.endpoint = settings.cosmos_gremlin_endpoint
        self.key = settings.cosmos_gremlin_key
        self.database = settings.cosmos_database
        self.graph = settings.cosmos_graph

        if not all([self.endpoint, self.key, self.database, self.graph]):
            missing = [
                k for k, v in {
                    "COSMOS_GREMLIN_ENDPOINT": self.endpoint,
                    "COSMOS_GREMLIN_KEY": self.key,
                    "COSMOS_DATABASE": self.database,
                    "COSMOS_GRAPH": self.graph
                }.items() if not v
            ]
            error_msg = f"Missing required Cosmos Gremlin settings: {', '.join(missing)}"
            logging.error(f"[COSMOS] [INIT] [ERROR] | Message: {error_msg}")
            raise RuntimeError(error_msg)

        # Azure Cosmos DB Gremlin username format: /dbs/{db}/colls/{graph}
        self.username = f"/dbs/{self.database}/colls/{self.graph}"

        self._client = None
        self._loop = None

    def _get_client(self) -> client.Client:
        if self._client is None:
            from urllib.parse import urlparse
            endpoint = self.endpoint.strip()
            if "://" not in endpoint:
                url_to_parse = f"wss://{endpoint}"
            else:
                url_to_parse = endpoint

            parsed = urlparse(url_to_parse)
            host = parsed.hostname or url_to_parse
            port = parsed.port or 443

            scheme = "wss"
            if parsed.scheme in ["http", "ws"]:
                scheme = "ws"

            wss_url = f"{scheme}://{host}:{port}/"

            # Ensure key is stripped and properly padded for base64
            clean_key = self.key.strip() if self.key else ""
            if clean_key and not clean_key.endswith("==") and len(clean_key) == 86:
                clean_key += "=="

            logging.info(f"Connecting to Cosmos Gremlin: {wss_url} as {self.username}")

            self._client = client.Client(
                wss_url,
                "g",
                username=self.username,
                password=clean_key,
                message_serializer=serializer.GraphSONSerializersV2d0(),
            )
        return self._client

    async def execute_query(
        self, query: str, bindings: Dict[str, Any] = None
    ) -> List[Any]:
        """
        Executes a Gremlin query asynchronously with detailed observability.
        """
        max_retries = 2
        for attempt in range(1, max_retries + 1):
            c = self._get_client()
            loop = asyncio.get_running_loop()

            logging.debug(
                f"[COSMOS] [QUERY_EXEC] [START] | Attempt: {attempt}/{max_retries} | Metadata: query_len={len(query)}, bindings_count={len(bindings or {})} | Message: Sending raw Gremlin query to Cosmos DB"
            )

            def _run():
                callback = c.submitAsync(query, bindings or {})
                return callback.result().all().result()

            try:
                timeout = getattr(settings, "llm_request_timeout_seconds", 60.0)
                result = await asyncio.wait_for(
                    loop.run_in_executor(None, _run),
                    timeout=timeout,
                )
                logging.debug(
                    f"[COSMOS] [QUERY_EXEC] [SUCCESS] | Metadata: result_count={len(result) if isinstance(result, list) else 1} | Message: Query returned successfully"
                )
                return result
            except asyncio.TimeoutError as te:
                logging.error(
                    f"[COSMOS] [QUERY_EXEC] [TIMEOUT] | Metadata: timeout={timeout} | Message: Gremlin query execution timed out"
                )
                self._client = None
                if attempt == max_retries:
                    raise TimeoutError(f"Cosmos DB Gremlin query timed out after {timeout} seconds") from te
            except Exception as e:
                err_msg = str(e).lower()
                logging.error(
                    f"[COSMOS] [QUERY_EXEC] [ERROR] | Attempt: {attempt}/{max_retries} | Metadata: error={str(e)} | Message: Failed to execute Gremlin query",
                    exc_info=True,
                )
                
                is_connection_error = any(x in err_msg for x in ["closed", "connection", "network", "socket", "broken pipe"])
                if is_connection_error:
                    logging.warning("[COSMOS] [CONN_RESET] Resetting Gremlin client connection because of connection error.")
                    try:
                        c.close()
                    except Exception:
                        pass
                    self._client = None
                    
                if attempt == max_retries or not is_connection_error:
                    raise

    def _sanitize_for_cosmos(
        self, data: Dict[str, Any], is_node: bool = True
    ) -> Dict[str, Any]:
        """
        Cleans up data before sending to Cosmos DB to reduce RU consumption and storage bloat.
        Removes redundant fields and heavy metadata not needed for graph traversal.
        """
        clean_data: Dict[str, Any] = {}

        # Define fields to exclude or simplify
        heavy_fields = [
            "resolved_aliases_data",
            "all_relations_history",
            "forensic_summary",
            "description",
            "pk",
            "source_file",
            "partitionKey",
        ]

        for k, v in data.items():
            # 1. Compact heavy investigative fields instead of storing raw nested data.
            if k == "forensic_summary":
                if isinstance(v, str) and v.strip():
                    clean_data["meta_forensic_summary"] = self._truncate_text(v)
                continue

            if k == "resolved_aliases_data":
                self._add_alias_provenance(clean_data, v)
                continue

            if k == "all_relations_history":
                self._add_relation_history(clean_data, v)
                continue

            if k == "source_files":
                self._add_source_files(clean_data, v)
                continue

            if k in heavy_fields:
                continue

            # 2. Simplify source_location if it's too long
            if k == "source_location" and isinstance(v, str) and len(v) > 100:
                chunks = v.split(";")
                clean_data[k] = f"Extracted from {len(chunks)} segments"
                continue

            # 3. Handle metadata object - flattening only essential simple fields
            if k == "metadata" and isinstance(v, dict):
                for mk, mv in v.items():
                    if mk == "forensic_summary":
                        if isinstance(mv, str) and mv.strip():
                            clean_data["meta_forensic_summary"] = self._truncate_text(mv)
                    elif mk == "description":
                        continue
                    elif mk == "resolved_aliases_data":
                        self._add_alias_provenance(clean_data, mv)
                    elif mk == "all_relations_history":
                        self._add_relation_history(clean_data, mv)
                    elif mk == "source_files":
                        self._add_source_files(clean_data, mv)
                    elif mk not in heavy_fields and isinstance(
                        mv, (str, int, float, bool)
                    ):
                        clean_data[f"meta_{mk}"] = mv
                continue

            # 4. Only keep primitive types for properties
            if isinstance(v, (str, int, float, bool)):
                clean_data[k] = self._truncate_text(v) if isinstance(v, str) else v

        # Unify fallback description into meta_forensic_summary if needed
        if is_node and "meta_forensic_summary" not in clean_data:
            raw_desc = data.get("description") or data.get("metadata", {}).get("description")
            if raw_desc and isinstance(raw_desc, str) and raw_desc.strip():
                clean_data["meta_forensic_summary"] = self._truncate_text(raw_desc)

        return clean_data

    @staticmethod
    def _truncate_text(value: str, max_chars: int = MAX_COMPACT_TEXT_CHARS) -> str:
        text = str(value)
        if len(text) <= max_chars:
            return text
        return f"{text[: max_chars - 3]}..."

    @classmethod
    def _json_preview(cls, value: Any) -> str:
        if isinstance(value, list):
            preview_value = value[:MAX_COMPACT_PREVIEW_ITEMS]
        else:
            preview_value = value
        preview = json.dumps(preview_value, ensure_ascii=False, separators=(",", ":"))
        return cls._truncate_text(preview, MAX_COMPACT_PREVIEW_CHARS)

    @classmethod
    def _add_alias_provenance(cls, clean_data: Dict[str, Any], value: Any) -> None:
        if not isinstance(value, list) or not value:
            return
        clean_data["alias_count"] = len(value)
        clean_data["alias_preview"] = cls._json_preview(
            [
                {
                    "id": item.get("original_id"),
                    "label": item.get("original_label"),
                    "source_file": item.get("source_file"),
                    "source_location": item.get("source_location"),
                }
                for item in value
                if isinstance(item, dict)
            ]
        )

    @classmethod
    def _add_relation_history(cls, clean_data: Dict[str, Any], value: Any) -> None:
        if not isinstance(value, list) or not value:
            return
        clean_data["relation_history_count"] = len(value)
        clean_data["relation_history_preview"] = cls._json_preview(
            [
                {
                    "relation": item.get("relation"),
                    "quality": item.get("quality"),
                    "timestamp": item.get("timestamp"),
                    "source_file": item.get("source_file"),
                    "source_location": item.get("source_location"),
                    "description": cls._truncate_text(
                        str(item.get("description") or ""), 240
                    )
                    if item.get("description")
                    else None,
                }
                for item in value
                if isinstance(item, dict)
            ]
        )

    @classmethod
    def _add_source_files(cls, clean_data: Dict[str, Any], value: Any) -> None:
        if not isinstance(value, list) or not value:
            return
        clean_data["source_files_count"] = len(value)
        clean_data["source_files_preview"] = cls._json_preview(
            [str(item) for item in value[:MAX_COMPACT_PREVIEW_ITEMS]]
        )

    async def upsert_node(self, node: Dict[str, Any]):
        """
        Upserts a node (vertex) into the graph with full lifecycle tracking and sanitization.
        Uses context-bound partitioning (doc_id) to ensure single-partition routing.
        """
        node_id = str(node.get("id"))
        # Use type as label if available, fallback to 'Entity'
        node_type = node.get("type") or "Entity"

        # OPTIMIZATION: Use pk as the unique partition key
        pk_value = (
            node.get("pk")
            or node.get("partitionKey")
            or node.get("source_file")
            or node.get("metadata", {}).get("source_file")
            or node.get("doc_id")
            or "global"
        )

        # Sanitize data before building query
        clean_properties = self._sanitize_for_cosmos(node, is_node=True)

        # Ensure 'type' is in clean_properties so it's persisted as a property
        if "type" not in clean_properties:
            clean_properties["type"] = node_type

        logging.info(
            f"[COSMOS] [NODE_UPSERT] [START] | Metadata: NodeID={node_id}, Type={node_type}, PK={pk_value}, PropCount={len(clean_properties)} | Message: Initiating sanitized vertex upsert"
        )

        # Include pk in the lookup to ensure single-partition routing
        query = (
            "g.V(id).has('pk', pk).fold().coalesce("
            "  unfold(), "
            "  addV(label).property('id', id).property('pk', pk)"
            ")"
        )

        bindings = {"id": node_id, "label": node_type, "pk": pk_value}

        idx = 0
        for k, v in clean_properties.items():
            if k in ["id", "pk", "source_file", "partitionKey"]:
                continue
            prop_key = f"p_{idx}"
            query += f".property('{k}', {prop_key})"
            bindings[prop_key] = v
            idx += 1

        try:
            await self.execute_query(query, bindings)
            logging.info(
                f"[COSMOS] [NODE_UPSERT] [SUCCESS] | Metadata: NodeID={node_id} | Message: Vertex created/updated successfully"
            )
        except Exception:
            logging.error(
                f"[COSMOS] [NODE_UPSERT] [ERROR] | Metadata: NodeID={node_id} | Message: Critical failure in vertex upsert"
            )
            raise

    async def upsert_edge(self, edge: Dict[str, Any]):
        """
        Upserts an edge with full context tracking and sanitization.
        """
        source = str(edge.get("source"))
        target = str(edge.get("target"))
        relation = edge.get("relation") or "related_to"
        edge_id = f"{source}_{relation}_{target}"

        # OPTIMIZATION: Use pk as the unique partition key
        pk_value = (
            edge.get("pk")
            or edge.get("partitionKey")
            or edge.get("source_file")
            or edge.get("metadata", {}).get("source_file")
            or edge.get("doc_id")
            or "global"
        )

        # Sanitize properties
        clean_properties = self._sanitize_for_cosmos(edge, is_node=False)

        logging.info(
            f"[COSMOS] [EDGE_UPSERT] [START] | Metadata: EdgeID={edge_id}, Source={source}, Target={target}, Relation={relation}, PK={pk_value}, PropCount={len(clean_properties)} | Message: Connecting vertices with sanitized metadata"
        )

        query = (
            "g.V(source).has('pk', pk).as('s').V(target).has('pk', pk).as('t')"
            ".coalesce("
            "  select('s').outE(rel).where(inV().hasId(target)),"
            "  addE(rel).from('s').to('t').property('id', eid).property('pk', pk)"
            ")"
        )

        bindings = {
            "source": source,
            "target": target,
            "rel": relation,
            "eid": edge_id,
            "pk": pk_value,
        }

        idx = 0
        for k, v in clean_properties.items():
            if k in ["source", "target", "relation", "id", "source_file", "partitionKey"]:
                continue
            prop_key = f"ep_{idx}"
            query += f".property('{k}', {prop_key})"
            bindings[prop_key] = v
            idx += 1

        try:
            await self.execute_query(query, bindings)
            logging.info(
                f"[COSMOS] [EDGE_UPSERT] [SUCCESS] | Metadata: EdgeID={edge_id} | Message: Edge created/updated successfully"
            )
        except Exception:
            logging.error(
                f"[COSMOS] [EDGE_UPSERT] [ERROR] | Metadata: EdgeID={edge_id} | Message: Critical failure in edge upsert"
            )
            raise

    async def get_node(self, node_id: str, partition_key: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """
        Retrieves a single node and its properties from the graph.
        """
        if partition_key:
            query = "g.V(id).has('pk', pk)"
            bindings = {"id": node_id, "pk": partition_key}
        else:
            query = "g.V(id)"
            bindings = {"id": node_id}
        try:
            result = await self.execute_query(query, bindings)
            return result[0] if result else None
        except Exception as e:
            logging.error(
                f"[COSMOS] [NODE_READ] [ERROR] | Metadata: NodeID={node_id}, Error={str(e)} | Message: Failed to retrieve vertex"
            )
            return None

    async def get_neighbors(self, node_id: str, partition_key: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        Retrieves all immediate neighbors and the connecting edges for a node.
        """
        if partition_key:
            query = (
                "g.V(id).has('pk', pk).outE().as('edge').inV().as('neighbor').select('edge', 'neighbor')"
            )
            bindings = {"id": node_id, "pk": partition_key}
        else:
            query = (
                "g.V(id).outE().as('edge').inV().as('neighbor').select('edge', 'neighbor')"
            )
            bindings = {"id": node_id}
        try:
            return await self.execute_query(query, bindings)
        except Exception as e:
            logging.error(
                f"[COSMOS] [NEIGHBORS_READ] [ERROR] | Metadata: NodeID={node_id}, Error={str(e)} | Message: Failed to retrieve neighbors"
            )
            return []

    async def get_quid_pro_quo_chains(self, max_hops: int = 3, partition_key: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        Identifies reciprocal relationships and exchange patterns (Quid Pro Quo) in the cloud graph.
        """
        logging.info(
            "[COSMOS] [ANALYTICS] [START] | Message: Searching for Quid Pro Quo patterns"
        )
        if partition_key:
            query = (
                "g.V().has('pk', pk).hasLabel('PERSON', 'ORGANIZATION').as('a')"
                ".outE('FINANCIAL_TRANSFER', 'IN_EXCHANGE_FOR').inV().has('pk', pk).as('b')"
                ".outE().where(inV().hasId(select('a').id())).as('return_edge')"
                ".select('a', 'b', 'return_edge').limit(20)"
            )
            bindings = {"pk": partition_key}
            try:
                return await self.execute_query(query, bindings)
            except Exception as e:
                logging.error(
                    f"[COSMOS] [ANALYTICS] [ERROR] | Metadata: Error={str(e)} | Message: Quid Pro Quo query failed"
                )
                return []
        else:
            query = (
                "g.V().hasLabel('PERSON', 'ORGANIZATION').as('a')"
                ".outE('FINANCIAL_TRANSFER', 'IN_EXCHANGE_FOR').inV().as('b')"
                ".outE().where(inV().hasId(select('a').id())).as('return_edge')"
                ".select('a', 'b', 'return_edge').limit(20)"
            )
            try:
                return await self.execute_query(query)
            except Exception as e:
                logging.error(
                    f"[COSMOS] [ANALYTICS] [ERROR] | Metadata: Error={str(e)} | Message: Quid Pro Quo query failed"
                )
                return []

    async def get_key_actors(self, limit: int = 10, partition_key: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        Identifies the most central entities (God Nodes) using degree centrality in the cloud.
        """
        logging.info(
            "[COSMOS] [ANALYTICS] [START] | Message: Calculating graph centrality for key actors"
        )
        if partition_key:
            query = (
                "g.V().has('pk', pk).project('id', 'label', 'degree', 'type', 'description')"
                ".by('id').by(label).by(bothE().count())"
                ".by(coalesce(values('type'), constant('Entity')))"
                ".by(coalesce(values('description'), coalesce(values('meta_forensic_summary'), constant(''))))"
                ".order().by(select('degree'), decr).limit(count)"
            )
            bindings = {"count": limit, "pk": partition_key}
        else:
            query = (
                "g.V().project('id', 'label', 'degree', 'type', 'description')"
                ".by('id').by(label).by(bothE().count())"
                ".by(coalesce(values('type'), constant('Entity')))"
                ".by(coalesce(values('description'), coalesce(values('meta_forensic_summary'), constant(''))))"
                ".order().by(select('degree'), decr).limit(count)"
            )
            bindings = {"count": limit}
        try:
            return await self.execute_query(query, bindings)
        except Exception as e:
            logging.error(
                f"[COSMOS] [ANALYTICS] [ERROR] | Metadata: Error={str(e)} | Message: Centrality query failed"
            )
            return []

    async def get_low_confidence_patterns(
        self, threshold: float = 0.3, partition_key: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """
        Finds synthetic or inferred relationships with low confidence scores.
        """
        logging.info(
            "[COSMOS] [ANALYTICS] [START] | Message: Auditing low-confidence inferred patterns"
        )
        if partition_key:
            query = (
                "g.E().has('pk', pk).has('meta_is_inferred', true).has('confidence_score', lt(limit))"
                ".project('id', 'source', 'target', 'relation', 'score')"
                ".by('id').by(outV().id()).by(inV().id()).by(label).by('confidence_score')"
                ".limit(50)"
            )
            bindings = {"limit": threshold, "pk": partition_key}
        else:
            query = (
                "g.E().has('meta_is_inferred', true).has('confidence_score', lt(limit))"
                ".project('id', 'source', 'target', 'relation', 'score')"
                ".by('id').by(outV().id()).by(inV().id()).by(label).by('confidence_score')"
                ".limit(50)"
            )
            bindings = {"limit": threshold}
        try:
            return await self.execute_query(query, bindings)
        except Exception as e:
            logging.error(
                f"[COSMOS] [ANALYTICS] [ERROR] | Metadata: Error={str(e)} | Message: Inferred pattern audit failed"
            )
            return []

    async def sync_graph(self, graph_data: Dict[str, Any]):
        """
        Synchronizes a full graph structure to Cosmos DB with detailed progress observability.
        Filters out any nodes and edges belonging to connected components with a size less than 3.
        """
        raw_nodes = graph_data.get("nodes", [])
        raw_edges = graph_data.get("edges", []) or graph_data.get("links", [])
        
        # 1. Build adjacency list of the graph
        adj = {str(n.get("id")): set() for n in raw_nodes if n.get("id") is not None}
        for e in raw_edges:
            u = str(e.get("source"))
            v = str(e.get("target"))
            if u not in adj:
                adj[u] = set()
            if v not in adj:
                adj[v] = set()
            adj[u].add(v)
            adj[v].add(u)
            
        # 2. Find connected components using BFS
        visited = set()
        components = []
        for node_id in adj:
            if node_id not in visited:
                comp = []
                queue = [node_id]
                visited.add(node_id)
                head = 0
                while head < len(queue):
                    curr = queue[head]
                    head += 1
                    comp.append(curr)
                    for neighbor in adj[curr]:
                        if neighbor not in visited:
                            visited.add(neighbor)
                            queue.append(neighbor)
                components.append(comp)
                
        # 3. Keep only nodes belonging to components of size >= 3
        valid_nodes_set = {n for comp in components if len(comp) >= 3 for n in comp}
        
        nodes = [n for n in raw_nodes if str(n.get("id")) in valid_nodes_set]
        edges = [
            e for e in raw_edges
            if str(e.get("source")) in valid_nodes_set and str(e.get("target")) in valid_nodes_set
        ]
        
        doc_id = (
            graph_data.get("metadata", {}).get("doc_id")
            or graph_data.get("metadata", {}).get("source_file")
            or graph_data.get("doc_id")
            or "unknown"
        )

        logging.info(
            f"[COSMOS] [GRAPH_SYNC] [START] | Metadata: DocID={doc_id}, RawNodeCount={len(raw_nodes)}, RawEdgeCount={len(raw_edges)}, FilteredNodeCount={len(nodes)}, FilteredEdgeCount={len(edges)} | Message: Connected component size filtered. Beginning graph synchronization"
        )

        batch_size = settings.cosmos_batch_size
        for i in range(0, len(nodes), batch_size):
            batch = nodes[i : i + batch_size]
            logging.debug(
                f"[COSMOS] [BATCH_PROCESS] [RUNNING] | Metadata: BatchIndex={i}, BatchSize={len(batch)}, Scope=Nodes | Message: Processing node batch"
            )
            tasks = [self.upsert_node(n) for n in batch]
            await asyncio.gather(*tasks)
            logging.info(
                f"[COSMOS] [GRAPH_SYNC] [PROGRESS] | Metadata: SyncedNodes={min(i + batch_size, len(nodes))}, TotalNodes={len(nodes)} | Message: Nodes progress update"
            )
            await asyncio.sleep(0.1)

        for i in range(0, len(edges), batch_size):
            batch = edges[i : i + batch_size]
            logging.debug(
                f"[COSMOS] [BATCH_PROCESS] [RUNNING] | Metadata: BatchIndex={i}, BatchSize={len(batch)}, Scope=Edges | Message: Processing edge batch"
            )
            tasks = [self.upsert_edge(e) for e in batch]
            await asyncio.gather(*tasks)
            logging.info(
                f"[COSMOS] [GRAPH_SYNC] [PROGRESS] | Metadata: SyncedEdges={min(i + batch_size, len(edges))}, TotalEdges={len(edges)} | Message: Edges progress update"
            )
            await asyncio.sleep(0.1)

        logging.info(
            f"[COSMOS] [GRAPH_SYNC] [COMPLETE] | Metadata: DocID={doc_id} | Message: Synchronization finished successfully"
        )

    async def close(self):
        """
        Gracefully closes the connection to Cosmos DB with lifecycle tracking.
        """
        if self._client:
            logging.info(
                "[COSMOS] [CONN_TERMINATE] [START] | Message: Shutting down Gremlin driver sessions"
            )
            loop = asyncio.get_running_loop()
            try:
                await asyncio.wait_for(
                    loop.run_in_executor(None, self._client.close),
                    timeout=10.0,
                )
            except asyncio.TimeoutError:
                logging.warning(
                    "[COSMOS] [CONN_TERMINATE] [TIMEOUT] | Message: Shutting down Gremlin driver session timed out"
                )
            self._client = None
            logging.info(
                "[COSMOS] [CONN_TERMINATE] [SUCCESS] | Message: Connection resources released cleanly"
            )
