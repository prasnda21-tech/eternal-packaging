"""Internal production categories for Master Barang Eternal.

Accurate sync and the public catalog do not own these rows.
Renaming a category does not rewrite progress snapshots.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from fastapi import HTTPException

COLLECTION = "product_categories"
NAME_MAX = 80


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _actor(user: Optional[dict]) -> Optional[str]:
    if not isinstance(user, dict):
        return None
    return str(user.get("email") or user.get("id") or "").strip() or None


def _name_key(name: str) -> str:
    return " ".join(str(name or "").strip().casefold().split())


def public_category(doc: Optional[dict]) -> Optional[dict]:
    if not doc:
        return None
    return {
        "id": doc.get("id"),
        "name": doc.get("name"),
        "active": doc.get("active") is not False,
        "created_at": doc.get("created_at"),
        "created_by": doc.get("created_by"),
        "updated_at": doc.get("updated_at"),
        "updated_by": doc.get("updated_by"),
    }


async def ensure_indexes(db) -> None:
    col = db[COLLECTION]
    await col.create_index("id", unique=True)
    await col.create_index("name_key", unique=True)


def _clean_name(name: Any) -> str:
    text = " ".join(str(name or "").strip().split())
    if not text:
        raise HTTPException(400, "Nama kategori wajib diisi.")
    if len(text) > NAME_MAX:
        raise HTTPException(400, f"Nama kategori maksimal {NAME_MAX} karakter.")
    return text


async def list_categories(db) -> dict:
    docs = await db[COLLECTION].find({}, {"_id": 0}).sort("name", 1).to_list(500)
    rows = [public_category(doc) for doc in docs]
    return {"items": rows, "total": len(rows)}


async def create_category(db, name: Any, *, user: Optional[dict] = None) -> dict:
    await ensure_indexes(db)
    text = _clean_name(name)
    key = _name_key(text)
    existing = await db[COLLECTION].find_one({"name_key": key}, {"_id": 0, "id": 1})
    if existing:
        raise HTTPException(409, "Nama kategori sudah dipakai.")
    stamp = now_iso()
    actor = _actor(user)
    doc = {
        "id": str(uuid.uuid4()),
        "name": text,
        "name_key": key,
        "active": True,
        "created_at": stamp,
        "created_by": actor,
        "updated_at": stamp,
        "updated_by": actor,
    }
    await db[COLLECTION].insert_one(doc)
    fresh = await db[COLLECTION].find_one({"id": doc["id"]}, {"_id": 0})
    return public_category(fresh)


async def update_category(
    db,
    category_id: str,
    *,
    name: Any = None,
    active: Any = None,
    user: Optional[dict] = None,
) -> dict:
    category_id = str(category_id or "").strip()
    if not category_id:
        raise HTTPException(400, "ID kategori wajib.")
    existing = await db[COLLECTION].find_one({"id": category_id}, {"_id": 0})
    if not existing:
        raise HTTPException(404, "Kategori tidak ditemukan.")
    update: Dict[str, Any] = {}
    if name is not None:
        text = _clean_name(name)
        key = _name_key(text)
        clash = await db[COLLECTION].find_one({"name_key": key, "id": {"$ne": category_id}}, {"_id": 0, "id": 1})
        if clash:
            raise HTTPException(409, "Nama kategori sudah dipakai.")
        update["name"] = text
        update["name_key"] = key
    if active is not None:
        update["active"] = active is True or str(active).strip().lower() in ("1", "true", "yes")
    if not update:
        return public_category(existing)
    stamp = now_iso()
    update["updated_at"] = stamp
    update["updated_by"] = _actor(user)
    await db[COLLECTION].update_one({"id": category_id}, {"$set": update})
    fresh = await db[COLLECTION].find_one({"id": category_id}, {"_id": 0})
    return public_category(fresh)


async def set_master_product_category(
    db,
    item_id: str,
    category_id: Any,
    *,
    user: Optional[dict] = None,
) -> dict:
    """Point one master item at an internal category. Does not rewrite orders."""
    from services.master_items import COLLECTION as ITEMS
    from services.master_items import public_master_item

    item_id = str(item_id or "").strip()
    if not item_id:
        raise HTTPException(400, "ID barang/jasa wajib.")
    existing = await db[ITEMS].find_one(
        {"$or": [{"id": item_id}, {"accurate_item_id": item_id}]},
        {"_id": 0},
    )
    if not existing:
        raise HTTPException(404, "Barang/jasa tidak ditemukan.")
    raw = None if category_id is None else str(category_id).strip()
    if raw == "":
        raw = None
    if raw:
        category = await db[COLLECTION].find_one({"id": raw}, {"_id": 0, "id": 1, "active": 1})
        if not category:
            raise HTTPException(400, "Kategori produksi tidak ditemukan.")
        if category.get("active") is False:
            raise HTTPException(400, "Kategori produksi tidak aktif.")
    stamp = now_iso()
    await db[ITEMS].update_one(
        {"id": existing["id"]},
        {"$set": {
            "product_category_id": raw,
            "product_category_updated_at": stamp,
            "product_category_updated_by": _actor(user),
            "updated_at": stamp,
        }},
    )
    fresh = await db[ITEMS].find_one({"id": existing["id"]}, {"_id": 0})
    return public_master_item(fresh)
