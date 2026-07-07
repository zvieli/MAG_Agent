import re
import logging
from typing import List, Dict, Any
from src.models.models import HEBREW_TYPE_MAP


class ASTNode:
    pass


class ASTBinaryOp(ASTNode):
    def __init__(self, left, op, right):
        self.left = left
        self.op = op
        self.right = right


class ASTComparison(ASTNode):
    def __init__(self, left, op, right):
        self.left = left
        self.op = op
        self.right = right


class ASTNotExists(ASTNode):
    def __init__(self, var_name, condition):
        self.var_name = var_name
        self.condition = condition


class Parser:
    def __init__(self, text: str):
        self.tokens = self._tokenize(text)
        self.pos = 0

    def _tokenize(self, text: str):
        # Support Hebrew characters in identifiers
        token_pattern = r"""
            (?P<AND>\bAND\b) |
            (?P<OR>\bOR\b) |
            (?P<NOT_EXISTS>\bNOT\s+EXISTS\b) |
            (?P<WHERE>\bWHERE\b) |
            (?P<EQ>==) |
            (?P<NEQ>!=) |
            (?P<LPAREN>\() |
            (?P<RPAREN>\)) |
            (?P<STRING>'[^']*'|"[^"]*") |
            (?P<IDENT>[a-zA-Z_א-ת][a-zA-Z0-9_\.א-ת]*) |
            (?P<WS>\s+)
        """
        tokens = []
        for match in re.finditer(token_pattern, text, re.VERBOSE | re.IGNORECASE):
            kind = match.lastgroup
            value = match.group()
            if kind == "WS":
                continue
            if kind == "STRING":
                value = value[1:-1]
            tokens.append((kind, value))
        return tokens

    def peek(self):
        if self.pos < len(self.tokens):
            return self.tokens[self.pos]
        return None

    def consume(self, expected_kind=None):
        tok = self.peek()
        if tok:
            if expected_kind and tok[0] != expected_kind:
                raise ValueError(f"Expected {expected_kind}, got {tok[0]} ('{tok[1]}')")
            self.pos += 1
        return tok

    def parse(self):
        return self.parse_or()

    def parse_or(self):
        node = self.parse_and()
        while self.peek() and self.peek()[0] == "OR":
            self.consume("OR")
            right = self.parse_and()
            node = ASTBinaryOp(node, "OR", right)
        return node

    def parse_and(self):
        node = self.parse_primary()
        while self.peek() and self.peek()[0] == "AND":
            self.consume("AND")
            right = self.parse_primary()
            node = ASTBinaryOp(node, "AND", right)
        return node

    def parse_primary(self):
        tok = self.peek()
        if not tok:
            raise ValueError("Unexpected end of input")

        if tok[0] == "LPAREN":
            self.consume("LPAREN")
            node = self.parse()
            self.consume("RPAREN")
            return node

        if tok[0] == "NOT_EXISTS":
            self.consume("NOT_EXISTS")
            self.consume("LPAREN")
            var_tok = self.consume("IDENT")
            var_name = var_tok[1]
            condition = None
            if self.peek() and self.peek()[0] == "WHERE":
                self.consume("WHERE")
                condition = self.parse()
            self.consume("RPAREN")
            return ASTNotExists(var_name, condition)

        left_tok = self.consume("IDENT")
        left = left_tok[1]

        op_tok = self.peek()
        if not op_tok or op_tok[0] not in ("EQ", "NEQ"):
            raise ValueError(f"Expected comparison operator, got {op_tok}")
        op = self.consume()[1]

        right_tok = self.peek()
        if not right_tok:
            raise ValueError("Expected value after comparison operator")

        if right_tok[0] == "STRING":
            right = self.consume("STRING")[1]
            # Wrap string in quotes to distinguish it from identifier in evaluator
            return ASTComparison(left, op, f"'{right}'")
        else:
            right = self.consume("IDENT")[1]
            return ASTComparison(left, op, right)


class Evaluator:
    def __init__(self, engine, nodes, edges):
        self.engine = engine
        self.nodes = nodes
        self.edges = edges
        self.node_lookup = {str(n.get("id")): n for n in nodes}

    def evaluate(self, ast, ctx):
        if isinstance(ast, ASTBinaryOp):
            left_val = self.evaluate(ast.left, ctx)
            if ast.op.upper() == "AND":
                if not left_val:
                    return False
                return self.evaluate(ast.right, ctx)
            elif ast.op.upper() == "OR":
                if left_val:
                    return True
                return self.evaluate(ast.right, ctx)

        elif isinstance(ast, ASTComparison):
            left_val, left_type = self._resolve(ast.left, ctx)

            if ast.right.startswith("'"):
                right_val = ast.right[1:-1]
            else:
                right_val, _ = self._resolve(ast.right, ctx)
                if not right_val and ast.right not in ctx:
                    right_val = ast.right  # Literal fallback

            if ast.op == "==":
                return self.engine._matches(right_val, left_val, left_type)
            elif ast.op == "!=":
                return not self.engine._matches(right_val, left_val, left_type)

        elif isinstance(ast, ASTNotExists):
            if ast.var_name == "edge":
                # Optimization: if condition has edge.source == node.id, we can filter edges
                for edge in self.edges:
                    sub_ctx = dict(ctx)
                    sub_ctx["edge"] = edge
                    if not ast.condition or self.evaluate(ast.condition, sub_ctx):
                        return False
                return True
            elif ast.var_name == "node":
                for node in self.nodes:
                    sub_ctx = dict(ctx)
                    sub_ctx["node"] = node
                    if not ast.condition or self.evaluate(ast.condition, sub_ctx):
                        return False
                return True
        return False

    def _resolve(self, path: str, ctx: dict):
        if path in ctx:
            obj = ctx[path]
            if isinstance(obj, dict) and "id" in obj:
                return str(obj["id"]), "unknown"
            return str(obj), "unknown"

        parts = path.split(".")
        base = parts[0]
        if base not in ctx:
            return "", "unknown"

        obj = ctx[base]

        # Handle edge.source.class or edge.target.class
        if base == "edge" and len(parts) >= 3:
            if parts[1] in ("source", "target"):
                node_id = str(obj.get(parts[1]))
                node = self.node_lookup.get(node_id, {})
                if parts[2] == "class":
                    return str(node.get("type", "")), "class"
                else:
                    return str(node.get(parts[2], "")), "unknown"

        val = obj
        for p in parts[1:]:
            if isinstance(val, dict):
                if p == "class":
                    p = "type"
                val = val.get(p, "")
            else:
                val = ""
                break

        field_type = parts[-1]
        if field_type == "class":
            field_type = "class"
        elif field_type == "epistemic_state":
            field_type = "epistemic_state"
        elif field_type == "relation":
            field_type = "relation"
        else:
            field_type = "unknown"

        return str(val), field_type


