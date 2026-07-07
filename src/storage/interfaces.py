from __future__ import annotations

from abc import ABC, abstractmethod
from typing import List


class IArtifactStore(ABC):
    @abstractmethod
    def read_bytes(self, path: str) -> bytes:
        raise NotImplementedError

    @abstractmethod
    def write_bytes(self, path: str, data: bytes) -> None:
        raise NotImplementedError

    @abstractmethod
    def exists(self, path: str) -> bool:
        raise NotImplementedError

    @abstractmethod
    def list_files(self, prefix: str) -> List[str]:
        raise NotImplementedError

    @abstractmethod
    def delete(self, path: str) -> None:
        raise NotImplementedError

    @abstractmethod
    def delete_folder(self, prefix: str) -> None:
        raise NotImplementedError
