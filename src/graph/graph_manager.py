from src.utils.bidi_utils import fix_text
import json
import re
import logging
from typing import Any, Union, Dict, List
from collections import Counter
import networkx as nx
from networkx.algorithms.community import greedy_modularity_communities
from src.extraction.extraction_engine import _build_azure_client
from src.utils.config import settings
import asyncio
from src.models.models import (
    _normalize_confidence,
    normalize_relation_label,
    should_keep_edge_by_confidence,
)
from src.utils.model_helpers import (
    sanitize_value as _sanitize_value,
    merge_text_values as _merge_text_values,
)
from src.graph.local_graphify import score_all
from src.storage.factory import get_artifact_store
from src.storage.interfaces import IArtifactStore

def _get_artifact_store() -> IArtifactStore:
    return get_artifact_store()


def load_graph_data(
    input_source: Union[str, Dict[str, Any]],
    artifact_store: IArtifactStore | None = None,
) -> Dict[str, Any]:
    """
    Centralized utility to load graph data from various sources (JSON file or Dict).

    This is the unified entry point for all components that need to read the
    investigative graph structure.

    Args:
        input_source (Union[str, Dict[str, Any]]): A blob path or already loaded dictionary.

    Returns:
        Dict[str, Any]: The loaded graph data containing nodes and edges/links.

    """
    if isinstance(input_source, dict):
        return input_source
    store = artifact_store or _get_artifact_store()
    return json.loads(store.read_bytes(str(input_source)).decode("utf-8"))


def _slugify(value: Any) -> str:
    """
    Generate a URL-safe or filesystem-safe slug from a value.

    Args:
        value (Any): The value to slugify.

    Returns:
        str: The slugified string.
    """
    text = str(value or "").strip().lower()
    text = re.sub(r"[^a-z0-9\u0590-\u05FF]+", "_", text)
    return text.strip("_") or "context"


def prune_god_nodes(
    nodes: list[dict], edges: list[dict], schema_config: Any, threshold: int = 40
) -> tuple[list[dict], list[dict]]:
    """
    Identify and re-balance 'God Nodes' (nodes with degree > threshold).

    Currently DISABLED per user request to prevent 'God Context' node pollution.

    Args:
        nodes (list[dict]): List of graph nodes.
        edges (list[dict]): List of graph edges.
        schema_config (Any): Configuration containing threshold and rules.
        threshold (int): Degree threshold for identifying God Nodes. Defaults to 40.

    Returns:
        tuple[list[dict], list[dict]]: The (potentially modified) nodes and edges lists.
    """
    return nodes, edges


def _extract_primary_timestamp(*texts: str) -> str:
    """
    Extract a primary ISO-formatted timestamp from text context.

    Priority:
    1) Date range (e.g., בין 2012 ל-2017) -> start year-01-01
    2) Full ISO-like date
    3) Single year -> year-01-01
    4) Deterministic fallback ('1900-01-01')

    Args:
        *texts (str): Multiple text segments to search.

    Returns:
        str: The extracted ISO date string (YYYY-MM-DD).
    """
    joined = " ".join((str(t or "") for t in texts))
    range_match = re.search("בין\\s*(\\d{4})\\s*(?:ל|עד|-)\\s*(\\d{4})", joined)
    if range_match:
        return f"{range_match.group(1)}-01-01"
    iso_match = re.search("(\\d{4}[-/]\\d{1,2}[-/]\\d{1,2})", joined)
    if iso_match:
        return iso_match.group(1).replace("/", "-")
    year_match = re.search("\\b(19\\d{2}|20\\d{2}|21\\d{2})\\b", joined)
    if year_match:
        return f"{year_match.group(1)}-01-01"
    return "1900-01-01"


def _rewrite_source_file(
    records: list[dict[str, Any]], source_file: str
) -> list[dict[str, Any]]:
    """
    Update the 'source_file' field in a list of node or edge records.

    Args:
        records (list[dict[str, Any]]): List of node or edge dictionaries.
        source_file (str): The new source file identifier to assign.

    Returns:
        list[dict[str, Any]]: The updated list of records.
    """
    rewritten: list[dict[str, Any]] = []
    for record in records:
        updated = dict(record)
        updated["source_file"] = source_file
        rewritten.append(updated)
    return rewritten


