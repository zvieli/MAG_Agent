import json
import logging
from typing import Any, Optional
from pydantic import Field, PrivateAttr, AliasChoices
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Global system settings and configuration management."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        validate_assignment=True,
    )
    azure_openai_api_key: Optional[str] = Field(
        default=None, alias="AZURE_OPENAI_API_KEY"
    )
    strict_cloud_runtime: bool = Field(default=False, alias="STRICT_CLOUD_RUNTIME")
    storage_type: Optional[str] = Field(default=None, alias="STORAGE_TYPE")
    azure_storage_container: str = Field(default="graphify-out", alias="AZURE_STORAGE_CONTAINER")

    openai_api_key: Optional[str] = Field(default=None, alias="OPENAI_API_KEY")
    azure_openai_endpoint: Optional[str] = Field(
        default=None, alias="AZURE_OPENAI_ENDPOINT"
    )
    openai_base_url: Optional[str] = Field(default=None, alias="OPENAI_BASE_URL")
    azure_storage_connection_string: Optional[str] = Field(
        default=None, alias="AZURE_STORAGE_CONNECTION_STRING"
    )
    use_azure_storage: bool = Field(default=False, alias="USE_AZURE_STORAGE")
    azure_document_intelligence_endpoint: Optional[str] = Field(
        default=None,
        alias="AZURE_DOCUMENT_INTELLIGENCE_ENDPOINT",
        validation_alias=AliasChoices(
            "AZURE_DOCUMENT_INTELLIGENCE_ENDPOINT", "AZURE_OCR_ENDPOINT"
        ),
    )
    azure_document_intelligence_key: Optional[str] = Field(
        default=None,
        alias="AZURE_DOCUMENT_INTELLIGENCE_KEY",
        validation_alias=AliasChoices(
            "AZURE_DOCUMENT_INTELLIGENCE_KEY", "AZURE_OCR_KEY"
        ),
    )
    azure_search_endpoint: Optional[str] = Field(
        default=None, alias="AZURE_SEARCH_ENDPOINT"
    )
    azure_search_key: Optional[str] = Field(default=None, alias="AZURE_SEARCH_KEY")
    use_azure_search: bool = Field(default=False, alias="USE_AZURE_SEARCH")
    azure_search_index: str = Field(
        default="graphify-chunks", alias="AZURE_SEARCH_INDEX"
    )
    azure_search_index_nodes: str = Field(
        default="graphify-nodes", alias="AZURE_SEARCH_INDEX_NODES"
    )
    azure_search_index_edges: str = Field(
        default="graphify-edges", alias="AZURE_SEARCH_INDEX_EDGES"
    )
    sharepoint_site_url: Optional[str] = Field(
        default=None, alias="SHAREPOINT_SITE_URL"
    )
    cosmos_gremlin_endpoint: Optional[str] = Field(
        default=None, alias="COSMOS_GREMLIN_ENDPOINT"
    )
    cosmos_gremlin_key: Optional[str] = Field(default=None, alias="COSMOS_GREMLIN_KEY")
    cosmos_database: str = Field(default="graphify-db", alias="COSMOS_DATABASE")
    cosmos_graph: str = Field(default="investigation-graph", alias="COSMOS_GRAPH")
    cosmos_batch_size: int = Field(default=5, alias="COSMOS_BATCH_SIZE")
    azure_openai_api_version: str = Field(
        default="2024-08-01-preview", alias="AZURE_OPENAI_API_VERSION"
    )
    openai_api_version: Optional[str] = Field(default=None, alias="OPENAI_API_VERSION")
    graphify_extraction_model: str = Field(
        default="gpt-4o", alias="GRAPHIFY_EXTRACTION_MODEL"
    )
    graphify_agent_model: str = Field(default="gpt-4o", alias="GRAPHIFY_AGENT_MODEL")
    azure_openai_embedding_deployment: str = Field(
        default="text-embedding-3-small", alias="AZURE_OPENAI_EMBEDDING_DEPLOYMENT"
    )
    azure_openai_embedding_model: str = Field(
        default="text-embedding-3-small", alias="AZURE_OPENAI_EMBEDDING_MODEL"
    )
    openai_embedding_model: str = Field(
        default="text-embedding-ada-002", alias="OPENAI_EMBEDDING_MODEL"
    )

    tool_description_overrides: dict[str, str] = Field(
        default_factory=dict, alias="TOOL_DESCRIPTION_OVERRIDES"
    )
    min_confidence_score: float = Field(default=0.6, alias="MIN_CONFIDENCE_SCORE")
    hard_drop_confidence_score: float = Field(
        default=0.5, alias="HARD_DROP_CONFIDENCE_SCORE"
    )
    extraction_max_tokens: int = Field(default=16384, alias="EXTRACTION_MAX_TOKENS")
    extraction_concurrency: int = Field(default=1, alias="EXTRACTION_CONCURRENCY")
    llm_temperature: float = Field(default=0.0, alias="LLM_TEMPERATURE")
    llm_fallback_temperature: float = Field(
        default=0.2, alias="LLM_FALLBACK_TEMPERATURE"
    )
    max_tool_calls_per_question: int = Field(default=30, alias="MAX_TOOL_CALLS")
    session_idle_timeout_seconds: int = Field(default=120, alias="SESSION_IDLE_TIMEOUT")
    llm_request_timeout_seconds: float = Field(
        default=60.0, alias="LLM_REQUEST_TIMEOUT_SECONDS"
    )
    document_intelligence_timeout_seconds: float = Field(
        default=60.0, alias="DOCUMENT_INTELLIGENCE_TIMEOUT_SECONDS"
    )
    azure_document_intelligence_model: str = Field(
        default="prebuilt-layout", alias="AZURE_DOCUMENT_INTELLIGENCE_MODEL"
    )
    document_intelligence_concurrency: int = Field(
        default=5, alias="DOCUMENT_INTELLIGENCE_CONCURRENCY"
    )
    document_intelligence_max_attempts: int = Field(
        default=5, alias="DOCUMENT_INTELLIGENCE_MAX_ATTEMPTS"
    )
    document_intelligence_backoff_base: float = Field(
        default=2.0, alias="DOCUMENT_INTELLIGENCE_BACKOFF_BASE"
    )
    document_intelligence_backoff_max: float = Field(
        default=60.0, alias="DOCUMENT_INTELLIGENCE_BACKOFF_MAX"
    )
    connectivity_probe_timeout_seconds: float = Field(
        default=10.0, alias="CONNECTIVITY_PROBE_TIMEOUT_SECONDS"
    )
    orchestrator_extract_batch_size: int = Field(
        default=4, alias="ORCHESTRATOR_EXTRACT_BATCH_SIZE"
    )
    copilot_wire_api: str = Field(default="completions", alias="COPILOT_WIRE_API")
    _cached_schema: Optional[Any] = PrivateAttr(default=None)

    async def get_schema(self) -> Any:
        """Loads and returns the investigative schema configuration dynamically."""
        if self._cached_schema:
            return self._cached_schema

        from src.utils.config_manager import config_manager
        from src.models.base import SchemaConfigModel

        try:
            data = await config_manager.get_json_config(
                "ontology_schema.json", category="schemas"
            )
            self._cached_schema = SchemaConfigModel.model_validate(data)
            return self._cached_schema
        except Exception as e:
            logging.error(
                f"[CONFIG] [SCHEMA_VALIDATE] [ERROR] | Metadata: Error={str(e)} | Message: Error loading schema"
            )
            return SchemaConfigModel()

    async def get_prompt(self, attr_name: str) -> str:
        """
        Dynamically loads and formats an LLM prompt.

        Args:
            attr_name: The internal name for the prompt.

        Returns:
            The formatted prompt string.
        """
        from src.utils.config_manager import config_manager

        prompt_mapping = {
            "extraction_prompt_full": "extraction_prompt.md",
            "extraction_schema_instructions_full": "extraction_schema_instructions.md",
            "extraction_json_format": "extraction_json_format.md",
            "extraction_base_instruction": "extraction_prompt.md",
            "extraction_system_message": "extraction_system_message.md",
            "query_routing_instructions": "query_routing_instructions_default.md",
            "query_groundedness_instruction": "query_groundedness_instruction.md",
            "query_no_apologies_rule": "query_no_apologies_rule.md",
            "community_summary_system": "community_summary_system.md",
            "community_summary_user": "community_summary_user.md",
            "agent_system_prompt": "agent_system_prompt.md",
            "protocol_completeness_system": "protocol_completeness_system.md",
        }

        filename = prompt_mapping.get(attr_name)
        if not filename:
            raise ValueError(f"Unknown prompt attribute: {attr_name}")

        raw_content = await config_manager.get_config(filename, category="prompts")

        schema = await self.get_schema()
        schema_dict = schema.model_dump() if hasattr(schema, "model_dump") else {}

        # Load auxiliary prompt instructions if present in templates
        groundedness_instr = ""
        no_apologies_rule = ""
        routing_instr = ""
        try:
            groundedness_instr = await config_manager.get_config("query_groundedness_instruction.md", category="prompts")
            no_apologies_rule = await config_manager.get_config("query_no_apologies_rule.md", category="prompts")
            routing_instr = await config_manager.get_config("query_routing_instructions_default.md", category="prompts")
        except Exception as e:
            logging.warning(f"[CONFIG] Failed to load auxiliary prompt files: {e}")

        format_map = {
            "human_entities": ", ".join(schema_dict.get("human_entity_types", [])),
            "organization_entities": ", ".join(
                schema_dict.get("organization_entity_types", [])
            ),
            "event_entities": ", ".join(schema_dict.get("event_entity_types", [])),
            "causality_relations": ", ".join(
                schema_dict.get("causality_relations", [])
            ),
            "entity_types": ", ".join(schema_dict.get("entity_types", [])),
            "entity_types_str": ", ".join(schema_dict.get("entity_types", [])),
            "relation_types": ", ".join(schema_dict.get("causality_relations", [])),
            "guidelines": "Follow forensic legal evidence rules strictly.",
            "legal_role_map": json.dumps(
                schema_dict.get("legal_role_map", {}), ensure_ascii=False
            ),
            "investigation_language": "Hebrew",
            "groundedness_instruction": groundedness_instr,
            "no_apologies_rule": no_apologies_rule,
            "routing_instructions": routing_instr,
        }

        formatted = raw_content.strip("\"'")
        for key, value in format_map.items():
            formatted = formatted.replace(f"{{{key}}}", value)

        return formatted


settings = Settings()
