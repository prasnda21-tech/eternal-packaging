#!/usr/bin/env python3
"""Local preview UAT for PR #664 SSI Excel — dummy data only (never production)."""
from __future__ import annotations

import asyncio
import json
import os
import sys
import uuid

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, ROOT)

os.environ["DB_NAME"] = os.environ.get("UAT_DB_NAME", "eternal_packaging_preview_uat_664")
mongo = os.environ.get("MONGO_URL", "mongodb://127.0.0.1:27017/?replicaSet=eternal_local_rs")
if "replicaSet=" not in mongo:
    mongo = mongo.rstrip("/") + ("&" if "?" in mongo else "?") + "replicaSet=eternal_local_rs"
os.environ["MONGO_URL"] = mongo

from motor.motor_asyncio import AsyncIOMotorClient

from services.physical_stock import (
    BALANCE_COLLECTION,
    BRANCHES_COLLECTION,
    LEDGER_COLLECTION,
    MASTER_ITEMS_COLLECTION,
    MOVEMENT_OPENING_BALANCE,
    MOVEMENT_SALES_DELIVERY,
    WAREHOUSES_COLLECTION,
    ensure_indexes,
    list_physical_stock_catalog,
)
from services.r2_go_live import set_r2_authority_active
import server


async def reset(db, client):
    await client.drop_database(db.name)


async def seed_preview_admin(db):
    """Keep browser UAT login after isolated DB drop (preview-only)."""
    import bcrypt
    email = "admin@eternalpackaging.com"
    if await db.users.find_one({"email": email}):
        return
    pw = bcrypt.hashpw(b"eternal123", bcrypt.gensalt()).decode()
    await db.users.insert_one({
        "id": "uat-admin-664",
        "email": email,
        "name": "Admin",
        "role": "admin",
        "is_super_admin": True,
        "is_active": True,
        "active": True,
        "password_hash": pw,
    })


async def seed_master(db, code, *, item_type="INVENTORY"):
    item = {
        "id": f"mid-{code.lower()}-{uuid.uuid4().hex[:6]}",
        "item_code": code,
        "item_name": f"Dummy {code}",
        "unit": "PCS",
        "item_type": item_type,
        "is_active": True,
    }
    await db[MASTER_ITEMS_COLLECTION].insert_one(dict(item))
    return item


async def seed_balance_ledger(db, item, branch, wh, *, opening, delivered=0):
    physical = opening - delivered
    await db[BALANCE_COLLECTION].insert_one({
        "id": f"bal-{uuid.uuid4().hex[:8]}",
        "master_item_id": item["id"],
        "branch_id": branch["id"],
        "warehouse_id": wh["id"],
        "physical_qty": physical,
        "baseline_status": "reconciled",
        "updated_at": "2026-10-27T00:00:00+00:00",
    })
    await db[LEDGER_COLLECTION].insert_one({
        "id": f"led-open-{uuid.uuid4().hex[:8]}",
        "master_item_id": item["id"],
        "branch_id": branch["id"],
        "warehouse_id": wh["id"],
        "movement_type": MOVEMENT_OPENING_BALANCE,
        "qty_delta": opening,
        "idempotency_key": f"uat-open-{uuid.uuid4().hex}",
    })
    if delivered:
        await db[LEDGER_COLLECTION].insert_one({
            "id": f"led-del-{uuid.uuid4().hex[:8]}",
            "master_item_id": item["id"],
            "branch_id": branch["id"],
            "warehouse_id": wh["id"],
            "movement_type": MOVEMENT_SALES_DELIVERY,
            "qty_delta": -abs(delivered),
            "idempotency_key": f"uat-del-{uuid.uuid4().hex}",
            "reference_id": f"SJ-{item['item_code']}",
        })
    return physical


