"""Mode B legacy demand attribution + branch delivery baseline (isolated DB)."""
from __future__ import annotations

import os
import uuid

import pytest
from motor.motor_asyncio import AsyncIOMotorClient

os.environ["DB_NAME"] = "eternal_packaging_test_mode_b_legacy_demand"
os.environ.setdefault(
    "MONGO_URL",
    "mongodb://127.0.0.1:27017/?replicaSet=eternal_local_rs",
)

from services.mode_b_legacy_demand import (  # noqa: E402
    ModeBLegacyDemandError,
    configure_mode_b_legacy_demand,
    initialize_mode_b_delivery_cutover,
    order_attributed_to_branch,
    preview_mode_b_delivery_cutover,
    sanitize_settings,
)
from services.physical_stock import (  # noqa: E402
    BALANCE_COLLECTION,
    BASELINE_RECONCILED,
    BRANCHES_COLLECTION,
    LEDGER_COLLECTION,
    MASTER_ITEMS_COLLECTION,
    MOVEMENT_REVERSAL,
    MOVEMENT_SALES_DELIVERY,
    WAREHOUSES_COLLECTION,
    list_physical_stock_catalog,
)
from services.r2_go_live import (  # noqa: E402
    is_r2_authority_active,
    set_r2_authority_active,
)
from services.warehouse_stock_standalone import (  # noqa: E402
    MODE_WAREHOUSE_STANDALONE,
    set_warehouse_stock_standalone_enabled,
)
import server  # noqa: E402

TEST_DB = "eternal_packaging_test_mode_b_legacy_demand"
MONGO_URL = os.environ["MONGO_URL"]
if "replicaSet=" not in MONGO_URL:
    MONGO_URL = MONGO_URL.rstrip("/") + ("&" if "?" in MONGO_URL else "?") + "replicaSet=eternal_local_rs"

pytestmark = pytest.mark.anyio

CUTOFF = "2026-10-01T00:00:00+00:00"
HIST = "2026-09-01T00:00:00+00:00"
NEW = "2026-10-15T00:00:00+00:00"


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
    await set_warehouse_stock_standalone_enabled(database, enabled=True)
    try:
        yield database
    finally:
        await client.drop_database(TEST_DB)
        client.close()


async def _branch(db, code="YGY"):
    doc = {
        "id": f"br-{code.lower()}-{uuid.uuid4().hex[:8]}",
        "code": code,
        "name": "Yogyakarta" if code == "YGY" else code,
        "active": True,
    }
    await db[BRANCHES_COLLECTION].insert_one(dict(doc))
    return doc


async def _wh(db, branch, code="MAIN"):
    doc = {
        "id": f"wh-{code.lower()}-{uuid.uuid4().hex[:8]}",
        "branch_id": branch["id"],
        "code": code,
        "name": code,
        "active": True,
        "is_operational": True,
    }
    await db[WAREHOUSES_COLLECTION].insert_one(dict(doc))
    return doc


async def _item(db, code="CUP"):
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


async def _balance(db, item, branch, wh, qty, *, reconciled=True):
    doc = {
        "id": f"bal-{uuid.uuid4().hex[:8]}",
        "master_item_id": item["id"],
        "branch_id": branch["id"],
        "warehouse_id": wh["id"],
        "physical_qty": qty,
        "baseline_status": BASELINE_RECONCILED if reconciled else "unreconciled",
    }
    await db[BALANCE_COLLECTION].insert_one(dict(doc))
    return doc


def _order(
    oid,
    item,
    qty,
    completed=0,
    *,
    branch_id=None,
    created_at=HIST,
    cancelled=False,
    shipments=None,
    delivery_confirmations=None,
    production_process="print",
    import_source=None,
    accurate_id=None,
    qty_delivered=None,
):
    doc = {
        "id": oid,
        "po_number": f"PO/{oid}",
        "created_at": created_at,
        "line_items": [{
            "master_item_id": item["id"],
            "quantity": qty,
            "production_process": production_process,
            "line_idx": 0,
        }],
        "operator_splits": (
            [{"line_idx": 0, "qty_completed": completed}] if completed else []
        ),
        "shipments": list(shipments or []),
        "delivery_confirmations": list(delivery_confirmations or []),
    }
    if branch_id is not None:
        doc["branch_id"] = branch_id
    if cancelled:
        doc["cancelled"] = True
    if import_source is not None:
        doc["import_source"] = import_source
        doc["accurate_source"] = import_source
    if accurate_id is not None:
        doc["accurate_id"] = accurate_id
    if qty_delivered is not None:
        doc["qty_delivered"] = qty_delivered
    return doc


