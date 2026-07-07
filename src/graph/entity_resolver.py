from src.utils.bidi_utils import fix_text
import re
import logging
from collections import defaultdict
from difflib import SequenceMatcher
from typing import Any, Set, Dict
import networkx as nx
from src.models.base import EntityMergeGroup, EntityResolutionPlan
from src.utils.model_helpers import merge_text_values as _merge_text_values

logger = logging.getLogger(__name__)

def _norm_type(raw_type: Any) -> str:
    return str(raw_type or "").strip().lower().replace(" ", "").replace("_", "")


def _configured_marker_match(text: str, markers: list[str]) -> bool:
    normalized_text = text.lower()
    return any(str(marker).lower() in normalized_text for marker in markers)


def _configured_pattern_match(text: str, patterns: list[str]) -> bool:
    return any(re.search(pattern, text, re.IGNORECASE) for pattern in patterns)


def _semantic_discriminator_values(
    text: str, semantic_class: str, schema_config: Any
) -> set[str]:
    patterns = (
        getattr(schema_config, "semantic_class_discriminator_patterns", {}) or {}
    ).get(semantic_class, [])
    values: set[str] = set()
    for pattern in patterns:
        for match in re.finditer(pattern, text, re.IGNORECASE):
            if match.groups():
                values.update(str(group) for group in match.groups() if group)
            else:
                values.add(match.group(0))
    return values


def _infer_semantic_class(node: dict[str, Any], schema_config: Any) -> str:
    """Infer a coarse resolver class even when extraction emitted OTHER."""
    node_type = _norm_type(node.get("type", ""))
    human_types = {_norm_type(t) for t in schema_config.human_entity_types}
    org_types = {_norm_type(t) for t in schema_config.organization_entity_types}
    event_types = {_norm_type(t) for t in schema_config.event_entity_types}
    statute_types = {_norm_type(t) for t in schema_config.statute_entity_types}
    evidence_types = {_norm_type(t) for t in schema_config.evidence_entity_types}
    date_types = {_norm_type(t) for t in (schema_config.date_types or [])}

    if node_type in human_types:
        return "person"
    if node_type in org_types:
        return "organization"
    if node_type in event_types:
        return "event"
    if node_type in statute_types:
        return "statute"
    if node_type in evidence_types:
        return "evidence"
    if node_type in date_types:
        return "date"

    label = str(node.get("label", ""))
    node_id = str(node.get("id", ""))
    summary = str(node.get("forensic_summary") or node.get("description") or "")
    text = f"{node_id} {label} {summary}".lower()

    semantic_patterns = getattr(schema_config, "semantic_class_patterns", {}) or {}
    for semantic_class, patterns in semantic_patterns.items():
        if _configured_pattern_match(text, patterns):
            return str(semantic_class)

    semantic_markers = getattr(schema_config, "semantic_class_markers", {}) or {}
    for semantic_class, markers in semantic_markers.items():
        if _configured_marker_match(text, markers):
            return str(semantic_class)

    normalized_label = _normalize_person_name(label, schema_config)
    label_tokens = normalized_label.split()
    if len(label_tokens) >= 2 and not any(char.isdigit() for char in normalized_label):
        return "person"

    return "other"


def _semantic_classes_compatible(class1: str, class2: str) -> bool:
    if class1 == class2:
        return True
    return False


def _type_for_semantic_class_from_config(
    semantic_class: str, current_type: Any, schema_config: Any
) -> str:
    current = str(current_type or "").strip()
    if current and current.upper() != "OTHER":
        return current
    type_map = getattr(schema_config, "semantic_class_type_map", {}) or {}
    return str(type_map.get(semantic_class, current or "OTHER"))


def get_legal_role_map(schema_config: Any) -> Dict[str, Set[str]]:
    """
    Transform the schema's legal role map into a set-based dictionary for faster lookup.

    Args:
        schema_config (Any): Configuration containing the 'legal_role_map'.

    Returns:
        Dict[str, Set[str]]: Mapping of role categories to sets of keywords.
    """
    return {key: set(values) for key, values in schema_config.legal_role_map.items()}


def _get_role_categories(forensic_summary: str, schema_config: Any) -> Set[str]:
    """
    Identify legal role categories from a forensic summary string.

    Args:
        forensic_summary (str): The text description of an entity.
        schema_config (Any): Configuration containing role definitions.

    Returns:
        Set[str]: A set of identified role categories (e.g., {'WITNESS', 'DEFENDANT'}).
    """
    categories = set()
    if not forensic_summary:
        return categories
    for cat, keywords in {
        key: set(values) for key, values in schema_config.legal_role_map.items()
    }.items():
        if any((kw in forensic_summary for kw in keywords)):
            categories.add(cat)
    return categories


def _score_label_quality(label: str, schema_config: Any) -> int:
    """
    Score a label based on its investigative utility.

    Prioritizes full names over single-word titles or short acronyms.

    Args:
        label (str): The entity label.
        schema_config (Any): Configuration (not currently used in logic but kept for consistency).

    Returns:
        int: The quality score.
    """
    tokens = label.split()
    score = len(tokens) * 1000 + len(label)
    if len(tokens) >= 2:
        score += 20
    if len(label) < 4:
        score -= 10
    return score


def _normalize_person_name(name: str, schema_config: Any) -> str:
    """
    Normalize a person's name for canonical deduplication.

    Performs the following:
    - Lowercasing and punctuation stripping.
    - Advanced Hebrew prefix cleaning (Phase 2 Upgrade).
    - Tokenization and re-joining.

    Args:
        name (str): The raw name string.
        schema_config (Any): Configuration (not currently used for prefixes).

    Returns:
        str: The normalized name string.
    """
    cleaned = str(name or "").lower()
    cleaned = re.sub("[^a-z0-9\\u0590-\\u05FF ]", " ", cleaned)

    # Strip investigative/honorific prefixes
    tokens = cleaned.split()

    # HEBREW PREFIX CLEANING (Phase 2 Upgrade)
    prefixes = ("ב", "ל", "ו", "ה", "מ", "כש", "ש")
    cleaned_tokens = []
    for t in tokens:
        if len(t) > 3:
            if t.startswith("כש") and len(t) > 4:
                t = t[2:]
            else:
                for p in prefixes:
                    if p != "כש" and t.startswith(p):
                        if len(t[1:]) >= 3:
                            t = t[1:]
                        break
        cleaned_tokens.append(t)

    return " ".join(cleaned_tokens).strip()


def _normalize_org_name(name: str, schema_config: Any) -> str:
    """
    Normalize an organization name for canonical deduplication.

    Performs the following:
    - Removal of language-specific honorifics (e.g., 'Ltd', 'Inc').
    - Advanced Hebrew prefix cleaning.

    Args:
        name (str): The raw organization name.
        schema_config (Any): Configuration containing 'language_prefixes'.

    Returns:
        str: The normalized organization name.
    """
    cleaned = str(name or "").lower()

    # Strip investigative/honorific prefixes from config
    for prefix in schema_config.language_prefixes:
        cleaned = re.sub(prefix, "", cleaned)

    cleaned = re.sub("[^a-z0-9\\u0590-\\u05FF ]", " ", cleaned)
    tokens = cleaned.split()

    # HEBREW PREFIX CLEANING (Phase 2 Upgrade)
    prefixes = ("ב", "ל", "ו", "ה", "מ", "כש", "ש")
    cleaned_tokens = []
    for t in tokens:
        if len(t) > 3:
            for p in prefixes:
                if t.startswith(p) and len(t[1:]) >= 3:
                    t = t[1:]
                    break
        cleaned_tokens.append(t)

    return " ".join(cleaned_tokens).strip()


def _similarity(a: str, b: str) -> float:
    """
    Calculate the character-level similarity ratio between two strings.

    Args:
        a (str): First string.
        b (str): Second string.

    Returns:
        float: Similarity ratio (0.0 to 1.0).
    """
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a, b).ratio()


def _is_witness(node: dict, schema_config: Any) -> bool:
    """
    Check if a node represents a witness based on type or label terms.

    Args:
        node (dict): The node attribute dictionary.
        schema_config (Any): Configuration containing 'witness_keywords'.

    Returns:
        bool: True if identified as a witness.
    """
    t = str(node.get("type", "")).strip().lower()
    label = str(node.get("label", "")).lower()
    witness_terms = getattr(schema_config, "witness_keywords", [])
    return any((term == t or term in label for term in witness_terms))


