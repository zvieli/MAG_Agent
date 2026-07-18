import sys
import os

# Auto-execute using the project's virtual environment if run globally
venv_python = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".venv", "bin", "python")
if not sys.prefix.endswith(".venv") and os.path.exists(venv_python) and sys.executable != venv_python:
    os.execv(venv_python, [venv_python] + sys.argv)

import asyncio
from src.graph.cosmos_manager import CosmosGremlinManager

async def inspect_schema():
    cosmos_mgr = CosmosGremlinManager()
    
    print("--- Inspecting Graph Schema ---")
    
    # 1. שימוש ב-valueMap(true) במקום elementMap()
    # ערך true בתוך valueMap מבטיח שתקבל גם את ה-id וה-label
    node_query = "g.V().limit(1).valueMap(true)"
    node_result = await cosmos_mgr.execute_query(node_query)
    print(f"\n[Node Schema Sample]:\n{node_result}")
    
    # 2. כנ"ל עבור ה-Edges
    edge_query = "g.E().limit(1).valueMap(true)"
    edge_result = await cosmos_mgr.execute_query(edge_query)
    print(f"\n[Edge Schema Sample]:\n{edge_result}")
    
    # 3. רשימת כל סוגי ה-Labels הקיימים (כדי להבין את ה"סכמה")
    labels_query = "g.V().label().dedup()"
    labels = await cosmos_mgr.execute_query(labels_query)
    print(f"\n[Node Labels found]:\n{labels}")

    await cosmos_mgr.close()


async def analyze_pk_distribution():
    cosmos_mgr = CosmosGremlinManager()
    
    # שליפת המיפוי: {pk: count}
    query = "g.V().group().by('pk').by(count())"
    distribution = await cosmos_mgr.execute_query(query)
    
    print("--- Pipeline Distribution (PK vs Node Count) ---")
    # ה-Cosmos מחזיר את זה כמילון בתוך רשימה
    if distribution:
        stats = distribution[0]
        # מיון לפי כמות ה-Nodes (מהגדול לקטן)
        sorted_stats = sorted(stats.items(), key=lambda item: item[1], reverse=True)
        
        for pk, count in sorted_stats:
            print(f"PK: {pk:<25} | Nodes: {count}")
    
    await cosmos_mgr.close()


async def delete_run_by_pk(pk_to_delete):
    cosmos_mgr = CosmosGremlinManager()
    print(f"--- Deleting all nodes and edges for PK: {pk_to_delete} ---")
    
    # 1. מחיקת כל הקשתות (Edges) המקושרות ל-PK הזה
    # בגרף, קשת חייבת להימחק לפני הצומת כדי למנוע שגיאות
    delete_edges = f"g.E().has('partitionKey', '{pk_to_delete}').drop()"
    await cosmos_mgr.execute_query(delete_edges)
    print("Edges deleted.")
    
    # 2. מחיקת כל הצמתים (Nodes) עם ה-PK הזה
    delete_nodes = f"g.V().has('pk', '{pk_to_delete}').drop()"
    await cosmos_mgr.execute_query(delete_nodes)
    print("Nodes deleted.")
    
    print(f"Cleanup for {pk_to_delete} completed.")
if __name__ == "__main__":
    asyncio.run(analyze_pk_distribution())