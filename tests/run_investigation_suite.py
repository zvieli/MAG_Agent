import asyncio
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import logging
logging.basicConfig(level=logging.DEBUG)

from src.evaluation.metrics import (  # noqa: E402
    calculate_groundedness,
    calculate_graph_recall,
    detect_conflicts,
)
from src.query_agent.query_agent import QueryAgent  # noqa: E402
from src.storage.factory import get_artifact_store  # noqa: E402
from src.utils.bidi_utils import fix_text  # noqa: E402


async def load_validation_data(doc_id: str | None = None):
    """
    Loads graph and chunk data directly from Cosmos DB for validation.

    Returns:
    Tuple of (graph_dict, chunks_dict).
    """
    from src.storage.factory import get_cosmos_manager
    cosmos_mgr = get_cosmos_manager()

    # 1. Fetch vertices from Cosmos DB
    if doc_id:
        print(f"INFO: Loading validation graph vertices from Cosmos DB for partitionKey: {doc_id}")
        vertices_query = "g.V().has('partitionKey', pk)"
        bindings = {"pk": doc_id}
    else:
        print("INFO: Loading validation graph vertices from Cosmos DB (all partition keys)...")
        vertices_query = "g.V()"
        bindings = {}

    try:
        vertices = await cosmos_mgr.execute_query(vertices_query, bindings)
    except Exception as e:
        print(f"WARNING: Failed to load vertices from Cosmos DB: {e}")
        vertices = []

    nodes = []
    for v in vertices:
        v_clean = {
            "id": v.get("id"),
            "label": v.get("label"),
            "type": "Entity",
            "metadata": {}
        }
        # Extract properties
        properties = v.get("properties", {})
        for prop_name, prop_list in properties.items():
            if isinstance(prop_list, list) and len(prop_list) > 0:
                val = prop_list[0].get("value")
                if prop_name == "type":
                    v_clean["type"] = val
                elif prop_name.startswith("meta_"):
                    meta_key = prop_name[5:]
                    v_clean["metadata"][meta_key] = val
                else:
                    v_clean[prop_name] = val
        nodes.append(v_clean)

    # 2. Fetch edges from Cosmos DB
    if doc_id:
        print(f"INFO: Loading validation graph edges from Cosmos DB for partitionKey: {doc_id}")
        edges_query = "g.E().has('partitionKey', pk)"
        bindings = {"pk": doc_id}
    else:
        print("INFO: Loading validation graph edges from Cosmos DB (all partition keys)...")
        edges_query = "g.E()"
        bindings = {}

    try:
        edges = await cosmos_mgr.execute_query(edges_query, bindings)
    except Exception as e:
        print(f"WARNING: Failed to load edges from Cosmos DB: {e}")
        edges = []

    links = []
    for e in edges:
        e_clean = {
            "id": e.get("id"),
            "relation": e.get("label"),
            "source": e.get("outV"),
            "target": e.get("inV"),
            "metadata": {}
        }
        properties = e.get("properties", {})
        for prop_name, val in properties.items():
            e_clean[prop_name] = val
        links.append(e_clean)

    graph = {
        "nodes": nodes,
        "links": links,
        "edges": links
    }
    return (graph, {})


QUESTIONS = [
"מי החשודים העיקריים"]


async def run_benchmark(target_question_id: int | None = None):
    # Resolve the active doc_id from Cosmos DB dynamically
    from src.storage.factory import get_cosmos_manager
    cosmos_mgr = get_cosmos_manager()
    try:
        pks = await cosmos_mgr.execute_query("g.V().values('partitionKey').dedup()")
        doc_id = pks[0] if pks else None
    except Exception as e:
        print(f"WARNING: Failed to query partition keys from Cosmos DB: {e}")
        doc_id = None

    agent = QueryAgent(doc_id=doc_id)
    graph, chunks = await load_validation_data(doc_id)
    results = []
    summary_stats = []

    questions_to_run = []
    if target_question_id is not None:
        if 1 <= target_question_id <= len(QUESTIONS):
            questions_to_run = [(target_question_id, QUESTIONS[target_question_id - 1])]
        else:
            print(f"Error: Question ID {target_question_id} out of range.")
            return
    else:
        questions_to_run = list(enumerate(QUESTIONS, 1))

    print(
        fix_text(
            str(f"🚀 מתחיל הרצת בנצ'מרק חקירתי ({len(questions_to_run)} שאלות)...")
        )
    )
    async with agent:
        for i, question in questions_to_run:
            print(
                fix_text(str(f"🔍 חוקר שאלה {i}/{len(QUESTIONS)}: {question[:50]}..."))
            )
            try:
                response = await agent.answer_question(question)
                groundedness = await calculate_groundedness(response)
                recall = calculate_graph_recall(response, graph, question)
                conflicts = detect_conflicts(response, graph)
                summary_stats.append(
                    {
                        "id": i,
                        "question": question[:50] + "...",
                        "groundedness": groundedness,
                        "recall": recall,
                        "conflicts": ", ".join(conflicts) if conflicts else "None",
                    }
                )
                results.append(f"## שאלה {i}: {question}\n\n{response}\n\n---\n")
            except Exception as e:
                print(fix_text(str(f"❌ שגיאה בשאלה {i}: {e}")))
                results.append(
                    f"## שאלה {i}: {question}\n\nשגיאה בעת המענה: {e}\n\n---\n"
                )
                summary_stats.append(
                    {
                        "id": i,
                        "question": question[:50] + "...",
                        "groundedness": 0.0,
                        "recall": 0.0,
                        "conflicts": "ERROR",
                    }
                )
    dashboard = "\n# לוח מחוונים (Validation Dashboard)\n\n"
    dashboard += "| # | שאלה | Groundedness | Recall | Conflicts |\n"
    dashboard += "|---|-------|--------------|--------|-----------|\n"
    for s in summary_stats:
        dashboard += f"| {s['id']} | {s['question']} | {s['groundedness']:.1f}% | {s['recall']:.1f}% | {s['conflicts']} |\n"
    avg_groundedness = sum((s["groundedness"] for s in summary_stats)) / len(
        summary_stats
    )
    avg_recall = sum((s["recall"] for s in summary_stats)) / len(summary_stats)
    dashboard += f"\n**ממוצע כולל: Groundedness {avg_groundedness:.1f}% | Recall {avg_recall:.1f}%**\n"
    report_name = (
        f"reports/investigation_report_{datetime.now().strftime('%Y%m%d_%H%M')}.md"
    )
    store = get_artifact_store()
    report_content = (
        '# דו"ח חקירה אוטומטי - תיקי האלפים\n\n' + "\n".join(results) + dashboard
    )
    store.write_bytes(report_name, report_content.encode("utf-8"))
    print(fix_text(str(f'✅ החקירה הסתיימה. הדו"ח נשמר בענן בנתיב: {report_name}')))


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Run Investigation Suite Benchmark")
    parser.add_argument(
        "--question", type=int, help="ID of a specific question to run (1-based)"
    )
    args = parser.parse_args()

    asyncio.run(run_benchmark(target_question_id=args.question))
