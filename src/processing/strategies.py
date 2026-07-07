from typing import Any, Dict, Tuple, List
import networkx as nx
from src.interfaces.protocols import PersistenceProvider
from src.extraction.extraction_engine import extract_from_chunk
from src.graph.local_graphify import god_nodes, surprising_connections
from src.graph.entity_resolver import (
    resolve_entities_via_blocking,
    _apply_entity_resolution_to_extraction,
)
from src.graph.graph_manager import _dedupe_extracted_edges
from src.processing.vector_indexer import index_edge_labels
from src.graph.local_graphify import generate
from src.processing.document_processor import _count_words


class LLMExtractionStrategy:
    """
    Standard extraction strategy using the LLM engine for graph construction.

    This strategy encapsulates the logic for calling the extraction engine
    and managing its client and configuration.
    """

    def __init__(self, client: Any, schema_config: Any = None):
        """
        Initialize the extraction strategy.

        Args:
            client (Any): The LLM client (OpenAI or AzureOpenAI).
            schema_config (Any, optional): Configuration for extraction rules.
        """
        self.client = client
        self.schema_config = schema_config

    async def extract(
        self, chunk_text: str, chunk_label: str
    ) -> Tuple[Any, Dict[str, int]]:
        """
        Execute the extraction process for a single chunk.

        Args:
            chunk_text (str): The text content to process.
            chunk_label (str): A label for the chunk.

        Returns:
            Tuple[Any, Dict[str, int]]: The extracted graph data and token usage.
        """
        return await extract_from_chunk(
            self.client, chunk_text, self.schema_config, chunk_label=chunk_label
        )


class DeepGraphAnalysisStrategy:
    """
    Standard investigative analysis strategy using the Graphify library.

    Provides specialized metrics like God Nodes and Surprising Connections
    to support forensic discovery.
    """

    def run_analysis(
        self, G: nx.Graph, communities: Dict[int, List[str]]
    ) -> Dict[str, Any]:
        """
        Run deep structural analysis on the graph.

        Args:
            G (nx.Graph): The investigative graph.
            communities (Dict[int, List[str]]): Community clusters.

        Returns:
            Dict[str, Any]: Analysis results containing God Nodes and Surprising Connections.
        """
        return {
            "god_nodes": god_nodes(G),
            "surprising_connections": surprising_connections(G, communities),
        }


class DefaultEntityResolver:
    """
    Standard entity resolution using blocking and multi-pass alignment.

    This resolver handles the remapping of aliases and deduplication of edges
    while preserving investigative provenance.
    """

    def __init__(self, schema_config: Any = None):
        """
        Initialize the entity resolver.

        Args:
            schema_config (Any, optional): Configuration for resolution thresholds.
        """
        self.schema_config = schema_config
        self.semantic_class_markers = (
            getattr(schema_config, "semantic_class_markers", {}) or {}
        )
        self.semantic_class_patterns = (
            getattr(schema_config, "semantic_class_patterns", {}) or {}
        )
        self.semantic_class_discriminator_patterns = (
            getattr(schema_config, "semantic_class_discriminator_patterns", {}) or {}
        )
        self.semantic_class_type_map = (
            getattr(schema_config, "semantic_class_type_map", {}) or {}
        )

    async def resolve(
        self,
        nodes: List[Dict[str, Any]],
        edges: List[Dict[str, Any]],
        client: Any = None,
    ) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], Dict[str, Any]]:
        """
        Resolve entity duplicates across the provided nodes and edges.

        Args:
            nodes (List[Dict[str, Any]]): List of raw nodes.
            edges (List[Dict[str, Any]]): List of raw edges.
            client (Any, optional): Optional LLM client for assisted refinement.

        Returns:
            Tuple[List[Dict[str, Any]], List[Dict[str, Any]], Dict[str, Any]]:
                Resolved nodes, deduplicated edges, and a resolution report.
        """
        resolution_mapping = await resolve_entities_via_blocking(
            nodes, edges, client=client, schema_config=self.schema_config
        )
        resolved_nodes, resolved_edges, resolution_report = (
            _apply_entity_resolution_to_extraction(
                nodes,
                edges,
                resolution_mapping,
                schema_config=self.schema_config,
                llm_report=getattr(resolve_entities_via_blocking, "last_report", None),
            )
        )
        return (
            resolved_nodes,
            _dedupe_extracted_edges(resolved_edges, self.schema_config),
            resolution_report,
        )


