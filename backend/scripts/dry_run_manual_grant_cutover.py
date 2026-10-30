#!/usr/bin/env python3
"""Normalize legacy package grants that production does not currently enforce.

Default is dry-run: print a diff and write nothing.

``--apply --database <DB_NAME>`` writes only when ``<DB_NAME>`` equals the
connected ``DB_NAME``. A backup is inserted and counted before any update.
A document that changed since the plan is left untouched and the run stops.
APP_ENV=production is allowed on this explicit path. Do not clear APP_ENV.

Run this while the production permission evaluator is still the live code,
then activate the new evaluator. See docs/yogyakarta-operational-cutover.md.

Usage:
  cd backend && python3 scripts/dry_run_manual_grant_cutover.py
  cd backend && python3 scripts/dry_run_manual_grant_cutover.py --apply --database "$DB_NAME"
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
    CutoverConflict,
    apply_cutover,
    load_targets,
    require_explicit_database,
    summarize_plans,
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


def _redact(text: str) -> str:
    secret = os.environ.get("MONGO_URL") or ""
    if secret:
        text = text.replace(secret, "[redacted]")
    return text


async def main() -> None:
    parser = argparse.ArgumentParser(description="Dry-run or apply the manual grant cutover")
    parser.add_argument("--apply", action="store_true", help="Write backup and updates. Requires --database.")
    parser.add_argument("--database", default="", help="Must equal DB_NAME. Required with --apply.")
    args = parser.parse_args()
    _load_env()
    db_name = os.environ.get("DB_NAME")
    require_explicit_database(args.apply, args.database, db_name)
    mongo_url = os.environ.get("MONGO_URL")
    if not mongo_url or not db_name:
        raise SystemExit("MONGO_URL and DB_NAME are required")
    from motor.motor_asyncio import AsyncIOMotorClient

    client = AsyncIOMotorClient(mongo_url, serverSelectionTimeoutMS=8000)
    try:
        db = client[db_name]
        _roles, _users, plans = await load_targets(db)
        report = summarize_plans(plans)
        report["mode"] = "apply" if args.apply else "dry-run"
        report["db_name"] = db_name
        report["app_env"] = os.environ.get("APP_ENV") or ""
        print(json.dumps(report, indent=2, default=str))
        if args.apply:
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
            try:
                result = await apply_cutover(db, plans, stamp=stamp)
            except CutoverConflict as exc:
                print(json.dumps({
                    "applied": False,
                    "wrote": exc.wrote,
                    "conflicts": exc.conflicts,
                }, indent=2, default=str))
                raise SystemExit(2)
            print(json.dumps({
                "applied": True,
                "documents": result["applied"],
                "backup_collection": result["backup_collection"],
            }))
    except SystemExit:
        raise
    except Exception as exc:
        raise SystemExit(_redact(f"cutover failed: {exc}")) from None
    finally:
        client.close()


if __name__ == "__main__":
    asyncio.run(main())
