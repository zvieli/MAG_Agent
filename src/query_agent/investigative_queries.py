"""
Investigative Query Functions for Knowledge Graph Analysis.

Implements temporal logic, multi-hop traversal, inferred reasoning, and quid pro quo mapping.
"""

from __future__ import annotations

import networkx as nx
from networkx.readwrite import json_graph
from collections import defaultdict, deque
from datetime import datetime
from pathlib import Path
from typing import Any, Optional
import unicodedata
import difflib

from src.models.models import (
    SCHEMA_CONFIG,
    normalize_relation_label,
    CAUSAL_RELATIONS,
    EXCHANGE_RELATIONS,
    CAUSE_RELATIONS,
    CO_OCCURRENCE_RELATIONS,
    GOD_NODE_THRESHOLD,
)
from src.graph.graph_manager import load_graph_data


def _translate_relation(relation: Any) -> str:
    """
    Translates relation labels using schema definitions.

    Args:
        relation: Raw relation label.

    Returns:
        Normalized and translated relation label.
    """
    return normalize_relation_label(relation)


def _parse_timestamp(ts: str | None) -> datetime | str | None:
    """
    Parse timestamp string into datetime object.

    Args:
        ts: Timestamp string (YYYY-MM-DD format preferred)

    Returns:
        Parsed datetime, original string for partial dates, or None if invalid/unknown
    """
    if not ts or ts == "unknown":
        return None

    try:
        return datetime.strptime(ts[:10], "%Y-%m-%d")
    except (ValueError, TypeError):
        return str(ts)


def _timeline_sort_key(ts_value: datetime | str | None) -> tuple[int, str]:
    """
    Generates a sort key for chronological events.

    Args:
        ts_value: Parsed datetime or raw string.

    Returns:
        Tuple for sorting (datetime first, then strings, then empty).
    """
    if isinstance(ts_value, datetime):
        return (0, ts_value.isoformat())
    if isinstance(ts_value, str):
        return (1, ts_value)
    return (2, "")


import warnings

def _strip_diacritics(text: str) -> str:
    """
    [DEPRECATED] Removes diacritics (Nikud) from Hebrew text.
    """
    raise NotImplementedError("This function is deprecated and its body has been removed.")


def _strip_hebrew_prefixes(text: str) -> str:
    """
    [DEPRECATED] Strip common Hebrew prefixes with a length guard.
    """
    raise NotImplementedError("This function is deprecated and its body has been removed.")


def _score_nodes(G: nx.Graph, terms: list[str]) -> list[tuple[float, str]]:
    """
    [DEPRECATED] Scores nodes in the graph based on keyword overlap and fuzzy matching.
    """
    raise NotImplementedError("This function is deprecated and its body has been removed.")


def _bfs(
    G: nx.Graph, start_nodes: list[str], depth: int
) -> tuple[set[str], list[tuple]]:
    """
    Performs Breadth-First Search to find neighboring nodes and edges.

    Args:
        G: NetworkX graph instance.
        start_nodes: Seed nodes for traversal.
        depth: Maximum traversal depth.

    Returns:
        Tuple of (visited_node_ids, seen_edges).
    """
    visited: set[str] = set(start_nodes)
    frontier = set(start_nodes)
    edges_seen: set[tuple] = set()
    for _ in range(depth):
        next_frontier: set[str] = set()
        for n in frontier:
            if n not in G:
                continue
            for neighbor in G.neighbors(n):
                # Add edge to seen set (sort tuple to handle undirected graph consistently)
                edge = tuple(sorted((n, neighbor)))
                edges_seen.add(edge)
                if neighbor not in visited:
                    next_frontier.add(neighbor)
        visited.update(next_frontier)
        frontier = next_frontier
    return visited, list(edges_seen)


