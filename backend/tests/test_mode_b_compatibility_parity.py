"""Parity: Mode B Qty Pesanan / Belum Terkirim vs Compatibility on same orders."""
from __future__ import annotations

import os
import uuid

import pytest
from motor.motor_asyncio import AsyncIOMotorClient

os.environ["DB_NAME"] = "eternal_packaging_test_mode_b_compat_parity"
os.environ.setdefault(
    "MONGO_URL",
    "mongodb://127.0.0.1:27017/?replicaSet=eternal_local_rs",
)

from services.mode_b_legacy_demand import configure_mode_b_legacy_demand  # noqa: E402
from services.physical_stock import (  # noqa: E402
    BALANCE_COLLECTION,
    BASELINE_RECONCILED,
    BRANCHES_COLLECTION,
    MASTER_ITEMS_COLLECTION,
    WAREHOUSES_COLLECTION,
    aggregate_operational_orders,
    list_physical_stock_catalog,
    operational_availability,
)
from services.r2_go_live import set_r2_authority_active  # noqa: E402
from services.stock_compatibility_read import (  # noqa: E402
    aggregate_legacy_delivered_qty,
    list_compatibility_stock_catalog,
)
from services.warehouse_stock_standalone import (  # noqa: E402
    set_warehouse_stock_standalone_enabled,
)
import server  # noqa: E402

TEST_DB = "eternal_packaging_test_mode_b_compat_parity"
MONGO_URL = os.environ["MONGO_URL"]
if "replicaSet=" not in MONGO_URL:
    MONGO_URL = MONGO_URL.rstrip("/") + ("&" if "?" in MONGO_URL else "?") + "replicaSet=eternal_local_rs"

pytestmark = pytest.mark.anyio

CUTOFF = "2026-10-01T00:00:00+00:00"
HIST = "2026-09-01T00:00:00+00:00"


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
async def db():
    client = AsyncIOMotorClient(MONGO_URL)
    database = client[TEST_DB]
    await client.drop_database(TEST_DB)
    server.db = database
    await set_r2_authority_active(database, active=False)
    try:
        yield database
    finally:
        await client.drop_database(TEST_DB)
        client.close()


async def _seed_item(db, code):
    doc = {
        "id": f"item-{uuid.uuid4().hex[:8]}",
        "item_code": code,
        "item_name": code,
        "unit": "pcs",
        "item_type": "BARANG",
        "is_active": True,
    }
    await db[MASTER_ITEMS_COLLECTION].insert_one(dict(doc))
    return doc


def _po(oid, item, qty, completed, *, branch_id="50", shipped=0, cancelled=False, qty_delivered=None):
    shipments = []
    confirmations = []
    if shipped > 0:
        shipments = [{
            "id": f"sh-{oid}",
            "delivery_status": "Terkirim",
            "delivered_at": HIST,
            "qty_delivered": shipped,
            "lines": [{"line_idx": 0, "qty": shipped}],
        }]
        confirmations = [{
            "shipment_id": f"sh-{oid}",
            "items": [{"line_idx": 0, "qty": shipped}],
        }]
    doc = {
        "id": oid,
        "po_number": f"PO/{oid}",
        "created_at": HIST,
        "branch_id": branch_id,
        "line_items": [{
            "master_item_id": item["id"],
            "quantity": qty,
            "production_process": "print",
            "line_idx": 0,
        }],
        "operator_splits": (
            [{"line_idx": 0, "qty_completed": completed}] if completed else []
        ),
        "shipments": shipments,
        "delivery_confirmations": confirmations,
    }
    if qty_delivered is not None:
        doc["qty_delivered"] = qty_delivered
    if cancelled:
        doc["cancelled"] = True
    return doc


