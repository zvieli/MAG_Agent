import logging
from typing import Any, Dict
from src.utils.bidi_utils import fix_text
from src.models.models import RawGraphExtraction, normalize_relation_label


class GraphNormalizer:
    """
    Responsible for mapping unknown or non-standard entity types and relations
    to schema-approved values before strict validation.

    Attributes:
        schema_config (Any): The configuration object containing allowed entity
            and relation types.
        allowed_node_types (Set[str]): A set of allowed entity types.
        allowed_relations (Set[str]): A set of allowed relation types.
        role_map (Dict[str, List[str]]): A mapping of canonical types to lists
            of terms.
        inverted_role_map (Dict[str, str]): A mapping of terms to canonical types
            for quick lookup.
    """

    def __init__(self, schema_config: Any):
        """
        Initializes GraphNormalizer with schema configuration.

        Args:
            schema_config (Any): The configuration object containing entity and
                relation type definitions.
        """
        self.schema_config = schema_config
        self.allowed_node_types = set(schema_config.entity_types)
        self.allowed_relations = set(schema_config.relation_types.keys())
        self.role_map = getattr(schema_config, "legal_role_map", {})
        self.inverted_role_map = self._invert_role_map()

    def _invert_role_map(self) -> Dict[str, str]:
        """
        Inverts the role_map for easy term-to-type lookup.

        Returns:
            Dict[str, str]: A dictionary mapping Hebrew or non-standard terms
                (lowercase) to their canonical English types.
        """
        inverted = {}
        if not isinstance(self.role_map, dict):
            return inverted
        for canon_type, terms in self.role_map.items():
            if isinstance(terms, list):
                for term in terms:
                    inverted[str(term).strip().lower()] = canon_type
        return inverted

    def normalize_extraction(self, raw: RawGraphExtraction) -> Dict[str, Any]:
        """
        Standardizes RawGraphExtraction into a dictionary compatible with
        GraphExtraction.

        Maps raw node types and relation labels to canonical forms defined in the
        schema. Handles unknown types by mapping them to default values ('EVENT'
        for nodes, 'RELATED_TO' for edges).

        Args:
            raw (RawGraphExtraction): The raw graph extraction object containing
                nodes, edges, claims, and evidence.

        Returns:
            Dict[str, Any]: A dictionary representing the standardized graph
                extraction, ready for further processing or validation.
        """
        nodes = []
        for n in raw.nodes:
            node_dict = n.model_dump(by_alias=True)
            raw_type = node_dict.get("type", "")

            # 1. Role Map Check (Priority)
            norm_type = self.inverted_role_map.get(str(raw_type).lower())

            # 2. Direct Match Check
            if not norm_type and raw_type in self.allowed_node_types:
                norm_type = raw_type

            # 3. Fallback
            if not norm_type:
                # Be descriptive about what we are losing
                logging.warning(
                    fix_text(
                        f"[Normalization] Node type '{raw_type}' unknown for '{node_dict.get('label')}'. Mapping to 'EVENT'."
                    )
                )
                norm_type = "EVENT"

            node_dict["type"] = norm_type
            nodes.append(node_dict)

        edges = []
        for e in raw.edges:
            edge_dict = e.model_dump(by_alias=True)
            raw_rel = str(edge_dict.get("relation", "")).strip()

            # Fix verbose descriptive relation corruption
            # If the relation looks like a sentence or is very long, it's likely a description leak
            if len(raw_rel) > 40 or " " in raw_rel:
                raw_rel = "RELATED_TO"

            # Data-driven mappings from schema_config.json
            mappings = getattr(self.schema_config, "relation_mappings", {})
            # Clean raw_rel for more robust mapping (strip common extra chars)
            clean_rel = raw_rel.strip(" ()\"'").upper()
            if clean_rel in mappings:
                raw_rel = mappings[clean_rel]
            elif raw_rel.strip(" ()\"'") in mappings:
                raw_rel = mappings[raw_rel.strip(" ()\"'")]

            # 1. Normalize relation label (handles aliases and formatting)
            norm_rel = normalize_relation_label(raw_rel)
            # 2. Check if allowed
            if norm_rel not in self.allowed_relations:
                logging.warning(
                    fix_text(
                        f"[Normalization] Relation type '{raw_rel}' ('{norm_rel}') unknown. Mapping to 'RELATED_TO'."
                    )
                )

                # Preserve raw relation in metadata for forensic richness
                if "metadata" not in edge_dict or edge_dict["metadata"] is None:
                    edge_dict["metadata"] = {}

                # Use uppercase cleaned name for metadata
                edge_dict["metadata"]["raw_relation"] = (
                    raw_rel.strip(" ()\"'").upper().replace(" ", "_")
                )
                norm_rel = "RELATED_TO"

            edge_dict["relation"] = norm_rel
            edges.append(edge_dict)

        return {
            "nodes": nodes,
            "edges": edges,
            "claims": raw.claims,
            "evidence_fragments": raw.evidence_fragments,
        }
