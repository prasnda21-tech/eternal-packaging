"""Opname Qty Belum Terkirim parity with Stock Saat Ini.

Isolated replica-set test DB only. Does not touch production or live SOP sessions.
Locks: delivered proof = R2 branch ledger (+ reversal) when r2 on; shipment proven
when r2 off; never session-warehouse-limited; SERVICE excluded; selisih = count−book.
"""
from __future__ import annotations

import os
import uuid
from typing import Dict, List, Optional, Tuple

import pytest
from motor.motor_asyncio import AsyncIOMotorClient

from services.physical_stock import (
    BRANCHES_COLLECTION,
    MASTER_ITEMS_COLLECTION,
    MOVEMENT_OPENING_BALANCE,
    MOVEMENT_REVERSAL,
    MOVEMENT_SALES_DELIVERY,
    WAREHOUSES_COLLECTION,
    ensure_indexes as ensure_physical_indexes,
    list_physical_stock_catalog,
    operational_availability,
    post_movement,
)
from services.r2_go_live import set_r2_authority_active
from services.stock_opname_count_import import (
    BELUM_TERKIRIM_HEADER,
    build_count_template_xlsx,
)
from services.stock_opname_sessions import (
    MODE_FREEZE,
    _snapshot_order_buckets,
    create_stock_opname_session,
    ensure_indexes as ensure_opname_indexes,
    present_opname_line,
    start_stock_opname_cutoff,
)
from services.warehouse_stock_standalone import set_warehouse_stock_standalone_enabled

TEST_DB = "eternal_packaging_test_stock_opname_belum_parity"
MONGO_URL = os.environ.get(
    "MONGO_URL",
    "mongodb://127.0.0.1:27017/?replicaSet=eternal_local_rs",
)
if "replicaSet=" not in MONGO_URL:
    MONGO_URL = MONGO_URL.rstrip("/") + ("&" if "?" in MONGO_URL else "?") + "replicaSet=eternal_local_rs"

pytestmark = pytest.mark.anyio
USER = {"id": "opname-belum-parity-tester", "name": "Tester"}

# Owner-reported production-like SKUs (UAT fixture only — not live Mongo).
UAT_SKUS = (
    {"code": "100001", "book": 52_000, "open_siap": 248_303, "shipped_other_wh": 200_000},
    {"code": "100003", "book": 198_000, "open_siap": 403_614, "shipped_other_wh": 350_000},
    {"code": "100123", "book": 0, "open_siap": 308_072, "shipped_other_wh": 250_000},
)


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
async def db():
    assert "eternal_packaging_preview" not in TEST_DB
    client = AsyncIOMotorClient(MONGO_URL)
    database = client[TEST_DB]
    await client.drop_database(TEST_DB)
    await ensure_physical_indexes(database)
    await ensure_opname_indexes(database)
    try:
        yield database
    finally:
        await client.drop_database(TEST_DB)
        client.close()


async def _branch_wh(db, *, wh_code: str = "MAIN") -> Tuple[str, str]:
    branch_id = f"br-{uuid.uuid4().hex[:8]}"
    warehouse_id = f"wh-{uuid.uuid4().hex[:8]}"
    await db[BRANCHES_COLLECTION].insert_one({
        "id": branch_id, "code": "YGY", "name": "Yogyakarta", "active": True,
    })
    await db[WAREHOUSES_COLLECTION].insert_one({
        "id": warehouse_id, "branch_id": branch_id, "code": wh_code,
        "name": f"Gudang {wh_code}", "active": True, "is_operational": True,
    })
    return branch_id, warehouse_id


async def _item(db, *, code: str, name: str, item_type: str = "INVENTORY") -> str:
    item_id = f"item-{uuid.uuid4().hex[:8]}"
    await db[MASTER_ITEMS_COLLECTION].insert_one({
        "id": item_id,
        "item_code": code,
        "item_name": name,
        "unit": "PCS",
        "is_active": True,
        "item_type": item_type,
        "accurate_item_id": f"ACC-{item_id}",
    })
    return item_id


