#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

if [ -f "${PROJECT_ROOT}/.env" ]; then
    while IFS= read -r line || [ -n "$line" ]; do
        if [[ ! "$line" =~ ^[[:space:]]*# ]] && [[ "$line" =~ = ]]; then
            key=$(echo "$line" | cut -d'=' -f1 | xargs)
            value=$(echo "$line" | cut -d'=' -f2- | xargs)
            value="${value#\"}"
            value="${value%\"}"
            value="${value#\'}"
            value="${value%\'}"
            export "$key=$value"
        fi
    done < "${PROJECT_ROOT}/.env"
fi

APP_URL="${AZURE_FUNCTION_APP_URL:-}"
AUTH_KEY="${AZURE_FUNCTION_APP_KEY:-}"
PDF_PATH="${PDF_PATH:-data/files_03.01.2021_short_2pages.pdf}"
TRACE_ID="${TRACE_ID:-pipeline-cloud-$(date +%s)-${RANDOM}}"
ALLOW_ANONYMOUS="${ALLOW_ANONYMOUS_FUNCTIONS:-false}"

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

urlencode() {
    python3 -c 'import sys, urllib.parse; print(urllib.parse.quote(sys.argv[1]))' "$1"
}

if [ -z "$APP_URL" ]; then
    echo "Error: AZURE_FUNCTION_APP_URL is required."
    echo "Example: export AZURE_FUNCTION_APP_URL=https://<app>.azurewebsites.net"
    exit 1
fi

if [ -z "$AUTH_KEY" ] && [ "$ALLOW_ANONYMOUS" != "true" ]; then
    echo "Error: AZURE_FUNCTION_APP_KEY is required for cloud runs."
    echo "Set ALLOW_ANONYMOUS_FUNCTIONS=true only for intentionally anonymous test endpoints."
    exit 1
fi

if [ ! -f "$PDF_PATH" ]; then
    echo "Error: PDF file not found: $PDF_PATH"
    exit 1
fi

COMMON_HEADERS=(-H "x-mag-trace-id: ${TRACE_ID}")
if [ -n "$AUTH_KEY" ]; then
    COMMON_HEADERS+=(-H "x-functions-key: ${AUTH_KEY}")
fi

PDF_FILENAME=$(basename "$PDF_PATH")

echo "--- Observability Trace ---"
echo "Trace ID: $TRACE_ID"
echo "Function App: $APP_URL"

echo "--- Running Cloud Health Check ---"
HEALTH_RESPONSE=$(curl -sS --max-time 30 "${COMMON_HEADERS[@]}" "${APP_URL}/api/health" || true)
HEALTH_STATUS=$(echo "$HEALTH_RESPONSE" | json_get "data.get('status', '')" 2>/dev/null || true)
if [ "$HEALTH_STATUS" = "Unhealthy" ]; then
    echo "Warning: /api/health returned Unhealthy:"
    echo "$HEALTH_RESPONSE" | json_pretty
fi

echo "--- Running Duplicate Preflight ---"
PREFLIGHT_RESPONSE=$(curl -sS --max-time 30 \
    "${COMMON_HEADERS[@]}" \
    "${APP_URL}/api/pipeline-preflight?filename=$(urlencode "$PDF_FILENAME")")
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

echo "--- Submitting Pipeline to Azure ---"
RESPONSE=$(curl -sS -X POST \
    "${APP_URL}/api/pipelines" \
    "${COMMON_HEADERS[@]}" \
    -H "Content-Type: application/pdf" \
    -H "Content-Disposition: attachment; filename=\"${PDF_FILENAME}\"" \
    --data-binary "@${PDF_PATH}")

if [ -z "$RESPONSE" ]; then
    echo "Error: server returned an empty response."
    exit 1
fi

INSTANCE_ID=$(echo "$RESPONSE" | json_get "data.get('id', '')" 2>/dev/null || true)

if [ -z "$INSTANCE_ID" ]; then
    echo "Error: failed to obtain Instance ID. Server response:"
    echo "$RESPONSE" | json_pretty
    exit 1
fi

echo "--- Pipeline Started ---"
echo "Instance ID: $INSTANCE_ID"
echo "Trace ID:    $TRACE_ID"

echo "--- Monitoring Pipeline ---"
while true; do
    RESPONSE=$(curl -sS --max-time 20 \
        "${COMMON_HEADERS[@]}" \
        "${APP_URL}/api/pipelines/${INSTANCE_ID}" || true)

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

    sleep 5
done

echo "--- App Insights Trace Lookup ---"
echo "traces | where message has \"${TRACE_ID}\" | order by timestamp asc"
echo "Monitoring queries: monitoring/azure_monitor_observability.md"
