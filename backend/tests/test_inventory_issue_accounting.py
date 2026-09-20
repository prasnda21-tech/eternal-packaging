"""Phase 4B.3 financial inventory-issue evaluation — eligibility, WIB, FIFO/MA, GET."""
from __future__ import annotations

import os
import sys
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ.setdefault("MONGO_URL", "mongodb://127.0.0.1:27017")
_worker = os.environ.get("PYTEST_XDIST_WORKER") or "main"
os.environ["DB_NAME"] = f"eternal_packaging_inv_issue_acct_test_{_worker}"
os.environ.setdefault("JWT_SECRET", "test-secret-key-12345678901234567890")
os.environ.setdefault("CORS_ORIGINS", "*")

import server
from server import app, current_user
from services.accounting_periods import ensure_indexes as ensure_periods
from services.chart_of_accounts import create_account, ensure_indexes as ensure_coa
from services.general_ledger import SOURCE_LABELS
from services.inventory_issue_accounting import (
    CODE_CHRONOLOGY_CONFLICT,
    CODE_INSUFFICIENT,
    CODE_INVALID_TS,
    CODE_INVALID_UOM,
    CODE_MISSING_MASTER,
    CODE_PRE_EFFECTIVE,
    CODE_SERVICE,
    CODE_UNKNOWN_TYPE,
    CODE_WAITING,
    MSG_CHRONOLOGY_CONFLICT,
    METHOD_AVG,
    METHOD_FIFO,
    SOURCE_TYPE,
    STATUS_BLOCKED,
    STATUS_EXCLUDED,
    STATUS_PENDING,
    STATUS_POSTED,
    STATUS_WAITING,
    compute_fifo_consumption,
    compute_rata_rata,
    evaluate_inventory_issue,
    evaluate_inventory_issues,
    fifo_remaining_layers,
    movement_journal_date,
    parse_movement_instant,
    public_issue_view,
    wib_journal_date_from_instant,
    ensure_indexes,
)
from services.inventory_issue_posting import PERM_POST, PERM_VIEW, post_inventory_issue_accounting
from services.journal_core import JOURNAL_ENTRIES, JOURNAL_LINES, ensure_indexes as ensure_journal
from services.purchase_accounting_settings import SETTINGS_KEY
from services.purchase_goods_receipt_posting import post_purchase_goods_receipt_accounting
from permissions import ALL_PERMISSIONS, ROLE_DEFAULT_PERMISSIONS

POST_USER = {
    "id": "admin-cogs-acct",
    "email": "admin@eternalpackaging.com",
    "name": "Admin COGS Acct",
    "role": "admin",
}

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture(autouse=True)
def bind_motor():
    from motor.motor_asyncio import AsyncIOMotorClient

    server.client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    server.db = server.client[os.environ["DB_NAME"]]


@pytest.fixture(autouse=True)
async def indexes(bind_motor):
    await ensure_coa(server.db)
    await ensure_journal(server.db)
    await ensure_periods(server.db)
    await ensure_indexes(server.db)
    await server.db.app_settings.delete_many({})
    await server.db.accounting_periods.delete_many({})
    await server.db.purchase_orders.delete_many({})
    await server.db.purchase_goods_receipts.delete_many({})
    await server.db.master_items.delete_many({})
    await server.db.product_unit_settings.delete_many({})
    await server.db.stock_movements.delete_many({})
    await server.db.inventory_issue_accounting.delete_many({})
    await server.db.inventory_financial_item_locks.delete_many({})
    await server.db.inventory_financial_control.delete_many({})
    await server.db[JOURNAL_ENTRIES].delete_many({})
    await server.db[JOURNAL_LINES].delete_many({})
    await server.db.app_settings.update_one(
        {"key": "physical_stock"},
        {"$set": {"key": "physical_stock", "enabled": False, "stock_in_trigger": "goods_receipt"}},
        upsert=True,
    )
    yield


def _admin():
    app.dependency_overrides[current_user] = lambda: dict(POST_USER)


async def _coa(code_prefix: str, name: str, account_type: str) -> dict:
    return await create_account(
        server.db,
        {"code": f"{code_prefix}{uuid.uuid4().hex[:6]}", "name": name, "account_type": account_type},
        user={"id": "t", "name": "t", "role": "admin"},
    )


async def _configure_inventory(effective="2026-10-01", inventory=None, method="FIFO"):
    payable = await _coa("21", "Hutang Usaha", "APAY")
    clearing = await _coa("21", "Purchase Clearing", "OCLY")
    inventory = inventory or await _coa("13", "Persediaan", "INTR")
    cogs = await _coa("51", "HPP", "COGS")
    await server.db.app_settings.update_one(
        {"key": SETTINGS_KEY},
        {
            "$set": {
                "key": SETTINGS_KEY,
                "payable_account_id": payable["id"],
                "purchase_clearing_account_id": clearing["id"],
                "inventory_account_id": inventory["id"],
                "cogs_account_id": cogs["id"],
                "inventory_accounting_effective_date": effective,
            }
        },
        upsert=True,
    )
    await server.db.app_settings.update_one(
        {"key": "hpp_valuation"},
        {"$set": {"key": "hpp_valuation", "method": method}},
        upsert=True,
    )
    return inventory, clearing, payable, cogs


