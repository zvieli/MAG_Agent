# Evaluation Module (`src/evaluation/`)

This module provides quantitative and qualitative metrics to measure the health, accuracy, search recall, and reasoning quality of the Knowledge Graph and RAG pipeline.

---

## 📊 Core Evaluators

The module contains specialized evaluation classes that run against reference golden datasets:

### 1. RAG Evaluator (`rag_evaluator.py`)
- Evaluates the end-to-end performance of the query agent answers.
- Computes **Groundedness Scores** by matching cited fragments in responses to live data.
- Measures answer relevancy relative to a golden QA dataset.

### 2. Retrieval Evaluator (`retrieval_evaluator.py`)
- Tests the retrieval quality of **Azure AI Search** (`graphify-chunks` index).
- Measures **Hit Rate @ K** (how often the correct chunk is in top results) and **MRR** (Mean Reciprocal Rank) for vector query operations.

### 3. Extraction Evaluator (`extraction_evaluator.py`)
- Measures precision, recall, and F1 scores of the Two-Tier Ingestion engine against gold-standard manual node/edge extractions.
- Identifies systemic errors (e.g. missing certain relation types).

### 4. Graph Evaluator (`graph_evaluator.py`)
- Evaluates topological graph characteristics (node count, link density, connected component ratios).

---

## 📈 Quality Metrics & Groundedness (`metrics.py`)

- **Groundedness**: Evaluates citations in answers (supporting both legacy brackets `[Source: chunk_id]` and strict parentheses `(Source: filename, Fragment: node_id)`) by querying the Azure AI Search endpoint to verify that the target text contains the assertions made.
- **Final Quality Score**: Calculates a consolidated graph metric (0-100) based on:
  - **Connectivity**: Ratio of isolated nodes vs. connected vertices.
  - **Semantic Coverage**: Percentage of nodes that have complete, factual descriptions.
  - **Confidence Distribution**: Ratio of `DIRECT_EVIDENCE` vs `CIRCUMSTANTIAL` or `AMBIGUOUS` labels.
  - **Resolution Health**: Warning metrics triggered by near-merge candidates.
