import logging
import json
from typing import Any, Dict, List
from src.storage.blob_storage import IBlobStorage
from src.storage.search_manager import SearchManager
from src.processing.vector_indexer import generate_embeddings
from src.utils.config import settings

logger = logging.getLogger(__name__)


class RetrievalEvaluator:
    """
    Evaluates the performance of Azure AI Search (Retrieval) against a golden dataset.
    Measures Hit Rate @ K and MRR.
    """

    def __init__(self, storage: IBlobStorage, search_mgr: SearchManager, llm_client: Any = None):
        self.storage = storage
        self.search_mgr = search_mgr
        self.llm_client = llm_client

    async def evaluate(
        self, dataset_path: str = "evals/golden_dataset.jsonl"
    ) -> Dict[str, Any]:
        """
        Runs retrieval evaluation for each case in the dataset.
        """
        logging.info(
            f"[RETRIEVAL_EVAL] [START] | Metadata: Dataset={dataset_path} | Message: Starting evaluation"
        )

        try:
            content = await self.storage.read_text(dataset_path)
            cases = [json.loads(line) for line in content.splitlines() if line.strip()]
        except Exception as e:
            logging.error(
                f"[RETRIEVAL_EVAL] [ERROR] | Metadata: Error={str(e)} | Message: Failed to load dataset from blob"
            )
            return {"status": "error", "message": str(e)}

        total_cases = len(cases)
        hit_count_nodes = 0
        mrr_sum = 0

        results = []

        for case in cases:
            qa = case.get("expected_qa")
            if not qa:
                continue

            question = qa["question"]
            expected_nodes = qa.get("relevant_nodes", [])

            # Generate embedding for the question
            query_vector = await generate_embeddings([question])
            if not query_vector:
                logging.error(
                    f"[RETRIEVAL_EVAL] [ERROR] | Metadata: Question={question} | Message: Failed to generate query embedding"
                )
                continue

            # 1. Evaluate Chunk Retrieval
            node_hits, node_mrr = await self._evaluate_index(
                question,
                query_vector[0],
                settings.azure_search_index,
                expected_nodes,
                id_field="id",
            )

            # 2. Evaluate Edge Retrieval (Simple check for now)
            # ... can be extended ...

            hit_count_nodes += 1 if node_hits > 0 else 0
            mrr_sum += node_mrr

            results.append(
                {
                    "category": case.get("test_category", "default"),
                    "question": question,
                    "node_hits": node_hits,
                    "node_mrr": node_mrr,
                }
            )

        summary = {
            "total_cases": total_cases,
            "hit_rate_nodes": hit_count_nodes / total_cases if total_cases > 0 else 0,
            "mrr_nodes": mrr_sum / total_cases if total_cases > 0 else 0,
            "details": results,
        }

        logging.info(
            f"[RETRIEVAL_EVAL] [SUCCESS] | Metadata: HitRate={summary['hit_rate_nodes']:.2f} | Message: Evaluation complete"
        )
        return summary

    async def _evaluate_index(
        self,
        question: str,
        vector: List[float],
        index_name: str,
        expected_ids: List[str],
        id_field: str = "id",
    ) -> tuple:
        """Helper to search and check for expected IDs in top results."""
        from azure.search.documents.aio import SearchClient
        from azure.search.documents.models import VectorizedQuery

        if not expected_ids:
            return 1.0, 1.0  # Nothing to find

        async with SearchClient(
            endpoint=self.search_mgr.endpoint,
            index_name=index_name,
            credential=self.search_mgr.credential,
            connection_timeout=10,
            read_timeout=30,
        ) as client:
            vector_query = VectorizedQuery(
                vector=vector, k_nearest_neighbors=5, fields="embedding"
            )
            results = await client.search(
                search_text=question,
                vector_queries=[vector_query],
                select=[id_field],
                top=5,
            )

            retrieved_ids = []
            async for res in results:
                retrieved_ids.append(res[id_field])

            hits = 0
            mrr = 0
            for i, rid in enumerate(retrieved_ids):
                # Simple check: is any of the expected IDs in the result?
                # For better metrics, we check each expected ID
                if any(rid.lower() in str(eid).lower() for eid in expected_ids):
                    hits += 1
                    if mrr == 0:
                        mrr = 1 / (i + 1)

            return hits, mrr