def _is_defendant(node: dict, schema_config: Any) -> bool:
    """
    Check if a node represents a defendant based on type or label terms.

    Args:
        node (dict): The node attribute dictionary.
        schema_config (Any): Configuration containing 'defendant_keywords'.

    Returns:
        bool: True if identified as a defendant.
    """
    t = str(node.get("type", "")).strip().lower()
    label = str(node.get("label", "")).lower()
    defendant_terms = getattr(schema_config, "defendant_keywords", [])
    return any((term == t or term in label for term in defendant_terms))


def _is_name_token(token: str, schema_config: Any = None) -> bool:
    """
    Heuristically check if a token looks like a specific entity name.

    Excludes common stopwords and short tokens.

    Args:
        token (str): The token to check.
        schema_config (Any, optional): Configuration (not used).

    Returns:
        bool: True if the token appears to be a name.
    """
    if len(token) < 3:
        return False

    stop_words = {
        "את",
        "של",
        "עם",
        "על",
        "the",
        "and",
        "for",
        "from",
        "was",
        "has",
        "had",
    }
    if token.lower() in stop_words:
        return False
    if any(("A" <= char <= "Z" for char in token)):
        return True
    is_hebrew = all(("\u0590" <= char <= "\u05ff" for char in token))
    if is_hebrew and len(token) >= 4:
        return True
    if re.match("[A-Z][a-z]+", token):
        return True
    return False


def _jaccard_similarity(set1: Set[Any], set2: Set[Any]) -> float:
    """
    Calculate Jaccard similarity (Intersection over Union) between two sets.

    Args:
        set1 (Set[Any]): First set.
        set2 (Set[Any]): Second set.

    Returns:
        float: Jaccard similarity score.
    """
    if not set1 or not set2:
        return 0.0
    intersection = len(set1.intersection(set2))
    union = len(set1.union(set2))
    return intersection / union