def _chunk_records_from_extraction(
    extraction: Any, schema_config: Any, *, source_file: str, chunk_label: str
) -> tuple[
    list[dict[str, Any]],
    list[dict[str, Any]],
    list[dict[str, Any]],
    list[dict[str, Any]],
]:
    """
    Transform raw LLM extraction objects into structured graph records.

    Args:
        extraction (Any): The Pydantic model containing extracted nodes and edges.
        schema_config (Any): Configuration for normalization and relation labels.
        source_file (str): Identifier for the source document.
        chunk_label (str): Label for the specific text chunk (e.g., 'Chunk 1').

    Returns:
        tuple[list[dict], list[dict], list[dict], list[dict]]: (nodes, edges, claims, fragments).
    """
    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    claims: list[dict[str, Any]] = []
    fragments: list[dict[str, Any]] = []
    for n in extraction.nodes:
        nodes.append(
            {
                "id": str(n.id),
                "label": n.label,
                "file_type": "document",
                "type": n.type,
                "forensic_summary": n.forensic_summary,
                "description": n.forensic_summary,
                "source_file": source_file,
                "source_location": f"{chunk_label}: {n.source_location}"
                if n.source_location
                else chunk_label,
            }
        )
    segment_timestamp = _extract_primary_timestamp(
        chunk_label,
        " ".join((str(n.get("source_location", "")) for n in nodes)),
        " ".join((str(n.get("forensic_summary", "")) for n in nodes)),
    )
    existing_pairs = set()
    acted_by_pairs = set()
    valid_node_ids = {str(n.id) for n in extraction.nodes}
    node_types = {str(n.id): n.type for n in extraction.nodes}
    person_types = {"person", "defendant", "witness", "publicofficial"}
    action_types = {"regulatoryaction"}
    for e in extraction.edges:
        src, tgt = (str(e.source), str(e.target))
        if src not in valid_node_ids or tgt not in valid_node_ids:
            continue
        confidence, confidence_score = _normalize_confidence(
            e.confidence, e.confidence_score
        )
        edge_timestamp = _extract_primary_timestamp(
            getattr(e, "timestamp", None),
            getattr(e, "occurrence_date", None),
            getattr(e, "date", None),
            getattr(e, "source_location", None),
            chunk_label,
            segment_timestamp,
        )
        edges.append(
            {
                "source": src,
                "target": tgt,
                "relation": e.relation,
                "forensic_summary": e.forensic_summary,
                "description": e.forensic_summary,
                "source_file": source_file,
                "source_location": f"{chunk_label}: {e.source_location}"
                if e.source_location
                else chunk_label,
                "timestamp": edge_timestamp,
                "confidence": confidence,
                "confidence_score": confidence_score,
                "weight": 1.0,
            }
        )
        existing_pairs.add(tuple(sorted((src, tgt))))
        src_type = str(node_types.get(src, "")).lower()
        tgt_type = str(node_types.get(tgt, "")).lower()
        if {src_type, tgt_type} & person_types and {src_type, tgt_type} & action_types:
            action_node = src if src_type in action_types else tgt
            person_node = src if src_type in person_types else tgt
            acted_by_pair = tuple(sorted((action_node, person_node)))
            if acted_by_pair not in acted_by_pairs:
                edges.append(
                    {
                        "source": action_node,
                        "target": person_node,
                        "relation": normalize_relation_label(
                            schema_config.relation_types.get("ACTED_BY", "ACTED_BY")
                        ),
                        "source_file": source_file,
                        "source_location": f"{chunk_label}: {e.source_location}"
                        if e.source_location
                        else chunk_label,
                        "timestamp": edge_timestamp,
                        "confidence": "ראיה_ישירה",
                        "confidence_score": 0.95,
                        "weight": 2.0,
                    }
                )
                acted_by_pairs.add(acted_by_pair)
    return (nodes, edges, claims, fragments)