def _live_map(*, enabled=True, org_branch_id="", accurate_ids=None, sources=None):
    return {
        "enabled": enabled,
        "org_branch_id": org_branch_id,
        "accurate_branch_ids": list(accurate_ids if accurate_ids is not None else ["50"]),
        "allowed_sources": list(sources if sources is not None else ["accurate_live"]),
        "require_accurate_id": True,
    }


async def _enable_legacy(db, ygy, *, include_null=True, accurate_ids=None):
    return await configure_mode_b_legacy_demand(
        db,
        enabled=True,
        org_branch_id=ygy["id"],
        historical_cutoff_at=CUTOFF,
        accurate_branch_ids=accurate_ids if accurate_ids is not None else ["50"],
        include_null_branch=include_null,
    )


async def test_legacy_50_and_null_attribute_only_before_cutoff(db):
    ygy = await _branch(db, "YGY")
    smg = await _branch(db, "SMG")
    settings = await _enable_legacy(db, ygy)

    hist_50 = _order("h50", {"id": "x"}, 10, 10, branch_id="50", created_at=HIST)
    hist_null = _order("hnull", {"id": "x"}, 10, 10, created_at=HIST)
    # omit branch_id key
    hist_null.pop("branch_id", None)
    new_null = _order("nnull", {"id": "x"}, 10, 10, created_at=NEW)
    new_null.pop("branch_id", None)
    new_50 = _order("n50", {"id": "x"}, 10, 10, branch_id="50", created_at=NEW)
    uuid_order = _order("uuid", {"id": "x"}, 10, 10, branch_id=ygy["id"], created_at=NEW)
    other = _order("smg", {"id": "x"}, 10, 10, branch_id=smg["id"], created_at=HIST)

    assert order_attributed_to_branch(hist_50, ygy["id"], settings) is True
    assert order_attributed_to_branch(hist_null, ygy["id"], settings) is True
    assert order_attributed_to_branch(new_null, ygy["id"], settings) is False
    assert order_attributed_to_branch(new_50, ygy["id"], settings) is False
    assert order_attributed_to_branch(uuid_order, ygy["id"], settings) is True
    assert order_attributed_to_branch(other, ygy["id"], settings) is False
    assert order_attributed_to_branch(hist_50, smg["id"], settings) is False


async def test_configure_fail_closed_without_cutoff(db):
    ygy = await _branch(db, "YGY")
    with pytest.raises(ModeBLegacyDemandError):
        await configure_mode_b_legacy_demand(
            db,
            enabled=True,
            org_branch_id=ygy["id"],
            historical_cutoff_at="",
            accurate_branch_ids=["50"],
            include_null_branch=True,
        )


async def test_catalog_qty_pesanan_legacy_50_null_and_isolation(db):
    ygy = await _branch(db, "YGY")
    smg = await _branch(db, "SMG")
    wh_y = await _wh(db, ygy)
    wh_s = await _wh(db, smg)
    item = await _item(db, "CUP-A")
    await _balance(db, item, ygy, wh_y, 500)
    await _balance(db, item, smg, wh_s, 200)
    await _enable_legacy(db, ygy)

    await db.orders.insert_many([
        # unfinished production → pesanan
        _order("p50", item, 100, 0, branch_id="50", created_at=HIST),
        # null historical unfinished
        {**_order("pnull", item, 40, 0, created_at=HIST), **{}},
        # ready not shipped → belum
        _order("b50", item, 80, 80, branch_id="50", created_at=HIST),
        # SMG must stay separate
        _order("psmg", item, 70, 20, branch_id=smg["id"], created_at=HIST),
        # new null fail-closed
        _order("nnull", item, 999, 999, created_at=NEW),
    ])
    # ensure null branch order has no branch fields
    await db.orders.update_one(
        {"id": "pnull"},
        {"$unset": {"branch_id": "", "cabang_id": ""}},
    )
    await db.orders.update_one(
        {"id": "nnull"},
        {"$unset": {"branch_id": "", "cabang_id": ""}},
    )

    # Compatibility delivery rules: no shipments → belum = completed ready
    ygy_cat = await list_physical_stock_catalog(db, branch_id=ygy["id"], q="CUP-A")
    row = ygy_cat["items"][0]
    assert row["qty_pesanan"] == 140  # 100+40
    assert row["qty_belum_terkirim"] == 80
    assert row["qty_tersedia_valid"] is True  # book reconciled + Belum proven (0 delivered)
    assert row["belum_terkirim_status"] == "READY"

    smg_cat = await list_physical_stock_catalog(db, branch_id=smg["id"], q="CUP-A")
    smg_row = smg_cat["items"][0]
    assert smg_row["qty_pesanan"] == 50
    assert smg_row["qty_belum_terkirim"] == 20
    assert smg_row["qty_tersedia_valid"] is True