async def _open(db, item_id: str, branch_id: str, warehouse_id: str, qty: int) -> None:
    if qty == 0:
        return
    await post_movement(db, {
        "master_item_id": item_id,
        "branch_id": branch_id,
        "warehouse_id": warehouse_id,
        "movement_type": MOVEMENT_OPENING_BALANCE,
        "qty_delta": qty,
        "reference_type": "test",
        "reference_id": f"open-{item_id}-{warehouse_id}",
        "reason": "Saldo awal UAT",
        "idempotency_key": f"open-{item_id}-{warehouse_id}-{qty}",
    }, actor_id="tester")


async def _sales_delivery(
    db,
    *,
    item_id: str,
    branch_id: str,
    warehouse_id: str,
    qty: int,
    key: str,
) -> dict:
    return await post_movement(db, {
        "master_item_id": item_id,
        "branch_id": branch_id,
        "warehouse_id": warehouse_id,
        "movement_type": MOVEMENT_SALES_DELIVERY,
        "qty_delta": -abs(qty),
        "reference_type": "test_delivery",
        "reference_id": key,
        "reason": "UAT delivery",
        "idempotency_key": f"sd-{key}",
    }, actor_id="tester")


async def _non_print_order(db, *, branch_id: str, item_id: str, qty: int, oid: Optional[str] = None) -> str:
    order_id = oid or f"so-{uuid.uuid4().hex[:8]}"
    await db.orders.insert_one({
        "id": order_id,
        "branch_id": branch_id,
        "cancelled": False,
        "status": "open",
        "line_items": [{
            "master_item_id": item_id,
            "quantity": qty,
            "production_process": "non_print",
        }],
        "operator_splits": [],
        "shipments": [],
    })
    return order_id


async def _opname_belum(
    db,
    *,
    branch_id: str,
    warehouse_id: str,
    item_id: str,
    book: int,
) -> dict:
    lines = [{"master_item_id": item_id, "book_qty": book, "item_code": "x"}]
    await _snapshot_order_buckets(
        db, None, lines, branch_id=branch_id, warehouse_id=warehouse_id,
    )
    return lines[0]


async def _catalog_belum(db, *, branch_id: str, warehouse_id: str, item_id: str, code: str) -> dict:
    cat = await list_physical_stock_catalog(
        db, branch_id=branch_id, warehouse_id=warehouse_id, q=code,
    )
    row = next(
        (r for r in cat.get("items") or [] if r.get("master_item_id") == item_id),
        None,
    )
    assert row is not None, code
    return row


# --- regressions -----------------------------------------------------------------


async def test_r2_partial_delivery_and_reversal_match_catalog(db):
    await set_r2_authority_active(db, active=True)
    branch_id, wh_main = await _branch_wh(db, wh_code="MAIN")
    item = await _item(db, code="P-PART", name="Partial Cup")
    await _open(db, item, branch_id, wh_main, 1000)
    await _non_print_order(db, branch_id=branch_id, item_id=item, qty=100)

    before = await _opname_belum(
        db, branch_id=branch_id, warehouse_id=wh_main, item_id=item, book=1000,
    )
    assert before["qty_pesanan"] == 0
    assert before["qty_belum_terkirim"] == 100

    delivery = await _sales_delivery(
        db, item_id=item, branch_id=branch_id, warehouse_id=wh_main, qty=40, key="partial-1",
    )
    after_partial = await _opname_belum(
        db, branch_id=branch_id, warehouse_id=wh_main, item_id=item, book=960,
    )
    catalog_partial = await _catalog_belum(
        db, branch_id=branch_id, warehouse_id=wh_main, item_id=item, code="P-PART",
    )
    assert after_partial["qty_belum_terkirim"] == 60
    assert catalog_partial["qty_belum_terkirim"] == 60
    assert after_partial["qty_belum_terkirim"] == catalog_partial["qty_belum_terkirim"]

    # Reversal restores Belum (ledger net of REVERSAL — same as Stock Saat Ini).
    await post_movement(db, {
        "master_item_id": item,
        "branch_id": branch_id,
        "warehouse_id": wh_main,
        "movement_type": MOVEMENT_REVERSAL,
        "qty_delta": 40,
        "reference_type": "physical_stock_ledger",
        "reference_id": delivery["id"],
        "reason": "UAT reverse delivery",
        "idempotency_key": f"rev-{delivery['id']}",
    }, actor_id="tester")
    after_rev = await _opname_belum(
        db, branch_id=branch_id, warehouse_id=wh_main, item_id=item, book=1000,
    )
    catalog_rev = await _catalog_belum(
        db, branch_id=branch_id, warehouse_id=wh_main, item_id=item, code="P-PART",
    )
    assert after_rev["qty_belum_terkirim"] == 100
    assert catalog_rev["qty_belum_terkirim"] == 100


