"""Read-only HPP Pembelian monitoring. Isolated DB only."""
from __future__ import annotations

import os
import uuid

import pytest
from motor.motor_asyncio import AsyncIOMotorClient

from services.hpp_cutover import (
    BASELINES,
    EVENTS,
    SOURCE_FIRST_GR_UNIT_COST,
    SOURCE_GOODS_RECEIPT,
    SOURCE_PURCHASE_RETURN,
    SOURCE_STOCK_OPNAME_ADJUSTMENT,
    STATUS_NEEDS_COST,
    STATUS_PENDING_VALUATION,
    STATUS_RECOGNIZED,
    ensure_indexes,
    get_active_cutover,
)
from services.physical_stock import (
    LEDGER_COLLECTION,
    ensure_indexes as ensure_stock_indexes,
    get_balance,
    post_opening_balance,
)
from services.purchase_common import item_identity, money_round
from services.purchase_goods_receipts import create_purchase_goods_receipt
from services.purchase_hpp import get_purchase_hpp_item, list_purchase_hpp
from services.purchase_orders import create_purchase_order
from services.purchase_returns import confirm_purchase_return, create_purchase_return

TEST_DB = "eternal_packaging_test_purchase_hpp"
MONGO_URL = os.environ.get("MONGO_URL", "mongodb://127.0.0.1:27017/?replicaSet=eternal_local_rs")
if "replicaSet=" not in MONGO_URL:
    MONGO_URL = MONGO_URL.rstrip("/") + ("&" if "?" in MONGO_URL else "?") + "replicaSet=eternal_local_rs"

pytestmark = pytest.mark.anyio
USER = {"id": "user-purchase-hpp", "email": "admin@example.com", "name": "Admin"}


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
async def db():
    assert "eternal_packaging_preview" not in TEST_DB
    client = AsyncIOMotorClient(MONGO_URL)
    database = client[TEST_DB]
    await client.drop_database(TEST_DB)
    await ensure_stock_indexes(database)
    await ensure_indexes(database)
    await database.purchase_goods_receipts.create_index("id", unique=True)
    await database.purchase_goods_receipts.create_index("receipt_number", unique=True)
    await database.purchase_goods_receipts.create_index(
        "idempotency_key",
        unique=True,
        partialFilterExpression={"idempotency_key": {"$type": "string"}},
    )
    await database.purchase_returns.create_index("id", unique=True)
    await database.purchase_returns.create_index("return_number", unique=True)
    await database.purchase_returns.create_index(
        "idempotency_key",
        unique=True,
        partialFilterExpression={"idempotency_key": {"$type": "string"}},
    )
    await database.purchase_returns.create_index(
        "confirmation_key",
        unique=True,
        partialFilterExpression={"confirmation_key": {"$type": "string"}},
    )
    yield database
    await client.drop_database(TEST_DB)
    client.close()


async def _org(db):
    if await db.branches.count_documents({"id": "branch-ygy"}) == 0:
        await db.branches.insert_one({
            "id": "branch-ygy", "code": "YGY", "name": "Yogyakarta", "active": True,
        })
        await db.warehouses.insert_one({
            "id": "wh-main", "branch_id": "branch-ygy", "code": "MAIN",
            "name": "Gudang Yogyakarta", "active": True,
        })


async def _item(db, code):
    item_id = str(uuid.uuid4())
    await db.master_items.insert_one({
        "id": item_id,
        "item_code": code,
        "item_name": code,
        "unit": "PCS",
        "base_unit": "PCS",
        "accurate_item_id": item_id,
        "item_type": "INVENTORY",
        "is_active": True,
    })
    await db.product_stock_state.insert_one({
        "_id": item_id,
        "master_item_id": item_id,
        "accurate_item_id": item_id,
        "base_uom": "PCS",
        "total_qty": 0,
        "unprinted_qty": 0,
        "printed_qty": 0,
        "status": "INITIALIZED",
        "initialized": True,
        "pending_event": None,
        "version": 1,
    })
    return item_id


async def _po(db, item_id, qty, price, name, supplier="Supplier HPP"):
    return await create_purchase_order(db, {
        "supplier": supplier,
        "idempotency_key": str(uuid.uuid4()),
        "lines": [{
            "master_item_id": item_id,
            "item_name": name,
            "item_code": name,
            "unit": "PCS",
            "ordered_qty": qty,
            "unit_price": price,
        }],
    }, USER)


