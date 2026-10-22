"""GR + HPP atomicity regressions (PR #640 P0).

Stock may commit before HPP (no multi-doc txn with valuation). Contract:
- success → HTTP body with receipt_id, one stock movement, HPP recognized
- HPP failure → error includes receipt_id; stock not doubled; same-key retry resumes HPP
- unrelated cutover SKU drift must not block this receipt's recognition
"""
from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path

import pytest
from fastapi import HTTPException
from motor.motor_asyncio import AsyncIOMotorClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("MONGO_URL", "mongodb://127.0.0.1:27017")
if "replicaSet=" not in os.environ["MONGO_URL"]:
    os.environ["MONGO_URL"] = os.environ["MONGO_URL"].rstrip("/") + (
        "&" if "?" in os.environ["MONGO_URL"] else "?"
    ) + "replicaSet=eternal_local_rs"

from services.hpp_cutover import (  # noqa: E402
    confirm_cutover,
    ensure_indexes,
    list_reconciliation,
)
from services.physical_stock import (  # noqa: E402
    LEDGER_COLLECTION,
    MOVEMENT_PURCHASE_RECEIPT,
    MOVEMENT_REVERSAL,
    ensure_indexes as ensure_stock_indexes,
    get_balance,
)
from services.purchase_goods_receipts import (  # noqa: E402
    cancel_purchase_goods_receipt,
    create_purchase_goods_receipt,
)
from services.r2_go_live import set_r2_authority_active  # noqa: E402

DB_NAME = "eternal_gr_hpp_atomicity_pr640"
USER = {"id": "gr-hpp-user", "name": "GR", "role": "admin", "email": "gr@x"}


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
async def db():
    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    database = client[DB_NAME]
    await client.drop_database(DB_NAME)
    await ensure_stock_indexes(database)
    await ensure_indexes(database)
    await set_r2_authority_active(database, active=True)
    await database.purchase_goods_receipts.create_index("receipt_number", unique=True)
    await database.purchase_goods_receipts.create_index(
        "idempotency_key",
        unique=True,
        partialFilterExpression={"idempotency_key": {"$type": "string"}},
    )
    await database.app_settings.create_index("key", unique=True)
    await database.branches.insert_one(
        {"id": "branch-ygy", "code": "YGY", "name": "Yogyakarta", "active": True}
    )
    await database.warehouses.insert_one(
        {
            "id": "wh-ygy",
            "branch_id": "branch-ygy",
            "code": "MAIN",
            "name": "Gudang Yogyakarta",
            "active": True,
            "is_operational": True,
            "is_default": True,
        }
    )
    yield database
    await client.drop_database(DB_NAME)
    client.close()


async def _seed_item(db, *, mid: str, aid: str, qty: float, code: str = "SKU"):
    await db.master_items.insert_one(
        {"id": mid, "accurate_item_id": aid, "unit": "PCS", "item_code": code, "item_name": code}
    )
    await db.physical_stock_balances.insert_one(
        {
            "id": f"bal-{mid}",
            "master_item_id": mid,
            "branch_id": "branch-ygy",
            "warehouse_id": "wh-ygy",
            "physical_qty": qty,
        }
    )


async def _settings(db):
    await db.app_settings.update_one(
        {"key": "hpp_valuation"},
        {
            "$set": {
                "key": "hpp_valuation",
                "method": "RATA-RATA",
                "recognition_point": "goods_receipt",
                "recognition_label": "Saat Penerimaan Barang",
                "active_version": None,
                "last_recalculation_status": "SUCCESS",
            }
        },
        upsert=True,
    )


async def _open_cutover(db, mid: str, aid: str, cost: float, request_id: str):
    await _settings(db)
    return await confirm_cutover(
        db,
        {
            "client_request_id": request_id,
            "expected_method": "RATA-RATA",
            "expected_recognition_point": "goods_receipt",
            "expected_active_version": None,
            "acknowledge_settings_change": False,
            "lines": [
                {
                    "master_item_id": mid,
                    "accurate_item_id": aid,
                    "item_code": mid,
                    "item_name": mid,
                    "opening_unit_cost": cost,
                    "base_uom": "PCS",
                }
            ],
        },
        USER,
    )


