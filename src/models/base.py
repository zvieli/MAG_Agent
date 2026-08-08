"""Foundational data models for system configuration and investigative schemas."""

from typing import Any, List, Dict, Optional
from pydantic import BaseModel, Field, model_validator



class GraphAnalysisConfig(BaseModel):
    """Configuration for Knowledge Graph structural analysis and community detection.

    Attributes:
        god_node_threshold: Degree threshold above which a node is considered a 'God Node' (hub).
        max_hops_default: Default search depth for graph traversals.
        min_community_size: Minimum number of nodes required to form a distinct community.
        enable_llm_community_summaries: Whether to call the LLM for community text summaries.
        llm_summary_min_size: Minimum node count to trigger an LLM-generated community summary.
        chunk_overlap_sentences: Number of sentences to overlap between text chunks for connectivity.
        community_label_stopwords: List of common words to ignore when generating community labels.
    """

    god_node_threshold: int = 50
    max_hops_default: int = 3
    min_community_size: int = 3
    enable_llm_community_summaries: bool = False
    llm_summary_min_size: int = 10
    chunk_overlap_sentences: int = 3
    community_label_stopwords: List[str] = Field(
        default_factory=lambda: [
            "the",
            "and",
            "of",
            "a",
            "an",
            "in",
            "to",
            "for",
            "with",
            "on",
            "at",
            "by",
            "from",
            "is",
            "was",
            "are",
        ]
    )
    hebrew_stopwords: List[str] = Field(
        default_factory=lambda: [
            "מי", "הם", "הן", "הוא", "היא", "מה", "של", "את", "על", "עם", "פי", "זה", "אלה",
            "ללא", "כמו", "אשר", "כי", "אם", "גם", "בפי", "להן", "להם", "מתי", "למה", "איך",
            "איפה", "אילו", "שלנו", "להיות", "אחד", "אחת", "חלק", "שלהם", "שלהן", "שלך"
        ]
    )


class TimelineNarrativeConfig(BaseModel):
    """formatting rules for generating chronological investigative timelines.

    Attributes:
        causal_format: f-string template for causal relationships.
        generic_format: f-string template for non-causal relationships.
        inferred_marker: Suffix for relations logically inferred by the engine.
        red_flag_marker: UI/Text marker for suspicious temporal proximities.
    """

    causal_format: str = "{source} → {target} ({relation})"
    generic_format: str = "{source} — {target}"
    inferred_marker: str = " [inferred]"
    red_flag_marker: str = " [🚩 RED FLAG: TEMPORAL PROXIMITY]"


class ResolutionThresholds(BaseModel):
    """Similarity and logic thresholds for entity resolution and deduplication.

    Attributes:
        person: Similarity threshold (0-1) for merging Person entities.
        organization: Similarity threshold for merging Organization entities.
        other: Default similarity threshold for other entity types.
        structural_boost_sim: Minimum similarity to trigger a structural connectivity boost.
        structural_baseline_sim: Baseline similarity required before structural checks apply.
        disambiguation_high: Threshold for 'safe' automatic merges without human review.
        min_token_length: Minimum characters for a token to be considered during matching.
        min_fuzzy_length: Minimum string length required for fuzzy matching logic.
        fuzzy_match_threshold: Threshold for fuzzy string comparison.
        witness_collision_threshold: Strictness for preventing incorrect witness merges.
        jaccard_overlap_threshold: Required token overlap ratio for set-based similarity.
        event_merge_threshold: Threshold for merging time-bound events.
        law_merge_threshold: Threshold for merging legal/statutory references.
        evidence_location_match_threshold: Threshold for matching based on source location.
        evidence_match_threshold: Threshold for matching based on evidence content.
        canonical_dedupe_threshold: Threshold for merging into established canonical IDs.
    """

    person: float = 0.85
    organization: float = 0.85
    other: float = 0.85
    structural_boost_sim: float = 0.7
    structural_baseline_sim: float = 0.6
    disambiguation_high: float = 0.95
    min_token_length: int = 3
    min_fuzzy_length: int = 5
    fuzzy_match_threshold: float = 0.85
    witness_collision_threshold: float = 0.95
    jaccard_overlap_threshold: float = 0.55
    event_merge_threshold: float = 0.85
    law_merge_threshold: float = 0.9
    evidence_location_match_threshold: float = 0.6
    evidence_match_threshold: float = 0.7
    canonical_dedupe_threshold: float = 0.85


