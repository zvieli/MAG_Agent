from src.utils.bidi_utils import fix_text
import logging
import json
import re
import difflib
from datetime import datetime
from typing import Any
from collections import Counter
from urllib.parse import urlparse
import networkx as nx
from src.storage.factory import get_artifact_store
from src.extraction.extraction_engine import (
    _build_azure_client,
    resolve_host_ip,
    temporary_dns_override,
)
from src.utils.config import settings
from src.graph.graph_manager import _cohesion_scores_for_graph
from src.models.models import _normalize_confidence, SCHEMA_CONFIG
from src.utils.model_helpers import sanitize_value as _sanitize_value
import asyncio


def _parse_timestamp(ts: str | None) -> datetime | None:
    if not ts or ts == "unknown":
        return None
    try:
        return datetime.strptime(str(ts)[:10], "%Y-%m-%d")
    except (ValueError, TypeError):
        return None


def _analyze_temporal_consistency(graph: nx.Graph) -> list[dict[str, Any]]:
    anomalies = []
    cause_relations = {
        str(rel).replace(" ", "_").upper() for rel in SCHEMA_CONFIG.cause_relations
    }
    for u, v, data in graph.edges(data=True):
        relation = str(data.get("relation", "")).replace(" ", "_").upper()
        if relation not in cause_relations:
            continue
        source_node_ts = _parse_timestamp(graph.nodes[u].get("event_date"))
        target_node_ts = _parse_timestamp(graph.nodes[v].get("event_date"))
        if source_node_ts and target_node_ts:
            if source_node_ts > target_node_ts:
                anomalies.append(
                    {
                        "source": graph.nodes[u].get("label", u),
                        "target": graph.nodes[v].get("label", v),
                        "relation": data.get("relation"),
                        "source_date": source_node_ts.strftime("%Y-%m-%d"),
                        "target_date": target_node_ts.strftime("%Y-%m-%d"),
                        "issue": "Cause occurs after Effect",
                    }
                )
    return anomalies


def _analyze_entity_resolution_health(graph: nx.Graph) -> list[dict[str, Any]]:
    potential_duplicates = []
    nodes_by_type = {}
    human_types = {t.lower() for t in SCHEMA_CONFIG.human_entity_types}
    for node_id, attrs in graph.nodes(data=True):
        node_type = attrs.get("type", "Unknown")
        nodes_by_type.setdefault(node_type, []).append(
            (node_id, attrs.get("label", node_id), node_type)
        )
    for node_type, nodes in nodes_by_type.items():
        if len(nodes) < 2:
            continue
        is_human = str(node_type).lower() in human_types
        is_event = str(node_type).lower() in {"אירוע", "event", "context"}
        merge_threshold = (
            SCHEMA_CONFIG.resolution_thresholds.person
            if is_human
            else SCHEMA_CONFIG.resolution_thresholds.other
        )
        reporting_floor = merge_threshold - 0.02
        for i in range(len(nodes)):
            for j in range(i + 1, len(nodes)):
                id1, label1, _ = nodes[i]
                id2, label2, _ = nodes[j]
                if not label1 or not label2:
                    continue

                l1, l2 = (str(label1).lower(), str(label2).lower())

                # Skip expected similarities for evidence fragments and claims
                if (
                    id1.startswith("fragment_")
                    or id1.startswith("evidence_")
                    or id2.startswith("fragment_")
                    or id2.startswith("evidence_")
                ):
                    continue
                if (
                    node_type == "Claim"
                    or id1.startswith("claim_")
                    or id2.startswith("claim_")
                ):
                    continue
                if (
                    node_type in ["DATE", "תאריך"]
                    or id1.startswith("date_")
                    or id2.startswith("date_")
                ):
                    continue
                # Aggressive Skip: If labels are primarily numeric/date-like, they are valid variations
                if re.match(r"^[\d./-]{5,}$", l1) or re.match(r"^[\d./-]{5,}$", l2):
                    continue

                if is_event:
                    d1 = graph.nodes[id1].get("event_date")
                    d2 = graph.nodes[id2].get("event_date")
                    if d1 and d2 and (d1 != d2):
                        continue
                    # Extra guard: If labels contain different years or participants, they are likely distinct
                    years1 = set(re.findall(r"\b(20\d{2})\b", l1))
                    years2 = set(re.findall(r"\b(20\d{2})\b", l2))
                    if years1 and years2 and years1 != years2:
                        continue

                similarity = difflib.SequenceMatcher(None, l1, l2).ratio()
                is_duplicate = False
                reason = ""
                if reporting_floor <= similarity <= 0.98:
                    is_duplicate = True
                    reason = "High similarity (Near-merge)"
                elif is_human:
                    if len(l1) >= 3 and len(l2) >= 3:
                        if (l1 in l2 or l2 in l1) and similarity > 0.4:
                            is_duplicate = True
                            reason = "Surname/Partial match"
                if is_duplicate:
                    potential_duplicates.append(
                        {
                            "type": node_type,
                            "entity1": label1,
                            "entity2": label2,
                            "similarity": round(similarity, 3),
                            "reason": reason,
                        }
                    )
    return potential_duplicates[:25]