def _community_label(
    member_labels: list[str], member_types: list[str], schema_config: Any
) -> str:
    """
    Generate a descriptive Hebrew/English label for a community based on its members.

    Args:
        member_labels (list[str]): List of labels of nodes in the community.
        member_types (list[str]): List of entity types in the community.
        schema_config (Any): Configuration containing entity type groupings and stopwords.

    Returns:
        str: A generated summary label for the community.
    """
    normalized_types = [
        type_name.lower().replace(" ", "").replace("_", "")
        for type_name in member_types
        if type_name
    ]
    joined = " ".join(member_labels).lower()
    human_types = {
        str(t).lower().replace(" ", "").replace("_", "")
        for t in schema_config.human_entity_types
    }
    organization_types = {
        str(t).lower().replace(" ", "").replace("_", "")
        for t in schema_config.organization_entity_types
    }
    statute_types = {
        str(t).lower().replace(" ", "").replace("_", "")
        for t in schema_config.statute_entity_types
    }
    event_types = {
        str(t).lower().replace(" ", "").replace("_", "")
        for t in schema_config.event_entity_types
    }
    evidence_types = {
        str(t).lower().replace(" ", "").replace("_", "")
        for t in schema_config.evidence_entity_types
    }
    if any((type_name in event_types for type_name in normalized_types)):
        for label, type_name in zip(member_labels, member_types):
            ntype = type_name.lower().replace(" ", "").replace("_", "")
            if ntype in event_types and len(label) > 5:
                return label
    if any(
        (
            "reversal" in label.lower()
            or "remand" in label.lower()
            or "denial" in label.lower()
            for label in member_labels
        )
    ):
        return "Appeal Outcome"
    if any((type_name in statute_types for type_name in normalized_types)):
        if any(
            (
                term in joined
                for term in (
                    "interpretation",
                    "shall",
                    "intent",
                    "meaning",
                    "definition",
                )
            )
        ):
            return "Statutory Interpretation"
        if not any(
            (t in event_types or t in organization_types for t in normalized_types)
        ):
            return "Core Statutes"
    if (
        sum((1 for type_name in normalized_types if type_name in human_types)) >= 2
        and sum(
            (1 for type_name in normalized_types if type_name in organization_types)
        )
        >= 1
    ):
        return "Entities and Parties"
    if (
        sum((1 for type_name in normalized_types if type_name in human_types)) >= 2
        and sum(
            (1 for type_name in normalized_types if type_name in organization_types)
        )
        == 0
    ):
        return "People and Witnesses"
    if (
        sum((1 for type_name in normalized_types if type_name in organization_types))
        >= 2
    ):
        return "Organizations and Groups"
    if any(
        (
            type_name in event_types or type_name in evidence_types
            for type_name in normalized_types
        )
    ):
        return "Investigative Events"
    stopwords = set(schema_config.graph_analysis.community_label_stopwords)
    if not stopwords:
        logging.debug("[GraphManager] No community_label_stopwords found in SCHEMA_CONFIG, using empty set.")
    else:
        pass
    counts = Counter()
    for label in member_labels:
        for token in re.findall("[A-Za-z0-9]+", label.lower()):
            if token in stopwords or token.isdigit() or len(token) <= 2:
                continue
            counts[token] += 1
    top_tokens = [token.title() for token, _ in counts.most_common(3)]
    if not top_tokens:
        return "General Topic"
    if len(top_tokens) == 1:
        return top_tokens[0]
    return " ".join(top_tokens[:2])


def _build_community_labels(G: nx.Graph, communities: dict, schema_config: Any) -> dict:
    """
    Build labels for all communities in a graph.

    Args:
        G (nx.Graph): The graph containing node attributes.
        communities (dict): A mapping of community IDs to lists of node IDs.
        schema_config (Any): Configuration for label generation.

    Returns:
        dict: A mapping of community IDs to generated labels.
    """
    labels = {}
    for community_id, members in communities.items():
        member_labels = [G.nodes[node_id].get("label", node_id) for node_id in members]
        member_types = [G.nodes[node_id].get("type", "") for node_id in members]
        labels[community_id] = _community_label(
            member_labels, member_types, schema_config
        )
    return labels


