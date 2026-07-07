import logging
import asyncio
from typing import List, Dict, Any
from src.graph.cosmos_manager import CosmosGremlinManager

logger = logging.getLogger(__name__)

class OntologyEngine:
    """
    Core reasoning engine that evaluates graph patterns against legal ontology rules.
    Refactored to push down evaluations directly to the Azure Cosmos DB Gremlin API.
    """

    def __init__(self, schema_config: Dict[str, Any]):
        """
        Initializes the OntologyEngine.
        """
        self.schema = schema_config
        self.rules = self.schema.get("ontology_engine", {}).get("reasoning_rules", [])
        if not self.rules:
            self.rules = self.schema.get("reasoning_rules", [])

    def map_query(self, query: str) -> List[Dict[str, str]]:
        """
        [DEPRECATED] Maps abstract terms in the query to schema-defined types/relations.
        """
        raise NotImplementedError("This function is deprecated and its body has been removed.")

    def evaluate_graph_data(
        self, matched_nodes: List[Dict[str, Any]], matched_edges: List[Dict[str, Any]]
    ) -> List[str]:
        """
        [DEPRECATED] In-memory AST evaluation of graph rules.
        Use evaluate_pushdown for direct Cosmos DB pushdown.
        """
        logger.warning("evaluate_graph_data is deprecated. Use evaluate_pushdown instead.")
        return []

    async def evaluate_pushdown(self, cosmos_mgr: CosmosGremlinManager, doc_id: str) -> List[str]:
        """
        Evaluates the loaded ontology rules directly in Cosmos DB using optimized Gremlin pushdowns.

        Args:
            cosmos_mgr: CosmosGremlinManager connection helper.
            doc_id: Partition context for isolation.

        Returns:
            List of 'LEGAL FLAG' strings triggered by the reasoning rules.
        """
        if not cosmos_mgr:
            logger.warning("[OntologyEngine] CosmosGremlinManager not provided. Skipping pushdown rules.")
            return []

        pk = doc_id or "global"
        logger.info(f"[OntologyEngine] Executing pushdown rule evaluation on partition '{pk}'...")

        # Helper to extract value safely from project output
        def val(item, key, default="unknown"):
            if not isinstance(item, dict):
                return default
            res = item.get(key)
            if isinstance(res, list) and len(res) > 0:
                return res[0]
            return res or default

        # Define specialized tasks for each rule
        async def check_hearsay():
            query = (
                "g.E().has('partitionKey', pk).has('epistemic_state', 'HEARSAY')"
                ".project('id', 'relation', 'source', 'target')"
                ".by('id').by(label).by(outV().values('label').fold()).by(inV().values('label').fold())"
                ".limit(50)"
            )
            try:
                results = await cosmos_mgr.execute_query(query, {"pk": pk})
                flags = []
                for item in results:
                    rel = val(item, "relation")
                    src = val(item, "source", ["unknown"])
                    tgt = val(item, "target", ["unknown"])
                    src_label = src[0] if isinstance(src, list) and src else src
                    tgt_label = tgt[0] if isinstance(tgt, list) and tgt else tgt
                    flags.append(
                        f"🚨 LEGAL FLAG: Rule 'hearsay_inadmissibility' triggered on relation '{rel}' "
                        f"between '{src_label}' and '{tgt_label}'. Action: REQUIRES_LEGAL_REVIEW."
                    )
                return flags
            except Exception as e:
                logger.error(f"[OntologyEngine] hearsay_inadmissibility rule check failed: {e}")
                return []

        async def check_accomplice_corroboration():
            query = (
                "g.E().has('partitionKey', pk).hasLabel('TESTIFIED_ABOUT')"
                ".where(outV().has('type', 'Accomplice'))"
                ".project('id', 'relation', 'source', 'target')"
                ".by('id').by(label).by(outV().values('label').fold()).by(inV().values('label').fold())"
                ".limit(50)"
            )
            try:
                results = await cosmos_mgr.execute_query(query, {"pk": pk})
                flags = []
                for item in results:
                    rel = val(item, "relation")
                    src = val(item, "source", ["unknown"])
                    tgt = val(item, "target", ["unknown"])
                    src_label = src[0] if isinstance(src, list) and src else src
                    tgt_label = tgt[0] if isinstance(tgt, list) and tgt else tgt
                    flags.append(
                        f"🚨 LEGAL FLAG: Rule 'accomplice_corroboration_requirement' triggered on relation '{rel}' "
                        f"between '{src_label}' and '{tgt_label}'. Action: MISSING_CORROBORATION_EVIDENCE."
                    )
                return flags
            except Exception as e:
                logger.error(f"[OntologyEngine] accomplice_corroboration rule check failed: {e}")
                return []

        async def check_state_witness_corroboration():
            query = (
                "g.E().has('partitionKey', pk).has('epistemic_state', 'STATE_WITNESS_TESTIMONY')"
                ".project('id', 'relation', 'source', 'target')"
                ".by('id').by(label).by(outV().values('label').fold()).by(inV().values('label').fold())"
                ".limit(50)"
            )
            try:
                results = await cosmos_mgr.execute_query(query, {"pk": pk})
                flags = []
                for item in results:
                    rel = val(item, "relation")
                    src = val(item, "source", ["unknown"])
                    tgt = val(item, "target", ["unknown"])
                    src_label = src[0] if isinstance(src, list) and src else src
                    tgt_label = tgt[0] if isinstance(tgt, list) and tgt else tgt
                    flags.append(
                        f"🚨 LEGAL FLAG: Rule 'state_witness_corroboration' triggered on relation '{rel}' "
                        f"between '{src_label}' and '{tgt_label}'. Action: NEEDS_ADDITIONAL_EVIDENCE."
                    )
                return flags
            except Exception as e:
                logger.error(f"[OntologyEngine] state_witness_corroboration rule check failed: {e}")
                return []

        async def check_mens_rea():
            query = (
                "g.V().has('partitionKey', pk).has('type', 'Offense_Type').has('label', 'שוחד והפרת אמונים')"
                ".where(__.inE().outV().has('type', 'Mens_Rea').count().is(0))"
                ".project('id', 'label')"
                ".by('id').by('label')"
                ".limit(50)"
            )
            try:
                results = await cosmos_mgr.execute_query(query, {"pk": pk})
                flags = []
                for item in results:
                    lbl = val(item, "label")
                    flags.append(
                        f"🚨 LEGAL FLAG: Rule 'mens_rea_check' triggered on node '{lbl}'. "
                        f"Action: INCOMPLETE_OFFENSE_ELEMENTS."
                    )
                return flags
            except Exception as e:
                logger.error(f"[OntologyEngine] mens_rea rule check failed: {e}")
                return []

        async def check_conflict_of_interest():
            query = (
                "g.E().has('partitionKey', pk).hasLabel('CONFLICT_OF_INTEREST')"
                ".project('id', 'relation', 'source', 'target')"
                ".by('id').by(label).by(outV().values('label').fold()).by(inV().values('label').fold())"
                ".limit(50)"
            )
            try:
                results = await cosmos_mgr.execute_query(query, {"pk": pk})
                flags = []
                for item in results:
                    rel = val(item, "relation")
                    src = val(item, "source", ["unknown"])
                    tgt = val(item, "target", ["unknown"])
                    src_label = src[0] if isinstance(src, list) and src else src
                    tgt_label = tgt[0] if isinstance(tgt, list) and tgt else tgt
                    flags.append(
                        f"🚨 LEGAL FLAG: Rule 'conflict_of_interest_alert' triggered on relation '{rel}' "
                        f"between '{src_label}' and '{tgt_label}'. Action: POTENTIAL_BRIBERY_OR_FRAUD."
                    )
                return flags
            except Exception as e:
                logger.error(f"[OntologyEngine] conflict_of_interest rule check failed: {e}")
                return []

        async def check_investigative_corroboration():
            query = (
                "g.V().has('partitionKey', pk).has('type', 'Offense_Type')"
                ".project('id', 'label')"
                ".by('id').by('label')"
                ".limit(50)"
            )
            try:
                results = await cosmos_mgr.execute_query(query, {"pk": pk})
                flags = []
                for item in results:
                    lbl = val(item, "label")
                    flags.append(
                        f"🚨 LEGAL FLAG: Rule 'investigative_corroboration' triggered on node '{lbl}'. "
                        f"Action: REQUIRES_EVIDENCE_ANCHOR."
                    )
                return flags
            except Exception as e:
                logger.error(f"[OntologyEngine] investigative_corroboration rule check failed: {e}")
                return []

        async def check_hearsay_offense():
            query = (
                "g.E().has('partitionKey', pk).has('epistemic_state', 'HEARSAY')"
                ".where(inV().has('type', 'Offense_Type'))"
                ".project('id', 'relation', 'source', 'target')"
                ".by('id').by(label).by(outV().values('label').fold()).by(inV().values('label').fold())"
                ".limit(50)"
            )
            try:
                results = await cosmos_mgr.execute_query(query, {"pk": pk})
                flags = []
                for item in results:
                    rel = val(item, "relation")
                    src = val(item, "source", ["unknown"])
                    tgt = val(item, "target", ["unknown"])
                    src_label = src[0] if isinstance(src, list) and src else src
                    tgt_label = tgt[0] if isinstance(tgt, list) and tgt else tgt
                    flags.append(
                        f"🚨 LEGAL FLAG: Rule 'hearsay_offense_rule' triggered on relation '{rel}' "
                        f"between '{src_label}' and '{tgt_label}'. Action: INVALID_EVIDENCE_TYPE."
                    )
                return flags
            except Exception as e:
                logger.error(f"[OntologyEngine] hearsay_offense rule check failed: {e}")
                return []

        # Run checks in parallel
        check_tasks = [
            check_hearsay(),
            check_accomplice_corroboration(),
            check_state_witness_corroboration(),
            check_mens_rea(),
            check_conflict_of_interest(),
            check_investigative_corroboration(),
            check_hearsay_offense()
        ]

        all_results = await asyncio.gather(*check_tasks)
        flattened_flags = [flag for sublist in all_results for flag in sublist]

        logger.info(f"[OntologyEngine] Pushdown checks complete. Triggered {len(flattened_flags)} flags.")
        return list(set(flattened_flags))