def _deduplicate_canonical_groups(
    groups: dict[str, list[str]],
    node_metadata: dict[str, dict[str, Any]],
    adjacency_ids: dict[str, Set[str]],
    schema_config: Any,
) -> dict[str, list[str]]:
    """
    Second-pass consolidation for entity groups using structural and semantic rules.

    Consolidates groups with highly similar labels and overlapping core identities.
    Uses structural context to resolve subset-name ambiguities (e.g., merging
    surname into full name).

    Args:
        groups (dict[str, list[str]]): Current groups of node IDs.
        node_metadata (dict[str, dict[str, Any]]): Pre-computed metadata for all nodes.
        adjacency_ids (dict[str, Set[str]]): Neighbor ID mapping for structural checks.
        schema_config (Any): Configuration containing thresholds and type whitelists.

    Returns:
        dict[str, list[str]]: The refined and consolidated groups.
    """
    if not groups:
        return groups
    group_keys = list(groups.keys())
    final_groups = {k: list(v) for k, v in groups.items()}
    thresholds = schema_config.resolution_thresholds
    threshold = thresholds.canonical_dedupe_threshold
    date_regex = "(\\d{1,2}[_./-]\\d{1,2}[_./-]\\d{2,4})"

    merged_into = {}

    def find_target(k):
        while k in merged_into:
            k = merged_into[k]
        return k

    human_types = {
        t.lower().replace(" ", "").replace("_", "")
        for t in schema_config.human_entity_types
    }
    org_types = {
        t.lower().replace(" ", "").replace("_", "")
        for t in schema_config.organization_entity_types
    }
    event_types = {
        t.lower().replace(" ", "").replace("_", "")
        for t in schema_config.event_entity_types
    }
    event_types.add("context")
    date_types = set(schema_config.date_types) or {"date", "תאריך"}
    statute_types = {
        t.lower().replace(" ", "").replace("_", "")
        for t in schema_config.statute_entity_types
    }
    for i in range(len(group_keys)):
        k1 = group_keys[i]
        root1 = find_target(k1)
        if root1 not in final_groups:
            continue
        members1 = final_groups[root1]
        god_threshold = schema_config.graph_analysis.god_node_threshold

        def _get_best_label(m_list):
            return max(
                m_list,
                key=lambda m: (
                    2 if node_metadata[m]["degree"] > god_threshold else 0,
                    1 if len(node_metadata[m]["label"].split()) >= 2 else 0,
                    node_metadata[m]["degree"],
                    len(node_metadata[m]["label"]),
                ),
            )

        best_id1 = _get_best_label(members1)
        label1 = node_metadata[best_id1]["label"]
        type1 = (
            node_metadata[members1[0]]["type"].lower().replace(" ", "").replace("_", "")
        )

        # Evidence Collapse Guard (Canonical Pass)
        evidence_keywords = set(getattr(schema_config, "evidence_keywords", []))
        is_evidence1 = (
            any(kw in type1 for kw in evidence_keywords)
            or any(
                kw in str(node_metadata[members1[0]].get("entity_class", "")).lower()
                for kw in evidence_keywords
            )
            or any(kw in label1.lower() for kw in evidence_keywords)
            or any(kw in best_id1.lower() for kw in evidence_keywords)
        )
        if is_evidence1:
            continue

        class1 = node_metadata[members1[0]].get("semantic_class") or _infer_semantic_class(
            node_metadata[members1[0]], schema_config
        )
        is_person1 = class1 == "person"
        is_org1 = class1 == "organization"
        is_event1 = class1 == "event"
        is_date1 = class1 == "date"
        is_statute1 = class1 == "statute"
        neighbors1 = set()
        for m in members1:
            neighbors1.update(adjacency_ids.get(m, set()))
        for j in range(i + 1, len(group_keys)):
            k2 = group_keys[j]
            root2 = find_target(k2)
            if root1 == root2 or root2 not in final_groups:
                continue
            members2 = final_groups[root2]
            best_id2 = _get_best_label(members2)
            label2 = node_metadata[best_id2]["label"]
            type2 = (
                node_metadata[members2[0]]["type"]
                .lower()
                .replace(" ", "")
                .replace("_", "")
            )
            class1 = node_metadata[members1[0]].get("semantic_class") or _infer_semantic_class(
                node_metadata[members1[0]], schema_config
            )
            class2 = node_metadata[members2[0]].get("semantic_class") or _infer_semantic_class(
                node_metadata[members2[0]], schema_config
            )
            if not _semantic_classes_compatible(class1, class2):
                continue

            identity_text1 = " ".join(
                f"{m} {node_metadata[m].get('label', '')}" for m in members1
            )
            identity_text2 = " ".join(
                f"{m} {node_metadata[m].get('label', '')}" for m in members2
            )
            discriminator_values1 = _semantic_discriminator_values(
                identity_text1, class1, schema_config
            )
            discriminator_values2 = _semantic_discriminator_values(
                identity_text2, class2, schema_config
            )
            if (
                discriminator_values1
                and discriminator_values2
                and discriminator_values1 != discriminator_values2
            ):
                continue

            is_person2 = class2 == "person"
            is_org2 = class2 == "organization"
            is_event2 = class2 == "event"
            is_date2 = class2 == "date"
            is_statute2 = class2 == "statute"

            # Evidence Collapse Guard for Canonical Pass (Target node)
            is_evidence2 = (
                any(kw in type2 for kw in evidence_keywords)
                or any(
                    kw
                    in str(node_metadata[members2[0]].get("entity_class", "")).lower()
                    for kw in evidence_keywords
                )
                or any(kw in label2.lower() for kw in evidence_keywords)
                or any(kw in best_id2.lower() for kw in evidence_keywords)
            )
            if is_evidence2:
                continue

            if class1 != class2:
                if not (is_person1 and is_person2):
                    continue

            def _get_all_digits(m_list):
                text = " ".join(
                    [
                        str(m) + " " + str(node_metadata[m].get("label", ""))
                        for m in m_list
                    ]
                )
                return set(re.findall("\\d+", text))

            if (
                is_date1
                or is_date2
                or is_statute1
                or is_statute2
                or is_event1
                or is_event2
            ):
                digits1 = _get_all_digits(members1)
                digits2 = _get_all_digits(members2)
                if digits1 and digits2 and (digits1 != digits2):
                    continue
            d1_text = " ".join(
                [
                    str(m) + " " + str(node_metadata[m].get("label", ""))
                    for m in members1
                ]
            )
            d2_text = " ".join(
                [
                    str(m) + " " + str(node_metadata[m].get("label", ""))
                    for m in members2
                ]
            )
            d1_match = re.search(date_regex, d1_text)
            d2_match = re.search(date_regex, d2_text)
            if d1_match and d2_match:
                if d1_match.group(1).replace("_", ".") != d2_match.group(1).replace(
                    "_", "."
                ):
                    continue
            elif is_event1 and is_event2:
                if (d1_match or d2_match) and label1 != label2:
                    continue
            sim = _similarity(label1, label2)
            norm1 = (
                _normalize_person_name(label1, schema_config)
                if is_person1
                else _normalize_org_name(label1, schema_config)
            )
            norm2 = (
                _normalize_person_name(label2, schema_config)
                if is_person2
                else _normalize_org_name(label2, schema_config)
            )
            if norm1 == norm2 and norm1:
                logger.debug(
                    fix_text(
                        str(
                            f"DEBUG: Identity merging {root2} ({label2}) into {root1} ({label1})"
                        )
                    )
                )
                final_groups[root1].extend(final_groups[root2])
                merged_into[root2] = root1
                del final_groups[root2]
                for m in members2:
                    neighbors1.update(adjacency_ids.get(m, set()))
                continue
            max_sim = max(sim, _similarity(norm1, norm2))
            neighbors2 = set()
            for m in members2:
                neighbors2.update(adjacency_ids.get(m, set()))
            j_sim = _jaccard_similarity(neighbors1, neighbors2)
            anchors1 = {
                n
                for n in neighbors1
                if node_metadata.get(n, {}).get("type", "").lower()
                in org_types | event_types
            }
            anchors2 = {
                n
                for n in neighbors2
                if node_metadata.get(n, {}).get("type", "").lower()
                in org_types | event_types
            }
            anchor_sim = _jaccard_similarity(anchors1, anchors2)
            if is_org1:
                t1 = norm1.split()
                t2 = norm2.split()
                if (len(t1) == 1 or len(t2) == 1) and (len(t1) > 0 and len(t2) > 0):
                    shorter = t1 if len(t1) == 1 else t2
                    longer = t2 if len(t1) == 1 else t1
                    if shorter[0] in longer and len(shorter[0]) >= 4:
                        max_sim = 1.0
            elif is_person1:
                t1 = norm1.split()
                t2 = norm2.split()
                # Check for different first names if both have >= 2 tokens
                if len(t1) >= 2 and len(t2) >= 2:
                    if t1[0] != t2[0] and len(t1[0]) > 2 and (len(t2[0]) > 2):
                        continue
                # PATCH: Single-Word Hijack Prevention for People
                if (len(t1) == 1 and len(t2) >= 2) or (len(t2) == 1 and len(t1) >= 2):
                    continue
                if (len(t1) == 1 or len(t2) == 1) and len(t1) != len(t2):
                    shorter = norm1 if len(t1) == 1 else norm2
                    longer = norm2 if len(t1) == 1 else norm1
                    if shorter in longer:
                        full_name_nodes = [
                            n
                            for n in node_metadata.values()
                            if str(n.get("type", ""))
                            .lower()
                            .replace(" ", "")
                            .replace("_", "")
                            in human_types
                            and shorter in n.get("norm_person", "").split()
                            and (len(n.get("norm_person", "").split()) >= 2)
                        ]
                        s_root = root1 if len(t1) == 1 else root2
                        l_root = root2 if len(t1) == 1 else root1
                        s_neigh = adjacency_ids.get(s_root, set())
                        l_neigh = adjacency_ids.get(l_root, set())
                        if s_neigh and l_neigh and s_neigh.issubset(l_neigh):
                            logger.debug(
                                fix_text(
                                    str(
                                        f"DEBUG: Structural Inclusion for '{shorter}': Neighbors are subset of '{longer}'. Force merge."
                                    )
                                )
                            )
                            max_sim = 1.0
                        if len(full_name_nodes) > 1:
                            # TIE-BREAKER: Sort by degree and explicit god-node status to find the best candidate
                            candidate_info = [
                                (
                                    n["id"],
                                    n.get("norm_person", "").split()[0],
                                    n.get("degree", 0),
                                )
                                for n in full_name_nodes
                            ]
                            god_threshold = (
                                schema_config.graph_analysis.god_node_threshold
                            )

                            # Prioritize nodes that are confirmed God Nodes (hubs) first, then by degree
                            candidate_info.sort(
                                key=lambda x: x[2]
                                + (10000 if x[2] > god_threshold else 0),
                                reverse=True,
                            )

                            top_id, top_first_name, top_degree = candidate_info[0]
                            second_id, second_first_name, second_degree = (
                                candidate_info[1]
                            )

                            unique_first_names = {c[1] for c in candidate_info}
                            if len(unique_first_names) > 1:
                                if anchor_sim > 0.4:
                                    logger.debug(
                                        fix_text(
                                            str(
                                                f"DEBUG: Identity Anchor Rule for '{shorter}': Merging into '{top_id}' based on shared anchors (Anchor-Sim {anchor_sim})"
                                            )
                                        )
                                    )
                                    max_sim = 1.0
                                elif (
                                    top_degree >= 2 * max(1, second_degree)
                                    or top_degree > god_threshold
                                ):
                                    logger.debug(
                                        fix_text(
                                            str(
                                                f"DEBUG: Centrality Tie-Breaker for '{shorter}': Merging into dominant hub '{top_id}' (Degree {top_degree} vs {second_degree})"
                                            )
                                        )
                                    )
                                    max_sim = 1.0
                                elif j_sim > 0.6:
                                    # Fallback to structural overlap if no clear degree winner
                                    max_sim = 1.0
                                else:
                                    continue
                            else:
                                max_sim = 1.0
                        elif len(full_name_nodes) == 1:
                            target_node = full_name_nodes[0]
                            target_degree = target_node.get("degree", 0)
                            if j_sim > 0.1 or target_degree > 50 or anchor_sim > 0.2:
                                logger.debug(
                                    fix_text(
                                        str(
                                            f"DEBUG: Merging surname '{shorter}' into unique candidate '{target_node['id']}' (J-Sim {j_sim}, Anchor-Sim {anchor_sim})"
                                        )
                                    )
                                )
                                max_sim = 1.0
                            else:
                                continue
                        elif j_sim > 0.5:
                            max_sim = 1.0
                        else:
                            continue
                    else:
                        continue
                if (len(t1) == 1 or len(t2) == 1) and (len(t1) > 0 and len(t2) > 0):
                    shorter = t1 if len(t1) == 1 else t2
                    longer = t2 if len(t1) == 1 else t1
                    if shorter[0] in longer and len(shorter[0]) >= 4:
                        if (
                            j_sim > 0.5
                            or _jaccard_similarity(set(members1), set(members2)) > 0.5
                        ):
                            max_sim = 1.0
                    elif shorter[0] not in longer:
                        continue

            def _get_core_identity(m):
                return (
                    m.replace("person_", "")
                    .replace("org_", "")
                    .replace("event_", "")
                    .split("_")[0]
                )

            ids1 = {_get_core_identity(m) for m in members1}
            ids2 = {_get_core_identity(m) for m in members2}
            id_overlap = _jaccard_similarity(ids1, ids2)
            if is_event1 and is_event2:

                event_generic_tokens = set(
                    getattr(schema_config, "event_generic_id_tokens", []) or []
                )

                def _get_event_tokens(m_list):
                    tokens = set()
                    for m in m_list:
                        core = m.replace("event_", "").replace("canonical_", "")
                        parts = re.findall("[a-z0-9]+", core.lower())
                        tokens.update(
                            {
                                p
                                for p in parts
                                if p not in event_generic_tokens and len(p) >= 1
                            }
                        )
                    return tokens

                ev_tokens1 = _get_event_tokens(members1)
                ev_tokens2 = _get_event_tokens(members2)
                if ev_tokens1 and ev_tokens2 and (ev_tokens1 != ev_tokens2):
                    if not (
                        ev_tokens1.issubset(ev_tokens2)
                        or ev_tokens2.issubset(ev_tokens1)
                    ):
                        continue
            if (
                max_sim >= threshold
                or (id_overlap > 0.5 and max_sim > 0.7)
                or (j_sim > 0.5 and max_sim > 0.6)
            ):
                final_groups[root1].extend(final_groups[root2])
                merged_into[root2] = root1
                del final_groups[root2]
                neighbors1.update(neighbors2)
    return final_groups


