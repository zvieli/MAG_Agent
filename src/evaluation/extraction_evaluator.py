import asyncio
import json
import sys
from pathlib import Path
from typing import List, Dict

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
        # RELATED_TO (Default for others)
        "RELATED_TO": "RELATED_TO",
        "WORKS_FOR": "RELATED_TO",
        "MEMBER_OF": "RELATED_TO",
        "OWNED_BY": "RELATED_TO",
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


async def run_evaluation():
    """
    Runs the extraction engine evaluation against a golden dataset.

    Measures precision, recall, and epistemic accuracy (confidence labels)
    for extracted entities and relations.
    """
    store = get_artifact_store()
    if not store.exists(GOLDEN_DATASET_BLOB_PATH):
        print(f"Error: Golden dataset not found in cloud at {GOLDEN_DATASET_BLOB_PATH}")
        return

    results = []
    content = store.read_bytes(GOLDEN_DATASET_BLOB_PATH).decode("utf-8")
    for line in content.splitlines():
        if line.strip():
            results.append(json.loads(line))

    total_precision = 0
    total_recall = 0
    total_epistemic_acc = 0
    count = 0

    print(fix_text("\n=== Extraction Engine Evaluation ===\n"))

    for case in results:
        input_text = case["input_text"]
        expected_entities = case["expected_entities"]
        expected_relations = case["expected_relations"]
        category = case["test_category"]

        print(fix_text(f"Evaluating Case: {category}"))

        # Call extraction engine (mocking chunk_label for evaluation)
        extraction, _ = await extract_from_chunk(
            None, input_text, SCHEMA_CONFIG, chunk_label=f"eval_{category}"
        )

        # Debug: Print raw extraction
        print(f"DEBUG Raw Nodes: {[(n.id, n.label) for n in extraction.nodes]}")
        print(
            f"DEBUG Raw Edges: {[(e.source, e.relation, e.target, e.confidence) for e in extraction.edges]}"
        )

        # Create ID to Label mapping for better relation matching
        id_to_label = {n.id: normalize_label(n.label) for n in extraction.nodes}

        # Metrics for Entities
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

        # Metrics for Relations
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

        # Epistemic Accuracy (specifically for confidence labels)
        epistemic_acc = calculate_epistemic_accuracy(
            extracted_relations, expected_relations
        )

        # Average precision/recall for this case
        case_precision = (ent_precision + rel_precision) / 2
        case_recall = (ent_recall + rel_recall) / 2

        print(
            fix_text(
                f"  Precision: {case_precision:.2f}, Recall: {case_recall:.2f}, Epistemic Acc: {epistemic_acc:.2f}"
            )
        )
        print(
            fix_text(
                f"  Extracted Entities: {[e['label'] for e in extracted_entities]}"
            )
        )
        print(
            fix_text(f"  Expected Entities:  {[e['label'] for e in expected_entities]}")
        )
        print(
            fix_text(
                f"  Extracted Relations: {[(r['source'], r['relation'], r['target']) for r in extracted_relations]}"
            )
        )
        print(
            fix_text(
                f"  Expected Relations:  {[(r['source'], r['relation'], r['target']) for r in expected_relations]}"
            )
        )

        total_precision += case_precision
        total_recall += case_recall
        total_epistemic_acc += epistemic_acc
        count += 1

    if count > 0:
        avg_precision = total_precision / count
        avg_recall = total_recall / count
        avg_epistemic = total_epistemic_acc / count

        print("\n" + "=" * 30)
        print(fix_text(f"FINAL RESULTS ({count} cases)"))
        print(f"Average Precision: {avg_precision:.2f}")
        print(f"Average Recall:    {avg_recall:.2f}")
        print(f"Average Epistemic Accuracy: {avg_epistemic:.2f}")
        print("=" * 30)


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
    asyncio.run(run_evaluation())
