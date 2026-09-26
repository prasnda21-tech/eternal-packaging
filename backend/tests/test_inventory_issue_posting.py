"""Phase 4B.3 financial inventory-issue posting — journals, locks, crash, races."""
from __future__ import annotations

import asyncio
import os
import sys
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import httpx
import pytest
from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ.setdefault("MONGO_URL", "mongodb://127.0.0.1:27017")
_worker = os.environ.get("PYTEST_XDIST_WORKER") or "main"
os.environ["DB_NAME"] = f"eternal_packaging_inv_issue_post_test_{_worker}"
os.environ.setdefault("JWT_SECRET", "test-secret-key-12345678901234567890")
os.environ.setdefault("CORS_ORIGINS", "*")

import server
from server import app, current_user
from services.accounting_periods import close_period, ensure_indexes as ensure_periods
from services.chart_of_accounts import create_account, ensure_indexes as ensure_coa
from services.inventory_financial_locks import (
    acquire_item_lock,
    acquire_item_locks,
    release_item_lock,
    renew_item_lock,
    renew_item_locks,
)
from services.inventory_issue_accounting import (
    CLAIM_AT_FIELD,
    CLAIM_FIELD,
    COLLECTION,
    CONTROL_COLLECTION,
    CONTROL_KEY,
    POSTING_BASIS_FIELD,
    SOURCE_TYPE,
    STATUS_POSTED,
    ensure_indexes,
    evaluate_inventory_issue,
    find_inventory_issue_source_journal,
)
from services.inventory_issue_posting import (
    acquire_posting_claim,
    clear_posting_claim,
    establish_cogs_policy,
    persist_accounting_posting_basis,
    post_inventory_issue_accounting,
    require_held_item_lock,
)
from services.journal_core import JOURNAL_ENTRIES, JOURNAL_LINES, ensure_indexes as ensure_journal
from services.purchase_accounting_settings import SETTINGS_KEY
from services.purchase_goods_receipt_posting import (
    post_purchase_goods_receipt_accounting,
    reverse_purchase_goods_receipt_accounting,
)

