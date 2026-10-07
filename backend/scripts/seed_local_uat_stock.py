"""Manual local preview seed for Stock Saat Ini UAT.

Does not run on backend startup. Refuses any host or database that is not
the local preview database.

    python3 scripts/seed_local_uat_stock.py
    python3 scripts/seed_local_uat_stock.py --cleanup
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
from datetime import datetime, timezone
from urllib.parse import urlparse

from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

load_dotenv(os.path.join(ROOT, ".env"))

NAMESPACE = "LOCAL-UAT-STOCK-20261004"
ALLOWED_HOSTS = {"127.0.0.1", "localhost", "::1"}
ALLOWED_DB = "eternal_packaging_preview"
FORBIDDEN_ENVS = {"production", "prod"}

ITEMS = (
    ("UAT-001", "12 oz Datar 7 gr Starindo", 1000, None, None),
    ("UAT-002", "12 oz Datar 8 gr SA", 1000, 300, None),
    ("UAT-003", "12 oz Inject Bening Wingoh", 1000, 300, 200),
    ("UAT-004", "12 oz Inject Datar Bening Klir", 100, 300, None),
    ("UAT-005", "12 oz Inject Datar Bening Victory", 500, 500, 500),
    ("UAT-006", "LID Connect Inject Bening Klir", 0, 200, None),
    ("UAT-007", "LID Connect Inject Bening Super", 750, 400, 150),
)


def assert_local_preview() -> tuple[str, str]:
    url = str(os.environ.get("MONGO_URL") or "").strip()
    db_name = str(os.environ.get("DB_NAME") or "").strip()
    app_env = str(os.environ.get("APP_ENV") or "").strip().lower()
    host = (urlparse(url).hostname or "").lower()
    if app_env in FORBIDDEN_ENVS:
        raise SystemExit(f"Refusing to run: APP_ENV={app_env}")
    if host not in ALLOWED_HOSTS:
        raise SystemExit(f"Refusing to run: Mongo host {host or 'unknown'} is not local")
    if db_name != ALLOWED_DB:
        raise SystemExit(f"Refusing to run: database {db_name or 'unknown'} is not {ALLOWED_DB}")
    if not url.startswith("mongodb://"):
        raise SystemExit("Refusing to run: Mongo URI is not a local mongodb:// URL")
    return url, db_name


def item_id(code: str) -> str:
    return f"{NAMESPACE}:{code}".lower()


def order_id(code: str) -> str:
    return f"{NAMESPACE}:SO:{code}"


def opening_key(code: str) -> str:
    return f"{NAMESPACE}:opening:{code}"


async def _actor_id(db) -> str:
    user = await db.users.find_one({"email": "admin@eternalpackaging.com"}, {"_id": 0, "id": 1})
    if user and user.get("id"):
        return str(user["id"])
    return f"{NAMESPACE}:actor"


async def _ygy_main(db) -> tuple[dict, dict]:
    branches = await db.branches.find(
        {"$or": [{"code": "YGY"}, {"name": {"$regex": "yogyakarta", "$options": "i"}}]},
        {"_id": 0},
    ).to_list(20)
    coded = [row for row in branches if str(row.get("code") or "").upper() == "YGY"]
    branch = coded[0] if coded else (branches[0] if len(branches) == 1 else None)
    if not branch or branch.get("active") is not True:
        raise SystemExit("Refusing to run: active YGY branch was not found")
    warehouses = await db.warehouses.find({"branch_id": branch["id"], "active": True}, {"_id": 0}).to_list(20)
    main = [row for row in warehouses if str(row.get("code") or "").upper() == "MAIN"]
    if not main:
        raise SystemExit("Refusing to run: active YGY MAIN warehouse was not found")
    return branch, main[0]


async def _upsert_item(db, code: str, name: str, now: str) -> str:
    master_id = item_id(code)
    existing_code = await db.master_items.find_one({"item_code": code}, {"_id": 0, "id": 1, "uat_namespace": 1})
    if existing_code and existing_code.get("id") != master_id:
        raise SystemExit(f"Refusing to overwrite non-UAT item_code {code}")
    existing = await db.master_items.find_one({"id": master_id}, {"_id": 0, "id": 1, "uat_namespace": 1})
    if existing:
        if existing.get("uat_namespace") != NAMESPACE:
            raise SystemExit(f"Refusing to reuse {code}: namespace mismatch")
        print(f"reuse {code}")
        return master_id
    await db.master_items.insert_one({
        "id": master_id,
        "item_code": code,
        "item_name": name,
        "item_type": "Barang",
        "unit": "PCS",
        "is_active": True,
        "accurate_item_id": f"LOCAL-UAT-{code}",
        "accurate_source": "local_uat_not_accurate",
        "uat_namespace": NAMESPACE,
        "created_at": now,
        "updated_at": now,
    })
    print(f"create {code}")
    return master_id


async def _ensure_opening(db, master_id: str, code: str, qty: int, branch: dict, warehouse: dict, actor: str) -> None:
    from services.physical_stock import post_opening_balance, read_opening_balance

    status = await read_opening_balance(
        db, master_id, branch_id=branch["id"], warehouse_id=warehouse["id"],
    )
    if status.get("established"):
        if int(status.get("opening_qty") or 0) != qty:
            raise SystemExit(f"Refusing to change existing opening for {code}")
        print(f"opening exists {code} {qty}")
        return
    await post_opening_balance(
        db,
        master_id,
        {
            "branch_id": branch["id"],
            "warehouse_id": warehouse["id"],
            "physical_qty": qty,
            "reason": "Saldo awal UAT lokal",
            "idempotency_key": opening_key(code),
        },
        actor_id=actor,
    )
    print(f"opening {code} {qty}")


async def _ensure_order(db, master_id: str, code: str, name: str, ordered: int, completed, now: str) -> None:
    if ordered is None:
        return
    oid = order_id(code)
    existing = await db.orders.find_one({"id": oid}, {"_id": 0, "id": 1, "uat_namespace": 1})
    if existing:
        if existing.get("uat_namespace") != NAMESPACE:
            raise SystemExit(f"Refusing to reuse order {oid}")
        print(f"order exists {code}")
        return
    doc = {
        "id": oid,
        "po_number": f"UAT-SO-{code[-3:]}",
        "customer": "Lokal UAT",
        "customer_name": "Lokal UAT",
        "cancelled": False,
        "is_cancelled": False,
        "status": "open",
        "uat_namespace": NAMESPACE,
        "created_at": now,
        "line_items": [{
            "master_item_id": master_id,
            "item_code": code,
            "name": name,
            "quantity": ordered,
            "unit": "PCS",
            "production_process": "print",
        }],
    }
    if completed is not None:
        doc["operator_splits"] = [{"line_idx": 0, "qty_completed": completed}]
    await db.orders.insert_one(doc)
    print(f"order {code} ordered={ordered} completed={completed}")


async def seed() -> None:
    url, db_name = assert_local_preview()
    from services.physical_stock import ensure_indexes

    client = AsyncIOMotorClient(_transaction_url(url))
    hello = await client.admin.command("hello")
    if hello.get("setName") != "eternal_local_rs" or not hello.get("isWritablePrimary"):
        raise SystemExit("Refusing to run: local replica set eternal_local_rs is not primary")
    db = client[db_name]
    await ensure_indexes(db)
    branch, warehouse = await _ygy_main(db)
    actor = await _actor_id(db)
    now = datetime.now(timezone.utc).isoformat()
    for code, name, qty, ordered, completed in ITEMS:
        master_id = await _upsert_item(db, code, name, now)
        await _ensure_opening(db, master_id, code, qty, branch, warehouse, actor)
        await _ensure_order(db, master_id, code, name, ordered, completed, now)
    print(f"seeded namespace {NAMESPACE} on {branch.get('code')} / {warehouse.get('code')}")
    client.close()


async def cleanup() -> None:
    url, db_name = assert_local_preview()
    client = AsyncIOMotorClient(url)
    db = client[db_name]
    items = await db.master_items.find({"uat_namespace": NAMESPACE}, {"_id": 0, "id": 1, "item_code": 1}).to_list(50)
    ids = [row["id"] for row in items if row.get("id")]
    ledger_query = {
        "$or": [
            {"idempotency_key": {"$regex": f"^{NAMESPACE}"}},
            {"reference_id": {"$regex": f"^{NAMESPACE}"}},
            {"master_item_id": {"$in": ids}, "reference_id": {"$regex": "^opening:local-uat-stock-20261004"}},
        ]
    }
    ledger = await db.physical_stock_ledger.delete_many(ledger_query)
    removed_balances = 0
    for master_id in ids:
        remaining = await db.physical_stock_ledger.count_documents({"master_item_id": master_id})
        if remaining:
            print(f"left balance for {master_id}: {remaining} non-UAT ledger rows remain")
            continue
        result = await db.physical_stock_balances.delete_many({"master_item_id": master_id})
        removed_balances += result.deleted_count
    orders = await db.orders.delete_many({"uat_namespace": NAMESPACE})
    masters = await db.master_items.delete_many({"uat_namespace": NAMESPACE})
    print(
        f"cleanup {NAMESPACE}: items={masters.deleted_count} orders={orders.deleted_count} "
        f"ledger={ledger.deleted_count} balances={removed_balances}"
    )
    client.close()


def _transaction_url(url: str) -> str:
    if "replicaSet=" in url:
        return url
    join = "&" if "?" in url else "?"
    return f"{url}{join}replicaSet=eternal_local_rs"


def main() -> None:
    parser = argparse.ArgumentParser(description="Local preview Stock Saat Ini UAT seed")
    parser.add_argument("--cleanup", action="store_true", help="Remove only this UAT namespace")
    args = parser.parse_args()
    asyncio.run(cleanup() if args.cleanup else seed())


if __name__ == "__main__":
    main()