def _is_match(
    n1: dict,
    n2: dict,
    adjacency_ids: dict[str, Set[str]],
    schema_config: Any,
    entity_evidence_roles: dict[str, Set[tuple[str, str]]] = None,
) -> bool:
    """
    Refined investigative entity matching logic.

    Combines character-level similarity, structural Jaccard overlap, and
    investigative guards (e.g., Evidence Collapse Guard) to determine if two
    nodes represent the same entity.

    Args:
        n1 (dict): First node attributes.
        n2 (dict): Second node attributes.
        adjacency_ids (dict[str, Set[str]]): Graph topology mapping.
        schema_config (Any): Configuration containing thresholds and roles.
        entity_evidence_roles (dict, optional): Mapping of entities to evidence context roles.

    Returns:
        bool: True if the nodes represent the same entity.
    """
    label1 = n1.get("label", "")
    label2 = n2.get("label", "")
    id1 = n1.get("id", "")
    id2 = n2.get("id", "")
    thresholds = schema_config.resolution_thresholds

    # Evidence Collapse Guard Clause: Atomic evidence must never be merged.
    evidence_types = {str(t).lower() for t in schema_config.evidence_entity_types}

    def _is_evidence_logic(n):
        t = str(n.get("type", "")).lower()
        return t in evidence_types

    is_ev1 = _is_evidence_logic(n1)
    is_ev2 = _is_evidence_logic(n2)
    if is_ev1 and is_ev2:
        # High-similarity merge for fragments (per investigative plan)
        d1 = str(n1.get("description") or n1.get("forensic_summary", "")).strip()
        d2 = str(n2.get("description") or n2.get("forensic_summary", "")).strip()
        if abs(len(d1) - len(d2)) > 150:
            return False
        if d1 and d2 and _similarity(d1, d2) >= 0.90:
            return True
        return False
    elif is_ev1 != is_ev2:
        return False

    type1 = _norm_type(n1.get("type", ""))
    type2 = _norm_type(n2.get("type", ""))
    class1 = n1.get("semantic_class") or _infer_semantic_class(n1, schema_config)
    class2 = n2.get("semantic_class") or _infer_semantic_class(n2, schema_config)
    if not _semantic_classes_compatible(class1, class2):
        return False
    discriminator_values1 = _semantic_discriminator_values(
        f"{id1} {label1}", class1, schema_config
    )
    discriminator_values2 = _semantic_discriminator_values(
        f"{id2} {label2}", class2, schema_config
    )
    if (
        discriminator_values1
        and discriminator_values2
        and discriminator_values1 != discriminator_values2
    ):
        return False

    human_types = {_norm_type(t) for t in schema_config.human_entity_types}
    org_types = {_norm_type(t) for t in schema_config.organization_entity_types}
    event_types = {_norm_type(t) for t in schema_config.event_entity_types}
    event_types.add("context")
    evidence_types = {_norm_type(t) for t in schema_config.evidence_entity_types}
    date_types = set(schema_config.date_types) or {"date", "תאריך"}
    statute_types = {_norm_type(t) for t in schema_config.statute_entity_types}
    is_person1 = class1 == "person"
    is_org1 = class1 == "organization"
    is_person2 = class2 == "person"
    is_org2 = class2 == "organization"
    is_event1 = class1 == "event"
    is_event2 = class2 == "event"
    is_evidence1 = class1 == "evidence"
    is_evidence2 = class2 == "evidence"
    is_date1 = class1 == "date"
    is_date2 = class2 == "date"
    is_statute1 = class1 == "statute"
    is_statute2 = class2 == "statute"
    date_regex = "(\\d{1,2}[_./-]\\d{1,2}[_./-]\\d{2,4})"
    d1_match = re.search(date_regex, label1) or re.search(date_regex, n1.get("id", ""))
    d2_match = re.search(date_regex, label2) or re.search(date_regex, n2.get("id", ""))
    if d1_match and d2_match:
        if d1_match.group(1).replace("_", ".") != d2_match.group(1).replace("_", "."):
            return False
    elif is_date1 or is_date2 or is_statute1 or is_statute2:

        def _get_digits(text):
            return set(re.findall("\\d+", text))

        digits1 = _get_digits(label1 + " " + n1.get("id", ""))
        digits2 = _get_digits(label2 + " " + n2.get("id", ""))
        if digits1 and digits2 and (digits1 != digits2):
            return False
    if is_person1 != is_person2:
        return False
    if is_event1 != is_event2:
        return False
    if is_org1 != is_org2:
        return False
    if is_evidence1 or is_evidence2:
        return False
    if (is_date1 or is_date2) and label1 != label2:
        return False
    if is_event1 and is_event2:
        d1 = n1.get("event_date")
        d2 = n2.get("event_date")
        if d1 and d2 and (d1 != d2):
            return False
        generic_tokens = set(getattr(schema_config, "event_generic_id_tokens", []))

        def _get_id_identity_tokens(cid):
            parts = re.findall("[a-z0-9]{3,}", cid.lower())
            return {p for p in parts if p not in generic_tokens}

        id_identity1 = _get_id_identity_tokens(n1["id"])
        id_identity2 = _get_id_identity_tokens(n2["id"])
        if id_identity1 and id_identity2 and (id_identity1 != id_identity2):
            if not (
                id_identity1.issubset(id_identity2)
                or id_identity2.issubset(id_identity1)
            ):
                return False
        if len(label1) > 20 and len(label2) > 20:
            if label1 != label2 and _similarity(label1, label2) < 0.95:
                return False
    if is_person1 and is_person2:
        # Active Surname Ingestion Pass (Top-Priority fallback)
        norm1 = _normalize_person_name(label1, schema_config)
        norm2 = _normalize_person_name(label2, schema_config)
        t1, t2 = norm1.split(), norm2.split()
        if (len(t1) == 1 and len(t2) >= 2) or (len(t2) == 1 and len(t1) >= 2):
            shorter_word = t1[0] if len(t1) == 1 else t2[0]
            longer_text = norm2 if len(t1) == 1 else norm1
            if shorter_word in longer_text and len(shorter_word) >= 3:
                return True  # Flag as valid match candidate for topological tie-breaker
    is_w1 = _is_witness(n1, schema_config)
    is_w2 = _is_witness(n2, schema_config)
    is_d1 = _is_defendant(n1, schema_config)
    is_d2 = _is_defendant(n2, schema_config)
    is_hub = n1.get("degree", 0) > 50 or n2.get("degree", 0) > 50
    if not is_hub:
        if is_w1 and is_d2 or (is_w2 and is_d1):
            return False
        if (
            is_w1 != is_w2
            and _similarity(label1, label2) < thresholds.witness_collision_threshold
        ):
            return False
    if is_person1 and is_person2:
        t1 = _normalize_person_name(label1, schema_config).split()
        t2 = _normalize_person_name(label2, schema_config).split()
        if len(t1) >= 2 and len(t2) >= 2:
            if t1[0] != t2[0] and len(t1[0]) > 2 and (len(t2[0]) > 2):
                return False
    num_pattern = schema_config.collision_prevention_patterns[0]
    m1 = re.search(num_pattern, label1, re.IGNORECASE)
    m2 = re.search(num_pattern, label2, re.IGNORECASE)
    if m1 and m2:
        if m1.group(1) != m2.group(1):
            return False
    if is_person1 and is_person2:
        t1 = _normalize_person_name(label1, schema_config).split()
        t2 = _normalize_person_name(label2, schema_config).split()
        if len(t1) > 0 and len(t2) > 0:
            if len(t1) > 1 and len(t2) > 1:
                if t1[0] != t2[0] and len(t1[0]) > 2 and (len(t2[0]) > 2):
                    if t1[0] not in t2[0] and t2[0] not in t1[0]:
                        return False
            elif len(t1) == 1 or len(t2) == 1:
                shorter = t1 if len(t1) == 1 else t2
                longer = t2 if len(t1) == 1 else t1
                if shorter[0] not in longer:
                    return False
    if is_statute1 or is_statute2:
        if _similarity(label1, label2) < thresholds.law_merge_threshold:
            return False
    sim = _similarity(label1, label2)
    norm1 = (
        _normalize_person_name(label1, schema_config)
        if is_person1
        else _normalize_org_name(label1, schema_config)
    )
    norm2 = (
        _normalize_person_name(label2, schema_config)
        if is_person2
        else _normalize_org_name(label2, schema_config)
    )
    max_sim = max(sim, _similarity(norm1, norm2))

    # Subset / Contains match logic
    is_fuzzy_included = False
    if norm1 and norm2 and (norm1 in norm2 or norm2 in norm1):
        t1, t2 = norm1.split(), norm2.split()
        # PATCH: Single-Word Hijack Prevention for People
        if is_person1 and (
            (len(t1) == 1 and len(t2) >= 2) or (len(t2) == 1 and len(t1) >= 2)
        ):
            shorter_word = t1[0] if len(t1) == 1 else t2[0]
            longer_text = norm2 if len(t1) == 1 else norm1
            if shorter_word in longer_text and len(shorter_word) >= 3:
                return True  # Flag as valid match candidate
            else:
                is_fuzzy_included = False
        # Only allow subset match if names are long enough to be meaningful
        elif len(norm1) >= 3 and len(norm2) >= 3:
            # If same entity_class, highly likely to be the same entity
            class1 = n1.get("entity_class")
            class2 = n2.get("entity_class")
            if class1 and class2 and class1 == class2:
                is_fuzzy_included = True
                max_sim = 1.0  # Force merge if subset + same class
            elif not class1 or not class2:
                # If one is missing class, still consider it a strong match
                is_fuzzy_included = True
                max_sim = max(max_sim, 0.9)

    if is_person1 or is_org1:
        if norm1 == norm2 and norm1:
            max_sim = 1.0

    if is_person1 and is_person2:
        # Partial Name Overlap
        if len(norm1) >= 4 and len(norm2) >= 4:
            if norm1.startswith(norm2) or norm2.startswith(norm1):
                max_sim = 1.0
                is_fuzzy_included = True

        name_sim = max(sim, _similarity(n1.get("id", ""), n2.get("id", "")))
        if name_sim < 0.3 and not is_fuzzy_included:
            return False
    neighbors1 = adjacency_ids.get(n1["id"], set())
    neighbors2 = adjacency_ids.get(n2["id"], set())
    if neighbors1 and neighbors2:
        j_sim = _jaccard_similarity(neighbors1, neighbors2)
        if j_sim >= thresholds.jaccard_overlap_threshold:
            if max_sim >= thresholds.structural_baseline_sim or is_fuzzy_included:
                if is_event1 and is_event2:
                    if max_sim >= thresholds.event_merge_threshold:
                        return True
                    else:
                        return False
                return True
    if entity_evidence_roles:
        roles1 = entity_evidence_roles.get(n1["id"], set())
        roles2 = entity_evidence_roles.get(n2["id"], set())
        if roles1 and roles2 and roles1.intersection(roles2):
            loc1 = n1.get("source_location")
            loc2 = n2.get("source_location")
            if (
                loc1
                and loc2
                and (loc1 == loc2)
                and (sim > thresholds.evidence_location_match_threshold)
            ):
                return True
            if sim > thresholds.evidence_match_threshold:
                return True
    if is_person1 and is_person2:
        core1 = n1["id"].replace("person_", "").split("_")[0]
        core2 = n2["id"].replace("person_", "").split("_")[0]
        if len(core1) > 3 and len(core2) > 3:
            id_sim = SequenceMatcher(None, core1, core2).ratio()
            if (
                id_sim < 0.4
                and sim > 0.9
                and (core1 not in core2 and core2 not in core1)
            ):
                return False
    if is_event1 and is_event2:

        def _get_meaningful_tokens(text):
            return {t.lower() for t in re.findall("[\\u0590-\\u05FF\\w]{3,}", text)}

        tokens1 = _get_meaningful_tokens(f"{label1} {n1['id']}")
        tokens2 = _get_meaningful_tokens(f"{label2} {n2['id']}")
        unique1 = tokens1 - tokens2
        unique2 = tokens2 - tokens1
        names1 = {t for t in unique1 if _is_name_token(t, schema_config)}
        names2 = {t for t in unique2 if _is_name_token(t, schema_config)}
        if names1 and names2 and (names1 != names2):
            return False
        core1 = n1["id"].replace("event_", "")
        core2 = n2["id"].replace("event_", "")
        generic_tokens = set(getattr(schema_config, "event_generic_id_tokens", []))

        def _get_id_identity_tokens(cid):
            parts = re.findall("[a-z0-9]{3,}", cid.lower())
            return {p for p in parts if p not in generic_tokens}

        id_identity1 = _get_id_identity_tokens(core1)
        id_identity2 = _get_id_identity_tokens(core2)
        if id_identity1 and id_identity2 and (id_identity1 != id_identity2):
            if not (
                id_identity1.issubset(id_identity2)
                or id_identity2.issubset(id_identity1)
            ):
                return False
        if SequenceMatcher(None, core1, core2).ratio() < 0.4 and label1 != label2:
            return False
        if unique1 and unique2 and (sim < 0.6):
            return False
    target_threshold = (
        thresholds.person
        if is_person1
        else thresholds.organization
        if is_org1
        else thresholds.other
    )

    # Dynamic Threshold for High-Confidence Blocks
    is_high_confidence_block = False
    if is_person1 and is_person2:
        tokens1, tokens2 = norm1.split(), norm2.split()
        if tokens1 and tokens2 and tokens1[-1] == tokens2[-1]:
            is_high_confidence_block = True

    if is_high_confidence_block:
        target_threshold = min(target_threshold, 0.75)

    if is_person1 and (
        abs(len(norm1) - len(norm2)) >= 1
        or (norm1 and norm2 and (norm1[-1] != norm2[-1]))
    ):
        target_threshold = thresholds.disambiguation_high
        if (
            norm1
            and norm2
            and (norm1 in norm2 or norm2 in norm1)
            and (len(norm1) >= 3)
            and (len(norm2) >= 3)
        ):
            t1 = norm1.split()
            t2 = norm2.split()

            # FIRST-NAME COLLISION GUARD
            if len(t1) >= 2 and len(t2) >= 2:
                if t1[0] != t2[0] and len(t1[0]) > 2 and (len(t2[0]) > 2):
                    is_fuzzy_included = False
                    return False

            if (len(t1) == 1 or len(t2) == 1) and len(t1) != len(t2):
                is_fuzzy_included = False
            else:
                is_fuzzy_included = True
        else:
            is_fuzzy_included = False
    if max_sim >= target_threshold or is_fuzzy_included:
        if is_person1:
            tokens1 = norm1.split()
            tokens2 = norm2.split()

            # Family Conflict Rule
            if len(tokens1) >= 2 and len(tokens2) >= 2:
                if tokens1[-1] == tokens2[-1]:
                    first_sim = _similarity(tokens1[0], tokens2[0])
                    if first_sim < 0.6:
                        return False

            if tokens1 and tokens2:
                s1 = tokens1[-1]
                s2 = tokens2[-1]
                if (
                    s1 != s2
                    and len(s1) > thresholds.min_token_length
                    and (len(s2) > thresholds.min_token_length)
                ):
                    if s1 not in s2 and s2 not in s1:
                        return False
                if s1 == s2 and len(tokens1) > 1 and (len(tokens2) > 1):
                    if (
                        tokens1[0] != tokens2[0]
                        and len(tokens1[0]) > 1
                        and (len(tokens2[0]) > 1)
                    ):
                        return False
        return True
    return False