async def _receive(db, po, qty, *, idempotency_key=None):
    return await create_purchase_goods_receipt(db, {
        "purchase_order_id": po["id"],
        "idempotency_key": idempotency_key or str(uuid.uuid4()),
        "lines": [{"purchase_order_line_id": po["lines"][0]["line_id"], "received_qty": qty}],
    }, USER)


def _row(payload, item_id):
    rows = payload["rows"] if isinstance(payload, dict) and "rows" in payload else [payload]
    return next(row for row in rows if row["master_item_id"] == item_id)


async def test_latest_gr_versus_previous_hpp_weighted_average(db):
    await _org(db)
    item_id = await _item(db, "HPP-REV-AVG")
    first = await _po(db, item_id, 100, 1000, "HPP-REV-AVG")
    await _receive(db, first, 100, idempotency_key="gr-hpp-1000")
    second = await _po(db, item_id, 100, 1300, "HPP-REV-AVG")
    receipt = await _receive(db, second, 100, idempotency_key="gr-hpp-1300")

    listing = await list_purchase_hpp(db)
    row = _row(listing, item_id)
    assert row["hpp_status"] == "ADA_HPP"
    assert row["hpp_status_label"] == "Ada HPP"
    assert row["stock_fisik"] == 200
    assert row["hpp_before_latest_gr"] == 1000
    assert row["latest_purchase_unit_cost"] == 1300
    assert row["price_vs_hpp_delta"] == 300
    assert row["price_vs_hpp_pct"] == 30
    assert row["hpp_after_latest_gr"] == 1150
    assert row["current_hpp"] == 1150
    assert row["inventory_value"] == 230000
    assert row["hpp_current_diverges_from_last_gr"] is False
    assert row["latest_gr_id"] == receipt["id"]
    assert row["latest_gr_number"] == receipt["receipt_number"]
    assert row["latest_supplier"] == "Supplier HPP"
    assert row["latest_po_number"] == second["po_number"]
    assert row["latest_gr_qty"] == 100
    assert await db.customer_pricing_versions.count_documents({}) == 0
    assert (await db.product_stock_state.find_one({"master_item_id": item_id}))["total_qty"] == 0
    assert (await get_balance(db, item_id, "branch-ygy", "wh-main"))["physical_qty"] == 200

    detail = await get_purchase_hpp_item(db, item_id)
    types = [event["source_type"] for event in detail["history"]]
    assert SOURCE_GOODS_RECEIPT in types
    assert SOURCE_PURCHASE_RETURN not in types
    gr_rows = [event for event in detail["history"] if event["source_type"] == SOURCE_GOODS_RECEIPT]
    latest = gr_rows[-1]
    assert latest["incoming_cost_label"] == "Harga Pembelian"
    assert latest["incoming_unit_cost"] == 1300
    assert latest["hpp_before"] == 1000
    assert latest["hpp_after"] == 1150
    assert latest["price_delta"] == 300
    assert latest["price_pct"] == 30


async def test_first_gr_bootstrap_flag_and_zero_delta(db):
    await _org(db)
    item_id = await _item(db, "HPP-REV-BOOT")
    await post_opening_balance(db, item_id, {
        "branch_id": "branch-ygy",
        "warehouse_id": "wh-main",
        "physical_qty": 100,
        "reason": "Saldo awal HPP review",
        "idempotency_key": f"open-{item_id}",
    }, actor_id="user-purchase-hpp")
    po = await _po(db, item_id, 100, 1000, "HPP-REV-BOOT")
    await _receive(db, po, 100, idempotency_key="gr-boot")
    row = _row(await list_purchase_hpp(db), item_id)
    assert row["bootstrap_first_gr"] is True
    assert row["current_hpp"] == 1000
    assert row["stock_fisik"] == 200
    baseline = await db[BASELINES].find_one({"master_item_id": item_id}, {"_id": 0})
    assert baseline["provenance_source"] == SOURCE_FIRST_GR_UNIT_COST
    detail = await get_purchase_hpp_item(db, item_id)
    opening = next(event for event in detail["history"] if event["source_type"] == "OPENING_HPP")
    assert "penerimaan pertama" in opening["source_type_label"].lower()