class AnalysisConfig(BaseModel):
    """Configuration for query analysis, citations, and Hebrew linguistic processing.

    Attributes:
        hebrew_prefixes: Regex pattern for common Hebrew prefixes (ו, ל, ה, מ, ב).
        temporal_keywords: Keywords indicating a chronological or timeline-based query.
        citation_patterns: Regexes for parsing source citations in LLM responses.
        not_found_markers: Keywords indicating the LLM could not find an answer.
    """

    hebrew_prefixes: str = "[בולהמ]?"
    temporal_keywords: List[str] = Field(
        default_factory=lambda: [
            "ציר זמן",
            'לו"ז',
            "מועדי",
            "לוח זמנים",
            "כרונולוג",
            "timeline",
        ]
    )
    citation_patterns: Dict[str, str] = Field(
        default_factory=lambda: {
            "outer": r"\[(?:Source|מקור):\s*([^\]]+)\]",
            "inner_ids": r"(\b[a-fA-F0-9]{64}(?:\.txt)?\b|\bchunk_\d+\b|\bChunk\s+\d+\b)",
        }
    )
    not_found_markers: List[str] = Field(
        default_factory=lambda: [
            "אין מספיק מידע",
            "לא מצאתי",
            "לא מצאה",
            "No sufficient information",
        ]
    )


