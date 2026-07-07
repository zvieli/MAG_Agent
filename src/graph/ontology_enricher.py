import difflib
import networkx as nx
from typing import Dict, Any, Optional


class OntologyEnricher:
    """
    Enriches the knowledge graph with legal taxonomies and applies reasoning rules.
    """

    def __init__(self, ontology_data: Dict[str, Any]):
        """
        Initializes OntologyEnricher with the provided ontology data.

        Args:
            ontology_data (Dict[str, Any]): The ontology configuration dictionary.
        """
        self.ontology_data = ontology_data
        self.classes = self.ontology_data.get("ontology_engine", {}).get("classes", {})
        self.reasoning_rules = self.ontology_data.get("ontology_engine", {}).get(
            "reasoning_rules", []
        )

    def _clean_hebrew_prefixes(self, text: str) -> str:
        """
        Removes common Hebrew prefixes from a word to improve matching accuracy.

        Args:
            text (str): The Hebrew string to clean.

        Returns:
            str: The string with prefixes removed if it meets the length criteria.
        """
        if not text or len(text) <= 3:
            return text

        prefixes = ("ב", "ל", "ו", "ה", "מ", "כש", "ש")
        # Check for multi-character prefix 'כש' first
        if text.startswith("כש") and len(text) > 4:
            return text[2:]

        for p in prefixes:
            if p != "כש" and text.startswith(p):
                # Only strip if the remaining word is substantial
                if len(text[1:]) >= 3:
                    return text[1:]
        return text

    def _fuzzy_match(self, label: str, taxonomy: Dict[str, Any]) -> Optional[str]:
        """
        Performs fuzzy matching between a label and a taxonomy dictionary.

        Args:
            label (str): The entity label to match.
            taxonomy (Dict[str, Any]): A dictionary where keys are canonical
                taxonomy terms.

        Returns:
            Optional[str]: The best matching taxonomy key if the match ratio
                exceeds 0.6, otherwise None.
        """
        if not taxonomy:
            return None

        options = list(taxonomy.keys())
        if not options:
            return None

        cleaned_label = self._clean_hebrew_prefixes(label)

        best_match = None
        highest_ratio = 0.0

        for opt in options:
            # We also clean the taxonomy keys just in case, though they should be canonical
            cleaned_opt = self._clean_hebrew_prefixes(opt)
            ratio = difflib.SequenceMatcher(None, cleaned_label, cleaned_opt).ratio()
            if ratio > highest_ratio:
                highest_ratio = ratio
                best_match = opt

        # Threshold for fuzzy matching
        if highest_ratio > 0.6:
            return best_match
        return None

    def enrich_graph(self, G: nx.Graph):
        """
        Matches nodes against legal taxonomies and adds metadata flags.

        Iterates over nodes of type OFFENSE, DOCUMENT, or LAW and uses fuzzy
        matching to find their corresponding entry in the ontology classes.

        Args:
            G (nx.Graph): The NetworkX graph to enrich. Changes are made in-place.
        """
        for n, data in G.nodes(data=True):
            node_type = data.get("type", "").upper()
            label = data.get("label", "")

            taxonomy_key = None
            if node_type == "OFFENSE":
                taxonomy_key = "Offense_Type"
            elif node_type == "DOCUMENT":
                taxonomy_key = "Document_Format"
            elif node_type == "LAW":
                taxonomy_key = (
                    "Statute_Type"  # Assuming Statute_Type exists or fallback to search
                )

            if taxonomy_key and taxonomy_key in self.classes:
                sub_classes = self.classes[taxonomy_key].get("sub_classes", {})
                match = self._fuzzy_match(label, sub_classes)
                if match:
                    G.nodes[n]["legal_taxonomy"] = match

    def apply_reasoning_rules(self, G: nx.Graph):
        """
        Traverses the graph and applies automated reasoning rules from the ontology.

        Evaluates conditions on nodes and edges and applies actions (setting
        attributes or flags) when conditions are met.

        Args:
            G (nx.Graph): The NetworkX graph to process. Changes are made in-place.
        """
        for rule in self.reasoning_rules:
            condition = rule.get("condition", {})
            action = rule.get("action", {})

            match_type = condition.get("match")
            where_clause = condition.get("where", "")

            if match_type == "edge":
                if G.is_multigraph():
                    for u, v, k, data in G.edges(keys=True, data=True):
                        if self._evaluate_condition(data, where_clause):
                            self._apply_action(G, (u, v, k), action, is_edge=True)
                else:
                    for u, v, data in G.edges(data=True):
                        if self._evaluate_condition(data, where_clause):
                            self._apply_action(G, (u, v), action, is_edge=True)
            elif match_type == "node":
                for n, data in G.nodes(data=True):
                    if self._evaluate_condition(data, where_clause):
                        self._apply_action(G, n, action, is_edge=False)

    def _evaluate_condition(self, data: Dict[str, Any], where: str) -> bool:
        """
        Evaluates a rule's 'where' clause against node or edge attribute data.

        Args:
            data (Dict[str, Any]): The attribute dictionary of the graph element.
            where (str): A string representing the condition (e.g., "type == 'VALUE'").

        Returns:
            bool: True if the condition is met, False otherwise.
        """
        # Simple parser for "key == 'value'" or "key in ['val1', 'val2']"
        # In a real scenario, this would be more robust.
        try:
            # Basic support for edge.property or node.property
            clean_where = where.replace("edge.", "").replace("node.", "")

            if " == " in clean_where:
                key, val = clean_where.split(" == ")
                key = key.strip()
                val = val.strip("'").strip('"')

                # Support mapping 'epistemic_state' to 'confidence' attribute
                actual_val = data.get(key)
                if actual_val is None and key == "epistemic_state":
                    actual_val = data.get("confidence")

                return str(actual_val) == val

            # Special case for CONFLICT_OF_INTEREST as requested
            if "CONFLICT_OF_INTEREST" in where and data.get("relation") == "RELATED_TO":
                # Heuristic: if label implies conflict
                label = str(data.get("label", "")).lower()
                if "ניגוד" in label or "conflict" in label:
                    return True

            return False
        except Exception:
            return False

    def _apply_action(
        self, G: nx.Graph, element: Any, action: Dict[str, Any], is_edge: bool
    ):
        """
        Applies a rule's action to a specific graph element.

        Args:
            G (nx.Graph): The NetworkX graph.
            element (Any): The node ID or edge tuple.
            action (Dict[str, Any]): A dictionary defining 'set' attributes or
                'flag' values.
            is_edge (bool): Whether the element is an edge.
        """
        target = None
        if is_edge:
            if G.is_multigraph():
                u, v, k = element
                target = G.edges[u, v, k]
            else:
                u, v = element
                target = G.edges[u, v]
        else:
            target = G.nodes[element]

        # Set attributes
        for key, val in action.get("set", {}).items():
            target[key] = val

        # Add flags
        flag = action.get("flag")
        if flag:
            if "investigative_flags" not in target:
                target["investigative_flags"] = []
            if flag not in target["investigative_flags"]:
                target["investigative_flags"].append(flag)
