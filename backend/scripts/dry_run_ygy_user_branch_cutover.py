#!/usr/bin/env python3
"""DRY-RUN ONLY: propose YGY org + user branch assignments for future cutover.

Refuses APPLY. Never mutates production. Never prints secrets.

Usage:
  cd backend && python3 scripts/dry_run_ygy_user_branch_cutover.py
  cd backend && DB_NAME=eternal_packaging_preview python3 scripts/dry_run_ygy_user_branch_cutover.py
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from services.branch_access import (  # noqa: E402
    DEFAULT_BRANCH_CODE,
    DEFAULT_WAREHOUSE_CODE,
    assigned_branch_ids,
)
from services.purchasing_inventory_access import HOLDING_TEAM_ROLE  # noqa: E402

FORBIDDEN_APPLY = {"1", "true", "yes", "apply"}


def _refuse_apply() -> None:
    if str(os.environ.get("APPLY") or "").strip().lower() in FORBIDDEN_APPLY:
        raise SystemExit("REFUSED: APPLY is not allowed in this dry-run tool")
    if "--apply" in sys.argv:
        raise SystemExit("REFUSED: --apply is not allowed in this dry-run tool")


async def dry_run(db) -> dict:
    from services.branch_access import find_branch_by_code

    ygy = await find_branch_by_code(db, DEFAULT_BRANCH_CODE)
    ygy_proposal = {
        "exists": bool(ygy),
        "id": (ygy or {}).get("id"),
        "code": DEFAULT_BRANCH_CODE,
        "name": "Yogyakarta",
        "action": "reuse" if ygy else "would_create",
    }
    wh = None
    if ygy:
        wh = await db.warehouses.find_one(
            {
                "branch_id": ygy["id"],
                "code": DEFAULT_WAREHOUSE_CODE,
                "active": {"$ne": False},
            },
            {"_id": 0},
        )
        if not wh:
            wh = await db.warehouses.find_one(
                {"branch_id": ygy["id"], "active": {"$ne": False}, "is_operational": {"$ne": False}},
                {"_id": 0},
            )
    wh_proposal = {
        "exists": bool(wh),
        "id": (wh or {}).get("id"),
        "code": (wh or {}).get("code") or DEFAULT_WAREHOUSE_CODE,
        "name": (wh or {}).get("name") or "Gudang Utama Yogyakarta",
        "action": "reuse" if wh else "would_create",
    }

    users = await db.users.find(
        {},
        {"_id": 0, "id": 1, "email": 1, "name": 1, "role": 1, "status": 1, "branch_id": 1, "branch_ids": 1},
    ).to_list(5000)

    already = []
    would_ygy = []
    holding = []
    ambiguous = []
    for user in users:
        email = str(user.get("email") or "")
        role = str(user.get("role") or "").strip().lower()
        assigned = assigned_branch_ids(user)
        summary = {"id": user.get("id"), "email": email, "role": role, "status": user.get("status")}
        if role == HOLDING_TEAM_ROLE:
            holding.append(summary)
            continue
        if assigned:
            if ygy and ygy.get("id") in assigned and len(assigned) == 1:
                already.append({**summary, "branch_ids": assigned})
            elif ygy and any(bid == ygy.get("id") for bid in assigned) and len(assigned) > 1:
                ambiguous.append({**summary, "branch_ids": assigned, "reason": "multi_branch_includes_ygy"})
            else:
                already.append({**summary, "branch_ids": assigned})
            continue
        # No assignment → propose YGY for normal/active operational users
        if str(user.get("status") or "active").lower() not in {"active", ""}:
            ambiguous.append({**summary, "reason": "inactive_without_assignment"})
            continue
        would_ygy.append(summary)

    return {
        "mode": "DRY_RUN",
        "db_name": db.name,
        "ygy_branch": ygy_proposal,
        "ygy_warehouse": wh_proposal,
        "USERS_TOTAL": len(users),
        "USERS_ALREADY_ASSIGNED": len(already),
        "USERS_WOULD_ASSIGN_YGY": len(would_ygy),
        "HOLDING_TEAM_GLOBAL": len(holding),
        "AMBIGUOUS_USERS": len(ambiguous),
        "samples": {
            "would_assign_ygy": would_ygy[:20],
            "already_assigned": already[:20],
            "holding_team": holding[:20],
            "ambiguous": ambiguous[:20],
        },
    }


async def main() -> int:
    _refuse_apply()
    mongo_url = os.environ.get("MONGO_URL") or "mongodb://127.0.0.1:27017/?replicaSet=eternal_local_rs"
    db_name = os.environ.get("DB_NAME") or "eternal_packaging_preview"
    host = (urlparse(mongo_url).hostname or "").lower()
    if host not in {"127.0.0.1", "localhost", "::1"}:
        # Still dry-run readable, but warn loudly — never apply.
        print(f"WARNING: non-local host {host}; dry-run only, no writes")
    from motor.motor_asyncio import AsyncIOMotorClient

    client = AsyncIOMotorClient(mongo_url)
    db = client[db_name]
    try:
        report = await dry_run(db)
        import json
        print(json.dumps(report, indent=2, ensure_ascii=False))
        return 0
    finally:
        client.close()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
