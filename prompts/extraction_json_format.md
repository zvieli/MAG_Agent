Your output MUST be a valid JSON object with the following structure:
{
  "nodes": [{"id": "...", "type": "...", "entity_class": "...", "label": "...", "forensic_summary": "..."}],
  "edges": [{"source": "...", "target": "...", "relation": "...", "confidence": "...", "forensic_summary": "..."}],
  "claims": [{"claim_id": "...", "subject_id": "...", "statement": "...", "epistemic_state": "...", "evidence_fragment_id": "..."}],
  "evidence_fragments": [{"fragment_id": "...", "document_id": "...", "text": "..."}]
}
