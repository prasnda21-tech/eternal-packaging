#!/usr/bin/env python3
"""Normalize legacy package grants that were stored but not effective.

Default is dry-run: print a diff and write nothing.
``--apply`` copies the original documents into ``manual_grant_cutover_backups``
and then updates role_permissions and users. Apply is refused when
APP_ENV=production. Do not point this script at a production database.

Holding and Direktur keep package keys that were already in the grant.
Other roles lose package keys that were not in the old confirmation stamp.
Already-normalized documents are skipped, so a second run is idle.

Usage:
  cd backend && python3 scripts/dry_run_manual_grant_cutover.py
  cd backend && python3 scripts/dry_run_manual_grant_cutover.py --apply
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from services.manual_grant_cutover import (  # noqa: E402
    plan_role_document,
    plan_user_document,
    summarize_plans,
)
from services.purchasing_inventory_access import (  # noqa: E402
    MANUAL_GRANT_NORMALIZED_FIELD,
    PACKAGE_CONFIRMED_KEYS_FIELD,
)


def _load_env() -> None:
    env_path = ROOT / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text().splitlines():
        text = line.strip()
        if not text or text.startswith("#") or "=" not in text:
            continue
        key, value = text.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def _refuse_production_apply(apply: bool) -> None:
    if apply and str(os.environ.get("APP_ENV") or "").strip().lower() == "production":
        raise SystemExit("REFUSED: --apply is not allowed when APP_ENV=production")


async def _collect(db):
    roles = await db.role_permissions.find({}, {"_id": 0}).to_list(500)
    users = await db.users.find(
        {},
        {"_id": 0, "password_hash": 0, "password": 0},
    ).to_list(5000)
    plans = [plan_role_document(doc) for doc in roles]
    plans.extend(plan_user_document(doc) for doc in users)
    return roles, users, plans


async def _apply(db, roles, users, plans) -> None:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup = db.manual_grant_cutover_backups
    role_by_id = {doc.get("role"): doc for doc in roles}
    user_by_id = {(doc.get("email") or doc.get("id")): doc for doc in users}
    for plan in plans:
        if plan.get("action") != "normalize":
            continue
        source = role_by_id.get(plan["id"]) if plan["kind"] == "role" else user_by_id.get(plan["id"])
        if not source:
            continue
        await backup.insert_one({
            "cutover": "manual_grant_v1",
            "at": stamp,
            "kind": plan["kind"],
            "doc_id": plan["id"],
            "before": source,
        })
        if plan["kind"] == "role":
            await db.role_permissions.update_one(
                {"role": plan["id"], MANUAL_GRANT_NORMALIZED_FIELD: {"$ne": True}},
                {"$set": {
                    "permissions": plan["next_permissions"],
                    PACKAGE_CONFIRMED_KEYS_FIELD: plan["next_confirmed_keys"],
                    MANUAL_GRANT_NORMALIZED_FIELD: True,
                    "manual_grant_cutover_at": stamp,
                }},
            )
        else:
            await db.users.update_one(
                {"email": plan["id"], MANUAL_GRANT_NORMALIZED_FIELD: {"$ne": True}},
                {"$set": {
                    "permissions_override": plan["next_override"],
                    PACKAGE_CONFIRMED_KEYS_FIELD: plan["next_confirmed_keys"],
                    MANUAL_GRANT_NORMALIZED_FIELD: True,
                    "manual_grant_cutover_at": stamp,
                }},
            )


async def main() -> None:
    parser = argparse.ArgumentParser(description="Dry-run or apply the manual grant cutover")
    parser.add_argument("--apply", action="store_true", help="Write backup and updates. Refused in production.")
    args = parser.parse_args()
    _load_env()
    _refuse_production_apply(args.apply)
    mongo_url = os.environ.get("MONGO_URL")
    db_name = os.environ.get("DB_NAME")
    if not mongo_url or not db_name:
        raise SystemExit("MONGO_URL and DB_NAME are required")
    from motor.motor_asyncio import AsyncIOMotorClient

    client = AsyncIOMotorClient(mongo_url)
    try:
        roles, users, plans = await _collect(client[db_name])
        report = summarize_plans(plans)
        report["mode"] = "apply" if args.apply else "dry-run"
        report["db_name"] = db_name
        report["app_env"] = os.environ.get("APP_ENV") or ""
        print(json.dumps(report, indent=2, default=str))
        if args.apply:
            await _apply(client, roles, users, plans)
            print(json.dumps({"applied": True, "backup_collection": "manual_grant_cutover_backups"}))
    finally:
        client.close()


if __name__ == "__main__":
    asyncio.run(main())