def _semantic_fallback(
    G: nx.Graph,
    query_embedding: list[float],
    vector_index: dict[str, list[float]],
    chunk_index: dict[str, dict[str, Any]] = None,
) -> list[str]:
    """
    Finds graph entry points using vector similarity if keyword search fails.

    Args:
        G: NetworkX graph instance.
        query_embedding: Vector representation of the query.
        vector_index: Index of edge label embeddings.
        chunk_index: Index of document chunk embeddings.

    Returns:
        List of relevant node IDs.
    """
    from src.processing.vector_indexer import cosine_similarity

    start_nodes = set()

    # 1. Fallback via Edge Labels (Relations)
    rel_similarities = []
    for rel_label, emb in vector_index.items():
        sim = cosine_similarity(query_embedding, emb)
        rel_similarities.append((rel_label, sim))

    top_rels = sorted(rel_similarities, key=lambda x: x[1], reverse=True)[:10]
    top_rel_labels = {r for r, s in top_rels if s > 0.6}

    if top_rel_labels:
        for u, v, d in G.edges(data=True):
            if d.get("relation") in top_rel_labels:
                start_nodes.add(u)
                start_nodes.add(v)

    # 2. Fallback via Chunks (if nodes have source_location)
    if chunk_index and len(start_nodes) < 10:
        chunk_similarities = []
        for chunk_id, info in chunk_index.items():
            sim = cosine_similarity(query_embedding, info.get("embedding", []))
            chunk_similarities.append((chunk_id, sim))

        top_chunks = sorted(chunk_similarities, key=lambda x: x[1], reverse=True)[:10]
        top_chunk_ids = {c for c, s in top_chunks if s > 0.6}

        # Match nodes to these chunks
        if top_chunk_ids:
            for nid, data in G.nodes(data=True):
                loc = str(data.get("source_location") or "")
                if any(cid in loc for cid in top_chunk_ids):
                    start_nodes.add(nid)

    return list(start_nodes)[:15]


def query_knowledge_graph(
    graph_source: Any,
    question: str,
    depth: int = 3,
    edge_limit: int = 30,
    query_embedding: list[float] = None,
    vector_index: dict[str, list[float]] = None,
    chunk_index: dict[str, dict[str, Any]] = None,
) -> str:
    """
    [DEPRECATED] Performs a deep investigative query on the knowledge graph with truncation.
    """
    raise NotImplementedError("This function is deprecated and its body has been removed.")


