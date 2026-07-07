import re
import json
import hashlib
import time
import logging
from pathlib import Path
from typing import Any
from src.utils.bidi_utils import fix_text


def _log_failed_repair(content: str, error_msg: str) -> None:
    """Log raw LLM output to logs/failed_repairs for forensic analysis."""
    try:
        log_dir = Path("logs/failed_repairs")
        log_dir.mkdir(parents=True, exist_ok=True)

        # Use content hash + timestamp for unique filename
        content_hash = hashlib.md5(content.encode("utf-8")).hexdigest()[:8]
        timestamp = int(time.time())
        log_file = log_dir / f"fail_{timestamp}_{content_hash}.txt"

        log_entry = f"ERROR: {error_msg}\n"
        log_entry += "=" * 80 + "\n"
        log_entry += content
        log_entry += "\n" + "=" * 80 + "\n"

        log_file.write_text(log_entry, encoding="utf-8")
        logging.info(fix_text(f"[Forensics] Failed JSON repair logged to {log_file}"))
    except Exception as e:
        logging.error(fix_text(f"[DEBUG] Error logging failed repair: {e}"))


def _extract_json_payload(content: str) -> str:
    """Extracts a JSON string from a potentially noisy LLM response.

    Handles markdown code blocks and attempts to find the outermost
    curly braces. If the JSON is truncated, it delegates to closure logic.

    Args:
        content: The raw text response from the LLM.

    Returns:
        A string containing only the (potentially repaired) JSON object.

    Raises:
        ValueError: If no JSON object can be identified in the response.
    """
    text = content.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines).strip()

    # Find first '{' and last '}'
    start = text.find("{")
    end = text.rfind("}")

    if start == -1:
        _log_failed_repair(content, "Model response did not contain a JSON object.")
        raise ValueError("Model response did not contain a JSON object.")

    if end == -1 or end < start:
        # Potentially truncated JSON
        try:
            return _close_truncated_json(text[start:])
        except Exception as e:
            _log_failed_repair(content, f"Truncated JSON closure failed: {e}")
            raise

    return text[start : end + 1]


def _close_truncated_json(json_str: str) -> str:
    """Robustly closes a truncated JSON string using iterative balancing.

    Attempts to fix incomplete JSON (often caused by token limits) by
    calculating necessary closing brackets and stripping trailing
    dangling keys or commas.

    Args:
        json_str: The incomplete JSON string.

    Returns:
        A syntactically valid JSON string.
    """

    def balance(s: str) -> str:
        stack = []
        in_string = False
        escape = False
        for char in s:
            if escape:
                escape = False
                continue
            if char == "\\":
                escape = True
                continue
            if char == '"':
                in_string = not in_string
                continue
            if not in_string:
                if char == "{":
                    stack.append("}")
                elif char == "[":
                    stack.append("]")
                elif char == "}":
                    if stack and stack[-1] == "}":
                        stack.pop()
                elif char == "]":
                    if stack and stack[-1] == "]":
                        stack.pop()

        res = s
        if in_string:
            res += '"'

        # Remove trailing comma if any
        res = res.rstrip()
        if res.endswith(","):
            res = res[:-1]

        for closer in reversed(stack):
            res += closer
        return res

    # Try balancing the original
    balanced = balance(json_str)
    try:
        json.loads(balanced)
        return balanced
    except json.JSONDecodeError:
        pass

    # If it fails, iteratively remove characters from the end of json_str (up to 500 chars)
    # until balancing makes it valid.
    # This handles dangling keys like {"id": "foo", "desc
    for i in range(1, min(len(json_str), 501)):
        truncated = json_str[:-i]
        balanced = balance(truncated)
        try:
            json.loads(balanced)
            return balanced
        except json.JSONDecodeError:
            continue

    return json_str  # Fallback to original if all else fails


