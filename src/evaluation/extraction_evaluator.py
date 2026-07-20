import asyncio
import json
import sys
from pathlib import Path
from typing import List, Dict, Any

# Ensure project root is in sys.path
ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from src.extraction.extraction_engine import extract_from_chunk  # noqa: E402
from src.models.models import SCHEMA_CONFIG, normalize_confidence_label  # noqa: E402
from src.storage.factory import get_artifact_store  # noqa: E402
from src.utils.bidi_utils import fix_text  # noqa: E402

GOLDEN_DATASET_BLOB_PATH = "evals/golden_extraction.jsonl"


def normalize_label(label: str) -> str:
    """
    Normalizes an entity label by stripping whitespace and ensuring string type.

    Args:
        label: Raw label from the extraction.

    Returns:
        Cleaned string label.
    """
    if not label:
        return ""
    return str(label).strip()


def normalize_relation(relation: str) -> str:
    """
    Maps legacy relation types to the new canonical relation set.

    Heuristically translates high-cardinality relation strings into one of the
    5 canonical types: COMMUNICATED_WITH, FINANCIAL_TRANSFER, INSTRUCTED,
    TESTIFIED_ABOUT, or RELATED_TO.

    Args:
        relation: Raw relation string.

    Returns:
        Canonical relation string.
    """
    rel = str(relation).upper().strip()

    # Canonical Set: COMMUNICATED_WITH, FINANCIAL_TRANSFER, INSTRUCTED, TESTIFIED_ABOUT, RELATED_TO

    mapping = {
        # COMMUNICATED_WITH
        "COMMUNICATED_WITH": "COMMUNICATED_WITH",
        "SPOKE_TO": "COMMUNICATED_WITH",
        "MET_WITH": "COMMUNICATED_WITH",
        "SENT_MESSAGE": "COMMUNICATED_WITH",
        "CALLED": "COMMUNICATED_WITH",
        "EMAILED": "COMMUNICATED_WITH",
        "CORRESPONDED": "COMMUNICATED_WITH",
        "DISCUSSED": "COMMUNICATED_WITH",
        # FINANCIAL_TRANSFER
        "FINANCIAL_TRANSFER": "FINANCIAL_TRANSFER",
        "PAID": "FINANCIAL_TRANSFER",
        "TRANSFERRED_FUNDS": "FINANCIAL_TRANSFER",
        "BRIBED": "FINANCIAL_TRANSFER",
        "GAVE_MONEY": "FINANCIAL_TRANSFER",
        "RECEIVED_PAYMENT": "FINANCIAL_TRANSFER",
        "FUNDED": "FINANCIAL_TRANSFER",
        "INVESTED": "FINANCIAL_TRANSFER",
        "LOANED": "FINANCIAL_TRANSFER",
        # INSTRUCTED
        "INSTRUCTED": "INSTRUCTED",
        "ORDERED": "INSTRUCTED",
        "DIRECTED": "INSTRUCTED",
        "MANAGED": "INSTRUCTED",
        "SUPERVISED": "INSTRUCTED",
        "HIRED": "INSTRUCTED",
        "FIRED": "INSTRUCTED",
        "APPOINTED": "INSTRUCTED",
        "REQUESTED_ACTION": "INSTRUCTED",
        # TESTIFIED_ABOUT
        "TESTIFIED_ABOUT": "TESTIFIED_ABOUT",
        "WITNESSED": "TESTIFIED_ABOUT",
        "REPORTED": "TESTIFIED_ABOUT",
        "IDENTIFIED": "TESTIFIED_ABOUT",
        "CONFESSED_TO": "TESTIFIED_ABOUT",
        "ACCUSED": "TESTIFIED_ABOUT",
        "DESCRIBED": "TESTIFIED_ABOUT",
        "MENTIONED": "TESTIFIED_ABOUT",
        # SEIZED_FROM
        "SEIZED_FROM": "SEIZED_FROM",
        "CONFISCATED_FROM": "SEIZED_FROM",
        "TOOK_FROM": "SEIZED_FROM",
        # OWNER_OF
        "OWNER_OF": "OWNER_OF",
        "OWNED_BY": "OWNER_OF",
        "OWNS": "OWNER_OF",
        # INVOLVED_IN
        "INVOLVED_IN": "INVOLVED_IN",
        "PARTICIPATED_IN": "INVOLVED_IN",
        "ARRESTED_IN": "INVOLVED_IN",
        # RELATED_TO (Default for others)
        "RELATED_TO": "RELATED_TO",
        "WORKS_FOR": "RELATED_TO",
        "MEMBER_OF": "RELATED_TO",
        "LOCATED_AT": "RELATED_TO",
        "PART_OF": "RELATED_TO",
        "ASSOCIATED_WITH": "RELATED_TO",
        "CONFLICT_OF_INTEREST": "RELATED_TO",
        "FAMILY_MEMBER": "RELATED_TO",
        "FRIEND_OF": "RELATED_TO",
        "COLLEAGUE": "RELATED_TO",
        "SHAREHOLDER": "RELATED_TO",
        "COMMITTED": "RELATED_TO",
        "PART_OF_SAME_CONTEXT": "RELATED_TO",
    }

    # Heuristic fallback if not in explicit mapping
    if rel in mapping:
        return mapping[rel]

    # Categorical heuristics
    if any(k in rel for k in ["PAY", "CASH", "MONEY", "FUND", "TRANSFER", "BANK"]):
        return "FINANCIAL_TRANSFER"
    if any(
        k in rel for k in ["TALK", "MET", "CALL", "SEND", "MESSAGE", "COMMUN", "SPOKE"]
    ):
        return "COMMUNICATED_WITH"
    if any(
        k in rel
        for k in [
            "ORDER",
            "DIRECT",
            "COMMAND",
            "MANAGE",
            "BOSS",
            "INSTRUCT",
            "HIRE",
            "APPOINT",
            "CONCEAL",
        ]
    ):
        return "INSTRUCTED"
    if any(
        k in rel
        for k in [
            "TESTIFY",
            "WITNESS",
            "SAY",
            "STATE",
            "TELL",
            "REPORT",
            "CONFESS",
            "ACCUSE",
            "MENTIONED",
        ]
    ):
        return "TESTIFIED_ABOUT"

    return "RELATED_TO"


