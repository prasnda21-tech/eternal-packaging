"""Web presence is a heartbeat, not proof of work or login history.

Store only the latest two timestamps on the authenticated user. Multiple tabs
and devices merge with $max; no session history or unbounded event collection.
"""
from datetime import datetime, timedelta, timezone

from fastapi import Depends, HTTPException, Query, Response
from pydantic import BaseModel, ConfigDict, Field

ONLINE_SECONDS = 90
PERMISSION = "users.view"


class Heartbeat(BaseModel):
    model_config = ConfigDict(extra="forbid")
    # Elapsed monotonic time since a real browser interaction. Never accept a
    # client user id or absolute clock; missing means heartbeat only.
    activity_age_ms: int | None = Field(default=None, ge=0, le=90000, strict=True)


def utc(value):
    if not isinstance(value, datetime):
        return None
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def iso(value):
    value = utc(value)
    return value.isoformat() if value else None


def presence_row(user, now):
    presence = user.get("web_presence") or {}
    seen = utc(presence.get("seen_at"))
    online = bool(seen and 0 <= (now - seen).total_seconds() < ONLINE_SECONDS)
    return {
        "id": str(user.get("id") or user.get("_id")),
        "name": user.get("name") or "Pengguna tanpa nama",
        "online": online,
        "last_seen_at": iso(seen),
        "last_active_at": iso(presence.get("active_at")),
    }


async def record_heartbeat(db, user, payload, now=None):
    now = now or datetime.now(timezone.utc)
    email = user.get("email")
    if not email:
        raise HTTPException(401, "Sesi tidak valid")
    update = {"web_presence.seen_at": now}
    if payload.activity_age_ms is not None:
        update["web_presence.active_at"] = now - timedelta(milliseconds=payload.activity_age_ms)
    # Identity is supplied by current_user, never by the request body.
    await db.users.update_one({"email": email}, {"$max": update})
    return {"server_time": now.isoformat(), "online_timeout_seconds": ONLINE_SECONDS}


async def list_presence(db, page=1, page_size=50, now=None):
    now = now or datetime.now(timezone.utc)
    query = {"$or": [{"status": "active"}, {"status": {"$exists": False}}]}
    projection = {"_id": 1, "id": 1, "name": 1, "web_presence": 1}
    total = await db.users.count_documents(query)
    cursor = db.users.find(query, projection).sort([
        ("web_presence.active_at", -1), ("web_presence.seen_at", -1), ("_id", 1),
    ]).skip((page - 1) * page_size).limit(page_size)
    rows = await cursor.to_list(length=page_size)
    return {
        "rows": [presence_row(row, now) for row in rows], "total": total,
        "page": page, "page_size": page_size, "server_time": now.isoformat(),
        "online_timeout_seconds": ONLINE_SECONDS,
    }


def register_web_presence_routes(router, *, get_db, current_user, require_perm):
    @router.post("/auth/presence")
    async def heartbeat(payload: Heartbeat, response: Response, user=Depends(current_user)):
        response.headers["Cache-Control"] = "no-store"
        return await record_heartbeat(get_db(), user, payload)

    @router.get("/dashboard/active-users")
    async def active_users(response: Response, page: int = Query(1, ge=1),
                           page_size: int = Query(50, ge=1, le=100),
                           user=Depends(require_perm(PERMISSION))):
        response.headers["Cache-Control"] = "no-store"
        return await list_presence(get_db(), page, page_size)
