import asyncio
import os
import sys
import argparse
from pathlib import Path

# Add project root to sys.path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.evaluation.rag_evaluator import RagEvaluator
from src.storage.factory import get_artifact_store, get_search_manager
from src.extraction.extraction_engine import _build_azure_client

class LocalOrStoreReader:
    """
    Wrapper for artifact storage that checks local filesystem first
    before falling back to cloud storage.
    """
    def __init__(self):
        self.store = None

    async def read_text(self, path: str) -> str:
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                return f.read()
        if self.store is None:
            self.store = get_artifact_store()
        return await self.store.read_text(path)

async def run_evals(dataset_path: str):
    print("=" * 60)
    print(f"🚀 STARTING RAG EVALUATION SUITE")
    print(f"Dataset Path: {dataset_path}")
    print("=" * 60)

    local_store = LocalOrStoreReader()
    search_mgr = get_search_manager()
    llm_client = _build_azure_client()

    evaluator = RagEvaluator(
        storage=local_store,
        search_mgr=search_mgr,
        llm_client=llm_client
    )

    try:
        summary = await evaluator.evaluate(dataset_path)
    except Exception as e:
        print(f"❌ Error executing evaluations: {e}")
        return

    if not summary or summary.get("status") == "error":
        print("❌ Evaluation failed. Check logs or dataset file presence.")
        return

    print("\n" + "=" * 25 + " METRICS SUMMARY " + "=" * 25)
    print(f"📊 Average Groundedness (Citations):     {summary.get('avg_groundedness', 0.0):.2f}%")
    print(f"📊 Average Hallucination Score (Grounded): {summary.get('avg_hallucination_score', 0.0) * 100:.2f}%")
    print("=" * 67)

    print("\n" + "=" * 25 + " DETAILED CASES " + "=" * 25)
    for idx, case in enumerate(summary.get("cases", []), 1):
        print(f"\n[Case #{idx}] Question: {case['question']}")
        print(f"   Expected Answer:  {case['expected']}")
        print(f"   Generated Answer: {case['generated']}")
        print(f"   - Citation Groundedness: {case['groundedness']:.2f}%")
        print(f"   - Answer Similarity:     {case['similarity'] * 100:.2f}%")
        print(f"   - Grounding Check (LLM): {case['hallucination_score'] * 100:.2f}%")
        print(f"   - Reason/Notes:          {case['hallucination_reason']}")
        print("-" * 67)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run MAG Agent RAG Evaluations")
    parser.add_argument(
        "--dataset",
        type=str,
        default="evals/golden_dataset.jsonl",
        help="Path to the JSONL evaluation dataset on disk (e.g. evals/golden_dataset_1128.jsonl)"
    )
    args = parser.parse_args()
    asyncio.run(run_evals(args.dataset))
