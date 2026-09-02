"""Append-only ownership history."""
from __future__ import annotations

from typing import Optional

from pymongo.errors import DuplicateKeyError

from services.sales_activity.clock import iso_now
from services.sales_activity.constants import COL_EVENTS
from services.sales_activity.util import new_id, public_doc


async def append_event(
    db,
    *,
    outlet_id: str,
    event_type: str,
    previous_owner_id: Optional[str] = None,
    new_owner_id: Optional[str] = None,
    actor_type: str,
    actor_user_id: Optional[str] = None,
    rule_id: Optional[str] = None,
    rule_version: Optional[int] = None,
    reason: Optional[str] = None,
    source: Optional[str] = None,
    import_batch_id: Optional[str] = None,
    correlation_key: Optional[str] = None,
    payload: Optional[dict] = None,
) -> dict:
    doc = {
        "event_id": new_id(),
        "outlet_id": outlet_id,
        "event_type": event_type,
        "previous_owner_id": previous_owner_id,
        "new_owner_id": new_owner_id,
        "occurred_at": iso_now(),
        "actor_type": actor_type,
        "actor_user_id": actor_user_id,
        "rule_id": rule_id,
        "rule_version": rule_version,
        "reason": reason,
        "source": source,
        "import_batch_id": import_batch_id,
        "correlation_key": correlation_key or f"{outlet_id}:{event_type}:{new_id()}",
        "payload": payload or {},
    }
    try:
        await db[COL_EVENTS].insert_one(doc)
    except DuplicateKeyError:
        existing = await db[COL_EVENTS].find_one({"correlation_key": doc["correlation_key"]}, {"_id": 0})
        if existing:
            return existing
        raise
    return public_doc(doc)


async def list_events(db, outlet_id: str) -> list:
    rows = await db[COL_EVENTS].find({"outlet_id": outlet_id}, {"_id": 0}).sort("occurred_at", 1).to_list(2000)
    return [public_doc(r) for r in rows]


async def count_events(db, outlet_id: Optional[str] = None) -> int:
    query = {"outlet_id": outlet_id} if outlet_id else {}
    return await db[COL_EVENTS].count_documents(query)
