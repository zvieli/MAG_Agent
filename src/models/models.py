"""Domain-specific Data Transfer Objects (DTOs) and centralized investigative constants.
"""

import asyncio
import threading
import logging
import os
from typing import Any, List, Tuple
from pathlib import Path
from pydantic import BaseModel, Field, field_validator
from src.models.base import SchemaConfigModel
from src.utils.model_helpers import (
    sanitize_value,
    should_keep_edge,
)

def load_schema_config(config_path: str | Path | None = None) -> SchemaConfigModel:
    """Load the investigation schema configuration from Azure-backed config storage."""
    from src.models.base import SchemaConfigModel
    
    # --- LOCAL-FIRST: Try loading from bundled filesystem ---
    local_paths = [
        Path("/home/site/wwwroot/schemas/ontology_schema.json"),
        Path(__file__).resolve().parent.parent.parent / "schemas" / "ontology_schema.json",
    ]
    if config_path:
        local_paths.insert(0, Path(config_path))
    
    for local_path in local_paths:
        if local_path.is_file():
            try:
                import json
                data = json.loads(local_path.read_text(encoding="utf-8"))
                config = SchemaConfigModel.model_validate(data)
                if config.relation_types:  # Sanity check: schema loaded with content
                    logging.info(
                        f"[MODELS] [SCHEMA_LOAD] Loaded schema from local file: {local_path} "
                        f"({len(config.relation_types)} relations, {len(config.entity_types)} entity types)"
                    )
                    return config
            except Exception as e:
                logging.warning(f"[MODELS] [SCHEMA_LOAD] Local file {local_path} failed: {e}")

    if os.environ.get("SKIP_SCHEMA_LOAD_DURING_IMPORT") == "true":
        return SchemaConfigModel()

    def _run_coro_sync(coro: Any) -> Any:
        try:
            return asyncio.run(coro)
        except Exception:
            result = {}
            error = {}
            def _runner():
                try:
                    result["value"] = asyncio.run(coro)
                except Exception as exc:
                    error["exc"] = exc
            thread = threading.Thread(target=_runner)
            thread.start()
            thread.join()
            if error:
                return None
            return result.get("value")

    try:
        from src.utils.config_manager import config_manager
        data = _run_coro_sync(asyncio.wait_for(
            config_manager.get_json_config('ontology_schema.json', category='schemas'),
            timeout=3.0
        ))
        if data:
            return SchemaConfigModel.model_validate(data)
    except Exception as e:
        logging.warning(f"[MODELS] [SCHEMA_LOAD] [WARNING] Could not load schema during import: {e}. Using defaults.")
    
    return SchemaConfigModel()

# Internal cache for lazy loading
_CACHE = {
    "SCHEMA_CONFIG": None,
    "RELATION_LABELS": None,
    "HEBREW_RELATION_LABELS": None,
    "RELATION_ALIASES": None,
    "ENTITY_TYPES": None,
    "CONFIDENCE_LABELS": None,
    "CONFIDENCE_ALIASES": None,
    "ALLOWED_CONFIDENCE_LABELS": None
}
_LOCK = threading.RLock()

def _get_cached(key):
    global _CACHE
    if _CACHE[key] is None:
        with _LOCK:
            if _CACHE[key] is None:
                if key == "SCHEMA_CONFIG":
                    _CACHE["SCHEMA_CONFIG"] = load_schema_config()
                elif key == "RELATION_LABELS":
                    _CACHE["RELATION_LABELS"] = _get_cached("SCHEMA_CONFIG").relation_types or {}
                elif key == "HEBREW_RELATION_LABELS":
                    labels = _get_cached("RELATION_LABELS")
                    _CACHE["HEBREW_RELATION_LABELS"] = set(labels.keys()) | {"OTHER_RELATION"}
                elif key == "RELATION_ALIASES":
                    labels = _get_cached("RELATION_LABELS")
                    aliases = {k.upper(): k for k in labels.keys()} if labels else {}
                    aliases["OTHER_RELATION"] = "OTHER_RELATION"
                    _CACHE["RELATION_ALIASES"] = aliases
                elif key == "ENTITY_TYPES":
                    config = _get_cached("SCHEMA_CONFIG")
                    if isinstance(config.entity_types, dict):
                        _CACHE["ENTITY_TYPES"] = list(config.entity_types.keys()) + ["OTHER"]
                    else:
                        _CACHE["ENTITY_TYPES"] = (config.entity_types or []) + ["OTHER"]
                elif key == "CONFIDENCE_LABELS":
                    _CACHE["CONFIDENCE_LABELS"] = _get_cached("SCHEMA_CONFIG").confidence_levels or {}
                elif key == "CONFIDENCE_ALIASES":
                    labels = _get_cached("CONFIDENCE_LABELS")
                    aliases = {
                        "EXTRACTED": "EXTRACTED",
                        "DIRECT": "EXTRACTED",
                        "INFERRED": "INFERRED",
                        "AMBIGUOUS": "AMBIGUOUS",
                        "HEARSAY": "HEARSAY",
                        "RAIA_YESHIRA": "EXTRACTED",
                        "NISTI": "INFERRED",
                        "AMBUIGUOUS": "AMBIGUOUS",
                    }
                    for k in labels.keys():
                        if k not in aliases:
                            aliases[k] = k
                    _CACHE["CONFIDENCE_ALIASES"] = aliases
                elif key == "ALLOWED_CONFIDENCE_LABELS":
                    labels = _get_cached("CONFIDENCE_LABELS")
                    allowed = set(labels.keys())
                    allowed.update(["EXTRACTED", "DIRECT"])
                    _CACHE["ALLOWED_CONFIDENCE_LABELS"] = allowed
    return _CACHE[key]

