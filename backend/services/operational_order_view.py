"""Outbound copy for PWA view=operational. Never mutates the stored order."""
from __future__ import annotations

import copy
from typing import Any, Optional

from services.surat_jalan_files import public_file_entry

_INLINE_OPEN_FIELDS = frozenset({"design_files", "payment_files"})
_PROGRESS_BLOB_KEYS = ("data_url", "photo", "photos", "url")


def is_operational_view(view: Optional[str]) -> bool:
    return str(view or "").strip().lower() == "operational"


def _nonempty_expr(value_expr: str) -> dict:
    return {
        "$gt": [
            {"$strLenCP": {
                "$convert": {"input": value_expr, "to": "string", "onError": "", "onNull": ""},
            }},
            0,
        ]
    }


def _without_keys_expr(doc_expr: str, keys: tuple) -> dict:
    return {
        "$arrayToObject": {
            "$filter": {
                "input": {"$objectToArray": doc_expr},
                "as": "kv",
                "cond": {"$not": [{"$in": ["$$kv.k", list(keys)]}]},
            }
        }
    }


def _map_files_expr(field: str, drop_when: dict) -> dict:
    """Drop data_url/url only when another open or evidence path remains."""
    return {
        "$map": {
            "input": f"${field}",
            "as": "file",
            "in": {
                "$cond": [
                    {"$ne": [{"$type": "$$file"}, "object"]},
                    "$$file",
                    {
                        "$cond": [
                            drop_when,
                            _without_keys_expr("$$file", ("data_url", "url")),
                            "$$file",
                        ]
                    },
                ]
            },
        }
    }


def _present_array_expr(field: str, mapped: dict) -> dict:
    return {
        "$cond": [
            {"$isArray": f"${field}"},
            mapped,
            {"$ifNull": [f"${field}", "$$REMOVE"]},
        ]
    }


def operational_order_pipeline(query: dict) -> list:
    """One-order read for view=operational.

    The aggregation result omits inline design/payment/sample/SJ bytes when
    id or storage_path still proves the file, and omits progress photos.
    Legacy data_url values with no other open path stay in the result.
    Delivery proof bytes stay here because delivery history copies them.
    """
    id_or_storage = {
        "$or": [
            _nonempty_expr("$$file.storage_path"),
            _nonempty_expr("$$file.id"),
        ]
    }
    storage_only = _nonempty_expr("$$file.storage_path")
    sj_drop = {
        "$or": [
            _nonempty_expr("$$file.storage_path"),
            {
                "$and": [
                    _nonempty_expr("$$file.id"),
                    {"$or": [
                        _nonempty_expr("$$file.name"),
                        _nonempty_expr("$$file.content_type"),
                        _nonempty_expr("$$file.uploaded_at"),
                    ]},
                ]
            },
        ]
    }
    progress = {
        "$map": {
            "input": "$operator_splits",
            "as": "split",
            "in": {
                "$cond": [
                    {
                        "$and": [
                            {"$eq": [{"$type": "$$split"}, "object"]},
                            {"$isArray": "$$split.progress_entries"},
                        ]
                    },
                    {
                        "$mergeObjects": [
                            "$$split",
                            {
                                "progress_entries": {
                                    "$map": {
                                        "input": "$$split.progress_entries",
                                        "as": "entry",
                                        "in": {
                                            "$cond": [
                                                {"$eq": [{"$type": "$$entry"}, "object"]},
                                                _without_keys_expr(
                                                    "$$entry",
                                                    ("data_url", "photo", "photos", "url"),
                                                ),
                                                "$$entry",
                                            ]
                                        },
                                    }
                                }
                            },
                        ]
                    },
                    "$$split",
                ]
            },
        }
    }
    return [
        {"$match": query},
        {"$limit": 1},
        {"$project": {"_id": 0}},
        {"$set": {
            "design_files": _present_array_expr("design_files", _map_files_expr("design_files", id_or_storage)),
            "payment_files": _present_array_expr("payment_files", _map_files_expr("payment_files", id_or_storage)),
            "sample_files": _present_array_expr("sample_files", _map_files_expr("sample_files", storage_only)),
            "surat_jalan_files": _present_array_expr("surat_jalan_files", _map_files_expr("surat_jalan_files", sj_drop)),
            "operator_splits": _present_array_expr("operator_splits", progress),
        }},
    ]


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