class DefaultStructuralInferrer:
    """
    Infers latent connections between actors and events based on shared context.

    Bridges nodes that appear in the same document locations or share high
    metadata overlap even if no explicit relation was extracted.
    """

    def __init__(self, schema_config: Any = None):
        """
        Initialize the structural inferrer.

        Args:
            schema_config (Any, optional): Configuration for inference rules.
        """
        self.schema_config = schema_config

    def infer(
        self, nodes: List[Dict[str, Any]], edges: List[Dict[str, Any]], source_file: str
    ) -> List[Dict[str, Any]]:
        """
        Infer new relationships based on topological and metadata context.

        Args:
            nodes (List[Dict[str, Any]]): Resolved nodes.
            edges (List[Dict[str, Any]]): Current edges.
            source_file (str): Source document identifier.

        Returns:
            List[Dict[str, Any]]: The enriched list of edges including inferred relations.
        """
        actor_types = {t.lower() for t in self.schema_config.human_entity_types} | {
            t.lower() for t in self.schema_config.organization_entity_types
        }
        event_types = {t.lower() for t in self.schema_config.event_entity_types} | {
            "context"
        }
        connected_ids = {edge["source"] for edge in edges} | {
            edge["target"] for edge in edges
        }
        actor_nodes = [
            n for n in nodes if str(n.get("type", "")).lower() in actor_types
        ]
        event_nodes = [
            n for n in nodes if str(n.get("type", "")).lower() in event_types
        ]

        inferred = []
        for event_node in event_nodes:
            if event_node["id"] not in connected_ids:
                ev_loc = event_node.get("source_location")
                if not ev_loc or ev_loc == "unknown":
                    continue
                ev_chunks = {c.strip() for c in str(ev_loc).split(";")}
                for actor_node in actor_nodes:
                    p_loc = actor_node.get("source_location")
                    if p_loc and p_loc != "unknown":
                        p_chunks = {c.strip() for c in str(p_loc).split(";")}
                        if ev_chunks.intersection(p_chunks):
                            inferred.append(
                                {
                                    "source": actor_node["id"],
                                    "target": event_node["id"],
                                    "relation": "RELATED_TO",
                                    "confidence": "INFERRED",
                                    "confidence_score": 0.6,
                                    "metadata": {"auto_bridge": True},
                                    "source_file": source_file,
                                    "source_location": "; ".join(
                                        ev_chunks.intersection(p_chunks)
                                    ),
                                }
                            )
        if inferred:
            edges.extend(inferred)
        return _dedupe_extracted_edges(edges, self.schema_config)


class DefaultVectorIndexer:
    """
    Generates semantic embeddings for investigative artifacts.
    """

    def __init__(self, persistence: PersistenceProvider = None):
        self.persistence = persistence

    async def index(self, graph_data: Dict[str, Any], filename: str):
        """
        Generate and persist semantic indices.
        """
        from src.processing.vector_indexer import index_chunks

        await index_edge_labels(graph_data, filename, self.persistence)
        await index_chunks(persistence=self.persistence)


class DefaultReporter:
    """
    Produces Markdown summaries with deep investigative insights.
    """

    def __init__(self, persistence: PersistenceProvider):
        self.persistence = persistence

    async def generate(
        self,
        G: nx.Graph,
        communities: Dict[int, List[str]],
        graph_data: Dict[str, Any],
        pages: List[Dict[str, Any]],
        source_file: str,
    ):
        """
        Generate all investigative reports for a processed document.
        """
        import asyncio

        def _compute():
            total_words = sum((_count_words(page.get("text", "")) for page in pages))
            
            # Dynamic calculation fallbacks to prevent KeyError when "graph" is missing in graph_data
            from src.graph.local_graphify import score_all, god_nodes, surprising_connections
            
            cohesion_fallback = score_all(G, communities)
            god_nodes_fallback = god_nodes(G, top_n=10)
            surprising_connections_fallback = surprising_connections(G, communities)
            labels_fallback = {str(c): f"Community {c}" for c in communities}
            
            analysis = graph_data.get("graph") or {}
            analysis_cohesion = analysis.get("cohesion") or cohesion_fallback
            analysis_labels = analysis.get("community_labels") or labels_fallback
            analysis_gods = analysis.get("god_nodes") or god_nodes_fallback
            analysis_surprising = analysis.get("surprising_connections") or surprising_connections_fallback
            
            token_usage = graph_data.get("metadata", {}).get("token_usage") or {"total_tokens": 0}

            md_report = generate(
                G,
                communities,
                analysis_cohesion,
                analysis_labels,
                analysis_gods,
                analysis_surprising,
                {"total_files": 1, "total_words": total_words},
                token_usage,
                source_file,
            )

            insight_md = "\n\n## Deep Investigative Insights (SOLID Refactor)\n"
            insight_md += "### God Nodes (High Centrality)\n"
            for g in analysis_gods[:5]:
                insight_md += f"- **{g['label']}** (Degree: {g['degree']}): {g.get('description', '')[:100]}...\n"

            insight_md += "\n### Surprising Connections\n"
            for s in analysis_surprising[:5]:
                s_src = s.get("source") or s.get("source_label") or "Unknown"
                s_tgt = s.get("target") or s.get("target_label") or "Unknown"
                s_score = s.get("_score") or s.get("score") or 0.0
                insight_md += f"- {s_src} <-> {s_tgt} (Score: {s_score:.2f})\n"

            return md_report + insight_md

        report_content = await asyncio.to_thread(_compute)
        await self.persistence.save_text(report_content, "GRAPH_REPORT.md")
