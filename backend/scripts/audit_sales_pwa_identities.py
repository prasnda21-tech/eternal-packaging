#!/usr/bin/env python3
"""READ ONLY audit of PWA Sales identities, mapping, and PO counts.

Never writes. No --apply flag. Safe for production when given --db-name.
Does not contain credentials. Discovers users.id from email + employee_id + role.
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
    parser = argparse.ArgumentParser(description="READ ONLY PWA Sales identity audit")
    parser.add_argument("--db-name", required=True, help="Explicit Mongo database name (wajib)")
    parser.add_argument("--allow-production", action="store_true", help="Required to read eternal_packaging")
    args = parser.parse_args()
    if "--apply" in sys.argv:
        print("Refuse: audit script is read-only and has no --apply.", file=sys.stderr)
        return 2

    mongo_url = os.environ.get("MONGO_URL") or "mongodb://127.0.0.1:27017"
    from services.sales_eternal_deploy import (
        canonical_user_query,
        refuse_db_target,
        require_explicit_db_name,
        plan_sales_user_mapping,
    )
    from services.sales_user_mapping import SETTINGS_KEY, STATUS_MATCHED, exact_sales_label
    db_name = require_explicit_db_name(args.db_name, apply=False)
    refuse_db_target(db_name, allow_production=args.allow_production)

    from motor.motor_asyncio import AsyncIOMotorClient
    client = AsyncIOMotorClient(mongo_url)
    db = client[db_name]
    users = await db.users.find(
        canonical_user_query(),
        {"_id": 0, "id": 1, "email": 1, "employee_id": 1, "role": 1, "status": 1, "name": 1, "full_name": 1},
    ).to_list(20)
    existing = await db.app_settings.find_one({"key": SETTINGS_KEY}, {"_id": 0})
    plan = plan_sales_user_mapping(existing, users)
    discovered = plan.get("discovered") or {}

    po_counts = {}
    for key, row in discovered.items():
        uid = row["user_id"]
        po_counts[key] = {
            "sales_user_id": uid,
            "mapped_po_count": await db.orders.count_documents({
                "sales_user_id": uid,
                "cancelled": {"$ne": True},
            }),
            "accurate_label_po_count": await db.orders.count_documents({
                "sales": row["accurate_label"],
                "cancelled": {"$ne": True},
            }),
        }

    unmapped_count = 0
    unmapped_labels = {}
    cursor = db.orders.find(
        {"cancelled": {"$ne": True}},
        {"_id": 0, "sales": 1, "sales_user_id": 1, "sales_ownership_status": 1, "po_number": 1},
    )
    async for doc in cursor:
        if exact_sales_label(doc.get("sales_user_id")):
            continue
        unmapped_count += 1
        label = exact_sales_label(doc.get("sales")) or "(empty)"
        unmapped_labels[label] = unmapped_labels.get(label, 0) + 1

    report = {
        "mode": "READ_ONLY",
        "db_name": db_name,
        "ok": plan.get("ok"),
        "errors": plan.get("errors") or [],
        "identities": discovered,
        "mapping_entries": (existing or {}).get("entries") or [],
        "po_counts": po_counts,
        "unmapped_po_count": unmapped_count,
        "unmapped_labels": unmapped_labels,
        "writes": 0,
        "note": "No database writes. Do not treat similar names as the same person.",
    }
    print(json.dumps(report, indent=2, default=str))
    return 0 if plan.get("ok") else 2


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