async def test_return_is_history_not_latest_purchase_and_can_diverge(db):
    await _org(db)
    item_id = await _item(db, "HPP-REV-RET")
    first = await _po(db, item_id, 100, 1000, "HPP-REV-RET")
    await _receive(db, first, 100, idempotency_key="gr-ret-1000")
    second = await _po(db, item_id, 100, 1300, "HPP-REV-RET")
    receipt = await _receive(db, second, 100, idempotency_key="gr-ret-1300")
    draft = await create_purchase_return(db, {
        "idempotency_key": "hpp-ret-draft",
        "lines": [{
            "purchase_goods_receipt_id": receipt["id"],
            "purchase_goods_receipt_line_id": receipt["lines"][0]["line_id"],
            "return_qty": 50,
        }],
    }, USER)
    await confirm_purchase_return(db, draft["id"], {"idempotency_key": "hpp-ret-confirm"}, USER)

    row = _row(await list_purchase_hpp(db), item_id)
    assert row["latest_purchase_unit_cost"] == 1300
    assert row["hpp_before_latest_gr"] == 1000
    assert row["hpp_after_latest_gr"] == 1150
    assert row["latest_gr_id"] == receipt["id"]
    assert row["current_hpp"] == money_round((200 * 1150 - 50 * 1300) / 150)
    assert row["hpp_current_diverges_from_last_gr"] is True
    detail = await get_purchase_hpp_item(db, item_id)
    types = [event["source_type"] for event in detail["history"]]
    assert SOURCE_PURCHASE_RETURN in types
    ret = next(event for event in detail["history"] if event["source_type"] == SOURCE_PURCHASE_RETURN)
    assert ret["incoming_cost_label"] == "Harga penerimaan asal"
    assert ret["incoming_unit_cost"] == 1300


async def test_opname_appears_in_history_not_as_latest_purchase(db):
    await _org(db)
    item_id = await _item(db, "HPP-REV-OP")
    po = await _po(db, item_id, 100, 1000, "HPP-REV-OP")
    receipt = await _receive(db, po, 100, idempotency_key="gr-op-1000")
    cutover = await get_active_cutover(db)
    identity = item_identity({"master_item_id": item_id, "accurate_item_id": item_id})
    await db[EVENTS].insert_one({
        "event_id": str(uuid.uuid4()),
        "source_type": SOURCE_STOCK_OPNAME_ADJUSTMENT,
        "source_key": f"stock-opname:sess-{item_id}:line-1",
        "status": STATUS_RECOGNIZED,
        "qty": 10,
        "unit_cost": 1000,
        "total_cost": 10000,
        "master_item_id": item_id,
        "accurate_item_id": item_id,
        "item_identity": identity,
        "item_code": "HPP-REV-OP",
        "item_name": "HPP-REV-OP",
        "cutover_id": cutover["cutover_id"],
        "recognized_at": "2026-10-07T00:00:00+00:00",
        "created_at": "2026-10-07T00:00:00+00:00",
    })
    row = _row(await list_purchase_hpp(db), item_id)
    assert row["latest_gr_id"] == receipt["id"]
    assert row["latest_purchase_unit_cost"] == 1000
    assert row["current_hpp"] == 1000
    detail = await get_purchase_hpp_item(db, item_id)
    opname = next(event for event in detail["history"] if event["source_type"] == SOURCE_STOCK_OPNAME_ADJUSTMENT)
    assert opname["incoming_cost_label"] == "HPP Berlaku"
    assert opname["source_type_label"] == "Stock Opname"


