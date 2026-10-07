#!/usr/bin/env python3
"""Manual local fixture for pre-cutover Stock Saat Ini compatibility demo.

Refuses any database except eternal_packaging_preview.
Does NOT auto-run on startup. Does NOT touch production.

Usage:
  cd backend && python3 scripts/seed_preview_stock_compatibility.py
"""
from __future__ import annotations

import asyncio
import os
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

ALLOWED_DB = "eternal_packaging_preview"


async def main() -> int:
    db_name = os.environ.get("DB_NAME", ALLOWED_DB)
    if db_name != ALLOWED_DB:
        print(f"REFUSED: DB_NAME={db_name!r} (only {ALLOWED_DB} allowed)")
        return 2
    mongo_url = os.environ.get(
        "MONGO_URL",
        "mongodb://127.0.0.1:27017/?replicaSet=eternal_local_rs",
    )
    from motor.motor_asyncio import AsyncIOMotorClient

    client = AsyncIOMotorClient(mongo_url)
    db = client[db_name]
    try:
        # Clear previous demo fixtures only (idempotent demo ids)
        demo_ids = ["demo-box", "demo-cup", "demo-lid"]
        await db.master_items.delete_many({"id": {"$in": demo_ids}})
        await db.product_stock_state.delete_many({"master_item_id": {"$in": demo_ids}})
        await db.orders.delete_many({"id": {"$regex": "^demo-so-"}})
        await db.sales_deliveries.delete_many({"id": {"$regex": "^demo-sj-"}})

        items = [
            {"id": "demo-box", "item_code": "BOX-01", "item_name": "Karton Box", "unit": "PCS", "is_active": True, "accurate_item_id": "demo-acc-box"},
            {"id": "demo-cup", "item_code": "CUP-01", "item_name": "Paper Cup", "unit": "PCS", "is_active": True, "accurate_item_id": "demo-acc-cup"},
            {"id": "demo-lid", "item_code": "LID-01", "item_name": "Cup Lid", "unit": "PCS", "is_active": True, "accurate_item_id": "demo-acc-lid"},
        ]
        for item in items:
            await db.master_items.update_one({"id": item["id"]}, {"$set": item}, upsert=True)
        for state in [
            {"master_item_id": "demo-box", "total_qty": 15900, "unprinted_qty": 15900, "printed_qty": 0, "initialized": True, "status": "INITIALIZED"},
            {"master_item_id": "demo-cup", "total_qty": 1100, "unprinted_qty": 1100, "printed_qty": 0, "initialized": True, "status": "INITIALIZED"},
            {"master_item_id": "demo-lid", "total_qty": 150, "unprinted_qty": 150, "printed_qty": 0, "initialized": True, "status": "INITIALIZED"},
        ]:
            await db.product_stock_state.update_one(
                {"master_item_id": state["master_item_id"]}, {"$set": state}, upsert=True,
            )

        async def upsert_order(doc):
            await db.orders.update_one({"id": doc["id"]}, {"$set": doc}, upsert=True)

        async def upsert_sj(doc):
            await db.sales_deliveries.update_one({"id": doc["id"]}, {"$set": doc}, upsert=True)

        # Active order: ordered 1000, completed 400 → pesanan 600, belum 200 after SJ 200
        await upsert_order({
            "id": "demo-so-active",
            "po_number": "DEMO-SO-ACTIVE",
            "cancelled": False,
            "line_items": [{
                "line_id": "ln-0",
                "master_item_id": "demo-box",
                "quantity": 1000,
                "production_process": "print",
                "name": "Karton Box",
            }],
            "operator_splits": [{"line_idx": 0, "qty_completed": 400}],
            "qty_delivered": 200,
        })
        await upsert_sj({
            "id": "demo-sj-1",
            "po_id": "demo-so-active",
            "sj_number": "SJ-DEMO-1",
            "is_cancelled": False,
            "line_items": [{"line_idx": 0, "master_item_id": "demo-box", "qty": 200}],
            "total_qty": 200,
        })
        await upsert_order({
            "id": "demo-so-cup",
            "po_number": "DEMO-SO-CUP",
            "cancelled": False,
            "line_items": [{
                "line_id": "ln-0",
                "master_item_id": "demo-cup",
                "quantity": 500,
                "production_process": "print",
                "name": "Paper Cup",
            }],
            "operator_splits": [{"line_idx": 0, "qty_completed": 100}],
            "qty_delivered": 0,
        })
        await upsert_order({
            "id": "demo-so-cancelled",
            "po_number": "DEMO-SO-CANCELLED",
            "cancelled": True,
            "line_items": [{
                "line_id": "ln-0",
                "master_item_id": "demo-lid",
                "quantity": 999,
                "production_process": "print",
                "name": "Cup Lid",
            }],
            "operator_splits": [{"line_idx": 0, "qty_completed": 999}],
        })
        await upsert_order({
            "id": "demo-so-lid",
            "po_number": "DEMO-SO-LID",
            "cancelled": False,
            "line_items": [{
                "line_id": "ln-0",
                "master_item_id": "demo-lid",
                "quantity": 50,
                "production_process": "print",
                "name": "Cup Lid",
            }],
            "operator_splits": [{"line_idx": 0, "qty_completed": 50}],
            "qty_delivered": 50,
        })
        await upsert_sj({
            "id": "demo-sj-lid",
            "po_id": "demo-so-lid",
            "sj_number": "SJ-DEMO-LID",
            "is_cancelled": False,
            "line_items": [{"line_idx": 0, "master_item_id": "demo-lid", "qty": 50}],
            "total_qty": 50,
        })
        print("OK seeded eternal_packaging_preview compatibility demo fixtures")
        print("  demo-box: physical=15900 ordered=1000 completed=400 delivered=200")
        print("  demo-cup: physical=1100 ordered=500 completed=100 delivered=0")
        print("  demo-lid: physical=150 ordered=50 completed=50 delivered=50 (+ cancelled ignored)")
        return 0
    finally:
        client.close()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
