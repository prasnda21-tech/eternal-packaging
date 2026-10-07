#!/usr/bin/env python3
"""Manual local fixture: YGY branch + one operational warehouse.

LOCAL PREVIEW ONLY. Refuses any database except eternal_packaging_preview.
Does NOT auto-run on startup. Does NOT touch production. Idempotent.

Usage:
  cd backend && DB_NAME=eternal_packaging_preview python3 scripts/seed_preview_ygy_org.py
"""
from __future__ import annotations

import asyncio
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

ALLOWED_DB = "eternal_packaging_preview"
ALLOWED_HOSTS = {"127.0.0.1", "localhost", "::1"}
FORBIDDEN_ENVS = {"production", "prod"}

BRANCH_ID = "preview-branch-ygy"
WAREHOUSE_ID = "preview-wh-ygy-utama"
BRANCH_CODE = "YGY"
WAREHOUSE_CODE = "YGY-UTAMA"


def _assert_local_preview() -> tuple[str, str]:
    url = str(os.environ.get("MONGO_URL") or "").strip() or (
        "mongodb://127.0.0.1:27017/?replicaSet=eternal_local_rs"
    )
    db_name = str(os.environ.get("DB_NAME") or ALLOWED_DB).strip()
    app_env = str(os.environ.get("APP_ENV") or "").strip().lower()
    host = (urlparse(url).hostname or "").lower()
    if app_env in FORBIDDEN_ENVS:
        raise SystemExit(f"REFUSED: APP_ENV={app_env}")
    if host not in ALLOWED_HOSTS:
        raise SystemExit(f"REFUSED: Mongo host {host or 'unknown'} is not local")
    if db_name != ALLOWED_DB:
        raise SystemExit(f"REFUSED: DB_NAME={db_name!r} (only {ALLOWED_DB} allowed)")
    if not url.startswith("mongodb://"):
        raise SystemExit("REFUSED: Mongo URI must be local mongodb://")
    return url, db_name


async def main() -> int:
    mongo_url, db_name = _assert_local_preview()
    from motor.motor_asyncio import AsyncIOMotorClient

    client = AsyncIOMotorClient(mongo_url)
    db = client[db_name]
    now = datetime.now(timezone.utc).isoformat()
    try:
        existing_branch = await db.branches.find_one(
            {"$or": [{"id": BRANCH_ID}, {"code": BRANCH_CODE}]},
            {"_id": 0},
        )
        if existing_branch:
            await db.branches.update_one(
                {"id": existing_branch["id"]},
                {"$set": {
                    "code": BRANCH_CODE,
                    "name": "Yogyakarta",
                    "active": True,
                    "updated_at": now,
                    "preview_fixture": True,
                }},
            )
            branch_id = existing_branch["id"]
            print(f"reuse branch {BRANCH_CODE} id={branch_id}")
        else:
            await db.branches.insert_one({
                "id": BRANCH_ID,
                "code": BRANCH_CODE,
                "name": "Yogyakarta",
                "active": True,
                "created_at": now,
                "updated_at": now,
                "preview_fixture": True,
            })
            branch_id = BRANCH_ID
            print(f"create branch {BRANCH_CODE} id={branch_id}")

        existing_wh = await db.warehouses.find_one(
            {"$or": [
                {"id": WAREHOUSE_ID},
                {"branch_id": branch_id, "code": WAREHOUSE_CODE},
            ]},
            {"_id": 0},
        )
        if existing_wh:
            await db.warehouses.update_one(
                {"id": existing_wh["id"]},
                {"$set": {
                    "branch_id": branch_id,
                    "code": WAREHOUSE_CODE,
                    "name": "Gudang Utama Yogyakarta",
                    "active": True,
                    "is_operational": True,
                    "updated_at": now,
                    "preview_fixture": True,
                }},
            )
            print(f"reuse warehouse {WAREHOUSE_CODE} id={existing_wh['id']}")
        else:
            await db.warehouses.insert_one({
                "id": WAREHOUSE_ID,
                "branch_id": branch_id,
                "code": WAREHOUSE_CODE,
                "name": "Gudang Utama Yogyakarta",
                "active": True,
                "is_operational": True,
                "created_at": now,
                "updated_at": now,
                "preview_fixture": True,
            })
            print(f"create warehouse {WAREHOUSE_CODE} id={WAREHOUSE_ID}")

        branch = await db.branches.find_one({"id": branch_id}, {"_id": 0})
        wh = await db.warehouses.find_one(
            {"branch_id": branch_id, "code": WAREHOUSE_CODE},
            {"_id": 0},
        )
        print("OK", {
            "db": db_name,
            "branch": {"id": branch.get("id"), "code": branch.get("code"), "name": branch.get("name")},
            "warehouse": {
                "id": wh.get("id"),
                "code": wh.get("code"),
                "name": wh.get("name"),
                "is_operational": wh.get("is_operational"),
            },
        })
        return 0
    finally:
        client.close()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