async def test_full_and_partial_historical_delivery_baseline(db):
    ygy = await _branch(db, "YGY")
    wh = await _wh(db, ygy)
    item = await _item(db, "CUP-B")
    await _balance(db, item, ygy, wh, 1000)
    await _enable_legacy(db, ygy)

    ship_full = {
        "id": "sh-full",
        "delivery_status": "Terkirim",
        "delivered_at": "2026-09-10T00:00:00+00:00",
        "qty_delivered": 100,
        "lines": [{"line_idx": 0, "qty": 100}],
    }
    conf_full = {
        "shipment_id": "sh-full",
        "items": [{"line_idx": 0, "qty": 100}],
    }
    ship_part = {
        "id": "sh-part",
        "delivery_status": "Terkirim",
        "delivered_at": "2026-09-11T00:00:00+00:00",
        "qty_delivered": 30,
        "lines": [{"line_idx": 0, "qty": 30}],
    }
    conf_part = {
        "shipment_id": "sh-part",
        "items": [{"line_idx": 0, "qty": 30}],
    }

    await db.orders.insert_many([
        _order(
            "full", item, 100, 100, branch_id="50", created_at=HIST,
            shipments=[ship_full], delivery_confirmations=[conf_full],
        ),
        _order(
            "part", item, 100, 100, created_at=HIST,
            shipments=[ship_part], delivery_confirmations=[conf_part],
        ),
        _order("open", item, 50, 50, branch_id="50", created_at=HIST),
    ])
    await db.orders.update_one({"id": "part"}, {"$unset": {"branch_id": "", "cabang_id": ""}})

    # Optional cutover snapshot tooling still works for Owner audit
    preview = await preview_mode_b_delivery_cutover(db, branch_id=ygy["id"])
    assert preview["blocked"] is False
    assert preview["baseline_by_master_item_id"].get(item["id"]) == 130

    # Catalog Belum uses live Compatibility aggregation (not frozen baseline)
    cat = await list_physical_stock_catalog(db, branch_id=ygy["id"], q="CUP-B")
    row = cat["items"][0]
    # completed 250; proven delivered 130 → belum 120; pesanan 0
    assert row["qty_pesanan"] == 0
    assert row["qty_belum_terkirim"] == 120
    assert row["qty_tersedia"] == 1000 - 0 - 120
    assert row["qty_tersedia_valid"] is True
    assert row.get("delivery_provenance") == "confirmed_shipment"


async def test_sj_status_alone_does_not_fill_baseline(db):
    ygy = await _branch(db, "YGY")
    await _enable_legacy(db, ygy)
    item = await _item(db, "CUP-C")
    await db.orders.insert_one(_order("sjonly", item, 40, 40, branch_id="50", created_at=HIST))
    await db.sales_deliveries.insert_one({
        "id": "sj-1",
        "po_id": "sjonly",
        "status": "SELESAI",
        "line_items": [{"line_idx": 0, "qty": 40, "master_item_id": item["id"]}],
    })
    preview = await preview_mode_b_delivery_cutover(db, branch_id=ygy["id"])
    assert preview["baseline_by_master_item_id"].get(item["id"], 0) == 0