def _analyze_citation_integrity(graph: nx.Graph) -> list[dict[str, Any]]:
    broken_citations = []

    # Get chunk IDs directly from graph metadata
    graph_chunk_ids = graph.graph.get("chunk_ids", [])
    chunk_ids = set()
    if isinstance(graph_chunk_ids, (list, tuple, set)):
        chunk_ids = {str(chunk_id) for chunk_id in graph_chunk_ids}

    for u, v, data in graph.edges(data=True):
        source_loc = data.get("source_location")
        if source_loc and source_loc != "unknown" and chunk_ids:
            match = re.search("chunk_\\d+", str(source_loc))
            if match:
                chunk_id = match.group(0)
                if chunk_id not in chunk_ids:
                    broken_citations.append(
                        {
                            "edge": f"{u} -> {v}",
                            "location": source_loc,
                            "issue": "Chunk ID not found in index",
                        }
                    )
    return broken_citations[:20]


def _analyze_blind_spots(graph: nx.Graph) -> list[str]:
    blind_spots = []
    try:
        store = get_artifact_store()
        if not store.exists(".graphify_extract.json"):
            return [
                "Metadata file (.graphify_extract.json) not found in storage for blind spot analysis."
            ]

        raw_data = store.read_bytes(".graphify_extract.json")
        extractions = json.loads(raw_data.decode("utf-8"))

        all_mentioned_labels = set()
        for entry in extractions:
            nodes = entry.get("nodes", [])
            for node in nodes:
                if isinstance(node, dict):
                    label = node.get("label")
                    if label:
                        all_mentioned_labels.add(str(label).lower())
        graph_labels = {
            str(attrs.get("label", node_id)).lower()
            for node_id, attrs in graph.nodes(data=True)
        }
        missing = all_mentioned_labels - graph_labels
        # Filter out common short words and non-investigative terms
        missing = [m for m in missing if len(m) > 3]
        if missing:
            blind_spots = sorted(missing, key=len, reverse=True)[:15]
    except Exception as e:
        return [f"Error during blind spot analysis: {str(e)}"]
    return blind_spots


def _calculate_intermediary_density(graph: nx.Graph) -> float:
    human_types = {t.lower() for t in SCHEMA_CONFIG.human_entity_types}
    human_nodes = [
        node_id
        for node_id, attrs in graph.nodes(data=True)
        if str(attrs.get("type", "")).lower() in human_types
    ]
    if not human_nodes:
        return 0.0
    intermediaries = [node_id for node_id in human_nodes if graph.degree(node_id) > 1]
    return len(intermediaries) / len(human_nodes) * 100.0


def _sanitize_summary_fragment(text: str) -> str:
    cleaned = " ".join(str(text).split()).strip()
    return cleaned.strip(" ,;:.")


def _rank_community_members(
    G: nx.Graph, members: list[str], limit: int = 12
) -> list[str]:
    member_set = {str(member) for member in members}
    ranked = sorted(
        member_set,
        key=lambda node_id: (
            -G.degree(node_id) if node_id in G else 0,
            str(G.nodes[node_id].get("label", node_id))
            if node_id in G
            else str(node_id),
            str(node_id),
        ),
    )
    return ranked[: max(1, min(limit, len(ranked)))]


