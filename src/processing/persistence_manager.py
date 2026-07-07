import json
import logging
from typing import Any
from contextlib import asynccontextmanager
from src.storage.blob_storage import IBlobStorage

logger = logging.getLogger(__name__)


class PersistenceManager:
    """
    Centralized manager for persisting graph data and analytical artifacts.
    """

    def __init__(self, storage: IBlobStorage, base_blob_path: str = ""):
        self.storage = storage
        self.base_blob_path = base_blob_path.rstrip("/")

    def _get_blob_path(self, filename: str) -> str:
        return f"{self.base_blob_path}/{filename}" if self.base_blob_path else filename

    async def save_json(self, data: Any, filename: str, indent: int = 2):
        blob_path = self._get_blob_path(filename)
        try:
            json_string = json.dumps(data, indent=indent, ensure_ascii=False)
            await self.storage.write_text(blob_path, json_string)
            logger.info(
                f"[PERSISTENCE] [SAVE_JSON] [SUCCESS] | Metadata: Filename={filename}"
            )
        except Exception as e:
            logger.error(
                f"[PERSISTENCE] [SAVE_JSON] [ERROR] | Metadata: Filename={filename}, Error={str(e)}"
            )

    async def save_text(self, content: str, filename: str):
        blob_path = self._get_blob_path(filename)
        try:
            await self.storage.write_text(blob_path, content)
            logger.info(
                f"[PERSISTENCE] [SAVE_TEXT] [SUCCESS] | Metadata: Filename={filename}"
            )
        except Exception as e:
            logger.error(
                f"[PERSISTENCE] [SAVE_TEXT] [ERROR] | Metadata: Filename={filename}, Error={str(e)}"
            )

    async def save_stream(self, stream: Any, filename: str):
        blob_path = self._get_blob_path(filename)
        try:
            if hasattr(stream, "getvalue"):
                content = stream.getvalue()
            elif hasattr(stream, "read"):
                content = stream.read()
            else:
                content = str(stream)

            await self.storage.write_text(blob_path, str(content))
            logger.info(
                f"[PERSISTENCE] [SAVE_STREAM] [SUCCESS] | Metadata: Filename={filename}"
            )
        except Exception as e:
            logger.error(
                f"[PERSISTENCE] [SAVE_STREAM] [ERROR] | Metadata: Filename={filename}, Error={str(e)}"
            )

    async def save_artifact(self, data: Any, filename: str):
        if isinstance(data, (dict, list)):
            await self.save_json(data, filename)
        else:
            await self.save_text(str(data), filename)

    @asynccontextmanager
    async def get_temp_path(self, filename: str):
        blob_path = self._get_blob_path(filename)
        async with self.storage.get_temp_path(blob_path) as path:
            yield path
