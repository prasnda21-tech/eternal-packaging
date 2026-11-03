"""Dummy-DB entrypoint proof for YGY ATS cutover via list_physical_stock_catalog.

Not helper-only: seeds orders + ledger, enables Mode B Accurate Live map,
calls the real SSI catalog path (list ≡ detail ≡ export source).
Does not mutate production and does not activate ETERNAL_ATS_YGY_MODE beyond
draft_read for this process.
"""
from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta
from decimal import Decimal

import pytest
from motor.motor_asyncio import AsyncIOMotorClient

os.environ["DB_NAME"] = "eternal_packaging_test_ats_ygy_entrypoint"
os.environ.setdefault(
    "MONGO_URL",
    "mongodb://127.0.0.1:27017/?replicaSet=eternal_local_rs",
)

from services.mode_b_legacy_demand import configure_mode_b_legacy_demand  # noqa: E402
from services.physical_stock import (  # noqa: E402
    BALANCE_COLLECTION,
    BRANCHES_COLLECTION,
    LEDGER_COLLECTION,
    MASTER_ITEMS_COLLECTION,
    MOVEMENT_PURCHASE_RECEIPT,
    MOVEMENT_REVERSAL,
    MOVEMENT_SALES_DELIVERY,
    WAREHOUSES_COLLECTION,
    ensure_indexes,
    list_physical_stock_catalog,
)
from services.r2_go_live import set_r2_authority_active  # noqa: E402
from services.stock_ats_ygy_runtime import (  # noqa: E402
    CUTOFF_AT,
    YGY_BRANCH_ID,
    YGY_WAREHOUSE_ID,
    collect_ats_stream_for_ygy,
    order_matches_ygy_mode_b,
    project_master_ats,
)
from services.warehouse_stock_standalone import (  # noqa: E402
    set_warehouse_stock_standalone_enabled,
)

TEST_DB = "eternal_packaging_test_ats_ygy_entrypoint"
MONGO_URL = os.environ["MONGO_URL"]
if "replicaSet=" not in MONGO_URL:
    MONGO_URL = MONGO_URL.rstrip("/") + ("&" if "?" in MONGO_URL else "?") + "replicaSet=eternal_local_rs"

MID_100032 = "a88fb167-cf11-4d1f-8862-25dae1723053"
CUTOFF = datetime.fromisoformat(CUTOFF_AT)
AFTER = (CUTOFF + timedelta(hours=1)).isoformat()
BEFORE = (CUTOFF - timedelta(days=10)).isoformat()
AS_OF_LATER = (CUTOFF + timedelta(hours=5)).isoformat()

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
async def db(monkeypatch):
    assert "preview" not in TEST_DB
    prev_mode = os.environ.get("ETERNAL_ATS_YGY_MODE")
    os.environ["ETERNAL_ATS_YGY_MODE"] = "draft_read"
    # Catalog ATS as_of uses physical_stock.now_iso — freeze after all fixture events.
    import services.physical_stock as physical_stock_mod

    monkeypatch.setattr(physical_stock_mod, "now_iso", lambda: AS_OF_LATER)
    client = AsyncIOMotorClient(MONGO_URL)
    database = client[TEST_DB]
    await client.drop_database(TEST_DB)
    await ensure_indexes(database)
    await set_r2_authority_active(database, active=False)
    await set_warehouse_stock_standalone_enabled(database, enabled=True)
    await configure_mode_b_legacy_demand(
        database,
        enabled=False,
        org_branch_id=YGY_BRANCH_ID,
        historical_cutoff_at=CUTOFF_AT,
        accurate_live_branch_map={
            "enabled": True,
            "org_branch_id": YGY_BRANCH_ID,
            "accurate_branch_ids": ["50"],
            "allowed_sources": ["accurate_live"],
            "require_accurate_id": True,
        },
    )
    try:
        yield database
    finally:
        await client.drop_database(TEST_DB)
        client.close()
        if prev_mode is None:
            os.environ.pop("ETERNAL_ATS_YGY_MODE", None)
        else:
            os.environ["ETERNAL_ATS_YGY_MODE"] = prev_mode


