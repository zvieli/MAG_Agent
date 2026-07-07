import os
import sys
import asyncio
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.utils.config import settings

def mask(val: str | None) -> str:
    if not val:
        return "MISSING"
    if val.startswith("@Microsoft.KeyVault"):
        return f"UNRESOLVED KEYVAULT REFERENCE ({val})"
    if len(val) <= 8:
        return "****"
    return f"{val[:4]}...{val[-4:]}"

async def main():
    print("=" * 60)
    print("🔍 AZURE FUNCTION APP DIAGNOSTICS SCRIPT")
    print("=" * 60)
    
    print("\n--- Environment Variables (Pydantic Settings) ---")
    print(f"AZURE_OPENAI_ENDPOINT: {settings.azure_openai_endpoint or 'MISSING'}")
    print(f"AZURE_OPENAI_API_KEY:  {mask(settings.azure_openai_api_key)}")
    print(f"AZURE_OPENAI_API_VERSION: {settings.azure_openai_api_version or 'MISSING'}")
    print(f"AZURE_OPENAI_EMBEDDING_DEPLOYMENT: {settings.azure_openai_embedding_deployment or 'MISSING'}")
    
    print(f"\nAZURE_SEARCH_ENDPOINT: {settings.azure_search_endpoint or 'MISSING'}")
    print(f"AZURE_SEARCH_KEY:      {mask(settings.azure_search_key)}")
    print(f"AZURE_SEARCH_INDEX:    {settings.azure_search_index or 'MISSING'}")
    
    print(f"\nAZURE_STORAGE_CONNECTION_STRING: {mask(settings.azure_storage_connection_string)}")
    print(f"AzureStorageConnection:          {mask(os.environ.get('AzureStorageConnection'))}")
    print(f"AzureWebJobsStorage:             {mask(os.environ.get('AzureWebJobsStorage'))}")

    print(f"\nCOSMOS_GREMLIN_ENDPOINT: {settings.cosmos_gremlin_endpoint or 'MISSING'}")
    print(f"COSMOS_DATABASE:         {settings.cosmos_database or 'MISSING'}")
    print(f"COSMOS_GRAPH:            {settings.cosmos_graph or 'MISSING'}")
    print(f"COSMOS_GREMLIN_KEY:      {mask(settings.cosmos_gremlin_key)}")

    # Check for Key Vault un-resolved values in env
    print("\n--- Key Vault Resolution Check ---")
    all_ok = True
    for key, val in os.environ.items():
        if val and val.startswith("@Microsoft.KeyVault"):
            print(f"❌ Alert: Variable '{key}' contains an unresolved Key Vault reference: {val}")
            all_ok = False
    if all_ok:
        print("✅ No unresolved Key Vault references found in raw os.environ.")

    print("\n--- Live Connection Probes ---")
    from src.operations.dependency_checker import DependencyChecker
    checker = DependencyChecker()
    
    try:
        print("Probing storage...")
        storage_res = await checker._check_storage_config()
        print(f"✅ Storage config check passed: {storage_res}")
    except Exception as e:
        print(f"❌ Storage check failed: {e}")
        
    try:
        print("Probing Azure OpenAI...")
        openai_res = await checker._check_openai()
        print(f"✅ OpenAI check passed: {openai_res}")
    except Exception as e:
        print(f"❌ OpenAI check failed: {e}")
        
    try:
        print("Probing Azure AI Search...")
        search_res = await checker._check_search()
        print(f"✅ Search check passed: {search_res}")
    except Exception as e:
        print(f"❌ Search check failed: {e}")

    try:
        print("Probing Cosmos DB (Gremlin)...")
        # Run with a strict timeout wrapper as Gremlin client connecting can block indefinitely
        cosmos_res = await asyncio.wait_for(checker._check_cosmos(), timeout=10.0)
        print(f"✅ Cosmos DB check passed: {cosmos_res}")
    except asyncio.TimeoutError:
        print("❌ Cosmos DB check failed: Timed out after 10.0s (possible firewall/port 443 block)")
    except Exception as e:
        print(f"❌ Cosmos DB check failed: {e}")

    await checker.close()
    print("\n" + "=" * 60)

if __name__ == "__main__":
    asyncio.run(main())