def _resolve_entity_groups_from_plan(
    graph: nx.Graph, plan: EntityResolutionPlan, schema_config: Any
) -> tuple[nx.MultiGraph, dict[str, Any]]:
    """
    Apply an entity resolution plan to a graph, merging nodes and redirecting edges.

    Args:
        graph (nx.Graph): The source graph.
        plan (EntityResolutionPlan): The Pydantic plan defining merge groups.
        schema_config (Any): Configuration for label selection and forensics.

    Returns:
        tuple[nx.MultiGraph, dict[str, Any]]: The resolved graph and a resolution report.
    """
    alias_to_canonical: dict[str, str] = {}
    merge_groups: list[dict[str, Any]] = []
    used_nodes: set[str] = set()
    resolved = nx.MultiGraph()
    resolved.graph.update(graph.graph)
    for node_id, attrs in graph.nodes(data=True):
        resolved.add_node(node_id, **dict(attrs))
    for group in plan.merge_groups:
        canonical_id = str(group.canonical_id)
        if canonical_id not in graph:
            continue
        alias_ids = [
            str(alias_id)
            for alias_id in group.alias_ids
            if str(alias_id) in graph and str(alias_id) != canonical_id
        ]
        if not alias_ids:
            continue
        group_nodes = [canonical_id, *alias_ids]
        if any((node_id in used_nodes for node_id in group_nodes)):
            continue
        used_nodes.update(group_nodes)
        target_canonical_id = f"canonical_{canonical_id}"
        for node_id in group_nodes:
            alias_to_canonical[node_id] = target_canonical_id
        member_labels = [str(graph.nodes[nid].get("label", nid)) for nid in group_nodes]
        best_label = (
            max(member_labels, key=lambda label: _score_label_quality(label, schema_config))
            if member_labels
            else target_canonical_id
        )
        merge_groups.append(
            {
                "canonical_id": target_canonical_id,
                "canonical_label": best_label,
                "alias_ids": alias_ids,
                "alias_labels": [
                    graph.nodes[alias_id].get("label", alias_id)
                    for alias_id in alias_ids
                ],
                "reason": group.reason,
            }
        )
        member_attrs = [
            graph.nodes[member_id] for member_id in group_nodes if member_id in graph
        ]
        if not member_attrs:
            continue
        canonical_attrs = dict(member_attrs[0])
        canonical_attrs["id"] = target_canonical_id
        canonical_attrs["label"] = best_label
        canonical_attrs["file_type"] = "document"
        # Handle both forensic_summary and description for merging
        summs = [
            str(m.get("forensic_summary") or m.get("description") or "")
            for m in member_attrs
        ]
        merged_summ = _merge_text_values(summs, max_words=20)
        canonical_attrs["forensic_summary"] = merged_summ
        canonical_attrs["description"] = merged_summ
        all_locations = set()
        for m in member_attrs:
            loc = m.get("source_location", "")
            if loc:
                all_locations.update(
                    [c.strip() for c in str(loc).split(";") if c.strip()]
                )
        if all_locations:
            canonical_attrs["source_location"] = "; ".join(sorted(list(all_locations)))
        metadata = dict(canonical_attrs.get("metadata", {}))
        metadata["is_canonical"] = True
        metadata["merge_reason"] = group.reason or "Algorithmic match"
        canonical_attrs["metadata"] = metadata
        canonical_attrs["resolved_from"] = group_nodes
        resolved.add_node(target_canonical_id, **canonical_attrs)
        for member_id in group_nodes:
            node_source_file = graph.nodes[member_id].get(
                "source_file", "entity_resolution"
            )
            resolved.add_edge(
                member_id,
                target_canonical_id,
                relation="IS_ALIAS_OF",
                weight=2.0,
                source_file=node_source_file,
            )
    if graph.is_multigraph():
        for source, target, _key, edge_attrs in graph.edges(keys=True, data=True):
            resolved.add_edge(source, target, **dict(edge_attrs))
    else:
        for source, target, edge_attrs in graph.edges(data=True):
            resolved.add_edge(source, target, **dict(edge_attrs))
    resolution_report = {
        "merge_group_count": len(merge_groups),
        "merged_alias_count": sum((len(group["alias_ids"]) for group in merge_groups)),
        "merge_groups": merge_groups,
        "node_count_before": graph.number_of_nodes(),
        "node_count_after": resolved.number_of_nodes(),
        "edge_count_before": graph.number_of_edges(),
        "edge_count_after": resolved.number_of_edges(),
    }
    resolved.graph["entity_resolution"] = resolution_report
    return (resolved, resolution_report)


