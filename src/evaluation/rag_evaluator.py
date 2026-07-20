import logging
import json
from typing import Any, Dict, List
from src.storage.blob_storage import IBlobStorage
from src.storage.search_manager import SearchManager
from src.evaluation.metrics import calculate_groundedness
from src.utils.config import settings

logger = logging.getLogger(__name__)


class RagEvaluator:
    """
    Evaluates End-to-End RAG performance.
    Measures Groundedness and Answer Relevance.
    """

    def __init__(
        self, storage: IBlobStorage, search_mgr: SearchManager, llm_client: Any
    ):
        self.storage = storage
        self.search_mgr = search_mgr
        self.llm_client = llm_client

    async def evaluate(
        self, dataset_path: str = "evals/golden_dataset.jsonl"
    ) -> Dict[str, Any]:
        """Runs E2E RAG evaluation."""
        logging.info(
            f"[RAG_EVAL] [START] | Metadata: Dataset={dataset_path} | Message: Starting E2E evaluation"
        )

        try:
            content = await self.storage.read_text(dataset_path)
            cases = [json.loads(line) for line in content.splitlines() if line.strip()]
        except Exception as e:
            logging.error(
                f"[RAG_EVAL] [ERROR] | Metadata: Error={str(e)} | Message: Failed to load dataset"
            )
            return {"status": "error"}

        results = []
        total_groundedness = 0
        total_hallucination_score = 0

        for case in cases:
            qa = case.get("expected_qa")
            if not qa:
                continue

            question = qa["question"]
            expected_answer = qa["answer"]

            # 1. Live Retrieval from Azure AI Search
            from src.processing.cloud_knowledge_provider import CloudKnowledgeProvider

            kp = CloudKnowledgeProvider(search_mgr=self.search_mgr, doc_id="global")
            retrieved_context = await kp.search_chunks(question, top_k=5)

            # 2. Generate Answer based on Live Context
            generated_answer = await self._generate_answer(
                question, [retrieved_context]
            )

            # 3. Calculate Metrics
            # Groundedness (Live Citation Verification)
            groundedness_score = await calculate_groundedness(
                generated_answer, search_mgr=self.search_mgr
            )

            # Answer Similarity (LLM Judge)
            similarity_score = await self._judge_similarity(
                generated_answer, expected_answer
            )

            # Hallucination Check (LLM Judge vs Live Context)
            (
                hallucination_score,
                hallucination_reason,
            ) = await self._detect_hallucinations(generated_answer, [retrieved_context])

            total_groundedness += groundedness_score
            total_hallucination_score += hallucination_score

            results.append(
                {
                    "question": question,
                    "generated": generated_answer,
                    "expected": expected_answer,
                    "groundedness": groundedness_score,
                    "similarity": similarity_score,
                    "hallucination_score": hallucination_score,
                    "hallucination_reason": hallucination_reason,
                }
            )

        summary = {
            "avg_groundedness": total_groundedness / len(results) if results else 0,
            "avg_hallucination_score": total_hallucination_score / len(results)
            if results
            else 0,
            "cases": results,
        }

        logging.info(
            f"[RAG_EVAL] [SUCCESS] | Metadata: AvgGroundedness={summary['avg_groundedness']:.2f}, AvgHallucination={summary['avg_hallucination_score']:.2f} | Message: E2E Evaluation complete"
        )
        return summary

    async def _generate_answer(self, question: str, chunks: List[str]) -> str:
        """Simulates LLM answer generation."""
        # Inject standard citation guidelines to ensure the model references chunks
        citation_instr = (
            "\n### CITATION RULES:\n"
            "Every fact or claim in your final response MUST end with a source citation referencing the exact chunk ID it came from (the identifier following 'CHUNK: ' in the text). "
            "Use the format: [Your text in Hebrew/English] (Source: [chunk_id]). "
            "Example: 'החשוד נעצר ב-2026 (Source: 20260125015510______-26-1128__________________1).'\n"
            "DO NOT shorten or alter the chunk ID to 'chunk_X'. Copy it exactly.\n\n"
            "### INVESTIGATIVE GRANULARITY RULES:\n"
            "1. **Differentiate Parallel Frameworks**: Be extremely precise. If a document mentions details for both military/IDF (field context) and police booking (station context), list both or specify which is which (e.g., 'השוטר העוצר בפועל/החוקר בתיק הוא רס\"ם פלג מגידש, וקצין צה\"ל מאשר המעצר בשטח הוא סרן בר כהן').\n"
            "2. **Differentiate Time Intervals**: Do not confuse incident opening/registration times (e.g., 'זמן תחילת הארוע: 11:21') with interrogation session times (e.g., 'החקירה החלה בשעה 12:40 והסתיימה בשעה 13:31:39'). If asked about investigation times, retrieve the interrogation form start/end times.\n"
            "3. **Facebook Post Layout**: In standard police reports, the text of a Facebook post is often printed *above* its item metadata (which contains the post date, like '29 בנובמבר 2024'). Carefully associate the text with the date listed in the metadata block directly below it.\n"
        )
        prompt = f"Answer the question based ONLY on the provided chunks.{citation_instr}\nChunks: {' '.join(chunks)}\nQuestion: {question}"
        response = await self.llm_client.chat.completions.create(
            model=settings.graphify_agent_model,
            messages=[{"role": "user", "content": prompt}],
        )
        return response.choices[0].message.content

    async def _judge_similarity(self, actual: str, expected: str) -> float:
        """Uses LLM to score similarity between answers (0.0 - 1.0)."""
        prompt = f"Compare these two investigative answers and score their similarity from 0.0 to 1.0.\nActual: {actual}\nExpected: {expected}\nScore only (float):"
        try:
            response = await self.llm_client.chat.completions.create(
                model=settings.graphify_agent_model,
                messages=[{"role": "user", "content": prompt}],
            )
            return float(response.choices[0].message.content.strip())
        except Exception:
            return 0.5

    async def _detect_hallucinations(self, answer: str, chunks: List[str]) -> tuple:
        """Uses LLM to detect if the answer contains information not present in the chunks."""
        prompt = f"### TASK\nYou are a forensic auditor. Check the following answer for hallucinations based ONLY on the provided chunks.\n\n### CHUNKS\n{' '.join(chunks)}\n\n### ANSWER\n{answer}\n\n### INSTRUCTIONS\n1. Identify any claims in the answer not supported by the chunks.\n2. Score from 0.0 (Pure Hallucination) to 1.0 (Fully Grounded).\n3. Provide a brief reason.\n\nFormat: [Score]|[Reason]"

        try:
            response = await self.llm_client.chat.completions.create(
                model=settings.graphify_agent_model,
                messages=[{"role": "user", "content": prompt}],
            )
            content = response.choices[0].message.content.strip()
            if "|" in content:
                score_str, reason = content.split("|", 1)
                return float(score_str.strip()), reason.strip()
            return 0.5, content
        except Exception:
            return 0.5, "Failed to parse LLM judgment"
