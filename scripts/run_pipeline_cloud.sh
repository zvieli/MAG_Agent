#!/bin/bash

# Load environment variables from .env if present
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [ -f "$SCRIPT_DIR/../.env" ]; then
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
    done < "$SCRIPT_DIR/../.env"
fi

APP_URL="${AZURE_FUNCTION_APP_URL:-https://homkeyhakera-lagend-e7cafje6hyhfdyfk.westeurope-01.azurewebsites.net}"
AUTH_KEY="${AZURE_FUNCTION_APP_KEY:-NKpBhLHOMIPKDmNgemwvSfJ-4UWXtz7qzWtrxHV1TEY2AzFuEVdhRg==}"
PDF_PATH="data/files_03.01.2021_short_2pages.pdf"

AUTH_HEADER=""
if [ ! -z "$AUTH_KEY" ]; then
    AUTH_HEADER="-H x-functions-key: $AUTH_KEY"
fi

echo "--- Submitting Pipeline to Azure ---"
RESPONSE=$(curl -s -X POST \
  "$APP_URL/api/pipelines" \
  -H "Content-Type: application/pdf" \
  $AUTH_HEADER \
  --data-binary "@$PDF_PATH")

if [ -z "$RESPONSE" ]; then
    echo "Error: Server returned an empty response."
    exit 1
fi

INSTANCE_ID=$(echo $RESPONSE | python3 -c "import sys, json; print(json.load(sys.stdin).get('id', ''))")

if [ -z "$INSTANCE_ID" ]; then
    echo "Error: Failed to obtain Instance ID. Server response:"
    echo $RESPONSE
    exit 1
fi

echo "--- Pipeline Started with Instance ID: $INSTANCE_ID ---"

echo "--- Monitoring Pipeline ---"
while true; do
    RESPONSE=$(curl -s --max-time 10 $AUTH_HEADER "$APP_URL/api/pipelines/$INSTANCE_ID")
    
    if [ -z "$RESPONSE" ]; then
        STATUS="Pending (Server Busy/Timeout)"
        CUSTOM="None"
    else
        STATUS=$(echo $RESPONSE | python3 -c "import sys, json; data = json.load(sys.stdin); print(data.get('runtimeStatus', 'Unknown'))" 2>/dev/null || echo "Unknown")
        CUSTOM=$(echo $RESPONSE | python3 -c "import sys, json; data = json.load(sys.stdin); print(data.get('customStatus', {}))" 2>/dev/null || echo "None")
    fi
    
    echo "Status: $STATUS | Custom Status: $CUSTOM"
    
    if [[ "$STATUS" == *"Failed"* ]]; then
        echo "--- Pipeline FAILED! ---"
        echo "Fetching details..."
        # הדפסת ה-Output המלא של השגיאה מהשרת
        echo $RESPONSE | python3 -c "import sys, json; print(json.load(sys.stdin).get('output', 'No output details'))"
        break
    fi

    if [[ "$STATUS" == *"Completed"* ]]; then
        echo "--- Pipeline Finished ---"
        break
    fi
    sleep 5
done
