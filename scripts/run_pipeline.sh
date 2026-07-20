#!/bin/bash

echo "--- Restarting containers ---"
docker compose down -v
docker compose up -d

echo "--- Running Preflight Checks ---"
docker compose exec function-app python3 scripts/preflight_connectivity.py

echo "--- Waiting for Function App to be ready ---"
sleep 5
until curl -s "http://localhost:7071/api/ping" | grep -q "Healthy"; do
    echo "Waiting for Function App to report Healthy..."
    sleep 2
done

echo "--- Submitting Pipeline ---"
PDF_PATH="data/sample_page_1.pdf"
PDF_FILENAME=$(basename "$PDF_PATH")
RESPONSE=$(curl -s -X POST \
     "http://localhost:7071/api/pipelines" \
     -H "Content-Type: application/pdf" \
     -H "Content-Disposition: attachment; filename=\"${PDF_FILENAME}\"" \
     --data-binary "@${PDF_PATH}")

INSTANCE_ID=$(echo "$RESPONSE" | python3 -c "import sys, json; print(json.load(sys.stdin).get('id', ''))" 2>/dev/null)

if [ -z "$INSTANCE_ID" ]; then
    echo "Error: Failed to obtain Instance ID. Response: $RESPONSE"
    exit 1
fi

echo "--- Pipeline Started with Instance ID: $INSTANCE_ID ---"

echo "--- Monitoring Pipeline ---"
while true; do
    RESPONSE=$(curl -s --max-time 10 "http://localhost:7071/api/pipelines/$INSTANCE_ID")
    
    if [ -z "$RESPONSE" ]; then
        STATUS="Pending (Server Busy/Timeout)"
        CUSTOM="None"
    else
        STATUS=$(echo "$RESPONSE" | python3 -c "import sys, json; data = json.load(sys.stdin); print(data.get('runtimeStatus', 'Unknown'))" 2>/dev/null || echo "Unknown")
        CUSTOM=$(echo "$RESPONSE" | python3 -c "import sys, json; data = json.load(sys.stdin); print(data.get('customStatus', {}))" 2>/dev/null || echo "None")
    fi
    
    echo "Status: $STATUS | Custom Status: $CUSTOM"
    
    if [[ "$STATUS" == *"Completed"* || "$STATUS" == *"Failed"* ]]; then
        echo "--- Pipeline $STATUS ---"
        break
    fi
    
    sleep 3
done