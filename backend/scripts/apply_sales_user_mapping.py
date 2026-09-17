#!/usr/bin/env python3
"""Idempotent Accurate sales-label mapping. Default: DRY RUN. Not run at startup.

Write only with --apply AND explicit --db-name. Does not read DB_NAME from env
for writes. Does not contain credentials. users.id is discovered at runtime.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parents[1] / ".env")


async def main() -> int:
    parser = argparse.ArgumentParser(description="Canonical sales_user_mapping (dry-run default)")
    parser.add_argument("--db-name", required=True, help="Explicit Mongo database name (wajib)")
    parser.add_argument("--apply", action="store_true", help="Write mapping. Default is dry-run.")
    parser.add_argument("--allow-production", action="store_true", help="Required to touch eternal_packaging")
    args = parser.parse_args()

    mongo_url = os.environ.get("MONGO_URL") or "mongodb://127.0.0.1:27017"
    from services.sales_eternal_deploy import (
        apply_sales_user_mapping,
        canonical_user_query,
        plan_sales_user_mapping,
        refuse_db_target,
        require_explicit_db_name,
    )
    db_name = require_explicit_db_name(args.db_name, apply=args.apply)
    refuse_db_target(db_name, allow_production=args.allow_production)

    from motor.motor_asyncio import AsyncIOMotorClient
    client = AsyncIOMotorClient(mongo_url)
    db = client[db_name]
    existing = await db.app_settings.find_one({"key": "sales_user_mapping"}, {"_id": 0})
    found = await db.users.find(
        canonical_user_query(),
        {"_id": 0, "id": 1, "email": 1, "employee_id": 1, "role": 1, "status": 1, "name": 1},
    ).to_list(20)
    plan = plan_sales_user_mapping(existing, found)
    print(json.dumps({
        "mode": "APPLY" if args.apply else "DRY_RUN",
        "db_name": db_name,
        "plan": {
            k: plan[k] for k in (
                "ok", "errors", "actions", "changed", "before", "after",
                "discovered", "profiles",
            ) if k in plan
        },
    }, indent=2, default=str))
    if not args.apply:
        return 0 if plan["ok"] else 2
    if not plan["ok"]:
        print("Refuse APPLY because plan has errors.", file=sys.stderr)
        return 2
    result = await apply_sales_user_mapping(db, plan, actor={"email": "mapping-script", "role": "script"})
    print(json.dumps({"apply": result}, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