async def _seed_master(mid: str, item_type="INVENTORY", unit="PCS"):
    await server.db.master_items.delete_many({"id": mid})
    doc = {"id": mid, "item_type": item_type, "unit": unit, "item_name": mid, "item_code": mid}
    await server.db.master_items.insert_one(doc)
    return doc


async def _seed_gr(*, qty=100, price=10, receipt_date="2026-10-02", mid=None, inventory=None):
    if inventory is None:
        inventory, *_ = await _configure_inventory()
    mid = mid or f"m-{uuid.uuid4().hex[:8]}"
    await _seed_master(mid)
    po_id = f"po-{uuid.uuid4().hex[:8]}"
    line_id = f"ln-{uuid.uuid4().hex[:8]}"
    po = {
        "id": po_id,
        "po_number": f"PB-{uuid.uuid4().hex[:6].upper()}",
        "order_date": "2026-09-01",
        "cancelled": False,
        "supplier": "PT Supplier",
        "lines": [{
            "line_id": line_id,
            "ordered_qty": max(qty, 1000),
            "unit_price": price,
            "unit": "PCS",
            "master_item_id": mid,
        }],
    }
    rid = str(uuid.uuid4())
    gr = {
        "id": rid,
        "receipt_number": f"GR-{uuid.uuid4().hex[:6].upper()}",
        "purchase_order_id": po_id,
        "po_number": po["po_number"],
        "receipt_date": receipt_date,
        "supplier": "PT Supplier",
        "cancelled": False,
        "source": "po_linked",
        "lines": [{
            "line_id": f"gl-{uuid.uuid4().hex[:8]}",
            "purchase_order_line_id": line_id,
            "received_qty": qty,
            "unit": "PCS",
            "master_item_id": mid,
            "unit_price": price,
        }],
        "created_at": f"{receipt_date}T00:00:00Z",
    }
    await server.db.purchase_orders.insert_one(po)
    await server.db.purchase_goods_receipts.insert_one(gr)
    posted = await post_purchase_goods_receipt_accounting(
        server.db, rid, POST_USER, serialize_in_process=False
    )
    if isinstance(posted, dict):
        gr["accounting_journal_id"] = posted.get("accounting_journal_id")
    return mid, gr, inventory


async def _seed_movement(
    *,
    mid,
    qty=10,
    date="2026-10-05T03:00:00Z",
    movement_type="PENGIRIMAN",
    movement_id=None,
    tx_uom="PCS",
    base_uom="PCS",
    factor=1,
    created_at=None,
    status=None,
    voided_at=None,
):
    mov_id = movement_id or str(uuid.uuid4())
    doc = {
        "id": mov_id,
        "movement_type": movement_type,
        "master_item_id": mid,
        "date": date,
        "created_at": created_at or date,
        "transaction_qty": qty,
        "transaction_uom": tx_uom,
        "conversion_factor": factor,
        "base_qty": qty * (factor if tx_uom != base_uom else 1) if factor != 1 and tx_uom != base_uom else qty,
        "base_uom": base_uom,
        "reference_type": "sales_delivery",
        "reference_id": f"ref-{mov_id[:8]}",
        "reference_number": f"SJ-{mov_id[:6]}",
        "order_id": f"so-{mov_id[:6]}",
        "line_id": f"ln-{mov_id[:6]}",
        "status": status or "POSTED",
    }
    if voided_at:
        doc["voided_at"] = voided_at
    if tx_uom != base_uom:
        doc["base_qty"] = qty * factor
    await server.db.stock_movements.insert_one(doc)
    doc.pop("_id", None)
    return doc


async def test_wib_16_59_same_day():
    instant = parse_movement_instant("2026-09-20T16:59:59Z")
    assert instant is not None
    assert wib_journal_date_from_instant(instant) == "2026-09-20"


async def test_wib_17_00_next_day():
    instant = parse_movement_instant("2026-09-20T17:00:00Z")
    assert wib_journal_date_from_instant(instant) == "2026-09-21"


async def test_wib_18_30_next_day():
    instant = parse_movement_instant("2026-09-20T18:30:00Z")
    assert wib_journal_date_from_instant(instant) == "2026-09-21"


async def test_date_only_and_missing_timestamp_block():
    assert parse_movement_instant("2026-09-20") is None
    assert parse_movement_instant("") is None
    assert parse_movement_instant(None) is None
    assert parse_movement_instant("not-a-date") is None
    assert movement_journal_date({"date": "2026-09-20"}) is None


