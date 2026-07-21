# Evaluation Module

`src/evaluation/` contains offline quality checks for extraction, graph health, retrieval, routing, and answer groundedness.

## Current Components

- `extraction_evaluator.py`: compares extracted graph items against gold/reference extraction data.
- `graph_evaluator.py`: computes graph quality and topology health metrics.
- `metrics.py`: shared quality and groundedness metrics.
- `rag_evaluator.py`: evaluates query-agent answers against expected answers and citations.
- `retrieval_evaluator.py`: evaluates Azure AI Search retrieval quality for source chunks.
- `routing_evaluator.py`: evaluates query-routing behavior.

## Typical Uses

- Regression testing extraction quality after prompt/schema changes.
- Measuring retrieval hit rate after embedding or chunking changes.
- Checking graph health after entity-resolution changes.
- Evaluating query-agent groundedness before demos or releases.

## Maintainer Notes

- Keep evaluators separate from production pipeline activities.
- Prefer deterministic fixtures and small reference datasets for CI.
- Do not require live Azure dependencies for unit-level evaluator tests unless the test is explicitly marked/invoked as integration.