async def _seed_master_wh(db, *, physical_qty: float = -19300):
    item = {
        "id": MID_100032,
        "item_code": "100032",
        "item_name": "12 oz Datar 7 gr Starindo",
        "unit": "PCS",
        "item_type": "INVENTORY",
        "is_active": True,
    }
    branch = {
        "id": YGY_BRANCH_ID,
        "code": "YGY",
        "name": "Yogyakarta",
        "active": True,
    }
    wh = {
        "id": YGY_WAREHOUSE_ID,
        "branch_id": YGY_BRANCH_ID,
        "code": "YGY-MAIN",
        "name": "YGY Main",
        "active": True,
        "is_operational": True,
        "is_default": True,
    }
    await db[MASTER_ITEMS_COLLECTION].insert_one(dict(item))
    await db[BRANCHES_COLLECTION].insert_one(dict(branch))
    await db[WAREHOUSES_COLLECTION].insert_one(dict(wh))
    # Physical balance may be negative; ATS overlay must not rewrite it.
    await db[BALANCE_COLLECTION].insert_one({
        "id": f"bal-{uuid.uuid4().hex[:8]}",
        "master_item_id": MID_100032,
        "branch_id": YGY_BRANCH_ID,
        "warehouse_id": YGY_WAREHOUSE_ID,
        "physical_qty": physical_qty,
        "version": 1,
        "updated_at": AFTER,
    })
    return item, branch, wh


OTHER_WH_ID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"


def _ygy_alloc(qty: int, *, unit: str = "PCS", warehouse_id: str = YGY_WAREHOUSE_ID):
    return [{
        "allocation_id": f"sa-{uuid.uuid4().hex[:8]}",
        "warehouse_id": warehouse_id,
        "qty": qty,
        "unit": unit,
    }]


async def _insert_delivery(
    db,
    *,
    order_id: str,
    line_id: str,
    line_idx: int,
    qty: int,
    occurred_at: str,
    movement_id: str | None = None,
    unit: str = "PCS",
):
    mid = movement_id or f"sd-{uuid.uuid4().hex[:10]}"
    await db[LEDGER_COLLECTION].insert_one({
        "id": mid,
        "master_item_id": MID_100032,
        "branch_id": YGY_BRANCH_ID,
        "warehouse_id": YGY_WAREHOUSE_ID,
        "movement_type": MOVEMENT_SALES_DELIVERY,
        "qty_delta": -int(qty),
        "reference_type": "sales_delivery_confirmation",
        "reference_id": f"conf-{mid}",
        "reason": "test ship",
        "actor_id": "t",
        "occurred_at": occurred_at,
        "created_at": occurred_at,
        "idempotency_key": f"sales-delivery:conf-{mid}:0:{MID_100032}",
        "metadata": {
            "order_id": order_id,
            "line_id": line_id,
            "line_idx": line_idx,
            "confirmation_id": f"conf-{mid}",
            "unit": unit,
        },
    })
    return mid


async def _insert_reversal(db, *, original_id: str, occurred_at: str, qty_delta: int = 0):
    rid = f"rev-{uuid.uuid4().hex[:10]}"
    await db[LEDGER_COLLECTION].insert_one({
        "id": rid,
        "master_item_id": MID_100032,
        "branch_id": YGY_BRANCH_ID,
        "warehouse_id": YGY_WAREHOUSE_ID,
        "movement_type": MOVEMENT_REVERSAL,
        "qty_delta": int(qty_delta) if qty_delta else 1,  # physical sign unused by ATS REVERSE qty=0
        "reference_type": "physical_stock_ledger",
        "reference_id": original_id,
        "reason": "reversal",
        "actor_id": "t",
        "occurred_at": occurred_at,
        "created_at": occurred_at,
        "idempotency_key": f"reversal:{original_id}",
        "metadata": {},
    })
    return rid


async def _catalog_row(db, *, q: str = "100032"):
    catalog = await list_physical_stock_catalog(
        db,
        branch_id=YGY_BRANCH_ID,
        warehouse_id=YGY_WAREHOUSE_ID,
        q=q,
        page=1,
        limit=50,
    )
    items = catalog.get("items") or []
    row = next(r for r in items if r.get("master_item_id") == MID_100032)
    return catalog, row


async def test_mode_b_official_attribution_not_hardcoded_tokens(db):
    settings = await db.app_settings.find_one({"key": "mode_b_legacy_demand"}, {"_id": 0})
    live = {
        "branch_id": "50",
        "import_source": "accurate_live",
        "accurate_id": "acc-live-1",
    }
    assert order_matches_ygy_mode_b(live, settings) is True
    assert order_matches_ygy_mode_b({"branch_id": YGY_BRANCH_ID}, settings) is True
    # Digit alone without Accurate Live identity must not match when legacy off.
    assert order_matches_ygy_mode_b({"branch_id": "50"}, settings) is False
    assert order_matches_ygy_mode_b(
        {"accurate_branch_id": "50", "raw_branch_token": "50"},
        settings,
    ) is False