def build_timeline(
    graph_source: str | Path | dict[str, Any],
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    entity_filter: Optional[str] = None,
    event_limit: int = 30,
) -> dict[str, Any]:
    """
    Build a chronological timeline of actions from the knowledge graph.

    Scans all edges and extracts timestamp or occurrence_date properties,
    sorting them chronologically. Returns a narrative timeline with source citations.

    Args:
        graph_source: Path to graph.json or graph dictionary
        start_date: Filter start date (YYYY-MM-DD)
        end_date: Filter end date (YYYY-MM-DD)
        entity_filter: Optional entity ID to filter timeline to specific subject

    Returns:
        Dictionary with timeline events, source locations, and metadata
    """
    graph = load_graph_data(graph_source)

    nodes = {
        node["id"]: node for node in graph.get("nodes", []) if isinstance(node, dict)
    }
    edges = graph.get("edges", graph.get("links", []))

    # Extract timeline events
    timeline_events = []

    for edge in edges:
        if not isinstance(edge, dict):
            continue

        # Expand historical relations if present (from merged edges)
        history = edge.get("metadata", {}).get("all_relations_history", [])
        if history:
            relations_to_process = history
        else:
            # Fallback to the top-level relation if no history exists
            relations_to_process = [
                {
                    "relation": edge.get("metadata", {}).get(
                        "raw_relation", edge.get("relation")
                    ),
                    "timestamp": edge.get("timestamp") or edge.get("occurrence_date"),
                    "source_location": edge.get("source_location"),
                    "source_file": edge.get("source_file"),
                }
            ]

        for rel_data in relations_to_process:
            timestamp = rel_data.get("timestamp")
            if not timestamp or timestamp == "unknown":
                continue

            parsed_ts = _parse_timestamp(timestamp)
            if parsed_ts is None:
                continue

            # Apply date filters
            if start_date:
                start_parsed = _parse_timestamp(start_date)
                if isinstance(start_parsed, datetime) and isinstance(
                    parsed_ts, datetime
                ):
                    if parsed_ts < start_parsed:
                        continue

            if end_date:
                end_parsed = _parse_timestamp(end_date)
                if isinstance(end_parsed, datetime) and isinstance(parsed_ts, datetime):
                    if parsed_ts > end_parsed:
                        continue

            # Apply entity filter if specified
            if entity_filter:
                source = str(edge.get("source", ""))
                target = str(edge.get("target", ""))
                if entity_filter not in [source, target]:
                    continue

            source_node = nodes.get(str(edge.get("source")))
            target_node = nodes.get(str(edge.get("target")))

            source_label = (
                source_node.get("label") if source_node else edge.get("source")
            )
            target_label = (
                target_node.get("label") if target_node else edge.get("target")
            )

            timeline_events.append(
                {
                    "timestamp": parsed_ts,
                    "timestamp_str": timestamp,
                    "source": str(edge.get("source")),
                    "target": str(edge.get("target")),
                    "source_label": source_label,
                    "target_label": target_label,
                    "relation": _translate_relation(
                        rel_data.get("relation", "UNKNOWN")
                    ),
                    "confidence": edge.get("confidence", "UNKNOWN"),
                    "confidence_score": edge.get("confidence_score"),
                    "source_location": rel_data.get("source_location", "unknown"),
                    "source_file": rel_data.get("source_file", "unknown"),
                }
            )

    # Sort by timestamp
    timeline_events.sort(key=lambda e: _timeline_sort_key(e["timestamp"]))

    # Truncate if too many events
    if len(timeline_events) > event_limit:
        timeline_events = timeline_events[:event_limit]
        # Mark last event as truncated for the report
        if timeline_events:
            timeline_events[-1]["is_truncated_marker"] = True

    # Identify temporal proximity red flags (e.g., within 48 hours)
    for i in range(len(timeline_events) - 1):
        e1 = timeline_events[i]
        e2 = timeline_events[i + 1]
        if isinstance(e1["timestamp"], datetime) and isinstance(
            e2["timestamp"], datetime
        ):
            delta = abs((e2["timestamp"] - e1["timestamp"]).total_seconds())
            if delta <= 172800:  # 48 hours
                e1["red_flag"] = True
                e2["red_flag"] = True

    # Build narrative
    narrative = []
    nav_config = SCHEMA_CONFIG.timeline_narrative

    for event in timeline_events:
        if event["relation"] in CAUSAL_RELATIONS:
            action = nav_config.causal_format.format(
                source=event["source_label"],
                target=event["target_label"],
                relation=event["relation"],
            )
        else:
            action = nav_config.generic_format.format(
                source=event["source_label"], target=event["target_label"]
            )

        confidence_marker = ""
        if event["confidence"] == "INFERRED":
            confidence_marker = nav_config.inferred_marker

        flag_marker = nav_config.red_flag_marker if event.get("red_flag") else ""

        narrative.append(
            {
                "date": event["timestamp_str"],
                "action": action + confidence_marker + flag_marker,
                "relation": event["relation"],
                "source_file": event["source_file"],
                "source_location": event["source_location"],
                "is_suspicious": event.get("red_flag", False),
            }
        )

    return {
        "timeline": narrative,
        "event_count": len(timeline_events),
        "is_truncated": len(timeline_events) >= event_limit,
        "date_range": {
            "earliest": timeline_events[0]["timestamp_str"]
            if timeline_events
            else None,
            "latest": timeline_events[-1]["timestamp_str"] if timeline_events else None,
        },
        "filters_applied": {
            "start_date": start_date,
            "end_date": end_date,
            "entity": entity_filter,
        },
    }


