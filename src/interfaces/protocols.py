from typing import Any, Protocol, List, Dict, Tuple
from pathlib import Path
from contextlib import asynccontextmanager
import networkx as nx


class KnowledgeProvider(Protocol):
    """Abstract interface for providing graph data and analytical artifacts to agents.

    This protocol decouples agents from specific storage formats (JSON, Neo4j, etc.)
    and supports both batch loading and streaming retrieval for scalability.
    """

    def get_graph_data(self) -> Dict[str, Any]:
        """Returns the full node-link graph structure."""
        ...

    def get_analysis_metadata(self) -> Dict[str, Any]:
        """Returns calculated metrics like community counts and God Nodes."""
        ...

    def get_structural_report(self) -> str:
        """Returns a Markdown summary of the graph's health."""
        ...

    def get_vector_index_path(self, index_type: str) -> Path:
        """Returns the local path to the requested vector index."""
        ...

    def get_raw_document_path(self) -> Path:
        """Returns the path to the original source document."""
        ...

    def stream_nodes(self, chunk_size: int = 1000):
        """Generator yielding nodes in batches for large-scale processing."""
        ...

    def stream_edges(self, chunk_size: int = 1000):
        """Generator yielding edges in batches for large-scale processing."""
        ...

    def query_nodes(self, filter_params: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Retrieves nodes matching specific filter criteria."""
        ...

    def query_edges(self, filter_params: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Retrieves edges matching specific filter criteria."""
        ...


class ExtractionStrategy(Protocol):
    """Abstract strategy for extracting graph data from document chunks."""

    async def extract(
        self, chunk_text: str, chunk_label: str
    ) -> Tuple[Any, Dict[str, int]]:
        """Performs graph extraction on a text chunk.

        Args:
            chunk_text: The raw text to analyze.
            chunk_label: A unique identifier for the chunk.

        Returns:
            A tuple of (Extraction Object, Token Usage Statistics).
        """
        ...


class AnalysisStrategy(Protocol):
    """Abstract strategy for running deep graph analysis."""

    def run_analysis(
        self, G: nx.Graph, communities: Dict[int, List[str]]
    ) -> Dict[str, Any]:
        """Executes analytical logic on a NetworkX graph.

        Args:
            G: The NetworkX graph object.
            communities: A mapping of community IDs to lists of node IDs.

        Returns:
            A dictionary containing analysis results (e.g., God Nodes).
        """
        ...


class PersistenceProvider(Protocol):
    """Abstract interface for persisting pipeline artifacts to disk or cloud."""

    async def save_json(self, data: Any, filename: str):
        """Serializes data to a JSON file."""
        ...

    async def save_text(self, text: str, filename: str):
        """Writes text content to a file."""
        ...

    async def save_stream(self, stream: Any, filename: str):
        """Writes a text stream to a file."""
        ...

    @asynccontextmanager
    async def get_temp_path(self, filename: str):
        """Returns the absolute path for a given filename in storage."""
        ...


class EntityResolver(Protocol):
    """Abstract interface for resolving entities across multiple extractions."""

    async def resolve(
        self,
        nodes: List[Dict[str, Any]],
        edges: List[Dict[str, Any]],
        client: Any = None,
    ) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], Dict[str, Any]]:
        """Deduplicates nodes and remaps edges.

        Args:
            nodes: List of raw node dictionaries.
            edges: List of raw edge dictionaries.
            client: Optional LLM client for semantic disambiguation.

        Returns:
            A tuple of (Resolved Nodes, Remapped Edges, Resolution Metadata).
        """
        ...


class StructuralInferrer(Protocol):
    """Abstract interface for inferring implicit connections in the graph."""

    def infer(
        self, nodes: List[Dict[str, Any]], edges: List[Dict[str, Any]], source_file: str
    ) -> List[Dict[str, Any]]:
        """Identifies and creates new edges based on structural logic.

        Returns:
            A list of newly inferred edges.
        """
        ...


class VectorIndexer(Protocol):
    """Abstract interface for indexing graph data for semantic search."""

    async def index(self, graph_data: Dict[str, Any], filename: str):
        """Generates embeddings and saves a vector search index."""
        ...


class Reporter(Protocol):
    """Abstract interface for generating human-readable investigative reports."""

    async def generate(
        self,
        G: nx.Graph,
        communities: Dict[int, List[str]],
        graph_data: Dict[str, Any],
        pages: List[Dict[str, Any]],
        source_file: str,
    ):
        """Synthesizes graph and document data into a Markdown report."""
        ...