async def test_entrypoint_baseline_only_ready_20000(db):
    await _seed_master_wh(db)
    catalog, row = await _catalog_row(db)
    assert row["ats_cutover_status"] == "READY"
    assert row["stock_dapat_dijual"] == 20000.0
    assert row["qty_siap_jual"] == 20000.0
    assert row["physical_qty"] == -19300
    assert row["ats_baseline"] == 20000.0
    assert row["ats_penambahan"] == 0.0
    assert row["ats_pengurangan"] == 0.0
    assert row["ats_sekarang"] == 20000.0
    assert row.get("ats_coverage_gaps") == []
    # list page fields agree with detail-style single-row fetch
    _, detail = await _catalog_row(db, q="100032")
    assert detail["stock_dapat_dijual"] == row["stock_dapat_dijual"]
    assert detail["ats_cutover_status"] == row["ats_cutover_status"]
    assert detail["ats_baseline"] == row["ats_baseline"]
    assert detail["ats_sekarang"] == row["ats_sekarang"]
    assert catalog.get("warehouse_stock_standalone") is True


async def test_entrypoint_old_po_ship_does_not_recut_baseline(db):
    await _seed_master_wh(db)
    oid = f"so-old-{uuid.uuid4().hex[:6]}"
    await db.orders.insert_one({
        "id": oid,
        "branch_id": "50",
        "import_source": "accurate_live",
        "accurate_id": "acc-old-1",
        "created_at": BEFORE,
        "cancelled": False,
        "line_items": [{
            "line_id": "ln-old",
            "master_item_id": MID_100032,
            "quantity": 5000,
            "production_process": "non_print",
        }],
    })
    await _insert_delivery(
        db, order_id=oid, line_id="ln-old", line_idx=0, qty=2000, occurred_at=AFTER,
    )
    stream = await collect_ats_stream_for_ygy(
        db,
        master_item_ids=[MID_100032],
        as_of=AS_OF_LATER,
    )
    pack = stream["by_master"][MID_100032]
    assert pack["evidence_complete"] is True
    assert pack["reservations_at_cutoff"] == []
    assert any("SKIP_OLD_PO_SHIP" in s for s in pack["skipped"])
    assert not any(e.kind == "ISSUE_FREE" for e in pack["events"])
    assert not any(e.kind == "SHIP_RESERVED" for e in pack["events"])

    _, row = await _catalog_row(db)
    assert row["ats_cutover_status"] == "READY"
    assert row["stock_dapat_dijual"] == 20000.0
    assert row["physical_qty"] == -19300
    assert any("SKIP_OLD_PO_SHIP" in s for s in (row.get("ats_cutover_skipped") or []))


async def test_entrypoint_old_po_cancel_no_release_and_unavailable(db):
    """Pre-cutoff PO cancelled after cutoff without opening reservation.

    Saldo must not inflate (no RELEASE), but status must be UNAVAILABLE /
    UNVERIFIED because free stock may be understated vs true free.
    """
    await _seed_master_wh(db)
    oid = f"so-oldc-{uuid.uuid4().hex[:6]}"
    await db.orders.insert_one({
        "id": oid,
        "branch_id": "50",
        "import_source": "accurate_live",
        "accurate_id": "acc-old-c",
        "created_at": BEFORE,
        "cancelled": True,
        "cancelled_at": AFTER,
        "line_items": [{
            "line_id": "ln-oldc",
            "master_item_id": MID_100032,
            "quantity": 3000,
        }],
    })
    stream = await collect_ats_stream_for_ygy(
        db, master_item_ids=[MID_100032], as_of=AS_OF_LATER,
    )
    pack = stream["by_master"][MID_100032]
    assert pack["evidence_complete"] is False
    assert not any(e.kind == "RELEASE" for e in pack["events"])
    assert any("SKIP_OLD_PO_CANCEL" in s for s in pack["skipped"])
    assert any("OLD_PO_CANCEL_UNDERSTATED_FREE_STOCK" in g for g in pack["gaps"])
    _, row = await _catalog_row(db)
    assert row["ats_cutover_status"] == "UNAVAILABLE"
    assert row["stock_dapat_dijual"] is None
    assert row.get("stock_dapat_dijual_status") == "UNVERIFIED"
    assert row.get("qty_siap_jual_status") == "UNVERIFIED"
    # Physical / baseline figure not inflated by unproven RELEASE
    assert row["physical_qty"] == -19300
    assert any("UNDERSTATED" in g for g in (row.get("ats_coverage_gaps") or []))