# Magic __getattr__ for module-level lazy constants
def __getattr__(name):
    if name in _CACHE:
        return _get_cached(name)
    raise AttributeError(f"module {__name__} has no attribute {name}")

# Static mappings that don't depend on external config
HEBREW_TYPE_MAP = {
    "SUSPECT": "חשוד",
    "WITNESS": "עד",
    "ACCOMPLICE": "עד מדינה",
    "ORGANIZATION": "ארגון",
    "DOCUMENT": "מסמך",
    "LAW": "חוק",
    "EXHIBIT": "מוצג",
    "EVENT": "אירוע",
    "OFFENSE": "עבירה",
    "PERSON": "אדם"
}

# Functions for runtime logic
def normalize_relation_label(relation: Any) -> str:
    raw = sanitize_value(relation)
    if not raw:
        return ""
    hebrew_labels = _get_cached("HEBREW_RELATION_LABELS")
    if raw in hebrew_labels:
        return raw
    compact = raw.replace(" ", "_")
    if compact in hebrew_labels:
        return compact
    aliases = _get_cached("RELATION_ALIASES")
    return aliases.get(raw.upper(), "OTHER_RELATION")

def normalize_confidence_label(label: Any, score: float | None = None) -> Tuple[str, float]:
    raw = str(label or "EXTRACTED").upper().strip()
    aliases = _get_cached("CONFIDENCE_ALIASES")
    final_label = aliases.get(raw, "EXTRACTED")
    
    if score is None:
        labels = _get_cached("CONFIDENCE_LABELS")
        score = labels.get(final_label, 0.7)
    return final_label, float(score)

# Aliases for graph_manager.py compatibility
_normalize_confidence = normalize_confidence_label
def should_keep_edge_by_confidence(
    confidence: Any, score: float | None = None, relation: str = ""
) -> bool:
    config = _get_cached("SCHEMA_CONFIG")
    hard_drop = getattr(config, "hard_drop_confidence_score", 0.5)
    effective_min = getattr(config, "min_confidence_score", 0.6)
    
    special = getattr(config, "special_thresholds", {}) or {}
    if relation:
        for spec_key, spec_val in special.items():
            if spec_key.lower() in str(relation).lower():
                effective_min = spec_val
                break

    _, numeric_score = normalize_confidence_label(confidence, score)
    return should_keep_edge(numeric_score, hard_drop, effective_min)


# Pydantic Models
class Node(BaseModel):
    id: str
    label: str = ""
    type: str = "Entity"
    forensic_summary: str = ""
    event_date: str | None = None
    source_file: str = "unknown"
    source_location: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)
    model_config = {"populate_by_name": True}

    @field_validator("type")
    @classmethod
    def validate_node_type(cls, v):
        allowed = set(_get_cached("ENTITY_TYPES"))
        if not allowed or allowed == {"OTHER"}:
            return v
        if v in allowed:
            return v
        logging.warning(f"[VALIDATION] Node type '{v}' not in allowed set, preserving as-is.")
        return v

class Edge(BaseModel):
    source: str
    target: str
    relation: str
    description: str = ""
    confidence: str = "EXTRACTED"
    confidence_score: float | None = None
    source_file: str = "unknown"
    source_location: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("relation")
    @classmethod
    def validate_relation_type(cls, v):
        allowed = set(_get_cached("RELATION_LABELS").keys())
        if not allowed:
            return v
        if v in allowed:
            return v
        norm = normalize_relation_label(v)
        if norm in allowed:
            return norm
        logging.warning(f"[VALIDATION] Relation '{v}' not in allowed set, preserving as-is.")
        return v

class RawGraphExtraction(BaseModel):
    nodes: List[Node] = Field(default_factory=list)
    edges: List[Edge] = Field(default_factory=list)