class ExtractionEvaluator:
    """
    Evaluates the extraction engine performance against a golden dataset.
    """
    def __init__(self, storage: Any, search_mgr: Any = None, llm_client: Any = None):
        self.storage = storage
        self.search_mgr = search_mgr
        self.llm_client = llm_client

    async def evaluate(self, dataset_path: str = "evals/golden_extraction.jsonl") -> Dict[str, Any]:
        logging_info = logging.info if "logging" in globals() else print
        logging_error = logging.error if "logging" in globals() else print

        logging_info(f"[EXTRACTION_EVAL] [START] | Dataset={dataset_path}")
        try:
            content = await self.storage.read_text(dataset_path)
            cases = [json.loads(line) for line in content.splitlines() if line.strip()]
        except Exception as e:
            logging_error(f"[EXTRACTION_EVAL] [ERROR] | Error={str(e)}")
            return {"status": "error", "message": str(e)}

        total_precision = 0
        total_recall = 0
        total_epistemic_acc = 0
        count = 0
        results = []

        for case in cases:
            input_text = case["input_text"]
            expected_entities = case["expected_entities"]
            expected_relations = case["expected_relations"]
            category = case.get("test_category", "default")

            extraction, _ = await extract_from_chunk(
                None, input_text, SCHEMA_CONFIG, chunk_label=f"eval_{category}"
            )

            id_to_label = {n.id: normalize_label(n.label) for n in extraction.nodes}

            # Normalize entities
            extracted_entities = [
                {"label": normalize_label(n.label), "type": n.type}
                for n in extraction.nodes
            ]
            expected_entities_norm = [
                {"label": normalize_label(e["label"]), "type": e["type"]}
                for e in expected_entities
            ]

            ent_precision, ent_recall = calculate_precision_recall(
                extracted_entities, expected_entities_norm, "label"
            )

            # Normalize relations
            extracted_relations = []
            for r in extraction.edges:
                conf, _ = normalize_confidence_label(
                    r.confidence, getattr(r, "confidence_score", None)
                )
                extracted_relations.append(
                    {
                        "source": id_to_label.get(r.source, r.source),
                        "target": id_to_label.get(r.target, r.target),
                        "relation": normalize_relation(r.relation),
                        "confidence": conf,
                    }
                )

            expected_relations_norm = []
            for r in expected_relations:
                expected_relations_norm.append(
                    {**r, "relation": normalize_relation(r.get("relation", ""))}
                )

            rel_precision, rel_recall = calculate_precision_recall(
                extracted_relations, expected_relations_norm, "relation"
            )

            epistemic_acc = calculate_epistemic_accuracy(
                extracted_relations, expected_relations
            )

            case_precision = (ent_precision + rel_precision) / 2
            case_recall = (ent_recall + rel_recall) / 2

            total_precision += case_precision
            total_recall += case_recall
            total_epistemic_acc += epistemic_acc
            count += 1

            results.append({
                "category": category,
                "precision": case_precision,
                "recall": case_recall,
                "epistemic_acc": epistemic_acc,
                "extracted_entities": [e['label'] for e in extracted_entities],
                "expected_entities": [e['label'] for e in expected_entities],
                "extracted_relations": [(r['source'], r['relation'], r['target']) for r in extracted_relations],
                "expected_relations": [(r['source'], r['relation'], r['target']) for r in expected_relations]
            })

        summary = {
            "status": "success",
            "total_cases": count,
            "avg_precision": total_precision / count if count > 0 else 0.0,
            "avg_recall": total_recall / count if count > 0 else 0.0,
            "avg_epistemic_acc": total_epistemic_acc / count if count > 0 else 0.0,
            "cases": results
        }
        logging_info(f"[EXTRACTION_EVAL] [SUCCESS] | AvgPrecision={summary['avg_precision']:.2f}")
        return summary


