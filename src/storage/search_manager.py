from __future__ import annotations

import logging
import asyncio
from typing import Any, Dict, List, Optional, TYPE_CHECKING
from azure.core.credentials import AzureKeyCredential
from azure.search.documents.aio import SearchClient
from azure.search.documents.indexes.aio import SearchIndexClient
from azure.search.documents.indexes.models import (
    SearchIndex,
    SimpleField,
    SearchableField,
    SearchField,
    SearchFieldDataType,
    VectorSearch,
    HnswAlgorithmConfiguration,
    VectorSearchProfile,
    ExhaustiveKnnAlgorithmConfiguration,
    ExhaustiveKnnParameters,
)
from src.utils.config import settings
from src.tools.contracts import SearchQueryInput, SearchQueryResult, ToolExecutionError

logger = logging.getLogger(__name__)


class SearchManager:
    """
    Manages Azure AI Search indices and operations for the investigation pipeline.
    Supports hybrid search (text + vector) for document chunks, graph nodes, and edges.
    Requires an explicit API key for authentication.
    """

    def __init__(self):
        self.endpoint = settings.azure_search_endpoint
        self.key = settings.azure_search_key

        if not self.endpoint:
            error_msg = "AZURE_SEARCH_ENDPOINT is required for SearchManager."
            logging.error(f"[SEARCH_MANAGER] [INIT] [ERROR] | Message: {error_msg}")
            raise RuntimeError(error_msg)

        if self.key:
            self.credential = AzureKeyCredential(self.key)
        else:
            try:
                from azure.identity.aio import DefaultAzureCredential
                self.credential = DefaultAzureCredential()
                logging.info("[SEARCH_MANAGER] Authenticating using DefaultAzureCredential (Managed Identity)")
            except ImportError:
                error_msg = "AZURE_SEARCH_KEY is missing, and azure-identity is not installed for Managed Identity fallback."
                logging.error(f"[SEARCH_MANAGER] [INIT] [ERROR] | Message: {error_msg}")
                raise RuntimeError(error_msg)

        self.index_client = SearchIndexClient(
            endpoint=self.endpoint,
            credential=self.credential,
            connection_timeout=10,
            read_timeout=30,
        )
        self._search_clients: Dict[str, SearchClient] = {}

    def get_search_client(self, index_name: str) -> SearchClient:
        if index_name not in self._search_clients:
            self._search_clients[index_name] = SearchClient(
                endpoint=self.endpoint,
                index_name=index_name,
                credential=self.credential,
                connection_timeout=10,
                read_timeout=30,
            )
        return self._search_clients[index_name]

    async def create_index_if_not_exists(
        self,
        index_name: str,
        fields: List[Any],
        vector_config: Optional[VectorSearch] = None,
    ):
        """Creates an Azure AI Search index if it does not already exist."""
        try:
            await self.index_client.get_index(index_name)
            logging.info(
                f"[SEARCH_MANAGER] [INDEX_CHECK] [SUCCESS] | Metadata: Index={index_name} | Message: Index already exists"
            )
        except Exception:
            logging.info(
                f"[SEARCH_MANAGER] [INDEX_CREATE] [START] | Metadata: Index={index_name} | Message: Index not found, creating..."
            )
            index = SearchIndex(
                name=index_name, fields=fields, vector_search=vector_config
            )
            await self.index_client.create_index(index)
            logging.info(
                f"[SEARCH_MANAGER] [INDEX_CREATE] [SUCCESS] | Metadata: Index={index_name} | Message: Index created successfully"
            )

    async def get_default_vector_config(self) -> VectorSearch:
        """Returns the default vector search configuration for HNSW."""
        return VectorSearch(
            algorithms=[
                HnswAlgorithmConfiguration(name="myHnsw"),
                ExhaustiveKnnAlgorithmConfiguration(
                    name="myExhaustiveKnn",
                    parameters=ExhaustiveKnnParameters(metric="cosine"),
                ),
            ],
            profiles=[
                VectorSearchProfile(
                    name="myHnswProfile", algorithm_configuration_name="myHnsw"
                ),
                VectorSearchProfile(
                    name="myExhaustiveKnnProfile",
                    algorithm_configuration_name="myExhaustiveKnn",
                ),
            ],
        )

    async def ensure_investigation_indices(self):
        """Ensures that all required indices for the investigation are initialized."""
        vector_config = await self.get_default_vector_config()

        # 1. Chunks Index
        chunk_fields = [
            SimpleField(name="id", type=SearchFieldDataType.String, key=True),
            SearchableField(
                name="text",
                type=SearchFieldDataType.String,
                analyzer_name="he.microsoft",
            ),
            SearchableField(
                name="source_file", type=SearchFieldDataType.String, filterable=True
            ),
            SearchField(
                name="embedding",
                type=SearchFieldDataType.Collection(SearchFieldDataType.Single),
                searchable=True,
                vector_search_dimensions=1536,
                vector_search_profile_name="myHnswProfile",
            ),
        ]
        await self.create_index_if_not_exists(
            settings.azure_search_index, chunk_fields, vector_config
        )

        # Deleting graphify-nodes and graphify-edges indices from search client (Step 2: Clean and standardize Retrieval Layers)
        for idx_name in [settings.azure_search_index_nodes, settings.azure_search_index_edges]:
            try:
                await self.index_client.delete_index(idx_name)
                logging.info(f"[SEARCH_MANAGER] Deleted old entity search index: {idx_name}")
            except Exception as e:
                logging.debug(f"[SEARCH_MANAGER] Info: Old index {idx_name} deletion skipped: {e}")

    async def upload_documents(self, index_name: str, documents: List[Dict[str, Any]]):
        """Uploads a batch of documents to a specific index with retries."""
        if not documents:
            return

        for doc in documents:
            doc.pop("source_file", None)

        logging.info(
            f"[SEARCH_MANAGER] [UPLOAD] [START] | Metadata: Index={index_name}, Count={len(documents)} | Message: Uploading documents"
        )
        
        max_attempts = 5
        backoff = 1.0
        
        search_client = self.get_search_client(index_name)
        for attempt in range(max_attempts):
            try:
                results = await search_client.upload_documents(documents=documents)
                success_count = sum(1 for r in results if r.succeeded)
                logging.info(
                    f"[SEARCH_MANAGER] [UPLOAD] [SUCCESS] | Metadata: Index={index_name}, Success={success_count} | Message: Batch upload complete"
                )
                return
            except Exception as e:
                if attempt == max_attempts - 1:
                    logging.error(
                        f"[SEARCH_MANAGER] [UPLOAD] [ERROR] | Metadata: Index={index_name}, Error={str(e)} | Message: Failed to upload documents after {max_attempts} attempts"
                    )
                    raise
                sleep_time = backoff * (2 ** attempt)
                logging.warning(
                    f"[SEARCH_MANAGER] [UPLOAD] [RETRY] | Index={index_name}, Error={str(e)}. Retrying in {sleep_time:.1f}s (attempt {attempt + 1}/{max_attempts})...."
                )
                await asyncio.sleep(sleep_time)

    async def close(self):
        """Closes the underlying clients."""
        await self.index_client.close()
        for client in self._search_clients.values():
            await client.close()
        self._search_clients.clear()

    @staticmethod
    def _sanitize_id(doc_id: str) -> str:
        """Sanitizes document ID to be Azure Search compatible."""
        import re

        # Allow only letters, numbers, dashes, underscores
        return re.sub(r"[^a-zA-Z0-9_-]", "_", doc_id)

    async def execute_search_query(
        self,
        payload: Dict[str, Any] | SearchQueryInput,
    ) -> SearchQueryResult | ToolExecutionError:
        """Executes document search with Pydantic input/output contract enforcement."""
        import time
        from src.tools.contracts import (
            SearchQueryInput,
            SearchQueryResult,
            SearchChunkItem,
            ToolExecutionError,
        )

        start_time = time.time()
        try:
            input_data = (
                payload if isinstance(payload, SearchQueryInput) else SearchQueryInput.model_validate(payload)
            )
        except Exception as err:
            return ToolExecutionError(
                error_code="INVALID_TOOL_PAYLOAD",
                error_message=f"Search payload validation failed: {err}",
                tool_name="azure_ai_search",
                retryable=False,
            )

        try:
            client = self.get_search_client(settings.azure_search_index)
            filter_str = f"doc_id eq '{input_data.doc_id}'" if input_data.doc_id else None
            results = await client.search(
                search_text=input_data.query,
                top=input_data.top_k,
                filter=filter_str,
            )
            chunks: List[SearchChunkItem] = []
            async for item in results:
                score = float(item.get("@search.score", 0.0))
                if score >= input_data.min_score:
                    chunks.append(
                        SearchChunkItem(
                            chunk_id=str(item.get("id", "")),
                            doc_id=str(item.get("doc_id", input_data.doc_id)),
                            text=str(item.get("text", "")),
                            score=score,
                            source_location=item.get("source_location"),
                        )
                    )
            duration_ms = round((time.time() - start_time) * 1000, 2)
            return SearchQueryResult(
                doc_id=input_data.doc_id,
                total_found=len(chunks),
                chunks=chunks,
                duration_ms=duration_ms,
            )
        except Exception as e:
            duration_ms = round((time.time() - start_time) * 1000, 2)
            return ToolExecutionError(
                error_code="SEARCH_EXECUTION_FAILED",
                error_message=str(e),
                tool_name="azure_ai_search",
                doc_id=input_data.doc_id,
                retryable=True,
                details={"duration_ms": duration_ms},
            )