async def test_entrypoint_new_po_reserve_and_ship_reserved(db):
    await _seed_master_wh(db)
    oid = f"so-new-{uuid.uuid4().hex[:6]}"
    await db.orders.insert_one({
        "id": oid,
        "branch_id": "50",
        "import_source": "accurate_live",
        "accurate_id": "acc-new-1",
        "created_at": AFTER,
        "cancelled": False,
        "line_items": [{
            "line_id": "ln-new",
            "master_item_id": MID_100032,
            "quantity": 2000,
            "unit": "PCS",
            "item_type": "INVENTORY",
            "production_process": "non_print",
            "stock_allocations": _ygy_alloc(2000),
        }],
    })
    _, row1 = await _catalog_row(db)
    assert row1["ats_cutover_status"] == "READY"
    assert row1["stock_dapat_dijual"] == 18000.0  # 20000 - 2000 RESERVE
    assert row1["ats_baseline"] == 20000.0
    assert row1["ats_pengurangan"] == 2000.0
    assert row1["ats_penambahan"] == 0.0
    assert row1["ats_sekarang"] == 18000.0

    await _insert_delivery(
        db, order_id=oid, line_id="ln-new", line_idx=0, qty=1000, occurred_at=AFTER,
    )
    _, row2 = await _catalog_row(db)
    assert row2["ats_cutover_status"] == "READY"
    # SHIP_RESERVED does not re-cut free stock
    assert row2["stock_dapat_dijual"] == 18000.0
    assert row2["qty_siap_jual"] == row2["stock_dapat_dijual"]
    assert row2["ats_pengurangan"] == 2000.0  # ship effect 0 — no double cut
    assert row2["ats_sekarang"] == 18000.0
    assert row2["physical_qty"] == -19300


async def test_entrypoint_new_po_cancel_releases_remaining(db):
    await _seed_master_wh(db)
    oid = f"so-newc-{uuid.uuid4().hex[:6]}"
    await db.orders.insert_one({
        "id": oid,
        "branch_id": YGY_BRANCH_ID,
        "created_at": AFTER,
        "cancelled": True,
        "cancelled_at": (CUTOFF + timedelta(hours=2)).isoformat(),
        "line_items": [{
            "line_id": "ln-newc",
            "master_item_id": MID_100032,
            "quantity": 2000,
            "unit": "PCS",
            "item_type": "INVENTORY",
            "stock_allocations": _ygy_alloc(2000),
        }],
    })
    await _insert_delivery(
        db,
        order_id=oid,
        line_id="ln-newc",
        line_idx=0,
        qty=500,
        occurred_at=(CUTOFF + timedelta(hours=1, minutes=30)).isoformat(),
    )
    _, row = await _catalog_row(db)
    assert row["ats_cutover_status"] == "READY"
    # RESERVE 2000 → 18000; SHIP 500 no free effect; RELEASE 1500 → 19500
    assert row["stock_dapat_dijual"] == 19500.0


async def test_entrypoint_purchase_receipt_receive_free(db):
    await _seed_master_wh(db)
    await db[LEDGER_COLLECTION].insert_one({
        "id": f"pr-{uuid.uuid4().hex[:8]}",
        "master_item_id": MID_100032,
        "branch_id": YGY_BRANCH_ID,
        "warehouse_id": YGY_WAREHOUSE_ID,
        "movement_type": MOVEMENT_PURCHASE_RECEIPT,
        "qty_delta": 500,
        "reference_type": "purchase_receipt",
        "reference_id": "pr-1",
        "reason": "receipt",
        "actor_id": "t",
        "occurred_at": AFTER,
        "created_at": AFTER,
        "idempotency_key": f"purchase-receipt:pr-1:{uuid.uuid4().hex[:6]}",
        "metadata": {"unit": "PCS"},
    })
    _, row = await _catalog_row(db)
    assert row["ats_cutover_status"] == "READY"
    assert row["stock_dapat_dijual"] == 20500.0
    assert row["ats_baseline"] == 20000.0
    assert row["ats_penambahan"] == 500.0
    assert row["ats_pengurangan"] == 0.0
    assert row["ats_sekarang"] == 20500.0


async def test_entrypoint_unmapped_ledger_keeps_evidence_incomplete(db):
    await _seed_master_wh(db)
    await db[LEDGER_COLLECTION].insert_one({
        "id": f"adj-{uuid.uuid4().hex[:8]}",
        "master_item_id": MID_100032,
        "branch_id": YGY_BRANCH_ID,
        "warehouse_id": YGY_WAREHOUSE_ID,
        "movement_type": "MANUAL_ADJUSTMENT",
        "qty_delta": -10,
        "reference_type": "manual",
        "reference_id": "adj-1",
        "reason": "manual",
        "actor_id": "t",
        "occurred_at": AFTER,
        "created_at": AFTER,
        "idempotency_key": f"manual-{uuid.uuid4().hex[:8]}",
        "metadata": {},
    })
    stream = await collect_ats_stream_for_ygy(
        db, master_item_ids=[MID_100032], as_of=AS_OF_LATER,
    )
    pack = stream["by_master"][MID_100032]
    assert pack["evidence_complete"] is False
    assert any("UNMAPPED_LEDGER:MANUAL_ADJUSTMENT" in g for g in pack["gaps"])
    _, row = await _catalog_row(db)
    assert row["ats_cutover_status"] == "UNAVAILABLE"
    assert row["stock_dapat_dijual"] is None
    assert row["physical_qty"] == -19300  # physical not overwritten to invent ATS