async def main():
    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = client[os.environ["DB_NAME"]]
    assert db.name != "eternal_packaging"
    await reset(db, client)
    await seed_preview_admin(db)
    server.db = db
    server.client = client
    await ensure_indexes(db)
    await set_r2_authority_active(db, active=True)

    branch = {
        "id": "br-uat-ygy",
        "code": "UAT-YGY",
        "name": "UAT Yogyakarta",
        "active": True,
    }
    wh = {
        "id": "wh-uat-main",
        "branch_id": branch["id"],
        "code": "GUD-UAT",
        "name": "Gudang UAT",
        "active": True,
        "is_operational": True,
    }
    await db[BRANCHES_COLLECTION].insert_one(dict(branch))
    await db[WAREHOUSES_COLLECTION].insert_one(dict(wh))

    # --- Owner Excel ---
    owner = await seed_master(db, "OWN-EXCEL")
    await seed_balance_ledger(db, owner, branch, wh, opening=10_000, delivered=4_000)
    await db.orders.insert_one({
        "id": "po-uat-owner",
        "branch_id": branch["id"],
        "line_items": [{
            "master_item_id": owner["id"],
            "quantity": 32_000,
            "production_process": "print",
        }],
        "operator_splits": [{"line_idx": 0, "qty_completed": 8_000}],
        "shipments": [{
            "id": "sh-uat-print",
            "delivery_status": "Terkirim",
            "qty_delivered": 4_000,
            "lines": [{"line_idx": 0, "qty": 4_000}],
        }],
    })

    # --- Mixed print + polos ---
    mixed = await seed_master(db, "MIX-PRINT")
    await seed_balance_ledger(db, mixed, branch, wh, opening=10_000, delivered=5_000)
    await db.orders.insert_one({
        "id": "po-uat-mixed",
        "branch_id": branch["id"],
        "line_items": [
            {"master_item_id": mixed["id"], "quantity": 8_000, "production_process": "print"},
            {"master_item_id": mixed["id"], "quantity": 2_000, "production_process": "non_print"},
        ],
        "operator_splits": [{"line_idx": 0, "qty_completed": 8_000}],
        "shipments": [
            {
                "id": "sh-mix-print",
                "delivery_status": "Terkirim",
                "qty_delivered": 4_000,
                "lines": [{"line_idx": 0, "qty": 4_000}],
            },
            {
                "id": "sh-mix-polos",
                "delivery_status": "Terkirim",
                "qty_delivered": 1_000,
                "lines": [{"line_idx": 1, "qty": 1_000}],
            },
        ],
    })

    # --- Multiwarna unproven ---
    multi = await seed_master(db, "MULTI-WARNA")
    await seed_balance_ledger(db, multi, branch, wh, opening=1_000, delivered=0)
    svc = await seed_master(db, "ADDON-WARNA", item_type="SERVICE")
    await db.orders.insert_one({
        "id": "po-uat-multi",
        "branch_id": branch["id"],
        "line_items": [
            {"master_item_id": multi["id"], "quantity": 1_000, "production_process": "print"},
            {
                "master_item_id": svc["id"],
                "quantity": 1_000,
                "production_process": "print",
                "item_type": "SERVICE",
            },
        ],
        "operator_splits": [
            {"line_idx": 0, "qty_completed": 500},
            {"line_idx": 1, "qty_completed": 500},
            {"qty_completed": 100},  # unassigned + 2 print candidates → unproven
        ],
    })

    # --- Partial + cancel ---
    part = await seed_master(db, "PARTIAL-CXL")
    await seed_balance_ledger(db, part, branch, wh, opening=8_000, delivered=3_000)
    await db.orders.insert_one({
        "id": "po-uat-partial",
        "branch_id": branch["id"],
        "line_items": [{
            "master_item_id": part["id"],
            "quantity": 10_000,
            "production_process": "print",
        }],
        "operator_splits": [{"line_idx": 0, "qty_completed": 3_000}],
        "shipments": [{
            "id": "sh-partial",
            "delivery_status": "Terkirim",
            "qty_delivered": 3_000,
            "lines": [{"line_idx": 0, "qty": 3_000}],
        }],
    })
    await db.orders.insert_one({
        "id": "po-uat-cancelled",
        "branch_id": branch["id"],
        "cancelled": True,
        "status": "cancelled",
        "line_items": [{
            "master_item_id": part["id"],
            "quantity": 99_000,
            "production_process": "print",
        }],
        "operator_splits": [{"line_idx": 0, "qty_completed": 99_000}],
    })

    await seed_master(db, "SVC-ONLY", item_type="SERVICE")

    catalog = await list_physical_stock_catalog(db, branch_id=branch["id"], limit=50)
    by_code = {r["item_code"]: r for r in catalog.get("items") or []}

    own = by_code.get("OWN-EXCEL") or {}
    owner_checks = {
        "stock_6000": (own.get("stock") == 6_000 or own.get("physical_qty") == 6_000),
        "dapat_-22000": own.get("stock_dapat_dijual") == -22_000,
        "polos_2000": own.get("qty_polos") == 2_000,
        "belum_4000": own.get("tercetak_belum_kirim") == 4_000,
        "cetak_dikirim_4000": own.get("cetak_sudah_dikirim") == 4_000,
        "tercetak_8000": own.get("qty_tercetak") == 8_000,
    }

    mx = by_code.get("MIX-PRINT") or {}
    mw = by_code.get("MULTI-WARNA") or {}
    pr = by_code.get("PARTIAL-CXL") or {}

    report = {
        "db": db.name,
        "mongo": os.environ["MONGO_URL"],
        "production_write": False,
        "catalog_mode": catalog.get("stock_read_mode"),
        "r2": catalog.get("r2_authority_active"),
        "item_codes": sorted(by_code.keys()),
        "service_excluded": "SVC-ONLY" not in by_code and "ADDON-WARNA" not in by_code,
        "owner_excel": {
            "row": {
                k: own.get(k)
                for k in (
                    "saldo_awal", "pembelian", "stock", "physical_qty",
                    "stock_dapat_dijual", "qty_tercetak", "qty_polos",
                    "tercetak_belum_kirim", "cetak_sudah_dikirim",
                    "cetak_sudah_dikirim_status", "print_delivery_provenance",
                )
            },
            "checks": owner_checks,
            "pass": all(owner_checks.values()),
        },
        "mixed_print_polos": {
            "cetak_sudah_dikirim": mx.get("cetak_sudah_dikirim"),
            "qty_tercetak": mx.get("qty_tercetak"),
            "qty_polos": mx.get("qty_polos"),
            "tercetak_belum_kirim": mx.get("tercetak_belum_kirim"),
            "stock": mx.get("stock") or mx.get("physical_qty"),
            "expect_cetak_4000_not_5000": mx.get("cetak_sudah_dikirim") == 4_000,
            "expect_tercetak_print_only_8000": mx.get("qty_tercetak") == 8_000,
            "expect_polos_1000": mx.get("qty_polos") == 1_000,
            "provenance": mx.get("print_delivery_provenance"),
        },
        "multiwarna_unverified": {
            "qty_tercetak": mw.get("qty_tercetak"),
            "qty_tercetak_status": mw.get("qty_tercetak_status"),
            "qty_sudah_cetak_gudang_status": mw.get("qty_sudah_cetak_gudang_status"),
            "qty_polos": mw.get("qty_polos"),
            "qty_polos_gudang_status": mw.get("qty_polos_gudang_status"),
            "expect_unverified": (
                mw.get("qty_tercetak") is None
                or str(mw.get("qty_tercetak_status") or "").upper() == "UNVERIFIED"
                or mw.get("qty_sudah_cetak_gudang_status") == "BELUM_TERSEDIA"
            ),
        },
        "partial_and_cancel": {
            "stock": pr.get("stock") or pr.get("physical_qty"),
            "stock_dapat_dijual": pr.get("stock_dapat_dijual"),
            "cetak_sudah_dikirim": pr.get("cetak_sudah_dikirim"),
            "po_ordered_excludes_cancelled": pr.get("stock_dapat_dijual") == (
                (pr.get("stock") or pr.get("physical_qty") or 0) - (10_000 - 3_000)
            ),
        },
    }

    out = "/opt/cursor/artifacts/ssi_excel_uat_dummy_report.json"
    with open(out, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)
    print(json.dumps(report, indent=2, ensure_ascii=False))

    ok = (
        report["owner_excel"]["pass"]
        and report["service_excluded"]
        and report["mixed_print_polos"]["expect_cetak_4000_not_5000"]
        and report["mixed_print_polos"]["expect_tercetak_print_only_8000"]
        and report["mixed_print_polos"]["expect_polos_1000"]
        and report["multiwarna_unverified"]["expect_unverified"]
        and report["partial_and_cancel"]["po_ordered_excludes_cancelled"]
    )
    sys.exit(0 if ok else 2)


if __name__ == "__main__":
    asyncio.run(main())
