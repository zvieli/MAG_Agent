import re
from typing import Any, Dict, List
from src.models.models import SCHEMA_CONFIG


async def calculate_groundedness(answer: str, search_mgr: Any = None) -> float:
    """
    Calculate groundedness by verifying citations in the answer against live Azure AI Search data.

    This metric validates how well the generated answer is supported by source chunks.
    It supports multiple citation formats:
    1. Legacy: [Source: ID] or [מקור: ID]
    2. Strict (Flexible): (Source: filename, Fragment: node_id) or (מקור: filename, מיקום: node_id)

    Args:
        answer: The generated textual answer containing citations.
        search_mgr: Optional SearchManager instance for live verification.

    Returns:
        A percentage score (0.0 to 100.0) representing the ratio of valid citations.
    """
    from src.utils.config import settings

    config = SCHEMA_CONFIG.analysis_config

    # 1. Define patterns
    outer_pattern = config.citation_patterns.get(
        "outer", r"\[(?:Source|מקור):\s*([^\]]+)\]"
    )
    inner_pattern = config.citation_patterns.get(
        "inner_ids", r"(\b[a-fA-F0-9]{64}(?:\.txt)?\b|\bchunk_\d+\b|\bChunk\s+\d+\b)"
    )

    strict_pattern = (
        r"\(Source:\s*([^,\)]+)(?:,\s*(?:Fragment|Chunk)\s*:?\s*([^)]+))?\)"
    )
    hebrew_strict_pattern = r"\((?:מקור|Source):\s*([^,\)]+)(?:,\s*(?:מיקום|מקטע|Fragment|Chunk)\s*:?\s*([^)]+))?\)"

    # 2. Extract citations into verification groups
    citation_groups = []

    # Extract from legacy brackets
    for content in re.findall(outer_pattern, answer):
        ids = re.findall(inner_pattern, content, re.IGNORECASE)
        for cid in ids:
            citation_groups.append([cid.strip()])

    # Extract from strict parentheses (English and Hebrew)
    for src, frag in re.findall(strict_pattern, answer):
        frags = [f.strip() for f in frag.split(",")] if frag else []
        citation_groups.append([src.strip()] + frags)

    for src, frag in re.findall(hebrew_strict_pattern, answer):
        frags = [f.strip() for f in frag.split(",")] if frag else []
        citation_groups.append([src.strip()] + frags)

    if not citation_groups:
        return 0.0

    valid_count = 0
    total_refs = len(citation_groups)

    # 3. Live Verification against Azure AI Search
    # If search_mgr is not provided, we can't fully verify, so we return 0.0 or a heuristic
    if not search_mgr:
        from src.storage.search_manager import SearchManager

        search_mgr = SearchManager()

    from azure.search.documents.aio import SearchClient

    async with SearchClient(
        endpoint=search_mgr.endpoint,
        index_name=settings.azure_search_index,
        credential=search_mgr.credential,
    ) as client:
        for id_list in citation_groups:
            is_ref_valid = False
            for cid in id_list:
                # Direct check by ID
                try:
                    # Sanitize ID for Search
                    from src.storage.search_manager import SearchManager

                    sanitized_id = SearchManager._sanitize_id(cid)
                    await client.get_document(key=sanitized_id)
                    is_ref_valid = True
                    break
                except Exception:
                    # Not found by ID, try searching by filename or content
                    results = await client.search(search_text=cid, top=1)
                    async for _ in results:
                        is_ref_valid = True
                        break
                    if is_ref_valid:
                        break

            if is_ref_valid:
                valid_count += 1

    return (valid_count / total_refs) * 100 if total_refs > 0 else 0.0