def _community_context_edges(
    G: nx.Graph, members: list[str], limit: int = 18
) -> list[str]:
    member_set = set(members)
    edge_rows: list[tuple[int, str]] = []
    for u, v, data in G.subgraph(member_set).edges(data=True):
        u_label = _sanitize_summary_fragment(G.nodes[u].get("label", u))
        v_label = _sanitize_summary_fragment(G.nodes[v].get("label", v))
        relation = _sanitize_summary_fragment(data.get("relation", "related_to"))
        degree_score = (G.degree(u) if u in G else 0) + (G.degree(v) if v in G else 0)
        edge_rows.append((degree_score, f"{u_label} --({relation})--> {v_label}"))
    edge_rows.sort(key=lambda item: item[0], reverse=True)
    return [row for _, row in edge_rows[:limit]]


async def summarize_communities(G, communities, community_labels):
    from src.graph.local_graphify import god_nodes
    from src.extraction.extraction_engine import resolve_host_ip, temporary_dns_override

    summaries: dict[str, str] = {}
    seen_titles = Counter()
    gods = god_nodes(G, top_n=5)
    client = _build_azure_client()
    model = settings.graphify_extraction_model
    llm_min_size = SCHEMA_CONFIG.graph_analysis.llm_summary_min_size
    semaphore = asyncio.Semaphore(5)

    endpoint = (
        settings.azure_openai_endpoint or settings.openai_base_url or "<unset>"
    )
    endpoint_host = ""
    if endpoint != "<unset>":
        parsed_endpoint = urlparse(endpoint)
        endpoint_host = parsed_endpoint.hostname or endpoint.split("/")[0]
    resolved_ip = await asyncio.to_thread(resolve_host_ip, endpoint_host) if endpoint_host else None

    async def _summarize_single_community(community_id, members):
        if len(members) < llm_min_size:
            degrees = dict(G.degree(members))
            top_node_id = max(degrees, key=degrees.get) if degrees else members[0]
            label = G.nodes[top_node_id].get("label", top_node_id)
            return (community_id, label, f"קהילה מצומצמת המרוכזת סביב {label}.", False)
        selected_members = _rank_community_members(G, list(members), limit=12)
        node_contexts: list[str] = []
        edge_contexts: list[str] = []
        community_gods = [g for g in gods if g["id"] in selected_members]
        for node_id in selected_members:
            node_data = G.nodes[node_id] if node_id in G else {}
            label = _sanitize_summary_fragment(node_data.get("label", node_id))
            description = _sanitize_summary_fragment(node_data.get("description", ""))[
                :140
            ]
            node_contexts.append(f"Entity: {label} (Description: {description})")
        edge_contexts.extend(_community_context_edges(G, selected_members, limit=18))
        anchor_instruction = ""
        if community_gods:
            anchor_names = ", ".join([g["label"] for g in community_gods])
            anchor_instruction = f"קהילה זו כוללת ישויות מפתח: {anchor_names}. הסיכום והכותרת חייבים להתמקד בתפקידן באשכול זה."

        system_prompt = await settings.get_prompt("community_summary_system")
        community_summary_user = await settings.get_prompt("community_summary_user")
        user_prompt = community_summary_user.format(
            entities=chr(10).join(node_contexts),
            relationships=chr(10).join(edge_contexts),
            anchor_instruction=anchor_instruction,
        )

        async with semaphore:
            for attempt in range(3):
                try:
                    with temporary_dns_override(endpoint_host, resolved_ip):
                        response = await client.chat.completions.create(
                            model=model,
                            messages=[
                                {"role": "system", "content": system_prompt},
                                {"role": "user", "content": user_prompt},
                            ],
                            temperature=0 if attempt == 0 else 0.2,
                            max_tokens=2048,
                            response_format={"type": "json_object"},
                            timeout=settings.llm_request_timeout_seconds,
                        )
                    content = response.choices[0].message.content or "{}"
                    try:
                        data = json.loads(content)
                    except json.JSONDecodeError:
                        from src.extraction.extraction_engine import (
                            _extract_json_payload,
                            _repair_json_nodes,
                        )

                        payload = _extract_json_payload(content)
                        payload = _repair_json_nodes(payload)
                        data = json.loads(payload)
                    title = _sanitize_summary_fragment(
                        data.get("title", "קהילה ללא כותרת")
                    )
                    summary = _sanitize_summary_fragment(data.get("summary", ""))
                    return (community_id, title, summary, True)
                except Exception:
                    if attempt < 2:
                        await asyncio.sleep(1 + attempt)
                        continue
                    fallback_label = f"קהילה {community_id}"
                    return (
                        community_id,
                        fallback_label,
                        "לא ניתן היה להפיק סיכום עבור קהילה זו.",
                        False,
                    )

    tasks = [
        _summarize_single_community(cid, mems) for cid, mems in communities.items()
    ]
    results = await asyncio.gather(*tasks)
    for cid, title, summary, is_llm in results:
        if is_llm:
            seen_titles[title] += 1
            if seen_titles[title] > 1:
                title = f"{title} ({seen_titles[title]})"
        community_labels[cid] = title
        summaries[str(cid)] = summary
    return summaries


