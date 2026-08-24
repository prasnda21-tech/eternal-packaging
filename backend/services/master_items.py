"""Master Barang/Jasa — Accurate item sync + PO line resolution.

Reuses AccurateClient (same auth, headers, timeouts, pagination as Import PO).
Identity is always `accurate_item_id`. Never match by item_name.
"""
from __future__ import annotations

import asyncio
import re
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from pymongo.errors import DuplicateKeyError

from services.accurate import AccurateClient, normalize_accurate_item

COLLECTION = "master_items"
UNKNOWN_ITEM_MSG = (
    "Item belum tersedia di Master Barang/Jasa. "
    "Sinkronkan Master Barang/Jasa dari Accurate terlebih dahulu."
)
ACCURATE_OWNED_FIELDS = (
    "item_code",
    "item_name",
    "item_type",
    "unit",
    "is_active",
    "accurate_updated_at",
)

_sync_lock = asyncio.Lock()


class MasterItemUnresolvedError(Exception):
    """PO line could not be bound to a Master Item by Accurate ID."""


class SyncInProgressError(Exception):
    """A Master Item sync is already running in this process."""


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def public_master_item(doc: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not doc:
        return None
    return {
        "id": doc.get("id"),
        "accurate_item_id": doc.get("accurate_item_id"),
        "item_code": doc.get("item_code"),
        "item_name": doc.get("item_name"),
        "item_type": doc.get("item_type"),
        "unit": doc.get("unit"),
        "is_active": doc.get("is_active"),
        "accurate_updated_at": doc.get("accurate_updated_at"),
        "last_synced_at": doc.get("last_synced_at"),
        "created_at": doc.get("created_at"),
        "updated_at": doc.get("updated_at"),
    }


def _owned_payload(incoming: Dict[str, Any]) -> Dict[str, Any]:
    out = {}
    for key in ACCURATE_OWNED_FIELDS:
        if key not in incoming:
            continue
        val = incoming.get(key)
        # Do not wipe a stored value just because this list row omitted the field.
        if val is None or val == "":
            continue
        out[key] = val
    return out


def _fields_changed(existing: Dict[str, Any], incoming_owned: Dict[str, Any]) -> bool:
    for key, val in incoming_owned.items():
        if existing.get(key) != val:
            return True
    return False


async def upsert_master_item(db, incoming: Dict[str, Any]) -> Tuple[Dict[str, Any], str]:
    """Upsert one Master Item keyed by accurate_item_id.

    Returns (public_doc, action) where action is 'created' | 'updated' | 'unchanged'.
    """
    if incoming.get("accurate_item_id") is None and incoming.get("id") is not None:
        incoming = normalize_accurate_item(incoming) or {}
    aid = str(incoming.get("accurate_item_id") or "").strip()
    if not aid:
        raise ValueError("accurate_item_id wajib")
    owned = _owned_payload(incoming)
    now = now_iso()
    col = db[COLLECTION]
    existing = await col.find_one({"accurate_item_id": aid}, {"_id": 0})
    if existing:
        changed = _fields_changed(existing, owned)
        update = {"last_synced_at": now, "updated_at": now, **owned}
        await col.update_one({"accurate_item_id": aid}, {"$set": update})
        fresh = await col.find_one({"accurate_item_id": aid}, {"_id": 0})
        return public_master_item(fresh), ("updated" if changed else "unchanged")

    new_id = str(uuid.uuid4())
    doc = {
        "id": new_id,
        "accurate_item_id": aid,
        **{k: owned.get(k) for k in ACCURATE_OWNED_FIELDS},
        "last_synced_at": now,
        "created_at": now,
        "updated_at": now,
    }
    try:
        await col.update_one(
            {"accurate_item_id": aid},
            {"$setOnInsert": doc},
            upsert=True,
        )
    except DuplicateKeyError:
        # Concurrent insert of the same Accurate ID — retry as update.
        return await upsert_master_item(db, incoming)
    fresh = await col.find_one({"accurate_item_id": aid}, {"_id": 0})
    # If another worker inserted first, treat as update/unchanged of that row.
    if fresh and fresh.get("id") != new_id:
        changed = _fields_changed(fresh, owned)
        await col.update_one(
            {"accurate_item_id": aid},
            {"$set": {"last_synced_at": now, "updated_at": now, **owned}},
        )
        fresh = await col.find_one({"accurate_item_id": aid}, {"_id": 0})
        return public_master_item(fresh), ("updated" if changed else "unchanged")
    return public_master_item(fresh), "created"


async def sync_master_items(db, client: AccurateClient) -> Dict[str, Any]:
    """Pull Accurate items and upsert. Never truncates. Idempotent by accurate_item_id."""
    if _sync_lock.locked():
        raise SyncInProgressError("Sinkronisasi Master Barang/Jasa sedang berjalan")
    async with _sync_lock:
        rows = await client.fetch_all_items()
        created = updated = unchanged = failed = 0
        for row in rows:
            try:
                if not row or not row.get("accurate_item_id"):
                    failed += 1
                    continue
                _doc, action = await upsert_master_item(db, row)
                if action == "created":
                    created += 1
                elif action == "updated":
                    updated += 1
                else:
                    unchanged += 1
            except Exception:
                failed += 1
        last = await db[COLLECTION].find({}, {"_id": 0, "last_synced_at": 1}).sort("last_synced_at", -1).limit(1).to_list(1)
        last_synced_at = (last[0].get("last_synced_at") if last else None)
        total = await db[COLLECTION].count_documents({})
        return {
            "status": "ok",
            "mode": "live" if client.is_live else "mock",
            "created": created,
            "updated": updated,
            "unchanged": unchanged,
            "failed": failed,
            "total": total,
            "last_synced_at": last_synced_at,
            "message": (
                f"Baru: {created} · Diperbarui: {updated} · "
                f"Tidak Berubah: {unchanged} · Gagal: {failed}"
            ),
        }


async def lookup_masters_by_accurate_ids(db, ids: List[str]) -> Dict[str, Dict[str, Any]]:
    clean = [str(i).strip() for i in ids if i is not None and str(i).strip()]
    if not clean:
        return {}
    docs = await db[COLLECTION].find(
        {"accurate_item_id": {"$in": clean}},
        {"_id": 0},
    ).to_list(len(clean) + 10)
    return {d["accurate_item_id"]: d for d in docs if d.get("accurate_item_id")}


async def resolve_po_line_items(
    db,
    client: AccurateClient,
    line_items: List[Dict[str, Any]],
    *,
    require_master: bool,
) -> List[Dict[str, Any]]:
    """Bind Accurate PO lines to Master Items by accurate_item_id.

    Targeted item/detail.do fetch is used for IDs missing from Master.
    Never matches by name. Historical / file imports pass require_master=False.
    """
    items = list(line_items or [])
    if not require_master:
        return items

    missing_id = [li for li in items if not str(li.get("accurate_item_id") or "").strip()]
    if missing_id:
        raise MasterItemUnresolvedError(UNKNOWN_ITEM_MSG)

    ids = [str(li.get("accurate_item_id")).strip() for li in items]
    masters = await lookup_masters_by_accurate_ids(db, ids)
    missing = [i for i in ids if i not in masters]
    # Targeted fetch is supported (same AccurateClient, item/detail.do).
    for aid in missing:
        try:
            fetched = await client.fetch_item_detail(aid)
        except Exception as e:
            raise MasterItemUnresolvedError(UNKNOWN_ITEM_MSG) from e
        if not fetched or not fetched.get("accurate_item_id"):
            raise MasterItemUnresolvedError(UNKNOWN_ITEM_MSG)
        doc, _action = await upsert_master_item(db, fetched)
        masters[aid] = {
            "id": doc["id"],
            "accurate_item_id": doc["accurate_item_id"],
            "item_code": doc.get("item_code"),
            "item_name": doc.get("item_name"),
        }

    out: List[Dict[str, Any]] = []
    for li in items:
        row = dict(li)
        aid = str(li.get("accurate_item_id")).strip()
        master = masters.get(aid) or {}
        row["accurate_item_id"] = aid
        row["master_item_id"] = master.get("id")
        if not row.get("name"):
            row["name"] = master.get("item_name") or row.get("name")
        if not row.get("item_code"):
            row["item_code"] = master.get("item_code") or row.get("item_code")
        out.append(row)
    return out


def build_list_query(
    q: str = "",
    item_type: str = "",
    status: str = "",
) -> Dict[str, Any]:
    filt: Dict[str, Any] = {}
    clauses: List[Dict[str, Any]] = []
    needle = (q or "").strip()
    if needle:
        rx = re.escape(needle)
        clauses.append({"$or": [
            {"item_code": {"$regex": rx, "$options": "i"}},
            {"item_name": {"$regex": rx, "$options": "i"}},
            {"accurate_item_id": {"$regex": rx, "$options": "i"}},
        ]})
    itype = (item_type or "").strip()
    if itype and itype not in ("all", "*"):
        clauses.append({"item_type": itype})
    st = (status or "").strip().lower()
    if st == "active":
        clauses.append({"is_active": True})
    elif st in ("inactive", "nonaktif"):
        clauses.append({"is_active": False})
    if len(clauses) == 1:
        filt = clauses[0]
    elif clauses:
        filt = {"$and": clauses}
    return filt


async def list_master_items(
    db,
    *,
    q: str = "",
    item_type: str = "",
    status: str = "",
    page: int = 1,
    page_size: int = 50,
) -> Dict[str, Any]:
    page = max(1, int(page or 1))
    page_size = max(1, min(int(page_size or 50), 100))
    filt = build_list_query(q, item_type, status)
    col = db[COLLECTION]
    total = await col.count_documents(filt)
    skip = (page - 1) * page_size
    docs = await col.find(filt, {"_id": 0}).sort("item_name", 1).skip(skip).limit(page_size).to_list(page_size)
    all_total = await col.count_documents({})
    active = await col.count_documents({"is_active": True})
    inactive = await col.count_documents({"is_active": False})
    known_status = await col.count_documents({"is_active": {"$in": [True, False]}})
    last = await col.find({}, {"_id": 0, "last_synced_at": 1}).sort("last_synced_at", -1).limit(1).to_list(1)
    types_raw = await col.distinct("item_type")
    item_types = sorted([t for t in types_raw if t])
    return {
        "items": [public_master_item(d) for d in docs],
        "total": total,
        "page": page,
        "page_size": page_size,
        "page_count": max(1, (total + page_size - 1) // page_size) if total else 1,
        "summary": {
            "total": all_total,
            "active": active if known_status else None,
            "inactive": inactive if known_status else None,
            "last_synced_at": last[0].get("last_synced_at") if last else None,
            "has_active_status": bool(known_status),
        },
        "item_types": item_types,
    }