async def test_naive_full_datetime_is_utc():
    instant = parse_movement_instant("2026-09-20T17:00:00")
    assert instant is not None
    assert instant.tzinfo is not None
    assert wib_journal_date_from_instant(instant) == "2026-09-21"


async def test_permissions_and_gl_label():
    assert PERM_VIEW in ALL_PERMISSIONS
    assert PERM_POST in ALL_PERMISSIONS
    assert PERM_VIEW in ROLE_DEFAULT_PERMISSIONS.get("admin", [])
    assert PERM_POST in ROLE_DEFAULT_PERMISSIONS.get("admin", [])
    for role, perms in ROLE_DEFAULT_PERMISSIONS.items():
        if role == "admin":
            continue
        assert PERM_VIEW not in perms
        assert PERM_POST not in perms
    assert SOURCE_LABELS[SOURCE_TYPE] == "HPP / Pengeluaran Persediaan"
    assert "inventory_issue_reversal" not in SOURCE_LABELS


async def test_fifo_final_residual_rounding_pure():
    layers = [{
        "acquisition_key": "r1:l1",
        "receipt_id": "r1",
        "receipt_line_id": "l1",
        "purchase_order_line_id": "p1",
        "inventory_account_id": "inv",
        "remaining_base_qty": Decimal("3"),
        "remaining_value": Decimal("10.00"),
        "base_unit_cost": Decimal("3.33"),
    }]
    first, err = compute_fifo_consumption(issue_base_qty=Decimal("1"), layers=layers)
    assert err is None
    assert Decimal(str(first[0]["total_cost"])) == Decimal("3.33")
    consumed = {"r1:l1": {"qty": Decimal("2"), "value": Decimal("6.66")}}
    remaining = fifo_remaining_layers([{
        "acquisition_key": "r1:l1",
        "receipt_id": "r1",
        "receipt_line_id": "l1",
        "purchase_order_line_id": "p1",
        "inventory_account_id": "inv",
        "financial_inventory_item_key": "m:x",
        "base_qty": Decimal("3"),
        "total_cost": Decimal("10.00"),
        "base_unit_cost": Decimal("3.33"),
        "receipt_date": "2026-10-01",
    }], "m:x", consumed)
    last, err = compute_fifo_consumption(issue_base_qty=Decimal("1"), layers=remaining)
    assert err is None
    assert Decimal(str(last[0]["total_cost"])) == Decimal("3.34")


async def test_rata_rata_full_depletion_pure():
    computed, err = compute_rata_rata(
        item_key="m:x",
        issue_base_qty=Decimal("10"),
        issue_instant=datetime(2026, 10, 5, 3, tzinfo=timezone.utc),
        issue_id="iss-2",
        issue_journal_date="2026-10-05",
        acquisitions=[{
            "financial_inventory_item_key": "m:x",
            "receipt_date": "2026-10-02",
            "receipt_id": "r1",
            "receipt_line_id": "l1",
            "inventory_account_id": "inv",
            "base_qty": Decimal("10"),
            "total_cost": Decimal("100.01"),
        }],
        live_issue_bases=[],
    )
    assert err is None
    assert computed["total_cogs"] == Decimal("100.01")
    assert computed["qty_after"] == Decimal("0")
    assert computed["inventory_value_after"] == Decimal("0")


async def test_valid_pengiriman_pending_and_no_pengiriman_excluded():
    mid, gr, inv = await _seed_gr(qty=50, price=20)
    mov = await _seed_movement(mid=mid, qty=10)
    other = await _seed_movement(mid=mid, qty=5, movement_type="PENERIMAAN", movement_id=str(uuid.uuid4()))
    pending = await evaluate_inventory_issue(server.db, mov["id"])
    assert pending["status"] == STATUS_PENDING
    assert pending["can_post"] is True
    assert pending["computed_cogs"] == 200.0
    listed = await evaluate_inventory_issues(server.db)
    ids = {row["movement_id"] for row in listed}
    assert mov["id"] in ids
    assert other["id"] not in ids
    not_issue = await evaluate_inventory_issue(server.db, other["id"])
    assert not_issue["can_post"] is False
    assert not_issue["status"] == STATUS_EXCLUDED


async def test_service_and_pre_effective_excluded_do_not_block():
    await _configure_inventory(effective="2026-10-01")
    svc = "m-svc"
    await _seed_master(svc, item_type="SERVICE")
    inv_mid, _, _ = await _seed_gr(qty=20, price=10)
    svc_mov = await _seed_movement(mid=svc, qty=2, date="2026-10-03T03:00:00Z")
    pre = await _seed_movement(mid=inv_mid, qty=2, date="2026-09-20T03:00:00Z")
    later = await _seed_movement(mid=inv_mid, qty=2, date="2026-10-06T03:00:00Z")
    svc_row = await evaluate_inventory_issue(server.db, svc_mov["id"])
    pre_row = await evaluate_inventory_issue(server.db, pre["id"])
    later_row = await evaluate_inventory_issue(server.db, later["id"])
    assert svc_row["status"] == STATUS_EXCLUDED
    assert svc_row["blocker_code"] == CODE_SERVICE
    assert pre_row["status"] == STATUS_EXCLUDED
    assert pre_row["blocker_code"] == CODE_PRE_EFFECTIVE
    assert later_row["status"] == STATUS_PENDING
    assert later_row["can_post"] is True