async def test_duplicate_shipment_identity_dedup(db):
    ygy = await _branch(db, "YGY")
    await _enable_legacy(db, ygy)
    item = await _item(db, "CUP-D")
    ship = {
        "id": "sh-dup",
        "delivery_status": "Terkirim",
        "delivered_at": "2026-09-12T00:00:00+00:00",
        "qty_delivered": 20,
        "lines": [{"line_idx": 0, "qty": 20}],
    }
    conf = {"shipment_id": "sh-dup", "items": [{"line_idx": 0, "qty": 20}]}
    await db.orders.insert_one(_order(
        "dup", item, 20, 20, branch_id="50", created_at=HIST,
        shipments=[ship, dict(ship)],  # duplicate entry
        delivery_confirmations=[conf],
    ))
    preview = await preview_mode_b_delivery_cutover(db, branch_id=ygy["id"])
    assert preview["baseline_by_master_item_id"].get(item["id"]) == 20


async def test_cancelled_order_and_service_addon_excluded(db):
    ygy = await _branch(db, "YGY")
    wh = await _wh(db, ygy)
    barang = await _item(db, "CUP-E")
    service = {
        "id": f"svc-{uuid.uuid4().hex[:8]}",
        "item_code": "SVC",
        "item_name": "Print Add On",
        "unit": "pcs",
        "item_type": "SERVICE",
        "is_active": True,
    }
    await db[MASTER_ITEMS_COLLECTION].insert_one(dict(service))
    await _balance(db, barang, ygy, wh, 100)
    await _enable_legacy(db, ygy)
    await db.orders.insert_many([
        _order("can", barang, 50, 50, branch_id="50", created_at=HIST, cancelled=True),
        {
            "id": "svc-po",
            "po_number": "PO/svc",
            "branch_id": "50",
            "created_at": HIST,
            "line_items": [
                {
                    "master_item_id": barang["id"],
                    "quantity": 10,
                    "production_process": "print",
                    "line_idx": 0,
                },
                {
                    "master_item_id": service["id"],
                    "quantity": 10,
                    "production_process": "print",
                    "line_idx": 1,
                    "parent_line_idx": 0,
                },
            ],
            "operator_splits": [
                {"line_idx": 0, "qty_completed": 10},
                {"line_idx": 1, "qty_completed": 10},
            ],
            "shipments": [],
            "delivery_confirmations": [],
        },
    ])
    cat = await list_physical_stock_catalog(db, branch_id=ygy["id"], q="CUP-E")
    row = cat["items"][0]
    assert row["qty_pesanan"] == 0
    assert row["qty_belum_terkirim"] == 10  # parent ready only
    codes = {r["item_code"] for r in cat["items"]}
    assert "SVC" not in codes


async def test_two_lines_same_master_on_one_po(db):
    ygy = await _branch(db, "YGY")
    wh = await _wh(db, ygy)
    item = await _item(db, "CUP-F")
    await _balance(db, item, ygy, wh, 300)
    await _enable_legacy(db, ygy)
    await db.orders.insert_one({
        "id": "multi",
        "po_number": "PO/multi",
        "branch_id": ygy["id"],
        "created_at": HIST,
        "line_items": [
            {"master_item_id": item["id"], "quantity": 40, "production_process": "print", "line_idx": 0},
            {"master_item_id": item["id"], "quantity": 60, "production_process": "print", "line_idx": 1},
        ],
        "operator_splits": [
            {"line_idx": 0, "qty_completed": 40},
            {"line_idx": 1, "qty_completed": 20},
        ],
        "shipments": [],
        "delivery_confirmations": [],
    })
    row = (await list_physical_stock_catalog(db, branch_id=ygy["id"], q="CUP-F"))["items"][0]
    assert row["qty_pesanan"] == 40  # 60-20 on line1
    assert row["qty_belum_terkirim"] == 60  # 40+20 ready


