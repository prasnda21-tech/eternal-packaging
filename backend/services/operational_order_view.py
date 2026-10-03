"""Outbound copy for PWA view=operational. Never mutates the stored order."""
from __future__ import annotations

import copy
from typing import Any, Optional

from services.surat_jalan_files import public_file_entry

_INLINE_OPEN_FIELDS = frozenset({"design_files", "payment_files"})
_PROGRESS_BLOB_KEYS = ("data_url", "photo", "photos", "url")


def is_operational_view(view: Optional[str]) -> bool:
    return str(view or "").strip().lower() == "operational"


def _has_open_substitute(entry: dict, field: str) -> bool:
    """True when a real open path remains after data_url is omitted.

    storage_path opens through the stored-file endpoint.
    design/payment id opens through GET /orders/{id}/attachments/{kind}/{file_id}.
    A bare id on sample or proof files is not that endpoint.
    """
    if str(entry.get("storage_path") or "").strip():
        return True
    if field in _INLINE_OPEN_FIELDS and str(entry.get("id") or "").strip():
        return True
    return False


def _file_copy(entry: Any, field: str) -> Any:
    if not isinstance(entry, dict):
        return entry
    out = dict(entry)
    if "data_url" in out and _has_open_substitute(out, field):
        out.pop("data_url", None)
        out.pop("url", None)
    return out


def _strip_progress_blobs(order: dict) -> None:
    for split in order.get("operator_splits") or []:
        if not isinstance(split, dict):
            continue
        entries = split.get("progress_entries")
        if not isinstance(entries, list):
            continue
        cleaned = []
        for entry in entries:
            if not isinstance(entry, dict):
                cleaned.append(entry)
                continue
            item = {k: v for k, v in entry.items() if k not in _PROGRESS_BLOB_KEYS}
            cleaned.append(item)
        split["progress_entries"] = cleaned


def _sj_public_keeping_legacy(files: list) -> list:
    """Same public SJ shape as desktop GET, except legacy inline with no id
    and no storage_path keeps data_url so it can still be opened.
    """
    out = []
    for entry in files:
        if not isinstance(entry, dict):
            continue
        public = public_file_entry(entry)
        file_id = str(entry.get("id") or "").strip()
        storage = str(entry.get("storage_path") or "").strip()
        data_url = str(entry.get("data_url") or "")
        if not file_id and not storage and data_url.startswith("data:"):
            public["data_url"] = entry.get("data_url")
        out.append(public)
    return out


def operational_public_order(order: dict) -> dict:
    """Light response copy. Stored documents are not modified.

    Skips nothing that was already computed on the order. Callers skip
    line-replace and sales-delivery enrichment before calling this.
    """
    if not isinstance(order, dict):
        return order
    public = copy.deepcopy(order)
    public.pop("line_product_replace", None)
    public.pop("sales_delivery_summary", None)
    for field in ("design_files", "payment_files", "sample_files", "delivery_proofs"):
        files = public.get(field)
        if isinstance(files, list):
            public[field] = [_file_copy(entry, field) for entry in files]
    sj = public.get("surat_jalan_files")
    if isinstance(sj, list) and sj:
        public["surat_jalan_files"] = _sj_public_keeping_legacy(sj)
    _strip_progress_blobs(public)
    return public