class SchemaConfigModel(BaseModel):
    """The root configuration model defining the investigation's ontology and logic.

    This class centralizes all settings for extraction, normalization, and reasoning.
    """

    schema_version: str = "2.1"
    case_id: str = "unknown"
    description: str = ""
    human_entity_types: List[str] = Field(default_factory=list)
    organization_entity_types: List[str] = Field(default_factory=list)
    event_entity_types: List[str] = Field(default_factory=list)
    statute_entity_types: List[str] = Field(default_factory=list)
    evidence_entity_types: List[str] = Field(default_factory=list)
    causality_relations: List[str] = Field(default_factory=list)
    exchange_relations: List[str] = Field(default_factory=list)
    cause_relations: List[str] = Field(default_factory=list)
    co_occurrence_relations: List[str] = Field(default_factory=list)
    entity_types: Dict[str, str] = Field(default_factory=dict)
    person_label_prefixes: List[str] = Field(default_factory=list)
    legal_role_map: Dict[str, List[str]] = Field(default_factory=dict)
    semantic_class_markers: Dict[str, List[str]] = Field(default_factory=dict)
    semantic_class_patterns: Dict[str, List[str]] = Field(default_factory=dict)
    semantic_class_type_map: Dict[str, str] = Field(default_factory=dict)
    semantic_class_discriminator_patterns: Dict[str, List[str]] = Field(
        default_factory=dict
    )
    event_generic_id_tokens: List[str] = Field(default_factory=list)

    # Domain configuration for resolution and analysis
    date_types: List[str] = Field(default_factory=list)

    confidence_levels: Dict[str, str] = Field(default_factory=dict)
    entity_type_display_labels: Dict[str, str] = Field(default_factory=dict)
    confidence_aliases: Dict[str, str] = Field(default_factory=dict)
    confidence_level_scores: Dict[str, float] = Field(default_factory=dict)
    confidence_ranks: Dict[str, int] = Field(default_factory=dict)
    confidence_display_labels: Dict[str, str] = Field(default_factory=dict)
    relation_types: Dict[str, str] = Field(default_factory=dict)
    relation_mappings: Dict[str, str] = Field(default_factory=dict)
    blocked_relation_labels: List[str] = Field(default_factory=list)
    min_confidence_score: float = 0.6
    hard_drop_confidence_score: float = 0.5
    deterministic_patterns: List[str] = Field(default_factory=list)
    collision_prevention_patterns: List[str] = Field(
        default_factory=lambda: [
            r"(?:נאשם|defendant|נא'|חשוד|עד|witness|suspect)\s*(\d+)"
        ]
    )
    representative_patterns: List[str] = Field(
        default_factory=lambda: [r"^(?:נציג|דובר|מטעם|עורך דין מטעם|עורכת דין מטעם)"]
    )
    default_confidence_label: str = "DIRECT"
    special_thresholds: Dict[str, float] = Field(
        default_factory=lambda: {"causality": 0.55}
    )
    synthetic_anchor_config: Dict[str, Any] = Field(
        default_factory=lambda: {
            "label": "מקרה משותף",
            "type": "Context",
            "description": "Auto-generated context anchor for shared witness co-occurrence",
            "relation": "PART_OF_SAME_CONTEXT",
        }
    )
    regulatory_action_linking_config: Dict[str, Any] = Field(
        default_factory=lambda: {
            "action_type": "RegulatoryAction",
            "relation_label": "הנחיה לביצוע",
            "skip_keyword": "עד",
        }
    )
    language_prefixes: List[str] = Field(default_factory=list)
    analysis_config: AnalysisConfig = Field(default_factory=AnalysisConfig)
    resolution_thresholds: ResolutionThresholds = Field(
        default_factory=ResolutionThresholds
    )
    extraction_guidelines: List[str] = Field(default_factory=list)
    graph_analysis: GraphAnalysisConfig = Field(default_factory=GraphAnalysisConfig)
    timeline_narrative: TimelineNarrativeConfig = Field(
        default_factory=TimelineNarrativeConfig
    )
    protocol_completeness: Dict[str, Any] = Field(default_factory=dict)
    ontology_engine: Dict[str, Any] = Field(default_factory=dict)
    relations: Optional[Dict[str, Any]] = Field(default=None)

    @model_validator(mode="before")
    @classmethod
    def populate_relations_from_ontology(cls, data: Any) -> Any:
        if isinstance(data, dict) and "relations" in data:
            relations = data["relations"]
            relation_types = {}
            causality_relations = []
            exchange_relations = []
            cause_relations = []
            co_occurrence_relations = []
            
            for rel_name, rel_info in relations.items():
                if isinstance(rel_info, dict):
                    desc = rel_info.get("description", "")
                    tags = rel_info.get("tags", [])
                else:
                    desc = str(rel_info)
                    tags = []
                
                relation_types[rel_name] = desc
                if "causality" in tags:
                    causality_relations.append(rel_name)
                if "exchange" in tags:
                    exchange_relations.append(rel_name)
                if "cause" in tags:
                    cause_relations.append(rel_name)
                if "co_occurrence" in tags:
                    co_occurrence_relations.append(rel_name)
            
            data["relation_types"] = relation_types
            data["causality_relations"] = causality_relations
            data["exchange_relations"] = exchange_relations
            data["cause_relations"] = cause_relations
            data["co_occurrence_relations"] = co_occurrence_relations
        return data



class EpistemicState:
    """Constants representing the degree of certainty or source of a claim."""

    VERIFIED = "verified"
    TESTIMONY = "testimony"
    ALLEGATION = "allegation"
    INFERENCE = "inference"
    SPECULATION = "speculation"
    CONTRADICTED = "contradicted"
    DISPUTED = "disputed"


class EntityMergeGroup(BaseModel):
    """A group of entity IDs that have been identified as representing the same actor.

    Attributes:
        canonical_id: The ID selected as the primary/canonical representation.
        alias_ids: List of secondary IDs to be merged into the canonical ID.
        canonical_label: The preferred human-readable label for the group.
        reason: The logic or threshold that triggered the merge.
    """

    canonical_id: str
    alias_ids: List[str] = Field(default_factory=list)
    canonical_label: Optional[str] = None
    reason: str = Field(default="")


class EntityResolutionPlan(BaseModel):
    """A collection of merge groups representing a full deduplication pass.

    Attributes:
        merge_groups: List of groups to be processed by the Graph Manager.
    """

    merge_groups: List[EntityMergeGroup] = Field(default_factory=list)
