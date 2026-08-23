"""Emergent Object Storage provider (legacy / current production).

Wraps the pre-migration behavior. Kept intact for backward compatibility until
a full cutover to S3 is completed. Do NOT add features here — new work should
go through the S3 provider.
"""
from __future__ import annotations
import os
import logging
from typing import Tuple
import requests

from .base import StorageProvider

logger = logging.getLogger(__name__)


class EmergentStorageProvider(StorageProvider):
    name = "emergent"

    def __init__(self) -> None:
        self._storage_key: str | None = None

    def _base(self) -> str:
        return (os.environ.get("INTEGRATION_PROXY_URL") or "").strip() or "https://integrations.emergentagent.com"

    def _url(self) -> str:
        return self._base().rstrip("/") + "/objstore/api/v1/storage"

    def _emergent_key(self) -> str | None:
        return os.environ.get("EMERGENT_LLM_KEY")

    def is_configured(self) -> bool:
        return bool(self._emergent_key())

    def init(self) -> None:
        if self._storage_key:
            return
        key = self._emergent_key()
        if not key:
            logger.warning("EMERGENT_LLM_KEY not set — Emergent object storage disabled.")
            return
        resp = requests.post(f"{self._url()}/init", json={"emergent_key": key}, timeout=30)
        resp.raise_for_status()
        self._storage_key = resp.json()["storage_key"]
        logger.info("Emergent object storage initialized")

    def _require_key(self) -> str:
        self.init()
        if not self._storage_key:
            raise RuntimeError("Object storage tidak terkonfigurasi (EMERGENT_LLM_KEY belum di-set)")
        return self._storage_key

    def put_object(self, path: str, data: bytes, content_type: str) -> dict:
        key = self._require_key()
        resp = requests.put(
            f"{self._url()}/objects/{path}",
            headers={"X-Storage-Key": key, "Content-Type": content_type},
            data=data,
            timeout=120,
        )
        resp.raise_for_status()
        return resp.json()

    def get_object(self, path: str) -> Tuple[bytes, str]:
        key = self._require_key()
        resp = requests.get(
            f"{self._url()}/objects/{path}",
            headers={"X-Storage-Key": key},
            timeout=60,
        )
        resp.raise_for_status()
        return resp.content, resp.headers.get("Content-Type", "application/octet-stream")