def analyze_graph_health(graph: nx.Graph) -> dict[str, Any]:
    node_count = graph.number_of_nodes()
    edge_count = graph.number_of_edges()
    degrees = dict(graph.degree())
    isolated_nodes_count = sum((1 for degree in degrees.values() if degree == 0))
    average_degree = sum(degrees.values()) / node_count if node_count else 0.0
    simple_graph = nx.Graph()
    simple_graph.add_nodes_from(graph.nodes())
    simple_graph.add_edges_from(graph.edges())
    components = sorted(nx.connected_components(simple_graph), key=len, reverse=True)
    giant_component_nodes = len(components[0]) if components else 0
    giant_component_ratio = giant_component_nodes / node_count if node_count else 0.0
    isolated_ratio = isolated_nodes_count / node_count if node_count else 0.0
    description_count = sum(
        (
            1
            for _, attrs in graph.nodes(data=True)
            if _sanitize_value(attrs.get("description"))
        )
    )
    description_coverage = description_count / node_count if node_count else 0.0
    # Reporting-level confidence mapping (Display only)
    CONFIDENCE_DISPLAY_MAP = {
        "DIRECT": "ראיה ישירה",
        "INFERRED": "נסיבתי",
        "AMBIGUOUS": "עמום",
        "HEARSAY": "מפי השמועה",
    }

    confidence_counts = Counter(
        (
            _normalize_confidence(
                attrs.get("confidence"), attrs.get("confidence_score")
            )[0]
            for _, _, attrs in graph.edges(data=True)
        )
    )

    # Map to Hebrew display labels while preserving counts
    display_confidence_counts = Counter(
        {CONFIDENCE_DISPLAY_MAP.get(k, k): v for k, v in confidence_counts.items()}
    )

    confidence_total = sum(confidence_counts.values())
    extracted_ratio = (
        (confidence_counts.get("DIRECT", 0) + confidence_counts.get("EXTRACTED", 0))
        / confidence_total
        if confidence_total
        else 0.0
    )
    ambiguous_ratio = (
        confidence_counts.get("AMBIGUOUS", 0) / confidence_total
        if confidence_total
        else 0.0
    )
    communities_data = (
        graph.graph.get("communities", {})
        if isinstance(graph.graph.get("communities", {}), dict)
        else {}
    )
    if not communities_data:
        community_counts: Counter[str] = Counter()
        for node_id, attrs in graph.nodes(data=True):
            community_id = attrs.get("community")
            if community_id is not None:
                community_counts[str(community_id)] += 1
        communities_data = {
            int(cid) if str(cid).isdigit() else cid: [] for cid in community_counts
        }
        for node_id, attrs in graph.nodes(data=True):
            community_id = attrs.get("community")
            if community_id is not None:
                communities_data.setdefault(community_id, []).append(node_id)
    community_summaries = graph.graph.get("community_summaries", {})
    if not isinstance(community_summaries, dict):
        community_summaries = {}
    top_communities = [
        (community_id, members)
        for community_id, members in sorted(
            communities_data.items(), key=lambda item: len(item[1]), reverse=True
        )
        if len(members) > 3
    ][:3]
    community_breakdown: list[dict[str, Any]] = []
    summary_hits = 0
    cohesion_scores = _cohesion_scores_for_graph(graph)
    average_cohesion = (
        round(sum(cohesion_scores.values()) / len(cohesion_scores), 2)
        if cohesion_scores
        else 0.0
    )
    for community_id, members in top_communities:
        summary = _sanitize_value(community_summaries.get(str(community_id), ""))
        if summary:
            summary_hits += 1
        community_breakdown.append(
            {
                "community_id": str(community_id),
                "size": len(members),
                "summary": summary or "No community_summary available.",
                "cohesion": cohesion_scores.get(str(community_id)),
            }
        )
    community_summary_coverage = (
        summary_hits / len(top_communities) if top_communities else 0.0
    )
    temporal_anomalies = _analyze_temporal_consistency(graph)
    potential_duplicates = _analyze_entity_resolution_health(graph)
    broken_citations = _analyze_citation_integrity(graph)
    blind_spots = _analyze_blind_spots(graph)
    intermediary_density = _calculate_intermediary_density(graph)
    connectivity_score = (
        0.45 * giant_component_ratio
        + 0.35 * (1.0 - isolated_ratio)
        + 0.2 * min(1.0, average_degree / 2.0)
    ) * 100.0
    semantic_score = description_coverage * 100.0
    confidence_score = (0.7 * (1.0 - ambiguous_ratio) + 0.3 * extracted_ratio) * 100.0
    community_score = community_summary_coverage * 100.0
    integrity_penalty = (
        len(temporal_anomalies) * 10
        + len(broken_citations) * 5
        + len(potential_duplicates) * 3
    )
    god_node_split_penalty = 0
    node_degrees = dict(graph.degree())
    for d in potential_duplicates:
        e1_id = next(
            (
                n
                for n, attrs in graph.nodes(data=True)
                if attrs.get("label") == d["entity1"]
            ),
            None,
        )
        e2_id = next(
            (
                n
                for n, attrs in graph.nodes(data=True)
                if attrs.get("label") == d["entity2"]
            ),
            None,
        )
        if e1_id and e2_id:
            if node_degrees.get(e1_id, 0) > 40 or node_degrees.get(e2_id, 0) > 40:
                god_node_split_penalty += 20
    identity_collapse_detected = False
    for node_id, attrs in graph.nodes(data=True):
        metadata = attrs.get("metadata", {})
        aliases_data = metadata.get("resolved_aliases_data", [])
        if not aliases_data:
            continue
        distinct_first_names = set()
        for alias in aliases_data:
            label = alias.get("original_label", "")
            tokens = str(label).split()
            if tokens and len(tokens) >= 2 and (len(tokens[0]) > 2):
                name = tokens[0].replace('"', "").replace("'", "")
                if not any(
                    (
                        name in existing or existing in name
                        for existing in distinct_first_names
                    )
                ):
                    distinct_first_names.add(name)
        if len(distinct_first_names) > 1:
            identity_collapse_detected = True
            break
    depth_bonus = (
        min(15, intermediary_density / 2.0) if intermediary_density > 20 else 0
    )
    if identity_collapse_detected:
        final_score = 0
    else:
        final_score = max(
            1,
            min(
                100,
                round(
                    0.3 * connectivity_score
                    + 0.2 * semantic_score
                    + 0.15 * confidence_score
                    + 0.15 * community_score
                    + 0.2
                    * (100.0 - min(100, integrity_penalty + god_node_split_penalty))
                    + depth_bonus
                ),
            ),
        )
    return {
        "node_count": node_count,
        "edge_count": edge_count,
        "identity_collapse": identity_collapse_detected,
        "connectivity": {
            "isolated_nodes_count": isolated_nodes_count,
            "isolated_nodes_percent": isolated_nodes_count / node_count * 100.0
            if node_count
            else 0.0,
            "average_degree": average_degree,
            "giant_component_nodes": giant_component_nodes,
            "giant_component_percent": giant_component_nodes / node_count * 100.0
            if node_count
            else 0.0,
            "island_nodes_count": max(0, node_count - giant_component_nodes),
            "island_nodes_percent": max(0, node_count - giant_component_nodes)
            / node_count
            * 100.0
            if node_count
            else 0.0,
        },
        "semantic": {
            "description_coverage_count": description_count,
            "description_coverage_percent": description_coverage * 100.0,
            "confidence_counts": dict(display_confidence_counts),
            "confidence_percentages": {
                key: value / confidence_total * 100.0 if confidence_total else 0.0
                for key, value in display_confidence_counts.items()
            },
        },
        "investigative": {
            "temporal_anomalies": temporal_anomalies,
            "potential_duplicates": potential_duplicates,
            "broken_citations": broken_citations,
            "blind_spots": blind_spots,
            "intermediary_density": intermediary_density,
        },
        "communities": community_breakdown,
        "cohesion": {"average_cohesion": average_cohesion, "scores": cohesion_scores},
        "final_quality_score": final_score,
    }