class OntologyEngine:
    """
    Core reasoning engine that evaluates graph patterns against legal ontology rules.

    Operates on a subset of the graph (typically search results) to identify
    potential legal violations or forensic patterns defined in the schema.
    """

    def __init__(self, schema_config: Dict[str, Any]):
        """
        Initializes the OntologyEngine.

        Args:
            schema_config: Dictionary containing ontology rules and type mappings.
        """
        self.schema = schema_config
        self.rules = self.schema.get("ontology_engine", {}).get("reasoning_rules", [])
        if not self.rules:
            # Fallback for old schema format
            self.rules = self.schema.get("reasoning_rules", [])
        self._parser_cache = {}

    def map_query(self, query: str) -> List[Dict[str, str]]:
        """
        [DEPRECATED] Maps abstract terms in the query to schema-defined types/relations.
        """
        raise NotImplementedError("This function is deprecated and its body has been removed.")

    def evaluate_graph_data(
        self, matched_nodes: List[Dict[str, Any]], matched_edges: List[Dict[str, Any]]
    ) -> List[str]:
        """
        Evaluates the provided graph data against loaded ontology rules.

        Performs strictly structural evaluation based on node types and edge relations
        using a custom parser and evaluator.

        Args:
            matched_nodes: List of nodes retrieved during search.
            matched_edges: List of edges retrieved during search.

        Returns:
            List of 'LEGAL FLAG' strings triggered by the reasoning rules.
        """
        flags = []
        if not self.rules:
            return flags

        logging.info(
            f"[OntologyEngine] Evaluating {len(matched_nodes)} nodes and {len(matched_edges)} edges against {len(self.rules)} rules..."
        )

        evaluator = Evaluator(self, matched_nodes, matched_edges)

        for rule in self.rules:
            condition = rule.get("condition", {})
            match_type = condition.get("match")
            where_clause = condition.get("where", "")
            action = rule.get("action", {})
            flag_msg = action.get("flag")

            if not flag_msg or not where_clause:
                continue

            if where_clause not in self._parser_cache:
                try:
                    parser = Parser(where_clause)
                    self._parser_cache[where_clause] = parser.parse()
                except Exception as e:
                    logging.error(
                        f"[OntologyEngine] Failed to parse rule '{rule.get('name')}': {e}"
                    )
                    continue

            ast = self._parser_cache[where_clause]

            if match_type == "edge":
                for edge in matched_edges:
                    try:
                        if evaluator.evaluate(ast, {"edge": edge}):
                            insight = f"🚨 LEGAL FLAG: Rule '{rule['name']}' triggered on relation '{edge.get('relation')}' between '{edge.get('source_label', edge.get('source'))}' and '{edge.get('target_label', edge.get('target'))}'. Action: {flag_msg}."
                            if insight not in flags:
                                flags.append(insight)
                    except Exception:
                        # Silently skip if evaluation fails for a specific edge
                        pass

            elif match_type == "node":
                for node in matched_nodes:
                    try:
                        if evaluator.evaluate(ast, {"node": node}):
                            node_label = node.get("label", node.get("id", ""))
                            insight = f"🚨 LEGAL FLAG: Rule '{rule['name']}' triggered on node '{node_label}'. Action: {flag_msg}."
                            if insight not in flags:
                                flags.append(insight)
                    except Exception:
                        # Silently skip if evaluation fails for a specific node
                        pass

        return flags

    def _matches(self, expected: str, actual: str, field_type: str) -> bool:
        """
        Bilingual matching logic for rule evaluation.

        Args:
            expected: Expected value from the rule.
            actual: Actual value from the graph.
            field_type: Field type (e.g., 'class', 'relation', 'epistemic_state').

        Returns:
            True if values match according to schema mappings.
        """
        if not expected or not actual:
            return False

        expected = str(expected).strip().lower()
        actual = str(actual).strip().lower()

        if expected == actual:
            return True

        # Bilingual logic using schema mappings
        if field_type == "epistemic_state":
            for eng, heb in self.schema.get("confidence_levels", {}).items():
                if eng.lower() == expected and heb.lower() == actual:
                    return True

        if field_type == "class":
            # Check localized HEBREW_TYPE_MAP
            for eng, heb in HEBREW_TYPE_MAP.items():
                if eng.lower() == expected and heb.lower() == actual:
                    return True
            # Check legal_role_map
            for eng, heb_list in self.schema.get("legal_role_map", {}).items():
                if eng.lower() == expected:
                    if any(heb.lower() == actual for heb in heb_list):
                        return True

        if field_type == "relation":
            for eng, heb in self.schema.get("relation_types", {}).items():
                if eng.lower() == expected and heb.lower() == actual:
                    return True

        return False
