import asyncio
import os
import sys
import argparse
from pathlib import Path

# Add project root to sys.path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# Force line-buffering on stdout/stderr to ensure logs flush immediately when redirected
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(line_buffering=True)
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(line_buffering=True)

from src.storage.factory import get_artifact_store, get_search_manager
from src.extraction.extraction_engine import _build_azure_client

from src.evaluation.rag_evaluator import RagEvaluator
from src.evaluation.retrieval_evaluator import RetrievalEvaluator
from src.evaluation.routing_evaluator import RoutingEvaluator
from src.evaluation.extraction_evaluator import ExtractionEvaluator

MODES = {
    "rag": RagEvaluator,
    "retrieval": RetrievalEvaluator,
    "routing": RoutingEvaluator,
    "extraction": ExtractionEvaluator
}

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

async def run_evals(mode: str, dataset_path: str):
    import logging
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        stream=sys.stdout,
        force=True
    )
    print("=" * 60)
    print(f"🚀 STARTING EVALUATION SUITE: {mode.upper()} MODE")
    print(f"Dataset Path: {dataset_path}")
    print("=" * 60)

    if mode not in MODES:
        print(f"❌ Unknown mode: {mode}")
        return

    local_store = LocalOrStoreReader()
    search_mgr = get_search_manager()
    llm_client = _build_azure_client()

    evaluator_class = MODES[mode]
    evaluator = evaluator_class(
        storage=local_store,
        search_mgr=search_mgr,
        llm_client=llm_client
    )

    try:
        summary = await evaluator.evaluate(dataset_path)
    except Exception as e:
        err_msg = str(e)
        print(f"❌ Error executing evaluations: {err_msg}")
        if "403" in err_msg or "firewall" in err_msg.lower() or "forbidden" in err_msg.lower():
            print("\n" + "!" * 15 + " AZURE FIREWALL BLOCK DETECTED " + "!" * 15)
            print("Access was denied due to Azure OpenAI Virtual Network/Firewall rules.")
            try:
                import urllib.request
                public_ip = urllib.request.urlopen("https://api.ipify.org", timeout=3).read().decode("utf-8")
                print(f"\n👉 Your current public IP is: {public_ip}")
            except Exception:
                public_ip = "Unknown"
                print("\n👉 Could not retrieve public IP.")
            print("\n👉 Troubleshooting Actions:")
            print("   1. Verify if you need to connect to the organization VPN.")
            print("   2. Whitelist your public IP in the Azure Portal under your Azure OpenAI resource -> 'Networking' tab.")
            print("=" * 60)
        return

    if not summary or summary.get("status") == "error":
        print("❌ Evaluation failed. Check logs or dataset file presence.")
        return

    # Print results custom to each mode
    if mode == "rag":
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

    elif mode == "retrieval":
        print("\n" + "=" * 25 + " RETRIEVAL SUMMARY " + "=" * 25)
        print(f"📊 Total Cases Evaluated:       {summary.get('total_cases', 0)}")
        print(f"📊 Hit Rate @ K (Chunks):       {summary.get('hit_rate_nodes', 0.0) * 100:.2f}%")
        print(f"📊 MRR (Mean Reciprocal Rank): {summary.get('mrr_nodes', 0.0):.4f}")
        print("=" * 67)

    elif mode == "routing":
        print("\n" + "=" * 25 + " ROUTING SUMMARY " + "=" * 25)
        print(f"📊 Total Cases Evaluated:       {summary.get('total_cases', 0)}")
        print(f"📊 Routing Accuracy:           {summary.get('accuracy', 0.0):.2f}%")
        print(f"📊 Over-Routing Rate (Parallel):{summary.get('over_routing_rate', 0.0):.2f}%")
        print(f"📊 Under-Routing Rate (Missed): {summary.get('under_routing_rate', 0.0):.2f}%")
        print("=" * 67)

        print("\n" + "=" * 25 + " DETAILED CASES " + "=" * 25)
        for idx, case in enumerate(summary.get("cases", []), 1):
            print(f"\n[Case #{idx}] Task: {case['task']}")
            print(f"   Expected Route: {case['expected']}")
            print(f"   Actual Route:   {case['actual']}")
            print(f"   Status:         {'✅ Correct' if case['correct'] else '❌ Incorrect'}")
            print("-" * 67)

    elif mode == "extraction":
        print("\n" + "=" * 25 + " EXTRACTION SUMMARY " + "=" * 25)
        print(f"📊 Total Cases Evaluated:       {summary.get('total_cases', 0)}")
        print(f"📊 Average Entity/Relation Precision: {summary.get('avg_precision', 0.0) * 100:.2f}%")
        print(f"📊 Average Entity/Relation Recall:    {summary.get('avg_recall', 0.0) * 100:.2f}%")
        print(f"📊 Average Epistemic Accuracy:        {summary.get('avg_epistemic_acc', 0.0) * 100:.2f}%")
        print("=" * 67)

        print("\n" + "=" * 25 + " DETAILED CASES " + "=" * 25)
        for idx, case in enumerate(summary.get("cases", []), 1):
            print(f"\n[Case #{idx}] Category: {case['category']}")
            print(f"   Precision: {case['precision']:.2f}, Recall: {case['recall']:.2f}, Epistemic Acc: {case['epistemic_acc']:.2f}")
            print(f"   Extracted Entities: {case['extracted_entities']}")
            print(f"   Expected Entities:  {case['expected_entities']}")
            print(f"   Extracted Relations: {case['extracted_relations']}")
            print(f"   Expected Relations:  {case['expected_relations']}")
            print("-" * 67)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run MAG Agent RAG, Retrieval, Routing, and Extraction Evaluations")
    parser.add_argument(
        "--mode",
        type=str,
        default="rag",
        choices=["rag", "retrieval", "routing", "extraction"],
        help="Evaluation mode to execute (default: rag)"
    )
    parser.add_argument(
        "--dataset",
        type=str,
        default=None,
        help="Path to the JSONL evaluation dataset on disk. If not provided, defaults to the mode's standard dataset."
    )
    args = parser.parse_args()

    # Determine default dataset based on mode
    dataset = args.dataset
    if not dataset:
        if args.mode == "routing":
            dataset = "evals/golden_routing_dataset.jsonl"
        elif args.mode == "extraction":
            dataset = "evals/golden_extraction.jsonl"
        else:
            dataset = "evals/golden_dataset_1128.jsonl"

    asyncio.run(run_evals(args.mode, dataset))