async def _po(db, *, po_id: str, lines: list):
    await db.purchase_orders.insert_one(
        {
            "id": po_id,
            "po_number": f"PB-{po_id}",
            "supplier": "Supplier",
            "cancelled": False,
            "physical_stock_snapshot": {
                "enabled": True,
                "stock_in_trigger": "goods_receipt",
                "stock_out_trigger": "sales_delivery",
            },
            "lines": lines,
        }
    )


def _po_line(mid: str, aid: str, qty: float, unit_price: float, line_id: str):
    return {
        "line_id": line_id,
        "line_idx": 0,
        "master_item_id": mid,
        "accurate_item_id": aid,
        "item_code": aid,
        "item_name": aid,
        "unit": "PCS",
        "ordered_qty": qty,
        "unit_price": unit_price,
    }


async def _row(db, cutover_id: str, mid: str):
    rows = await list_reconciliation(db, cutover_id)
    return next(row for row in rows if row["master_item_id"] == mid)


@pytest.mark.anyio
async def test_1_normal_gr_success_stock_and_hpp(db):
    mid, aid = "sku-ok", "acc-ok"
    await _seed_item(db, mid=mid, aid=aid, qty=100)
    opened = await _open_cutover(db, mid, aid, 1000, "atom-1")
    await _po(db, po_id="po-ok", lines=[_po_line(mid, aid, 40, 1200, "pol-ok")])
    receipt = await create_purchase_goods_receipt(
        db,
        {
            "purchase_order_id": "po-ok",
            "idempotency_key": "gr-ok",
            "warehouse_id": "wh-ygy",
            "lines": [{"purchase_order_line_id": "pol-ok", "received_qty": 40}],
        },
        USER,
    )
    assert receipt["id"]
    assert receipt.get("r2_physical") is True
    bal = await get_balance(db, mid, "branch-ygy", "wh-ygy")
    assert bal["physical_qty"] == 140
    assert await db[LEDGER_COLLECTION].count_documents(
        {"movement_type": MOVEMENT_PURCHASE_RECEIPT, "metadata.receipt_id": receipt["id"]}
    ) == 1
    row = await _row(db, opened["cutover"]["cutover_id"], mid)
    assert row["hpp_status"] == "OK"
    assert row["stock_fisik"] == 140
    assert row["valuation_qty"] == 140


@pytest.mark.anyio
async def test_2_unrelated_cutover_mismatch_does_not_block_gr(db):
    """UAT root trigger: other baseline SKUs drifted; this GR item is consistent."""
    dirty_mid, dirty_aid = "sku-dirty", "acc-dirty"
    mid, aid = "sku-fresh", "acc-fresh"
    await _seed_item(db, mid=dirty_mid, aid=dirty_aid, qty=100, code="DIRTY")
    await _seed_item(db, mid=mid, aid=aid, qty=50, code="FRESH")
    opened = await _open_cutover(db, dirty_mid, dirty_aid, 1000, "atom-2a")
    # Second participant via GR bootstrap path: confirm only one line above.
    # Inject drifted dirty stock AFTER cutover so valuation stays at 100.
    await db.physical_stock_balances.update_one(
        {"master_item_id": dirty_mid, "warehouse_id": "wh-ygy"},
        {"$set": {"physical_qty": 500}},
    )
    # Bootstrap fresh item into same active cutover via first GR.
    await _po(db, po_id="po-fresh", lines=[_po_line(mid, aid, 20, 1100, "pol-fresh")])
    receipt = await create_purchase_goods_receipt(
        db,
        {
            "purchase_order_id": "po-fresh",
            "idempotency_key": "gr-fresh",
            "warehouse_id": "wh-ygy",
            "lines": [{"purchase_order_line_id": "pol-fresh", "received_qty": 20}],
        },
        USER,
    )
    assert receipt["id"]
    rows = await list_reconciliation(db, opened["cutover"]["cutover_id"])
    dirty = next(r for r in rows if r["master_item_id"] == dirty_mid)
    fresh = next(r for r in rows if r["master_item_id"] == mid)
    assert dirty["hpp_status"] == "QTY_MISMATCH"
    assert fresh["hpp_status"] == "OK"
    assert fresh["valuation_qty"] == 70  # 50 pre-GR bootstrap + 20 GR
    assert await db[LEDGER_COLLECTION].count_documents(
        {"movement_type": MOVEMENT_PURCHASE_RECEIPT, "metadata.receipt_id": receipt["id"]}
    ) == 1


