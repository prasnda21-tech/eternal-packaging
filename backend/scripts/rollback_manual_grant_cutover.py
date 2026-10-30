#!/usr/bin/env python3
"""Roll back the manual grant cutover.

Default is dry-run. ``--apply --database <DB_NAME>`` restores the pre-cutover
grant only when the live grant still equals the post-cutover snapshot.
A later manual grant is not overwritten.

Use this only before the new permission evaluator is activated. After that
code is live, restore from a full MongoDB backup instead: putting the
pre-cutover package keys back would make them effective.

Usage:
  cd backend && python3 scripts/rollback_manual_grant_cutover.py
  cd backend && python3 scripts/rollback_manual_grant_cutover.py --apply --database "$DB_NAME"
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

from services.manual_grant_cutover import (  # noqa: E402
    CutoverConflict,
    require_explicit_database,
    rollback_cutover,
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
    parser = argparse.ArgumentParser(description="Dry-run or apply the manual grant rollback")
    parser.add_argument("--apply", action="store_true", help="Restore pre-cutover grants. Requires --database.")
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
        try:
            report = await rollback_cutover(client[db_name], apply=args.apply)
        except CutoverConflict as exc:
            print(json.dumps({
                "restored": False,
                "wrote": exc.wrote,
                "conflicts": exc.conflicts,
                "db_name": db_name,
            }, indent=2, default=str))
            raise SystemExit(2)
        report["db_name"] = db_name
        report["app_env"] = os.environ.get("APP_ENV") or ""
        print(json.dumps(report, indent=2, default=str))
    except SystemExit:
        raise
    except Exception as exc:
        raise SystemExit(_redact(f"rollback failed: {exc}")) from None
    finally:
        client.close()


if __name__ == "__main__":
    asyncio.run(main())
