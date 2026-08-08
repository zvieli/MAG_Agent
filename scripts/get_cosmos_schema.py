import argparse
import asyncio
import os
import sys
from typing import Any, Dict


SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(SCRIPT_DIR, ".."))

if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

# Re-execute with the project interpreter so Gremlin dependencies match the app.
venv_python = os.path.join(PROJECT_ROOT, ".venv", "bin", "python")
if not sys.prefix.endswith(".venv") and os.path.exists(venv_python) and sys.executable != venv_python:
    os.execv(venv_python, [venv_python] + sys.argv)

from src.graph.cosmos_manager import (  # noqa: E402
    DUPLICATE_MATCH_FIELDS,
    CosmosGremlinManager,
    duplicate_lookup_query,
)


def _scalar(value: Any) -> Any:
    if isinstance(value, list) and value:
        first = value[0]
        if isinstance(first, dict) and "value" in first:
            return first.get("value")
        return first
    return value


def _safe_vertex_summary(vertex: Dict[str, Any]) -> Dict[str, Any]:
    properties = vertex.get("properties") or {}
    return {
        "id": str(vertex.get("id") or ""),
        "label": str(
            _scalar(properties.get("label"))
            or _scalar(properties.get("name"))
            or vertex.get("label")
            or ""
        ),
        "type": str(_scalar(properties.get("type")) or vertex.get("label") or ""),
        "matched_value": _scalar(vertex.get("matched_value")),
    }


async def inspect_document(cosmos_mgr: CosmosGremlinManager, doc_id: str) -> None:
    print(f"--- Exact Duplicate Preflight Lookup: {doc_id} ---")
    found = False
    for property_name in DUPLICATE_MATCH_FIELDS:
        result = await cosmos_mgr.execute_query(
            duplicate_lookup_query(property_name),
            {"requested_doc_id": doc_id},
        )
        if not result:
            print(f"{property_name}: 0 matches")
            continue

        summary = _safe_vertex_summary(result[0]) if isinstance(result[0], dict) else {}
        exact = str(summary.get("matched_value") or "") == doc_id
        print(f"{property_name}: 1 match | exact={exact} | sample={summary}")
        found = found or exact

    print(f"Authoritative preflight result: {'duplicate' if found else 'new'}")


async def inspect_schema(cosmos_mgr: CosmosGremlinManager) -> None:
    print("--- Inspecting Graph Schema ---")

    node_result = await cosmos_mgr.execute_query("g.V().limit(1).valueMap(true)")
    print(f"\n[Node Schema Sample]:\n{node_result}")

    edge_result = await cosmos_mgr.execute_query("g.E().limit(1).valueMap(true)")
    print(f"\n[Edge Schema Sample]:\n{edge_result}")

    labels = await cosmos_mgr.execute_query("g.V().label().dedup()")
    print(f"\n[Node Labels found]:\n{labels}")


async def analyze_pk_distribution(cosmos_mgr: CosmosGremlinManager) -> None:
    distribution = await cosmos_mgr.execute_query("g.V().group().by('pk').by(count())")

    print("--- Pipeline Distribution (PK vs Node Count) ---")
    if distribution:
        stats = distribution[0]
        for pk, count in sorted(stats.items(), key=lambda item: item[1], reverse=True):
            print(f"PK: {pk:<25} | Nodes: {count}")


async def delete_run_by_pk(cosmos_mgr: CosmosGremlinManager, pk_to_delete: str) -> None:
    print(f"--- Deleting all nodes and edges for PK: {pk_to_delete} ---")
    await cosmos_mgr.execute_query(
        "g.E().has('pk', requested_doc_id).drop()",
        {"requested_doc_id": pk_to_delete},
    )
    print("Edges deleted.")
    await cosmos_mgr.execute_query(
        "g.V().has('pk', requested_doc_id).drop()",
        {"requested_doc_id": pk_to_delete},
    )
    print("Nodes deleted.")


async def run(args: argparse.Namespace) -> None:
    cosmos_mgr = CosmosGremlinManager()
    try:
        identity = cosmos_mgr.data_source_identity
        print(
            "Cosmos target: "
            f"{identity['host']} / {identity['database']} / {identity['graph']} "
            f"[{identity['data_source_id']}]"
        )
        if args.doc_id:
            await inspect_document(cosmos_mgr, args.doc_id)
        if not args.doc_only:
            await analyze_pk_distribution(cosmos_mgr)
            await inspect_schema(cosmos_mgr)
    finally:
        await cosmos_mgr.close()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Inspect the configured Cosmos Gremlin graph.")
    parser.add_argument("--doc-id", help="Run the exact duplicate-preflight lookup for this document ID.")
    parser.add_argument(
        "--doc-only",
        action="store_true",
        help="Skip the global distribution and schema samples (requires --doc-id).",
    )
    args = parser.parse_args()
    if args.doc_only and not args.doc_id:
        parser.error("--doc-only requires --doc-id")
    return args


if __name__ == "__main__":
    asyncio.run(run(parse_args()))
