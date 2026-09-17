#!/usr/bin/env python3
"""Idempotent sales_eternal permission readiness. Default: DRY RUN. Not run at startup.

Write only with --apply AND explicit --db-name. Mutates only discovered Ayub and Euis
user documents. Does not contain credentials.
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
    parser = argparse.ArgumentParser(description="sales_eternal permission snapshot (dry-run default)")
    parser.add_argument("--db-name", required=True, help="Explicit Mongo database name (wajib)")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--allow-production", action="store_true")
    args = parser.parse_args()
    mongo_url = os.environ.get("MONGO_URL") or "mongodb://127.0.0.1:27017"
    from services.sales_eternal_deploy import (
        apply_sales_eternal_permissions,
        canonical_user_query,
        plan_sales_eternal_permissions,
        plan_sales_user_mapping,
        refuse_db_target,
        require_explicit_db_name,
    )
    from services.sales_user_mapping import SALES_PWA_ROLE
    db_name = require_explicit_db_name(args.db_name, apply=args.apply)
    refuse_db_target(db_name, allow_production=args.allow_production)
    from motor.motor_asyncio import AsyncIOMotorClient
    client = AsyncIOMotorClient(mongo_url)
    db = client[db_name]
    users = await db.users.find(
        canonical_user_query(),
        {"_id": 0, "id": 1, "email": 1, "employee_id": 1, "role": 1, "status": 1, "permissions_override": 1},
    ).to_list(20)
    identity = plan_sales_user_mapping(None, users)
    if not identity["ok"]:
        print(json.dumps({
            "mode": "APPLY" if args.apply else "DRY_RUN",
            "db_name": db_name,
            "ok": False,
            "errors": identity["errors"],
        }, indent=2, default=str))
        return 2
    snap = await db.role_permissions.find_one({"role": SALES_PWA_ROLE}, {"_id": 0, "permissions": 1})
    plan = plan_sales_eternal_permissions(
        role_snapshot=None if not snap else list(snap.get("permissions") or []),
        existing_users=users,
        mutable_user_ids=identity.get("mutable_user_ids"),
    )
    print(json.dumps({
        "mode": "APPLY" if args.apply else "DRY_RUN",
        "db_name": db_name,
        "plan": plan,
    }, indent=2, default=str))
    if not args.apply:
        return 0 if plan["ok"] else 2
    if not plan["ok"]:
        return 2
    result = await apply_sales_eternal_permissions(db, plan, actor={"email": "permissions-script", "role": "script"})
    print(json.dumps({"apply": result}, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
