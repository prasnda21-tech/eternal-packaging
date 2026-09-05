"""Manual, idempotent Chart of Accounts seed.

Usage (from backend/):
  python -m scripts.seed_chart_of_accounts

Does NOT run on API GET or app startup. Inserts missing codes only —
never overwrites or deletes existing accounts.
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
load_dotenv(ROOT / ".env")

from services.chart_of_accounts import ensure_indexes, load_seed_rows, seed_accounts  # noqa: E402


async def main() -> int:
    mongo = os.environ.get("MONGO_URL") or "mongodb://127.0.0.1:27017"
    db_name = os.environ.get("DB_NAME") or "eternal_packaging"
    rows = load_seed_rows()
    client = AsyncIOMotorClient(mongo)
    db = client[db_name]
    await ensure_indexes(db)
    result = await seed_accounts(db, rows, actor="seed:chart_of_accounts")
    client.close()
    print(
        f"seed chart_of_accounts: inserted={result['inserted']} "
        f"skipped={result['skipped']} total_seed={result['total_seed']} db={db_name}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
