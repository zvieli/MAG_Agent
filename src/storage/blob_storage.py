from abc import ABC, abstractmethod
from pathlib import Path
import tempfile
import logging
from contextlib import asynccontextmanager
from typing import List, AsyncGenerator

try:
    from azure.storage.blob.aio import BlobServiceClient
    from azure.core.exceptions import ResourceNotFoundError
except ImportError:
    BlobServiceClient = None
    ResourceNotFoundError = Exception


class IBlobStorage(ABC):
    @abstractmethod
    async def read_text(self, blob_path: str) -> str:
        """Reads blob content as UTF-8 string."""
        pass

    @abstractmethod
    async def read_bytes(self, blob_path: str) -> bytes:
        """Reads blob content as bytes."""
        pass

    @abstractmethod
    async def write_text(self, blob_path: str, content: str) -> None:
        """Writes string to blob, creating directories."""
        pass

    @abstractmethod
    async def write_bytes(self, blob_path: str, content: bytes) -> None:
        """Writes bytes to blob, creating directories."""
        pass

    @abstractmethod
    async def exists(self, blob_path: str) -> bool:
        """Returns True if blob exists."""
        pass

    @abstractmethod
    async def list_blobs(self, prefix: str = "") -> List[str]:
        """Lists all blobs starting with the given prefix."""
        pass

    @abstractmethod
    @asynccontextmanager
    async def get_temp_path(self, blob_path: str) -> AsyncGenerator[Path, None]:
        """
        Yields a physical Path for the blob.
        For remote storage, downloads to a temp file and uploads back if modified.
        """
        pass


class AzureBlobStorage(IBlobStorage):
    """
    Azure Blob implementation of IBlobStorage.
    Requires an explicit connection_string for authentication.
    """

    def __init__(
        self, connection_string: str = None, container_name: str = "graphify-out"
    ):
        if BlobServiceClient is None:
            raise ImportError(
                "azure-storage-blob is not installed. Please install it to use AzureBlobStorage."
            )

        self.container_name = container_name

        if not connection_string:
            raise RuntimeError(
                "connection_string is required for AzureBlobStorage. "
                "Managed Identity and implicit authentication are not supported in this pass."
            )

        try:
            self.blob_service_client = BlobServiceClient.from_connection_string(
                connection_string
            )
        except Exception as e:
            logging.error(f"Failed to initialize BlobServiceClient: {str(e)}")
            raise RuntimeError(f"Invalid Azure Storage connection string: {str(e)}")

        self.container_client = self.blob_service_client.get_container_client(
            self.container_name
        )
        self._container_created = False

    async def _ensure_container_exists(self):
        if not self._container_created:
            try:
                await self.container_client.get_container_properties()
            except ResourceNotFoundError:
                try:
                    await self.container_client.create_container()
                except Exception as e:
                    logging.error(f"Failed to create container {self.container_name}: {str(e)}")
                    raise
            self._container_created = True

    async def read_text(self, blob_path: str) -> str:
        await self._ensure_container_exists()
        blob_client = self.container_client.get_blob_client(blob_path)
        try:
            download_stream = await blob_client.download_blob()
            data = await download_stream.readall()
            return data.decode("utf-8")
        except ResourceNotFoundError:
            raise FileNotFoundError(
                f"Blob not found: {blob_path} in container {self.container_name}"
            )

    async def read_bytes(self, blob_path: str) -> bytes:
        await self._ensure_container_exists()
        blob_client = self.container_client.get_blob_client(blob_path)
        try:
            download_stream = await blob_client.download_blob()
            return await download_stream.readall()
        except ResourceNotFoundError:
            raise FileNotFoundError(
                f"Blob not found: {blob_path} in container {self.container_name}"
            )

    async def write_text(self, blob_path: str, content: str) -> None:
        await self._ensure_container_exists()
        blob_client = self.container_client.get_blob_client(blob_path)
        await blob_client.upload_blob(content.encode("utf-8"), overwrite=True)

    async def write_bytes(self, blob_path: str, content: bytes) -> None:
        await self._ensure_container_exists()
        blob_client = self.container_client.get_blob_client(blob_path)
        await blob_client.upload_blob(content, overwrite=True)

    async def exists(self, blob_path: str) -> bool:
        await self._ensure_container_exists()
        blob_client = self.container_client.get_blob_client(blob_path)
        try:
            await blob_client.get_blob_properties()
            return True
        except ResourceNotFoundError:
            return False

    async def list_blobs(self, prefix: str = "") -> List[str]:
        await self._ensure_container_exists()
        blobs = []
        async for blob in self.container_client.list_blobs(name_starts_with=prefix):
            blobs.append(blob.name)
        return blobs

    @asynccontextmanager
    async def get_temp_path(self, blob_path: str) -> AsyncGenerator[Path, None]:
        """
        Safe temp file context manager. Downloads blob if exists,
        allows modification, and ensures cleanup.
        """
        suffix = Path(blob_path).suffix
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=True) as tmp:
            tmp_path = Path(tmp.name)

            try:
                if await self.exists(blob_path):
                    data = await self.read_bytes(blob_path)
                    tmp_path.write_bytes(data)
            except Exception:
                pass  # New file

            yield tmp_path

            if tmp_path.exists() and tmp_path.stat().st_size > 0:
                try:
                    data = tmp_path.read_bytes()
                    await self.write_bytes(blob_path, data)
                except Exception:
                    logging.error(f"[BLOB_STORAGE] Failed to write back blob: {blob_path}")
                    raise
