#!/bin/bash

# Knowledge Graph System Stats & Monitoring Script
# Comprehensive report for Cosmos DB, Blob Storage, AI Search, and Graph Triples.

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

# Locate Python interpreter
PYTHON_CMD="python3"
if [ -d "$SCRIPT_DIR/../.venv" ]; then
    PYTHON_CMD="$SCRIPT_DIR/../.venv/bin/python3"
fi

echo "===================================================="
echo "🚀 MAG AGENT - SYSTEM STATUS REPORT"
echo "===================================================="

# 1. GRAPH HEALTH (Entities, Distribution & All Triples)
echo -e "\n🌌 [GRAPH] Health, Composition & All Triples:"
$PYTHON_CMD -c "
from src.graph.cosmos_manager import CosmosGremlinManager
import asyncio
async def run():
    mgr = CosmosGremlinManager()
    try:
        nodes = await mgr.execute_query('g.V().valueMap(true)')
        triples = await mgr.execute_query('g.E().project(\"out\", \"rel\", \"in\").by(outV().values(\"label\")).by(label()).by(inV().values(\"label\"))')
        
        # Entity Stats
        stats = {}
        for n in nodes:
            c = n.get('meta_semantic_class', ['OTHER'])[0]
            stats[c] = stats.get(c, 0) + 1
        
        print(f'   -> Total Entities: {len(nodes)}')
        for k, v in sorted(stats.items(), key=lambda x: x[1], reverse=True):
            print(f'      - {k.upper():<12}: {v}')

        # Relations Stats (All)
        print(f'\n   -> ALL {len(triples)} Relations (Triples):')
        for t in triples:
            rel = t['rel'].replace('OTHER_RELATION', 'related_to')
            print(f'      {t['out']:<20} --({rel})--> {t['in']}')
            
    except Exception as e: print(f'   [!] Error: {e}')
    finally: await mgr.close()
asyncio.run(run())
"

# 📦 2. BLOB STORAGE STATISTICS
echo -e "\n📦 [BLOB STORAGE] Container: graphify-out"

# 2.1. Local (Azurite) - stays inside Docker
docker compose exec -T function-app python3 -c "
from azure.storage.blob import BlobServiceClient
import math
def convert_size(size_bytes):
   if not size_bytes or size_bytes == 0: return '0B'
   size_name = ('B', 'KB', 'MB', 'GB')
   i = int(math.floor(math.log(size_bytes, 1024)))
   p = math.pow(1024, i)
   s = round(size_bytes / p, 2)
   return f'{s} {size_name[i]}'

try:
    client = BlobServiceClient.from_connection_string('UseDevelopmentStorage=true')
    container = client.get_container_client('graphify-out')
    blobs = list(container.list_blobs())
    print('   --- Local (Azurite - devstoreaccount1) ---')
    print(f'   -> Total Blobs: {len(blobs)}')
    total_size = sum([getattr(b, 'size', 0) for b in blobs])
    print(f'   -> Total Storage Size: {convert_size(total_size)}')
except Exception as e:
    print('   --- Local (Azurite - devstoreaccount1) ---')
    print(f'   [ERROR] Blob Storage: {e}')
"

# 2.2. Cloud (Azure) - independent of Docker
$PYTHON_CMD -c "
import os
from azure.storage.blob import BlobServiceClient
import math

def convert_size(size_bytes):
   if not size_bytes or size_bytes == 0: return '0B'
   size_name = ('B', 'KB', 'MB', 'GB')
   i = int(math.floor(math.log(size_bytes, 1024)))
   p = math.pow(1024, i)
   s = round(size_bytes / p, 2)
   return f'{s} {size_name[i]}'

def get_account_name(conn_str):
    parts = dict(x.split('=', 1) for x in conn_str.split(';') if '=' in x)
    return parts.get('AccountName', 'Unknown')

cloud_conn = None
for var in ['AZURE_STORAGE_CONNECTION_STRING_CLOUD', 'CLOUD_STORAGE_CONNECTION_STRING', 'AZURE_STORAGE_CONNECTION_STRING']:
    val = os.environ.get(var)
    if val and not any(x in val.lower() for x in ['devstoreaccount1', 'usedevelopmentstorage=true', '127.0.0.1', 'localhost']):
        cloud_conn = val
        break

if cloud_conn:
    try:
        acct = get_account_name(cloud_conn)
        client = BlobServiceClient.from_connection_string(cloud_conn)
        container = client.get_container_client('graphify-out')
        blobs = list(container.list_blobs())
        print(f'   --- Cloud (Azure - {acct}) ---')
        print(f'   -> Total Blobs: {len(blobs)}')
        total_size = sum([getattr(b, 'size', 0) for b in blobs])
        print(f'   -> Total Storage Size: {convert_size(total_size)}')
    except Exception as e:
        print('   --- Cloud (Azure) ---')
        print(f'   [ERROR] Blob Storage: {e}')