def calculate_precision_recall(
    extracted: List[Dict], expected: List[Dict], match_key: str
) -> tuple[float, float]:
    """
    Calculates precision and recall between extracted and expected lists of objects.

    Args:
        extracted: List of dictionaries from the engine.
        expected: List of dictionaries from the golden dataset.
        match_key: The primary key to match on (e.g., 'label' or 'relation').

    Returns:
        A tuple of (precision, recall).
    """
    if not expected:
        # If none were expected, recall is 1.0. Precision is 1.0 if none were extracted, else 0.0.
        precision = 1.0 if not extracted else 0.0
        return precision, 1.0
    if not extracted:
        return 0.0, 0.0

    true_positives = 0
    for exp in expected:
        for ext in extracted:
            # For relations, match the triple (source, relation, target)
            if match_key == "relation":
                if (
                    ext.get("source") == exp.get("source")
                    and ext.get("target") == exp.get("target")
                    and ext.get("relation") == exp.get("relation")
                ):
                    true_positives += 1
                    break
            # For entities, match by label
            elif ext.get(match_key) == exp.get(match_key):
                true_positives += 1
                break

    precision = true_positives / len(extracted) if extracted else 0
    recall = true_positives / len(expected) if expected else 0
    return precision, recall


def calculate_epistemic_accuracy(
    extracted_rels: List[Dict], expected_rels: List[Dict]
) -> float:
    """
    Measures how accurately the engine predicted confidence levels.

    Args:
        extracted_rels: Extracted relations with 'confidence' field.
        expected_rels: Expected relations from golden dataset.

    Returns:
        Ratio of correctly predicted confidence labels.
    """
    relevant_expected = [r for r in expected_rels if "confidence" in r]
    if not relevant_expected:
        return 1.0  # No epistemic requirements to check

    matches = 0
    for exp in relevant_expected:
        for ext in extracted_rels:
            if ext.get("source") == exp.get("source") and ext.get("target") == exp.get(
                "target"
            ):
                if ext.get("confidence") == exp.get("confidence"):
                    matches += 1
                break

    return matches / len(relevant_expected)


if __name__ == "__main__":
    import logging
    from src.storage.factory import get_artifact_store
    logging.basicConfig(level=logging.INFO)
    class LocalStoreWrapper:
        async def read_text(self, path: str) -> str:
            return get_artifact_store().read_bytes(path).decode("utf-8")

    async def main():
        evaluator = ExtractionEvaluator(LocalStoreWrapper())
        summary = await evaluator.evaluate(GOLDEN_DATASET_BLOB_PATH)
        print(summary)

    asyncio.run(main())
