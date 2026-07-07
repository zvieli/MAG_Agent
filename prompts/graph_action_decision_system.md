Decide on the best graph action based on this task. If it asks for connection path between two actors, output 'connections'. If it asks for chronology/timeline of an entity, output 'timeline'. Otherwise, output 'ontology' to check rule violations.
Return JSON: {"action": "connections|timeline|ontology", "entity_a": "...", "entity_b": "..."}
