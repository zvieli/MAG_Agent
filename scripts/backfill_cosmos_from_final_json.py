#!/usr/bin/env python3
"""
Replay an enriched final.json graph into Cosmos DB.

Default mode is a local dry run that validates and summarizes the payload. Use
--apply to upsert the same topology with compact enriched properties.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.graph.cosmos_manager import CosmosGremlinManager  # noqa: E402
from src.utils.config import settings  # noqa: E402


DEFAULT_FINAL_JSON = (
    REPO_ROOT
    / "downloads/graph/20260125015510 יהודה-26-1128 חומר חקירה מושחר/final.json"
)


def load_graph(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        graph = json.load(handle)
    nodes = graph.get("nodes")
    edges = graph.get("edges") or graph.get("links")
    if not isinstance(nodes, list) or not isinstance(edges, list):
        raise ValueError("final.json must contain list-valued nodes and edges/links")
    if not nodes or not edges:
        raise ValueError("final.json contains an empty graph payload")
    return graph


def summarize_payload(graph: dict[str, Any]) -> dict[str, Any]:
    nodes = graph.get("nodes", [])
    edges = graph.get("edges") or graph.get("links", [])
    alias_nodes = [
        node
        for node in nodes
        if node.get("resolved_aliases_data")
        or (isinstance(node.get("metadata"), dict) and node["metadata"].get("resolved_aliases_data"))
    ]
    history_edges = [
        edge
        for edge in edges
        if edge.get("all_relations_history")
        or (isinstance(edge.get("metadata"), dict) and edge["metadata"].get("all_relations_history"))
    ]
    source_file_edges = [
        edge
        for edge in edges
        if edge.get("source_files")
        or (isinstance(edge.get("metadata"), dict) and edge["metadata"].get("source_files"))
    ]
    return {
        "doc_id": graph.get("metadata", {}).get("doc_id") or graph.get("doc_id"),
        "node_count": len(nodes),
        "edge_count": len(edges),
        "nodes_with_alias_provenance": len(alias_nodes),
        "edges_with_relation_history": len(history_edges),
        "edges_with_source_files": len(source_file_edges),
        "sample_node_id": nodes[0].get("id"),
        "sample_edge": {
            "source": edges[0].get("source"),
            "relation": edges[0].get("relation"),
            "target": edges[0].get("target"),
        },
    }


async def apply_backfill(graph: dict[str, Any]) -> None:
    manager = CosmosGremlinManager()
    try:
        await manager.sync_graph(graph)
    finally:
        await manager.close()


async def main() -> int:
    parser = argparse.ArgumentParser(
        description="Dry-run or apply Cosmos enrichment backfill from final.json."
    )
    parser.add_argument(
        "path",
        nargs="?",
        type=Path,
        default=DEFAULT_FINAL_JSON,
        help="Path to final.json. Defaults to the downloaded current-case graph.",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually upsert the graph into Cosmos. Omit for validation only.",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logging level for Cosmos sync.",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=None,
        help="Override COSMOS_BATCH_SIZE for this replay.",
    )
    parser.add_argument(
        "--timeout-seconds",
        type=float,
        default=None,
        help="Override Gremlin query timeout for this replay.",
    )
    args = parser.parse_args()

    logging.basicConfig(level=getattr(logging, args.log_level))
    if args.batch_size is not None:
        settings.cosmos_batch_size = args.batch_size
    if args.timeout_seconds is not None:
        settings.llm_request_timeout_seconds = args.timeout_seconds

    graph = load_graph(args.path)
    summary = summarize_payload(graph)
    print(json.dumps(summary, ensure_ascii=False, indent=2))

    if not args.apply:
        print("Dry run only. Re-run with --apply to upsert into Cosmos.")
        return 0

    await apply_backfill(graph)
    print("Cosmos backfill complete.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