async def test_missing_master_unknown_type_invalid_uom_invalid_ts_block_chronology():
    mid, _, _ = await _seed_gr(qty=40, price=10)
    ghost = await _seed_movement(mid="missing-master", qty=2, date="2026-10-03T03:00:00Z")
    await server.db.master_items.delete_many({"id": "missing-master"})
    unknown_id = "m-unknown"
    await _seed_master(unknown_id, item_type="WIDGET")
    unknown = await _seed_movement(mid=unknown_id, qty=2, date="2026-10-03T04:00:00Z")
    uom_mid, _, _ = await _seed_gr(qty=10, price=10, mid="m-uom")
    bad_uom = await _seed_movement(
        mid=uom_mid, qty=2, date="2026-10-04T03:00:00Z", tx_uom="CARTON", base_uom="PCS", factor=1
    )
    bad_ts = await _seed_movement(mid=mid, qty=2, date="2026-10-01")
    later = await _seed_movement(mid=mid, qty=2, date="2026-10-07T03:00:00Z")
    ghost_row = await evaluate_inventory_issue(server.db, ghost["id"])
    unknown_row = await evaluate_inventory_issue(server.db, unknown["id"])
    uom_row = await evaluate_inventory_issue(server.db, bad_uom["id"])
    ts_row = await evaluate_inventory_issue(server.db, bad_ts["id"])
    later_row = await evaluate_inventory_issue(server.db, later["id"])
    assert ghost_row["status"] == STATUS_BLOCKED
    assert ghost_row["blocker_code"] == CODE_MISSING_MASTER
    assert unknown_row["blocker_code"] == CODE_UNKNOWN_TYPE
    assert uom_row["blocker_code"] == CODE_INVALID_UOM
    assert ts_row["blocker_code"] == CODE_INVALID_TS
    assert later_row["status"] == STATUS_WAITING
    assert later_row["blocker_code"] == CODE_WAITING
    assert later_row["can_post"] is False


async def test_invalid_timestamp_blocks_later_valid_issue():
    mid, _, _ = await _seed_gr(qty=20, price=10)
    broken = await _seed_movement(mid=mid, qty=2, date="not-a-timestamp")
    later = await _seed_movement(mid=mid, qty=2, date="2026-10-08T03:00:00Z")
    broken_row = await evaluate_inventory_issue(server.db, broken["id"])
    later_row = await evaluate_inventory_issue(server.db, later["id"])
    assert broken_row["status"] == STATUS_BLOCKED
    assert broken_row["blocker_code"] == CODE_INVALID_TS
    assert broken_row["journal_date"] is None
    assert later_row["status"] == STATUS_WAITING
    assert later_row["can_post"] is False
    from services.inventory_issue_posting import post_inventory_issue_accounting
    from fastapi import HTTPException
    try:
        await post_inventory_issue_accounting(server.db, later["id"], POST_USER)
        raise AssertionError("later issue posted despite invalid earlier timestamp")
    except HTTPException as exc:
        assert exc.status_code == 409


async def test_waiting_earlier_issue_and_independent_items():
    a, _, _ = await _seed_gr(qty=30, price=10, mid="m-a")
    b, _, _ = await _seed_gr(qty=30, price=10, mid="m-b")
    early = await _seed_movement(mid=a, qty=5, date="2026-10-04T03:00:00Z")
    late = await _seed_movement(mid=a, qty=5, date="2026-10-06T03:00:00Z")
    other = await _seed_movement(mid=b, qty=5, date="2026-10-06T03:00:00Z")
    rows = {row["movement_id"]: row for row in await evaluate_inventory_issues(server.db)}
    assert rows[early["id"]]["status"] == STATUS_PENDING
    assert rows[late["id"]]["status"] == STATUS_WAITING
    assert rows[other["id"]]["status"] == STATUS_PENDING
    assert rows[other["id"]]["can_post"] is True