async def test_list_detail_export_same_ats_projection(db):
    """Same catalog entrypoint drives list/detail/export — agree on ATS fields."""
    await _seed_master_wh(db)
    oid = f"so-parity-{uuid.uuid4().hex[:6]}"
    await db.orders.insert_one({
        "id": oid,
        "branch_id": "50",
        "import_source": "accurate_live",
        "accurate_id": "acc-parity",
        "created_at": AFTER,
        "cancelled": False,
        "line_items": [{
            "line_id": "ln-p",
            "master_item_id": MID_100032,
            "quantity": 2000,
            "unit": "PCS",
            "item_type": "INVENTORY",
            "stock_allocations": _ygy_alloc(2000),
        }],
    })
    list_cat = await list_physical_stock_catalog(
        db, branch_id=YGY_BRANCH_ID, warehouse_id=YGY_WAREHOUSE_ID, page=1, limit=50,
    )
    detail_cat = await list_physical_stock_catalog(
        db, branch_id=YGY_BRANCH_ID, warehouse_id=YGY_WAREHOUSE_ID, q="100032", page=1, limit=1,
    )
    export_cat = await list_physical_stock_catalog(
        db, branch_id=YGY_BRANCH_ID, warehouse_id=YGY_WAREHOUSE_ID, page=1, limit=1000,
    )
    def _row(cat):
        return next(r for r in cat["items"] if r["master_item_id"] == MID_100032)

    a, b, c = _row(list_cat), _row(detail_cat), _row(export_cat)
    for key in (
        "stock_dapat_dijual",
        "qty_siap_jual",
        "ats_cutover_status",
        "ats_cutover_available_qty",
        "physical_qty",
    ):
        assert a[key] == b[key] == c[key]
    assert a["stock_dapat_dijual"] == 18000.0


async def test_entrypoint_other_warehouse_po_does_not_cut_ygy_ats(db):
    await _seed_master_wh(db)
    await db[WAREHOUSES_COLLECTION].insert_one({
        "id": OTHER_WH_ID,
        "branch_id": YGY_BRANCH_ID,
        "code": "YGY-OTHER",
        "name": "Other WH",
        "active": True,
        "is_operational": True,
    })
    oid = f"so-oth-{uuid.uuid4().hex[:6]}"
    await db.orders.insert_one({
        "id": oid,
        "branch_id": YGY_BRANCH_ID,
        "created_at": AFTER,
        "cancelled": False,
        "line_items": [{
            "line_id": "ln-oth",
            "master_item_id": MID_100032,
            "quantity": 4000,
            "unit": "PCS",
            "item_type": "INVENTORY",
            "stock_allocations": _ygy_alloc(4000, warehouse_id=OTHER_WH_ID),
        }],
    })
    stream = await collect_ats_stream_for_ygy(
        db, master_item_ids=[MID_100032], as_of=AS_OF_LATER,
    )
    pack = stream["by_master"][MID_100032]
    assert pack["evidence_complete"] is True
    assert not any(e.kind == "RESERVE" for e in pack["events"])
    assert any("SKIP_OTHER_WAREHOUSE_ALLOCATION" in s for s in pack["skipped"])
    _, row = await _catalog_row(db)
    assert row["ats_cutover_status"] == "READY"
    assert row["stock_dapat_dijual"] == 20000.0


async def test_entrypoint_unit_mismatch_is_gap_not_ready(db):
    await _seed_master_wh(db)
    oid = f"so-uom-{uuid.uuid4().hex[:6]}"
    await db.orders.insert_one({
        "id": oid,
        "branch_id": YGY_BRANCH_ID,
        "created_at": AFTER,
        "cancelled": False,
        "line_items": [{
            "line_id": "ln-uom",
            "master_item_id": MID_100032,
            "quantity": 2000,
            "unit": "BOX",
            "item_type": "INVENTORY",
            "stock_allocations": _ygy_alloc(2000, unit="BOX"),
        }],
    })
    stream = await collect_ats_stream_for_ygy(
        db, master_item_ids=[MID_100032], as_of=AS_OF_LATER,
    )
    pack = stream["by_master"][MID_100032]
    assert pack["evidence_complete"] is False
    assert any("UNIT_MISMATCH" in g for g in pack["gaps"])
    _, row = await _catalog_row(db)
    assert row["ats_cutover_status"] == "UNAVAILABLE"
    assert row["stock_dapat_dijual"] is None
    assert row["physical_qty"] == -19300


