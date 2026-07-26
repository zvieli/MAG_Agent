Generate search queries optimized for hybrid document retrieval based on this task.
You must output a JSON object with two fields:
1. "semantic_query": A descriptive, natural-language sentence optimized for vector embeddings (focusing on semantic meaning and context).
2. "keyword_query": A space-separated list containing ONLY key proper nouns, entity names (people/organizations/locations), and unique identifiers/case numbers, optimized for keyword BM25 retrieval. Do NOT include generic terms like "interaction", "timeline", "details", etc.

Example:
Task: תאר את ציר הזמן של האירועים הקשורים לאינטראקציה בין אינאס אחלאוי לבין הכוח העוצר.
Output:
{
  "semantic_query": "מעצר וחקירה של אינאס אחלאוי ציר זמן ואינטראקציה עם הכוח העוצר",
  "keyword_query": "אינאס אחלאוי"
}
