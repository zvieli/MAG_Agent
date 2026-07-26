#!/usr/bin/env python3
"""Backfill protocol completeness artifacts for an already-ingested case.

The intended trial path is:
    docker compose exec -T function-app python3 scripts/backfill_protocol_completeness.py --doc-id "<DOC_ID>"

The script reads case graph data from Cosmos DB and raw document chunks from Azure
AI Search, runs the local protocol completeness analyzer, and writes the result to
local Azurite by default so the local Function App endpoint can serve it.
"""

from __future__ import annotations

# ruff: noqa: E402

import argparse
import asyncio
import json
import logging
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from azure.core.exceptions import ResourceNotFoundError
from azure.search.documents.aio import SearchClient
from azure.storage.blob import BlobServiceClient

from src.graph.cosmos_manager import CosmosGremlinManager
from src.models.models import SCHEMA_CONFIG
from src.reporting.protocol_completeness import analyze_protocol_completeness
from src.storage.azure_store import AzureBlobArtifactStore
from src.storage.factory import get_cosmos_manager, get_search_manager
from src.storage.interfaces import IArtifactStore
from src.storage.search_manager import SearchManager
from src.utils.config import settings


AZURITE_CONNECTION_STRING = (
    "DefaultEndpointsProtocol=http;"
    "AccountName=devstoreaccount1;"
    "AccountKey=Eby8vdM02xNOcqFlqUwJPLlmEtlCDXJ1OUzFT50uSRZ6IFsuFq2UVErCz4I6tq/"
    "K1SZFPTOtr/KBHBeksoGMGw==;"
    "BlobEndpoint=http://127.0.0.1:10000/devstoreaccount1;"
    "QueueEndpoint=http://127.0.0.1:10001/devstoreaccount1;"
    "TableEndpoint=http://127.0.0.1:10002/devstoreaccount1;"
)


def _property_value(value: Any, default: Any = None) -> Any:
    """Flatten Cosmos valueMap values into a primitive where possible."""
    if value is None:
        return default
    if isinstance(value, list):
        if not value:
            return default
        first = value[0]
        if isinstance(first, dict) and "value" in first:
            return first.get("value", default)
        return first
    if isinstance(value, dict) and "value" in value:
        return value.get("value", default)
    return value


def _flatten_properties(properties: dict[str, Any] | None) -> dict[str, Any]:
    clean: dict[str, Any] = {}
    for key, value in (properties or {}).items():
        normalized = _property_value(value)
        if isinstance(normalized, (str, int, float, bool)) or normalized is None:
            clean[key] = normalized
    return clean


def _vertex_to_node(vertex: dict[str, Any]) -> dict[str, Any]:
    props = _flatten_properties(vertex.get("properties"))
    node_type = props.get("type") or props.get("label") or vertex.get("label") or "Entity"
    label = (
        props.get("label")
        or props.get("name")
        or props.get("display_name")
        or vertex.get("label")
        or vertex.get("id")
        or ""
    )
    node = {
        "id": str(vertex.get("id") or ""),
        "type": str(node_type),
        "label": str(label),
        "metadata": props,
    }
    for key in ("description", "forensic_summary", "source_location"):
        if props.get(key):
            node[key] = props[key]
    if "forensic_summary" not in node and props.get("meta_forensic_summary"):
        node["forensic_summary"] = props["meta_forensic_summary"]
    return node


def _edge_to_record(edge: dict[str, Any]) -> dict[str, Any]:
    props = _flatten_properties(edge.get("properties"))
    relation = props.get("relation") or edge.get("label") or "RELATED_TO"
    record = {
        "id": str(edge.get("id") or ""),
        "source": str(edge.get("source") or ""),
        "target": str(edge.get("target") or ""),
        "relation": str(relation),
        "metadata": props,
    }
    for key in ("description", "forensic_summary", "source_location", "confidence"):
        if props.get(key):
            record[key] = props[key]
    if "forensic_summary" not in record and props.get("meta_forensic_summary"):
        record["forensic_summary"] = props["meta_forensic_summary"]
    return record