def _canonicalize_resolution_mapping(mapping: dict[str, str]) -> dict[str, str]:
    """
    Trace resolution chains to ensure all aliases point directly to final canonical ID.

    Args:
        mapping (dict[str, str]): Raw ID mapping dictionary.

    Returns:
        dict[str, str]: Flattened ID mapping.
    """
    normalized = {str(key): str(value) for key, value in mapping.items()}

    def resolve(node_id: str) -> str:
        current = str(node_id)
        seen: set[str] = set()
        while True:
            next_id = normalized.get(current, current)
            if next_id == current or next_id in seen:
                return current if current in normalized else next_id
            seen.add(current)
            current = next_id

    return {node_id: resolve(node_id) for node_id in normalized}


def _build_entity_resolution_report(
    nodes: list[dict[str, Any]], mapping: dict[str, str]
) -> dict[str, Any]:
    """
    Construct a structured report summarizing the results of entity resolution.

    Args:
        nodes (list[dict[str, Any]]): Original list of nodes.
        mapping (dict[str, str]): Final ID mapping.

    Returns:
        dict[str, Any]: Resolution metrics and merge group details.
    """
    normalized_map = _canonicalize_resolution_mapping(mapping)
    node_lookup = {
        str(node.get("id")): node
        for node in nodes
        if isinstance(node, dict) and node.get("id") is not None
    }
    members_by_canonical: dict[str, list[str]] = {}
    canonical_order: list[str] = []
    for node in nodes:
        node_id = str(node.get("id"))
        canonical_id = normalized_map.get(node_id, node_id)
        if canonical_id not in members_by_canonical:
            members_by_canonical[canonical_id] = set()
            canonical_order.append(canonical_id)
        members_by_canonical[canonical_id].add(node_id)
    merge_groups: list[dict[str, Any]] = []
    for canonical_id in canonical_order:
        member_ids = list(members_by_canonical.get(canonical_id, set()))
        if len(member_ids) <= 1:
            if len(member_ids) == 1 and member_ids[0] != canonical_id:
                pass
            else:
                continue
        original_canonical_id = canonical_id
        if canonical_id not in member_ids:
            if canonical_id.startswith("canonical_") and canonical_id[10:] in member_ids:
                original_canonical_id = canonical_id[10:]
            else:
                def _simple_score(m_id):
                    lbl = node_lookup.get(m_id, {}).get("label", m_id)
                    return (len(lbl.split()), len(lbl))
                original_canonical_id = max(member_ids, key=_simple_score)
        canonical_node = node_lookup.get(original_canonical_id) or node_lookup.get(
            member_ids[0], {}
        )
        alias_ids = [member_id for member_id in member_ids if member_id != original_canonical_id]
        if not alias_ids:
            continue
        merge_groups.append(
            {
                "canonical_id": canonical_id,
                "canonical_label": canonical_node.get("label", canonical_id),
                "alias_ids": alias_ids,
                "alias_labels": [
                    node_lookup.get(alias_id, {}).get("label", alias_id)
                    for alias_id in alias_ids
                ],
                "reason": "Algorithmic match",
            }
        )
    return {
        "merge_group_count": len(merge_groups),
        "merged_alias_count": sum((len(group["alias_ids"]) for group in merge_groups)),
        "merge_groups": merge_groups,
        "node_count_before": len(nodes),
        "node_count_after": len(members_by_canonical),
        "edge_count_before": 0,
        "edge_count_after": 0,
        "mapping": normalized_map,
    }