@pytest.mark.anyio
async def test_3_hpp_failure_after_stock_surfaces_receipt_id_and_retries(db, monkeypatch):
    from services import hpp_cutover

    mid, aid = "sku-fail", "acc-fail"
    await _seed_item(db, mid=mid, aid=aid, qty=100)
    opened = await _open_cutover(db, mid, aid, 1000, "atom-3")
    await _po(db, po_id="po-fail", lines=[_po_line(mid, aid, 50, 1200, "pol-fail")])
    body = {
        "purchase_order_id": "po-fail",
        "idempotency_key": "gr-fail-retry",
        "warehouse_id": "wh-ygy",
        "lines": [{"purchase_order_line_id": "pol-fail", "received_qty": 50}],
    }
    real = hpp_cutover.rebuild_cutover_valuation

    async def boom(*_a, **_k):
        raise RuntimeError("forced HPP rebuild failure")

    monkeypatch.setattr(hpp_cutover, "rebuild_cutover_valuation", boom)
    with pytest.raises(HTTPException) as caught:
        await create_purchase_goods_receipt(db, body, USER)
    detail = caught.value.detail
    assert caught.value.status_code == 500
    assert isinstance(detail, dict)
    assert detail.get("receipt_id")
    assert detail.get("recoverable") is True
    receipt_id = detail["receipt_id"]
    assert await db.purchase_goods_receipts.count_documents({"id": receipt_id}) == 1
    assert await db[LEDGER_COLLECTION].count_documents(
        {"movement_type": MOVEMENT_PURCHASE_RECEIPT, "metadata.receipt_id": receipt_id}
    ) == 1
    bal = await get_balance(db, mid, "branch-ygy", "wh-ygy")
    assert bal["physical_qty"] == 150

    monkeypatch.setattr(hpp_cutover, "rebuild_cutover_valuation", real)
    again = await create_purchase_goods_receipt(db, body, USER)
    assert again["id"] == receipt_id
    assert await db[LEDGER_COLLECTION].count_documents(
        {"movement_type": MOVEMENT_PURCHASE_RECEIPT, "metadata.receipt_id": receipt_id}
    ) == 1
    row = await _row(db, opened["cutover"]["cutover_id"], mid)
    assert row["hpp_status"] == "OK"
    assert row["valuation_qty"] == 150


@pytest.mark.anyio
async def test_4_own_item_valuation_mismatch_still_fails(db, monkeypatch):
    from services import hpp_cutover

    mid, aid = "sku-own-bad", "acc-own-bad"
    await _seed_item(db, mid=mid, aid=aid, qty=100)
    await _open_cutover(db, mid, aid, 1000, "atom-4")
    await _po(db, po_id="po-own", lines=[_po_line(mid, aid, 10, 1000, "pol-own")])

    real_list = hpp_cutover.list_reconciliation

    async def fake_list(db_arg, cutover_id=None):
        rows = await real_list(db_arg, cutover_id)
        out = []
        for row in rows:
            if row["master_item_id"] == mid:
                out.append(
                    {
                        **row,
                        "hpp_status": "QTY_MISMATCH",
                        "stock_fisik": 999,
                        "valuation_qty": 100,
                        "difference": 899,
                    }
                )
            else:
                out.append(row)
        return out

    monkeypatch.setattr(hpp_cutover, "list_reconciliation", fake_list)
    with pytest.raises(HTTPException) as caught:
        await create_purchase_goods_receipt(
            db,
            {
                "purchase_order_id": "po-own",
                "idempotency_key": "gr-own-bad",
                "warehouse_id": "wh-ygy",
                "lines": [{"purchase_order_line_id": "pol-own", "received_qty": 10}],
            },
            USER,
        )
    assert caught.value.status_code == 500
    assert "valuation qty does not match" in str(caught.value.detail.get("error") or "")
    assert caught.value.detail.get("receipt_id")