async def fetch_graph_from_cosmos(
    cosmos_mgr: CosmosGremlinManager,
    doc_id: str,
    *,
    limit: int,
) -> dict[str, Any]:
    vertex_query = (
        "g.V().or(has('pk', pk), has('partitionKey', pk), has('source_file', pk), has('doc_id', pk)).limit(limit)"
        ".project('id', 'label', 'properties')"
        ".by(id()).by(label()).by(valueMap(true))"
    )
    edge_query = (
        "g.E().or(has('pk', pk), has('partitionKey', pk), has('source_file', pk), has('doc_id', pk)).limit(limit)"
        ".project('id', 'label', 'source', 'target', 'properties')"
        ".by(id()).by(label()).by(outV().id()).by(inV().id()).by(valueMap(true))"
    )
    bindings = {"pk": doc_id, "limit": max(1, int(limit))}
    vertices, edges = await asyncio.gather(
        cosmos_mgr.execute_query(vertex_query, bindings),
        cosmos_mgr.execute_query(edge_query, bindings),
    )
    return {
        "nodes": [_vertex_to_node(vertex) for vertex in vertices],
        "edges": [_edge_to_record(edge) for edge in edges],
        "metadata": {
            "doc_id": doc_id,
            "source": "cosmos_backfill",
            "node_count": len(vertices),
            "edge_count": len(edges),
        },
    }


async def fetch_raw_text_from_search(
    search_mgr: SearchManager,
    doc_id: str,
    *,
    max_chunks: int,
    max_empty_chunk_misses: int,
) -> tuple[str, dict[str, Any]]:
    sanitized_doc_id = SearchManager._sanitize_id(doc_id)
    chunks: list[tuple[int, str]] = []
    consecutive_misses = 0
    attempted = 0

    async with SearchClient(
        endpoint=search_mgr.endpoint,
        index_name=settings.azure_search_index,
        credential=search_mgr.credential,
        connection_timeout=10,
        read_timeout=30,
    ) as client:
        for chunk_index in range(1, max(1, int(max_chunks)) + 1):
            attempted = chunk_index
            chunk_id = SearchManager._sanitize_id(f"{doc_id}_{chunk_index}")
            try:
                document = await client.get_document(key=chunk_id)
            except ResourceNotFoundError:
                consecutive_misses += 1
                if consecutive_misses >= max(1, int(max_empty_chunk_misses)):
                    break
                continue

            text = str(document.get("text") or "")
            if text.strip():
                chunks.append((chunk_index, text))
            consecutive_misses = 0

    raw_text = "\n\n".join(
        f"## Chunk {chunk_index}\n\n{text}" for chunk_index, text in chunks
    )
    return raw_text, {
        "sanitizedDocId": sanitized_doc_id,
        "chunksFound": len(chunks),
        "chunksAttempted": attempted,
        "maxChunks": max_chunks,
        "maxEmptyChunkMisses": max_empty_chunk_misses,
        "source": "azure_ai_search",
    }


def _local_azurite_store(container_name: str) -> IArtifactStore:
    BlobServiceClient.from_connection_string(AZURITE_CONNECTION_STRING)
    return AzureBlobArtifactStore(
        connection_string=AZURITE_CONNECTION_STRING,
        container_name=container_name,
    )


def _cloud_blob_store(container_name: str) -> IArtifactStore:
    if not settings.azure_storage_connection_string:
        raise RuntimeError("AZURE_STORAGE_CONNECTION_STRING is required for cloud output.")
    return AzureBlobArtifactStore(
        connection_string=settings.azure_storage_connection_string,
        container_name=container_name,
    )


