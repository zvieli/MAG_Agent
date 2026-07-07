import random
import asyncio
import logging
from typing import List, Dict, Any
from src.extraction.extraction_engine import _build_azure_client
from src.utils.config import settings

logger = logging.getLogger(__name__)


class GraphEvaluator:
    """
    Evaluates the quality of a generated Knowledge Graph.

    This component uses a two-layered evaluation approach:
    1. Structural Topography: Deterministic metrics measuring graph density,
       isolated nodes, and ontology compliance.
    2. Semantic LLM-as-a-Judge: Factual correctness and investigative grounding
       verified against the original source document chunks.
    """

    def __init__(
        self,
        graph_data: Dict[str, Any],
        chunks: List[Dict[str, Any]],
        schema_config: Any,
    ):
        """
        Initializes the GraphEvaluator.

        Args:
            graph_data: The Unified Knowledge Graph dictionary.
            chunks: List of document chunks used for extraction.
            schema_config: Configuration containing entity and relation types.
        """
        self.graph_data = graph_data
        self.nodes = graph_data.get("nodes", [])
        self.edges = graph_data.get("edges", [])
        self.chunks = {chunk["label"]: chunk["text"] for chunk in chunks}
        self.schema_config = schema_config
        self.client = _build_azure_client()

    def calculate_structural_metrics(self) -> Dict[str, float]:
        """
        Layer 1: Deterministic structural metrics.

        Calculates isolated nodes ratio, edge density, and compliance with the
        defined ontology (valid entity and relation types).

        Returns:
            Dictionary containing 'isolated_nodes_ratio', 'edge_to_node_density',
            and 'ontology_compliance_score'.
        """
        if not self.nodes:
            return {
                "isolated_nodes_ratio": 0.0,
                "edge_to_node_density": 0.0,
                "ontology_compliance_score": 0.0,
            }

        # Node degrees
        degrees = {node["id"]: 0 for node in self.nodes}
        for edge in self.edges:
            src, tgt = edge.get("source"), edge.get("target")
            if src in degrees:
                degrees[src] += 1
            if tgt in degrees:
                degrees[tgt] += 1

        isolated_count = sum(1 for d in degrees.values() if d == 0)
        isolated_nodes_ratio = isolated_count / len(self.nodes)
        edge_to_node_density = len(self.edges) / len(self.nodes)

        # Ontology Compliance
        valid_node_types = set(self.schema_config.entity_types.keys())
        valid_rel_types = set(self.schema_config.relation_types.keys())

        node_compliance = sum(
            1 for n in self.nodes if n.get("type") in valid_node_types
        )
        edge_compliance = sum(
            1 for e in self.edges if e.get("relation") in valid_rel_types
        )

        total_elements = len(self.nodes) + len(self.edges)
        ontology_compliance_score = (
            (node_compliance + edge_compliance) / total_elements
            if total_elements > 0
            else 0.0
        )

        return {
            "isolated_nodes_ratio": round(isolated_nodes_ratio, 4),
            "edge_to_node_density": round(edge_to_node_density, 4),
            "ontology_compliance_score": round(ontology_compliance_score, 4),
        }

    async def evaluate_factual_accuracy(self, sample_size: int = 10) -> float:
        """
        Layer 2: Semantic LLM-as-a-Judge factual correctness.

        Randomly samples edges from the graph and asks a judge LLM to verify
        their faithfulness against the specific source chunk they were extracted from.

        Args:
            sample_size: Number of edges to sample for evaluation.

        Returns:
            A score (0.0 to 1.0) representing the ratio of faithful edges.
        """
        if not self.edges:
            return 1.0

        # Filter edges that have a source_location we can map to a chunk
        valid_edges = []
        for e in self.edges:
            loc = e.get("source_location", "")
            # Extract chunk label (e.g., "chunk_5: s3" -> "chunk_5")
            chunk_label = loc.split(":")[0].strip() if loc else ""
            if chunk_label in self.chunks:
                valid_edges.append((e, chunk_label))

        if not valid_edges:
            logging.warning(
                "[GRAPH_EVALUATOR] [FAITHFULNESS] [SKIP] | Message: No edges found with valid source_location mapping"
            )
            return 0.0

        sample = random.sample(valid_edges, min(sample_size, len(valid_edges)))
        scores = []

        logging.info(
            f"[GRAPH_EVALUATOR] [FAITHFULNESS] [START] | Metadata: SampleSize={len(sample)} | Message: Starting LLM judge verification"
        )

        tasks = [self._judge_edge(edge, chunk_label) for edge, chunk_label in sample]
        results = await asyncio.gather(*tasks)
        scores = [r for r in results if r is not None]

        if not scores:
            return 0.0

        return round(sum(scores) / len(scores), 4)

    async def _judge_edge(self, edge: Dict[str, Any], chunk_label: str) -> int | None:
        """
        Prompts the judge model to evaluate a single edge's faithfulness.

        This implementation uses updated criteria: 'Inference is NOT Hallucination'.
        Investigative grounding is accepted if the text provides the 'building blocks'
        for the relationship, even if the connection is circumstantial or analytical
        (e.g., Quid Pro Quo based on temporal proximity).

        Args:
            edge: The edge dictionary to verify.
            chunk_label: The ID of the chunk containing the source text.

        Returns:
            1 if faithful, 0 if hallucinatory, None on error.
        """
        chunk_text = self.chunks[chunk_label]
        edge_desc = (
            f"{edge.get('source')} --({edge.get('relation')})--> {edge.get('target')}"
        )
        forensic_summary = edge.get("forensic_summary", "No summary provided")

        prompt = (
            "You are an investigative auditor. Your task is to verify if an extracted relationship (edge) "
            "between two entities is factually supported by the provided source text chunk.\n\n"
            f"SOURCE TEXT CHUNK ({chunk_label}):\n{chunk_text}\n\n"
            f"EXTRACTED EDGE:\n{edge_desc}\n"
            f"SUMMARY: {forensic_summary}\n\n"
            "CRITERIA:\n"
            "1. INVESTIGATIVE INFERENCE: Inference is NOT hallucination. If the text provides the 'building blocks' "
            "(e.g., specific dates, names, and meetings) that reasonably support the analytical synthesis in the edge, "
            "it is considered grounded.\n"
            "2. CIRCUMSTANTIAL LINKS: Relationships like 'Quid Pro Quo' or 'Awareness' are often circumstantial. "
            "If the text describes a temporal proximity or a pattern that supports this link, mark it as faithful.\n"
            "3. HALLUCINATION DEFINITION: Hallucination strictly means inventing names, dates, or events that "
            "do not exist in the source text. Do NOT penalize logical deductions derived from actual facts.\n"
            "4. If the relationship is fundamentally misrepresented or contradicts the text, return 0.\n\n"
            "Return exactly '1' if the edge is faithful (including valid investigative inferences) and '0' if it is not. "
            "Provide NO other text."
        )

        try:
            response = await self.client.chat.completions.create(
                model=settings.graphify_extraction_model,
                messages=[
                    {
                        "role": "system",
                        "content": "You are a factual correctness judge.",
                    },
                    {"role": "user", "content": prompt},
                ],
                temperature=0.0,
                max_tokens=10,
            )
            content = response.choices[0].message.content.strip()
            if "1" in content:
                return 1
            if "0" in content:
                return 0
            return None
        except Exception as e:
            logging.error(
                f"[GRAPH_EVALUATOR] [JUDGE_ERROR] | Metadata: Chunk={chunk_label}, Error={str(e)}"
            )
            return None
