"""Stamp legacy payroll periods and narrow the monthly unique index.

Default is dry-run (zero writes). Apply with:
  python -m scripts.migrate_piece_payroll_indexes --apply

The full unique index on (year, month, revision) stays in place until every
legacy document has period_scope=monthly and the partial unique index exists.
Safe to run again. Does not run at API startup and must not be aimed at
production without review.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path
from typing import Any, Dict, Optional

from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
load_dotenv(ROOT / ".env")

from services.hr_payroll import PERIODS  # noqa: E402
from services.operator_wage_claims import ensure_piece_indexes  # noqa: E402
from services.purchase_common import now_iso  # noqa: E402

PARTIAL_NAME = "uniq_monthly_period_revision"
MONTHLY_KEYS = [("year", 1), ("month", 1), ("revision", 1)]


def _keys(spec: dict) -> list:
    return [(str(field), int(direction)) for field, direction in (spec.get("key") or [])]


def legacy_monthly_index(info: Dict[str, dict]) -> Optional[str]:
    for name, spec in (info or {}).items():
        if _keys(spec) != MONTHLY_KEYS:
            continue
        if spec.get("unique") and not spec.get("partialFilterExpression"):
            return name
    return None


def partial_monthly_index(info: Dict[str, dict]) -> Optional[str]:
    for name, spec in (info or {}).items():
        if _keys(spec) != MONTHLY_KEYS or not spec.get("unique"):
            continue
        partial = spec.get("partialFilterExpression") or {}
        if partial.get("period_scope") == "monthly":
            return name
    return None


async def inspect(db) -> Dict[str, Any]:
    info = await db[PERIODS].index_information()
    missing = await db[PERIODS].count_documents({"period_scope": {"$exists": False}})
    nulls = await db[PERIODS].count_documents({"period_scope": {"$type": "null"}})
    return {
        "collection": PERIODS,
        "legacy_index": legacy_monthly_index(info),
        "partial_index": partial_monthly_index(info),
        "unstamped": missing + nulls,
        "indexes": sorted(info),
    }


async def _create_partial(db) -> str:
    info = await db[PERIODS].index_information()
    existing = partial_monthly_index(info)
    if existing:
        return existing
    await db[PERIODS].create_index(
        MONTHLY_KEYS,
        unique=True,
        name=PARTIAL_NAME,
        partialFilterExpression={"period_scope": "monthly"},
    )
    return PARTIAL_NAME


async def _stamp_one(db) -> bool:
    row = await db[PERIODS].find_one(
        {"$or": [{"period_scope": {"$exists": False}}, {"period_scope": None}]},
        {"_id": 1, "id": 1, "year": 1, "month": 1, "revision": 1},
    )
    if not row:
        return False
    result = await db[PERIODS].update_one(
        {"_id": row["_id"], "$or": [{"period_scope": {"$exists": False}}, {"period_scope": None}]},
        {"$set": {"period_scope": "monthly", "period_scope_stamped_at": now_iso()}},
    )
    if result.modified_count != 1:
        raise RuntimeError(f"Stempel periode {row.get('id')} tidak tersimpan.")
    return True


async def run(db, *, apply: bool) -> Dict[str, Any]:
    before = await inspect(db)
    plan = {
        "dry_run": not apply,
        "before": before,
        "steps": [
            "create partial unique index uniq_monthly_period_revision where period_scope=monthly",
            "stamp period_scope=monthly on documents that have no scope",
            "drop the non-partial unique (year, month, revision) only after the stamp count is zero",
            "create piece wage and bonus-day indexes",
        ],
    }
    if not apply:
        plan["after"] = before
        return plan
    partial = await _create_partial(db)
    stamped = 0
    while await _stamp_one(db):
        stamped += 1
    after_stamp = await inspect(db)
    if after_stamp["unstamped"] != 0:
        plan.update({"partial_index": partial, "stamped": stamped, "after": after_stamp, "dropped": None, "stopped": "unstamped_remain"})
        return plan
    info = await db[PERIODS].index_information()
    legacy = legacy_monthly_index(info)
    dropped = None
    if legacy and partial_monthly_index(info):
        await db[PERIODS].drop_index(legacy)
        dropped = legacy
    await ensure_piece_indexes(db)
    plan.update({
        "partial_index": partial,
        "stamped": stamped,
        "dropped": dropped,
        "after": await inspect(db),
        "stopped": None,
    })
    return plan


def main() -> None:
    parser = argparse.ArgumentParser(description="Migrate piece payroll period indexes")
    parser.add_argument("--apply", action="store_true", help="Write indexes and stamps. Default is dry-run.")
    parser.add_argument("--mongo-url", default=os.environ.get("MONGO_URL", "mongodb://127.0.0.1:27017"))
    parser.add_argument("--db", default=os.environ.get("DB_NAME", "eternal_packaging"))
    args = parser.parse_args()
    client = AsyncIOMotorClient(args.mongo_url)
    result = asyncio.run(run(client[args.db], apply=args.apply))
    import json
    print(json.dumps(result, default=str, indent=2))


if __name__ == "__main__":
    main()
