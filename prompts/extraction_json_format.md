Return the graph using the exact Structured Outputs contract supplied with the
request. Both root arrays are required. Every listed field is required and no
additional fields are permitted:

{
  "nodes": [
    {
      "id": "stable chunk-local ID",
      "label": "display label",
      "type": "one canonical ontology entity key",
      "forensic_summary": "source-grounded summary",
      "event_date": "date from the source, or null"
    }
  ],
  "edges": [
    {
      "source": "ID of a node in this output",
      "target": "ID of a different node in this output",
      "relation": "one canonical ontology relation key",
      "description": "source-grounded relationship description",
      "confidence": "one canonical ontology confidence key",
      "confidence_score": 0.0
    }
  ]
}

Never emit an edge unless both endpoint nodes are present in the same output.
Never emit duplicate node IDs or self-loops. Confidence scores must be in the
inclusive range 0 through 1.