async def test_multi_warehouse_delivery_counts_for_branch_demand(db):
    """Belum must not be limited to the opname session warehouse."""
    await set_r2_authority_active(db, active=True)
    branch_id, wh_main = await _branch_wh(db, wh_code="MAIN")
    wh_alt = f"wh-alt-{uuid.uuid4().hex[:6]}"
    await db[WAREHOUSES_COLLECTION].insert_one({
        "id": wh_alt, "branch_id": branch_id, "code": "ALT",
        "name": "Gudang ALT", "active": True, "is_operational": True,
    })
    item = await _item(db, code="MW-1", name="Multi WH")
    await _open(db, item, branch_id, wh_main, 500)
    await _non_print_order(db, branch_id=branch_id, item_id=item, qty=200)
    await _sales_delivery(
        db, item_id=item, branch_id=branch_id, warehouse_id=wh_alt, qty=80, key="alt-ship",
    )

    # Legacy bug: warehouse filter on MAIN would leave Belum=200.
    # Correct: branch delivery from ALT reduces Belum → 120.
    line = await _opname_belum(
        db, branch_id=branch_id, warehouse_id=wh_main, item_id=item, book=500,
    )
    catalog = await _catalog_belum(
        db, branch_id=branch_id, warehouse_id=wh_main, item_id=item, code="MW-1",
    )
    assert line["qty_belum_terkirim"] == 120
    assert catalog["qty_belum_terkirim"] == 120
    assert line["demand_allocation"] == "unallocated"


async def test_service_addon_excluded_from_physical_buckets(db):
    await set_r2_authority_active(db, active=True)
    branch_id, wh_main = await _branch_wh(db)
    parent = await _item(db, code="CUP-P", name="Cup Parent")
    service = await _item(db, code="SABLON-A", name="Sablon Merah", item_type="SERVICE")
    await _open(db, parent, branch_id, wh_main, 100)
    await db.orders.insert_one({
        "id": f"so-addon-{uuid.uuid4().hex[:6]}",
        "branch_id": branch_id,
        "cancelled": False,
        "line_items": [
            {
                "master_item_id": parent,
                "quantity": 40,
                "production_process": "print",
            },
            {
                "master_item_id": service,
                "quantity": 40,
                "production_process": "print",
                "item_type": "SERVICE",
                "parent_line_idx": 0,
            },
        ],
        # Parent + SERVICE both completed — physical siap = parent only when linked group done.
        "operator_splits": [
            {"line_idx": 0, "qty_completed": 40},
            {"line_idx": 1, "qty_completed": 40},
        ],
        "shipments": [],
    })
    lines = [
        {"master_item_id": parent, "book_qty": 100},
        {"master_item_id": service, "book_qty": 0},
    ]
    await _snapshot_order_buckets(
        db, None, lines, branch_id=branch_id, warehouse_id=wh_main,
    )
    assert lines[0]["qty_belum_terkirim"] == 40  # parent siap only
    assert lines[0]["qty_pesanan"] == 0
    assert lines[1]["qty_belum_terkirim"] == 0
    assert lines[1]["qty_pesanan"] == 0