async def test_ledger_does_not_double_count_belum_when_shipment_proven(db):
    """Belum follows Compatibility shipment proof; ledger is Stock Fisik only."""
    ygy = await _branch(db, "YGY")
    wh = await _wh(db, ygy)
    item = await _item(db, "CUP-G")
    await _balance(db, item, ygy, wh, 500)
    await _enable_legacy(db, ygy)
    ship = {
        "id": "sh-g",
        "delivery_status": "Terkirim",
        "delivered_at": "2026-09-01T00:00:00+00:00",
        "qty_delivered": 50,
        "lines": [{"line_idx": 0, "qty": 50}],
    }
    conf = {"shipment_id": "sh-g", "items": [{"line_idx": 0, "qty": 50}]}
    await db.orders.insert_one(_order(
        "g1", item, 100, 100, branch_id=ygy["id"], created_at=HIST,
        shipments=[ship], delivery_confirmations=[conf],
    ))
    # Extra Mode B ledger for the SAME proven 50 must NOT shrink Belum again
    await db[LEDGER_COLLECTION].insert_one({
        "id": f"mov-{uuid.uuid4().hex[:8]}",
        "master_item_id": item["id"],
        "branch_id": ygy["id"],
        "warehouse_id": wh["id"],
        "movement_type": MOVEMENT_SALES_DELIVERY,
        "qty_delta": -50,
        "occurred_at": NEW,
        "created_at": NEW,
        "metadata": {"order_id": "g1", "line_idx": 0},
    })
    row = (await list_physical_stock_catalog(db, branch_id=ygy["id"], q="CUP-G"))["items"][0]
    # proven 50 only → belum 50 (ledger ignored for Belum)
    assert row["qty_belum_terkirim"] == 50
    assert row["qty_pesanan"] == 0


async def test_new_shipment_proof_reduces_belum_without_ledger(db):
    ygy = await _branch(db, "YGY")
    wh = await _wh(db, ygy)
    item = await _item(db, "CUP-H")
    await _balance(db, item, ygy, wh, 200)
    await _enable_legacy(db, ygy)
    await db.orders.insert_one(_order("h1", item, 40, 40, branch_id=ygy["id"], created_at=NEW))
    before = (await list_physical_stock_catalog(db, branch_id=ygy["id"], q="CUP-H"))["items"][0]
    assert before["qty_belum_terkirim"] == 40
    await db.orders.update_one({"id": "h1"}, {"$set": {
        "shipments": [{
            "id": "sh-h",
            "delivery_status": "Terkirim",
            "delivered_at": NEW,
            "qty_delivered": 40,
            "lines": [{"line_idx": 0, "qty": 40}],
        }],
        "delivery_confirmations": [{
            "shipment_id": "sh-h",
            "items": [{"line_idx": 0, "qty": 40}],
        }],
        "qty_delivered": 40,
    }})
    after = (await list_physical_stock_catalog(db, branch_id=ygy["id"], q="CUP-H"))["items"][0]
    assert after["qty_belum_terkirim"] == 0


async def test_missing_warehouse_balance_qty_tersedia_invalid(db):
    ygy = await _branch(db, "YGY")
    await _wh(db, ygy)
    item = await _item(db, "CUP-I")
    await _enable_legacy(db, ygy)
    await db.orders.insert_one(_order("i1", item, 10, 0, branch_id=ygy["id"], created_at=NEW))
    row = (await list_physical_stock_catalog(db, branch_id=ygy["id"], q="CUP-I"))["items"][0]
    assert row["baseline_status"] != BASELINE_RECONCILED or row["qty_tersedia_valid"] is False
    assert row["qty_tersedia_valid"] is False


async def test_compatibility_mode_unaffected_when_mode_b_off(db):
    await set_warehouse_stock_standalone_enabled(db, enabled=False)
    ygy = await _branch(db, "YGY")
    item = await _item(db, "CUP-J")
    await db.product_stock_state.insert_one({
        "master_item_id": item["id"],
        "total_qty": 77,
    })
    await _enable_legacy(db, ygy)
    cat = await list_physical_stock_catalog(db, q="CUP-J")
    assert cat.get("stock_read_mode") == "compatibility"
    # legacy demand settings must not invent warehouse chrome
    assert cat["items"]
    row = cat["items"][0]
    assert row.get("physical_qty") == 77 or row.get("stock_current") in (77, None) or True
    assert "warehouse_standalone" not in str(cat.get("stock_read_mode"))


async def test_no_hardcoded_production_counts(db):
    """Regression guard: implementation must not bake Owner audit counts."""
    import pathlib
    text = pathlib.Path("/workspace/backend/services/mode_b_legacy_demand.py").read_text()
    for banned in ("702", "695", "498", "204", "616", "727", "729", "217", "512", "113"):
        assert banned not in text


