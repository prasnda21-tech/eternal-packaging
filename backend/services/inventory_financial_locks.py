"""Mongo CAS locks for financial inventory item consumption.

Authority is Mongo findOneAndUpdate, never an in-process asyncio lock.
One live lease per financial_inventory_item_key. Token-scoped release.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional
import uuid

from pymongo import ReturnDocument

from services.purchase_common import now_iso

COLLECTION = "inventory_financial_item_locks"
LEASE_SECONDS = 600

LOCK_CONFLICT_CODE = "FINANCIAL_ITEM_LOCK_CONFLICT"
LOCK_CONFLICT_MESSAGE = (
    "Item persediaan sedang dikunci oleh proses accounting lain."
)


def _parse_iso(raw: Any) -> Optional[datetime]:
    text = str(raw or "").strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def lock_is_active(doc: Optional[dict], *, now: Optional[datetime] = None) -> bool:
    row = doc or {}
    token = str(row.get("token") or "").strip()
    if not token:
        return False
    expires = _parse_iso(row.get("expires_at"))
    if expires is None:
        return False
    current = now or datetime.now(timezone.utc)
    return current < expires


def _stale_or_same_holder_clause(holder_movement_id: str, *, now: Optional[datetime] = None) -> Dict[str, Any]:
    current = now or datetime.now(timezone.utc)
    return {
        "$or": [
            {"token": {"$exists": False}},
            {"token": None},
            {"token": ""},
            {"expires_at": {"$exists": False}},
            {"expires_at": None},
            {"expires_at": ""},
            {"expires_at": {"$lte": current.isoformat()}},
            {"holder_movement_id": str(holder_movement_id)},
        ]
    }


async def ensure_indexes(db) -> None:
    await db[COLLECTION].create_index("financial_inventory_item_key", unique=True)
    await db[COLLECTION].create_index("holder_movement_id")


async def acquire_item_lock(
    db,
    financial_inventory_item_key: str,
    holder_movement_id: str,
    *,
    token: Optional[str] = None,
    now: Optional[datetime] = None,
) -> Optional[dict]:
    key = str(financial_inventory_item_key or "").strip()
    holder = str(holder_movement_id or "").strip()
    if not key or not holder:
        return None
    claim_token = str(token or uuid.uuid4())
    current = now or datetime.now(timezone.utc)
    claimed_at = current.isoformat()
    expires_at = (current + timedelta(seconds=LEASE_SECONDS)).isoformat()
    try:
        doc = await db[COLLECTION].find_one_and_update(
            {
                "financial_inventory_item_key": key,
                **_stale_or_same_holder_clause(holder, now=current),
            },
            {
                "$set": {
                    "financial_inventory_item_key": key,
                    "token": claim_token,
                    "holder_movement_id": holder,
                    "claimed_at": claimed_at,
                    "expires_at": expires_at,
                }
            },
            upsert=True,
            return_document=ReturnDocument.AFTER,
        )
    except Exception:
        return None
    if not doc:
        return None
    if str(doc.get("token") or "") != claim_token:
        return None
    if str(doc.get("holder_movement_id") or "") != holder:
        return None
    doc.pop("_id", None)
    return doc


async def release_item_lock(
    db,
    financial_inventory_item_key: str,
    token: str,
) -> bool:
    key = str(financial_inventory_item_key or "").strip()
    claim = str(token or "").strip()
    if not key or not claim:
        return False
    result = await db[COLLECTION].delete_one(
        {"financial_inventory_item_key": key, "token": claim}
    )
    return int(getattr(result, "deleted_count", 0) or 0) > 0


async def acquire_item_locks(
    db,
    financial_inventory_item_keys: Iterable[str],
    holder_movement_id: str,
    *,
    now: Optional[datetime] = None,
) -> Optional[List[dict]]:
    """Acquire locks in sorted key order. Failure releases any prior locks."""
    keys = sorted({str(k).strip() for k in financial_inventory_item_keys if str(k or "").strip()})
    acquired: List[dict] = []
    for key in keys:
        locked = await acquire_item_lock(db, key, holder_movement_id, now=now)
        if not locked:
            for row in acquired:
                await release_item_lock(
                    db,
                    str(row.get("financial_inventory_item_key") or ""),
                    str(row.get("token") or ""),
                )
            return None
        acquired.append(locked)
    return acquired


async def release_item_locks(db, locks: Optional[Iterable[dict]]) -> None:
    for row in locks or []:
        await release_item_lock(
            db,
            str((row or {}).get("financial_inventory_item_key") or ""),
            str((row or {}).get("token") or ""),
        )


async def get_item_lock(db, financial_inventory_item_key: str) -> Optional[dict]:
    key = str(financial_inventory_item_key or "").strip()
    if not key:
        return None
    doc = await db[COLLECTION].find_one({"financial_inventory_item_key": key}, {"_id": 0})
    return doc


async def renew_item_lock(
    db,
    financial_inventory_item_key: str,
    token: str,
    *,
    now: Optional[datetime] = None,
) -> Optional[dict]:
    """CAS: exact token must still be the live unexpired holder. Extends the lease."""
    key = str(financial_inventory_item_key or "").strip()
    claim = str(token or "").strip()
    if not key or not claim:
        return None
    current = now or datetime.now(timezone.utc)
    expires_at = (current + timedelta(seconds=LEASE_SECONDS)).isoformat()
    doc = await db[COLLECTION].find_one_and_update(
        {
            "financial_inventory_item_key": key,
            "token": claim,
            "expires_at": {"$gt": current.isoformat()},
        },
        {"$set": {"expires_at": expires_at}},
        return_document=ReturnDocument.AFTER,
    )
    if not doc:
        return None
    if str(doc.get("token") or "") != claim:
        return None
    if not lock_is_active(doc, now=current):
        return None
    doc.pop("_id", None)
    return doc


async def renew_item_locks(
    db, locks: Optional[Iterable[dict]], *, now: Optional[datetime] = None
) -> Optional[List[dict]]:
    renewed: List[dict] = []
    for row in locks or []:
        held = await renew_item_lock(
            db,
            str((row or {}).get("financial_inventory_item_key") or ""),
            str((row or {}).get("token") or ""),
            now=now,
        )
        if not held:
            return None
        renewed.append(held)
    return renewed
