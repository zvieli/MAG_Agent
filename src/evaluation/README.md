# Evaluation Module

`src/evaluation/` provides quality evaluation frameworks for graph extraction, retrieval precision, agent answer groundedness, routing accuracy, and prompt structure verification.

## Components

- `rag_evaluator.py`: Evaluates query-agent answers against reference answers, measuring groundedness, factual alignment, and citation accuracy.
- `retrieval_evaluator.py`: Evaluates Azure AI Search retrieval quality for source document chunks (MRR, Hit@K, Recall).
- `extraction_evaluator.py`: Compares extracted knowledge graph nodes and edges against gold-standard evaluation benchmarks.
- `graph_evaluator.py`: Computes graph topology health, connectivity metrics, and community structure quality.
- `routing_evaluator.py`: Evaluates query routing decisions (`doc_branch`, `graph_branch`, or `parallel`).
- `metrics.py`: Shared evaluation metric calculation functions.

## Automated Groundedness & Prompt Structure Unit Tests (`tests/`)

- `test_agent_groundedness_prompts.py`: Verifies template variable formatting, citation instruction binding in synthesis/finalizer prompts, and tests `_build_prompt` prompt loading structure.
- `test_build_prompt_loading_and_structure_printing`: Tests extraction prompt loading, prints the full rendered prompt to stdout (via `pytest -s`), and asserts that the **Static Prefix** precedes the **Dynamic Suffix** for Azure OpenAI Prompt Caching.

## Typical Evaluation Workflows

```bash
# Run unit test suite including prompt structure & groundedness verification
PYTHONPATH=. ./.venv/bin/pytest tests/test_agent_groundedness_prompts.py -s

# Run full evaluation suite
.venv/bin/python -m evals.run_rag_eval
```

## Maintainer Notes

- Evaluators are designed for quality diagnostics and CI benchmark runs; they are kept strictly decoupled from production pipeline activities.
- Use deterministic mock fixtures for unit tests so they can execute fast without cloud dependencies.
