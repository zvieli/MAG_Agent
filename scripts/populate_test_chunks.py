import asyncio
import json
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.storage.factory import get_artifact_store, get_search_manager
from src.processing.vector_indexer import generate_embeddings
from src.utils.config import settings
from src.storage.search_manager import SearchManager

logging.basicConfig(level=logging.INFO)

async def main():
    store = get_artifact_store()
    doc_id = "281ab247-112b-48b9-bd89-1358a7a1d51c"
    md_path = f"extracted_text/{doc_id}.md"
    
    if not store.exists(md_path):
        logging.error(f"MD file not found: {md_path}")
        return
        
    md_content = store.read_bytes(md_path).decode("utf-8")
    
    # Split content by page headers
    parts = md_content.split("## Page ")
    page_texts = []
    for part in parts:
        part = part.strip()
        if not part:
            continue
        # Split out the page number (first line)
        lines = part.split("\n", 1)
        if len(lines) > 1:
            page_texts.append(lines[1].strip())
        else:
            page_texts.append(part)
            
    if len(page_texts) < 2:
        logging.warning("Failed to split by '## Page ', splitting in half instead.")
        mid = len(md_content) // 2
        page_texts = [md_content[:mid], md_content[mid:]]
        
    chunks = [
        {"text": page_texts[0], "chunk_index": 1},
        {"text": page_texts[1], "chunk_index": 2}
    ]
    
    # Save chunks to blob storage
    for chunk in chunks:
        chunk_id = f"{chunk['chunk_index']:05d}"
        payload = {
            **chunk,
            "doc_id": doc_id,
            "source_blob_path": f"uploads/{doc_id}.pdf"
        }
        payload_bytes = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
        
        # Write to chunks/
        store.write_bytes(f"chunks/{doc_id}/{chunk_id}.json", payload_bytes)
        # Write to investigations/
        store.write_bytes(f"investigations/{doc_id}/{chunk_id}.json", payload_bytes)
        logging.info(f"Wrote chunk {chunk_id} to Blob Storage.")
        
    # Generate embeddings and index in Azure Search
    texts = [c["text"] for c in chunks]
    embeddings = await generate_embeddings(texts)
    
    if not embeddings:
        logging.error("Failed to generate embeddings.")
        return
        
    search_mgr = get_search_manager()
    await search_mgr.ensure_investigation_indices()
    
    documents = []
    for i, chunk in enumerate(chunks):
        documents.append({
            "id": SearchManager._sanitize_id(f"{doc_id}_{chunk['chunk_index']}"),
            "text": chunk["text"],
            "embedding": embeddings[i],
            "source_file": doc_id,
        })
        
    await search_mgr.upload_documents(settings.azure_search_index, documents)
    logging.info("Successfully uploaded chunks to Azure AI Search.")

if __name__ == "__main__":
    asyncio.run(main())