def detect_communities(graph_data: dict) -> dict:
    """
    Detect communities in the graph using Greedy Modularity.

    Args:
        graph_data (dict): The graph dictionary containing 'nodes' and 'links'/'edges'.

    Returns:
        dict: A mapping of node IDs to community IDs.
    """
    logging.info(fix_text("[GraphManager] זיהוי קהילות יצא לדרך באמצעות - Greedy Modularity..."))
    G = nx.Graph()
    for node in graph_data.get("nodes", []):
        G.add_node(str(node.get("id")))
    for edge in graph_data.get("links", []) or graph_data.get("edges", []):
        G.add_edge(str(edge.get("source")), str(edge.get("target")))
    try:
        communities = greedy_modularity_communities(G)
    except Exception as e:
        logging.error(f"[GraphManager] NetworkX Modularitiy failed: {e}")
        communities = []
    node_to_comm = {}
    for i, comm in enumerate(communities):
        for node_id in comm:
            node_to_comm[str(node_id)] = i
    for node in graph_data.get("nodes", []):
        nid = str(node.get("id"))
        if nid in node_to_comm:
            node["community_id"] = node_to_comm[nid]
    if "graph" not in graph_data:
        graph_data["graph"] = {}
    graph_data["graph"]["communities"] = {
        i: list(comm) for i, comm in enumerate(communities)
    }
    logging.info(fix_text(f"[GraphManager] חולצו {len(communities)} קהילות בגרף בהצלחה."))
    return node_to_comm


async def generate_community_reports(
    graph_data: dict, artifact_store: IArtifactStore | None = None
) -> dict:
    """
    Generate LLM-powered investigative briefings for each community.

    Args:
        graph_data (dict): The complete graph dictionary.

    Returns:
        dict: A mapping of community IDs to Hebrew summaries.
    """
    store = artifact_store or _get_artifact_store()
    logging.info(fix_text("[GraphManager] סוכן LLM מופעל על קהילות (Community Reporting)..."))
    client = _build_azure_client()
    communities = graph_data.get("graph", {}).get("communities", {})
    summaries = {}
    nodes_by_id = {str(n.get("id")): n for n in graph_data.get("nodes", [])}
    edges = graph_data.get("links", []) or graph_data.get("edges", [])
    model = getattr(settings, "azure_openai_model", None) or getattr(
        settings, "openai_model", "gpt-4o"
    )
    for c_id, members in communities.items():
        node_texts = []
        for nid in members:
            data = nodes_by_id.get(str(nid), {})
            lbl = data.get("label", nid)
            fs = data.get("forensic_summary") or data.get("description", "")
            if fs:
                node_texts.append(f"Entity: {lbl} - {fs}")
        edge_texts = []
        for e in edges:
            src = str(e.get("source"))
            tgt = str(e.get("target"))
            if src in members and tgt in members:
                lbl = e.get("relation", "")
                fs = e.get("forensic_summary", "")
                desc = fs if fs else lbl
                edge_texts.append(f"{src} -> {tgt} : {desc}")
        cluster_text = (
            "Entities:\n"
            + "\n".join(node_texts)
            + "\n\nRelations:\n"
            + "\n".join(edge_texts)
        )
        prompt = f"Write an Executive Investigative Briefing for this cluster of entities and relationships.\nAnalyze the core forensic summary and describe the events and key players.\nOutput must be in fluent Hebrew ONLY.\n\nCluster Data:\n{cluster_text}"
        try:
            resp = await client.chat.completions.create(
                model=model,
                messages=[{"role": "system", "content": prompt}],
                temperature=0.3,
            )
            summaries[str(c_id)] = resp.choices[0].message.content
            logging.info(fix_text(f"[GraphManager] סיכום הופק בהצלחה עבור קהילה {c_id}"))
        except Exception as e:
            logging.error(fix_text(f"[GraphManager] שגיאה בהפקת סיכום קהילה {c_id}: {e}"))
    store.write_bytes(
        "community_summaries.json",
        json.dumps(summaries, indent=2, ensure_ascii=False).encode("utf-8"),
    )
    logging.info(fix_text("[GraphManager] סיכומי קהילות שמורים ב- community_summaries.json"))
    return summaries