def report_graph_health(graph: nx.Graph) -> dict[str, Any]:
    logging.info(fix_text("\n" + "=" * 88 + "\nבדיקת תקינות גרף החקירה\n" + "=" * 88))
    pruned_count = graph.graph.get("pruned_node_count", 0)
    if pruned_count > 0:
        logging.info(
            fix_text(
                str(
                    f"ניקוי מבני: הוסרו {pruned_count} צמתים מרכיבים קטנים (<=3)\n"
                    + "-" * 80
                )
            )
        )
    report = analyze_graph_health(graph)
    connectivity = report["connectivity"]
    semantic = report["semantic"]
    communities = report["communities"]
    logging.info(fix_text(str("מדדי קישוריות\n" + "-" * 80)))
    logging.info(
        fix_text(
            str(
                f"צמתים מבודדים: {connectivity['isolated_nodes_count']} ({connectivity['isolated_nodes_percent']:.1f}% מכלל הצמתים)"
            )
        )
    )
    logging.info(fix_text(
str(f"ממוצע דרגה: {connectivity['average_degree']:.2f}")))
    logging.info(
        fix_text(
            str(
                f"רכיב מקושר מרכזי: {connectivity['giant_component_percent']:.1f}% ({connectivity['giant_component_nodes']} מתוך {report['node_count']} צמתים)"
            )
        )
    )
    logging.info(
        fix_text(
            str(
                f"צמתים באיים: {connectivity['island_nodes_count']} ({connectivity['island_nodes_percent']:.1f}% מכלל הצמתים)\n"
            )
        )
    )
    logging.info(fix_text(
str("איכות סמנטית\n" + "-" * 80)))
    logging.info(
        fix_text(
            str(
                f"כיסוי תיאורי: {semantic['description_coverage_percent']:.1f}% ({semantic['description_coverage_count']} מתוך {report['node_count']} צמתים)"
            )
        )
    )
    logging.info(fix_text(
str("התפלגות ביטחון:")))
    for label in ("ראיה ישירה", "נסיבתי", "עמום", "מפי השמועה"):
        count = semantic["confidence_counts"].get(label, 0)
        percent = semantic["confidence_percentages"].get(label, 0.0)
        if count > 0:
            logging.info(fix_text(
str(f"  {label}: {count} ({percent:.1f}%)")))
    logging.info(fix_text(
str("\nפירוט קהילות (אשכולות מידע)\n" + "-" * 80)))
    if communities:
        for index, community in enumerate(communities, start=1):
            logging.info(
                fix_text(
                    str(
                        f"{index}. קהילה {community['community_id']} - גודל: {community['size']}"
                    )
                )
            )
            logging.info(
                fix_text(
                    str(f"   סיכום: {community['summary'] or 'אין סיכום זמין לקהילה.'}")
                )
            )
    else:
        logging.info(fix_text(
str("לא נמצאו קהילות הגדולות מ-3 צמתים.")))
    logging.info(fix_text(
str("\nיושרה חקירתית וניתוח עומק\n" + "-" * 80)))
    investigative = report.get("investigative", {})
    logging.info(
        fix_text(
            str(
                f"צפיפות מתווכים (Intermediary Density): {investigative.get('intermediary_density', 0.0):.1f}%"
            )
        )
    )
    anomalies = investigative.get("temporal_anomalies", [])
    if anomalies:
        logging.info(
            fix_text(
                str(
                    f"⚠️  נמצאו {len(anomalies)} אנומליות כרונולוגיות (סיבה מופיעה אחרי תוצאה):"
                )
            )
        )
        for a in anomalies[:5]:
            logging.info(
                fix_text(
                    str(
                        f"  - {a['source']} ({a['source_date']}) --[{a['relation']}]--> {a['target']} ({a['target_date']})"
                    )
                )
            )
    else:
        logging.info(fix_text(
str("✅ לא נמצאו אנומליות כרונולוגיות במבנה הסיבתי.")))
    broken = investigative.get("broken_citations", [])
    if broken:
        logging.info(
            fix_text(str(f"⚠️  נמצאו {len(broken)} סימוכין שבורים (קובץ או צ'אנק חסר):"))
        )
        for b in broken[:5]:
            logging.info(
                fix_text(
                    str(
                        f"  - {b['edge']}: {b['issue']} ({b.get('file') or b.get('location')})"
                    )
                )
            )
    else:
        logging.info(fix_text(
str("✅ כל הסימוכין תקינים ומקושרים לקבצי מקור קיימים.")))
    dupes = investigative.get("potential_duplicates", [])
    if dupes:
        logging.info(
            fix_text(
                str(
                    f"🔍 חשד לכפילויות (Entity Resolution Health): {len(dupes)} זוגות דומים"
                )
            )
        )
        for d in dupes[:5]:
            reason_str = f" ({d['reason']})" if "reason" in d else ""
            logging.info(
                fix_text(
                    str(
                        f"  - {d['entity1']} <-> {d['entity2']} (דמיון: {d['similarity']}){reason_str}"
                    )
                )
            )
    blind_spots = investigative.get("blind_spots", [])
    if (
        blind_spots
        and isinstance(blind_spots, list)
        and (not str(blind_spots[0]).startswith("Metadata"))
    ):
        logging.info(
            fix_text(
                str(
                    f"🕵️  שטחים מתים (Blind Spots): {len(blind_spots)} ישויות שהוזכרו בטקסט אך לא נכנסו לגרף\n  דוגמאות: {', '.join(blind_spots[:8])}"
                )
            )
        )
    logging.info(fix_text(
str("\nלכידות קהילה\n" + "-" * 80)))
    cohesion = report.get("cohesion", {})
    cohesion_scores = cohesion.get("scores", {})
    if cohesion_scores:
        logging.info(
            fix_text(
                str(f"Average Cohesion: {cohesion.get('average_cohesion', 0.0):.2f}")
            )
        )
        for community in communities:
            community_cohesion = community.get("cohesion")
            if community_cohesion is not None:
                logging.info(
                    fix_text(
                        str(
                            f"  Community {community['community_id']}: {community_cohesion:.2f} ({community['size']} nodes)"
                        )
                    )
                )
    else:
        logging.info(fix_text(
str("אין ציוני לכידות זמינים.")))
    logging.info(fix_text(
str("\nציון איכות סופי\n" + "-" * 80)))
    if report.get("identity_collapse"):
        logging.info(
            fix_text(
                str(
                    "🚨 CRITICAL: IDENTITY COLLAPSE DETECTED (Conflicting identities merged)"
                )
            )
        )
    logging.info(
        fix_text(
            str(
                f"ציון: {report['final_quality_score']}/100\nציון היוריסטי המבוסס על קישוריות, כיסוי תיאורי, התפלגות ביטחון וסיכומי קהילה\n"
            )
        )
    )
    graph.graph["health_report"] = report
    return report
