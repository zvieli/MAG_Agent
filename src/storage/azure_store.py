from __future__ import annotations

import logging
from typing import List, Optional

from azure.storage.blob import BlobServiceClient
from src.storage.interfaces import IArtifactStore


class AzureBlobArtifactStore(IArtifactStore):
    """
    Azure Blob implementation of IArtifactStore.
    Requires an explicit connection_string for authentication.
    """

    def __init__(
        self,
        *,
        connection_string: Optional[str] = None,
        container_name: str = "graphify-out",
    ) -> None:
        self.container_name = container_name

        if not connection_string:
            raise RuntimeError(
                "AZURE_STORAGE_CONNECTION_STRING is required for AzureBlobArtifactStore. "
                "Managed Identity and implicit authentication are not supported in this pass."
            )

        try:
            self._service_client = BlobServiceClient.from_connection_string(
                connection_string
            )
        except Exception as e:
            logging.error(f"Failed to initialize BlobServiceClient: {str(e)}")
            raise RuntimeError(f"Invalid Azure Storage connection string: {str(e)}")

        self._container_client = self._service_client.get_container_client(
            self.container_name
        )
        self._ensure_container_exists()

    def _ensure_container_exists(self) -> None:
        try:
            self._container_client.get_container_properties()
        except Exception:
            try:
                self._container_client.create_container()
            except Exception as e:
                logging.error(f"Failed to create container {self.container_name}: {str(e)}")
                raise

    def _blob_client(self, path: str):
        return self._container_client.get_blob_client(path)

    def read_bytes(self, path: str) -> bytes:
        blob_client = self._blob_client(path)
        stream = blob_client.download_blob()
        return stream.readall()

    def write_bytes(self, path: str, data: bytes) -> None:
        blob_client = self._blob_client(path)
        blob_client.upload_blob(data, overwrite=True)

    def exists(self, path: str) -> bool:
        blob_client = self._blob_client(path)
        try:
            blob_client.get_blob_properties()
            return True
        except Exception:
            return False

    def list_files(self, prefix: str) -> List[str]:
        return [
            blob.name
            for blob in self._container_client.list_blobs(name_starts_with=prefix)
        ]

    def delete(self, path: str) -> None:
        blob_client = self._blob_client(path)
        try:
            blob_client.delete_blob()
        except Exception as e:
            logging.warning(f"Failed to delete blob {path}: {e}")

    def delete_folder(self, prefix: str) -> None:
        blobs = self.list_files(prefix)
        if not blobs:
            return
        
        # Azure Blob Storage doesn't have folders, so we delete all blobs with the prefix
        for blob_name in blobs:
            self.delete(blob_name)