else:
    print('   --- Cloud (Azure) ---')
    print('   [!] Cloud connection string not found in environment (set AZURE_STORAGE_CONNECTION_STRING_CLOUD or CLOUD_STORAGE_CONNECTION_STRING).')
"

# 🔍 3. AI SEARCH INDICES
echo -e "\n🔍 [AI SEARCH] Critical Index Health:"
$PYTHON_CMD -c "
from src.storage.search_manager import SearchManager
import asyncio
async def run():
    mgr = SearchManager()
    try:
        client = mgr.index_client
        async for index in client.list_index_names():
            stats = await client.get_index_statistics(index)
            print(f'      - {index:<20} | Docs: {stats[\"document_count\"]}')
    except Exception as e: print(f'   [!] Search Error: {e}')
    finally: await mgr.close()
asyncio.run(run())
"

# 📄 4. FINAL GRAPH ARTIFACTS
echo -e "\n📄 [LATEST GRAPH] Recent Artifacts:"

# 4.1. Local (Azurite) - stays inside Docker
docker compose exec -T function-app python3 -c "
from azure.storage.blob import BlobServiceClient
try:
    client = BlobServiceClient.from_connection_string('UseDevelopmentStorage=true')
    container = client.get_container_client('graphify-out')
    blobs = [b for b in container.list_blobs() if 'graph/' in b.name]
    print('   --- Local (Azurite - devstoreaccount1) ---')
    if not blobs:
        print('      (No graph artifacts found)')
    else:
        for b in sorted(blobs, key=lambda x: x.last_modified, reverse=True)[:5]:
            dt = b.last_modified.strftime('%Y-%m-%d %H:%M')
            print('      - ' + str(b.name) + ' (Modified: ' + str(dt) + ')')
except Exception as e: print('      (No graph artifacts found)')
"



# 4.2. Cloud (Azure) - independent of Docker
$PYTHON_CMD -c "
import os
from azure.storage.blob import BlobServiceClient

def get_account_name(conn_str):
    parts = dict(x.split('=', 1) for x in conn_str.split(';') if '=' in x)
    return parts.get('AccountName', 'Unknown')

cloud_conn = None
for var in ['AZURE_STORAGE_CONNECTION_STRING_CLOUD', 'CLOUD_STORAGE_CONNECTION_STRING', 'AZURE_STORAGE_CONNECTION_STRING']:
    val = os.environ.get(var)
    if val and not any(x in val.lower() for x in ['devstoreaccount1', 'usedevelopmentstorage=true', '127.0.0.1', 'localhost']):
        cloud_conn = val
        break

if cloud_conn:
    try:
        acct = get_account_name(cloud_conn)
        client = BlobServiceClient.from_connection_string(cloud_conn)
        container = client.get_container_client('graphify-out')
        blobs = [b for b in container.list_blobs() if 'graph/' in b.name]
        print(f'   --- Cloud (Azure - {acct}) ---')
        if not blobs:
            print('      (No graph artifacts found)')
        else:
            for b in sorted(blobs, key=lambda x: x.last_modified, reverse=True)[:5]:
                date_str = b.last_modified.strftime('%Y-%m-%d %H:%M')
                print(f'      - {b.name} (Modified: {date_str})')

    except Exception as e:
        print('   --- Cloud (Azure) ---')
        print(f'      [ERROR] {e}')
else:
    print('   --- Cloud (Azure) ---')
    print('      [!] Cloud connection string not found in environment.')
"

echo -e "\n🚨 [HUNT] Suspicious Generic Relations (The 'Noise'):"
$PYTHON_CMD -c "
from src.graph.cosmos_manager import CosmosGremlinManager
import asyncio
async def run():
    mgr = CosmosGremlinManager()
    try:
        triples = await mgr.execute_query('g.E().project(\"out\", \"rel\", \"in\").by(outV().values(\"label\")).by(label()).by(inV().values(\"label\"))')
        specific = [t for t in triples if t['rel'] != 'OTHER_RELATION']
        if not specific:
            print('   -> [!] ALERT: 100% of relations are generic (related_to).')
        else:
            print(f'   -> Found {len(specific)} specific relations:')
            for t in specific[:5]: print(f'      {t[\"out\"]} --({t[\"rel\"]})--> {t[\"in\"]}')
    finally: await mgr.close()
asyncio.run(run())
"

echo -e "\n===================================================="
echo "✅ Statistics retrieval complete."
echo "===================================================="