async def test_standalone_uses_shipment_proof_like_catalog(db):
    await set_r2_authority_active(db, active=False)
    await set_warehouse_stock_standalone_enabled(db, enabled=True)
    branch_id, wh_main = await _branch_wh(db)
    item = await _item(db, code="LEG-1", name="Legacy Ship")
    await _open(db, item, branch_id, wh_main, 300)
    oid = await _non_print_order(db, branch_id=branch_id, item_id=item, qty=90)
    await db.orders.update_one(
        {"id": oid},
        {"$set": {
            "shipments": [{
                "id": "ship-1",
                "sales_delivery_id": "ship-1",
                "delivery_status": "Terkirim",
                "delivered_at": "2026-01-01T00:00:00+00:00",
                "total_qty": 25,
                "qty_delivered": 25,
                "lines": [{"line_idx": 0, "qty": 25}],
            }],
        }},
    )
    line = await _opname_belum(
        db, branch_id=branch_id, warehouse_id=wh_main, item_id=item, book=300,
    )
    catalog = await _catalog_belum(
        db, branch_id=branch_id, warehouse_id=wh_main, item_id=item, code="LEG-1",
    )
    assert line["qty_belum_terkirim"] == 65
    assert catalog["qty_belum_terkirim"] == 65


async def test_snapshot_idempotent_and_difference_stays_count_minus_book(db):
    await set_r2_authority_active(db, active=True)
    branch_id, wh_main = await _branch_wh(db)
    item = await _item(db, code="IDEM-1", name="Idem")
    await _open(db, item, branch_id, wh_main, 80)
    await _non_print_order(db, branch_id=branch_id, item_id=item, qty=30)
    lines_a = [{"master_item_id": item, "book_qty": 80}]
    lines_b = [{"master_item_id": item, "book_qty": 80}]
    await _snapshot_order_buckets(
        db, None, lines_a, branch_id=branch_id, warehouse_id=wh_main,
    )
    await _snapshot_order_buckets(
        db, None, lines_b, branch_id=branch_id, warehouse_id=wh_main,
    )
    assert lines_a[0]["qty_belum_terkirim"] == lines_b[0]["qty_belum_terkirim"] == 30
    assert lines_a[0]["qty_pesanan"] == lines_b[0]["qty_pesanan"] == 0

    presented = present_opname_line(
        {
            **lines_a[0],
            "physical_count_qty": 75,
            "book_qty": 80,
            "qty_pesanan": 0,
            "qty_belum_terkirim": 30,
        },
        mode=MODE_FREEZE,
    )
    assert presented["difference_qty"] == -5  # 75 − 80 only
    assert presented["qty_tersedia_setelah"] == 75 - 0 - 30


async def test_excel_header_rename_and_legacy_alias(db):
    await set_r2_authority_active(db, active=True)
    branch_id, wh_main = await _branch_wh(db)
    item = await _item(db, code="XL-1", name="Excel")
    await _open(db, item, branch_id, wh_main, 10)
    created = await create_stock_opname_session(db, {
        "branch_id": branch_id,
        "warehouse_id": wh_main,
        "mode": MODE_FREEZE,
    }, USER)
    counting = await start_stock_opname_cutoff(db, created["id"], USER)
    blob = build_count_template_xlsx(counting)
    from io import BytesIO
    from openpyxl import Workbook, load_workbook
    from services.stock_opname_count_import import _header_map, COUNT_SHEET

    exported = load_workbook(BytesIO(blob))
    export_headers = [
        str(cell.value).strip()
        for cell in next(exported[COUNT_SHEET].iter_rows(min_row=1, max_row=1))
        if cell.value not in (None, "")
    ]
    assert BELUM_TERKIRIM_HEADER in export_headers
    meta_values = [
        str(row[1]).lower()
        for row in exported["Sesi"].iter_rows(values_only=True)
        if row and len(row) > 1 and row[1] is not None
    ]
    assert any("bukan stok fisik" in value for value in meta_values)

    # Legacy column name still accepted by header map.
    wb = Workbook()
    ws = wb.active
    ws.title = COUNT_SHEET
    ws.append([
        "Kode Barang", "Nama Barang", "Satuan Dasar", "Stock Buku", "Qty Pesanan",
        "Qty Belum Terkirim",  # legacy
        "Stock Fisik", "Selisih", "Qty Tersedia Setelah Opname", "ID Barang",
    ])
    buf = BytesIO()
    wb.save(buf)
    loaded = load_workbook(BytesIO(buf.getvalue()))
    mapping = _header_map(loaded[COUNT_SHEET])
    assert BELUM_TERKIRIM_HEADER in mapping