async def test_fifo_oldest_first_and_insufficient():
    mid, gr1, inv = await _seed_gr(qty=5, price=10, receipt_date="2026-10-02")
    await _seed_gr(qty=5, price=20, receipt_date="2026-10-03", mid=mid, inventory=inv)
    first = await _seed_movement(mid=mid, qty=6, date="2026-10-06T03:00:00Z")
    posted = await post_inventory_issue_accounting(server.db, first["id"], POST_USER)
    assert posted["status"] == STATUS_POSTED
    stored = await server.db.inventory_issue_accounting.find_one({"movement_id": first["id"]}, {"_id": 0})
    consumed = stored["accounting_posting_basis"]["consumed_acquisitions"]
    assert len(consumed) == 2
    assert Decimal(str(consumed[0]["base_qty_consumed"])) == Decimal("5")
    assert Decimal(str(consumed[0]["total_cost"])) == Decimal("50.00")
    assert Decimal(str(consumed[1]["base_qty_consumed"])) == Decimal("1")
    assert Decimal(str(consumed[1]["total_cost"])) == Decimal("20.00")
    assert Decimal(str(stored["accounting_posting_basis"]["total_cogs"])) == Decimal("70.00")
    overflow = await _seed_movement(mid=mid, qty=10, date="2026-10-07T03:00:00Z")
    blocked = await evaluate_inventory_issue(server.db, overflow["id"])
    assert blocked["status"] == STATUS_BLOCKED
    assert blocked["blocker_code"] == CODE_INSUFFICIENT


async def test_unposted_and_reversed_gr_not_consumable():
    mid = "m-live"
    await _configure_inventory()
    await _seed_master(mid)
    po_id = f"po-{uuid.uuid4().hex[:8]}"
    line_id = f"ln-{uuid.uuid4().hex[:8]}"
    await server.db.purchase_orders.insert_one({
        "id": po_id,
        "po_number": "PB-UNPOST",
        "order_date": "2026-09-01",
        "cancelled": False,
        "lines": [{"line_id": line_id, "ordered_qty": 10, "unit_price": 10, "unit": "PCS", "master_item_id": mid}],
    })
    await server.db.purchase_goods_receipts.insert_one({
        "id": "gr-unposted",
        "receipt_number": "GR-UNPOST",
        "purchase_order_id": po_id,
        "receipt_date": "2026-10-02",
        "cancelled": False,
        "lines": [{
            "line_id": "gl-1",
            "purchase_order_line_id": line_id,
            "received_qty": 10,
            "unit": "PCS",
            "master_item_id": mid,
            "unit_price": 10,
        }],
    })
    mov = await _seed_movement(mid=mid, qty=2)
    blocked = await evaluate_inventory_issue(server.db, mov["id"])
    assert blocked["blocker_code"] == CODE_INSUFFICIENT
    live_mid, gr, _ = await _seed_gr(qty=10, price=8)
    from services.purchase_goods_receipt_posting import reverse_purchase_goods_receipt_accounting
    await reverse_purchase_goods_receipt_accounting(
        server.db, gr["id"], POST_USER, "koreksi", serialize_in_process=False
    )
    after = await _seed_movement(mid=live_mid, qty=2, date="2026-10-08T03:00:00Z")
    blocked2 = await evaluate_inventory_issue(server.db, after["id"])
    assert blocked2["blocker_code"] == CODE_INSUFFICIENT


async def test_rata_rata_same_day_acquisition_before_issue_and_migration():
    inv_a, *_rest = await _configure_inventory(method=METHOD_AVG)
    mid, gr, _ = await _seed_gr(qty=10, price=10, receipt_date="2026-10-05", inventory=inv_a)
    mov = await _seed_movement(mid=mid, qty=4, date="2026-10-05T18:00:00Z")
    row = await evaluate_inventory_issue(server.db, mov["id"])
    assert row["status"] == STATUS_PENDING
    assert row["hpp_method"] == METHOD_AVG
    assert row["computed_cogs"] == 40.0
    other = "m-avg-mig"
    await _seed_master(other)
    inv_b = await _coa("13", "Persediaan B", "INTR")
    await server.db.app_settings.update_one(
        {"key": SETTINGS_KEY},
        {"$set": {"inventory_account_id": inv_a["id"]}},
    )
    await _seed_gr(qty=10, price=10, receipt_date="2026-10-05", mid=other, inventory=inv_a)
    await server.db.app_settings.update_one(
        {"key": SETTINGS_KEY},
        {"$set": {"inventory_account_id": inv_b["id"]}},
    )
    await _seed_gr(qty=10, price=20, receipt_date="2026-10-06", mid=other, inventory=inv_b)
    later = await _seed_movement(mid=other, qty=2, date="2026-10-07T03:00:00Z")
    blocked = await evaluate_inventory_issue(server.db, later["id"])
    assert blocked["blocker_code"] == "INVENTORY_ACCOUNT_MIGRATION_REQUIRED"


