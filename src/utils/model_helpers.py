from typing import Any, Tuple


def sanitize_value(value: Any) -> str:
    """Safely converts any input value to a trimmed, single-line string.

    Handles None, lists, and dicts by returning an empty string to prevent
    serialization errors in graph properties.

    Args:
        value: The raw input to be sanitized.

    Returns:
        A cleaned string representation.
    """
    if value is None:
        return ""
    if isinstance(value, (list, dict)):
        return ""
    return str(value).strip()


def merge_text_values(values: list[str], *, max_words: int = 20) -> str:
    """Merges multiple text strings into a deduplicated, concise summary.

    Commonly used for merging forensic summaries during node or edge unification.

    Args:
        values: A list of strings to be merged.
        max_words: The word limit for the resulting summary.

    Returns:
        A combined string with duplicates removed and truncated to max_words.
    """
    seen = set()
    kept = []
    for v in values:
        if not v:
            continue
        clean = v.strip().lower()
        if clean not in seen:
            seen.add(clean)
            kept.append(v.strip())

    if not kept:
        return ""

    text = " | ".join(kept)
    words = text.split()
    if len(words) > max_words:
        return " ".join(words[:max_words]) + "..."
    return text


def norm_type_string(raw_type: Any) -> str:
    """Normalizes a type string for structural comparisons.

    Removes spaces, underscores, and case sensitivity.

    Args:
        raw_type: The raw type identifier.

    Returns:
        A canonicalized lowercase string with no whitespace or separators.
    """
    return str(raw_type or "").strip().lower().replace(" ", "").replace("_", "")


def normalize_confidence_value(
    confidence: str | None,
    confidence_score: float | None,
    confidence_aliases: dict[str, str],
    default_label: str = "EXTRACTED",
) -> Tuple[str, float]:
    """Normalizes confidence tokens and ensures a valid numeric score.

    Maps diverse LLM outputs (e.g., 'Direct', 'Inferred', 'Hearsay') to canonical
    labels and assigns default scores if the LLM did not provide one.

    Args:
        confidence: The raw confidence string or label.
        confidence_score: The numeric score (0.0 to 1.0).
        confidence_aliases: A dictionary mapping raw tokens to canonical ones.
        default_label: The fallback label if no match is found.

    Returns:
        A tuple of (canonical_label, numeric_score).
    """
    raw = sanitize_value(confidence)
    normalized = confidence_aliases.get(
        raw, confidence_aliases.get(raw.upper(), default_label)
    )

    if normalized in ["EXTRACTED", "DIRECT"]:
        default_score = 1.0
    elif normalized == "INFERRED":
        default_score = 0.8
    elif normalized == "AMBIGUOUS":
        default_score = 0.2
    else:
        default_score = 1.0

    if confidence_score is None:
        return normalized, default_score

    return normalized, float(confidence_score)


def should_keep_edge(
    numeric_score: float, hard_drop_score: float, effective_min_score: float
) -> bool:
    """Determines if a relationship edge should be retained based on confidence.

    Applies a two-tier filter: a 'hard drop' for obviously low-quality data,
    and an 'effective minimum' that can be adjusted based on the relation type.

    Args:
        numeric_score: The normalized confidence score.
        hard_drop_score: The absolute floor below which edges are discarded.
        effective_min_score: The target threshold for retention.

    Returns:
        True if the edge meets retention criteria.
    """
    if numeric_score < hard_drop_score:
        return False
    return numeric_score >= effective_min_score