async def test_entrypoint_receive_then_reversal_restores_ats(db):
    await _seed_master_wh(db)
    pr_id = f"pr-{uuid.uuid4().hex[:8]}"
    await db[LEDGER_COLLECTION].insert_one({
        "id": pr_id,
        "master_item_id": MID_100032,
        "branch_id": YGY_BRANCH_ID,
        "warehouse_id": YGY_WAREHOUSE_ID,
        "movement_type": MOVEMENT_PURCHASE_RECEIPT,
        "qty_delta": 500,
        "reference_type": "purchase_receipt",
        "reference_id": "pr-line-1",
        "reason": "receipt",
        "actor_id": "t",
        "occurred_at": AFTER,
        "created_at": AFTER,
        "idempotency_key": f"purchase-receipt:{pr_id}",
        "metadata": {"unit": "PCS"},
    })
    await _insert_reversal(
        db,
        original_id=pr_id,
        occurred_at=(CUTOFF + timedelta(hours=2)).isoformat(),
        qty_delta=-500,
    )
    stream = await collect_ats_stream_for_ygy(
        db, master_item_ids=[MID_100032], as_of=AS_OF_LATER,
    )
    pack = stream["by_master"][MID_100032]
    assert pack["evidence_complete"] is True
    kinds = [e.kind for e in pack["events"]]
    assert "RECEIVE_FREE" in kinds
    assert "REVERSE" in kinds
    _, row = await _catalog_row(db)
    assert row["ats_cutover_status"] == "READY"
    assert row["stock_dapat_dijual"] == 20000.0


async def test_entrypoint_reserve_ship_reverse_cancel_returns_baseline(db):
    await _seed_master_wh(db)
    oid = f"so-rsc-{uuid.uuid4().hex[:6]}"
    t_reserve = AFTER
    t_ship = (CUTOFF + timedelta(hours=1, minutes=30)).isoformat()
    t_rev = (CUTOFF + timedelta(hours=2)).isoformat()
    t_cancel = (CUTOFF + timedelta(hours=3)).isoformat()
    await db.orders.insert_one({
        "id": oid,
        "branch_id": YGY_BRANCH_ID,
        "created_at": t_reserve,
        "cancelled": True,
        "cancelled_at": t_cancel,
        "line_items": [{
            "line_id": "ln-rsc",
            "master_item_id": MID_100032,
            "quantity": 2000,
            "unit": "PCS",
            "item_type": "INVENTORY",
            "stock_allocations": _ygy_alloc(2000),
        }],
    })
    ship_id = await _insert_delivery(
        db, order_id=oid, line_id="ln-rsc", line_idx=0, qty=500, occurred_at=t_ship,
    )
    await _insert_reversal(db, original_id=ship_id, occurred_at=t_rev, qty_delta=500)
    stream = await collect_ats_stream_for_ygy(
        db, master_item_ids=[MID_100032], as_of=AS_OF_LATER,
    )
    pack = stream["by_master"][MID_100032]
    assert pack["evidence_complete"] is True
    kinds = [e.kind for e in pack["events"]]
    assert kinds.count("RESERVE") == 1
    assert kinds.count("SHIP_RESERVED") == 1
    assert kinds.count("REVERSE") == 1
    assert kinds.count("RELEASE") == 1
    # RELEASE must restore full reserved qty (net ship after reverse = 0)
    release = next(e for e in pack["events"] if e.kind == "RELEASE")
    assert str(release.qty) == "2000"
    _, row = await _catalog_row(db)
    assert row["ats_cutover_status"] == "READY"
    assert row["stock_dapat_dijual"] == 20000.0
    projected = project_master_ats(
        master_item_id=MID_100032,
        as_of=AS_OF_LATER,
        reservations_at_cutoff=pack["reservations_at_cutoff"],
        events=pack["events"],
        evidence_complete=True,
    )
    assert projected["status"] == "READY"
    assert projected["available_qty"] == 20000.0
    rem = projected.get("remaining_reservations") or {}
    assert all(Decimal(v) == 0 for v in rem.values())


async def test_entrypoint_owner_default_ygy_main_when_no_allocation(db):
    """Owner 2026-11-03: empty alloc → YGY-MAIN full line; 100032 20000−1000=19000."""
    await _seed_master_wh(db)
    oid = f"so-def-{uuid.uuid4().hex[:6]}"
    await db.orders.insert_one({
        "id": oid,
        "branch_id": "50",
        "import_source": "accurate_live",
        "accurate_id": "acc-def-100032",
        "created_at": AFTER,
        "cancelled": False,
        "line_items": [{
            "line_id": "ln-def",
            "master_item_id": MID_100032,
            "quantity": 1000,
            "unit": "PCS",
            "item_type": "INVENTORY",
            # No stock_allocations — Owner default YGY-MAIN
        }],
    })
    stream = await collect_ats_stream_for_ygy(
        db, master_item_ids=[MID_100032], as_of=AS_OF_LATER,
    )
    pack = stream["by_master"][MID_100032]
    assert pack["evidence_complete"] is True
    reserves = [e for e in pack["events"] if e.kind == "RESERVE"]
    assert len(reserves) == 1
    assert str(reserves[0].qty) == "1000"
    _, row = await _catalog_row(db)
    assert row["ats_cutover_status"] == "READY"
    assert row["ats_baseline"] == 20000.0
    assert row["ats_pengurangan"] == 1000.0
    assert row["ats_penambahan"] == 0.0
    assert row["ats_sekarang"] == 19000.0
    assert row["stock_dapat_dijual"] == 19000.0
    assert row["physical_qty"] == -19300  # physical untouched