async def test_get_list_and_detail_are_zero_write():
    mid, _, _ = await _seed_gr(qty=20, price=5)
    mov = await _seed_movement(mid=mid, qty=3)
    _admin()
    before = {
        "acct": await server.db.inventory_issue_accounting.count_documents({}),
        "locks": await server.db.inventory_financial_item_locks.count_documents({}),
        "ctrl": await server.db.inventory_financial_control.count_documents({}),
        "journals": await server.db[JOURNAL_ENTRIES].count_documents({"source_type": SOURCE_TYPE}),
    }
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        listed = await client.get("/api/ledger/inventory-issues")
        detail = await client.get(f"/api/ledger/inventory-issues/{mov['id']}")
    assert listed.status_code == 200, listed.text
    assert detail.status_code == 200, detail.text
    body = detail.json()["item"]
    assert body["movement_id"] == mov["id"]
    assert body["status"] in {STATUS_PENDING, STATUS_BLOCKED, STATUS_WAITING, STATUS_EXCLUDED, STATUS_POSTED}
    assert set(public_issue_view(body).keys()) >= {
        "movement_id", "journal_date", "financial_inventory_item_key", "can_post", "status"
    }
    after = {
        "acct": await server.db.inventory_issue_accounting.count_documents({}),
        "locks": await server.db.inventory_financial_item_locks.count_documents({}),
        "ctrl": await server.db.inventory_financial_control.count_documents({}),
        "journals": await server.db[JOURNAL_ENTRIES].count_documents({"source_type": SOURCE_TYPE}),
    }
    assert after == before


async def test_fifo_future_gr_cannot_fund_earlier_issue():
    inventory, *_ = await _configure_inventory(method=METHOD_FIFO)
    mid = f"m-fifo-future-{uuid.uuid4().hex[:6]}"
    await _seed_master(mid)
    mov = await _seed_movement(mid=mid, qty=100, date="2026-10-05T03:00:00Z")
    await _seed_gr(qty=100, price=10, receipt_date="2026-10-10", mid=mid, inventory=inventory)
    row = await evaluate_inventory_issue(server.db, mov["id"])
    assert row["blocker_code"] == CODE_INSUFFICIENT
    assert row["can_post"] is False
    from fastapi import HTTPException
    try:
        await post_inventory_issue_accounting(server.db, mov["id"], POST_USER)
        raise AssertionError("future GR funded earlier FIFO issue")
    except HTTPException as exc:
        assert exc.status_code == 409
        assert (exc.detail or {}).get("code") == CODE_INSUFFICIENT
    assert await server.db[JOURNAL_ENTRIES].count_documents(
        {"source_type": SOURCE_TYPE, "source_id": mov["id"]}
    ) == 0


async def test_fifo_same_day_gr_may_fund_issue():
    inventory, *_ = await _configure_inventory(method=METHOD_FIFO)
    mid, _, _ = await _seed_gr(qty=100, price=10, receipt_date="2026-10-05", inventory=inventory)
    mov = await _seed_movement(mid=mid, qty=40, date="2026-10-05T03:00:00Z")
    row = await evaluate_inventory_issue(server.db, mov["id"])
    assert row["status"] == STATUS_PENDING
    assert row["can_post"] is True
    assert row["computed_cogs"] == 400.0
    posted = await post_inventory_issue_accounting(server.db, mov["id"], POST_USER)
    assert posted["status"] == STATUS_POSTED
    assert await server.db[JOURNAL_ENTRIES].count_documents(
        {"source_type": SOURCE_TYPE, "source_id": mov["id"], "reversal_of": None}
    ) == 1


async def test_fifo_future_gr_cannot_rescue_shortage():
    inventory, *_ = await _configure_inventory(method=METHOD_FIFO)
    mid, _, _ = await _seed_gr(qty=40, price=10, receipt_date="2026-10-04", inventory=inventory)
    await _seed_gr(qty=100, price=20, receipt_date="2026-10-10", mid=mid, inventory=inventory)
    mov = await _seed_movement(mid=mid, qty=80, date="2026-10-05T03:00:00Z")
    row = await evaluate_inventory_issue(server.db, mov["id"])
    assert row["blocker_code"] == CODE_INSUFFICIENT
    assert row["can_post"] is False


async def test_fifo_invalid_receipt_date_is_unavailable():
    layers = fifo_remaining_layers(
        [{
            "acquisition_key": "r-bad:l1",
            "receipt_id": "r-bad",
            "receipt_line_id": "l1",
            "inventory_account_id": "inv",
            "financial_inventory_item_key": "m:x",
            "base_qty": Decimal("100"),
            "total_cost": Decimal("1000.00"),
            "base_unit_cost": Decimal("10.00"),
            "receipt_date": "",
        }],
        "m:x",
        {},
        as_of_journal_date="2026-10-05",
    )
    assert layers == []
    consumed, err = compute_fifo_consumption(issue_base_qty=Decimal("10"), layers=layers)
    assert consumed is None
    assert err == CODE_INSUFFICIENT


