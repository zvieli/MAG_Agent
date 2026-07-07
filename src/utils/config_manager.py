import asyncio
import json
import logging
import os
import time
from pathlib import Path
from typing import Any, Dict, Optional

from azure.core.exceptions import ResourceNotFoundError
from azure.storage.blob.aio import BlobServiceClient


class ConfigManager:
    """
    Centralized Configuration Manager – Local-First, Cloud-Fallback.

    Resolution order for every config request:
      1. In-memory TTL cache
      2. Local filesystem (bundled in Docker image at build-time)
      3. Azure Blob Storage (runtime override / fallback)

    This ensures the application is self-contained at startup while
    preserving the ability to hot-swap configs via Blob Storage in
    production without redeployment.
    """

    _instance = None
    _lock = asyncio.Lock()

    # ── Discover the base path for bundled config files ──
    # The schemas, prompts, and config files are always stored relative to the project root.
    # We resolve this path relative to this file's path (three parent directories up from src/utils/).
    _BASE_DIR = Path(__file__).resolve().parent.parent.parent

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(ConfigManager, cls).__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return

        # Cache structure: { "category/filename": {"content": "...", "timestamp": float} }
        self._cache: Dict[str, Dict[str, Any]] = {}

        # TTL in seconds (default 5 minutes)
        self.ttl_seconds = int(os.environ.get("CONFIG_CACHE_TTL_SECONDS", 300))

        # Azure Storage Settings
        self.container_name = os.environ.get(
            "AZURE_CONFIG_CONTAINER", "graphify-config"
        )

        # Priority: AZURE_CONFIG_STORAGE_CONNECTION_STRING > AZURE_STORAGE_CONNECTION_STRING
        self.connection_string = os.environ.get(
            "AZURE_CONFIG_STORAGE_CONNECTION_STRING"
        ) or os.environ.get("AZURE_STORAGE_CONNECTION_STRING")

        # Feature flag: set to "true" to force cloud-only mode (legacy behaviour)
        self.cloud_only = (
            os.environ.get("CONFIG_CLOUD_ONLY", "false").lower() == "true"
        )

        self.blob_service_client: Optional[BlobServiceClient] = None
        self._initialized = True

        logging.info(
            f"[CONFIG_MANAGER] Initialized. "
            f"Base dir: {self._BASE_DIR} | "
            f"Cloud-only: {self.cloud_only} | "
            f"Cache TTL: {self.ttl_seconds}s | "
            f"Container: {self.container_name}"
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _resolve_local_path(self, filename: str, category: str) -> Optional[Path]:
        """Return the local filesystem path for a config file, or None."""
        local_path = self._BASE_DIR / category / filename
        if local_path.is_file():
            return local_path
        return None

    async def _get_blob_client(self) -> Optional[BlobServiceClient]:
        """Lazy initialization of the BlobServiceClient.

        Returns None (instead of raising) when no connection string is
        available, since local files are the primary source.
        """
        if self.blob_service_client is None:
            if not self.connection_string:
                logging.warning(
                    "[CONFIG_MANAGER] No Azure Storage connection string configured. "
                    "Cloud fetch disabled – relying on local filesystem only."
                )
                return None

            try:
                self.blob_service_client = BlobServiceClient.from_connection_string(
                    self.connection_string
                )
                logging.info(
                    "[CONFIG_MANAGER] BlobServiceClient created using connection string."
                )
            except Exception as e:
                logging.error(
                    f"[CONFIG_MANAGER] [AUTH_ERROR] Failed to init BlobServiceClient "
                    f"from connection string: {e}"
                )
                return None

        return self.blob_service_client

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def get_config(self, filename: str, category: str = "prompts") -> str:
        """
        Retrieves a configuration file (prompt, schema, setting).

        Resolution order:
          1. In-memory TTL cache
          2. Local filesystem  ({base_dir}/{category}/{filename})
          3. Azure Blob Storage ({container}/{category}/{filename})

        Args:
            filename: The name of the file (e.g., 'extraction_prompt.md').
            category: The subfolder category ('prompts', 'schemas', 'settings').

        Returns:
            The content of the file as a string.

        Raises:
            FileNotFoundError: If the file is not found in any source.
        """
        cache_key = f"{category}/{filename}"
        current_time = time.time()

        # ── 1. Check Cache ──
        async with self._lock:
            cached_entry = self._cache.get(cache_key)
            if cached_entry:
                age = current_time - float(cached_entry["timestamp"])
                if age < self.ttl_seconds:
                    logging.debug(
                        f"[CONFIG_MANAGER] [CACHE_HIT] {cache_key} (Age: {age:.1f}s)"
                    )
                    return str(cached_entry["content"])
                else:
                    logging.debug(
                        f"[CONFIG_MANAGER] [CACHE_EXPIRED] {cache_key} "
                        f"(Age: {age:.1f}s > TTL {self.ttl_seconds}s)"
                    )

        content = None
        source = None

        # ── 2. Try Local Filesystem (unless cloud-only mode) ──
        if not self.cloud_only:
            local_path = self._resolve_local_path(filename, category)
            if local_path is not None:
                try:
                    content = local_path.read_text(encoding="utf-8")
                    source = f"local:{local_path}"
                    logging.info(
                        f"[CONFIG_MANAGER] [LOCAL_HIT] Loaded {cache_key} "
                        f"from {local_path}"
                    )
                except Exception as e:
                    logging.warning(
                        f"[CONFIG_MANAGER] [LOCAL_READ_ERROR] {local_path}: {e}"
                    )

        # ── 3. Fallback to Azure Blob Storage ──
        if content is None:
            blob_path = f"{category}/{filename}"
            client = await self._get_blob_client()
            if client:
                try:
                    container_client = client.get_container_client(self.container_name)
                    blob_client = container_client.get_blob_client(blob_path)

                    stream = await blob_client.download_blob()
                    content_bytes = await stream.readall()
                    content = content_bytes.decode("utf-8")
                    source = f"cloud:{self.container_name}/{blob_path}"
                    logging.info(
                        f"[CONFIG_MANAGER] [CLOUD_FETCH_SUCCESS] "
                        f"Fetched {blob_path} from Azure"
                    )
                except ResourceNotFoundError:
                    logging.warning(
                        f"[CONFIG_MANAGER] [CLOUD_FETCH_MISSING] "
                        f"Blob not found: {blob_path}"
                    )
                except Exception as e:
                    logging.error(
                        f"[CONFIG_MANAGER] [CLOUD_FETCH_ERROR] "
                        f"Unexpected error fetching {blob_path}: {e}"
                    )

        # ── 4. All sources exhausted ──
        if content is None:
            local_searched = self._BASE_DIR / category / filename
            error_msg = (
                f"Configuration file '{filename}' not found in any source. "
                f"Searched: local={local_searched}, "
                f"cloud={self.container_name}/{category}/{filename}"
            )
            logging.critical(f"[CONFIG_MANAGER] [MISSING_CONFIG] {error_msg}")
            raise FileNotFoundError(error_msg)

        # ── 5. Update Cache ──
        async with self._lock:
            self._cache[cache_key] = {"content": content, "timestamp": current_time}

        logging.debug(f"[CONFIG_MANAGER] [RESOLVED] {cache_key} via {source}")
        return content

    async def get_json_config(self, filename: str, category: str = "schemas") -> Any:
        """Helper to fetch and parse a JSON configuration file."""
        content = await self.get_config(filename, category)
        try:
            return json.loads(content)
        except json.JSONDecodeError as e:
            logging.error(
                f"[CONFIG_MANAGER] [JSON_PARSE_ERROR] Failed to parse {category}/{filename}: {e}"
            )
            raise

    async def close(self):
        """Close Azure connections."""
        if self.blob_service_client:
            await self.blob_service_client.close()


# Singleton instance
config_manager = ConfigManager()