async def test_pesanan_and_belum_parity_on_attributed_historical_set(db):
    """Same attributed order set → Compatibility helpers == Mode B catalog buckets."""
    ygy = {
        "id": f"br-ygy-{uuid.uuid4().hex[:8]}",
        "code": "YGY",
        "name": "Yogyakarta",
        "active": True,
    }
    await db[BRANCHES_COLLECTION].insert_one(dict(ygy))
    wh = {
        "id": f"wh-main-{uuid.uuid4().hex[:8]}",
        "branch_id": ygy["id"],
        "code": "MAIN",
        "name": "MAIN",
        "active": True,
        "is_operational": True,
    }
    await db[WAREHOUSES_COLLECTION].insert_one(dict(wh))

    cases = {
        "ACTIVE": await _seed_item(db, "P-ACTIVE"),       # ordered, not completed
        "READY": await _seed_item(db, "P-READY"),         # completed, not shipped
        "PARTIAL": await _seed_item(db, "P-PARTIAL"),     # completed, partial ship
        "FULL": await _seed_item(db, "P-FULL"),           # completed, full ship
        "CANCEL": await _seed_item(db, "P-CANCEL"),       # cancelled
        "NULLBR": await _seed_item(db, "P-NULL"),         # null branch historical
        "FALLBACK": await _seed_item(db, "P-FALLBACK"),   # single-line qty_delivered fallback
    }
    for item in cases.values():
        await db[BALANCE_COLLECTION].insert_one({
            "id": f"bal-{uuid.uuid4().hex[:8]}",
            "master_item_id": item["id"],
            "branch_id": ygy["id"],
            "warehouse_id": wh["id"],
            "physical_qty": 1000,
            "baseline_status": BASELINE_RECONCILED,
        })
        await db.product_stock_state.insert_one({
            "master_item_id": item["id"],
            "total_qty": 1000,
            "initialized": True,
            "status": "INITIALIZED",
        })

    await db.orders.insert_many([
        _po("o-active", cases["ACTIVE"], 100, 0, branch_id="50"),
        _po("o-ready", cases["READY"], 80, 80, branch_id="50"),
        _po("o-part", cases["PARTIAL"], 100, 100, branch_id="50", shipped=40),
        _po("o-full", cases["FULL"], 60, 60, branch_id="50", shipped=60),
        _po("o-can", cases["CANCEL"], 50, 50, branch_id="50", cancelled=True),
        _po("o-null", cases["NULLBR"], 30, 10, branch_id=None),
        _po("o-fb", cases["FALLBACK"], 25, 25, branch_id="50", qty_delivered=25),
    ])
    await db.orders.update_one({"id": "o-null"}, {"$unset": {"branch_id": "", "cabang_id": ""}})

    await configure_mode_b_legacy_demand(
        db,
        enabled=True,
        org_branch_id=ygy["id"],
        historical_cutoff_at=CUTOFF,
        accurate_branch_ids=["50"],
        include_null_branch=True,
    )

    # --- Reference: Compatibility aggregation on the attributed order subset ---
    from services.mode_b_legacy_demand import filter_orders_for_branch, get_mode_b_legacy_demand_settings
    from services.physical_stock import _unique_accurate_owners

    settings = await get_mode_b_legacy_demand_settings(db)
    all_orders = await db.orders.find({}, {"_id": 0}).to_list(5000)
    attributed = filter_orders_for_branch(all_orders, ygy["id"], settings)
    owners = await _unique_accurate_owners(db)
    progress = aggregate_operational_orders(attributed, owners)
    delivered, provenance = aggregate_legacy_delivered_qty(attributed, [], owners)

    expected = {}
    for code, item in cases.items():
        mid = item["id"]
        demand = progress.get(mid) or {"ordered_qty": 0.0, "completed_qty": 0.0}
        buckets = operational_availability(
            physical_qty=1000,
            ordered_qty=demand["ordered_qty"],
            completed_qty=demand["completed_qty"],
            r2_delivered_qty=delivered.get(mid, 0),
        )
        expected[code] = {
            "pesanan": buckets["qty_pesanan"],
            "belum": buckets["qty_belum_terkirim"],
            "prov": provenance.get(mid),
        }

    assert expected["ACTIVE"]["pesanan"] == 100 and expected["ACTIVE"]["belum"] == 0
    assert expected["READY"]["pesanan"] == 0 and expected["READY"]["belum"] == 80
    assert expected["PARTIAL"]["pesanan"] == 0 and expected["PARTIAL"]["belum"] == 60
    assert expected["FULL"]["pesanan"] == 0 and expected["FULL"]["belum"] == 0
    assert expected["CANCEL"]["pesanan"] == 0 and expected["CANCEL"]["belum"] == 0
    assert expected["NULLBR"]["pesanan"] == 20 and expected["NULLBR"]["belum"] == 10
    assert expected["FALLBACK"]["pesanan"] == 0 and expected["FALLBACK"]["belum"] == 0
    assert expected["FALLBACK"]["prov"] == "order_qty_delivered"

    # --- Mode B catalog ---
    await set_warehouse_stock_standalone_enabled(db, enabled=True)
    mode_b = await list_physical_stock_catalog(db, branch_id=ygy["id"])
    by_code = {r["item_code"]: r for r in mode_b["items"]}
    for code, exp in expected.items():
        row = by_code[f"P-{code}" if code != "NULLBR" else "P-NULL"]
        # map codes to item_code
    by_code = {r["item_code"]: r for r in mode_b["items"]}
    mapping = {
        "ACTIVE": "P-ACTIVE",
        "READY": "P-READY",
        "PARTIAL": "P-PARTIAL",
        "FULL": "P-FULL",
        "CANCEL": "P-CANCEL",
        "NULLBR": "P-NULL",
        "FALLBACK": "P-FALLBACK",
    }
    diffs = []
    for key, item_code in mapping.items():
        row = by_code[item_code]
        exp = expected[key]
        if row["qty_pesanan"] != exp["pesanan"] or row["qty_belum_terkirim"] != exp["belum"]:
            diffs.append({
                "item": item_code,
                "mode_b": (row["qty_pesanan"], row["qty_belum_terkirim"]),
                "compat_ref": (exp["pesanan"], exp["belum"]),
                "provenance": exp["prov"],
            })
        assert row["qty_pesanan"] == exp["pesanan"], key
        assert row["qty_belum_terkirim"] == exp["belum"], key
    assert diffs == []

    # --- Compatibility catalog — same demand math; physical authority differs ---
    await set_warehouse_stock_standalone_enabled(db, enabled=False)
    compat = await list_compatibility_stock_catalog(db)
    compat_by = {r["item_code"]: r for r in compat["items"]}
    for key, item_code in mapping.items():
        assert compat_by[item_code]["qty_pesanan"] == expected[key]["pesanan"], key
        assert compat_by[item_code]["qty_belum_terkirim"] == expected[key]["belum"], key
    assert compat["stock_read_mode"] == "compatibility"
    await set_warehouse_stock_standalone_enabled(db, enabled=True)
    mode_b2 = await list_physical_stock_catalog(db, branch_id=ygy["id"])
    assert mode_b2["stock_read_mode"] == "warehouse_standalone"
    # Stock Fisik authority: Mode B warehouse book vs Compatibility company PSS
    assert mode_b2["items"][0]["physical_qty_is_book"] is True