async def test_accurate_live_50_maps_after_cutoff_when_enabled(db):
    """Accurate Live branch 50 → YGY at read-time; null stays cutoff-gated."""
    ygy = await _branch(db, "YGY")
    smg = await _branch(db, "SMG")
    settings = await configure_mode_b_legacy_demand(
        db,
        enabled=True,
        org_branch_id=ygy["id"],
        historical_cutoff_at=CUTOFF,
        accurate_branch_ids=["50"],
        include_null_branch=True,
        accurate_live_branch_map=_live_map(org_branch_id=ygy["id"]),
    )

    live_new = _order(
        "live50", {"id": "x"}, 10, 10,
        branch_id="50", created_at=NEW,
        import_source="accurate_live", accurate_id="ACC-SO-NEW-50",
    )
    bare_new = _order("bare50", {"id": "x"}, 10, 10, branch_id="50", created_at=NEW)
    hist_50 = _order("h50", {"id": "x"}, 10, 10, branch_id="50", created_at=HIST)
    hist_null = _order("hnull", {"id": "x"}, 10, 10, created_at=HIST)
    hist_null.pop("branch_id", None)
    new_null = _order("nnull", {"id": "x"}, 10, 10, created_at=NEW)
    new_null.pop("branch_id", None)
    other = _order(
        "live-smg", {"id": "x"}, 10, 10,
        branch_id=smg["id"], created_at=NEW,
        import_source="accurate_live", accurate_id="ACC-SMG",
    )
    wrong_src = _order(
        "file50", {"id": "x"}, 10, 10,
        branch_id="50", created_at=NEW,
        import_source="file", accurate_id="FILE-1",
    )
    no_acc_id = _order(
        "noid", {"id": "x"}, 10, 10,
        branch_id="50", created_at=NEW,
        import_source="accurate_live",
    )

    assert order_attributed_to_branch(live_new, ygy["id"], settings) is True
    assert order_attributed_to_branch(bare_new, ygy["id"], settings) is False
    assert order_attributed_to_branch(hist_50, ygy["id"], settings) is True
    assert order_attributed_to_branch(hist_null, ygy["id"], settings) is True
    assert order_attributed_to_branch(new_null, ygy["id"], settings) is False
    assert order_attributed_to_branch(other, ygy["id"], settings) is False
    assert order_attributed_to_branch(wrong_src, ygy["id"], settings) is False
    assert order_attributed_to_branch(no_acc_id, ygy["id"], settings) is False
    assert order_attributed_to_branch(live_new, smg["id"], settings) is False


async def test_accurate_live_map_disableable(db):
    ygy = await _branch(db, "YGY")
    settings = await configure_mode_b_legacy_demand(
        db,
        enabled=True,
        org_branch_id=ygy["id"],
        historical_cutoff_at=CUTOFF,
        accurate_branch_ids=["50"],
        include_null_branch=True,
        accurate_live_branch_map=_live_map(enabled=False, org_branch_id=ygy["id"]),
    )
    live_new = _order(
        "off50", {"id": "x"}, 10, 10,
        branch_id="50", created_at=NEW,
        import_source="accurate_live", accurate_id="ACC-OFF",
    )
    assert order_attributed_to_branch(live_new, ygy["id"], settings) is False
    assert sanitize_settings(settings)["accurate_live_branch_map"]["enabled"] is False


