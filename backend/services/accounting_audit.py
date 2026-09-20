"""Append-only accounting control events.

Period close/reopen, COA mutations, and accounting settings changes.
Does not record journal post/reversal (those already carry actor metadata).

Failure policy (no multi-doc Mongo transaction):
the business mutation writes first, then this insert. If the insert fails,
the exception propagates so callers do not return success without an event.
Period close/reopen retries write a missing event if the first audit insert failed.
"""
from __future__ import annotations

import uuid
from typing import Any, Dict, List, Optional

from services.purchase_common import actor_snapshot, now_iso

COLLECTION = "accounting_audit_events"

EVENT_PERIOD_CLOSED = "period_closed"
EVENT_PERIOD_REOPENED = "period_reopened"
EVENT_COA_CREATED = "coa_created"
EVENT_COA_RENAMED = "coa_renamed"
EVENT_COA_TYPE_CHANGED = "coa_type_changed"
EVENT_COA_DELETED = "coa_deleted"
EVENT_SALES_SETTINGS_CHANGED = "sales_accounting_settings_changed"
EVENT_PAYROLL_SETTINGS_CHANGED = "payroll_accounting_settings_changed"

ENTITY_PERIOD = "accounting_period"
ENTITY_COA = "ledger_account"
ENTITY_SALES_SETTINGS = "sales_accounting_settings"
ENTITY_PAYROLL_SETTINGS = "payroll_accounting_settings"


async def ensure_indexes(db) -> None:
    col = db[COLLECTION]
    await col.create_index("id", unique=True)
    await col.create_index("at")
    await col.create_index("event_type")
    await col.create_index([("entity_type", 1), ("entity_id", 1)])


def public_event(doc: Optional[dict]) -> Optional[dict]:
    if not doc:
        return None
    return {
        "id": doc.get("id"),
        "event_type": doc.get("event_type"),
        "entity_type": doc.get("entity_type"),
        "entity_id": doc.get("entity_id"),
        "actor": doc.get("actor"),
        "at": doc.get("at"),
        "before": doc.get("before"),
        "after": doc.get("after"),
        "reason": doc.get("reason") or "",
        "metadata": doc.get("metadata") or {},
    }


async def record_accounting_event(
    db,
    *,
    event_type: str,
    entity_type: str,
    entity_id: str,
    actor: Optional[dict] = None,
    before: Any = None,
    after: Any = None,
    reason: Optional[str] = None,
    metadata: Optional[dict] = None,
) -> dict:
    doc = {
        "id": str(uuid.uuid4()),
        "event_type": str(event_type),
        "entity_type": str(entity_type),
        "entity_id": str(entity_id or ""),
        "actor": actor_snapshot(actor),
        "at": now_iso(),
        "before": before,
        "after": after,
        "reason": str(reason or "").strip(),
        "metadata": dict(metadata or {}),
    }
    await db[COLLECTION].insert_one(dict(doc))
    doc.pop("_id", None)
    return public_event(doc)


async def list_accounting_events(
    db,
    *,
    event_type: Optional[str] = None,
    entity_type: Optional[str] = None,
    entity_id: Optional[str] = None,
    limit: int = 200,
) -> List[dict]:
    query: Dict[str, Any] = {}
    if event_type:
        query["event_type"] = str(event_type).strip()
    if entity_type:
        query["entity_type"] = str(entity_type).strip()
    if entity_id:
        query["entity_id"] = str(entity_id).strip()
    cap = max(1, min(int(limit or 200), 1000))
    docs = await db[COLLECTION].find(query, {"_id": 0}).sort("at", -1).to_list(cap)
    return [public_event(d) for d in docs]
