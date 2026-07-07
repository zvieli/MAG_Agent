# Cloud Operations & Debugging Guide

This document is for local debugging only. Production operations should use Azure Portal, Azure CLI, Managed Identity, and the deployment workflow in `deploy.ps1`.

## 🌌 Cosmos DB (Graph Database)

Accessing the graph database via Gremlin. You can run these commands directly on your host machine (ensure your virtual environment is active so dependencies and the `.env` file are loaded).

### 1. Count Total Nodes
Check how many entities are currently in the graph.
```bash
python3 -c "
from src.graph.cosmos_manager import CosmosGremlinManager
import asyncio
async def run():
    mgr = CosmosGremlinManager()
    try:
        res = await mgr.execute_query('g.V().count()')
        print(f'Total Nodes: {res[0]}')
    finally: await mgr.close()
asyncio.run(run())
"
```

### 2. List All Entities
Display ID and Type for every node in the database.
```bash
python3 -c "
import asyncio
from src.graph.cosmos_manager import CosmosGremlinManager

async def run_report():
    mgr = CosmosGremlinManager()
    try:
        # שאילתה בטוחה לכל גרסאות ה-Cosmos DB
        nodes = await mgr.execute_query('g.V().valueMap(true)')
        edges = await mgr.execute_query('g.E().valueMap(true)')
        
        print("\n" + "="*60)
        print("📊 KNOWLEDGE GRAPH SUMMARY")
        print("="*60)
        print(f"Entities: {len(nodes)} | Relations: {len(edges)}")
        
        print("\n🌌 ENTITIES (First 10):")
        for n in nodes[:10]:
            label = n.get('label', ['Unknown'])[0]
            name = n.get('name', [n.get('id', ['Unknown'])[0]])[0]
            print(f" -> [{label}] {name}")

        print("\n🔗 RELATIONS (First 10):")
        for e in edges[:10]:
            label = e.get('label', 'related_to')
            print(f" -> Relation: {label}")

    finally:
        await mgr.close()

asyncio.run(run_report())
"
```

### 3. Cleanup: Wipe Database (Dangerous!)
Delete all vertices and edges to start from a clean state.
```bash
python3 -c "
from src.graph.cosmos_manager import CosmosGremlinManager
import asyncio
async def run():
    mgr = CosmosGremlinManager()
    try:
        print('Cleaning Cosmos DB...')
        await mgr.execute_query('g.V().drop()')
        print('Cleanup complete.')
    finally: await mgr.close()
asyncio.run(run())
"
```

---

## 📦 Blob Storage (Azurite, Dev Only)

Managing files in the local storage emulator.

### 1. List All Blobs
See all files currently stored in the `graphify-out` container.
```bash
docker compose exec function-app python3 -c "
from azure.storage.blob import BlobServiceClient
client = BlobServiceClient.from_connection_string('UseDevelopmentStorage=true')
container = client.get_container_client('graphify-out')
for b in container.list_blobs(): print(f'-> {b.name}')
"
```

### 2. Download Final Graph Output
Download a specific graph JSON from storage to your host machine.
```bash
docker compose exec function-app python3 -c "from azure.storage.blob import BlobServiceClient; from pathlib import Path; client = BlobServiceClient.from_connection_string('UseDevelopmentStorage=true'); target = Path('graph/281ab247-112b-48b9-bd89-1358a7a1d51c/final.json'); target.parent.mkdir(parents=True, exist_ok=True); blob_client = client.get_blob_client('graphify-out', 'graph/281ab247-112b-48b9-bd89-1358a7a1d51c/final.json'); target.write_bytes(blob_client.download_blob().readall()); print('Downloaded to', target)"
```

### 3. Trigger Pipeline (Upload PDF)
Manually upload a PDF to the `raw/` folder to trigger the processing pipeline.
```bash
# Replace 'path/to/your/file.pdf' with your actual PDF path
curl -X POST --data-binary @path/to/your/file.pdf -H "Content-Disposition: attachment; filename=manual_upload.pdf" http://localhost:7071/api/start_pipeline
```

