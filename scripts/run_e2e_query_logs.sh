#!/usr/bin/env bash
set -eo pipefail

echo "=== 1. Resetting Docker Environment ==="
docker compose down
docker compose up -d

echo "=== 2. Waiting for Function App API Readiness ==="
until curl -s http://localhost:7071/api/health >/dev/null 2>&1 || curl -s http://localhost:7071/ >/dev/null 2>&1; do
    echo "Waiting for http://localhost:7071 to be ready..."
    sleep 3
done
echo "Function App endpoint is ready!"

DOC_ID="20260125015510 יהודה-26-1128 חומר חקירה מושחר"
TRACE_ID="e2e-query-$(date +%s)"

echo "=== 3. Submitting Investigation Query ==="
INITIAL_RESPONSE=$(curl -sS -X POST "http://localhost:7071/api/query" \
  -H "Content-Type: application/json" \
  -H "x-mag-trace-id: ${TRACE_ID}" \
  --data-binary @- <<JSON
{
  "doc_id": "${DOC_ID}",
  "question": "תאר את ציר הזמן והאינטראקציה בין החשודה אינאס אחלאוי לבין הכוח העוצר או החוקר, ומה בדיוק נאמר או תועד בפרוטוקול לגבי אותה פגישה?",
  "autonomous_mode": true
}
JSON
)

echo "Initial Response:"
echo "${INITIAL_RESPONSE}" | jq .

STATUS_URL=$(echo "${INITIAL_RESPONSE}" | jq -r '.statusQueryGetUri // empty')

if [ -z "${STATUS_URL}" ] || [ "${STATUS_URL}" == "null" ]; then
  echo "Error: statusQueryGetUri not found in initial response!"
  exit 1
fi

echo "=== 4. Polling Status Endpoint ==="
echo "Status URL: ${STATUS_URL}"

while true; do
  STATUS_RESPONSE=$(curl -sS "${STATUS_URL}")
  RUNTIME_STATUS=$(echo "${STATUS_RESPONSE}" | jq -r '.runtimeStatus // empty')
  
  echo "Current Status: ${RUNTIME_STATUS}"
  
  if [ "${RUNTIME_STATUS}" == "Completed" ]; then
    echo "=== Query Completed Successfully! ==="
    echo "${STATUS_RESPONSE}" | jq -r '.output // .output.final_answer // .'
    break
  elif [ "${RUNTIME_STATUS}" == "Failed" ] || [ "${RUNTIME_STATUS}" == "Terminated" ]; then
    echo "=== Query Terminated with Status: ${RUNTIME_STATUS} ==="
    echo "${STATUS_RESPONSE}" | jq .
    break
  else
    sleep 3
  fi
done

echo "=== 5. Extracting Observability Event Logs ==="
docker compose logs function-app | grep --line-buffered "\[OBS\]" | sed -E 's/.*\[OBS\][[:space:]]*//' | jq -c . > logs.txt || true
echo "Observability logs saved to logs.txt ($(wc -l < logs.txt) JSON events)."