POST_USER = {
    "id": "admin-cogs-post",
    "email": "admin@eternalpackaging.com",
    "name": "Admin COGS Post",
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


async def _seed_posted_gr(*, qty=100, price=10, receipt_date="2026-10-02", mid=None, inventory=None):
    if inventory is None:
        inventory, *_rest = await _configure_inventory()
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
    assert isinstance(posted, dict), getattr(posted, "detail", posted)
    return mid, gr, inventory, posted


async def _seed_movement(*, mid, qty=10, date="2026-10-05T03:00:00Z", movement_id=None, **extra):
    mov_id = movement_id or str(uuid.uuid4())
    doc = {
        "id": mov_id,
        "movement_type": extra.get("movement_type", "PENGIRIMAN"),
        "master_item_id": mid,
        "date": date,
        "created_at": extra.get("created_at", date),
        "transaction_qty": qty,
        "transaction_uom": extra.get("tx_uom", "PCS"),
        "conversion_factor": extra.get("factor", 1),
        "base_qty": extra.get("base_qty", qty),
        "base_uom": extra.get("base_uom", "PCS"),
        "reference_type": "sales_delivery",
        "reference_id": f"ref-{mov_id[:8]}",
        "reference_number": f"SJ-{mov_id[:6]}",
        "order_id": f"so-{mov_id[:6]}",
        "line_id": f"ln-{mov_id[:6]}",
        "status": "POSTED",
    }
    await server.db.stock_movements.insert_one(doc)
    doc.pop("_id", None)
    return doc


async def _unlocked_post(movement_id: str):
    try:
        return await post_inventory_issue_accounting(server.db, movement_id, POST_USER)
    except HTTPException as exc:
        return exc


def _describe_post_result(movement_id: str, result) -> str:
    """Surface the rejection. Success criteria stay in the assertions that call this."""
    if isinstance(result, HTTPException):
        return (
            f"movement_id={movement_id} status_code={result.status_code} "
            f"detail={result.detail!r}"
        )
    journal_id = (result or {}).get("accounting_journal_id") if isinstance(result, dict) else None
    return f"movement_id={movement_id} status_code=200 detail=posted journal_id={journal_id}"


async def _unlocked_reverse(receipt_id: str, reason: str = "koreksi"):
    try:
        return await reverse_purchase_goods_receipt_accounting(
            server.db, receipt_id, POST_USER, reason, serialize_in_process=False
        )
    except HTTPException as exc:
        return exc


async def _source_journals(movement_id: str):
    return await server.db[JOURNAL_ENTRIES].find(
        {"source_type": SOURCE_TYPE, "source_id": movement_id, "reversal_of": None},
        {"_id": 0},
    ).to_list(20)


async def _journal_lines(journal_id: str):
    return await server.db[JOURNAL_LINES].find({"journal_id": journal_id}, {"_id": 0}).to_list(20)


@pytest.mark.anyio
async def test_valid_pengiriman_posts_dr_cogs_cr_inventory():
    inventory, _clearing, _payable, cogs = await _configure_inventory()
    mid, gr, _, _ = await _seed_posted_gr(qty=40, price=25, inventory=inventory)
    mov = await _seed_movement(mid=mid, qty=8)
    _admin()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        res = await client.post(f"/api/ledger/inventory-issues/{mov['id']}/post")
    assert res.status_code == 200, res.text
    body = res.json()["item"]
    assert body["status"] == STATUS_POSTED
    assert body["accounting_journal_id"]
    journals = await _source_journals(mov["id"])
    assert len(journals) == 1
    assert journals[0]["source_type"] == SOURCE_TYPE
    assert journals[0]["source_id"] == mov["id"]
    assert journals[0]["reversal_of"] is None
    assert journals[0]["status"] == "posted"
    lines = await _journal_lines(journals[0]["id"])
    by_acc = {ln["account_id"]: ln for ln in lines}
    assert Decimal(str(by_acc[cogs["id"]]["debit"])) == Decimal("200.00")
    assert Decimal(str(by_acc[inventory["id"]]["credit"])) == Decimal("200.00")
    assert Decimal(str(by_acc[cogs["id"]]["debit"])) == Decimal(str(by_acc[inventory["id"]]["credit"]))


@pytest.mark.anyio
async def test_no_pengiriman_does_not_post():
    inventory, *_ = await _configure_inventory()
    mid, _, _, _ = await _seed_posted_gr(qty=10, price=10, inventory=inventory)
    mov = await _seed_movement(mid=mid, qty=2, movement_type="PENERIMAAN")
    result = await _unlocked_post(mov["id"])
    assert isinstance(result, HTTPException)
    assert await _source_journals(mov["id"]) == []


@pytest.mark.anyio
async def test_fifo_multiple_inventory_accounts_and_dr_equals_cr():
    inv_a, *_rest = await _configure_inventory()
    mid, _gr1, _, _ = await _seed_posted_gr(qty=5, price=10, receipt_date="2026-10-02", inventory=inv_a)
    inv_b = await _coa("13", "Persediaan B", "INTR")
    await server.db.app_settings.update_one({"key": SETTINGS_KEY}, {"$set": {"inventory_account_id": inv_b["id"]}})
    await _seed_posted_gr(qty=5, price=20, receipt_date="2026-10-03", mid=mid, inventory=inv_b)
    mov = await _seed_movement(mid=mid, qty=8)
    posted = await _unlocked_post(mov["id"])
    assert not isinstance(posted, HTTPException), getattr(posted, "detail", posted)
    journal = (await _source_journals(mov["id"]))[0]
    lines = await _journal_lines(journal["id"])
    debit = sum(Decimal(str(ln["debit"])) for ln in lines)
    credit = sum(Decimal(str(ln["credit"])) for ln in lines)
    assert debit == credit
    credits = {ln["account_id"]: Decimal(str(ln["credit"])) for ln in lines if Decimal(str(ln["credit"])) > 0}
    assert credits[inv_a["id"]] == Decimal("50.00")
    assert credits[inv_b["id"]] == Decimal("60.00")
    stored = await server.db[COLLECTION].find_one({"movement_id": mov["id"]}, {"_id": 0})
    basis = stored[POSTING_BASIS_FIELD]
    assert Decimal(str(sum(Decimal(str(x["total_cost"])) for x in basis["consumed_acquisitions"]))) == Decimal(str(basis["total_cogs"]))


@pytest.mark.anyio
async def test_fifo_exact_final_residual_rounding():
    inventory, *_ = await _configure_inventory()
    mid, _, _, _ = await _seed_posted_gr(qty=3, price=3.333333333, inventory=inventory)
    first = await _seed_movement(mid=mid, qty=1, date="2026-10-05T03:00:00Z")
    second = await _seed_movement(mid=mid, qty=1, date="2026-10-06T03:00:00Z")
    third = await _seed_movement(mid=mid, qty=1, date="2026-10-07T03:00:00Z")
    a = await _unlocked_post(first["id"])
    b = await _unlocked_post(second["id"])
    c = await _unlocked_post(third["id"])
    assert all(not isinstance(x, HTTPException) for x in (a, b, c))
    totals = []
    for mov in (first, second, third):
        stored = await server.db[COLLECTION].find_one({"movement_id": mov["id"]}, {"_id": 0})
        totals.append(Decimal(str(stored[POSTING_BASIS_FIELD]["total_cogs"])))
    assert sum(totals) == Decimal("10.00")
    assert totals[2] == Decimal("10.00") - totals[0] - totals[1]


@pytest.mark.anyio
async def test_fifo_insufficient_creates_no_partial_journal():
    inventory, *_ = await _configure_inventory()
    mid, _, _, _ = await _seed_posted_gr(qty=5, price=10, inventory=inventory)
    mov = await _seed_movement(mid=mid, qty=9)
    result = await _unlocked_post(mov["id"])
    assert isinstance(result, HTTPException)
    assert result.status_code == 409
    assert (result.detail or {}).get("code") == "INSUFFICIENT_FINANCIAL_INVENTORY"
    assert await _source_journals(mov["id"]) == []
    assert not await server.db[COLLECTION].find_one({"movement_id": mov["id"], "status": STATUS_POSTED})


@pytest.mark.anyio
async def test_rata_rata_partial_and_full_depletion():
    inventory, *_ = await _configure_inventory(method="RATA-RATA")
    mid, _, _, _ = await _seed_posted_gr(qty=10, price=10, inventory=inventory)
    first = await _seed_movement(mid=mid, qty=4, date="2026-10-05T03:00:00Z")
    posted = await _unlocked_post(first["id"])
    assert not isinstance(posted, HTTPException), getattr(posted, "detail", posted)
    stored = await server.db[COLLECTION].find_one({"movement_id": first["id"]}, {"_id": 0})
    basis = stored[POSTING_BASIS_FIELD]
    assert basis["hpp_method"] == "RATA-RATA"
    assert Decimal(str(basis["total_cogs"])) == Decimal("40.00")
    assert Decimal(str(basis["qty_after"])) == Decimal("6")
    rest = await _seed_movement(mid=mid, qty=6, date="2026-10-06T03:00:00Z")
    full = await _unlocked_post(rest["id"])
    assert not isinstance(full, HTTPException), getattr(full, "detail", full)
    stored2 = await server.db[COLLECTION].find_one({"movement_id": rest["id"]}, {"_id": 0})
    basis2 = stored2[POSTING_BASIS_FIELD]
    assert Decimal(str(basis2["total_cogs"])) == Decimal("60.00")
    assert Decimal(str(basis2["qty_after"])) == Decimal("0")
    assert Decimal(str(basis2["inventory_value_after"])) == Decimal("0")


@pytest.mark.anyio
async def test_method_cas_same_and_different_and_later_mismatch():
    inventory, *_ = await _configure_inventory(method="FIFO")
    a_mid, _, _, _ = await _seed_posted_gr(qty=20, price=10, inventory=inventory)
    b_mid, _, _, _ = await _seed_posted_gr(qty=20, price=10, inventory=inventory)
    a = await _seed_movement(mid=a_mid, qty=2, date="2026-10-05T03:00:00Z")
    b = await _seed_movement(mid=b_mid, qty=2, date="2026-10-05T04:00:00Z")
    firsts = await asyncio.gather(_unlocked_post(a["id"]), _unlocked_post(b["id"]))
    assert all(not isinstance(x, HTTPException) for x in firsts)
    policy = await server.db[CONTROL_COLLECTION].find_one({"key": CONTROL_KEY}, {"_id": 0})
    assert policy["costing_method"] == "FIFO"
    await server.db.app_settings.update_one({"key": "hpp_valuation"}, {"$set": {"method": "RATA-RATA"}})
    later = await _seed_movement(mid=a_mid, qty=2, date="2026-10-08T03:00:00Z")
    blocked = await _unlocked_post(later["id"])
    assert isinstance(blocked, HTTPException)
    assert (blocked.detail or {}).get("code") == "FINANCIAL_COGS_METHOD_MISMATCH"

    await server.db.inventory_financial_control.delete_many({})
    results = await asyncio.gather(
        establish_cogs_policy(server.db, "FIFO", POST_USER),
        establish_cogs_policy(server.db, "RATA-RATA", POST_USER),
        return_exceptions=True,
    )
    wins = [r for r in results if isinstance(r, str)]
    losses = [r for r in results if not isinstance(r, str)]
    assert len(wins) == 1
    assert len(losses) == 1
    frozen = await server.db[CONTROL_COLLECTION].find_one({"key": CONTROL_KEY}, {"_id": 0})
    assert frozen["costing_method"] in {"FIFO", "RATA-RATA"}


@pytest.mark.anyio
async def test_same_movement_double_post_one_journal():
    inventory, *_ = await _configure_inventory()
    mid, _, _, _ = await _seed_posted_gr(qty=20, price=10, inventory=inventory)
    mov = await _seed_movement(mid=mid, qty=3)
    first, second = await asyncio.gather(_unlocked_post(mov["id"]), _unlocked_post(mov["id"]))
    ok = [x for x in (first, second) if not isinstance(x, HTTPException)]
    assert ok
    journals = await _source_journals(mov["id"])
    assert len(journals) == 1
    retry = await _unlocked_post(mov["id"])
    assert not isinstance(retry, HTTPException)
    assert retry["accounting_journal_id"] == journals[0]["id"]
    assert len(await _source_journals(mov["id"])) == 1


@pytest.mark.anyio
async def test_same_item_serializes_and_overdraw_cannot_both_succeed(monkeypatch):
    """Item lock is one Mongo lease. A later issue that wins it is rejected
    while an earlier issue is still open, and the earlier call then loses the
    lease. That pair is two 409s; the caller retries the earlier issue once.
    """
    inventory, _clearing, _payable, cogs = await _configure_inventory()
    mid, _, _, _ = await _seed_posted_gr(qty=1000, price=1, inventory=inventory)
    earlier = await _seed_movement(mid=mid, qty=700, date="2026-10-05T03:00:00Z")
    later = await _seed_movement(mid=mid, qty=600, date="2026-10-05T03:00:01Z")

    import services.inventory_issue_posting as issue_posting

    real_acquire = issue_posting.acquire_item_lock
    later_holds = asyncio.Event()
    earlier_attempted = asyncio.Event()

    async def _later_wins_item_lock(db, key, holder, *args, **kwargs):
        holder_id = str(holder)
        if holder_id == later["id"]:
            lock = await real_acquire(db, key, holder, *args, **kwargs)
            later_holds.set()
            await asyncio.wait_for(earlier_attempted.wait(), timeout=5)
            return lock
        if holder_id == earlier["id"]:
            await asyncio.wait_for(later_holds.wait(), timeout=5)
            result = await real_acquire(db, key, holder, *args, **kwargs)
            earlier_attempted.set()
            return result
        return await real_acquire(db, key, holder, *args, **kwargs)

    monkeypatch.setattr(issue_posting, "acquire_item_lock", _later_wins_item_lock)
    first, second = await asyncio.gather(
        _unlocked_post(earlier["id"]),
        _unlocked_post(later["id"]),
    )
    observed = (
        f"earlier[{_describe_post_result(earlier['id'], first)}]; "
        f"later[{_describe_post_result(later['id'], second)}]"
    )
    assert isinstance(first, HTTPException), observed
    assert first.status_code == 409, observed
    assert (first.detail or {}).get("code") == "FINANCIAL_ITEM_LOCK_CONFLICT", observed
    assert isinstance(second, HTTPException), observed
    assert second.status_code == 409, observed
    assert (second.detail or {}).get("code") == "WAITING_EARLIER_ISSUE", observed
    assert await _source_journals(earlier["id"]) == [], observed
    assert await _source_journals(later["id"]) == [], observed

    monkeypatch.setattr(issue_posting, "acquire_item_lock", real_acquire)
    posted = await _unlocked_post(earlier["id"])
    assert not isinstance(posted, HTTPException), _describe_post_result(earlier["id"], posted)
    journal_id = posted["accounting_journal_id"]
    assert journal_id, _describe_post_result(earlier["id"], posted)
    again = await _unlocked_post(earlier["id"])
    assert not isinstance(again, HTTPException), _describe_post_result(earlier["id"], again)
    assert again["accounting_journal_id"] == journal_id
    earlier_journals = await _source_journals(earlier["id"])
    assert len(earlier_journals) == 1
    assert earlier_journals[0]["id"] == journal_id
    lines = await _journal_lines(journal_id)
    debits = sum(Decimal(str(ln["debit"])) for ln in lines)
    credits = sum(Decimal(str(ln["credit"])) for ln in lines)
    assert debits == credits == Decimal("700.00")
    by_acc = {ln["account_id"]: ln for ln in lines}
    assert Decimal(str(by_acc[cogs["id"]]["debit"])) == Decimal("700.00")

    overdraw = await _unlocked_post(later["id"])
    assert isinstance(overdraw, HTTPException), _describe_post_result(later["id"], overdraw)
    assert overdraw.status_code == 409, _describe_post_result(later["id"], overdraw)
    assert (overdraw.detail or {}).get("code") == "INSUFFICIENT_FINANCIAL_INVENTORY", (
        _describe_post_result(later["id"], overdraw)
    )
    assert await _source_journals(later["id"]) == []

    stored = await server.db[COLLECTION].find_one({"movement_id": earlier["id"]}, {"_id": 0})
    basis = stored[POSTING_BASIS_FIELD]
    consumed_qty = sum(
        Decimal(str(row["base_qty_consumed"])) for row in basis["consumed_acquisitions"]
    )
    opening_qty = Decimal("1000")
    remaining_qty = opening_qty - consumed_qty
    assert Decimal(str(basis["base_qty"])) == Decimal("700")
    assert consumed_qty == Decimal("700")
    assert Decimal(str(basis["total_cogs"])) == Decimal("700.00")
    assert remaining_qty == Decimal("300")
    assert remaining_qty >= 0

    older = await _seed_movement(mid=mid, qty=50, date="2026-10-04T03:00:00Z")
    blocked = await _unlocked_post(older["id"])
    assert isinstance(blocked, HTTPException), _describe_post_result(older["id"], blocked)
    assert blocked.status_code == 409, _describe_post_result(older["id"], blocked)
    assert (blocked.detail or {}).get("code") == "FINANCIAL_CHRONOLOGY_CONFLICT", (
        _describe_post_result(older["id"], blocked)
    )
    assert await _source_journals(older["id"]) == []
    assert len(await _source_journals(earlier["id"])) == 1
    assert await _source_journals(later["id"]) == []


@pytest.mark.anyio
async def test_different_items_post_independently():
    inventory, *_ = await _configure_inventory()
    a_mid, _, _, _ = await _seed_posted_gr(qty=10, price=10, inventory=inventory)
    b_mid, _, _, _ = await _seed_posted_gr(qty=10, price=10, inventory=inventory)
    a = await _seed_movement(mid=a_mid, qty=3)
    b = await _seed_movement(mid=b_mid, qty=3)
    first, second = await asyncio.gather(_unlocked_post(a["id"]), _unlocked_post(b["id"]))
    assert not isinstance(first, HTTPException), getattr(first, "detail", first)
    assert not isinstance(second, HTTPException), getattr(second, "detail", second)


@pytest.mark.anyio
async def test_stale_lock_and_claim_recovery_and_token_scoped_release():
    inventory, *_ = await _configure_inventory()
    mid, _, _, _ = await _seed_posted_gr(qty=10, price=10, inventory=inventory)
    mov = await _seed_movement(mid=mid, qty=2)
    item_key = f"m:{mid}"
    stale = (datetime.now(timezone.utc) - timedelta(seconds=601)).isoformat()
    await server.db.inventory_financial_item_locks.insert_one({
        "financial_inventory_item_key": item_key,
        "token": "old-lock",
        "holder_movement_id": "other",
        "claimed_at": stale,
        "expires_at": stale,
    })
    await server.db[COLLECTION].insert_one({
        "movement_id": mov["id"],
        CLAIM_FIELD: "old-claim",
        CLAIM_AT_FIELD: stale,
        "created_at": stale,
        "updated_at": stale,
    })
    posted = await _unlocked_post(mov["id"])
    assert not isinstance(posted, HTTPException), getattr(posted, "detail", posted)
    lock = await acquire_item_lock(server.db, item_key, "holder-1")
    assert lock
    assert await release_item_lock(server.db, item_key, "wrong-token") is False
    still = await server.db.inventory_financial_item_locks.find_one({"financial_inventory_item_key": item_key})
    assert still and still["token"] == lock["token"]
    assert await release_item_lock(server.db, item_key, lock["token"]) is True
    gone = await server.db.inventory_financial_item_locks.find_one({"financial_inventory_item_key": item_key})
    assert gone is None


@pytest.mark.anyio
async def test_crash_before_basis_after_basis_after_journal_and_period_heal():
    inventory, *_ = await _configure_inventory()
    mid, _, _, _ = await _seed_posted_gr(qty=20, price=10, inventory=inventory)
    mov = await _seed_movement(mid=mid, qty=4)
    claimed = await acquire_posting_claim(server.db, mov["id"])
    assert claimed
    stale = (datetime.now(timezone.utc) - timedelta(seconds=601)).isoformat()
    await server.db[COLLECTION].update_one(
        {"movement_id": mov["id"]},
        {"$set": {CLAIM_AT_FIELD: stale}},
    )
    retry = await _unlocked_post(mov["id"])
    assert not isinstance(retry, HTTPException), getattr(retry, "detail", retry)
    assert len(await _source_journals(mov["id"])) == 1

    mov2 = await _seed_movement(mid=mid, qty=3, date="2026-10-06T03:00:00Z")
    claim2 = await acquire_posting_claim(server.db, mov2["id"])
    from services.inventory_issue_accounting import build_cogs_posting_basis
    from services.inventory_issue_posting import persist_accounting_posting_basis, _reload_computed
    from services.purchase_accounting_settings import get_purchase_accounting_settings
    ev = await evaluate_inventory_issue(server.db, mov2["id"])
    computed = await _reload_computed(server.db, mov2["id"])
    settings = await get_purchase_accounting_settings(server.db)
    movement = await server.db.stock_movements.find_one({"id": mov2["id"]}, {"_id": 0})
    basis = build_cogs_posting_basis(
        movement=movement,
        evaluation=ev,
        computed=computed,
        settings=settings,
        user=POST_USER,
        claim_token=claim2[CLAIM_FIELD],
    )
    assert await persist_accounting_posting_basis(server.db, mov2["id"], claim2[CLAIM_FIELD], basis)
    await server.db[COLLECTION].update_one(
        {"movement_id": mov2["id"]},
        {"$set": {CLAIM_AT_FIELD: stale}},
    )
    again = await _unlocked_post(mov2["id"])
    assert not isinstance(again, HTTPException), getattr(again, "detail", again)
    assert len(await _source_journals(mov2["id"])) == 1

    mov3 = await _seed_movement(mid=mid, qty=2, date="2026-10-07T03:00:00Z")
    posted3 = await _unlocked_post(mov3["id"])
    assert not isinstance(posted3, HTTPException)
    jid = posted3["accounting_journal_id"]
    await server.db[COLLECTION].update_one(
        {"movement_id": mov3["id"]},
        {"$unset": {"accounting_journal_id": "", "posted_at": "", "status": ""}},
    )
    healed = await _unlocked_post(mov3["id"])
    assert not isinstance(healed, HTTPException)
    assert healed["accounting_journal_id"] == jid
    assert len(await _source_journals(mov3["id"])) == 1

    await close_period(server.db, "2026-10", user=POST_USER, reason="tutup")
    after_close = await _unlocked_post(mov3["id"])
    assert not isinstance(after_close, HTTPException), getattr(after_close, "detail", after_close)
    assert after_close["accounting_journal_id"] == jid
    blocked_new = await _seed_movement(mid=mid, qty=1, date="2026-10-08T03:00:00Z")
    closed = await _unlocked_post(blocked_new["id"])
    assert isinstance(closed, HTTPException)
    assert closed.status_code == 409


@pytest.mark.anyio
async def test_gr_fifo_consumption_guard_and_race():
    inventory, *_ = await _configure_inventory(method="FIFO")
    mid, gr, _, _ = await _seed_posted_gr(qty=10, price=10, inventory=inventory)
    unused = await _unlocked_reverse(gr["id"], "belum terpakai")
    assert not isinstance(unused, HTTPException), getattr(unused, "detail", unused)

    mid2, gr2, _, _ = await _seed_posted_gr(qty=10, price=10, inventory=inventory)
    mov = await _seed_movement(mid=mid2, qty=4)
    posted = await _unlocked_post(mov["id"])
    assert not isinstance(posted, HTTPException), getattr(posted, "detail", posted)
    blocked = await _unlocked_reverse(gr2["id"], "sudah terpakai")
    assert isinstance(blocked, HTTPException)
    assert blocked.status_code == 409
    assert (blocked.detail or {}).get("code") == "FINANCIAL_INVENTORY_ALREADY_CONSUMED"
    full_mov = await _seed_movement(mid=mid2, qty=6, date="2026-10-08T03:00:00Z")
    full = await _unlocked_post(full_mov["id"])
    assert not isinstance(full, HTTPException)
    blocked_full = await _unlocked_reverse(gr2["id"], "full consume")
    assert isinstance(blocked_full, HTTPException)

    mid3, gr3, _, _ = await _seed_posted_gr(qty=10, price=10, inventory=inventory)
    race_mov = await _seed_movement(mid=mid3, qty=5, date="2026-10-09T03:00:00Z")

    async def _rev():
        return await _unlocked_reverse(gr3["id"], "race")

    cogs_res, gr_res = await asyncio.gather(_unlocked_post(race_mov["id"]), _rev())
    cogs_ok = not isinstance(cogs_res, HTTPException)
    gr_ok = not isinstance(gr_res, HTTPException)
    assert not (cogs_ok and gr_ok)
    if cogs_ok:
        journals = await _source_journals(race_mov["id"])
        assert journals
        still = await find_inventory_issue_source_journal(server.db, race_mov["id"])
        assert still
    if gr_ok:
        assert await _source_journals(race_mov["id"]) == []


@pytest.mark.anyio
async def test_gr_rata_rata_same_later_block_earlier_ok():
    inventory, *_ = await _configure_inventory(method="RATA-RATA")
    mid, gr_late, _, _ = await _seed_posted_gr(qty=10, price=10, receipt_date="2026-10-10", inventory=inventory)
    early_gr_mid, _gr_early, _, _ = await _seed_posted_gr(
        qty=10, price=10, receipt_date="2026-10-01", mid=mid, inventory=inventory
    )
    early_mov = await _seed_movement(mid=mid, qty=2, date="2026-10-02T03:00:00Z")
    posted = await _unlocked_post(early_mov["id"])
    assert not isinstance(posted, HTTPException), getattr(posted, "detail", posted)
    allowed = await _unlocked_reverse(gr_late["id"], "gr kemudian")
    assert not isinstance(allowed, HTTPException), getattr(allowed, "detail", allowed)

    mid2, gr_same, _, _ = await _seed_posted_gr(qty=10, price=10, receipt_date="2026-10-05", inventory=inventory)
    same_day = await _seed_movement(mid=mid2, qty=2, date="2026-10-05T18:00:00Z")
    posted_same = await _unlocked_post(same_day["id"])
    assert not isinstance(posted_same, HTTPException), getattr(posted_same, "detail", posted_same)
    blocked_same = await _unlocked_reverse(gr_same["id"], "same day")
    assert isinstance(blocked_same, HTTPException)
    assert (blocked_same.detail or {}).get("code") == "FINANCIAL_INVENTORY_ALREADY_CONSUMED"

    mid3, gr_prior, _, _ = await _seed_posted_gr(qty=10, price=10, receipt_date="2026-10-04", inventory=inventory)
    later_cogs = await _seed_movement(mid=mid3, qty=2, date="2026-10-06T03:00:00Z")
    posted_later = await _unlocked_post(later_cogs["id"])
    assert not isinstance(posted_later, HTTPException)
    blocked_later = await _unlocked_reverse(gr_prior["id"], "later cogs")
    assert isinstance(blocked_later, HTTPException)


async def test_stale_item_lock_token_cannot_commit_or_release_successor():
    inventory, *_ = await _configure_inventory()
    mid, _, _, _ = await _seed_posted_gr(qty=1000, price=1, inventory=inventory)
    mov = await _seed_movement(mid=mid, qty=700)
    item_key = f"m:{mid}"
    lock_a = await acquire_item_lock(server.db, item_key, mov["id"])
    assert lock_a
    future = (datetime.now(timezone.utc) + timedelta(seconds=600)).isoformat()
    await server.db.inventory_financial_item_locks.update_one(
        {"financial_inventory_item_key": item_key},
        {"$set": {"token": "token-b", "holder_movement_id": "other-issue", "expires_at": future}},
    )
    from services.inventory_issue_accounting import InventoryIssueError

    with pytest.raises(InventoryIssueError) as exc:
        await require_held_item_lock(server.db, item_key, lock_a["token"])
    assert exc.value.code == "FINANCIAL_ITEM_LOCK_CONFLICT"
    assert await renew_item_lock(server.db, item_key, lock_a["token"]) is None
    assert await release_item_lock(server.db, item_key, lock_a["token"]) is False
    stolen = await server.db.inventory_financial_item_locks.find_one(
        {"financial_inventory_item_key": item_key}, {"_id": 0}
    )
    assert stolen["token"] == "token-b"
    blocked = await _unlocked_post(mov["id"])
    assert isinstance(blocked, HTTPException)
    assert (blocked.detail or {}).get("code") == "FINANCIAL_ITEM_LOCK_CONFLICT"
    assert await _source_journals(mov["id"]) == []


async def test_gr_reverse_stale_lock_cannot_reverse_after_cogs(monkeypatch):
    inventory, *_ = await _configure_inventory()
    mid, gr, _, _ = await _seed_posted_gr(qty=10, price=10, inventory=inventory)
    locks = await acquire_item_locks(server.db, [f"m:{mid}"], f"gr_reverse:{gr['id']}")
    assert locks
    future = (datetime.now(timezone.utc) + timedelta(seconds=600)).isoformat()
    await server.db.inventory_financial_item_locks.update_one(
        {"financial_inventory_item_key": f"m:{mid}"},
        {"$set": {"token": "cogs-b", "holder_movement_id": "mov-b", "expires_at": future}},
    )
    assert await renew_item_locks(server.db, locks) is None

    async def steal_then_none(db, held, now=None):
        return None

    monkeypatch.setattr("services.inventory_financial_locks.renew_item_locks", steal_then_none)
    blocked = await _unlocked_reverse(gr["id"], "stale resume")
    assert isinstance(blocked, HTTPException)
    assert blocked.status_code == 409
    assert (blocked.detail or {}).get("code") == "FINANCIAL_ITEM_LOCK_CONFLICT"
    still = await server.db[JOURNAL_ENTRIES].find_one(
        {"source_type": "purchase_goods_receipt", "source_id": gr["id"], "reversal_of": None},
        {"_id": 0},
    )
    assert still and still["status"] == "posted"


async def test_immutable_basis_survives_settings_drift():
    inventory, clearing, payable, cogs = await _configure_inventory()
    mid, _, _, _ = await _seed_posted_gr(qty=10, price=10, inventory=inventory)
    mov = await _seed_movement(mid=mid, qty=4)
    claim = await acquire_posting_claim(server.db, mov["id"])
    from services.inventory_issue_accounting import build_cogs_posting_basis
    from services.inventory_issue_posting import _reload_computed
    from services.purchase_accounting_settings import get_purchase_accounting_settings

    ev = await evaluate_inventory_issue(server.db, mov["id"])
    computed = await _reload_computed(server.db, mov["id"])
    settings = await get_purchase_accounting_settings(server.db)
    movement = await server.db.stock_movements.find_one({"id": mov["id"]}, {"_id": 0})
    basis = build_cogs_posting_basis(
        movement=movement,
        evaluation=ev,
        computed=computed,
        settings=settings,
        user=POST_USER,
        claim_token=claim[CLAIM_FIELD],
    )
    assert await persist_accounting_posting_basis(server.db, mov["id"], claim[CLAIM_FIELD], basis)
    original = dict(basis)
    new_cogs = await _coa("51", "HPP Baru", "COGS")
    await server.db.app_settings.update_one(
        {"key": SETTINGS_KEY},
        {"$set": {"cogs_account_id": new_cogs["id"]}},
    )
    await server.db.app_settings.update_one({"key": "hpp_valuation"}, {"$set": {"method": "RATA-RATA"}})
    await server.db.master_items.update_one({"id": mid}, {"$set": {"item_name": "renamed"}})
    rebuilt = dict(original)
    rebuilt["total_cogs"] = 1.0
    rebuilt["hpp_method"] = "RATA-RATA"
    rebuilt["cogs_account_id"] = new_cogs["id"]
    assert await persist_accounting_posting_basis(server.db, mov["id"], "other-token", rebuilt) is False
    stale = (datetime.now(timezone.utc) - timedelta(seconds=601)).isoformat()
    await server.db[COLLECTION].update_one(
        {"movement_id": mov["id"]},
        {"$set": {CLAIM_AT_FIELD: stale}},
    )
    retry = await _unlocked_post(mov["id"])
    assert not isinstance(retry, HTTPException), getattr(retry, "detail", retry)
    stored = await server.db[COLLECTION].find_one({"movement_id": mov["id"]}, {"_id": 0})
    kept = stored[POSTING_BASIS_FIELD]
    assert kept["total_cogs"] == original["total_cogs"]
    assert kept["hpp_method"] == original["hpp_method"]
    assert kept["cogs_account_id"] == original["cogs_account_id"]
    assert kept.get("consumed_acquisitions") == original.get("consumed_acquisitions")
    journal = (await _source_journals(mov["id"]))[0]
    lines = await _journal_lines(journal["id"])
    by_acc = {ln["account_id"]: ln for ln in lines}
    assert Decimal(str(by_acc[cogs["id"]]["debit"])) == Decimal(str(original["total_cogs"]))
    assert new_cogs["id"] not in by_acc
    assert len(await _source_journals(mov["id"])) == 1


async def test_unjournaled_basis_cannot_overconsume_after_later_post():
    inventory, *_ = await _configure_inventory()
    mid, _, _, _ = await _seed_posted_gr(qty=1000, price=1, inventory=inventory)
    early = await _seed_movement(mid=mid, qty=700, date="2026-10-05T03:00:00Z")
    later = await _seed_movement(mid=mid, qty=600, date="2026-10-06T03:00:00Z")
    claim = await acquire_posting_claim(server.db, early["id"])
    from services.inventory_issue_accounting import build_cogs_posting_basis
    from services.inventory_issue_posting import _reload_computed
    from services.purchase_accounting_settings import get_purchase_accounting_settings

    ev = await evaluate_inventory_issue(server.db, early["id"])
    computed = await _reload_computed(server.db, early["id"])
    settings = await get_purchase_accounting_settings(server.db)
    movement = await server.db.stock_movements.find_one({"id": early["id"]}, {"_id": 0})
    basis = build_cogs_posting_basis(
        movement=movement,
        evaluation=ev,
        computed=computed,
        settings=settings,
        user=POST_USER,
        claim_token=claim[CLAIM_FIELD],
    )
    assert await persist_accounting_posting_basis(server.db, early["id"], claim[CLAIM_FIELD], basis)
    waiting = await evaluate_inventory_issue(server.db, later["id"])
    assert waiting["status"] == "waiting_earlier_issue"
    assert waiting["can_post"] is False
    await server.db.stock_movements.update_one({"id": early["id"]}, {"$set": {"status": "VOID"}})
    posted_later = await _unlocked_post(later["id"])
    assert not isinstance(posted_later, HTTPException), getattr(posted_later, "detail", posted_later)
    await server.db.stock_movements.update_one({"id": early["id"]}, {"$unset": {"status": ""}})
    stale = (datetime.now(timezone.utc) - timedelta(seconds=601)).isoformat()
    await server.db[COLLECTION].update_one(
        {"movement_id": early["id"]},
        {"$set": {CLAIM_AT_FIELD: stale}},
    )
    retry = await _unlocked_post(early["id"])
    assert isinstance(retry, HTTPException)
    assert (retry.detail or {}).get("code") == "FINANCIAL_CHRONOLOGY_CONFLICT"
    assert await _source_journals(early["id"]) == []
    stored = await server.db[COLLECTION].find_one({"movement_id": early["id"]}, {"_id": 0})
    assert stored[POSTING_BASIS_FIELD]["total_cogs"] == basis["total_cogs"]
    assert stored[POSTING_BASIS_FIELD]["consumed_acquisitions"] == basis["consumed_acquisitions"]
    assert len(await _source_journals(later["id"])) == 1


async def test_posted_metadata_without_journal_is_not_consumption():
    inventory, *_ = await _configure_inventory()
    mid, _, _, _ = await _seed_posted_gr(qty=10, price=10, inventory=inventory)
    real = await _seed_movement(mid=mid, qty=4, date="2026-10-05T03:00:00Z")
    await server.db[COLLECTION].insert_one({
        "movement_id": "ghost-no-movement",
        "status": STATUS_POSTED,
        "accounting_journal_id": "missing-journal",
        POSTING_BASIS_FIELD: {
            "movement_id": "ghost-no-movement",
            "hpp_method": "FIFO",
            "total_cogs": 40,
            "financial_inventory_item_key": f"m:{mid}",
            "consumed_acquisitions": [{
                "acquisition_key": "ghost:1",
                "base_qty_consumed": 4,
                "total_cost": 40,
            }],
        },
    })
    row = await evaluate_inventory_issue(server.db, real["id"])
    assert row["status"] == "pending"
    assert row["can_post"] is True
    posted = await _unlocked_post(real["id"])
    assert not isinstance(posted, HTTPException), getattr(posted, "detail", posted)
    assert Decimal(str(posted["computed_cogs"])) == Decimal("40.00")


async def test_method_policy_is_immutable_and_unique():
    await establish_cogs_policy(server.db, "FIFO", POST_USER)
    from services.inventory_issue_accounting import InventoryIssueError

    with pytest.raises(InventoryIssueError) as exc:
        await establish_cogs_policy(server.db, "RATA-RATA", POST_USER)
    assert exc.value.code == "FINANCIAL_COGS_METHOD_MISMATCH"
    docs = await server.db[CONTROL_COLLECTION].find({"key": CONTROL_KEY}, {"_id": 0}).to_list(10)
    assert len(docs) == 1
    assert docs[0]["costing_method"] == "FIFO"
    again = await establish_cogs_policy(server.db, "FIFO", POST_USER)
    assert again == "FIFO"
    docs2 = await server.db[CONTROL_COLLECTION].find({"key": CONTROL_KEY}, {"_id": 0}).to_list(10)
    assert len(docs2) == 1


async def test_indexes_are_idempotent():
    await ensure_indexes(server.db)
    await ensure_indexes(server.db)
    acct = await server.db[COLLECTION].index_information()
    locks = await server.db.inventory_financial_item_locks.index_information()
    ctrl = await server.db[CONTROL_COLLECTION].index_information()
    assert any(idx.get("key") == [("movement_id", 1)] and idx.get("unique") for idx in acct.values())
    assert any(
        idx.get("key") == [("financial_inventory_item_key", 1)] and idx.get("unique")
        for idx in locks.values()
    )
    assert any(idx.get("key") == [("key", 1)] and idx.get("unique") for idx in ctrl.values())


async def test_fifo_qty3_total100_and_multi_account_balance():
    inventory, *_ = await _configure_inventory()
    mid, _, _, _ = await _seed_posted_gr(qty=3, price=33.333333333, inventory=inventory)
    first = await _seed_movement(mid=mid, qty=1, date="2026-10-05T03:00:00Z")
    second = await _seed_movement(mid=mid, qty=1, date="2026-10-06T03:00:00Z")
    third = await _seed_movement(mid=mid, qty=1, date="2026-10-07T03:00:00Z")
    posts = [await _unlocked_post(m["id"]) for m in (first, second, third)]
    assert all(not isinstance(x, HTTPException) for x in posts)
    totals = []
    for mov in (first, second, third):
        stored = await server.db[COLLECTION].find_one({"movement_id": mov["id"]}, {"_id": 0})
        totals.append(Decimal(str(stored[POSTING_BASIS_FIELD]["total_cogs"])))
        journal = (await _source_journals(mov["id"]))[0]
        lines = await _journal_lines(journal["id"])
        debit = sum(Decimal(str(ln["debit"])) for ln in lines)
        credit = sum(Decimal(str(ln["credit"])) for ln in lines)
        assert debit == credit
    assert sum(totals) == Decimal("100.00")


@pytest.mark.anyio
async def test_fresh_posting_claim_cannot_be_stolen():
    inventory, *_ = await _configure_inventory()
    mid, _, _, _ = await _seed_posted_gr(qty=10, price=10, inventory=inventory)
    mov = await _seed_movement(mid=mid, qty=2)
    first = await acquire_posting_claim(server.db, mov["id"], token="token-a")
    assert first and first[CLAIM_FIELD] == "token-a"
    second = await acquire_posting_claim(server.db, mov["id"], token="token-b")
    assert second is None
    stored = await server.db[COLLECTION].find_one({"movement_id": mov["id"]}, {"_id": 0})
    assert stored[CLAIM_FIELD] == "token-a"


@pytest.mark.anyio
async def test_stale_posting_claim_is_recoverable():
    inventory, *_ = await _configure_inventory()
    mid, _, _, _ = await _seed_posted_gr(qty=10, price=10, inventory=inventory)
    mov = await _seed_movement(mid=mid, qty=2)
    stale = (datetime.now(timezone.utc) - timedelta(seconds=601)).isoformat()
    await server.db[COLLECTION].insert_one({
        "movement_id": mov["id"],
        CLAIM_FIELD: "token-a",
        CLAIM_AT_FIELD: stale,
        "created_at": stale,
        "updated_at": stale,
    })
    recovered = await acquire_posting_claim(server.db, mov["id"], token="token-b")
    assert recovered and recovered[CLAIM_FIELD] == "token-b"
    stored = await server.db[COLLECTION].find_one({"movement_id": mov["id"]}, {"_id": 0})
    assert stored[CLAIM_FIELD] == "token-b"


@pytest.mark.anyio
async def test_old_token_cannot_clear_new_token():
    inventory, *_ = await _configure_inventory()
    mid, _, _, _ = await _seed_posted_gr(qty=10, price=10, inventory=inventory)
    mov = await _seed_movement(mid=mid, qty=2)
    owned = await acquire_posting_claim(server.db, mov["id"], token="token-b")
    assert owned and owned[CLAIM_FIELD] == "token-b"
    await clear_posting_claim(server.db, mov["id"], token="token-a")
    stored = await server.db[COLLECTION].find_one({"movement_id": mov["id"]}, {"_id": 0})
    assert stored[CLAIM_FIELD] == "token-b"
    await clear_posting_claim(server.db, mov["id"], token="token-b")
    cleared = await server.db[COLLECTION].find_one({"movement_id": mov["id"]}, {"_id": 0})
    assert not cleared.get(CLAIM_FIELD)


@pytest.mark.anyio
async def test_same_movement_concurrent_posting_one_journal():
    inventory, *_ = await _configure_inventory()
    mid, _, _, _ = await _seed_posted_gr(qty=20, price=10, inventory=inventory)
    mov = await _seed_movement(mid=mid, qty=3)
    first, second = await asyncio.gather(_unlocked_post(mov["id"]), _unlocked_post(mov["id"]))
    ok = [x for x in (first, second) if not isinstance(x, HTTPException)]
    assert ok
    journals = await _source_journals(mov["id"])
    assert len(journals) == 1


async def _insert_live_posted_issue(movement: dict, *, item_key: str, journal_date: str, qty: int, total_cogs: float):
    jid = f"j-{movement['id']}"
    await server.db[JOURNAL_ENTRIES].insert_one({
        "id": jid,
        "source_type": SOURCE_TYPE,
        "source_id": movement["id"],
        "status": STATUS_POSTED,
        "reversal_of": None,
        "date": journal_date,
    })
    await server.db[COLLECTION].insert_one({
        "movement_id": movement["id"],
        "financial_inventory_item_key": item_key,
        POSTING_BASIS_FIELD: {
            "movement_id": movement["id"],
            "financial_inventory_item_key": item_key,
            "journal_date": journal_date,
            "movement_timestamp": movement["date"],
            "hpp_method": "FIFO",
            "issue_base_qty": qty,
            "total_cogs": total_cogs,
        },
        "status": STATUS_POSTED,
        "accounting_journal_id": jid,
    })


@pytest.mark.anyio
async def test_post_revalidates_later_posted_issue_before_journal():
    inventory, *_ = await _configure_inventory()
    mid, _, _, _ = await _seed_posted_gr(qty=200, price=10, inventory=inventory)
    older = await _seed_movement(mid=mid, qty=30, date="2026-10-05T03:00:00Z")
    later = await _seed_movement(mid=mid, qty=20, date="2026-10-10T03:00:00Z")
    ready = await evaluate_inventory_issue(server.db, older["id"])
    assert ready["can_post"] is True
    assert ready["status"] == "pending"
    await _insert_live_posted_issue(
        later, item_key=f"m:{mid}", journal_date="2026-10-10", qty=20, total_cogs=200
    )
    result = await _unlocked_post(older["id"])
    assert isinstance(result, HTTPException)
    assert result.status_code == 409
    assert (result.detail or {}).get("code") == "FINANCIAL_CHRONOLOGY_CONFLICT"
    assert await _source_journals(older["id"]) == []
    later_row = await evaluate_inventory_issue(server.db, later["id"])
    assert later_row["status"] == STATUS_POSTED


@pytest.mark.anyio
async def test_unjournaled_older_basis_retry_blocks_after_later_post():
    inventory, *_ = await _configure_inventory()
    mid, _, _, _ = await _seed_posted_gr(qty=1000, price=1, inventory=inventory)
    older = await _seed_movement(mid=mid, qty=200, date="2026-10-05T03:00:00Z")
    later = await _seed_movement(mid=mid, qty=100, date="2026-10-10T03:00:00Z")
    claim = await acquire_posting_claim(server.db, older["id"])
    from services.inventory_issue_accounting import build_cogs_posting_basis
    from services.inventory_issue_posting import _reload_computed
    from services.purchase_accounting_settings import get_purchase_accounting_settings

    ev = await evaluate_inventory_issue(server.db, older["id"])
    computed = await _reload_computed(server.db, older["id"])
    settings = await get_purchase_accounting_settings(server.db)
    movement = await server.db.stock_movements.find_one({"id": older["id"]}, {"_id": 0})
    basis = build_cogs_posting_basis(
        movement=movement,
        evaluation=ev,
        computed=computed,
        settings=settings,
        user=POST_USER,
        claim_token=claim[CLAIM_FIELD],
    )
    assert await persist_accounting_posting_basis(server.db, older["id"], claim[CLAIM_FIELD], basis)
    original = dict(basis)
    await server.db.stock_movements.update_one({"id": older["id"]}, {"$set": {"status": "VOID"}})
    posted_later = await _unlocked_post(later["id"])
    assert not isinstance(posted_later, HTTPException), getattr(posted_later, "detail", posted_later)
    await server.db.stock_movements.update_one({"id": older["id"]}, {"$unset": {"status": ""}})
    stale = (datetime.now(timezone.utc) - timedelta(seconds=601)).isoformat()
    await server.db[COLLECTION].update_one(
        {"movement_id": older["id"]},
        {"$set": {CLAIM_AT_FIELD: stale}},
    )
    retry = await _unlocked_post(older["id"])
    assert isinstance(retry, HTTPException)
    assert (retry.detail or {}).get("code") == "FINANCIAL_CHRONOLOGY_CONFLICT"
    assert await _source_journals(older["id"]) == []
    stored = await server.db[COLLECTION].find_one({"movement_id": older["id"]}, {"_id": 0})
    assert stored[POSTING_BASIS_FIELD]["total_cogs"] == original["total_cogs"]
    assert stored[POSTING_BASIS_FIELD]["consumed_acquisitions"] == original["consumed_acquisitions"]
    assert stored[POSTING_BASIS_FIELD]["hpp_method"] == original["hpp_method"]