---

## ☁️ Azure Blob Storage (Cloud, No Docker)

Managing files in the Azure Storage account directly from your host machine. These commands use Python, the Azure Storage SDK, and the cloud storage values from `.env`.

### 1. One-time Python Setup
Create a local virtual environment and install the Azure Storage SDK.
```bash
python3 -m venv .venv
.venv/bin/python -m pip install 'azure-storage-blob>=12.19.0'
```

### 2. List All Azure Blobs
See all files currently stored in the cloud `graphify-out` container.
```bash
.venv/bin/python -c "
import os
from azure.storage.blob import BlobServiceClient

for line in open('.env'):
    line = line.strip()
    if not line or line.startswith('#') or '=' not in line:
        continue
    key, value = line.split('=', 1)
    os.environ.setdefault(key, value.strip().strip(chr(34)).strip(chr(39)))

conn_str = os.getenv('AZURE_STORAGE_CONNECTION_STRING_CLOUD')
container_name = os.getenv('ARTIFACTS_CONTAINER', 'graphify-out')

if not conn_str:
    raise SystemExit('Missing AZURE_STORAGE_CONNECTION_STRING_CLOUD in .env')

container = BlobServiceClient.from_connection_string(
    conn_str,
    connection_timeout=10,
    read_timeout=20,
).get_container_client(container_name)
for blob in container.list_blobs():
    print(blob.name)
"
```

### 3. Delete a Specific Azure Blob
Delete one blob by its path inside the container.
```bash
export BLOB_PATH="graph/files_03.01.2021/GRAPH_REPORT.md"

.venv/bin/python -c "
import os
from azure.storage.blob import BlobServiceClient

for line in open('.env'):
    line = line.strip()
    if not line or line.startswith('#') or '=' not in line:
        continue
    key, value = line.split('=', 1)
    os.environ.setdefault(key, value.strip().strip(chr(34)).strip(chr(39)))

conn_str = os.getenv('AZURE_STORAGE_CONNECTION_STRING_CLOUD')
container_name = os.getenv('ARTIFACTS_CONTAINER', 'graphify-out')
blob_path = os.getenv('BLOB_PATH')

if not conn_str:
    raise SystemExit('Missing AZURE_STORAGE_CONNECTION_STRING_CLOUD in .env')
if not blob_path:
    raise SystemExit('Set BLOB_PATH to the blob path you want to delete')

blob_client = BlobServiceClient.from_connection_string(
    conn_str,
    connection_timeout=10,
    read_timeout=20,
).get_blob_client(container_name, blob_path)
blob_client.delete_blob()
print(f'Deleted {container_name}/{blob_path}')
"
```

---

## 🔍 Azure AI Search (Dev/Validation)

### 1. List Search Indices / Check Connectivity
Check overall connectivity to Azure Search and other services directly from the host.
```bash
python3 scripts/preflight_connectivity.py
```

### 2. Delete a Search Index
Permanently remove a specific index (e.g., graphify-chunks).
```bash
# Set INDEX_NAME to the one you want to delete
python3 -c "
from src.storage.search_manager import SearchManager
import asyncio
async def run():
    manager = SearchManager()
    index_name = 'graphify-chunks' # <--- Change this
    try:
        print(f'Deleting index: {index_name}...')
        await manager.index_client.delete_index(index_name)
        print('Deletion complete.')
    except Exception as e:
        print(f'Error: {e}')
    finally: await manager.close()
asyncio.run(run())
"
```

---

## 📋 System Monitoring

### 1. Tail Real-time Logs
Monitor the pipeline activities, OpenAI calls, and errors.
- **If running via Docker Compose**:
  ```bash
  docker compose logs -f function-app
  ```
- **If running directly on the host (using Functions Core Tools)**:
  Logs are output directly to the terminal where you started the app via `func start`.

### 2. Filter Pipeline Logs (Docker)
See only the high-level orchestrator and activity logs.
```bash
docker compose logs -f function-app | grep -iE "ACTIVITY|ORCHESTRATOR|COSMOS|SEARCH"
```