def find_connections(
    graph_path: str | Path,
    entity_a: str,
    entity_b: str,
    max_hops: int = 3,
    path_limit: int = 10,
) -> dict[str, Any]:
    """
    Find multi-hop connections between two entities.

    Uses BFS to discover paths up to max_hops, identifying intermediaries
    such as advisors, spokespersons, or business associates.

    Args:
        graph_path: Path to graph.json
        entity_a: Source entity ID
        entity_b: Target entity ID
        max_hops: Maximum path length (default 3)

    Returns:
        Dictionary with paths, intermediaries, and path details
    """
    graph = load_graph_data(graph_path)

    nodes = {
        node["id"]: node for node in graph.get("nodes", []) if isinstance(node, dict)
    }
    edges = graph.get("edges", graph.get("links", []))

    # Pre-calculate node degrees for God Node filtering
    from collections import Counter

    node_degrees = Counter()
    for edge in edges:
        if isinstance(edge, dict):
            node_degrees[str(edge.get("source"))] += 1
            node_degrees[str(edge.get("target"))] += 1

    # Load configuration for human entity types
    human_entity_types = set(t.lower() for t in SCHEMA_CONFIG.human_entity_types)

    def _is_human_mediator(node_id: str) -> bool:
        node_type = str(nodes.get(node_id, {}).get("type", "")).strip().lower()
        return node_type in human_entity_types

    def _get_penalty_score(node_id: str) -> int:
        """Calculate penalty score for a node (higher is worse)."""
        score = 0
        degree = node_degrees.get(node_id, 0)
        if degree > GOD_NODE_THRESHOLD:
            score += degree - GOD_NODE_THRESHOLD
        return score

    # Build adjacency list
    adjacency: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for edge in edges:
        if not isinstance(edge, dict):
            continue

        source = str(edge.get("source"))
        target = str(edge.get("target"))

        adjacency[source].append(
            {
                "neighbor": target,
                "relation": _translate_relation(edge.get("relation")),
                "confidence": edge.get("confidence"),
                "confidence_score": edge.get("confidence_score"),
                "timestamp": edge.get("timestamp"),
            }
        )
        # Treat as undirected graph
        adjacency[target].append(
            {
                "neighbor": source,
                "relation": _translate_relation(edge.get("relation")),
                "confidence": edge.get("confidence"),
                "confidence_score": edge.get("confidence_score"),
                "timestamp": edge.get("timestamp"),
            }
        )

    # Sort neighbors: prioritize human mediators, then low penalty, then high confidence.
    for node_id, neighbors in adjacency.items():
        neighbors.sort(
            key=lambda item: (
                _get_penalty_score(item["neighbor"]),
                0 if _is_human_mediator(item["neighbor"]) else 1,
                item.get("confidence_score") is None,
                -(item.get("confidence_score") or 0.0),
            )
        )

    # BFS to find paths
    paths = []
    queue = deque(
        [(entity_a, [entity_a], [])]
    )  # (current_node, path_nodes, path_edges)
    visited_paths = set()

    while queue:
        current, path_nodes, path_edges = queue.popleft()

        if len(path_nodes) - 1 >= max_hops:
            continue

        # In BFS, we still want to avoid God Nodes unless they are the target
        ordered_neighbors = sorted(
            adjacency.get(current, []),
            key=lambda item: (
                _get_penalty_score(item["neighbor"])
                if item["neighbor"] != entity_b
                else -1,
                0 if _is_human_mediator(item["neighbor"]) else 1,
                item.get("confidence_score") is None,
                -(item.get("confidence_score") or 0.0),
            ),
        )

        for edge_data in ordered_neighbors:
            neighbor = edge_data["neighbor"]

            if neighbor in path_nodes:
                continue

            new_path = path_nodes + [neighbor]
            new_path_edges = path_edges + [edge_data]

            path_key = tuple(new_path)
            if path_key in visited_paths:
                continue
            visited_paths.add(path_key)

            if neighbor == entity_b:
                paths.append(
                    {
                        "path": new_path,
                        "edges": new_path_edges,
                        "hops": len(new_path) - 1,
                    }
                )
                if len(paths) >= path_limit:
                    break
            elif len(new_path) - 1 < max_hops:
                queue.append((neighbor, new_path, new_path_edges))

        if len(paths) >= path_limit:
            break

    # Format paths with node labels
    formatted_paths = []
    for path_info in sorted(
        paths,
        key=lambda p: (
            p["hops"],
            -sum(1 for node_id in p["path"][1:-1] if _is_human_mediator(node_id)),
        ),
    ):
        nodes_with_labels = [
            {
                "id": node_id,
                "label": nodes.get(node_id, {}).get("label", node_id),
                "type": nodes.get(node_id, {}).get("type", "Unknown"),
            }
            for node_id in path_info["path"]
        ]

        person_intermediaries = sum(
            1
            for node in nodes_with_labels[1:-1]
            if str(node.get("type", "")).lower() in human_entity_types
        )

        edges_with_labels = []
        for i, edge_data in enumerate(path_info["edges"]):
            source = path_info["path"][i]
            target = path_info["path"][i + 1]
            edges_with_labels.append(
                {
                    "source": source,
                    "target": target,
                    "relation": _translate_relation(edge_data["relation"]),
                    "confidence": edge_data["confidence"],
                    "timestamp": edge_data["timestamp"],
                }
            )

        formatted_paths.append(
            {
                "hops": path_info["hops"],
                "nodes": nodes_with_labels,
                "edges": edges_with_labels,
                "intermediaries": nodes_with_labels[1:-1]
                if len(nodes_with_labels) > 2
                else [],
                "person_intermediary_count": person_intermediaries,
            }
        )

    # Identify most common intermediaries
    intermediary_counts = defaultdict(int)
    for path in formatted_paths:
        for intermediary in path["intermediaries"]:
            intermediary_counts[intermediary["id"]] += 1

    top_intermediaries = sorted(
        [
            {"id": id, "label": label, "appearances": count}
            for id, count in intermediary_counts.items()
            for label in [nodes.get(id, {}).get("label", id)]
        ],
        key=lambda x: x["appearances"],
        reverse=True,
    )

    if not formatted_paths:
        return {
            "source": {
                "id": entity_a,
                "label": nodes.get(entity_a, {}).get("label", entity_a),
            },
            "target": {
                "id": entity_b,
                "label": nodes.get(entity_b, {}).get("label", entity_b),
            },
            "paths": [],
            "path_count": 0,
            "message": "No path found. DO NOT ADD ANY CITATION TAG FOR THIS INFO.",
            "max_hops_searched": max_hops,
        }

    return {
        "source": {
            "id": entity_a,
            "label": nodes.get(entity_a, {}).get("label", entity_a),
        },
        "target": {
            "id": entity_b,
            "label": nodes.get(entity_b, {}).get("label", entity_b),
        },
        "paths": formatted_paths,
        "path_count": len(formatted_paths),
        "shortest_hops": min([p["hops"] for p in formatted_paths], default=None),
        "top_intermediaries": top_intermediaries[:5],
        "max_hops_searched": max_hops,
    }