async def test_entrypoint_empty_alloc_list_defaults_ygy_main(db):
    await _seed_master_wh(db)
    oid = f"so-eal-{uuid.uuid4().hex[:6]}"
    await db.orders.insert_one({
        "id": oid,
        "branch_id": YGY_BRANCH_ID,
        "created_at": AFTER,
        "cancelled": False,
        "line_items": [{
            "line_id": "ln-eal",
            "master_item_id": MID_100032,
            "quantity": 1000,
            "unit": "PCS",
            "item_type": "INVENTORY",
            "stock_allocations": [],
        }],
    })
    _, row = await _catalog_row(db)
    assert row["ats_cutover_status"] == "READY"
    assert row["stock_dapat_dijual"] == 19000.0


async def test_entrypoint_partial_ygy_allocation_cuts_only_ygy_qty(db):
    """Explicit split: YGY 700 + other 300 on line 1000 → cut 700 only; no double invent."""
    await _seed_master_wh(db)
    await db[WAREHOUSES_COLLECTION].insert_one({
        "id": OTHER_WH_ID,
        "branch_id": YGY_BRANCH_ID,
        "code": "YGY-OTHER",
        "name": "Other WH",
        "active": True,
        "is_operational": True,
    })
    oid = f"so-part-{uuid.uuid4().hex[:6]}"
    await db.orders.insert_one({
        "id": oid,
        "branch_id": YGY_BRANCH_ID,
        "created_at": AFTER,
        "cancelled": False,
        "line_items": [{
            "line_id": "ln-part",
            "master_item_id": MID_100032,
            "quantity": 1000,
            "unit": "PCS",
            "item_type": "INVENTORY",
            "stock_allocations": [
                {
                    "allocation_id": "sa-ygy",
                    "warehouse_id": YGY_WAREHOUSE_ID,
                    "qty": 700,
                    "unit": "PCS",
                },
                {
                    "allocation_id": "sa-oth",
                    "warehouse_id": OTHER_WH_ID,
                    "qty": 300,
                    "unit": "PCS",
                },
            ],
        }],
    })
    stream = await collect_ats_stream_for_ygy(
        db, master_item_ids=[MID_100032], as_of=AS_OF_LATER,
    )
    pack = stream["by_master"][MID_100032]
    assert pack["evidence_complete"] is True
    reserves = [e for e in pack["events"] if e.kind == "RESERVE"]
    assert len(reserves) == 1
    assert str(reserves[0].qty) == "700"
    _, row = await _catalog_row(db)
    assert row["stock_dapat_dijual"] == 19300.0  # 20000 - 700


async def test_entrypoint_partial_ygy_only_does_not_invent_remainder(db):
    """Allocations present with YGY 400 of line 1000 → cut 400, do not invent 600."""
    await _seed_master_wh(db)
    oid = f"so-pyr-{uuid.uuid4().hex[:6]}"
    await db.orders.insert_one({
        "id": oid,
        "branch_id": YGY_BRANCH_ID,
        "created_at": AFTER,
        "cancelled": False,
        "line_items": [{
            "line_id": "ln-pyr",
            "master_item_id": MID_100032,
            "quantity": 1000,
            "unit": "PCS",
            "item_type": "INVENTORY",
            "stock_allocations": _ygy_alloc(400),
        }],
    })
    _, row = await _catalog_row(db)
    assert row["ats_cutover_status"] == "READY"
    assert row["stock_dapat_dijual"] == 19600.0  # 20000 - 400


async def test_entrypoint_pre_cutoff_no_alloc_does_not_re_cut_baseline(db):
    await _seed_master_wh(db)
    oid = f"so-old-{uuid.uuid4().hex[:6]}"
    await db.orders.insert_one({
        "id": oid,
        "branch_id": YGY_BRANCH_ID,
        "created_at": BEFORE,
        "cancelled": False,
        "line_items": [{
            "line_id": "ln-old",
            "master_item_id": MID_100032,
            "quantity": 5000,
            "unit": "PCS",
            "item_type": "INVENTORY",
            # empty alloc — must NOT invent RESERVE against baseline
        }],
    })
    stream = await collect_ats_stream_for_ygy(
        db, master_item_ids=[MID_100032], as_of=AS_OF_LATER,
    )
    pack = stream["by_master"][MID_100032]
    assert not any(e.kind == "RESERVE" for e in pack["events"])
    _, row = await _catalog_row(db)
    assert row["stock_dapat_dijual"] == 20000.0