def _apply_entity_resolution_to_extraction(
    nodes: list[dict[str, Any]],
    edges: list[dict[str, Any]],
    mapping: dict[str, str],
    schema_config: Any,
    llm_report: dict[str, Any] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    """
    Apply resolution to raw extraction lists before graph construction.

    This version performs actual merging (lossy) to eliminate duplicate nodes
    and remap their edges. Preserves investigative provenance in metadata.

    Args:
        nodes (list[dict[str, Any]]): Raw nodes list.
        edges (list[dict[str, Any]]): Raw edges list.
        mapping (dict[str, str]): ID mapping.
        schema_config (Any): Configuration for label and forensic merging.
        llm_report (dict, optional): Optional report from LLM-assisted pass.

    Returns:
        tuple[list[dict], list[dict], dict]: Resolved nodes, edges, and report.
    """
    normalized_map = _canonicalize_resolution_mapping(mapping)
    id_to_all_locations = defaultdict(set)
    for node in nodes:
        node_id = str(node.get("id"))
        loc = node.get("source_location", "")
        if loc:
            id_to_all_locations[node_id].update(
                [c.strip() for c in str(loc).split(";") if c.strip()]
            )
    node_lookup = {}
    for node in nodes:
        node_id = str(node.get("id"))
        if node_id is None:
            continue
        d_node = dict(node)
        if node_id in id_to_all_locations:
            d_node["source_location"] = "; ".join(
                sorted(list(id_to_all_locations[node_id]))
            )
        node_lookup[node_id] = d_node
    members_by_canonical: dict[str, list[str]] = {}
    canonical_order: list[str] = []
    for node in nodes:
        node_id = str(node.get("id"))
        canonical_id = normalized_map.get(node_id, node_id)
        if canonical_id not in members_by_canonical:
            members_by_canonical[canonical_id] = []
            canonical_order.append(canonical_id)
        members_by_canonical[canonical_id].append(node_id)
    resolved_nodes: list[dict[str, Any]] = []
    final_node_ids = set()
    for canonical_id in canonical_order:
        member_ids = members_by_canonical[canonical_id]
        member_nodes = [
            node_lookup[member_id]
            for member_id in member_ids
            if member_id in node_lookup
        ]
        if not member_nodes:
            continue
        best_label = max(
            [str(m.get("label", "")) for m in member_nodes],
            key=lambda label: _score_label_quality(label, schema_config),
        )
        norm_canonical = _normalize_person_name(best_label, schema_config).split()
        canonical_first_name = norm_canonical[0] if len(norm_canonical) >= 2 else None
        sterile_member_nodes = []
        for m in member_nodes:
            m_label = str(m.get("label", ""))
            norm_m = _normalize_person_name(m_label, schema_config).split()
            if canonical_first_name and len(norm_m) >= 2:
                if norm_m[0] != canonical_first_name and len(norm_m[0]) > 2:
                    continue
            sterile_member_nodes.append(m)
        if not sterile_member_nodes:
            sterile_member_nodes = [member_nodes[0]]
        if len(member_ids) == 1:
            base_node = dict(sterile_member_nodes[0])
            base_node["id"] = canonical_id
            semantic_class = _infer_semantic_class(base_node, schema_config)
            base_node["type"] = _type_for_semantic_class_from_config(
                semantic_class, base_node.get("type"), schema_config
            )
            base_node.setdefault("metadata", {})["semantic_class"] = semantic_class
            resolved_nodes.append(base_node)
            final_node_ids.add(canonical_id)
            continue
        base_node = dict(node_lookup.get(canonical_id, sterile_member_nodes[0]))
        base_node["id"] = canonical_id
        base_node["label"] = best_label
        semantic_class = _infer_semantic_class(base_node, schema_config)
        base_node["type"] = _type_for_semantic_class_from_config(
            semantic_class, base_node.get("type"), schema_config
        )
        all_locations = set()
        for m in sterile_member_nodes:
            loc = m.get("source_location", "")
            if loc:
                all_locations.update(
                    [c.strip() for c in str(loc).split(";") if c.strip()]
                )
        if all_locations:
            base_node["source_location"] = "; ".join(sorted(list(all_locations)))

        summs = [
            str(member.get("forensic_summary") or member.get("description") or "")
            for member in sterile_member_nodes
        ]
        merged_summ = _merge_text_values(summs, max_words=20)
        base_node["forensic_summary"] = merged_summ
        base_node["description"] = merged_summ

        if "metadata" not in base_node:
            base_node["metadata"] = {}
        base_node["metadata"]["is_canonical"] = True
        base_node["metadata"]["merge_reason"] = "Algorithmic match"
        base_node["metadata"]["semantic_class"] = semantic_class

        seen_aliases = set()
        unique_aliases = []
        for member in sterile_member_nodes:
            alias_id = member.get("id")
            if alias_id and alias_id not in seen_aliases:
                seen_aliases.add(alias_id)
                unique_aliases.append({
                    "original_id": alias_id,
                    "original_label": member.get("label"),
                    "original_forensic_summary": member.get("forensic_summary"),
                    "source_file": member.get("source_file"),
                    "source_location": member.get("source_location"),
                })
        base_node["metadata"]["resolved_aliases_data"] = unique_aliases
        resolved_nodes.append(base_node)
        final_node_ids.add(canonical_id)

    resolved_edges: list[dict[str, Any]] = []
    for edge in edges:
        source_id = str(edge.get("source"))
        target_id = str(edge.get("target"))
        new_source = normalized_map.get(source_id, source_id)
        new_target = normalized_map.get(target_id, target_id)

        if new_source not in final_node_ids or new_target not in final_node_ids:
            if source_id in final_node_ids:
                new_source = source_id
            if target_id in final_node_ids:
                new_target = target_id

            if new_source not in final_node_ids or new_target not in final_node_ids:
                continue

        relation = edge.get("relation", "")
        if new_source == new_target:
            if (
                "SPOUSE" in str(relation).upper()
                or "RELATIONSHIP" in str(relation).upper()
            ):
                logger.warning(
                    fix_text(
                        str(
                            f"🚩 RED FLAG: Blocked Identity Collapse Self-Loop ({relation}) for {new_source}"
                        )
                    )
                )
            continue
        new_edge = dict(edge)
        new_edge["source"] = new_source
        new_edge["target"] = new_target
        resolved_edges.append(new_edge)
    report = _build_entity_resolution_report(nodes, normalized_map)
    report["edge_count_before"] = len(edges)
    report["edge_count_after"] = len(resolved_edges)
    return (resolved_nodes, resolved_edges, report)


async def resolve_entities_via_blocking(
    nodes: list[dict[str, Any]],
    edges: list[dict[str, Any]],
    client: Any | None = None,
    schema_config: Any = None,
) -> dict[str, str]:
    """
    Strictly algorithmic entity resolution using token merging and structural similarity.

    Employs a multi-pass approach:
    1. Community-Aware Blocking: Compares nodes in the same connected components.
    2. Token/Surname Blocking: Compares nodes sharing names or identifiers.
    3. Global Hub Pass: Compares high-degree 'God Nodes' against all other nodes.
    4. Topological Tie-Breaking: Resolves ambiguous surname matches using neighbor context.

    Args:
        nodes (list[dict[str, Any]]): Raw nodes.
        edges (list[dict[str, Any]]): Raw edges.
        client (Any, optional): LLM client (not used in algorithmic pass).
        schema_config (Any, optional): Resolution thresholds and blocked tokens.

    Returns:
        dict[str, str]: A mapping of original IDs to canonical IDs.
    """
    logging.debug(

        fix_text(
            str(
                "\n"
                + "=" * 88
                + "\nALGORITHMIC ENTITY RESOLUTION\nScanning {} nodes and {} edges\n".format(
                    len(nodes), len(edges)
                )
                + "=" * 88
            )
        )
    )
    if not nodes:
        report = {
            "merge_group_count": 0,
            "merged_alias_count": 0,
            "merge_groups": [],
            "node_count_before": 0,
            "node_count_after": 0,
            "edge_count_before": len(edges),
            "edge_count_after": len(edges),
            "mapping": {},
        }
        resolve_entities_via_blocking.last_report = report
        return {}
    temp_graph = nx.Graph()
    for node in nodes:
        node_id = node.get("id")
        if node_id is None:
            continue
        temp_graph.add_node(
            str(node_id), **{k: v for k, v in node.items() if k != "id"}
        )
    adjacency_ids = defaultdict(set)
    for edge in edges:
        source = str(edge.get("source"))
        target = str(edge.get("target"))
        if source and target:
            temp_graph.add_edge(source, target)
            adjacency_ids[source].add(target)
            adjacency_ids[target].add(source)
    node_metadata = {}
    for n in temp_graph.nodes():
        attrs = temp_graph.nodes[n]
        label = str(attrs.get("label", n))
        node_type = str(attrs.get("type", "")).lower()
        node_metadata[n] = {
            "id": n,
            "label": label,
            "norm_person": _normalize_person_name(label, schema_config),
            "norm_org": _normalize_org_name(label, schema_config),
            "type": node_type,
            "entity_class": attrs.get("entity_class", ""),
            "degree": temp_graph.degree(n),
            "event_date": attrs.get("event_date"),
        }
        node_metadata[n]["semantic_class"] = _infer_semantic_class(
            node_metadata[n], schema_config
        )
    entity_evidence_roles = defaultdict(set)
    claim_to_evidence = defaultdict(set)
    for edge in edges:
        s, t = (str(edge.get("source")), str(edge.get("target")))
        rel = edge.get("relation")
        if rel == "supported_by":
            claim_to_evidence[s].add(t)
    for edge in edges:
        s, t = (str(edge.get("source")), str(edge.get("target")))
        rel = edge.get("relation")
        if rel == "asserted" and t in claim_to_evidence:
            for ev_id in claim_to_evidence[t]:
                entity_evidence_roles[s].add((ev_id, "subject"))
        elif rel == "targets" and s in claim_to_evidence:
            for ev_id in claim_to_evidence[s]:
                entity_evidence_roles[t].add((ev_id, "object"))
    parent = {n: n for n in temp_graph.nodes()}

    def find(i):
        if parent[i] == i:
            return i
        parent[i] = find(parent[i])
        return parent[i]

    def union(i, j):
        root_i = find(i)
        root_j = find(j)
        if root_i != root_j:
            parent[root_i] = root_j

    buckets = defaultdict(list)
    human_types = {
        str(t).lower().replace(" ", "").replace("_", "")
        for t in schema_config.human_entity_types
    }

    # 1. Community-Aware Blocking
    for i, component in enumerate(nx.connected_components(temp_graph)):
        block_id = f"community_block_{i}"
        for n_id in component:
            buckets[block_id].append(n_id)

    # 2. Token-Based & Surname Blocking
    for n_id, meta in node_metadata.items():
        m_type = str(meta["type"]).lower().replace(" ", "").replace("_", "")
        keys = set()
        if m_type in human_types:
            tokens = meta["norm_person"].split()
            if tokens:
                keys.add(f"fn_{tokens[0]}")
                keys.add(f"sn_{tokens[-1]}")
            else:
                keys.add("unknown_person")
        else:
            tokens = (
                meta["norm_org"].split()
                if meta["norm_org"]
                else meta["label"].lower().split()
            )
            if tokens:
                keys.add(f"org_{tokens[0]}")
            else:
                keys.add("unknown_other")

        for k in keys:
            buckets[k].append(n_id)
    comparisons_performed = 0
    node_ids = list(temp_graph.nodes())
    total_possible = (
        len(node_ids) * (len(node_ids) - 1) // 2 if len(node_ids) > 1 else 0
    )
    all_matches = set()
    for bucket_key, member_ids in buckets.items():
        if len(member_ids) < 2:
            continue
        for i in range(len(member_ids)):
            id1 = member_ids[i]
            n1 = node_metadata[id1]
            for j in range(i + 1, len(member_ids)):
                id2 = member_ids[j]
                n2 = node_metadata[id2]
                comparisons_performed += 1
                if _is_match(
                    n1, n2, adjacency_ids, schema_config, entity_evidence_roles
                ):
                    all_matches.add(tuple(sorted((id1, id2))))

    god_threshold = schema_config.graph_analysis.god_node_threshold
    god_nodes = [
        n_id for n_id, meta in node_metadata.items() if meta["degree"] > god_threshold
    ]
    if god_nodes:
        logging.debug(

            fix_text(
                str(
                    f"DEBUG: Performing Global Hub comparisons for {len(god_nodes)} whale(s) [Threshold > {god_threshold}]"
                )
            )
        )
        for g_id in god_nodes:
            n1 = node_metadata[g_id]
            for other_id in node_ids:
                if g_id == other_id:
                    continue
                n2 = node_metadata[other_id]
                if _is_match(
                    n1, n2, adjacency_ids, schema_config, entity_evidence_roles
                ):
                    all_matches.add(tuple(sorted((g_id, other_id))))

    # 3. Global Topological Tie-Breaker Pass
    match_adj = defaultdict(set)
    for u, v in all_matches:
        match_adj[u].add(v)
        match_adj[v].add(u)

    for u, v in sorted(list(all_matches)):
        n1, n2 = node_metadata[u], node_metadata[v]
        t1 = n1["norm_person"].split() if n1["type"] in human_types else []
        t2 = n2["norm_person"].split() if n2["type"] in human_types else []
        is_containment, single_id, multi_id = False, None, None
        if len(t1) == 1 and len(t2) > 1:
            is_containment, single_id, multi_id = True, u, v
        elif len(t2) == 1 and len(t1) > 1:
            is_containment, single_id, multi_id = True, v, u

        if is_containment:
            multi_candidates = [
                m
                for m in match_adj[single_id]
                if len(node_metadata[m]["norm_person"].split()) > 1
            ]
            if multi_candidates:
                best_multi = max(
                    multi_candidates, key=lambda mid: node_metadata[mid]["degree"]
                )
                if multi_id != best_multi:
                    continue

        union(u, v)
    efficiency = (
        (1 - comparisons_performed / total_possible) * 100 if total_possible > 0 else 0
    )
    logging.debug(

        fix_text(
            str(
                f"Efficiency Gain: {efficiency:.1f}% comparisons skipped (Performed {comparisons_performed} vs {total_possible} possible)."
            )
        )
    )

    groups = defaultdict(list)
    for n in temp_graph.nodes():
        groups[find(n)].append(n)

    groups = _deduplicate_canonical_groups(
        groups, node_metadata, adjacency_ids, schema_config
    )
    final_authority_groups = {}
    human_types = {
        str(t).lower().replace(" ", "").replace("_", "")
        for t in schema_config.human_entity_types
    }
    for root, members in groups.items():
        if len(members) <= 1:
            final_authority_groups[root] = members
            continue
        sub_clusters = defaultdict(list)
        generic_nodes = []
        for m in members:
            meta = node_metadata[m]
            m_type = str(meta["type"]).lower().replace(" ", "").replace("_", "")
            if m_type in human_types:
                t = meta["norm_person"].split()
                if len(t) >= 2:
                    sub_clusters[t[0]].append(m)
                else:
                    generic_nodes.append(m)
            else:
                generic_nodes.append(m)
        if len(sub_clusters) <= 1:
            final_authority_groups[root] = members
            continue
        logging.debug(

            fix_text(
                str(
                    f"DEBUG: FINAL AUTHORITY split for group '{root}': {list(sub_clusters.keys())}"
                )
            )
        )
        sub_cluster_degrees = {
            fn: sum((node_metadata[nid].get("degree", 0) for nid in ids))
            for fn, ids in sub_clusters.items()
        }
        for fn, deg in sub_cluster_degrees.items():
            if deg > god_threshold:
                sub_cluster_degrees[fn] += 10000
        for first_name, node_ids in sub_clusters.items():
            new_root = node_ids[0]
            final_authority_groups[new_root] = node_ids
            for g in generic_nodes:
                g_label = node_metadata[g]["norm_person"]

                matching_fns = [
                    fn
                    for fn, ids in sub_clusters.items()
                    if any(
                        (g_label in node_metadata[nid]["norm_person"] for nid in ids)
                    )
                ]
                if matching_fns:
                    if len(matching_fns) > 1:
                        if g not in final_authority_groups:
                            final_authority_groups[g] = [g]
                        continue
                    best_fn = max(matching_fns, key=lambda fn: sub_cluster_degrees[fn])
                    if first_name == best_fn:
                        if g not in [
                            n for gc in final_authority_groups.values() for n in gc
                        ]:
                            final_authority_groups[new_root].append(g)
    groups = final_authority_groups
    god_threshold = schema_config.graph_analysis.god_node_threshold

    def _canonical_score(m_id):
        lbl = node_metadata[m_id]["label"]
        tokens = lbl.split()
        token_count = len(tokens)
        is_god = 2 if node_metadata[m_id]["degree"] > god_threshold else 0
        return (token_count, is_god, node_metadata[m_id]["degree"], len(lbl))

    merge_groups: list[EntityMergeGroup] = []
    for root, members in groups.items():
        if len(members) <= 1:
            continue
        canonical_id = max(members, key=_canonical_score)
        alias_ids = [m for m in members if m != canonical_id]
        merge_groups.append(
            EntityMergeGroup(
                canonical_id=canonical_id,
                alias_ids=alias_ids,
                canonical_label=node_metadata[canonical_id]["label"],
                reason="Algorithmic match (token/structural similarity)",
            )
        )
    plan = EntityResolutionPlan(merge_groups=merge_groups)
    mapping = {
        str(node.get("id")): str(node.get("id"))
        for node in nodes
        if node.get("id") is not None
    }
    for group in groups.values():
        if len(group) <= 1:
            continue
        canonical_id = max(group, key=_canonical_score)
        target_id = (
            canonical_id
            if str(canonical_id).startswith("canonical_")
            else f"canonical_{canonical_id}"
        )
        for member_id in group:
            mapping[str(member_id)] = target_id
    _, resolution_report = _resolve_entity_groups_from_plan(
        temp_graph, plan, schema_config
    )
    logging.debug(

        fix_text(
            str(
                f"Resolved {resolution_report['merged_alias_count']} alias node(s) across {resolution_report['merge_group_count']} merge group(s)."
            )
        )
    )
    resolve_entities_via_blocking.last_report = resolution_report

    return mapping


async def resolve_entities(
    graph: nx.Graph, client: Any | None = None
) -> tuple[nx.Graph, dict[str, Any]]:
    """
    Perform graph-level algorithmic entity resolution.

    Args:
        graph (nx.Graph): The NetworkX graph to resolve.
        client (Any, optional): LLM client (not used).

    Returns:
        tuple[nx.Graph, dict[str, Any]]: The resolved graph and its report.
    """
    nodes = [{"id": n, **data} for n, data in graph.nodes(data=True)]
    edges = [
        {"source": u, "target": v, **data} for u, v, data in graph.edges(data=True)
    ]
    # This requires schema_config which is usually part of the pipeline.
    # If called standalone, it might need default settings.
    from src.models.models import SCHEMA_CONFIG

    mapping = await resolve_entities_via_blocking(
        nodes, edges, schema_config=SCHEMA_CONFIG
    )
    resolved_nodes, resolved_edges, report = _apply_entity_resolution_to_extraction(
        nodes, edges, mapping, schema_config=SCHEMA_CONFIG
    )
    new_G = nx.Graph()
    new_G.graph.update(graph.graph)
    for node in resolved_nodes:
        new_G.add_node(node["id"], **{k: v for k, v in node.items() if k != "id"})
    for edge in resolved_edges:
        new_G.add_edge(
            edge["source"],
            edge["target"],
            **{k: v for k, v in edge.items() if k not in ("source", "target")},
        )
    return (new_G, report)