def analyze_inferred_patterns(
    graph_source: str | Path | dict[str, Any],
    confidence_threshold: float = 0.3,
    relation_filter: Optional[str] = None,
    edge_limit: int = 30,
) -> dict[str, Any]:
    """
    Analyze inferred relationships with low confidence.

    Identifies edges marked as INFERRED with confidence_score ≤ threshold,
    groups them by pattern, and suggests investigative directions.

    Args:
        graph_source: Path to graph.json or graph dictionary
        confidence_threshold: Maximum confidence score to include (default 0.3)
        relation_filter: Optional relation type to filter on

    Returns:
        Dictionary with inferred patterns, patterns by type, and suggestions
    """
    graph = load_graph_data(graph_source)

    nodes = {
        node["id"]: node for node in graph.get("nodes", []) if isinstance(node, dict)
    }
    edges = graph.get("edges", graph.get("links", []))

    inferred_edges = []

    for edge in edges:
        if not isinstance(edge, dict):
            continue

        confidence = edge.get("confidence", "")
        confidence_score = edge.get("confidence_score")

        # Filter by confidence threshold
        if confidence_score is None or confidence_score > confidence_threshold:
            continue

        if confidence != "INFERRED":
            continue

        if relation_filter and edge.get("relation") != relation_filter:
            continue

        source_node = nodes.get(str(edge.get("source")))
        target_node = nodes.get(str(edge.get("target")))

        inferred_edges.append(
            {
                "source": str(edge.get("source")),
                "target": str(edge.get("target")),
                "source_label": source_node.get("label")
                if source_node
                else edge.get("source"),
                "target_label": target_node.get("label")
                if target_node
                else edge.get("target"),
                "source_type": source_node.get("type") if source_node else "Unknown",
                "target_type": target_node.get("type") if target_node else "Unknown",
                "relation": edge.get("relation"),
                "confidence_score": confidence_score,
                "source_location": edge.get("source_location"),
                "source_file": edge.get("source_file"),
            }
        )

        if len(inferred_edges) >= edge_limit:
            break

    # Group by pattern type
    patterns_by_type: dict[str, list] = defaultdict(list)
    for edge in inferred_edges:
        pattern_type = edge["relation"]
        patterns_by_type[pattern_type].append(edge)

    # Identify co-occurrence patterns (low-confidence adjacencies suggesting temporal/contextual proximity)
    co_occurrence_patterns = [
        e
        for e in inferred_edges
        if normalize_relation_label(e["relation"]) in CO_OCCURRENCE_RELATIONS
    ]

    # Identify potential causality or benefit chains
    causality_patterns = [
        e
        for e in inferred_edges
        if normalize_relation_label(e["relation"]) in CAUSAL_RELATIONS
    ]

    # Identify potential intermediaries (nodes with high inferred degree)
    inferred_node_degrees = defaultdict(int)
    for edge in inferred_edges:
        inferred_node_degrees[edge["source"]] += 1
        inferred_node_degrees[edge["target"]] += 1

    suspicious_intermediaries = sorted(
        [
            {
                "id": node_id,
                "label": nodes.get(node_id, {}).get("label", node_id),
                "inferred_degree": count,
            }
            for node_id, count in inferred_node_degrees.items()
        ],
        key=lambda x: x["inferred_degree"],
        reverse=True,
    )[:5]

    # Generate investigative suggestions
    suggestions = []

    # Use schema suggestions if available
    schema_suggestions = SCHEMA_CONFIG.investigative_suggestions
    if schema_suggestions:
        for sug in schema_suggestions:
            sug_type = sug.get("type", "GENERAL")
            # Simple keyword-based filtering for relevant suggestions
            if sug_type == "CO-OCCURRENCE ANALYSIS" and not co_occurrence_patterns:
                continue
            if sug_type == "CAUSALITY INVESTIGATION" and not causality_patterns:
                continue
            if sug_type == "INTERMEDIARY ANALYSIS" and not suspicious_intermediaries:
                continue

            # Map examples if requested
            sug_copy = sug.copy()
            if sug_type == "CO-OCCURRENCE ANALYSIS":
                sug_copy["examples"] = co_occurrence_patterns[:3]
            elif sug_type == "CAUSALITY INVESTIGATION":
                sug_copy["examples"] = causality_patterns[:3]
            elif sug_type == "INTERMEDIARY ANALYSIS":
                sug_copy["examples"] = suspicious_intermediaries

            suggestions.append(sug_copy)

    # Fallback to defaults if no suggestions matched or schema is empty
    if not suggestions:
        if co_occurrence_patterns:
            suggestions.append(
                {
                    "type": "CO-OCCURRENCE ANALYSIS",
                    "count": len(co_occurrence_patterns),
                    "suggestion": "Review entities mentioned in the same document sections. Co-occurrences may indicate undisclosed relationships or coordination.",
                    "examples": co_occurrence_patterns[:3],
                }
            )

        if causality_patterns:
            suggestions.append(
                {
                    "type": "CAUSALITY INVESTIGATION",
                    "count": len(causality_patterns),
                    "suggestion": "Low-confidence causality edges suggest potential transactional relationships. Verify whether stated benefits correlate with actions.",
                    "examples": causality_patterns[:3],
                }
            )

        if suspicious_intermediaries:
            suggestions.append(
                {
                    "type": "INTERMEDIARY ANALYSIS",
                    "count": len(suspicious_intermediaries),
                    "suggestion": "These nodes appear frequently in inferred relationships. They may be key connectors or undisclosed coordinators.",
                    "examples": suspicious_intermediaries,
                }
            )

    return {
        "total_inferred_edges": len(inferred_edges),
        "confidence_threshold": confidence_threshold,
        "patterns_by_relation": {k: len(v) for k, v in patterns_by_type.items()},
        "all_inferred_edges": inferred_edges,
        "investigative_suggestions": suggestions,
        "inferred_node_degrees": {
            k: v
            for k, v in sorted(
                inferred_node_degrees.items(), key=lambda x: x[1], reverse=True
            )[:10]
        },
    }


