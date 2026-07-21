# Reporting Module

`src/reporting/` generates human-readable case artifacts from the resolved graph and extracted text.

## Responsibilities

- Build Markdown graph reports.
- Generate community/topology summaries.
- Produce visual graph artifacts.
- Generate protocol completeness results for missing-evidence review.

## Current Components

- `report_generator.py`: graph report generation, NetworkX analysis, community summary handling, and visualization/report artifact creation.
- `protocol_completeness.py`: analyzer for `protocol_completeness` rules from `schemas/ontology_schema.json`; emits `protocol_completeness.json`.

## Protocol Completeness

The completeness analyzer checks whether required or conditionally required investigation materials were found in the uploaded case. It uses:

- curated checklist configuration from `schemas/ontology_schema.json`.
- raw chunk text from Azure Search/backfill/local artifacts.
- graph inventory signals from extracted nodes/edges.
- keyword and regex signals.
- evidence strength and review notes for weak matches.

The result is an advisory artifact, not a legal determination.

Common statuses:

- `present`
- `missing`
- `conditional_missing`
- `not_applicable`
- `unknown`

## Community Summaries

Community detection is still part of report generation, but LLM-written community summaries are cost-controlled by configuration. When disabled, deterministic fallback summaries keep reports usable without extra LLM calls.

## Maintainer Notes

- Keep report artifacts explainable and source-linked.
- Keep legal checklist rules in the ontology schema, not in Python.
- Do not mark weak exhibit descriptions as full chain-of-custody proof without explicit chain evidence.