def _artifact_store(output: str, container_name: str) -> IArtifactStore | None:
    if output == "print":
        return None
    if output == "local-azurite":
        return _local_azurite_store(container_name)
    if output == "cloud-blob":
        return _cloud_blob_store(container_name)
    raise ValueError(f"Unsupported output target: {output}")


def write_report_artifacts(
    store: IArtifactStore,
    doc_id: str,
    report: dict[str, Any],
) -> list[str]:
    data = json.dumps(report, ensure_ascii=False, indent=2).encode("utf-8")
    paths = [
        f"graph/{doc_id}/protocol_completeness.json",
        "graph/latest/protocol_completeness.json",
        "protocol_completeness.json",
    ]
    for path in paths:
        store.write_bytes(path, data)
    return paths


async def run_backfill(args: argparse.Namespace) -> dict[str, Any]:
    cosmos_mgr = get_cosmos_manager()
    search_mgr = get_search_manager()
    try:
        graph_data, search_payload = await asyncio.gather(
            fetch_graph_from_cosmos(cosmos_mgr, args.doc_id, limit=args.graph_limit),
            fetch_raw_text_from_search(
                search_mgr,
                args.doc_id,
                max_chunks=args.max_chunks,
                max_empty_chunk_misses=args.max_empty_chunk_misses,
            ),
        )
    finally:
        await asyncio.gather(
            cosmos_mgr.close(),
            search_mgr.close(),
            return_exceptions=True,
        )

    raw_text, search_metadata = search_payload
    report = analyze_protocol_completeness(
        schema_config=SCHEMA_CONFIG,
        graph_data=graph_data,
        raw_text=raw_text,
        doc_id=args.doc_id,
    )

    if getattr(args, "verify_with_agent", False):
        from src.reporting.protocol_completeness import verify_missing_items_with_agent

        report = await verify_missing_items_with_agent(
            report=report,
            doc_id=args.doc_id,
            raw_text=raw_text,
            search_manager=search_mgr,
            max_items=args.agent_max_items,
            critical_only=args.agent_critical_only,
        )

    report["source"] = "backfill"
    report["backfill"] = {
        "generatedAt": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "graph": graph_data.get("metadata", {}),
        "search": search_metadata,
        "verifiedWithAgent": bool(getattr(args, "verify_with_agent", False)),
    }

    store = _artifact_store(args.output, args.container)
    if store is not None:
        report["artifactPaths"] = write_report_artifacts(store, args.doc_id, report)

    return report


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate protocol_completeness.json for an already-ingested case."
    )
    parser.add_argument("--doc-id", required=True, help="Case document ID / Cosmos partition key.")
    parser.add_argument(
        "--output",
        choices=["local-azurite", "cloud-blob", "print"],
        default="local-azurite",
        help="Artifact output target. Defaults to local Azurite for Docker trials.",
    )
    parser.add_argument(
        "--container",
        default=settings.azure_storage_container or "graphify-out",
        help="Blob container name for local or cloud artifact output.",
    )
    parser.add_argument("--graph-limit", type=int, default=5000, help="Max vertices and max edges to read.")
    parser.add_argument("--max-chunks", type=int, default=2000, help="Max Search chunk IDs to probe.")
    parser.add_argument(
        "--max-empty-chunk-misses",
        type=int,
        default=20,
        help="Stop after this many consecutive missing chunk IDs.",
    )
    parser.add_argument(
        "--verify-with-agent",
        action="store_true",
        help="Run QueryAgent grounded verification on missing items after deterministic check.",
    )
    parser.add_argument("--agent-max-items", type=int, default=10, help="Max items to verify with agent.")
    parser.add_argument(
        "--agent-critical-only",
        action="store_true",
        help="Only verify missing items with severity=critical.",
    )
    parser.add_argument("--verbose", action="store_true", help="Enable debug logging.")
    return parser.parse_args()



def main() -> None:
    args = parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    report = asyncio.run(run_backfill(args))
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
