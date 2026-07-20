import logging
import json
from typing import Any, Dict
from src.storage.blob_storage import IBlobStorage
from src.storage.search_manager import SearchManager
from src.extraction.extraction_engine import _build_azure_client
from src.utils.config import settings

logger = logging.getLogger(__name__)

class RoutingEvaluator:
    """
    Evaluates the task routing accuracy of the LangGraph Coordinator.
    """
    def __init__(self, storage: IBlobStorage, search_mgr: SearchManager = None, llm_client: Any = None):
        self.storage = storage
        self.search_mgr = search_mgr
        self.llm_client = llm_client or _build_azure_client()

    async def evaluate(self, dataset_path: str = "evals/golden_routing_dataset.jsonl") -> Dict[str, Any]:
        logging.info(f"[ROUTING_EVAL] [START] | Metadata: Dataset={dataset_path} | Message: Starting routing evaluation")
        try:
            content = await self.storage.read_text(dataset_path)
            cases = [json.loads(line) for line in content.splitlines() if line.strip()]
        except Exception as e:
            logging.error(f"[ROUTING_EVAL] [ERROR] | Metadata: Error={str(e)} | Message: Failed to load dataset")
            return {"status": "error", "message": str(e)}

        total = len(cases)
        correct = 0
        over_routed = 0
        under_routed = 0
        results = []

        from src.utils.config_manager import config_manager
        system_prompt = await config_manager.get_config("route_coordinator_system.md")

        for case in cases:
            task = case["task"]
            expected = case["expected_route"].strip().lower() # 'doc_only', 'graph_only', 'parallel'

            # Query the coordinator LLM routing node logic
            user_prompt = f"Current Task: {task}"
            response = await self.llm_client.chat.completions.create(
                model=settings.graphify_agent_model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                temperature=0.0,
                max_tokens=10
            )
            raw_decision = response.choices[0].message.content.strip().lower()

            # Forgiving parsing logic mirroring src/query_agent/nodes.py:
            if "parallel" in raw_decision:
                actual = "parallel"
            elif "graph" in raw_decision:
                actual = "graph_only"
            else:
                actual = "doc_only"

            is_correct = (actual == expected)
            if is_correct:
                correct += 1
            elif expected != "parallel" and actual == "parallel":
                over_routed += 1
            elif expected == "parallel" and actual != "parallel":
                under_routed += 1

            results.append({
                "task": task,
                "expected": expected,
                "actual": actual,
                "correct": is_correct
            })

        summary = {
            "status": "success",
            "total_cases": total,
            "accuracy": (correct / total) * 100 if total > 0 else 0.0,
            "over_routing_rate": (over_routed / total) * 100 if total > 0 else 0.0,
            "under_routing_rate": (under_routed / total) * 100 if total > 0 else 0.0,
            "cases": results
        }
        logging.info(f"[ROUTING_EVAL] [SUCCESS] | Metadata: Accuracy={summary['accuracy']:.2f}% | Message: Routing evaluation complete")
        return summary
