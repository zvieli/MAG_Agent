#!/bin/bash

# הגדרות בסיס
URL="http://localhost:7071/api/query"
BASE_STATUS_URL="http://localhost:7071/runtime/webhooks/durabletask/instances"
CODE="tJGTUXT-ofq87l1f70Q3aisRkTpoMXo2AMc9tY1iVeJMAzFu4oza_Q==" # עדכן כאן את ה-Code שלך במידת הצורך
TASK_HUB="TestHubName"

# מערך של שאלות
declare -a questions=(
    "מי האנשים שקשורים לחברת בזק וביצעו פעולה של 'סיקור חיובי'?"
    "באילו תאריכים התקיימו הפגישות בין החשודים, והבא ציטוט מהראיות?"
    "מה אמר החשוד על מערכת היחסים עם בעלי התקשורת לפי הראיות הטקסטואליות?"
    "על סמך הראיות הטקסטואליות והגרף, זהה סתירות בין הצהרות החשוד לגבי היקף הפגישות עם בעלי התקשורת לבין תאריכי הפגישות המתועדים במערכת. הצג מטריצה של הסתירות והבא ציטוטים מדויקים מהמסמכים התומכים בכל סתירה."
)

run_investigation() {
    for q in "${questions[@]}"; do
        echo "--------------------------------------------------------"
        echo "Running query: $q"
        
        # שליחת השאילתה ושמירת ה-JSON
        response=$(curl -s -X POST $URL \
            -H "Content-Type: application/json" \
            -d "{\"question\": \"$q\", \"doc_id\": \"files_03.01.2021\"}")
        
        # חילוץ ה-instanceId
        instance_id=$(echo $response | jq -r '.id')
        echo "Instance ID: $instance_id"
        
        # המתנה לתוצאות
        status_url=$(echo $response | jq -r '.statusQueryGetUri')
        echo "Waiting for completion..."
        while true; do
            status_json=$(curl -s "$status_url")
            runtime_status=$(echo $status_json | jq -r '.runtimeStatus')
            
            if [ "$runtime_status" == "Completed" ]; then
                echo "Query Completed!"
                echo "Result:"
                echo $status_json | jq -r '.output.answer // .output'
                break
            elif [ "$runtime_status" == "Failed" ]; then
                echo "Query Failed!"
                break
            else
                sleep 2
            fi
        done
    done
}

run_investigation