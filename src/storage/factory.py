from __future__ import annotations

import threading
from typing import Any

from src.storage.interfaces import IArtifactStore
from src.storage.azure_store import AzureBlobArtifactStore
from src.utils.config import settings


def _should_use_azure_storage() -> bool:
    # Always run in Azure Storage interface mode (Azure Blob / Azurite)
    return True


def _build_azure_store() -> AzureBlobArtifactStore:
    connection_string = settings.azure_storage_connection_string

    is_prod = (
        settings.strict_cloud_runtime
        or (settings.storage_type and settings.storage_type.lower() == "azure")
    )

    if not connection_string or "azurite:" in connection_string:
        if is_prod and connection_string and "azurite:" not in connection_string:
            pass
        else:
            connection_string = "UseDevelopmentStorage=true"


    container = settings.azure_storage_container or "graphify-out"
    return AzureBlobArtifactStore(
        connection_string=connection_string,
        container_name=container,
    )


_ARTIFACT_STORE: IArtifactStore | None = None
_ARTIFACT_STORE_LOCK = threading.Lock()

_SEARCH_MANAGER: Any | None = None
_SEARCH_MANAGER_LOCK = threading.Lock()

_COSMOS_MANAGER: Any | None = None
_COSMOS_MANAGER_LOCK = threading.Lock()


def get_artifact_store() -> IArtifactStore:
    global _ARTIFACT_STORE
    if _ARTIFACT_STORE is None:
        with _ARTIFACT_STORE_LOCK:
            if _ARTIFACT_STORE is None:
                if _should_use_azure_storage():
                    _ARTIFACT_STORE = _build_azure_store()
                else:
                    raise RuntimeError(
                        "Azure artifact storage is required. Set STORAGE_TYPE=azure and provide an Azure storage connection string."
                    )
    return _ARTIFACT_STORE


def get_search_manager() -> Any:
    global _SEARCH_MANAGER
    if _SEARCH_MANAGER is None:
        with _SEARCH_MANAGER_LOCK:
            if _SEARCH_MANAGER is None:
                from src.storage.search_manager import SearchManager

                _SEARCH_MANAGER = SearchManager()
    return _SEARCH_MANAGER


def get_cosmos_manager() -> Any:
    global _COSMOS_MANAGER
    if _COSMOS_MANAGER is None:
        with _COSMOS_MANAGER_LOCK:
            if _COSMOS_MANAGER is None:
                from src.graph.cosmos_manager import CosmosGremlinManager

                _COSMOS_MANAGER = CosmosGremlinManager()
    return _COSMOS_MANAGER