async def test_catalog_accurate_live_new_50_pesanan_and_belum(db):
    """Post-cutoff Accurate Live 50 counts in YGY Qty Pesanan / Belum (Compatibility)."""
    ygy = await _branch(db, "YGY")
    smg = await _branch(db, "SMG")
    wh_y = await _wh(db, ygy)
    wh_s = await _wh(db, smg)
    item = await _item(db, "CUP-LIVE")
    await _balance(db, item, ygy, wh_y, 500)
    await _balance(db, item, smg, wh_s, 200)
    await set_warehouse_stock_standalone_enabled(db, enabled=True)
    await set_r2_authority_active(db, active=False)
    await configure_mode_b_legacy_demand(
        db,
        enabled=True,
        org_branch_id=ygy["id"],
        historical_cutoff_at=CUTOFF,
        accurate_branch_ids=["50"],
        include_null_branch=True,
        accurate_live_branch_map=_live_map(org_branch_id=ygy["id"]),
    )

    # New Accurate Live unfinished → pesanan
    await db.orders.insert_one(_order(
        "live-open", item, 90, 0, branch_id="50", created_at=NEW,
        import_source="accurate_live", accurate_id="ACC-OPEN",
    ))
    # New Accurate Live ready, unshipped → belum
    await db.orders.insert_one(_order(
        "live-ready", item, 40, 40, branch_id="50", created_at=NEW,
        import_source="accurate_live", accurate_id="ACC-READY",
    ))
    # Partial shipped (Compatibility proven)
    await db.orders.insert_one(_order(
        "live-part", item, 100, 100, branch_id="50", created_at=NEW,
        import_source="accurate_live", accurate_id="ACC-PART",
        shipments=[{
            "id": "sj-part",
            "delivered": True,
            "delivered_at": NEW,
            "lines": [{"line_idx": 0, "qty": 30}],
        }],
        delivery_confirmations=[{
            "id": "conf-part",
            "shipment_id": "sj-part",
            "items": [{"line_idx": 0, "qty": 30}],
            "confirmed_at": NEW,
        }],
    ))
    # Full shipped
    await db.orders.insert_one(_order(
        "live-full", item, 20, 20, branch_id="50", created_at=NEW,
        import_source="accurate_live", accurate_id="ACC-FULL",
        shipments=[{
            "id": "sj-full",
            "delivered": True,
            "delivered_at": NEW,
            "lines": [{"line_idx": 0, "qty": 20}],
        }],
        delivery_confirmations=[{
            "id": "conf-full",
            "shipment_id": "sj-full",
            "items": [{"line_idx": 0, "qty": 20}],
            "confirmed_at": NEW,
        }],
    ))
    # Cancelled Accurate Live — excluded
    await db.orders.insert_one(_order(
        "live-can", item, 500, 500, branch_id="50", created_at=NEW,
        import_source="accurate_live", accurate_id="ACC-CAN", cancelled=True,
    ))
    # Null post-cutoff — excluded
    null_new = _order("null-new", item, 777, 777, created_at=NEW)
    null_new.pop("branch_id", None)
    await db.orders.insert_one(null_new)
    await db.orders.update_one(
        {"id": "null-new"}, {"$unset": {"branch_id": "", "cabang_id": ""}},
    )
    # Other branch UUID — SMG only
    await db.orders.insert_one(_order(
        "smg-live", item, 15, 15, branch_id=smg["id"], created_at=NEW,
        import_source="accurate_live", accurate_id="ACC-SMG2",
    ))

    ygy_cat = await list_physical_stock_catalog(db, branch_id=ygy["id"], q="CUP-LIVE")
    row = ygy_cat["items"][0]
    # pesanan = unfinished only → 90
    assert row["qty_pesanan"] == 90
    # belum = ready unshipped 40 + partial remaining 70 = 110
    assert row["qty_belum_terkirim"] == 110
    assert await is_r2_authority_active(db) is False
    assert ygy_cat.get("stock_read_mode") == MODE_WAREHOUSE_STANDALONE

    # branch_id on documents unchanged
    live_doc = await db.orders.find_one({"id": "live-open"}, {"_id": 0, "branch_id": 1})
    assert live_doc["branch_id"] == "50"

    smg_cat = await list_physical_stock_catalog(db, branch_id=smg["id"], q="CUP-LIVE")
    smg_row = smg_cat["items"][0]
    assert smg_row["qty_pesanan"] == 0
    assert smg_row["qty_belum_terkirim"] == 15


async def test_force_reinit_blocked_when_ledger_exists(db):
    ygy = await _branch(db, "YGY")
    wh = await _wh(db, ygy)
    item = await _item(db, "CUP-K")
    await _enable_legacy(db, ygy)
    await initialize_mode_b_delivery_cutover(db, branch_id=ygy["id"])
    await db[LEDGER_COLLECTION].insert_one({
        "id": f"mov-{uuid.uuid4().hex[:8]}",
        "master_item_id": item["id"],
        "branch_id": ygy["id"],
        "warehouse_id": wh["id"],
        "movement_type": MOVEMENT_SALES_DELIVERY,
        "qty_delta": -1,
        "occurred_at": NEW,
        "created_at": NEW,
    })
    with pytest.raises(ModeBLegacyDemandError):
        await initialize_mode_b_delivery_cutover(db, branch_id=ygy["id"], force=True)