@pytest.mark.anyio
async def test_5_multiline_gr_single_posting_each_line(db):
    m1, a1 = "sku-m1", "acc-m1"
    m2, a2 = "sku-m2", "acc-m2"
    await _seed_item(db, mid=m1, aid=a1, qty=10, code="M1")
    await _seed_item(db, mid=m2, aid=a2, qty=20, code="M2")
    await _open_cutover(db, m1, a1, 500, "atom-5")
    await _po(
        db,
        po_id="po-multi",
        lines=[
            {**_po_line(m1, a1, 5, 500, "pol-m1"), "line_idx": 0},
            {**_po_line(m2, a2, 7, 700, "pol-m2"), "line_idx": 1},
        ],
    )
    receipt = await create_purchase_goods_receipt(
        db,
        {
            "purchase_order_id": "po-multi",
            "idempotency_key": "gr-multi",
            "warehouse_id": "wh-ygy",
            "lines": [
                {"purchase_order_line_id": "pol-m1", "received_qty": 5},
                {"purchase_order_line_id": "pol-m2", "received_qty": 7},
            ],
        },
        USER,
    )
    assert len(receipt["lines"]) == 2
    assert await db[LEDGER_COLLECTION].count_documents(
        {"movement_type": MOVEMENT_PURCHASE_RECEIPT, "metadata.receipt_id": receipt["id"]}
    ) == 2
    assert (await get_balance(db, m1, "branch-ygy", "wh-ygy"))["physical_qty"] == 15
    assert (await get_balance(db, m2, "branch-ygy", "wh-ygy"))["physical_qty"] == 27


@pytest.mark.anyio
async def test_6_7_8_cancel_idempotent_no_double_movement(db):
    mid, aid = "sku-cancel", "acc-cancel"
    await _seed_item(db, mid=mid, aid=aid, qty=100)
    opened = await _open_cutover(db, mid, aid, 1000, "atom-6")
    await _po(db, po_id="po-cancel", lines=[_po_line(mid, aid, 30, 1100, "pol-cancel")])
    body = {
        "purchase_order_id": "po-cancel",
        "idempotency_key": "gr-cancel-atom",
        "warehouse_id": "wh-ygy",
        "lines": [{"purchase_order_line_id": "pol-cancel", "received_qty": 30}],
    }
    first = await create_purchase_goods_receipt(db, body, USER)
    second = await create_purchase_goods_receipt(db, body, USER)
    assert second["id"] == first["id"]
    assert await db[LEDGER_COLLECTION].count_documents(
        {"movement_type": MOVEMENT_PURCHASE_RECEIPT, "metadata.receipt_id": first["id"]}
    ) == 1
    cancelled = await cancel_purchase_goods_receipt(db, first["id"], USER, "uat cancel")
    assert cancelled["cancelled"] is True
    again = await cancel_purchase_goods_receipt(db, first["id"], USER, "retry cancel")
    assert again["cancelled"] is True
    assert await db[LEDGER_COLLECTION].count_documents(
        {"movement_type": MOVEMENT_REVERSAL, "metadata.receipt_id": first["id"]}
    ) <= 1 or await db[LEDGER_COLLECTION].count_documents(
        {"movement_type": MOVEMENT_REVERSAL, "metadata.original_receipt_id": first["id"]}
    ) <= 2
    bal = await get_balance(db, mid, "branch-ygy", "wh-ygy")
    assert bal["physical_qty"] == 100
    row = await _row(db, opened["cutover"]["cutover_id"], mid)
    assert row["valuation_qty"] == 100
    assert row["stock_fisik"] == 100
    with pytest.raises(HTTPException) as caught:
        await create_purchase_goods_receipt(db, body, USER)
    assert caught.value.status_code == 409
    assert caught.value.detail["receipt_id"] == first["id"]
