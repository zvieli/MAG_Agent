import asyncio
import json
from azure.core.credentials import AzureKeyCredential
from azure.search.documents.indexes.aio import SearchIndexClient
from azure.search.documents.aio import SearchClient
from src.utils.config import settings
from src.storage.search_manager import SearchManager

async def inspect_search():
    endpoint = settings.azure_search_endpoint
    key = settings.azure_search_key
    index_name = settings.azure_search_index
    
    print(f"Connecting to Azure AI Search: {endpoint}")
    print(f"Target index: {index_name}\n")
    
    credential = AzureKeyCredential(key)
    
    # 1. Fetch Index Schema Definition
    index_client = SearchIndexClient(endpoint=endpoint, credential=credential)
    try:
        index_def = await index_client.get_index(index_name)
        print("=== 1. Live Index Fields ===")
        for field in index_def.fields:
            print(f"- {field.name}: {field.type} (Key={getattr(field, 'key', False)}, Filterable={getattr(field, 'filterable', False)}, Searchable={getattr(field, 'searchable', False)})")
            if getattr(field, 'analyzer_name', None):
                print(f"  Analyzer: {field.analyzer_name}")
    except Exception as e:
        print(f"Error fetching index definition: {e}")
    finally:
        await index_client.close()

    # 2. Inspect Sample Data
    search_client = SearchClient(endpoint=endpoint, index_name=index_name, credential=credential)
    try:
        print("\n=== 2. Sample Chunk Documents ===")
        results = await search_client.search(search_text="*", top=3)
        async for result in results:
            print(f"\nID: {result.get('id')}")
            print(f"Source File: {result.get('source_file')}")
            print(f"Text Snippet: {result.get('text', '')[:150]}...")
            print(f"Embedding length: {len(result.get('embedding', [])) if result.get('embedding') else 0}")
    except Exception as e:
        print(f"Error reading documents: {e}")
    finally:
        await search_client.close()

if __name__ == "__main__":
    asyncio.run(inspect_search())