async def test_sj_status_alone_not_in_either_path(db):
    ygy = {
        "id": f"br-ygy-{uuid.uuid4().hex[:8]}",
        "code": "YGY",
        "name": "Yogyakarta",
        "active": True,
    }
    await db[BRANCHES_COLLECTION].insert_one(dict(ygy))
    wh = {
        "id": f"wh-{uuid.uuid4().hex[:8]}",
        "branch_id": ygy["id"],
        "code": "MAIN",
        "name": "MAIN",
        "active": True,
        "is_operational": True,
    }
    await db[WAREHOUSES_COLLECTION].insert_one(dict(wh))
    item = await _seed_item(db, "P-SJONLY")
    await db[BALANCE_COLLECTION].insert_one({
        "id": f"bal-{uuid.uuid4().hex[:8]}",
        "master_item_id": item["id"],
        "branch_id": ygy["id"],
        "warehouse_id": wh["id"],
        "physical_qty": 100,
        "baseline_status": BASELINE_RECONCILED,
    })
    await db.product_stock_state.insert_one({
        "master_item_id": item["id"],
        "total_qty": 100,
        "initialized": True,
        "status": "INITIALIZED",
    })
    await db.orders.insert_one(_po("sj1", item, 40, 40, branch_id="50"))
    await db.sales_deliveries.insert_one({
        "id": "sj-doc",
        "po_id": "sj1",
        "status": "SELESAI",
        "line_items": [{"line_idx": 0, "qty": 40, "master_item_id": item["id"]}],
    })
    await configure_mode_b_legacy_demand(
        db, enabled=True, org_branch_id=ygy["id"], historical_cutoff_at=CUTOFF,
        accurate_branch_ids=["50"], include_null_branch=True,
    )
    await set_warehouse_stock_standalone_enabled(db, enabled=True)
    mb = (await list_physical_stock_catalog(db, branch_id=ygy["id"], q="P-SJONLY"))["items"][0]
    await set_warehouse_stock_standalone_enabled(db, enabled=False)
    cp = (await list_compatibility_stock_catalog(db, q="P-SJONLY"))["items"][0]
    assert mb["qty_belum_terkirim"] == 40
    assert cp["qty_belum_terkirim"] == 40
    assert mb["qty_pesanan"] == cp["qty_pesanan"] == 0