async def test_entrypoint_no_alloc_ship_reserved_does_not_double_cut(db):
    await _seed_master_wh(db)
    oid = f"so-nds-{uuid.uuid4().hex[:6]}"
    await db.orders.insert_one({
        "id": oid,
        "branch_id": "50",
        "import_source": "accurate_live",
        "accurate_id": "acc-nds",
        "created_at": AFTER,
        "cancelled": False,
        "line_items": [{
            "line_id": "ln-nds",
            "master_item_id": MID_100032,
            "quantity": 1000,
            "unit": "PCS",
            "item_type": "INVENTORY",
        }],
    })
    await _insert_delivery(
        db, order_id=oid, line_id="ln-nds", line_idx=0, qty=400, occurred_at=AFTER,
    )
    stream = await collect_ats_stream_for_ygy(
        db, master_item_ids=[MID_100032], as_of=AS_OF_LATER,
    )
    pack = stream["by_master"][MID_100032]
    kinds = [e.kind for e in pack["events"]]
    assert kinds.count("RESERVE") == 1
    assert kinds.count("SHIP_RESERVED") == 1
    _, row = await _catalog_row(db)
    # RESERVE once; SHIP_RESERVED effect 0
    assert row["stock_dapat_dijual"] == 19000.0
    assert row["ats_pengurangan"] == 1000.0


async def test_entrypoint_no_alloc_cancel_releases_remaining(db):
    await _seed_master_wh(db)
    oid = f"so-ncl-{uuid.uuid4().hex[:6]}"
    t_cancel = (CUTOFF + timedelta(hours=3)).isoformat()
    await db.orders.insert_one({
        "id": oid,
        "branch_id": YGY_BRANCH_ID,
        "created_at": AFTER,
        "cancelled": True,
        "cancelled_at": t_cancel,
        "line_items": [{
            "line_id": "ln-ncl",
            "master_item_id": MID_100032,
            "quantity": 1000,
            "unit": "PCS",
            "item_type": "INVENTORY",
        }],
    })
    await _insert_delivery(
        db,
        order_id=oid,
        line_id="ln-ncl",
        line_idx=0,
        qty=200,
        occurred_at=(CUTOFF + timedelta(hours=1, minutes=30)).isoformat(),
    )
    stream = await collect_ats_stream_for_ygy(
        db, master_item_ids=[MID_100032], as_of=AS_OF_LATER,
    )
    pack = stream["by_master"][MID_100032]
    kinds = [e.kind for e in pack["events"]]
    assert kinds.count("RESERVE") == 1
    assert kinds.count("SHIP_RESERVED") == 1
    assert kinds.count("RELEASE") == 1
    release = next(e for e in pack["events"] if e.kind == "RELEASE")
    assert str(release.qty) == "800"  # 1000 - 200 shipped
    _, row = await _catalog_row(db)
    # RESERVE 1000 → 19000; RELEASE 800 → 19800
    assert row["stock_dapat_dijual"] == 19800.0


async def test_list_detail_export_parity_owner_default_no_alloc(db):
    await _seed_master_wh(db)
    oid = f"so-par2-{uuid.uuid4().hex[:6]}"
    await db.orders.insert_one({
        "id": oid,
        "branch_id": "50",
        "import_source": "accurate_live",
        "accurate_id": "acc-par2",
        "created_at": AFTER,
        "cancelled": False,
        "line_items": [{
            "line_id": "ln-par2",
            "master_item_id": MID_100032,
            "quantity": 1000,
            "unit": "PCS",
            "item_type": "INVENTORY",
        }],
    })
    list_cat = await list_physical_stock_catalog(
        db, branch_id=YGY_BRANCH_ID, warehouse_id=YGY_WAREHOUSE_ID, page=1, limit=50,
    )
    detail_cat = await list_physical_stock_catalog(
        db, branch_id=YGY_BRANCH_ID, warehouse_id=YGY_WAREHOUSE_ID, q="100032", page=1, limit=1,
    )
    export_cat = await list_physical_stock_catalog(
        db, branch_id=YGY_BRANCH_ID, warehouse_id=YGY_WAREHOUSE_ID, page=1, limit=1000,
    )

    def _row(cat):
        return next(r for r in cat["items"] if r["master_item_id"] == MID_100032)

    a, b, c = _row(list_cat), _row(detail_cat), _row(export_cat)
    for key in (
        "stock_dapat_dijual",
        "qty_siap_jual",
        "ats_cutover_status",
        "ats_baseline",
        "ats_pengurangan",
        "ats_sekarang",
        "physical_qty",
    ):
        assert a[key] == b[key] == c[key]
    assert a["stock_dapat_dijual"] == 19000.0
    assert a["ats_sekarang"] == 19000.0
    assert a["physical_qty"] == -19300
