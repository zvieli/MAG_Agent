import sys
import json
import datetime
from pathlib import Path

# Add .venv site-packages to sys.path to support execution via system python interpreter
project_root = Path(__file__).resolve().parents[1]
for site_pkg in project_root.glob(".venv/lib/python*/site-packages"):
    if site_pkg.exists() and str(site_pkg) not in sys.path:
        sys.path.insert(0, str(site_pkg))

# Now import Azure Data Tables package
try:
    from azure.data.tables import TableServiceClient
except ModuleNotFoundError:
    print("Error: The 'azure-data-tables' library is not installed.")
    print("Please run the script using the project virtual environment:")
    print(f"  {project_root}/.venv/bin/python {__file__} <trace_id>")
    print("Or install the package for your current python environment:")
    print("  pip install azure-data-tables")
    sys.exit(1)

def main():
    if len(sys.argv) < 2:
        print("Usage: python scripts/trace_query_logs.py <trace_id>")
        print("Example: python scripts/trace_query_logs.py query-mr35g5cn-7htt4f")
        sys.exit(1)

    trace_id = sys.argv[1]
    task_hub = "TestHubName"
    
    # 1. Parse connection string from the local .env configuration
    conn_str = None
    env_path = project_root / ".env"
    if env_path.exists():
        for line in open(env_path, encoding="utf-8"):
            line = line.strip()
            if line.startswith("AZURE_STORAGE_CONNECTION_STRING_CLOUD="):
                conn_str = line.split("=", 1)[1].strip().strip('"').strip("'")
                break
    
    if not conn_str:
        print("Error: AZURE_STORAGE_CONNECTION_STRING_CLOUD not found in .env")
        sys.exit(1)

    # 2. Establish connection to Azure Table Storage
    print("Connecting to Azure Table Storage...")
    try:
        service_client = TableServiceClient.from_connection_string(conn_str)
    except Exception as e:
        print(f"Failed to create TableServiceClient: {e}")
        sys.exit(1)

    # 3. Resolve the client-side trace ID base36 timestamp
    try:
        ts_part = trace_id.split("-")[1]
        timestamp_ms = int(ts_part, 36)
        dt_utc = datetime.datetime.fromtimestamp(timestamp_ms / 1000.0, datetime.timezone.utc)
        print(f"Parsed client trace ID UTC Timestamp: {dt_utc}")
    except Exception as e:
        print(f"Could not parse trace ID timestamp: {e}")
        dt_utc = None

    # 4. Query instances table to find the matching orchestration GUID
    instances_table_name = f"{task_hub}Instances"
    history_table_name = f"{task_hub}History"
    instance_guid = None

    print(f"\n--- Checking Table: {instances_table_name} ---")
    try:
        table_client = service_client.get_table_client(instances_table_name)
        entities = list(table_client.list_entities())
        
        # Try matching by GUID directly if it matches standard guid length, otherwise scan by timestamp
        if dt_utc:
            print("Scanning for orchestration instance matching timestamp...")
            for ent in entities:
                created_time = ent.get("CreatedTime")
                if created_time:
                    # Find matching created times (within a 3-second window of the trace timestamp)
                    diff = abs((created_time - dt_utc).total_seconds())
                    if diff <= 3.0:
                        instance_guid = ent.get("PartitionKey")
                        print(f"✅ Found matching Orchestration GUID: {instance_guid}")
                        print(json.dumps(dict(ent), indent=2, default=str, ensure_ascii=False))
                        break
        
        # Fallback: check if the trace ID itself is the PartitionKey/Instance ID
        if not instance_guid:
            for ent in entities:
                if ent.get("PartitionKey") == trace_id:
                    instance_guid = trace_id
                    print(f"✅ Found matching Orchestration by direct ID: {instance_guid}")
                    print(json.dumps(dict(ent), indent=2, default=str, ensure_ascii=False))
                    break
                    
        if not instance_guid:
            print("No matching instance orchestration GUID found in active storage.")
            sys.exit(1)
            
    except Exception as e:
        print(f"Error querying {instances_table_name}: {e}")
        sys.exit(1)

    # 5. Query and display history events
    print(f"\n--- Querying Table: {history_table_name} (GUID: {instance_guid}) ---")
    try:
        table_client = service_client.get_table_client(history_table_name)
        entities = list(table_client.query_entities(f"PartitionKey eq '{instance_guid}'"))
        if entities:
            # Sort events by execution timeline
            entities = sorted(entities, key=lambda x: x.get("SequenceNumber") or str(x.get("_Timestamp", "")))
            print(f"Found {len(entities)} history events:")
            for ent in entities:
                event_type = ent.get("EventType", "Unknown")
                ts = ent.get("Timestamp") or ent.get("_Timestamp")
                details = {k: v for k, v in ent.items() if k not in ["PartitionKey", "RowKey", "Timestamp", "SequenceNumber", "EventType", "TaskHubName"]}
                print(f"[{ts}] Seq={ent.get('SequenceNumber')} | Event={event_type}")
                if details:
                    cleaned_details = {}
                    for k, v in details.items():
                        if isinstance(v, str) and (v.startswith("{") or v.startswith("[")):
                            try:
                                cleaned_details[k] = json.loads(v)
                            except:
                                cleaned_details[k] = v
                        else:
                            cleaned_details[k] = v
                    print(json.dumps(cleaned_details, indent=4, default=str, ensure_ascii=False))
        else:
            print(f"No history events found for GUID '{instance_guid}' in {history_table_name}.")
    except Exception as e:
        print(f"Error querying {history_table_name}: {e}")

if __name__ == "__main__":
    main()
