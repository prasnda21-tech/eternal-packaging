# DO NOT USE IN PRODUCTION DEPLOY
# Quarantined legacy heavy snapshot. Confirmed OOM risk (to_list(None) / full collection hash).
# Use scripts/safe-deploy/lib/preflight_data.py instead.
# Raises if executed.

raise SystemExit("REFUSING TO RUN: quarantined heavy snapshot. Use scripts/safe-deploy instead.")

import asyncio, hashlib, json, os
from motor.motor_asyncio import AsyncIOMotorClient
from bson import ObjectId
from datetime import datetime, date

env = {}
with open("/opt/eternal-packaging/backend/.env") as f:
    for line in f:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        env[k] = v.strip().strip('"').strip("'")
uri = env.get("MONGO_URL") or env.get("MONGODB_URL")
dbn = env.get("DB_NAME") or "eternal_packaging"

def default(o):
    if isinstance(o, ObjectId):
        return str(o)
    if isinstance(o, (datetime, date)):
        return o.isoformat()
    return str(o)

async def col_hash(db, name):
    docs = await db[name].find({}, {"_id": 0}).to_list(None)
    norm = [json.loads(json.dumps(d, default=default, sort_keys=True)) for d in docs]
    norm.sort(key=lambda d: json.dumps(d, sort_keys=True, separators=(",", ":")))
    payload = json.dumps(norm, sort_keys=True, separators=(",", ":"))
    return {"count": len(docs), "sha256": hashlib.sha256(payload.encode()).hexdigest()}

async def main():
    db = AsyncIOMotorClient(uri)[dbn]
    cols = [
        "purchase_orders", "purchase_goods_receipts", "purchase_invoices", "purchase_payments",
        "product_stock_state", "stock_movements", "orders",
        "hpp_recognition_events", "hpp_cost_versions", "hpp_cost_layers", "customer_pricing_versions",
    ]
    hashes = {c: await col_hash(db, c) for c in cols}
    mid = "cf11da27-f4e8-484b-9fd0-b18aac58e5e8"
    from services.inventory_valuation import lookup_inventory_valuation_item
    import sys
    sys.path.insert(0, "/opt/eternal-packaging/backend")
    val = await lookup_inventory_valuation_item(db, mid)
    item = (val or {}).get("item") or {}
    inv = await db.purchase_invoices.find_one({"invoice_number": "FPB-20260928-001"}, {"_id": 0, "invoice_number": 1, "due_date": 1, "invoice_date": 1})
    out = {
        "hashes": hashes,
        "valuation": {
            "item_code": item.get("item_code"),
            "stock_fisik": item.get("stock_fisik"),
            "current_hpp": item.get("current_hpp"),
            "inventory_value": item.get("inventory_value"),
            "hpp_status": item.get("hpp_status"),
            "hpp_source": item.get("hpp_source"),
        },
        "invoice_fpb": inv,
        "schema": {
            "invoices_total": await db.purchase_invoices.count_documents({}),
            "invoices_due_set": await db.purchase_invoices.count_documents({"due_date": {"$ne": None, "$exists": True}}),
            "invoices_without_due": await db.purchase_invoices.count_documents({"$or": [{"due_date": None}, {"due_date": {"$exists": False}}]}),
        },
    }
    print(json.dumps(out, indent=2, default=str))

asyncio.run(main())
