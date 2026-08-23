"""Storage provider abstraction.

Minimal contract intentionally mirrors `services.storage` historic surface so
existing callers do NOT change:
    put_object(path, data, content_type) -> dict metadata
    get_object(path) -> (bytes, content_type)
    is_configured() -> bool
    init() -> None (idempotent)

Object keys are opaque strings. Providers MUST NOT alter path semantics
(callers already namespace with `APP_NAME/category/...`).
"""
from __future__ import annotations
from abc import ABC, abstractmethod
from typing import Tuple


class StorageProvider(ABC):
    """Base interface for storage providers."""

    name: str = "base"

    @abstractmethod
    def is_configured(self) -> bool:
        """Return True if provider credentials/config are usable."""

    @abstractmethod
    def init(self) -> None:
        """Idempotent init. Establish sessions / verify credentials. Must
        NOT create public resources."""

    @abstractmethod
    def put_object(self, path: str, data: bytes, content_type: str) -> dict:
        """Upload binary bytes with given content_type. Return metadata dict.
        Failure MUST raise; MUST NOT leave a partial success visible to the
        caller."""

    @abstractmethod
    def get_object(self, path: str) -> Tuple[bytes, str]:
        """Return (bytes, content_type). Raises on missing object."""

    def head_object(self, path: str) -> dict:
        """Return metadata (size, content_type) without body. Default impl:
        do a get. Providers should override for efficiency."""
        data, ct = self.get_object(path)
        return {"size": len(data), "content_type": ct}