# --- UAT SKU before/after evidence ----------------------------------------------


async def test_uat_sku_100001_100003_100123_before_after_parity(db):
    """Fixture mirrors Owner-reported shape: large siap-kirim, deliveries on other WH.

    BEFORE (legacy warehouse-filtered delivered): Belum ≈ open_siap (inflated).
    AFTER (branch ledger + reversal-aware helper): Belum = open_siap − shipped_other_wh
    and matches Stock Saat Ini catalog for the same fixture.
    """
    await set_r2_authority_active(db, active=True)
    branch_id, wh_main = await _branch_wh(db, wh_code="MAIN")
    wh_alt = f"wh-alt-{uuid.uuid4().hex[:6]}"
    await db[WAREHOUSES_COLLECTION].insert_one({
        "id": wh_alt, "branch_id": branch_id, "code": "ALT",
        "name": "Gudang ALT", "active": True, "is_operational": True,
    })

    evidence: List[Dict] = []
    for spec in UAT_SKUS:
        item = await _item(db, code=spec["code"], name=f"UAT {spec['code']}")
        await _open(db, item, branch_id, wh_main, spec["book"])
        await _non_print_order(
            db, branch_id=branch_id, item_id=item, qty=spec["open_siap"],
        )
        await _sales_delivery(
            db,
            item_id=item,
            branch_id=branch_id,
            warehouse_id=wh_alt,
            qty=spec["shipped_other_wh"],
            key=f"uat-{spec['code']}",
        )

        # Reproduce legacy (warehouse-filtered) Belum for before/after proof.
        legacy_buckets = operational_availability(
            physical_qty=spec["book"],
            ordered_qty=spec["open_siap"],
            completed_qty=spec["open_siap"],
            r2_delivered_qty=0,  # MAIN-only filter saw no delivery
        )
        after_line = await _opname_belum(
            db,
            branch_id=branch_id,
            warehouse_id=wh_main,
            item_id=item,
            book=spec["book"],
        )
        catalog = await _catalog_belum(
            db,
            branch_id=branch_id,
            warehouse_id=wh_main,
            item_id=item,
            code=spec["code"],
        )
        expected_after = spec["open_siap"] - spec["shipped_other_wh"]
        assert legacy_buckets["qty_belum_terkirim"] == spec["open_siap"]
        assert after_line["qty_belum_terkirim"] == expected_after
        assert catalog["qty_belum_terkirim"] == expected_after
        # Freeze selisih identity unchanged: count − book (informational ATP separate).
        presented = present_opname_line(
            {
                **after_line,
                "book_qty": spec["book"],
                "physical_count_qty": spec["book"],
            },
            mode=MODE_FREEZE,
        )
        assert presented["difference_qty"] == 0
        evidence.append({
            "code": spec["code"],
            "book": spec["book"],
            "before_belum_legacy_wh_filter": legacy_buckets["qty_belum_terkirim"],
            "after_belum_opname": after_line["qty_belum_terkirim"],
            "after_belum_catalog": catalog["qty_belum_terkirim"],
            "difference_if_count_equals_book": presented["difference_qty"],
        })

    # Stable evidence block for PR / Owner (UAT fixture, not production).
    assert [row["code"] for row in evidence] == ["100001", "100003", "100123"]
    assert evidence[0]["before_belum_legacy_wh_filter"] == 248_303
    assert evidence[0]["after_belum_opname"] == 48_303
    assert evidence[1]["before_belum_legacy_wh_filter"] == 403_614
    assert evidence[1]["after_belum_opname"] == 53_614
    assert evidence[2]["before_belum_legacy_wh_filter"] == 308_072
    assert evidence[2]["after_belum_opname"] == 58_072