def save_graph_to_json(
    graph_data: dict[str, Any],
    path: str = "graph.json",
    artifact_store: IArtifactStore | None = None,
) -> None:
    """
    Save graph data to a JSON file with proper Hebrew encoding and community detection.

    Args:
        graph_data (dict[str, Any]): The graph data to save.
        path (str): Artifact path to the output JSON file.
    """
    store = artifact_store or _get_artifact_store()
    detect_communities(graph_data)
    nodes = graph_data.get("nodes", [])
    if isinstance(nodes, list):
        for node in nodes:
            if isinstance(node, dict) and "file_type" not in node:
                node["file_type"] = "document"

    # Compatibility with graphifyy query CLI which expects 'links'
    if "edges" in graph_data and "links" not in graph_data:
        graph_data["links"] = graph_data["edges"]

    store.write_bytes(
        path, json.dumps(graph_data, indent=2, ensure_ascii=False).encode("utf-8")
    )

    try:
        loop = asyncio.get_running_loop()
        loop.create_task(generate_community_reports(graph_data, store))
    except RuntimeError:
        asyncio.run(generate_community_reports(graph_data, store))


def load_graph_from_json(
    graph_path: str | None = None, artifact_store: IArtifactStore | None = None
) -> nx.MultiGraph:
    """
    Load a NetworkX graph from a JSON file.

    Args:
        graph_path (str | None): Artifact path to the graph.json file.

    Returns:
        nx.MultiGraph: The loaded graph instance.

    Raises:
    """
    store = artifact_store or _get_artifact_store()
    resolved_path = graph_path or "graph.json"
    graph_data = json.loads(store.read_bytes(resolved_path).decode("utf-8"))
    graph = nx.MultiGraph()
    for node in graph_data.get("nodes", []):
        if isinstance(node, dict) and node.get("id") is not None:
            node_id = str(node["id"])
            graph.add_node(node_id, **{k: v for k, v in node.items() if k != "id"})
    for edge in graph_data.get("links") or graph_data.get("edges") or []:
        if not isinstance(edge, dict):
            continue
        source = edge.get("source")
        target = edge.get("target")
        if source is None or target is None:
            continue
        graph.add_edge(
            str(source),
            str(target),
            **{k: v for k, v in edge.items() if k not in {"source", "target"}},
        )
    if isinstance(graph_data.get("graph"), dict):
        graph.graph.update(graph_data["graph"])
    for key in ("community_summaries", "token_usage", "entity_resolution"):
        if key in graph_data:
            graph.graph[key] = graph_data[key]
    return graph