def calculate_graph_recall(
    answer: str, graph: Dict[str, Any], question: str | None = None
) -> float:
    """
    Calculate structural recall based on entity and relationship presence in the answer.

    Uses a context-window approach to identify if relationships (edges) from the graph
    are represented in the text. Supports:
    1. Direct relation label matching.
    2. 2-hop logic: If entities connected via an intermediate node appear in proximity.
    3. Hebrew prefix handling (e.g., 'and-Label').
    4. Temporal window expansion: Increases sliding window size for timeline queries.

    Args:
        answer: The generated textual answer.
        graph: The Knowledge Graph dictionary.
        question: Optional original question to trigger temporal heuristics.

    Returns:
        A percentage score (0.0 to 100.0) representing graph edge coverage.
    """
    nodes = graph.get("nodes", [])
    links = graph.get("links", []) or graph.get("edges", [])
    config = SCHEMA_CONFIG.analysis_config

    # Map label -> id and id -> label
    id_to_label = {}
    label_to_id = {}
    for node in nodes:
        label = node.get("label")
        node_id = node.get("id")
        if label:
            id_to_label[node_id] = label
            label_to_id[label] = node_id

    # Adjacency list for 2-hop logic
    adj = {}
    for edge in links:
        src = edge.get("source")
        tgt = edge.get("target")
        if src not in adj:
            adj[src] = set()
        if tgt not in adj:
            adj[tgt] = set()
        adj[src].add(tgt)
        adj[tgt].add(src)

    # Identify which nodes are mentioned in the answer
    mentioned_ids = set()
    # Sanitize answer
    clean_answer = re.sub(r'["\'\”\“\‘\’]', "", answer)

    # Hebrew prefixes from config
    prefixes = config.hebrew_prefixes

    for label, node_id in label_to_id.items():
        clean_label = re.sub(r'["\'\”\“\‘\’]', "", label)
        # Match with optional Hebrew prefix and ensure word boundaries or non-Hebrew characters
        pattern = rf"(?:^|[^א-ת]){prefixes}{re.escape(clean_label)}(?:$|[^א-ת])"
        if re.search(pattern, clean_answer):
            mentioned_ids.add(node_id)

    if not mentioned_ids:
        return 0.0

    # Relevant edges: direct edges where both endpoints are mentioned
    relevant_edges = [
        edge
        for edge in links
        if edge.get("source") in mentioned_ids and edge.get("target") in mentioned_ids
    ]

    if not relevant_edges:
        return 0.0

    sentences = [s.strip() for s in re.split(r"[.!?\n]", answer) if s.strip()]

    # Context window size
    window_size = 3
    if question:
        timeline_keywords = config.temporal_keywords
        if any(kw in question.lower() for kw in timeline_keywords):
            window_size = 8

    found_edges = 0
    seen_relations = set()

    for edge in relevant_edges:
        rel = edge.get("relation", "")
        src_id = edge.get("source")
        tgt_id = edge.get("target")
        src_label = id_to_label.get(src_id, "")
        tgt_label = id_to_label.get(tgt_id, "")

        edge_sig = f"{rel}-{src_id}-{tgt_id}"
        if edge_sig in seen_relations:
            continue

        is_found = False
        # 1. Relation label appears in text
        if rel and rel in answer:
            is_found = True
        else:
            # 2. Both entities appear within a sliding window
            # OR they are 2-hop connected and appear in window
            for i in range(len(sentences)):
                window = " ".join(sentences[i : i + window_size])

                # Check if src and tgt are both in window (with prefixes)
                pattern_src = (
                    rf"(?:^|[^א-ת]){prefixes}{re.escape(src_label)}(?:$|[^א-ת])"
                )
                pattern_tgt = (
                    rf"(?:^|[^א-ת]){prefixes}{re.escape(tgt_label)}(?:$|[^א-ת])"
                )

                if re.search(pattern_src, window) and re.search(pattern_tgt, window):
                    # Check for 1-hop or 2-hop connection
                    if tgt_id in adj.get(src_id, set()):
                        is_found = True
                        break
                    # 2-hop logic: find if there's a common neighbor
                    neighbors_src = adj.get(src_id, set())
                    neighbors_tgt = adj.get(tgt_id, set())
                    if neighbors_src & neighbors_tgt:
                        is_found = True
                        break

        if is_found:
            found_edges += 1
            seen_relations.add(edge_sig)

    unique_relevant = len(
        {
            f"{e.get('relation')}-{e.get('source')}-{e.get('target')}"
            for e in relevant_edges
        }
    )

    return (found_edges / unique_relevant) * 100 if unique_relevant > 0 else 100.0


def detect_conflicts(answer: str, graph: Dict[str, Any]) -> List[str]:
    """
    Detects potential entity resolution conflicts in the answer.

    Checks if mentioned nodes have contradictory first names in their resolution
    history (metadata). This prevents the agent from conflating different individuals
    who share the same surname and were incorrectly merged.

    Args:
        answer: The generated textual answer.
        graph: The Knowledge Graph dictionary.

    Returns:
        List of conflict description strings.
    """
    nodes = graph.get("nodes", [])
    conflicts = []

    for node in nodes:
        label = node.get("label")
        if label and label in answer:
            metadata = node.get("metadata", {})
            aliases_data = metadata.get("resolved_aliases_data", [])
            if not aliases_data:
                continue

            distinct_first_names = set()
            for alias in aliases_data:
                orig_label = alias.get("original_label", "")
                tokens = str(orig_label).split()
                if tokens and len(tokens) >= 2 and len(tokens[0]) > 2:
                    name = tokens[0].replace('"', "").replace("'", "")
                    if not any(
                        name in existing or existing in name
                        for existing in distinct_first_names
                    ):
                        distinct_first_names.add(name)

            if len(distinct_first_names) > 1:
                conflicts.append(
                    f"Conflict: Merged node '{label}' has contradictory first names: {', '.join(sorted(list(distinct_first_names)))}"
                )

    return conflicts
