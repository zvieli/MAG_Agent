# Reporting Module

`src/reporting/` generates human-readable analytical artifacts, Markdown case reports, community summaries, and protocol completeness audit reports from the resolved graph and source text.

## Responsibilities

- Generate Markdown case reports (`graph/{doc_id}/GRAPH_REPORT.md`).
- Run community detection (NetworkX / Louvain clustering) and generate community summaries (`graph/{doc_id}/community_summaries.json`).
- Analyze case coverage against required investigative items (`graph/{doc_id}/protocol_completeness.json`).
- Verify missing or ambiguous protocol completeness items via background `QueryAgent` searches.

## Components

- `report_generator.py`: Graph report generation, NetworkX graph analysis, community summary creation, fallback community summaries, and Markdown output formatting (`DefaultReporter`).
- `protocol_completeness.py`: Analyzer for protocol completeness rules (`analyze_protocol_completeness`) and background agent verification runner (`verify_missing_items_with_agent`).

## Protocol Completeness Analysis & Agent Verification

The completeness analyzer checks case compliance against the curated checklist defined in `schemas/ontology_schema.json`.

### Analysis Pipeline

1. **Initial Assessment (`analyze_protocol_completeness`)**:
   - Scans raw text (`extracted_text/{doc_id}.md`) and graph inventory for required protocol pillars (e.g. suspect interrogations, search warrants, financial audit logs, exhibit seizures).
   - Assigns item status: `present`, `missing`, `conditional_missing`, `not_applicable`, or `critical_review_required`.
   - Emits `protocol_completeness.generated` observability event and saves `graph/{doc_id}/protocol_completeness.json`.

2. **On-Demand Agent Verification (`verify_missing_items_with_agent`)**:
   - Triggered via HTTP `POST /api/cases/{docId}/completeness/verify` or background activity `activity_verify_protocol_completeness`.
   - Uses `SearchManager` and `QueryAgent` to perform targeted hybrid searches over case materials to check if missing items exist in unindexed or subtle context fragments.
   - Updates `protocol_completeness.json` in Blob Storage and invalidates SPFx browser cache (`protocol_completeness:${docId}`).

## Community Summaries

NetworkX clustering clusters nodes into topological communities.
- When `enable_llm_community_summaries` is `true` in schema config, LLMs generate summaries for each community.
- When disabled, `fallback_community_summaries` generates deterministic, zero-cost summaries from community members and top central nodes.

## Artifact Serving

All generated reporting artifacts are stored in Azure Blob Storage under `graph/{doc_id}/...` and served directly by Function App HTTP APIs without triggering on-demand recalculations:
- `GET /api/cases/{docId}/report` \(\rightarrow\) `graph/{doc_id}/GRAPH_REPORT.md`
- `GET /api/cases/{docId}/communities` \(\rightarrow\) `graph/{doc_id}/community_summaries.json`
- `GET /api/cases/{docId}/completeness` \(\rightarrow\) `graph/{doc_id}/protocol_completeness.json`

## Maintainer Notes

- Checklist rules and protocol items must remain in `schemas/ontology_schema.json`, not hardcoded in Python.
- Report artifacts must maintain explicit source line and chunk references for auditability.