def _dedupe_extracted_edges(
    edges: List[Dict[str, Any]], schema_config: Any
) -> List[Dict[str, Any]]:
    """
    Deduplicate and merge edges using a multi-tier confidence retention logic.

    The logic works as follows:
    1. Group edges by (source, target) pairs.
    2. Rank edges within each group based on confidence levels
       (DIRECT_EVIDENCE > CIRCUMSTANTIAL > AMBIGUOUS).
    3. Keep the highest-ranked edge as the primary representative.
    4. Store all other discovered relations for that pair in
       'metadata.all_relations_history' to preserve investigative provenance.
    5. Merge source locations and ensure timestamps are normalized.

    Args:
        edges (List[Dict[str, Any]]): List of raw edges extracted from chunks.
        schema_config (Any): Configuration containing blocked relations and rules.

    Returns:
        List[Dict[str, Any]]: The merged and deduplicated list of edges.
    """
    grouped_edges: dict[tuple[str, str], dict[str, Any]] = {}
    blocked_relations = {
        str(rel).replace(" ", "_").lower()
        for rel in schema_config.blocked_relation_labels
    }

    def confidence_rank(confidence: str) -> int:
        return {
            "ראיה_ישירה": 3,
            "נסיבתי": 2,
            "עמום": 1,
            "EXTRACTED": 3,
            "INFERRED": 2,
            "AMBIGUOUS": 1,
        }.get(str(confidence).upper(), 0)

    for edge in edges:
        source = str(edge.get("source", "")).strip()
        target = str(edge.get("target", "")).strip()
        if not source or not target or source == target:
            continue
        key = (source, target)
        candidate = dict(edge)
        candidate["source"] = source
        candidate["target"] = target
        raw_relation = normalize_relation_label(candidate.get("relation"))
        relation_sanitized = _sanitize_value(raw_relation)
        relation_key = relation_sanitized.replace(" ", "_").lower()
        if (
            not relation_sanitized
            or relation_key in blocked_relations
            or len(relation_sanitized) < 4
        ):
            continue
        candidate["relation"] = relation_sanitized
        candidate["source_location"] = _sanitize_value(candidate.get("source_location"))
        source_file = candidate.get("source_file", "unknown")
        candidate["timestamp"] = _sanitize_value(
            candidate.get("timestamp")
            or candidate.get("occurrence_date")
            or candidate.get("date")
            or _extract_primary_timestamp(candidate.get("source_location"), source_file)
        )
        candidate["confidence"] = _sanitize_value(candidate.get("confidence") or "עמום")
        try:
            raw_score = candidate.get("confidence_score")
            candidate["confidence_score"] = (
                float(raw_score) if raw_score is not None else None
            )
        except (TypeError, ValueError):
            candidate["confidence_score"] = None
        try:
            norm_conf, norm_score = _normalize_confidence(
                candidate.get("confidence"), candidate.get("confidence_score")
            )
            candidate["confidence"] = norm_conf
            candidate["confidence_score"] = norm_score
        except Exception:
            pass
        if not should_keep_edge_by_confidence(
            candidate.get("confidence"),
            candidate.get("confidence_score"),
            candidate.get("relation"),
        ):
            continue
        candidate_quality = candidate["confidence_score"] * float(
            candidate.get("weight") or 1.0
        )
        existing = grouped_edges.get(key)
        candidate_description = (
            candidate.get("description") or candidate.get("forensic_summary") or ""
        )
        if existing is None:
            grouped_edges[key] = {
                "edge": candidate,
                "all_relations_data": [
                    {
                        "relation": candidate["relation"],
                        "quality": candidate_quality,
                        "timestamp": candidate["timestamp"],
                        "source_file": source_file,
                        "source_location": candidate["source_location"],
                        "description": candidate_description,
                    }
                ],
                "source": source,
                "target": target,
                "metadata": dict(candidate.get("metadata", {})),
            }
            continue
        existing["all_relations_data"].append(
            {
                "relation": candidate["relation"],
                "quality": candidate_quality,
                "timestamp": candidate["timestamp"],
                "source_file": source_file,
                "source_location": candidate["source_location"],
                "description": candidate_description,
            }
        )
        if "metadata" in candidate:
            existing["metadata"].update(candidate["metadata"])
        current_edge = existing["edge"]
        current_rank = confidence_rank(current_edge.get("confidence", "AMBIGUOUS"))
        candidate_rank = confidence_rank(candidate["confidence"])
        current_score = float(current_edge.get("confidence_score") or 0.0)
        candidate_score = float(candidate.get("confidence_score") or 0.0)
        if candidate_rank > current_rank or (
            candidate_rank == current_rank and candidate_score > current_score
        ):
            existing["edge"] = candidate
    merged_edges: list[dict[str, Any]] = []
    for item in grouped_edges.values():
        merged_edge = dict(item["edge"])
        merged_edge["source"] = item["source"]
        merged_edge["target"] = item["target"]
        if "metadata" in item:
            merged_edge["metadata"] = item["metadata"]
        sorted_rels = sorted(
            item["all_relations_data"], key=lambda x: x["quality"], reverse=True
        )
        seen_rels = set()
        unique_rels = []
        for r_data in sorted_rels:
            rel_label = r_data["relation"]
            if rel_label not in seen_rels:
                unique_rels.append(rel_label)
                seen_rels.add(rel_label)
        if not unique_rels:
            merged_edge["relation"] = "קשר חקירתי"
            merged_edge["description"] = (
                sorted_rels[0].get("description", "") if sorted_rels else ""
            )
        else:
            # Pick the highest quality unique relation instead of concatenating
            merged_edge["relation"] = unique_rels[0]
            # Ensure we also extract and assign the corresponding description from that same top-tier extraction
            merged_edge["description"] = sorted_rels[0].get("description", "")
        if "metadata" not in merged_edge:
            merged_edge["metadata"] = {}
        merged_edge["metadata"]["all_relations_history"] = item["all_relations_data"]
        merged_edge["metadata"]["source_files"] = list(
            set((r["source_file"] for r in item["all_relations_data"]))
        )
        all_locs = [
            r["source_location"]
            for r in item["all_relations_data"]
            if r.get("source_location")
        ]
        merged_edge["source_location"] = _merge_text_values(all_locs, max_words=20)
        merged_edge["timestamp"] = _sanitize_value(
            merged_edge.get("timestamp")
            or _extract_primary_timestamp(
                merged_edge.get("source_location"), merged_edge.get("source_file")
            )
        )
        merged_edge["weight"] = max(float(merged_edge.get("weight") or 0.0), 1.0)
        merged_edges.append(merged_edge)
    return merged_edges