def _repair_json_nodes(payload_str: str) -> str:
    """Normalizes and repairs the structural integrity of an extraction payload.

    Performs the following 'healing' operations:
    1. Ensures every node has a unique ID and a human-readable label.
    2. Flattens property dictionaries into top-level fields.
    3. Remaps edge endpoints to use canonicalized snake_case IDs.
    4. Fixes hallucinated keys (e.g., 'relation_type' -> 'relation').
    5. Preserves Hebrew characters in IDs while stripping invalid punctuation.

    Args:
        payload_str: The raw JSON string from extraction.

    Returns:
        A repaired and canonicalized JSON string.
    """
    try:
        payload = json.loads(payload_str)
    except json.JSONDecodeError as e:
        _log_failed_repair(payload_str, f"JSON load failed in repair_json_nodes: {e}")
        return payload_str

    if not isinstance(payload, dict):
        return payload_str

    nodes = payload.get("nodes", [])
    edges = payload.get("edges", [])
    if not isinstance(nodes, list):
        return payload_str

    def to_safe_id(value: str) -> str:
        """Convert a string to a safe node ID while preserving Hebrew UTF-8 characters."""
        if not value:
            return "unknown"
        # Preserve Hebrew characters, only replace spaces and invalid punctuation
        safe = re.sub(r"[^a-zA-Z0-9_\u0590-\u05FF]+", "_", value).strip("_").lower()
        return safe or "unknown"

    # Track id remapping and aliases for robust edge endpoint rewrites
    id_mapping: dict[str, str] = {}
    endpoint_alias_map: dict[str, str] = {}

    def _register_alias(value: Any, canonical_id: str) -> None:
        text = str(value or "").strip()
        if not text:
            return
        variants = {
            text,
            text.lower(),
            text.replace(" ", "_"),
            text.replace(" ", "_").lower(),
        }
        for variant in variants:
            endpoint_alias_map[variant] = canonical_id

    def _ensure_unique_id(candidate_id: str, used_ids: set[str]) -> str:
        base = candidate_id or "unknown"
        if base not in used_ids:
            used_ids.add(base)
            return base
        suffix = 2
        while f"{base}_{suffix}" in used_ids:
            suffix += 1
        unique = f"{base}_{suffix}"
        used_ids.add(unique)
        return unique

    repaired_nodes = []
    used_ids: set[str] = set()
    for node in nodes:
        if isinstance(node, dict):
            # Flatten properties if they exist (Triple-Enhanced format)
            props = node.get("properties", {})
            if isinstance(props, dict):
                if not node.get("label") and props.get("name"):
                    node["label"] = props.get("name")
                if not node.get("description") and props.get("forensic_summary"):
                    node["description"] = props.get("forensic_summary")
                # Also support direct forensic_summary if description is missing
                if not node.get("description") and node.get("forensic_summary"):
                    node["description"] = node.get("forensic_summary")

            old_id = str(node.get("id") or "").strip()
            # If label is missing, use id or generate one
            if "label" not in node or not node["label"]:
                if "id" in node and node["id"]:
                    node["label"] = node["id"]
                else:
                    node["label"] = "unknown"
            node_label = str(node.get("label") or "").strip()

            node_id = str(node.get("id") or "").strip()
            if not node_id:
                node_id = to_safe_id(node_label or "unknown")
            else:
                node_id = to_safe_id(node_id)

            canonical_id = _ensure_unique_id(node_id, used_ids)
            if old_id and old_id != canonical_id:
                id_mapping[old_id] = canonical_id
            node["id"] = canonical_id

            _register_alias(canonical_id, canonical_id)
            _register_alias(old_id, canonical_id)
            _register_alias(node_label, canonical_id)

            # Ensure description is present for downstream tasks
            if (
                "description" not in node
                or not str(node.get("description", "")).strip()
            ):
                node["description"] = str(
                    node.get("forensic_summary")
                    or node.get("label")
                    or node.get("type")
                    or "No description available"
                ).strip()

            # Ensure forensic_summary is also present for internal consistency
            if (
                "forensic_summary" not in node
                or not str(node.get("forensic_summary", "")).strip()
            ):
                node["forensic_summary"] = node["description"]
        repaired_nodes.append(node)

    # Remap edges to use new node ids and fix hallucinated keys
    def _normalize_endpoint(endpoint: Any) -> Any:
        if isinstance(endpoint, dict):
            endpoint = (
                endpoint.get("id") or endpoint.get("label") or endpoint.get("name")
            )
        if endpoint is None:
            return endpoint
        text = str(endpoint).strip()
        if not text:
            return text
        if text in id_mapping:
            return id_mapping[text]
        if text in endpoint_alias_map:
            return endpoint_alias_map[text]
        lowered = text.lower()
        if lowered in endpoint_alias_map:
            return endpoint_alias_map[lowered]
        underscored = text.replace(" ", "_")
        if underscored in endpoint_alias_map:
            return endpoint_alias_map[underscored]
        underscored_lower = underscored.lower()
        if underscored_lower in endpoint_alias_map:
            return endpoint_alias_map[underscored_lower]
        return text

    repaired_edges = []
    if isinstance(edges, list):
        for edge in edges:
            if isinstance(edge, dict):
                # Flatten properties for edges too
                props = edge.get("properties", {})
                if isinstance(props, dict):
                    if not edge.get("forensic_summary") and props.get(
                        "forensic_summary"
                    ):
                        edge["forensic_summary"] = props.get("forensic_summary")

                if (
                    "occurrence_date" not in edge or not edge.get("occurrence_date")
                ) and edge.get("date"):
                    edge["occurrence_date"] = edge.get("date")

                # Fix hallucinated keys: rename label to relation if relation is missing
                if ("label" in edge or "type" in edge) and (
                    "relation" not in edge or not edge.get("relation")
                ):
                    if "label" in edge and edge["label"]:
                        edge["relation"] = edge["label"]
                    elif "type" in edge and edge["type"]:
                        edge["relation"] = edge["type"]
                    # Remove the wrong key
                    edge.pop("label", None)
                    edge.pop("type", None)

                # Remap source and target using robust alias/id resolution
                if "source" in edge:
                    edge["source"] = _normalize_endpoint(edge["source"])
                if "target" in edge:
                    edge["target"] = _normalize_endpoint(edge["target"])

                # Ensure metadata is a dict
                if "metadata" in edge and isinstance(edge["metadata"], str):
                    edge["metadata"] = {"description": edge["metadata"]}
                elif "metadata" not in edge:
                    edge["metadata"] = {}
            repaired_edges.append(edge)

    # Repair evidence_fragments and claims metadata
    repaired_fragments = []
    fragments = payload.get("evidence_fragments", [])
    if isinstance(fragments, list):
        for frag in fragments:
            if isinstance(frag, dict):
                if "metadata" in frag and isinstance(frag["metadata"], str):
                    frag["metadata"] = {"description": frag["metadata"]}
                elif "metadata" not in frag:
                    frag["metadata"] = {}
            repaired_fragments.append(frag)

    repaired_claims = []
    claims = payload.get("claims", [])
    if isinstance(claims, list):
        for claim in claims:
            if isinstance(claim, dict):
                if "metadata" in claim and isinstance(claim["metadata"], str):
                    claim["metadata"] = {"description": claim["metadata"]}
                elif "metadata" not in claim:
                    claim["metadata"] = {}
            repaired_claims.append(claim)

    payload["nodes"] = repaired_nodes
    payload["edges"] = repaired_edges
    payload["evidence_fragments"] = repaired_fragments
    payload["claims"] = repaired_claims

    return json.dumps(payload, ensure_ascii=False)
