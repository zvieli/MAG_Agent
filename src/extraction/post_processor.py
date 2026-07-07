import re
from typing import Any, List, Dict
from collections import defaultdict
from src.utils.model_helpers import norm_type_string
from src.models.models import (
    GraphExtraction,
    normalize_relation_label,
    should_keep_edge_by_confidence,
)


def _apply_hebrew_spec(
    extraction: GraphExtraction, schema_config: Any
) -> GraphExtraction:
    """
    Post-process extraction for strict investigative output following Hebrew
    specifications.

    This function applies the following logic:
    - Filters nodes based on a whitelist of allowed entity types.
    - Filters edges based on informative semantic relations and confidence scores.
    - Preserves claims and evidence fragments for investigative provenance.
    - Generates **Synthetic Context Anchors** for co-occurrence clusters where multiple
      entities interact without an explicit semantic relation.
    - Standardizes timestamps and relation labels.

    Args:
        extraction (GraphExtraction): The raw graph extraction object to process.
        schema_config (Any): Configuration object containing whitelists, confidence
            thresholds, and anchor generation rules.

    Returns:
        GraphExtraction: The processed and filtered graph extraction, enriched
            with synthetic anchors and investigative metadata.

    Raises:
        ValidationError: If the resulting data structure fails Pydantic validation.
    """
    try:
        data = (
            extraction.model_dump()
            if hasattr(extraction, "model_dump")
            else extraction.dict()
        )
    except Exception:
        data = extraction if isinstance(extraction, dict) else {}

    allowed_node_types = set(schema_config.entity_types)
    banned_relations = {
        str(rel).replace(" ", "_").lower()
        for rel in schema_config.blocked_relation_labels
    }

    nodes = data.get("nodes", [])
    edges = data.get("edges", [])

    co_occurrence_relations = {
        normalize_relation_label(rel) for rel in schema_config.co_occurrence_relations
    }
    human_types = {
        str(type_name).strip().lower().replace(" ", "").replace("_", "")
        for type_name in schema_config.human_entity_types
    }

    kept_nodes = []
    node_by_id = {}
    for n in nodes:
        ntype = str(n.get("type", "")).strip()
        if ntype in allowed_node_types:
            kept_nodes.append(n)
            node_by_id[n["id"]] = n

    kept_edges = []
    weak_co_occurrence_edges: List[Dict[str, Any]] = []

    event_types_normalized = {
        str(t).lower().replace(" ", "").replace("_", "")
        for t in schema_config.event_entity_types
    }
    event_types_normalized.add("context")
    # Using 'הקשר' as a fixed fallback for event-like context nodes
    event_types_normalized.add("הקשר")

    for e in edges:
        rel = normalize_relation_label(e.get("relation"))
        rel_key = rel.replace(" ", "_").lower()
        rel_sanitized = str(e.get("relation", "")).strip()

        src = e.get("source")
        tgt = e.get("target")
        if src not in node_by_id or tgt not in node_by_id:
            continue

        src_type = norm_type_string(node_by_id[src].get("type", ""))
        tgt_type = norm_type_string(node_by_id[tgt].get("type", ""))

        bridges_shared_context = (
            src_type in event_types_normalized or tgt_type in event_types_normalized
        )

        if not rel_sanitized or len(rel_sanitized) < 4:
            continue

        # Don't drop banned relations if they are investigative (e.g. have specific description)
        is_generic = rel_key in banned_relations
        if is_generic and not bridges_shared_context:
            if rel in co_occurrence_relations:
                if src_type in human_types and tgt_type in human_types:
                    weak_co_occurrence_edges.append(e)
            continue

        e["relation"] = rel_sanitized

        if not should_keep_edge_by_confidence(
            e.get("confidence"), e.get("confidence_score"), e.get("relation")
        ):
            continue

        if not e.get("timestamp") or e.get("timestamp") == "unknown":
            e["timestamp"] = e.get("occurrence_date") or e.get("date") or "1900-01-01"

        kept_edges.append(e)

    anchor_config = schema_config.synthetic_anchor_config
    anchor_type = anchor_config.get("type", "הקשר")

    def _slugify_location(value: Any) -> str:
        text = str(value or "").strip().lower()
        text = re.sub(r"[^a-z0-9\u0590-\u05FF]+", "_", text)
        return text.strip("_") or "source_location"

    def _make_context_anchor(
        source_location: str, source_file: str, participants: List[str] = None
    ) -> Dict[str, Any]:
        anchor_id = f"context_{_slugify_location(source_location)}"
        label = anchor_config.get("label", "מקרה משותף")
        desc = anchor_config.get(
            "description", "עוגן הקשר שנוצר אוטומטית עבור אינטראקציה משותפת."
        )
        if participants:
            desc = f"אינטראקציה משותפת בין {', '.join(participants[:3])} במיקום {source_location}."

        return {
            "id": anchor_id,
            "label": label,
            "type": anchor_type,
            "source_file": source_file,
            "source_location": source_location,
            "description": desc,
            "metadata": {
                "synthetic_anchor": True,
                "anchor_kind": "co_occurrence_context",
            },
        }

    co_occurrence_clusters: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    preserved_edges: List[Dict[str, Any]] = []

    # PERFORMANCE OPTIMIZATION: Index event nodes by location to avoid O(N^2) search
    # This pre-indexing takes O(N), replacing the inner loop in co-occurrence cluster processing.
    event_anchors_by_location = {}
    for n in kept_nodes:
        n_loc = str(n.get("source_location") or "").strip()
        n_type = norm_type_string(n.get("type", ""))
        if n_loc and n_loc != "unknown" and n_type in event_types_normalized:
            if n_loc not in event_anchors_by_location:
                event_anchors_by_location[n_loc] = n

    for edge in kept_edges + weak_co_occurrence_edges:
        edge_relation = normalize_relation_label(edge.get("relation"))
        source_node = node_by_id.get(str(edge.get("source")), {})
        target_node = node_by_id.get(str(edge.get("target")), {})
        source_type = norm_type_string(source_node.get("type", ""))
        target_type = norm_type_string(target_node.get("type", ""))
        is_person_person_co_occurrence = (
            edge_relation in co_occurrence_relations
            and source_type in human_types
            and target_type in human_types
        )
        if is_person_person_co_occurrence:
            cluster_key = str(
                edge.get("source_location") or edge.get("timestamp") or ""
            )
            if cluster_key:
                co_occurrence_clusters[cluster_key].append(edge)
                continue
        preserved_edges.append(edge)

    for location_key, cluster_edges in co_occurrence_clusters.items():
        if not cluster_edges:
            continue

        source_location = str(cluster_edges[0].get("source_location") or "").strip()
        if not source_location or source_location == "unknown":
            preserved_edges.extend(cluster_edges)
            continue

        # Use the pre-indexed map for O(1) lookup instead of O(N) loop
        candidate_anchor = event_anchors_by_location.get(source_location)

        if candidate_anchor is None:
            anchor_id = f"context_{_slugify_location(source_location)}"
            candidate_anchor = node_by_id.get(anchor_id)
            if candidate_anchor is None:
                participants_ids = sorted(
                    {str(edge.get("source")) for edge in cluster_edges}.union(
                        {str(edge.get("target")) for edge in cluster_edges}
                    )
                )
                participant_labels = [
                    node_by_id.get(p_id, {}).get("label", p_id)
                    for p_id in participants_ids
                ]
                candidate_anchor = _make_context_anchor(
                    source_location,
                    str(cluster_edges[0].get("source_file") or "unknown"),
                    participants=participant_labels,
                )
                if candidate_anchor["id"] not in node_by_id:
                    kept_nodes.append(candidate_anchor)
                    node_by_id[candidate_anchor["id"]] = candidate_anchor
                    # Also update our optimization index
                    event_anchors_by_location[source_location] = candidate_anchor

        participants = sorted(
            {str(edge.get("source")) for edge in cluster_edges}.union(
                {str(edge.get("target")) for edge in cluster_edges}
            )
        )
        if not participants:
            preserved_edges.extend(cluster_edges)
            continue

        edge_confidence = max(
            (float(edge.get("confidence_score") or 0.0) for edge in cluster_edges),
            default=0.6,
        )
        edge_timestamp = (
            cluster_edges[0].get("timestamp") or source_location or "1900-01-01"
        )
        edge_source_file = (
            cluster_edges[0].get("source_file")
            or candidate_anchor.get("source_file")
            or "unknown"
        )

        for participant_id in participants:
            if participant_id == candidate_anchor.get("id"):
                continue
            participant_node = node_by_id.get(participant_id)
            if not participant_node:
                continue
            preserved_edges.append(
                {
                    "source": participant_id,
                    "target": candidate_anchor["id"],
                    "relation": normalize_relation_label(
                        anchor_config.get("relation", "PART_OF_SAME_CONTEXT")
                    ),
                    "source_file": edge_source_file,
                    "source_location": source_location,
                    "timestamp": edge_timestamp,
                    "confidence": "INFERRED",
                    "confidence_score": edge_confidence,
                    "weight": 1.0,
                    "metadata": {
                        "synthetic_anchor": True,
                    },
                }
            )

    data["nodes"] = kept_nodes
    data["edges"] = preserved_edges
    return GraphExtraction.model_validate(data)
