#!/usr/bin/env python3
"""Idempotent HOLDING_TEAM package permission top-up for future R2 go-live.

Default: dry-run (no writes).
Apply: pass --apply (still refuses production DB name unless --allow-production).

Usage:
  python scripts/top_up_holding_team_permissions.py
  python scripts/top_up_holding_team_permissions.py --apply
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

PRODUCTION_DB = "eternal_packaging"


async def main() -> int:
    parser = argparse.ArgumentParser(description="HOLDING_TEAM R2 package permission top-up")
    parser.add_argument("--apply", action="store_true", help="Write role_permissions (default dry-run)")
    parser.add_argument("--allow-production", action="store_true", help="Required to touch eternal_packaging")
    parser.add_argument("--mongo-url", default=os.environ.get("MONGO_URL", "mongodb://127.0.0.1:27017"))
    parser.add_argument("--db-name", default=os.environ.get("DB_NAME", "eternal_packaging_preview"))
    args = parser.parse_args()

    if args.db_name == PRODUCTION_DB and not args.allow_production:
        print(json.dumps({
            "error": "Refusing production DB without --allow-production",
            "db_name": args.db_name,
            "applied": False,
        }, indent=2))
        return 2

    from motor.motor_asyncio import AsyncIOMotorClient
    from services.r2_go_live import apply_holding_team_permission_top_up

    client = AsyncIOMotorClient(args.mongo_url)
    db = client[args.db_name]
    try:
        result = await apply_holding_team_permission_top_up(db, apply=bool(args.apply))
        print(json.dumps(result, indent=2, default=str))
        return 0
    finally:
        client.close()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
