#!/usr/bin/env bash
set -euo pipefail

APP_URL="${APP_URL:-http://localhost:7071}"
PDF_PATH="${PDF_PATH:-data/sample_page_1.pdf}"
TRACE_ID="${TRACE_ID:-pipeline-local-$(date +%s)-${RANDOM}}"

json_get() {
    local expr="$1"
    python3 -c "import json, sys
raw = json.load(sys.stdin)
data = raw.get('data') if isinstance(raw, dict) and isinstance(raw.get('data'), dict) else raw
value = ${expr}
print('' if value is None else value)"
}

json_pretty() {
    python3 -c 'import json, sys
text = sys.stdin.read()
result = text
try:
    result = json.dumps(json.loads(text), ensure_ascii=False, indent=2)
except Exception:
    pass
print(result)'
}

curl_json() {
    curl -sS --max-time 20 \
        -H "x-mag-trace-id: ${TRACE_ID}" \
        "$@"
}

echo "--- Restarting containers ---"
docker compose down -v
docker compose up -d

echo "--- Running Preflight Checks ---"
docker compose exec function-app python3 scripts/preflight_connectivity.py

echo "--- Waiting for Function App to be ready ---"
sleep 5
until curl_json "${APP_URL}/api/ping" | grep -q "Healthy"; do
    echo "Waiting for Function App to report Healthy..."
    sleep 2
done

if [ ! -f "$PDF_PATH" ]; then
    echo "Error: PDF file not found: $PDF_PATH"
    exit 1
fi

PDF_FILENAME=$(basename "$PDF_PATH")

echo "--- Observability Trace ---"
echo "Trace ID: $TRACE_ID"

echo "--- Running Duplicate Preflight ---"
PREFLIGHT_RESPONSE=$(curl_json "${APP_URL}/api/pipeline-preflight?filename=$(python3 -c 'import sys, urllib.parse; print(urllib.parse.quote(sys.argv[1]))' "$PDF_FILENAME")")
PREFLIGHT_STATUS=$(echo "$PREFLIGHT_RESPONSE" | json_get "data.get('status', '')" 2>/dev/null || true)

if [ "$PREFLIGHT_STATUS" = "duplicate" ]; then
    MATCHED_DOC_ID=$(echo "$PREFLIGHT_RESPONSE" | json_get "data.get('matched_doc_id', '')" 2>/dev/null || true)
    echo "--- Duplicate Case Found ---"
    echo "Matched doc id: $MATCHED_DOC_ID"
    echo "$PREFLIGHT_RESPONSE" | json_pretty
    echo "--- App Insights Trace Lookup ---"
    echo "traces | where message has \"${TRACE_ID}\" | order by timestamp asc"
    exit 0
fi

if [ "$PREFLIGHT_STATUS" != "new" ]; then
    echo "Error: pipeline preflight failed or returned an unexpected response:"
    echo "$PREFLIGHT_RESPONSE" | json_pretty
    exit 1
fi

echo "--- Submitting Pipeline ---"
RESPONSE=$(curl -sS -X POST \
    "${APP_URL}/api/pipelines" \
    -H "Content-Type: application/pdf" \
    -H "Content-Disposition: attachment; filename=\"${PDF_FILENAME}\"" \
    -H "x-mag-trace-id: ${TRACE_ID}" \
    --data-binary "@${PDF_PATH}")

INSTANCE_ID=$(echo "$RESPONSE" | json_get "data.get('id', '')" 2>/dev/null || true)

if [ -z "$INSTANCE_ID" ]; then
    echo "Error: failed to obtain Instance ID. Response:"
    echo "$RESPONSE" | json_pretty
    exit 1
fi

echo "--- Pipeline Started ---"
echo "Instance ID: $INSTANCE_ID"
echo "Trace ID:    $TRACE_ID"

echo "--- Monitoring Pipeline ---"
while true; do
    RESPONSE=$(curl_json "${APP_URL}/api/pipelines/${INSTANCE_ID}" || true)

    if [ -z "$RESPONSE" ]; then
        STATUS="Pending (Server Busy/Timeout)"
        PHASE="unknown"
        PROGRESS="0/0"
        DOC_ID=""
        LAST_ERROR=""
        STATUS_TRACE_ID="$TRACE_ID"
    else
        STATUS=$(echo "$RESPONSE" | json_get "data.get('runtimeStatus', 'Unknown')" 2>/dev/null || echo "Unknown")
        PHASE=$(echo "$RESPONSE" | json_get "(data.get('customStatus') or {}).get('phase', 'unknown')" 2>/dev/null || echo "unknown")
        COMPLETED=$(echo "$RESPONSE" | json_get "(data.get('customStatus') or {}).get('completed_chunks', 0)" 2>/dev/null || echo "0")
        TOTAL=$(echo "$RESPONSE" | json_get "(data.get('customStatus') or {}).get('total_chunks', 0)" 2>/dev/null || echo "0")
        PROGRESS="${COMPLETED}/${TOTAL}"
        DOC_ID=$(echo "$RESPONSE" | json_get "(data.get('customStatus') or {}).get('doc_id', '')" 2>/dev/null || true)
        LAST_ERROR=$(echo "$RESPONSE" | json_get "(data.get('customStatus') or {}).get('last_error', '')" 2>/dev/null || true)
        STATUS_TRACE_ID=$(echo "$RESPONSE" | json_get "(data.get('customStatus') or {}).get('trace_id', '')" 2>/dev/null || true)
    fi

    echo "Status: $STATUS | Phase: $PHASE | Chunks: $PROGRESS | Doc: $DOC_ID | Trace: ${STATUS_TRACE_ID:-$TRACE_ID}"

    if [[ "$STATUS" == *"Failed"* ]]; then
        echo "--- Pipeline FAILED ---"
        [ -n "$LAST_ERROR" ] && echo "Last error: $LAST_ERROR"
        echo "$RESPONSE" | json_pretty
        break
    fi

    if [[ "$STATUS" == *"Completed"* ]]; then
        echo "--- Pipeline Completed ---"
        echo "$RESPONSE" | json_pretty
        break
    fi

    sleep 3
done

echo "--- App Insights Trace Lookup ---"
echo "traces | where message has \"${TRACE_ID}\" | order by timestamp asc"
echo "Monitoring queries: monitoring/azure_monitor_observability.md"
