#!/usr/bin/env python3
"""Lightweight read-only production preflight data checks.

FORBIDDEN in this module:
  - to_list(None)
  - unbounded cursor materialization into lists for hashing
  - full-collection JSON serialize / SHA
  - mongodump / full export

ALLOWED:
  - count_documents
  - find_one with sort+limit semantics
  - targeted find_one by known SKU / invoice
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from datetime import date, datetime
from typing import Any


def _load_env(path: str) -> dict[str, str]:
    env: dict[str, str] = {}
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            env[k] = v.strip().strip('"').strip("'")
    return env


def _default(o: Any) -> Any:
    if isinstance(o, (datetime, date)):
        return o.isoformat()
    return str(o)


async def _latest_id(db, collection: str, id_fields: list[str], ts_fields: list[str]) -> dict[str, Any]:
    projection = {"_id": 0}
    for f in id_fields + ts_fields:
        projection[f] = 1
    sort_field = None
    for f in ts_fields:
        sort_field = f
        break
    query = {}
    cursor = db[collection].find(query, projection)
    if sort_field:
        cursor = cursor.sort(sort_field, -1)
    doc = await cursor.limit(1).to_list(1)
    row = doc[0] if doc else None
    if not row:
        return {"found": False}
    out: dict[str, Any] = {"found": True}
    for f in id_fields:
        if f in row:
            out[f] = row[f]
            break
    for f in ts_fields:
        if f in row:
            out["timestamp_field"] = f
            out["timestamp"] = row[f]
            break
    return out


async def _sku_check(db, sku: str, material_id: str | None) -> dict[str, Any]:
    """Targeted critical SKU check — never scans whole collections."""
    state = None
    if material_id:
        state = await db.product_stock_state.find_one(
            {"$or": [{"material_id": material_id}, {"item_id": material_id}, {"id": material_id}]},
            {"_id": 0},
        )
    if state is None:
        state = await db.product_stock_state.find_one(
            {"$or": [{"item_code": sku}, {"sku": sku}, {"code": sku}]},
            {"_id": 0},
        )

    # Prefer inventory valuation helper when available (same as ops contracts).
    valuation: dict[str, Any] = {}
    try:
        sys.path.insert(0, os.environ.get("BACKEND_DIR", "/opt/eternal-packaging/backend"))
        from services.inventory_valuation import lookup_inventory_valuation_item  # type: ignore

        mid = material_id or (state or {}).get("material_id") or (state or {}).get("item_id")
        if mid:
            val = await lookup_inventory_valuation_item(db, mid)
            item = (val or {}).get("item") or {}
            valuation = {
                "item_code": item.get("item_code"),
                "stock_fisik": item.get("stock_fisik"),
                "current_hpp": item.get("current_hpp"),
                "inventory_value": item.get("inventory_value"),
                "hpp_status": item.get("hpp_status"),
                "hpp_source": item.get("hpp_source"),
            }
    except Exception as exc:  # noqa: BLE001 — preflight must not crash deploy on import quirks
        valuation = {"error": f"valuation_lookup_failed:{type(exc).__name__}"}

    slim_state = None
    if state:
        slim_state = {
            k: state.get(k)
            for k in (
                "id",
                "material_id",
                "item_id",
                "item_code",
                "sku",
                "qty",
                "stock_fisik",
                "on_hand",
                "updated_at",
            )
            if k in state
        }

    return {"sku": sku, "material_id": material_id, "stock_state": slim_state, "valuation": valuation}


async def run_preflight(env_path: str, sku: str, material_id: str | None) -> dict[str, Any]:
    from motor.motor_asyncio import AsyncIOMotorClient

    env = _load_env(env_path)
    uri = env.get("MONGO_URL") or env.get("MONGODB_URL")
    dbn = env.get("DB_NAME") or "eternal_packaging"
    if not uri:
        raise SystemExit("MONGO_URL missing in backend .env")

    client = AsyncIOMotorClient(uri, serverSelectionTimeoutMS=5000)
    db = client[dbn]
    await db.command("ping")

    counts = {}
    for name in (
        "purchase_orders",
        "purchase_goods_receipts",
        "purchase_invoices",
        "purchase_payments",
        "product_stock_state",
        "stock_movements",
        "orders",
        "hpp_recognition_events",
    ):
        counts[name] = await db[name].count_documents({})

    latest = {
        "stock_movements": await _latest_id(
            db, "stock_movements", ["id", "event_id", "movement_id"], ["created_at", "timestamp", "updated_at"]
        ),
        "hpp_recognition_events": await _latest_id(
            db, "hpp_recognition_events", ["id", "event_id"], ["created_at", "recognized_at", "updated_at"]
        ),
        "purchase_invoices": await _latest_id(
            db, "purchase_invoices", ["id", "invoice_number"], ["created_at", "invoice_date", "updated_at"]
        ),
    }

    sku_check = await _sku_check(db, sku, material_id)

    invoices_total = counts["purchase_invoices"]
    invoices_due_set = await db.purchase_invoices.count_documents(
        {"due_date": {"$ne": None, "$exists": True}}
    )
    invoices_without_due = await db.purchase_invoices.count_documents(
        {"$or": [{"due_date": None}, {"due_date": {"$exists": False}}]}
    )

    client.close()
    return {
        "ok": True,
        "mode": "lightweight_preflight",
        "forbidden": ["to_list(None)", "full_collection_hash", "unbounded_materialization"],
        "counts": counts,
        "latest": latest,
        "schema": {
            "invoices_total": invoices_total,
            "invoices_due_set": invoices_due_set,
            "invoices_without_due": invoices_without_due,
        },
        "critical_sku": sku_check,
        "note": (
            "Counts/IDs may change during a live 24/7 window due to legitimate user activity. "
            "Do not treat inequality of global hashes as deploy corruption."
        ),
    }


def main() -> None:
    p = argparse.ArgumentParser(description="Lightweight RO deploy preflight (no full collection hash)")
    p.add_argument("--env", default=os.environ.get("BACKEND_ENV", "/opt/eternal-packaging/backend/.env"))
    p.add_argument("--sku", default=os.environ.get("CRITICAL_SKU", "100442"))
    p.add_argument("--material-id", default=os.environ.get("CRITICAL_SKU_MATERIAL_ID", ""))
    p.add_argument("--out", default="")
    args = p.parse_args()
    material_id = args.material_id or None
    result = asyncio.run(run_preflight(args.env, args.sku, material_id))
    text = json.dumps(result, indent=2, default=_default)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(text)
            f.write("\n")
    print(text)


if __name__ == "__main__":
    main()