def map_quid_pro_quo(graph_path: str | Path, edge_limit: int = 30) -> dict[str, Any]:
    """
    Map causal relationships and quid pro quo patterns in the knowledge graph.

    Extracts all edges labeled with causality relations (IN_EXCHANGE_FOR, CAUSED_BY, etc.),
    building chains that connect arbitrary node types. Generates benefit-action pairs
    and causality analysis without node type restrictions.

    Args:
        graph_path: Path to graph.json

    Returns:
        Dictionary with causal chains, benefit-action pairs, causers, and analysis
    """
    graph = load_graph_data(graph_path)

    nodes = {
        node["id"]: node for node in graph.get("nodes", []) if isinstance(node, dict)
    }
    edges = graph.get("edges", graph.get("links", []))

    # Load causality relations from config
    causal_relations = CAUSAL_RELATIONS

    quid_pro_quo_edges = []

    for edge in edges:
        if not isinstance(edge, dict):
            continue

        relation = str(edge.get("relation", ""))
        relation_upper = relation.upper()

        # Check if relation is in causality set (either original form or uppercase)
        if relation not in causal_relations and relation_upper not in causal_relations:
            continue

        source_node = nodes.get(str(edge.get("source")))
        target_node = nodes.get(str(edge.get("target")))

        quid_pro_quo_edges.append(
            {
                "source": str(edge.get("source")),
                "target": str(edge.get("target")),
                "source_label": source_node.get("label")
                if source_node
                else edge.get("source"),
                "target_label": target_node.get("label")
                if target_node
                else edge.get("target"),
                "source_type": source_node.get("type") if source_node else "Unknown",
                "target_type": target_node.get("type") if target_node else "Unknown",
                "relation": _translate_relation(relation),
                "confidence": edge.get("confidence"),
                "confidence_score": edge.get("confidence_score"),
                "timestamp": edge.get("timestamp"),
                "source_location": edge.get("source_location"),
                "source_file": edge.get("source_file"),
                "description": source_node.get("description") if source_node else None,
            }
        )

        if len(quid_pro_quo_edges) >= edge_limit:
            break

    max_hops = 3

    # Identify chains: bounded multi-hop causal paths (generic, not restricted by node type)
    qpp_adjacency: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for edge in quid_pro_quo_edges:
        qpp_adjacency[edge["source"]].append(edge)

    for source_id, next_edges in qpp_adjacency.items():
        next_edges.sort(
            key=lambda item: (
                item.get("confidence_score") is None,
                -(item.get("confidence_score") or 0.0),
                str(item.get("relation") or ""),
            )
        )

    chains: list[dict[str, Any]] = []
    seen_signatures: set[tuple[str, ...]] = set()

    def _path_confidence_score(path_edges: list[dict[str, Any]]) -> float | None:
        scores = [
            edge.get("confidence_score")
            for edge in path_edges
            if isinstance(edge.get("confidence_score"), (int, float))
        ]
        if not scores:
            return None
        return sum(float(score) for score in scores) / len(scores)

    def _record_chain(path_nodes: list[str], path_edges: list[dict[str, Any]]) -> None:
        signature = tuple(path_nodes)
        if signature in seen_signatures:
            return
        seen_signatures.add(signature)

        nodes_with_labels = [
            {
                "id": node_id,
                "label": nodes.get(node_id, {}).get("label", node_id),
                "type": nodes.get(node_id, {}).get("type", "Unknown"),
            }
            for node_id in path_nodes
        ]

        chains.append(
            {
                "hops": len(path_edges),
                "path_nodes": path_nodes,
                "nodes": nodes_with_labels,
                "edges": path_edges,
                "start": nodes_with_labels[0] if nodes_with_labels else None,
                "end": nodes_with_labels[-1] if nodes_with_labels else None,
                "path_confidence_score": _path_confidence_score(path_edges),
                "person_intermediary_count": sum(
                    1
                    for node in nodes_with_labels[1:-1]
                    if str(node.get("type", "")).lower()
                    in set(t.lower() for t in SCHEMA_CONFIG.human_entity_types)
                ),
            }
        )

    def _dfs(
        current_node: str, path_nodes: list[str], path_edges: list[dict[str, Any]]
    ) -> None:
        if path_edges:
            _record_chain(path_nodes, path_edges)
        if len(path_edges) >= max_hops:
            return

        for edge in qpp_adjacency.get(current_node, []):
            next_node = edge["target"]
            if next_node in path_nodes:
                continue
            _dfs(next_node, path_nodes + [next_node], path_edges + [edge])

    for source_id in qpp_adjacency:
        _dfs(source_id, [source_id], [])

    chains.sort(
        key=lambda chain: (
            chain["hops"],
            chain["person_intermediary_count"],
            -(chain["path_confidence_score"] or 0.0),
        )
    )

    # Analyze benefit recipients (targets of IN_EXCHANGE_FOR relations)
    benefit_recipients = defaultdict(list)
    for edge in quid_pro_quo_edges:
        if normalize_relation_label(edge["relation"]) in EXCHANGE_RELATIONS:
            benefit_recipients[edge["target"]].append(
                {
                    "actor": edge["source"],
                    "actor_label": edge["source_label"],
                    "confidence_score": edge["confidence_score"],
                    "timestamp": edge["timestamp"],
                }
            )

    # Identify action causers (sources of CAUSED_BY relations)
    causers = defaultdict(list)
    for edge in quid_pro_quo_edges:
        if normalize_relation_label(edge["relation"]) in CAUSE_RELATIONS:
            causers[edge["source"]].append(
                {
                    "effect": edge["target"],
                    "effect_label": edge["target_label"],
                    "confidence_score": edge["confidence_score"],
                    "timestamp": edge["timestamp"],
                }
            )

    return {
        "total_qid_pro_quo_edges": len(quid_pro_quo_edges),
        "all_edges": quid_pro_quo_edges,
        "chains": chains,
        "chain_count": len(chains),
        "benefit_recipients": {
            node_id: benefits for node_id, benefits in benefit_recipients.items()
        },
        "causers": {node_id: effects for node_id, effects in causers.items()},
        "in_exchange_for_count": len(
            [
                e
                for e in quid_pro_quo_edges
                if normalize_relation_label(e["relation"]) in EXCHANGE_RELATIONS
            ]
        ),
        "caused_by_count": len(
            [
                e
                for e in quid_pro_quo_edges
                if normalize_relation_label(e["relation"]) in CAUSE_RELATIONS
            ]
        ),
        "max_hops_searched": max_hops,
    }