def _cohesion_scores_for_graph(graph: nx.Graph) -> dict[str, float]:
    """
    Calculate or retrieve cohesion scores for graph communities.

    Args:
        graph (nx.Graph): The NetworkX graph.

    Returns:
        dict[str, float]: Mapping of community IDs to cohesion scores.
    """
    cohesion = graph.graph.get("cohesion")
    if not isinstance(cohesion, dict) or not cohesion:
        communities = graph.graph.get("communities", {})
        cohesion = (
            score_all(graph, communities)
            if isinstance(communities, dict) and communities
            else {}
        )
    normalized: dict[str, float] = {}
    for community_id, score in cohesion.items():
        try:
            normalized[str(community_id)] = float(score)
        except (TypeError, ValueError):
            continue
    return normalized


def prune_small_components(
    nodes: list[dict[str, Any]], edges: list[dict[str, Any]], min_size: int = 3
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], int]:
    """
    Remove connected components that have min_size nodes or fewer.

    Args:
        nodes (list[dict]): List of graph nodes.
        edges (list[dict]): List of graph edges.
        min_size (int): Minimum number of nodes for a component to be kept. Defaults to 3.

    Returns:
        tuple[list[dict], list[dict], int]: (filtered_nodes, filtered_edges, pruned_node_count).
    """
    if not nodes:
        return (nodes, edges, 0)
    temp_G = nx.Graph()
    node_ids = {str(n["id"]) for n in nodes}
    for e in edges:
        node_ids.add(str(e["source"]))
        node_ids.add(str(e["target"]))
    for nid in node_ids:
        temp_G.add_node(nid)
    for e in edges:
        temp_G.add_edge(str(e["source"]), str(e["target"]))
    components = list(nx.connected_components(temp_G))
    nodes_to_remove = set()
    for component in components:
        if len(component) <= min_size:
            nodes_to_remove.update(component)
    filtered_nodes = [n for n in nodes if str(n["id"]) not in nodes_to_remove]
    filtered_edges = [
        e
        for e in edges
        if str(e["source"]) not in nodes_to_remove
        and str(e["target"]) not in nodes_to_remove
    ]
    return (filtered_nodes, filtered_edges, len(nodes_to_remove))
