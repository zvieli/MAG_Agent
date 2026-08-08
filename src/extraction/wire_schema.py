"""Strict Structured Outputs contract for graph extraction.

The wire contract is deliberately separate from the graph DTOs used by the rest of
the application.  Provider output is validated here before source metadata,
normalization, caching, or entity resolution can occur.
"""

from __future__ import annotations

import hashlib
import json
import re
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, create_model, model_validator

from src.models.models import Edge, Node, RawGraphExtraction


class ExtractionNodeWire(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    id: str
    label: str
    type: str
    forensic_summary: str
    event_date: str | None


class ExtractionEdgeWire(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    source: str
    target: str
    relation: str
    description: str
    confidence: str
    confidence_score: float = Field(ge=0.0, le=1.0)


class ExtractionWireModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    nodes: list[ExtractionNodeWire]
    edges: list[ExtractionEdgeWire]

    @model_validator(mode="after")
    def validate_graph_integrity(self) -> "ExtractionWireModel":
        node_ids = [node.id for node in self.nodes]
        duplicate_ids = sorted({node_id for node_id in node_ids if node_ids.count(node_id) > 1})
        if duplicate_ids:
            raise ValueError(f"duplicate node IDs: {', '.join(duplicate_ids)}")

        available = set(node_ids)
        for edge in self.edges:
            if edge.source == edge.target:
                raise ValueError(f"self-loop is not permitted: {edge.source}")
            missing = [endpoint for endpoint in (edge.source, edge.target) if endpoint not in available]
            if missing:
                raise ValueError(
                    f"edge {edge.source}->{edge.target} references absent node IDs: "
                    + ", ".join(missing)
                )
        return self


def _enum(name: str, values: list[str]) -> type[Enum]:
    """Build an enum whose values are canonical ontology keys."""
    members: dict[str, str] = {}
    for index, value in enumerate(dict.fromkeys(str(item) for item in values if str(item))):
        key = re.sub(r"\W+", "_", value, flags=re.UNICODE).strip("_").upper()
        if not key or key[0].isdigit():
            key = f"VALUE_{index}"
        while key in members:
            key = f"{key}_{index}"
        members[key] = value
    if not members:
        raise ValueError(f"Cannot construct {name}: ontology contains no values")
    return Enum(name, members, type=str)


def create_wire_extraction_model(schema_config: Any) -> type[ExtractionWireModel]:
    """Create the provider contract with enums derived from canonical ontology keys."""
    entity_values = list((getattr(schema_config, "entity_types", {}) or {}).keys())
    relation_values = list((getattr(schema_config, "relation_types", {}) or {}).keys())
    confidence_values = list((getattr(schema_config, "confidence_levels", {}) or {}).keys())
    entity_enum = _enum("OntologyEntityType", entity_values)
    relation_enum = _enum("OntologyRelationType", relation_values)
    confidence_enum = _enum("OntologyConfidence", confidence_values)

    node_model = create_model(
        "StrictExtractionNode",
        __base__=ExtractionNodeWire,
        type=(entity_enum, ...),
    )
    edge_model = create_model(
        "StrictExtractionEdge",
        __base__=ExtractionEdgeWire,
        relation=(relation_enum, ...),
        confidence=(confidence_enum, ...),
    )
    return create_model(
        "StrictGraphExtraction",
        __base__=ExtractionWireModel,
        nodes=(list[node_model], ...),
        edges=(list[edge_model], ...),
    )


class SchemaPreflightError(RuntimeError):
    """Raised locally when a Structured Outputs schema exceeds provider limits."""


_UNSUPPORTED_KEYWORDS = {
    "allOf",
    "not",
    "if",
    "then",
    "else",
    "dependentRequired",
    "dependentSchemas",
    "patternProperties",
    "unevaluatedProperties",
    "minContains",
    "maxContains",
    "propertyNames",
}


def lint_structured_output_schema(schema: dict[str, Any]) -> dict[str, int]:
    """Validate documented Structured Outputs limits before making an API call."""
    property_count = 0
    enum_count = 0
    max_depth = 0
    unsupported: set[str] = set()
    object_errors: list[str] = []

    def walk(value: Any, depth: int, path: str) -> None:
        nonlocal property_count, enum_count, max_depth
        if isinstance(value, dict):
            max_depth = max(max_depth, depth)
            unsupported.update(_UNSUPPORTED_KEYWORDS.intersection(value))
            properties = value.get("properties")
            if isinstance(properties, dict):
                property_count += len(properties)
                required = value.get("required")
                if set(required or []) != set(properties):
                    object_errors.append(f"{path}: every property must be required")
                if value.get("additionalProperties") is not False:
                    object_errors.append(f"{path}: additionalProperties must be false")
            enum_values = value.get("enum")
            if isinstance(enum_values, list):
                enum_count += len(enum_values)
            for key, child in value.items():
                walk(child, depth + (1 if key in {"properties", "items", "$defs"} else 0), f"{path}.{key}")
        elif isinstance(value, list):
            for index, child in enumerate(value):
                walk(child, depth, f"{path}[{index}]")

    walk(schema, 1, "$")
    schema_chars = len(json.dumps(schema, ensure_ascii=False, separators=(",", ":")))
    failures = list(object_errors)
    if property_count > 5000:
        failures.append(f"properties={property_count} exceeds 5000")
    if max_depth > 10:
        failures.append(f"nesting_depth={max_depth} exceeds 10")
    if schema_chars > 120000:
        failures.append(f"schema_characters={schema_chars} exceeds 120000")
    if enum_count > 1000:
        failures.append(f"enum_values={enum_count} exceeds 1000")
    if unsupported:
        failures.append("unsupported_keywords=" + ",".join(sorted(unsupported)))
    if failures:
        raise SchemaPreflightError("Structured Outputs schema preflight failed: " + "; ".join(failures))
    return {
        "properties": property_count,
        "nesting_depth": max_depth,
        "schema_characters": schema_chars,
        "enum_values": enum_count,
    }


def schema_fingerprint(model: type[BaseModel]) -> str:
    payload = json.dumps(model.model_json_schema(), sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def to_graph_dto(value: ExtractionWireModel) -> RawGraphExtraction:
    """Map a validated provider object into the application's graph DTOs."""
    return RawGraphExtraction(
        nodes=[
            Node(
                id=node.id,
                label=node.label,
                type=str(node.type.value if isinstance(node.type, Enum) else node.type),
                forensic_summary=node.forensic_summary,
                event_date=node.event_date,
            )
            for node in value.nodes
        ],
        edges=[
            Edge(
                source=edge.source,
                target=edge.target,
                relation=str(edge.relation.value if isinstance(edge.relation, Enum) else edge.relation),
                description=edge.description,
                confidence=str(edge.confidence.value if isinstance(edge.confidence, Enum) else edge.confidence),
                confidence_score=edge.confidence_score,
            )
            for edge in value.edges
        ],
    )