async def test_pending_valuation_and_needs_cost_do_not_invent_hpp(db):
    await _org(db)
    pending_id = await _item(db, "HPP-REV-PEND")
    need_id = await _item(db, "HPP-REV-NEED")
    seed = await _item(db, "HPP-REV-SEED")
    po = await _po(db, seed, 10, 1000, "HPP-REV-SEED")
    await _receive(db, po, 10, idempotency_key="gr-seed")
    cutover = await get_active_cutover(db)
    await db[EVENTS].insert_one({
        "event_id": str(uuid.uuid4()),
        "source_type": SOURCE_GOODS_RECEIPT,
        "source_key": f"gr:pending-{pending_id}:line",
        "status": STATUS_PENDING_VALUATION,
        "qty": None,
        "unit_cost": None,
        "master_item_id": pending_id,
        "item_identity": item_identity({"master_item_id": pending_id, "accurate_item_id": pending_id}),
        "cutover_id": cutover["cutover_id"],
        "recognized_at": "2026-10-08T00:00:00+00:00",
        "created_at": "2026-10-08T00:00:00+00:00",
    })
    await db[EVENTS].insert_one({
        "event_id": str(uuid.uuid4()),
        "source_type": SOURCE_GOODS_RECEIPT,
        "source_key": f"gr:need-{need_id}:line",
        "status": STATUS_NEEDS_COST,
        "qty": None,
        "unit_cost": None,
        "master_item_id": need_id,
        "item_identity": item_identity({"master_item_id": need_id, "accurate_item_id": need_id}),
        "cutover_id": cutover["cutover_id"],
        "recognized_at": "2026-10-08T00:00:00+00:00",
        "created_at": "2026-10-08T00:00:00+00:00",
    })
    listing = await list_purchase_hpp(db)
    pending = _row(listing, pending_id)
    need = _row(listing, need_id)
    assert pending["hpp_status"] == STATUS_PENDING_VALUATION
    assert pending["hpp_status_label"] == "Menunggu valuasi"
    assert pending["current_hpp"] is None
    assert pending["hpp_before_latest_gr"] is None
    assert pending["latest_purchase_unit_cost"] is None
    assert need["hpp_status"] == STATUS_NEEDS_COST
    assert need["hpp_status_label"] == "Butuh harga PO"
    assert need["current_hpp"] is None
    pending_detail = await get_purchase_hpp_item(db, pending_id)
    assert pending_detail["history"][0]["hpp_before"] is None
    assert pending_detail["history"][0]["hpp_after"] is None


async def test_does_not_use_published_customer_pricing_or_legacy_stock(db):
    await _org(db)
    item_id = await _item(db, "HPP-REV-PUB")
    await db.product_stock_state.update_one(
        {"master_item_id": item_id},
        {"$set": {"total_qty": 999, "unprinted_qty": 999}},
    )
    await db.customer_pricing_groups.insert_one({
        "id": "group-hpp-rev",
        "group_name": "HPP-REV",
        "enabled": True,
        "current_published_version_id": "pub-hpp-rev",
    })
    await db.customer_pricing_versions.insert_one({
        "id": "pub-hpp-rev",
        "pricing_group_id": "group-hpp-rev",
        "status": "PUBLISHED",
        "pricing_mode": "HPP_FORMULA",
        "hpp_source": "MANUAL_HPP",
        "products": [{
            "master_item_id": item_id,
            "accurate_item_id": item_id,
            "manual_hpp": 7777,
            "hpp_snapshot": 7777,
            "hpp_status": "OK",
        }],
    })
    await post_opening_balance(db, item_id, {
        "branch_id": "branch-ygy",
        "warehouse_id": "wh-main",
        "physical_qty": 12,
        "reason": "R2 only",
        "idempotency_key": f"open-pub-{item_id}",
    }, actor_id="user-purchase-hpp")
    row = _row(await list_purchase_hpp(db), item_id)
    assert row["stock_fisik"] == 12
    assert row["current_hpp"] is None
    assert row["inventory_value"] is None
    assert row["hpp_status"] == "BELUM_ADA_HPP"
    assert await db.customer_pricing_versions.count_documents({}) == 1
    assert (await db.product_stock_state.find_one({"master_item_id": item_id}))["total_qty"] == 999
    assert await db[LEDGER_COLLECTION].count_documents({"master_item_id": item_id}) >= 1


async def test_export_xlsx_includes_comparison_columns(db):
    await _org(db)
    item_id = await _item(db, "HPP-REV-XLS")
    first = await _po(db, item_id, 100, 1000, "HPP-REV-XLS")
    await _receive(db, first, 100, idempotency_key="gr-xls-1000")
    second = await _po(db, item_id, 100, 1300, "HPP-REV-XLS")
    await _receive(db, second, 100, idempotency_key="gr-xls-1300")
    from services.purchase_hpp import export_xlsx
    listing = await list_purchase_hpp(db, q="HPP-REV-XLS")
    payload = export_xlsx(listing["rows"])
    assert payload[:2] == b"PK"
    assert len(payload) > 1000
    from io import BytesIO
    from openpyxl import load_workbook
    wb = load_workbook(BytesIO(payload))
    ws = wb.active
    headers = [cell.value for cell in ws[1]]
    assert headers[8] == "Persentase"
    assert "HPP Sebelum Pembelian Terakhir" in headers
    pct_cell = ws.cell(row=2, column=9)
    assert abs(float(pct_cell.value) - 0.30) < 1e-9
    assert pct_cell.number_format == "0.00%"
    assert ws.cell(row=2, column=6).number_format == "#,##0.00"
    assert ws.cell(row=2, column=7).number_format == "#,##0.00"
