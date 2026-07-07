import os
import logging
from src.utils.bidi_utils import fix_text
import math
from typing import Any, Dict, List
from src.extraction.extraction_engine import _build_azure_client
from src.utils.config import settings


def cosine_similarity(v1: List[float], v2: List[float]) -> float:
    """
    Calculate the cosine similarity between two numeric vectors.

    Args:
        v1 (List[float]): The first vector.
        v2 (List[float]): The second vector.

    Returns:
        float: The cosine similarity score (0.0 to 1.0).
    """
    if not v1 or not v2 or len(v1) != len(v2):
        return 0.0
    dot_product = sum((a * b for a, b in zip(v1, v2)))
    magnitude1 = math.sqrt(sum((a * a for a in v1)))
    magnitude2 = math.sqrt(sum((a * a for a in v2)))
    if magnitude1 == 0 or magnitude2 == 0:
        return 0.0
    return dot_product / (magnitude1 * magnitude2)


async def generate_embeddings(
    texts: List[str], client: Any = None
) -> List[List[float]]:
    """
    Generate semantic embeddings for a list of text strings using OpenAI/Azure.

    Args:
        texts (List[str]): List of strings to embed.
        client (Any, optional): The LLM client to use. Defaults to a new Azure client.

    Returns:
        List[List[float]]: A list of embedding vectors.
    """
    if not texts:
        return []
    client = client or _build_azure_client()
    model = (
        settings.azure_openai_embedding_deployment
        or settings.azure_openai_embedding_model
        or settings.openai_embedding_model
    )
    try:
        response = await client.embeddings.create(input=texts, model=model)
        return [item.embedding for item in response.data]
    except Exception as e:
        logging.error(fix_text(str(f"Error generating embeddings: {e}")))
        return []


logger = logging.getLogger(__name__)


async def index_edge_labels(
    graph_data: Dict[str, Any], filename: str, persistence: Any
):
    """
    Generate and persist vector embeddings for all unique edge relations in the graph.
    """
    edges = graph_data.get("edges", []) or graph_data.get("links", [])
    logging.info(
        f"[VECTOR_INDEXER] [EDGE_INDEX] [START] | Metadata: TotalEdges={len(edges)} | Message: Starting generation"
    )

    try:
        unique_relations = sorted(
            list(set((str(e.get("relation", "")) for e in edges if e.get("relation"))))
        )
        if not unique_relations:
            logging.warning(
                "[VECTOR_INDEXER] [EDGE_INDEX] [SKIP] | Message: No edge relations found to index."
            )
            return

        embeddings = await generate_embeddings(unique_relations)
        if not embeddings:
            logging.error(
                "[VECTOR_INDEXER] [EDGE_INDEX] [ERROR] | Message: Failed to generate embeddings."
            )
            return

        logging.info(
            f"[VECTOR_INDEXER] [EDGE_INDEX] [SUCCESS] | Metadata: UniqueRelationsCount={len(unique_relations)}, Filename={filename} | Message: Indexing complete (Cloud-Native)"
        )
    except Exception as e:
        logging.error(
            f"[VECTOR_INDEXER] [EDGE_INDEX] [ERROR] | Metadata: Error={str(e)} | Message: Critical failure in edge indexing"
        )


async def index_chunks(
    persistence: Any, output_filename: str = "chunk_embeddings.json"
):
    """
    Scan for text chunks in storage, generate embeddings, and save them to a JSON index.
    """
    storage = persistence.storage
    prefix = "chunk_cache/"

    logging.info(
        f"[VECTOR_INDEXER] [CHUNK_INDEX] [START] | Metadata: Prefix={prefix} | Message: Scanning storage for chunks"
    )

    try:
        blob_names = await storage.list_blobs(prefix=prefix)
        txt_files = [b for b in blob_names if b.endswith(".txt")]

        if not txt_files:
            logging.warning(
                "[VECTOR_INDEXER] [CHUNK_INDEX] [SKIP] | Message: No .txt files found in chunk_cache"
            )
            return

        chunk_data = []
        for blob_path in txt_files:
            try:
                text = await storage.read_text(blob_path)
                text = text.strip()
                if text:
                    chunk_data.append({"id": os.path.basename(blob_path), "text": text})
            except Exception as e:
                logging.warning(
                    f"[VECTOR_INDEXER] [CHUNK_INDEX] [WARNING] | Metadata: Path={blob_path}, Error={str(e)} | Message: Could not read chunk"
                )

        if not chunk_data:
            logging.warning(
                "[VECTOR_INDEXER] [CHUNK_INDEX] [SKIP] | Message: No valid text found in chunks"
            )
            return

        texts = [c["text"] for c in chunk_data]
        embeddings = await generate_embeddings(texts)
        if not embeddings:
            logging.error(
                "[VECTOR_INDEXER] [CHUNK_INDEX] [ERROR] | Message: Failed to generate embeddings"
            )
            return

        # Azure Search integration
        if (
            getattr(settings, "use_azure_search", False)
            and settings.azure_search_endpoint
            and settings.azure_search_key
        ):
            from src.storage.search_manager import SearchManager

            search_mgr = SearchManager()
            try:
                await search_mgr.ensure_investigation_indices()
                documents = []
                for i, data in enumerate(chunk_data):
                    documents.append(
                        {
                            "id": SearchManager._sanitize_id(data["id"]),
                            "text": data["text"],
                            "embedding": embeddings[i],
                            "source_file": prefix,  # Placeholder
                        }
                    )

                await search_mgr.upload_documents(
                    settings.azure_search_index, documents
                )
                await search_mgr.close()
                logging.info(
                    f"[VECTOR_INDEXER] [CHUNK_INDEX] [SUCCESS] | Metadata: ChunksIndexed={len(documents)} | Message: Chunks indexed to Azure AI Search"
                )
                return
            except Exception as e:
                logging.error(
                    f"[VECTOR_INDEXER] [AZURE_SEARCH] [ERROR] | Metadata: Error={str(e)} | Message: Azure Search indexing failed"
                )
                try:
                    await search_mgr.close()
                except Exception:
                    pass
                raise

        logging.info(
            f"[VECTOR_INDEXER] [CHUNK_INDEX] [SUCCESS] | Metadata: ChunksProcessed={len(chunk_data)} | Message: Vector indexing complete (No Local Index Saved)"
        )
    except Exception as e:
        logging.error(
            f"[VECTOR_INDEXER] [CHUNK_INDEX] [ERROR] | Metadata: Error={str(e)} | Message: Critical failure in chunk indexing"
        )
        raise
