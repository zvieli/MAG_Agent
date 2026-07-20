import logging
import re
import json
from typing import Any, Dict, List
from src.extraction.extraction_engine import _build_azure_client
from src.utils.config import settings

logger = logging.getLogger(__name__)


class GraphQueryBuilder:
    """
    Translates natural language questions into Gremlin traversals for Azure Cosmos DB.
    Enforces schema validity, partition isolation (doc_id), Canonical English labels,
    and performance safety limits.
    """

    def __init__(self, schema_config: Dict[str, Any], doc_id: str):
        """
        Initializes the GraphQueryBuilder.

        Args:
            schema_config: Loaded ontology schema dictionary.
            doc_id: Active investigation document ID context for partition isolation.
        """
        self.schema = schema_config
        self.doc_id = doc_id
        self.client = _build_azure_client()
        self.model_name = settings.graphify_agent_model or "gpt-4o"

        # Extract allowed entity and relation types from schema for LLM context
        self.entity_types = list(self.schema.get("entity_types", {}).keys())
        self.relation_types = list(self.schema.get("relations", {}).keys())

    async def build_gremlin_query(self, question: str, safety_limit: int = 50) -> str:
        """
        Calls the LLM to translate a natural language question into a Gremlin query.

        Args:
            question: Natural language question.
            safety_limit: The maximum number of elements to return in the traversal.

        Returns:
            Gremlin query string.
        """
        from src.utils.config_manager import config_manager
        template = await config_manager.get_config("gremlin_translation_system.md")
        system_prompt = template.format(
            doc_id=self.doc_id,
            safety_limit=safety_limit,
            entity_types=json.dumps(self.entity_types, ensure_ascii=False, indent=2),
            relation_types=json.dumps(self.relation_types, ensure_ascii=False, indent=2)
        )

        user_prompt = f"Natural Language Question: {question}"

        try:
            logger.info(f"[GraphQueryBuilder] Translating question to Gremlin: '{question}' using model {self.model_name}")
            response = await self.client.chat.completions.create(
                model=self.model_name,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                temperature=0.0,
                max_tokens=300,
                timeout=settings.llm_request_timeout_seconds,
            )
            query = response.choices[0].message.content.strip()

            # Clean markdown code blocks if the model ignored instructions
            if query.startswith("```"):
                # strip out opening ```gremlin / ```groovy / ``` and closing ```
                query = re.sub(r"^```[a-zA-Z]*\n?", "", query)
                query = re.sub(r"\n?```$", "", query).strip()

            # Self-correction: Ensure partition key isolation is enforced if the LLM missed it
            if f"'{self.doc_id}'" not in query and f'"{self.doc_id}"' not in query:
                logger.warning(f"[GraphQueryBuilder] Generated query missing partition isolation. Appending filter. Raw query: {query}")
                if "g.V()" in query:
                    query = query.replace("g.V()", f"g.V().has('pk', '{self.doc_id}')")
                else:
                    query = f"g.V().has('pk', '{self.doc_id}')." + query

            # Self-correction: Ensure limit(...) is present
            if ".limit(" not in query:
                logger.warning(f"[GraphQueryBuilder] Generated query missing safety limit. Appending .limit({safety_limit}). Raw query: {query}")
                query = f"{query}.limit({safety_limit})"

            logger.info(f"[GraphQueryBuilder] Generated Gremlin traversal: {query}")
            return query

        except Exception as e:
            logger.error(f"[GraphQueryBuilder] Gremlin translation failed: {e}", exc_info=True)
            # Safe default fallback query that returns suspects using the correct type property filter
            return f"g.V().has('pk', '{self.doc_id}').has('type', 'SUSPECT').limit({safety_limit})"

    def format_results(self, raw_results: List[Any]) -> str:
        """
        Parses and formats raw Cosmos DB Gremlin query results into clean, readable text.

        Args:
            raw_results: List of raw items returned by Cosmos DB.

        Returns:
            Human-readable string representation of the results.
        """
        if not raw_results:
            return "[QUERY_RESULT_EMPTY]"

        formatted_lines = []

        for idx, item in enumerate(raw_results):
            if not isinstance(item, dict):
                formatted_lines.append(f"- {str(item)}")
                continue

            item_type = item.get("type")
            
            # Case 1: Vertex / Node
            if item_type == "vertex" or ("id" in item and "label" in item and "properties" in item and "outV" not in item):
                node_id = item.get("id")
                properties = item.get("properties", {})
                
                # Extract entity type (e.g. 'SUSPECT') from 'type' property
                node_type = "Entity"
                if "type" in properties:
                    type_val = properties["type"]
                    if isinstance(type_val, list) and len(type_val) > 0 and isinstance(type_val[0], dict):
                        node_type = type_val[0].get("value", "Entity")
                    elif isinstance(type_val, str):
                        node_type = type_val
                elif "type" in item:
                    node_type = item["type"]
                
                # Display name (Hebrew display name is stored in top-level 'label' key of the vertex)
                display_name = item.get("label") or node_id
                
                # Check for standard properties overrides
                if "label" in properties and isinstance(properties["label"], list) and len(properties["label"]) > 0:
                    display_name = properties["label"][0].get("value", display_name)
                elif "name" in properties and isinstance(properties["name"], list) and len(properties["name"]) > 0:
                    display_name = properties["name"][0].get("value", display_name)

                description = ""
                if "description" in properties and isinstance(properties["description"], list) and len(properties["description"]) > 0:
                    description = properties["description"][0].get("value", "")
                elif "meta_forensic_summary" in properties and isinstance(properties["meta_forensic_summary"], list) and len(properties["meta_forensic_summary"]) > 0:
                    description = properties["meta_forensic_summary"][0].get("value", "")

                additional_props = []
                def _get_val(prop_name):
                    val_list = properties.get(prop_name)
                    if isinstance(val_list, list) and len(val_list) > 0:
                        first = val_list[0]
                        if isinstance(first, dict) and "value" in first:
                            return first.get("value")
                        return first
                    return val_list

                alias_cnt = _get_val("alias_count")
                alias_prev = _get_val("alias_preview")
                if alias_cnt is not None:
                    additional_props.append(f"Alias Count: {alias_cnt}")
                if alias_prev:
                    additional_props.append(f"Alias Preview: {alias_prev}")

                community_id = _get_val("community")
                if community_id:
                    additional_props.append(f"Community: {community_id}")

                source_loc = _get_val("source_location")
                if source_loc:
                    additional_props.append(f"Source Location: {source_loc}")

                source_file = _get_val("pk") or _get_val("partitionKey") or _get_val("source_file")
                if source_file:
                    additional_props.append(f"Source File: {source_file}")

                extra_str = f" | {' | '.join(additional_props)}" if additional_props else ""
                formatted_lines.append(f"Node [{node_id}] | Type: {node_type} | Name: \"{display_name}\" | Description: {description}{extra_str}")

            # Case 2: Edge / Relation
            elif item_type == "edge" or ("outV" in item and "inV" in item and "label" in item):
                edge_id = item.get("id")
                relation = item.get("label", "RELATED_TO")
                source = item.get("outV")
                target = item.get("inV")
                properties = item.get("properties", {})
                
                description = properties.get("description", "")
                if not description and "meta_description" in properties:
                    description = properties["meta_description"]

                def _get_edge_val(prop_name):
                    val = properties.get(prop_name)
                    if isinstance(val, list) and len(val) > 0:
                        first = val[0]
                        if isinstance(first, dict) and "value" in first:
                            return first.get("value")
                        return first
                    return val

                edge_additional_props = []
                confidence = _get_edge_val("confidence") or _get_edge_val("confidence_score")
                if confidence is not None:
                    edge_additional_props.append(f"Confidence: {confidence}")

                rel_history_cnt = _get_edge_val("relation_history_count")
                rel_history_prev = _get_edge_val("relation_history_preview")
                if rel_history_cnt is not None:
                    edge_additional_props.append(f"Relation History Count: {rel_history_cnt}")
                if rel_history_prev:
                    edge_additional_props.append(f"Relation History Preview: {rel_history_prev}")

                source_files_cnt = _get_edge_val("source_files_count")
                source_files_prev = _get_edge_val("source_files_preview")
                if source_files_cnt is not None:
                    edge_additional_props.append(f"Source Files Count: {source_files_cnt}")
                if source_files_prev:
                    edge_additional_props.append(f"Source Files Preview: {source_files_prev}")

                edge_source_loc = _get_edge_val("source_location")
                if edge_source_loc:
                    edge_additional_props.append(f"Source Location: {edge_source_loc}")

                edge_extra_str = f" | {' | '.join(edge_additional_props)}" if edge_additional_props else ""
                formatted_lines.append(f"Relation: [{source}] --[{relation}]--> [{target}] | Details: {description}{edge_extra_str}")

            # Case 3: Project / map output
            else:
                # Try parsing as general dictionary
                formatted_item = json.dumps(item, ensure_ascii=False)
                formatted_lines.append(f"- {formatted_item}")

        return "\n".join(formatted_lines)
