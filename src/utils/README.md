# Utilities Module (`src/utils/`)

This module provides reusable helper functions, configuration engines, text manipulation systems, and JSON parsing helpers utilized throughout the GraphRAG pipelines and reasoning services.

---

## ⚙️ Core Utilities

### 1. Configuration Engine (`config.py`)
- Employs **Pydantic Settings** to validate and load operational settings at runtime.
- Automatically handles dynamic resolution of LLM prompt templates stored in the root `/prompts` folder.
- Resolves schema properties and merges settings loaded via local `.env` overrides or cloud environment settings.

### 2. Forensic JSON Repair (`json_repair.py`)
LLM structural predictions can occasionally arrive truncated or malformed. To prevent ingestion crashes:
- **Bracket Balancing**: Iteratively computes missing braces and arrays to close truncated JSON strings.
- **Key Normalization**: Merges hallucinated property keys and resolves nested dictionaries into single flat schemas.
- **Safe ID Conversions**: Automatically translates Hebrew node labels into safe, ASCII-compatible snake_case strings while keeping the original string mapping as attributes (ensuring smooth Cosmos DB ingestion).

### 3. Bidirectional Text Renderer (`bidi_utils.py`)
- Standardizes terminal outputs for right-to-left languages.
- Implements visual reshaping and correct sequencing of mixed Hebrew and English character streams to prevent scrambled terminal logs.

### 4. Model Helpers (`model_helpers.py`)
- Standardizes data types prior to loading in Pydantic nodes/edges.
- Normalizes textual confidence score definitions (e.g. mapping various synonyms like `שכיח` or `עקיף` to standard categories).
- Implements validation logic that drops low-probability relationships based on minimum threshold settings.