async def test_rata_future_gr_does_not_change_earlier_average():
    inventory, *_ = await _configure_inventory(method=METHOD_AVG)
    mid, _, _ = await _seed_gr(qty=100, price=10, receipt_date="2026-10-01", inventory=inventory)
    await _seed_gr(qty=100, price=100, receipt_date="2026-10-10", mid=mid, inventory=inventory)
    mov = await _seed_movement(mid=mid, qty=50, date="2026-10-05T03:00:00Z")
    row = await evaluate_inventory_issue(server.db, mov["id"])
    assert row["status"] == STATUS_PENDING
    assert row["computed_cogs"] == 500.0
    posted = await post_inventory_issue_accounting(server.db, mov["id"], POST_USER)
    assert posted["status"] == STATUS_POSTED
    stored = await server.db.inventory_issue_accounting.find_one({"movement_id": mov["id"]}, {"_id": 0})
    assert Decimal(str(stored["accounting_posting_basis"]["total_cogs"])) == Decimal("500.00")
    assert Decimal(str(stored["accounting_posting_basis"]["average_cost"])) == Decimal("10.00")


async def test_rata_future_gr_cannot_rescue_shortage():
    inventory, *_ = await _configure_inventory(method=METHOD_AVG)
    mid, _, _ = await _seed_gr(qty=50, price=10, receipt_date="2026-10-01", inventory=inventory)
    await _seed_gr(qty=100, price=10, receipt_date="2026-10-10", mid=mid, inventory=inventory)
    mov = await _seed_movement(mid=mid, qty=100, date="2026-10-05T03:00:00Z")
    row = await evaluate_inventory_issue(server.db, mov["id"])
    assert row["blocker_code"] == CODE_INSUFFICIENT
    assert row["can_post"] is False


async def test_rata_same_day_acquisition_is_included():
    inventory, *_ = await _configure_inventory(method=METHOD_AVG)
    mid, _, _ = await _seed_gr(qty=80, price=12, receipt_date="2026-10-05", inventory=inventory)
    mov = await _seed_movement(mid=mid, qty=20, date="2026-10-05T10:00:00Z")
    row = await evaluate_inventory_issue(server.db, mov["id"])
    assert row["status"] == STATUS_PENDING
    assert row["can_post"] is True
    assert row["computed_cogs"] == 240.0


async def test_rata_later_posted_issue_excluded_from_earlier_replay():
    inventory, *_ = await _configure_inventory(method=METHOD_AVG)
    mid, _, _ = await _seed_gr(qty=100, price=10, receipt_date="2026-10-01", inventory=inventory)
    later = await _seed_movement(mid=mid, qty=50, date="2026-10-10T03:00:00Z")
    posted_later = await post_inventory_issue_accounting(server.db, later["id"], POST_USER)
    assert posted_later["status"] == STATUS_POSTED
    earlier = await _seed_movement(mid=mid, qty=80, date="2026-10-05T03:00:00Z")
    computed, err = compute_rata_rata(
        item_key=f"m:{mid}",
        issue_base_qty=Decimal("80"),
        issue_instant=datetime(2026, 10, 5, 3, tzinfo=timezone.utc),
        issue_id=earlier["id"],
        issue_journal_date="2026-10-05",
        acquisitions=[{
            "financial_inventory_item_key": f"m:{mid}",
            "receipt_date": "2026-10-01",
            "receipt_id": "r1",
            "receipt_line_id": "l1",
            "inventory_account_id": "inv",
            "base_qty": Decimal("100"),
            "total_cost": Decimal("1000.00"),
        }],
        live_issue_bases=[{
            "movement_id": later["id"],
            "financial_inventory_item_key": f"m:{mid}",
            "journal_date": "2026-10-10",
            "basis": {
                "journal_date": "2026-10-10",
                "movement_timestamp": later["date"],
                "issue_base_qty": 50,
                "total_cogs": 500,
                "inventory_account_id": "inv",
            },
        }],
    )
    assert err is None
    assert computed["total_cogs"] == Decimal("800.00")
    assert computed["qty_before"] == Decimal("100")
    row = await evaluate_inventory_issue(server.db, earlier["id"])
    assert row["status"] == STATUS_BLOCKED
    assert row["can_post"] is False
    assert row["blocker_code"] == CODE_CHRONOLOGY_CONFLICT
    assert row["blocker_message"] == MSG_CHRONOLOGY_CONFLICT
    later_journal = await server.db[JOURNAL_ENTRIES].find_one(
        {"source_type": SOURCE_TYPE, "source_id": later["id"], "reversal_of": None},
        {"_id": 0},
    )
    assert later_journal and later_journal["status"] == STATUS_POSTED
    later_row = await evaluate_inventory_issue(server.db, later["id"])
    assert later_row["status"] == STATUS_POSTED


