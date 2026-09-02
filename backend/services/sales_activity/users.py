"""Map Excel sales names to existing Eternal users. Never create users."""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from services.sales_activity.constants import USER_AMBIGUOUS, USER_MATCHED, USER_UNMATCHED
from services.sales_activity.util import is_blank, normalize_text

_USER_PROJECTION = {
    "_id": 0,
    "id": 1,
    "email": 1,
    "name": 1,
    "role": 1,
    "status": 1,
}


def _user_id(user: dict) -> str:
    return str(user.get("id") or "").strip()


def public_user(user: Optional[dict]) -> Optional[dict]:
    if not user:
        return None
    return {
        "id": _user_id(user),
        "email": user.get("email"),
        "name": user.get("name"),
        "role": user.get("role"),
        "status": user.get("status") or "active",
    }


def is_user_active(user: Optional[dict]) -> bool:
    if not user:
        return False
    return str(user.get("status") or "active") == "active" and bool(_user_id(user))


async def list_users(db) -> List[dict]:
    rows = await db.users.find({}, _USER_PROJECTION).to_list(2000)
    return [public_user(u) for u in rows if _user_id(u)]


async def get_user(db, user_id: str) -> Optional[dict]:
    uid = str(user_id or "").strip()
    if not uid:
        return None
    doc = await db.users.find_one({"id": uid}, _USER_PROJECTION)
    return public_user(doc) if doc else None


async def users_by_ids(db, ids: List[str]) -> Dict[str, dict]:
    clean = [str(i).strip() for i in ids if str(i or "").strip()]
    if not clean:
        return {}
    rows = await db.users.find({"id": {"$in": clean}}, _USER_PROJECTION).to_list(len(clean))
    return {_user_id(u): public_user(u) for u in rows if _user_id(u)}


def map_sales_name(name: Any, users: List[dict]) -> dict:
    raw = str(name or "").strip()
    if is_blank(raw):
        return {"status": USER_UNMATCHED, "input": raw, "user": None, "candidates": []}
    needle = normalize_text(raw)
    matches = []
    for user in users:
        names = {
            normalize_text(user.get("name")),
            normalize_text(user.get("email")),
            normalize_text((user.get("email") or "").split("@")[0]),
        }
        if needle in names:
            matches.append(user)
    unique = {u["id"]: u for u in matches if u.get("id")}
    found = list(unique.values())
    if len(found) == 1:
        return {"status": USER_MATCHED, "input": raw, "user": found[0], "candidates": found}
    if len(found) > 1:
        return {"status": USER_AMBIGUOUS, "input": raw, "user": None, "candidates": found}
    return {"status": USER_UNMATCHED, "input": raw, "user": None, "candidates": []}