async def test_fifo_later_posted_blocks_backdated_earlier_issue():
    inventory, *_ = await _configure_inventory(method=METHOD_FIFO)
    mid, _, _ = await _seed_gr(qty=200, price=10, receipt_date="2026-10-01", inventory=inventory)
    later = await _seed_movement(mid=mid, qty=40, date="2026-10-10T03:00:00Z")
    posted = await post_inventory_issue_accounting(server.db, later["id"], POST_USER)
    assert posted["status"] == STATUS_POSTED
    earlier = await _seed_movement(mid=mid, qty=30, date="2026-10-05T03:00:00Z")
    row = await evaluate_inventory_issue(server.db, earlier["id"])
    assert row["status"] == STATUS_BLOCKED
    assert row["can_post"] is False
    assert row["blocker_code"] == CODE_CHRONOLOGY_CONFLICT
    assert row["blocker_message"] == MSG_CHRONOLOGY_CONFLICT
    from fastapi import HTTPException
    try:
        await post_inventory_issue_accounting(server.db, earlier["id"], POST_USER)
        raise AssertionError("backdated FIFO issue posted after later journal")
    except HTTPException as exc:
        assert exc.status_code == 409
        assert (exc.detail or {}).get("code") == CODE_CHRONOLOGY_CONFLICT
    assert await server.db[JOURNAL_ENTRIES].count_documents(
        {"source_type": SOURCE_TYPE, "source_id": earlier["id"]}
    ) == 0
    later_row = await evaluate_inventory_issue(server.db, later["id"])
    assert later_row["status"] == STATUS_POSTED


async def test_fifo_later_posted_blocks_even_when_qty_remains():
    inventory, *_ = await _configure_inventory(method=METHOD_FIFO)
    mid, _, _ = await _seed_gr(qty=50, price=10, receipt_date="2026-10-01", inventory=inventory)
    await _seed_gr(qty=100, price=20, receipt_date="2026-10-02", mid=mid, inventory=inventory)
    later = await _seed_movement(mid=mid, qty=80, date="2026-10-10T03:00:00Z")
    posted = await post_inventory_issue_accounting(server.db, later["id"], POST_USER)
    assert posted["status"] == STATUS_POSTED
    stored = await server.db.inventory_issue_accounting.find_one({"movement_id": later["id"]}, {"_id": 0})
    consumed = stored["accounting_posting_basis"]["consumed_acquisitions"]
    assert len(consumed) == 2
    earlier = await _seed_movement(mid=mid, qty=30, date="2026-10-05T03:00:00Z")
    row = await evaluate_inventory_issue(server.db, earlier["id"])
    assert row["blocker_code"] == CODE_CHRONOLOGY_CONFLICT
    assert row["can_post"] is False


async def test_fifo_same_day_later_timestamp_posted_blocks_earlier_timestamp():
    inventory, *_ = await _configure_inventory(method=METHOD_FIFO)
    mid, _, _ = await _seed_gr(qty=200, price=10, receipt_date="2026-10-01", inventory=inventory)
    later = await _seed_movement(mid=mid, qty=20, date="2026-10-05T03:00:01Z")
    posted = await post_inventory_issue_accounting(server.db, later["id"], POST_USER)
    assert posted["status"] == STATUS_POSTED
    earlier = await _seed_movement(mid=mid, qty=20, date="2026-10-05T03:00:00Z")
    row = await evaluate_inventory_issue(server.db, earlier["id"])
    assert row["blocker_code"] == CODE_CHRONOLOGY_CONFLICT
    assert row["status"] == STATUS_BLOCKED


async def test_fifo_same_timestamp_later_movement_id_posted_blocks_earlier_id():
    inventory, *_ = await _configure_inventory(method=METHOD_FIFO)
    mid, _, _ = await _seed_gr(qty=200, price=10, receipt_date="2026-10-01", inventory=inventory)
    later = await _seed_movement(mid=mid, qty=20, date="2026-10-05T03:00:00Z", movement_id="issue-zzz")
    posted = await post_inventory_issue_accounting(server.db, later["id"], POST_USER)
    assert posted["status"] == STATUS_POSTED
    earlier = await _seed_movement(mid=mid, qty=20, date="2026-10-05T03:00:00Z", movement_id="issue-aaa")
    row = await evaluate_inventory_issue(server.db, earlier["id"])
    assert row["blocker_code"] == CODE_CHRONOLOGY_CONFLICT
    assert row["can_post"] is False


async def test_rata_later_posted_blocks_backdated_earlier_issue():
    inventory, *_ = await _configure_inventory(method=METHOD_AVG)
    mid, _, _ = await _seed_gr(qty=200, price=10, receipt_date="2026-10-01", inventory=inventory)
    later = await _seed_movement(mid=mid, qty=40, date="2026-10-10T03:00:00Z")
    posted = await post_inventory_issue_accounting(server.db, later["id"], POST_USER)
    assert posted["status"] == STATUS_POSTED
    earlier = await _seed_movement(mid=mid, qty=30, date="2026-10-05T03:00:00Z")
    row = await evaluate_inventory_issue(server.db, earlier["id"])
    assert row["status"] == STATUS_BLOCKED
    assert row["can_post"] is False
    assert row["blocker_code"] == CODE_CHRONOLOGY_CONFLICT
    later_row = await evaluate_inventory_issue(server.db, later["id"])
    assert later_row["status"] == STATUS_POSTED
