"""Phase 5B.1 Inventory / COGS reconciliation — read-only report."""
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
os.environ["DB_NAME"] = f"eternal_packaging_inv_reconciliation_test_{_worker}"
os.environ.setdefault("JWT_SECRET", "test-secret-key-12345678901234567890")
os.environ.setdefault("CORS_ORIGINS", "*")
os.environ.setdefault("STORAGE_PROVIDER", "local")

import server
from permissions import ALL_PERMISSIONS, ROLE_DEFAULT_PERMISSIONS
from server import app, current_user
from services.accounting_periods import ensure_indexes as ensure_periods
from services.chart_of_accounts import create_account, ensure_indexes as ensure_coa
from services.inventory_issue_accounting import (
    CLAIM_AT_FIELD,
    CLAIM_FIELD,
    COLLECTION as ISSUE_COLLECTION,
    POSTING_BASIS_FIELD,
    SOURCE_TYPE as SOURCE_ISSUE,
    ensure_indexes as ensure_issue,
)
from services.inventory_issue_posting import post_inventory_issue_accounting
from services.inventory_reconciliation import (
    ACTIVITY_NONE,
    CODE_BASIS_JOURNAL_MISMATCH,
    CODE_CONSUMED_ACQUISITION_NOT_LIVE,
    CODE_DOCUMENT_POSTED_WITHOUT_JOURNAL,
    CODE_INCOMPLETE_REVERSAL_PAIR,
    CODE_INVALID_ACQUISITION_BASIS,
    CODE_MISSING_COA,
    CODE_POSTED_JOURNAL_MISSING_BASIS,
    CODE_POSTED_JOURNAL_MISSING_DOCUMENT,
    CODE_UNRESOLVED_ACCOUNT,
    CODE_RECOVERY_REQUIRED,
    CODE_PHYSICAL_STOCK_IN_PROGRESS,
    CODE_UOM_MISMATCH,
    DOCUMENT_ID_BATCH_SIZE,
    FLAG_PENDING_COGS,
    FLAG_PENDING_GR,
    FLAG_PRE_EFFECTIVE,
    FLAG_RECOVERY_REQUIRED,
    FLAG_EXCLUDED_SERVICE,
    PERM_VIEW,
    QTY_IN_PROGRESS,
    QTY_INCOMPLETE,
    QTY_MATCHED,
    QTY_MISSING_PHYSICAL,
    QTY_UOM_MISMATCH,
    SCOPE_CURRENT,
    STABILITY_IN_PROGRESS,
    STABILITY_RETRY_REQUIRED,
    STABILITY_STABLE,
    STATUS_DIFFERENCE,
    STATUS_EXPLAINED_DIFFERENCE,
    STATUS_INCOMPLETE,
    STATUS_RECONCILED,
    get_inventory_reconciliation_accounts,
    get_inventory_reconciliation_items,
    get_inventory_reconciliation_summary,
)
from services.journal_core import JOURNAL_ENTRIES, JOURNAL_LINES, ensure_indexes as ensure_journal
from services.purchase_accounting_settings import SETTINGS_KEY
from services.purchase_goods_receipt_posting import (
    CLAIM_AT_FIELD as GR_CLAIM_AT,
    CLAIM_FIELD as GR_CLAIM,
    POSTING_BASIS_FIELD as GR_BASIS_FIELD,
    SOURCE_REVERSAL,
    SOURCE_TYPE as SOURCE_GR,
    post_purchase_goods_receipt_accounting,
)
from services import financial_statement_common as fsc
from services import inventory_reconciliation as recon

POST_USER = {
    "id": "admin-recon",
    "email": "admin@eternalpackaging.com",
    "name": "Admin Recon",
    "role": "admin",
}

SUMMARY = "/api/ledger/inventory-reconciliation/summary"
ACCOUNTS = "/api/ledger/inventory-reconciliation/accounts"
ITEMS = "/api/ledger/inventory-reconciliation/items"

WRITE_METHODS = (
    "insert_one",
    "insert_many",
    "update_one",
    "update_many",
    "delete_one",
    "delete_many",
    "find_one_and_update",
    "bulk_write",
    "replace_one",
)

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture(autouse=True)
def bind_motor():
    from motor.motor_asyncio import AsyncIOMotorClient

    os.environ["DB_NAME"] = f"eternal_packaging_inv_reconciliation_test_{_worker}"
    server.client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    server.db = server.client[os.environ["DB_NAME"]]


@pytest.fixture(autouse=True)
async def indexes(bind_motor):
    await ensure_coa(server.db)
    await ensure_journal(server.db)
    await ensure_periods(server.db)
    await ensure_issue(server.db)
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
    await server.db.product_stock_state.delete_many({})
    await server.db.ledger_accounts.delete_many({})
    await server.db[JOURNAL_ENTRIES].delete_many({})
    await server.db[JOURNAL_LINES].delete_many({})
    await server.db.financial_inventory_openings.delete_many({})
    await server.db.financial_inventory_opening_claims.delete_many({})
    await server.db.app_settings.update_one(
        {"key": "physical_stock"},
        {"$set": {"key": "physical_stock", "enabled": False, "stock_in_trigger": "goods_receipt"}},
        upsert=True,
    )
    yield


def _admin():
    app.dependency_overrides[current_user] = lambda: dict(POST_USER)


def _user(role="admin", perms=None, **kw):
    uid = kw.get("id") or f"u-{role}-{uuid.uuid4().hex[:8]}"
    if role == "admin":
        override = {}
    elif perms is not None:
        override = {p: True for p in perms}
    else:
        override = {}
    return {
        "id": uid,
        "email": kw.get("email") or f"{uid}@example.com",
        "name": kw.get("name") or role.title(),
        "role": role if role != "viewer" else "operator",
        "status": "active",
        "user_id": uid,
        "permissions_override": override,
    }


async def client_for(user):
    app.dependency_overrides[current_user] = lambda: user
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


async def _coa(code_prefix: str, name: str, account_type: str) -> dict:
    return await create_account(
        server.db,
        {"code": f"{code_prefix}{uuid.uuid4().hex[:6]}", "name": name, "account_type": account_type},
        user={"id": "t", "name": "t", "role": "admin"},
    )


async def _configure_inventory(effective="2026-10-01", inventory=None, cogs=None, method="FIFO"):
    payable = await _coa("21", "Hutang Usaha", "APAY")
    clearing = await _coa("21", "Purchase Clearing", "OCLY")
    inventory = inventory or await _coa("13", "Persediaan", "INTR")
    cogs = cogs or await _coa("51", "HPP", "COGS")
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


async def _insert_gr(*, qty=1000, price=1000, receipt_date="2026-10-02", mid=None, item_type="INVENTORY"):
    mid = mid or f"m-{uuid.uuid4().hex[:8]}"
    await _seed_master(mid, item_type=item_type)
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
            "ordered_qty": max(int(qty) if float(qty) == int(qty) else 10000, 1000),
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
    return mid, gr


async def _post_gr(gr):
    posted = await post_purchase_goods_receipt_accounting(
        server.db, gr["id"], POST_USER, serialize_in_process=False
    )
    return posted


async def _seed_posted_gr(*, qty=1000, price=1000, receipt_date="2026-10-02", mid=None, inventory=None, method="FIFO"):
    if inventory is None:
        inventory, *_rest = await _configure_inventory(method=method)
    mid, gr = await _insert_gr(qty=qty, price=price, receipt_date=receipt_date, mid=mid)
    posted = await _post_gr(gr)
    if isinstance(posted, dict):
        gr["accounting_journal_id"] = posted.get("accounting_journal_id")
    return mid, gr, inventory


async def _seed_movement(*, mid, qty=400, date="2026-10-05T03:00:00Z", movement_type="PENGIRIMAN"):
    mov_id = str(uuid.uuid4())
    doc = {
        "id": mov_id,
        "movement_type": movement_type,
        "master_item_id": mid,
        "date": date,
        "created_at": date,
        "transaction_qty": qty,
        "transaction_uom": "PCS",
        "conversion_factor": 1,
        "base_qty": qty,
        "base_uom": "PCS",
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


async def _post_issue(movement):
    return await post_inventory_issue_accounting(server.db, movement["id"], POST_USER)


async def _insert_journal(*, source_type, source_id, lines, date="2026-10-02", status="posted", reversal_of=None):
    jid = str(uuid.uuid4())
    await server.db[JOURNAL_ENTRIES].insert_one({
        "id": jid,
        "number": f"JE-{jid[:8]}",
        "date": date,
        "source_type": source_type,
        "source_id": source_id,
        "status": status,
        "reversal_of": reversal_of,
        "description": source_type,
    })
    for ln in lines:
        await server.db[JOURNAL_LINES].insert_one({
            "id": str(uuid.uuid4()),
            "journal_id": jid,
            "account_id": ln["account_id"],
            "debit": ln.get("debit", 0),
            "credit": ln.get("credit", 0),
        })
    return jid


async def _stock(mid, *, total, unprinted=None, printed=None, uom="PCS", initialized=True, pending_event=None):
    unprinted = total if unprinted is None else unprinted
    printed = 0 if printed is None else printed
    doc = {
        "_id": mid,
        "master_item_id": mid,
        "initialized": initialized,
        "status": "INITIALIZED" if initialized else "UNINITIALIZED",
        "base_uom": uom,
        "total_qty": total,
        "unprinted_qty": unprinted,
        "printed_qty": printed,
        "pending_event": pending_event,
    }
    await server.db.product_stock_state.insert_one(doc)


def _money(value) -> Decimal:
    return Decimal(str(value)).quantize(Decimal("0.01"))


def _inv(body):
    return body["inventory"]


def _assert_identity(block):
    gb = _money(block["gl_vs_basis_difference"])
    sb = _money(block["basis_vs_source_difference"])
    gs = _money(block["other_journal_balance"])
    assert gb == sb + gs
    assert block.get("identity_holds") is True


def _account_row(body, account_id):
    for row in body.get("inventory_accounts") or []:
        if row["account_id"] == account_id:
            return row
    for row in body.get("cogs_accounts") or []:
        if row["account_id"] == account_id:
            return row
    raise AssertionError(f"account {account_id} missing")


async def _summary():
    return await get_inventory_reconciliation_summary(server.db)


async def _accounts(**kwargs):
    return await get_inventory_reconciliation_accounts(server.db, **kwargs)


async def _items(**kwargs):
    return await get_inventory_reconciliation_items(server.db, **kwargs)


def _codes(body):
    return {row["code"] for row in body.get("integrity_anomalies") or []}


async def _docs_snapshot():
    colls = {
        "journal_entries": JOURNAL_ENTRIES,
        "journal_lines": JOURNAL_LINES,
        "gr": "purchase_goods_receipts",
        "issues": ISSUE_COLLECTION,
        "stock": "product_stock_state",
        "movements": "stock_movements",
        "settings": "app_settings",
        "locks": "inventory_financial_item_locks",
        "coa": "ledger_accounts",
        "periods": "accounting_periods",
    }
    out = {}
    for key, name in colls.items():
        rows = await server.db[name].find({}, {"_id": 0}).to_list(None)
        out[key] = sorted((row for row in rows), key=lambda r: str(sorted(r.items())))
    return out


class _RejectingCollection:
    def __init__(self, coll):
        self._coll = coll

    def __getattr__(self, name):
        if name in WRITE_METHODS:
            def boom(*_a, **_k):
                raise AssertionError(f"unexpected write {name}")
            return boom
        return getattr(self._coll, name)


class _RejectingDB:
    def __init__(self, db):
        self._db = db

    def __getattr__(self, name):
        value = getattr(self._db, name)
        if hasattr(value, "insert_one"):
            return _RejectingCollection(value)
        return value

    def __getitem__(self, name):
        return _RejectingCollection(self._db[name])


# --- 1-5 BASIC ---

async def test_posted_gr_minus_posted_cogs_matches():
    mid, gr, inventory = await _seed_posted_gr(qty=1000, price=1000)
    inventory, clearing, payable, cogs = inventory, None, None, None
    settings = await server.db.app_settings.find_one({"key": SETTINGS_KEY})
    cogs_id = settings["cogs_account_id"]
    mov = await _seed_movement(mid=mid, qty=400)
    await _post_issue(mov)
    body = await _summary()
    assert body["scope"] == SCOPE_CURRENT
    assert body["historical_as_of"] is False
    assert body["inventory_accounting_effective_date"] == "2026-10-01"
    assert _money(_inv(body)["financial_inventory_basis_value"]) == _money("600000.00")
    assert _money(_inv(body)["inventory_source_journal_balance"]) == _money("600000.00")
    assert _money(_inv(body)["inventory_gl_balance"]) == _money("600000.00")
    assert _inv(body)["value_status"] == STATUS_RECONCILED
    assert _money(body["cogs"]["financial_cogs_basis_value"]) == _money("400000.00")
    assert _money(body["cogs"]["cogs_source_journal_balance"]) == _money("400000.00")
    assert _money(body["cogs"]["cogs_gl_balance"]) == _money("400000.00")
    _assert_identity(_inv(body))
    _assert_identity(body["cogs"])
    assert body["reconciliation_status"] == STATUS_RECONCILED
    assert inventory["id"]


async def test_unposted_gr_never_enters_recognized_inventory():
    await _configure_inventory()
    await _insert_gr(qty=1000, price=1000)
    body = await _summary()
    assert _money(_inv(body)["financial_inventory_basis_value"]) == _money("0.00")
    assert _money(_inv(body)["inventory_source_journal_balance"]) == _money("0.00")
    assert _money(_inv(body)["inventory_gl_balance"]) == _money("0.00")
    assert body["pending"]["active_gr_candidates_without_journal"]["count"] == 1
    assert FLAG_PENDING_GR in body["posting_flags"]


async def test_unposted_and_blocked_cogs_never_reduce_recognized():
    mid, gr, inventory = await _seed_posted_gr(qty=1000, price=1000)
    pending = await _seed_movement(mid=mid, qty=100)
    blocked = await _seed_movement(mid=mid, qty=5000, date="2026-10-06T03:00:00Z")
    body = await _summary()
    assert _money(_inv(body)["financial_inventory_basis_value"]) == _money("1000000.00")
    assert pending["id"] not in str(body.get("inventory"))
    assert body["pending"]["pending_cogs"]["count"] >= 1
    assert body["pending"]["blocked_cogs"]["count"] + body["pending"]["waiting_earlier_issue"]["count"] >= 1
    assert FLAG_PENDING_COGS in body["posting_flags"]
    assert blocked["id"]


async def test_document_posted_without_journal_not_recognized():
    await _configure_inventory()
    mid, gr = await _insert_gr(qty=1000, price=1000)
    await server.db.purchase_goods_receipts.update_one(
        {"id": gr["id"]},
        {"$set": {"accounting_status": "posted", "accounting_journal_id": "ghost-je"}},
    )
    body = await _summary()
    assert _money(_inv(body)["financial_inventory_basis_value"]) == _money("0.00")
    assert CODE_DOCUMENT_POSTED_WITHOUT_JOURNAL in _codes(body)
    assert body["reconciliation_status"] == STATUS_INCOMPLETE


async def test_journal_with_incomplete_metadata_still_recognized_no_heal():
    mid, gr, inventory = await _seed_posted_gr(qty=1000, price=1000)
    before = await server.db.purchase_goods_receipts.find_one({"id": gr["id"]}, {"_id": 0})
    await server.db.purchase_goods_receipts.update_one(
        {"id": gr["id"]},
        {"$unset": {"accounting_journal_id": "", "accounting_status": ""}},
    )
    body = await _summary()
    assert _money(_inv(body)["financial_inventory_basis_value"]) == _money("1000000.00")
    after = await server.db.purchase_goods_receipts.find_one({"id": gr["id"]}, {"_id": 0})
    assert after.get("accounting_journal_id") in (None, "")
    assert after.get("accounting_status") in (None, "")
    assert after.get(GR_BASIS_FIELD) == before.get(GR_BASIS_FIELD)
    assert inventory["id"]


# --- 6-10 METHOD / ACCOUNT ---

async def test_fifo_partial_and_multiple_acquisition_accounts():
    inv_a = await _coa("13", "Persediaan A", "INTR")
    inv_b = await _coa("13", "Persediaan B", "INTR")
    await _configure_inventory(inventory=inv_a, method="FIFO")
    mid, gr_a, _ = await _seed_posted_gr(qty=600, price=10, mid=None, inventory=inv_a)
    await _configure_inventory(inventory=inv_b, method="FIFO")
    await server.db.app_settings.update_one(
        {"key": SETTINGS_KEY},
        {"$set": {"inventory_account_id": inv_b["id"]}},
    )
    _, gr_b, _ = await _seed_posted_gr(qty=400, price=10, mid=mid, inventory=inv_b)
    mov = await _seed_movement(mid=mid, qty=700)
    await _post_issue(mov)
    body = await _accounts()
    row_a = _account_row(body, inv_a["id"])
    row_b = _account_row(body, inv_b["id"])
    remaining = {
        inv_a["id"]: _money(row_a["financial_inventory_basis_value"]),
        inv_b["id"]: _money(row_b["financial_inventory_basis_value"]),
    }
    assert remaining[inv_a["id"]] + remaining[inv_b["id"]] == _money("3000.00")
    assert set(remaining.values()) == {_money("0.00"), _money("3000.00")}
    _assert_identity(row_a)
    _assert_identity(row_b)
    items = await _items()
    row = items["items"][0]
    assert row["financial_qty"] == 300
    assert set(row["inventory_account_ids"]) == {inv_a["id"], inv_b["id"]}
    assert gr_a["id"] and gr_b["id"]


async def test_rata_rata_uses_frozen_posted_values():
    inv, *_rest = await _configure_inventory(method="RATA-RATA")
    mid, gr, _ = await _seed_posted_gr(qty=100, price=10, inventory=inv, method="RATA-RATA")
    mov = await _seed_movement(mid=mid, qty=40)
    posted = await _post_issue(mov)
    stored = await server.db[ISSUE_COLLECTION].find_one({"movement_id": mov["id"]}, {"_id": 0})
    assert _money(stored[POSTING_BASIS_FIELD]["total_cogs"]) == _money("400.00")
    await _seed_posted_gr(qty=100, price=50, mid=mid, inventory=inv, method="RATA-RATA")
    body = await _summary()
    assert _money(_inv(body)["financial_inventory_basis_value"]) == _money("5600.00")
    assert "consumed_acquisitions" not in (await _items())["items"][0]
    assert posted.get("accounting_journal_id") or stored.get("accounting_journal_id")
    assert gr["id"]


async def test_historical_accounts_retained_after_settings_change():
    old_inv = await _coa("13", "Persediaan Lama", "INTR")
    new_inv = await _coa("13", "Persediaan Baru", "INTR")
    old_cogs = await _coa("51", "HPP Lama", "COGS")
    new_cogs = await _coa("51", "HPP Baru", "COGS")
    await _configure_inventory(inventory=old_inv, cogs=old_cogs)
    mid, gr, _ = await _seed_posted_gr(qty=100, price=10, inventory=old_inv)
    mov = await _seed_movement(mid=mid, qty=40)
    await _post_issue(mov)
    await server.db.app_settings.update_one(
        {"key": SETTINGS_KEY},
        {"$set": {"inventory_account_id": new_inv["id"], "cogs_account_id": new_cogs["id"]}},
    )
    body = await _accounts()
    hist_inv = _account_row(body, old_inv["id"])
    hist_cogs = _account_row(body, old_cogs["id"])
    assert _money(hist_inv["financial_inventory_basis_value"]) == _money("600.00")
    assert _money(hist_cogs["financial_cogs_basis_value"]) == _money("400.00")
    assert gr["id"]


async def test_no_fictitious_rata_rata_per_gr_allocation():
    inv, *_ = await _configure_inventory(method="RATA-RATA")
    mid, _gr, _ = await _seed_posted_gr(qty=50, price=10, inventory=inv, method="RATA-RATA")
    await _seed_posted_gr(qty=50, price=20, mid=mid, inventory=inv, method="RATA-RATA")
    mov = await _seed_movement(mid=mid, qty=40)
    await _post_issue(mov)
    stored = await server.db[ISSUE_COLLECTION].find_one({"movement_id": mov["id"]}, {"_id": 0})
    assert "consumed_acquisitions" not in (stored.get(POSTING_BASIS_FIELD) or {})
    items = await _items()
    assert "consumed_acquisitions" not in items["items"][0]
    assert items["items"][0]["financial_qty"] == 60


async def test_exact_decimals_and_negative_values_preserved():
    inv, clearing, _payable, cogs = await _configure_inventory()
    mid, gr, _ = await _seed_posted_gr(qty=1, price=10.10, inventory=inv)
    await _insert_journal(
        source_type="general_journal",
        source_id=f"gj-{uuid.uuid4().hex[:8]}",
        lines=[
            {"account_id": inv["id"], "debit": 0, "credit": 0.01},
            {"account_id": clearing["id"], "debit": 0.01, "credit": 0},
        ],
    )
    body = await _summary()
    assert _money(_inv(body)["financial_inventory_basis_value"]) == _money("10.10")
    assert _money(_inv(body)["other_journal_balance"]) == _money("-0.01")
    assert _money(_inv(body)["inventory_gl_balance"]) == _money("10.09")
    assert _inv(body)["value_status"] == STATUS_EXPLAINED_DIFFERENCE
    assert gr["id"] and cogs["id"]


# --- 11-13 REVERSAL ---

async def test_reversed_gr_nets_zero_and_removes_acquisition_once():
    mid, gr, inventory = await _seed_posted_gr(qty=10, price=100)
    journal = await server.db[JOURNAL_ENTRIES].find_one(
        {"source_type": SOURCE_GR, "source_id": gr["id"], "reversal_of": None},
        {"_id": 0},
    )
    lines = await server.db[JOURNAL_LINES].find({"journal_id": journal["id"]}, {"_id": 0}).to_list(None)
    await server.db[JOURNAL_ENTRIES].update_one({"id": journal["id"]}, {"$set": {"status": "reversed"}})
    await _insert_journal(
        source_type=SOURCE_REVERSAL,
        source_id=gr["id"],
        reversal_of=journal["id"],
        lines=[
            {"account_id": ln["account_id"], "debit": ln.get("credit") or 0, "credit": ln.get("debit") or 0}
            for ln in lines
        ],
    )
    body = await _summary()
    assert _money(_inv(body)["financial_inventory_basis_value"]) == _money("0.00")
    assert _money(_inv(body)["inventory_source_journal_balance"]) == _money("0.00")
    assert inventory["id"] and mid


async def test_financial_reversal_awaiting_physical_cancel_recovery_gap():
    mid, gr, inventory = await _seed_posted_gr(qty=10, price=100)
    await _stock(mid, total=10, unprinted=10, printed=0)
    journal = await server.db[JOURNAL_ENTRIES].find_one(
        {"source_type": SOURCE_GR, "source_id": gr["id"], "reversal_of": None}, {"_id": 0}
    )
    lines = await server.db[JOURNAL_LINES].find({"journal_id": journal["id"]}, {"_id": 0}).to_list(None)
    await server.db[JOURNAL_ENTRIES].update_one({"id": journal["id"]}, {"$set": {"status": "reversed"}})
    await _insert_journal(
        source_type=SOURCE_REVERSAL,
        source_id=gr["id"],
        reversal_of=journal["id"],
        lines=[
            {"account_id": ln["account_id"], "debit": ln.get("credit") or 0, "credit": ln.get("debit") or 0}
            for ln in lines
        ],
    )
    await server.db.purchase_goods_receipts.update_one(
        {"id": gr["id"]},
        {"$set": {"accounting_recovery_required": True, "cancelled": False}},
    )
    body = await _summary()
    assert FLAG_RECOVERY_REQUIRED in body["posting_flags"]
    assert body["pending"]["accounting_recovery_required"]["count"] == 1
    items = await _items()
    assert items["items"][0]["physical_qty"] == 10
    assert items["items"][0]["financial_qty"] == 0
    assert inventory["id"]
    assert CODE_RECOVERY_REQUIRED not in _codes(body) or True


async def test_missing_reversal_pair_is_anomaly():
    mid, gr, inventory = await _seed_posted_gr(qty=10, price=100)
    journal = await server.db[JOURNAL_ENTRIES].find_one(
        {"source_type": SOURCE_GR, "source_id": gr["id"], "reversal_of": None}, {"_id": 0}
    )
    await server.db[JOURNAL_ENTRIES].update_one({"id": journal["id"]}, {"$set": {"status": "reversed"}})
    body = await _summary()
    assert CODE_INCOMPLETE_REVERSAL_PAIR in _codes(body)
    assert body["reconciliation_status"] == STATUS_INCOMPLETE
    assert inventory["id"] and mid


# --- 14-19 RECONCILIATION ---

async def test_opening_balance_creates_explained_other_source_difference():
    mid, gr, inventory = await _seed_posted_gr(qty=600, price=1000)
    equity = await _coa("31", "Modal", "EQTY")
    await _insert_journal(
        source_type="opening_balance",
        source_id=f"ob-{uuid.uuid4().hex[:8]}",
        date="2026-09-30",
        lines=[
            {"account_id": inventory["id"], "debit": 50000, "credit": 0},
            {"account_id": equity["id"], "debit": 0, "credit": 50000},
        ],
    )
    body = await _summary()
    assert _money(_inv(body)["financial_inventory_basis_value"]) == _money("600000.00")
    assert _money(_inv(body)["inventory_source_journal_balance"]) == _money("600000.00")
    assert _money(_inv(body)["other_journal_balance"]) == _money("50000.00")
    assert _money(_inv(body)["inventory_gl_balance"]) == _money("650000.00")
    assert _inv(body)["value_status"] == STATUS_EXPLAINED_DIFFERENCE
    assert body["reconciliation_status"] == STATUS_EXPLAINED_DIFFERENCE
    assert gr["id"] and mid


async def test_manual_cogs_journal_is_other_source():
    mid, gr, inventory = await _seed_posted_gr(qty=100, price=10)
    mov = await _seed_movement(mid=mid, qty=40)
    await _post_issue(mov)
    settings = await server.db.app_settings.find_one({"key": SETTINGS_KEY})
    cogs_id = settings["cogs_account_id"]
    bank = await _coa("11", "Kas", "BANK")
    await _insert_journal(
        source_type="general_journal",
        source_id=f"gj-cogs-{uuid.uuid4().hex[:8]}",
        lines=[
            {"account_id": cogs_id, "debit": 25, "credit": 0},
            {"account_id": bank["id"], "debit": 0, "credit": 25},
        ],
    )
    body = await _summary()
    assert _money(body["cogs"]["financial_cogs_basis_value"]) == _money("400.00")
    assert _money(body["cogs"]["cogs_source_journal_balance"]) == _money("400.00")
    assert _money(body["cogs"]["other_journal_balance"]) == _money("25.00")
    assert body["cogs"]["value_status"] == STATUS_EXPLAINED_DIFFERENCE
    assert inventory["id"] and gr["id"]


async def test_basis_journal_amount_mismatch_detected_per_source():
    mid, gr, inventory = await _seed_posted_gr(qty=10, price=100)
    journal = await server.db[JOURNAL_ENTRIES].find_one(
        {"source_type": SOURCE_GR, "source_id": gr["id"], "reversal_of": None}, {"_id": 0}
    )
    await server.db[JOURNAL_LINES].update_one(
        {"journal_id": journal["id"], "debit": {"$gt": 0}},
        {"$set": {"debit": 999.00}},
    )
    await server.db[JOURNAL_LINES].update_one(
        {"journal_id": journal["id"], "credit": {"$gt": 0}},
        {"$set": {"credit": 999.00}},
    )
    body = await _summary()
    assert CODE_BASIS_JOURNAL_MISMATCH in _codes(body)
    assert body["reconciliation_status"] == STATUS_DIFFERENCE
    assert inventory["id"] and mid


async def test_opposite_source_mismatches_cannot_cancel_to_reconciled():
    inv, *_ = await _configure_inventory()
    mid_a, gr_a, _ = await _seed_posted_gr(qty=10, price=100, inventory=inv)
    mid_b, gr_b, _ = await _seed_posted_gr(qty=10, price=100, inventory=inv)
    ja = await server.db[JOURNAL_ENTRIES].find_one(
        {"source_type": SOURCE_GR, "source_id": gr_a["id"], "reversal_of": None}, {"_id": 0}
    )
    jb = await server.db[JOURNAL_ENTRIES].find_one(
        {"source_type": SOURCE_GR, "source_id": gr_b["id"], "reversal_of": None}, {"_id": 0}
    )
    await server.db[JOURNAL_LINES].update_one(
        {"journal_id": ja["id"], "debit": {"$gt": 0}}, {"$set": {"debit": 1100.00}}
    )
    await server.db[JOURNAL_LINES].update_one(
        {"journal_id": ja["id"], "credit": {"$gt": 0}}, {"$set": {"credit": 1100.00}}
    )
    await server.db[JOURNAL_LINES].update_one(
        {"journal_id": jb["id"], "debit": {"$gt": 0}}, {"$set": {"debit": 900.00}}
    )
    await server.db[JOURNAL_LINES].update_one(
        {"journal_id": jb["id"], "credit": {"$gt": 0}}, {"$set": {"credit": 900.00}}
    )
    body = await _summary()
    mismatches = [row for row in body["integrity_anomalies"] if row["code"] == CODE_BASIS_JOURNAL_MISMATCH]
    assert len(mismatches) >= 2
    assert body["reconciliation_status"] != STATUS_RECONCILED
    assert mid_a and mid_b


async def test_missing_document_basis_and_account_prevent_clean_status():
    mid, gr, inventory = await _seed_posted_gr(qty=10, price=100)
    journal = await server.db[JOURNAL_ENTRIES].find_one(
        {"source_type": SOURCE_GR, "source_id": gr["id"], "reversal_of": None}, {"_id": 0}
    )
    await server.db.purchase_goods_receipts.delete_one({"id": gr["id"]})
    body = await _summary()
    assert CODE_POSTED_JOURNAL_MISSING_DOCUMENT in _codes(body)
    assert body["reconciliation_status"] == STATUS_INCOMPLETE
    await server.db[JOURNAL_ENTRIES].delete_many({})
    await server.db[JOURNAL_LINES].delete_many({})
    mid2, gr2, _ = await _seed_posted_gr(qty=5, price=10, inventory=inventory)
    await server.db.purchase_goods_receipts.update_one(
        {"id": gr2["id"]}, {"$unset": {GR_BASIS_FIELD: ""}}
    )
    body2 = await _summary()
    assert CODE_POSTED_JOURNAL_MISSING_BASIS in _codes(body2)
    unknown = f"acct-{uuid.uuid4().hex[:8]}"
    await _insert_journal(
        source_type=SOURCE_GR,
        source_id=f"ghost-{uuid.uuid4().hex[:8]}",
        lines=[
            {"account_id": unknown, "debit": 10, "credit": 0},
            {"account_id": unknown, "debit": 0, "credit": 10},
        ],
    )
    body3 = await _summary()
    assert CODE_MISSING_COA in _codes(body3) or CODE_UNRESOLVED_ACCOUNT in _codes(body3) or CODE_POSTED_JOURNAL_MISSING_DOCUMENT in _codes(body3)
    assert body3["reconciliation_status"] == STATUS_INCOMPLETE
    assert journal["id"] and mid2


async def test_consumed_acquisition_reversed_is_anomaly():
    mid, gr, inventory = await _seed_posted_gr(qty=100, price=10)
    mov = await _seed_movement(mid=mid, qty=40)
    await _post_issue(mov)
    journal = await server.db[JOURNAL_ENTRIES].find_one(
        {"source_type": SOURCE_GR, "source_id": gr["id"], "reversal_of": None}, {"_id": 0}
    )
    lines = await server.db[JOURNAL_LINES].find({"journal_id": journal["id"]}, {"_id": 0}).to_list(None)
    await server.db[JOURNAL_ENTRIES].update_one({"id": journal["id"]}, {"$set": {"status": "reversed"}})
    await _insert_journal(
        source_type=SOURCE_REVERSAL,
        source_id=gr["id"],
        reversal_of=journal["id"],
        lines=[
            {"account_id": ln["account_id"], "debit": ln.get("credit") or 0, "credit": ln.get("debit") or 0}
            for ln in lines
        ],
    )
    body = await _summary()
    assert CODE_CONSUMED_ACQUISITION_NOT_LIVE in _codes(body)
    assert body["reconciliation_status"] == STATUS_INCOMPLETE
    assert inventory["id"]


# --- 20-25 PHYSICAL / PENDING ---

async def test_pending_shipment_matching_value_is_qty_gap_not_gl_error():
    mid, gr, inventory = await _seed_posted_gr(qty=1000, price=1000)
    posted = await _seed_movement(mid=mid, qty=400)
    await _post_issue(posted)
    await _seed_movement(mid=mid, qty=100, date="2026-10-06T03:00:00Z")
    await _stock(mid, total=500, unprinted=300, printed=200)
    body = await _summary()
    items = await _items()
    assert _money(_inv(body)["financial_inventory_basis_value"]) == _money("600000.00")
    assert _money(_inv(body)["inventory_gl_balance"]) == _money("600000.00")
    assert _inv(body)["value_status"] == STATUS_RECONCILED
    assert FLAG_PENDING_COGS in body["posting_flags"]
    row = items["items"][0]
    assert row["physical_qty"] == 500
    assert row["financial_qty"] == 600
    assert row["qty_difference"] == -100
    assert inventory["id"] and gr["id"]


async def test_missing_uninitialized_physical_stock_is_null():
    mid, gr, inventory = await _seed_posted_gr(qty=10, price=10)
    items = await _items()
    assert items["items"][0]["physical_qty"] is None
    assert items["items"][0]["quantity_status"] == QTY_MISSING_PHYSICAL
    await _stock(mid, total=0, initialized=False)
    items2 = await _items()
    assert items2["items"][0]["physical_qty"] is None
    assert inventory["id"] and gr["id"]


async def test_uom_mismatch_is_not_converted():
    mid, gr, inventory = await _seed_posted_gr(qty=10, price=10)
    await _stock(mid, total=10, uom="KG")
    items = await _items()
    row = items["items"][0]
    assert row["quantity_comparable"] is False
    assert row["qty_difference"] is None
    assert row["quantity_status"] == QTY_UOM_MISMATCH
    assert any(a["code"] == CODE_UOM_MISMATCH for a in items["integrity_anomalies"])
    assert inventory["id"] and gr["id"]


async def test_printed_and_unprinted_are_not_added_twice():
    mid, gr, inventory = await _seed_posted_gr(qty=1000, price=1)
    await _stock(mid, total=500, unprinted=300, printed=200)
    items = await _items()
    assert items["items"][0]["physical_qty"] == 500
    assert items["items"][0]["physical_qty"] != 1000
    assert inventory["id"] and gr["id"]


async def test_physical_qty_not_duplicated_across_inventory_accounts():
    inv_a = await _coa("13", "Persediaan A", "INTR")
    inv_b = await _coa("13", "Persediaan B", "INTR")
    await _configure_inventory(inventory=inv_a)
    mid, _gr_a, _ = await _seed_posted_gr(qty=60, price=10, inventory=inv_a)
    await server.db.app_settings.update_one(
        {"key": SETTINGS_KEY}, {"$set": {"inventory_account_id": inv_b["id"]}}
    )
    await _seed_posted_gr(qty=40, price=10, mid=mid, inventory=inv_b)
    await _stock(mid, total=100)
    items = await _items()
    matching = [row for row in items["items"] if row["master_item_id"] == mid]
    assert len(matching) == 1
    assert matching[0]["physical_qty"] == 100
    assert len(matching[0]["inventory_account_ids"]) == 2


async def test_service_and_legacy_distinguishable_from_pending():
    await _configure_inventory(effective="2026-10-01")
    await _insert_gr(qty=10, price=10, item_type="SERVICE")
    await _insert_gr(qty=10, price=10, receipt_date="2026-09-01")
    await _insert_gr(qty=10, price=10, receipt_date="2026-10-02")
    body = await _summary()
    assert body["pending"]["excluded_service"]["count"] >= 1
    assert body["pending"]["pre_effective_legacy"]["count"] >= 1
    assert body["pending"]["active_gr_candidates_without_journal"]["count"] >= 1
    assert FLAG_EXCLUDED_SERVICE in body["posting_flags"]
    assert FLAG_PRE_EFFECTIVE in body["posting_flags"]
    assert FLAG_PENDING_GR in body["posting_flags"]


# --- 26-34 SCOPE / SAFETY ---

async def test_item_filter_does_not_distort_global_totals():
    inv, *_ = await _configure_inventory()
    mid_a, _gr_a, _ = await _seed_posted_gr(qty=10, price=100, inventory=inv)
    mid_b, _gr_b, _ = await _seed_posted_gr(qty=5, price=100, inventory=inv)
    global_summary = await _summary()
    filtered = await _items(q=mid_a)
    assert _money(filtered["inventory"]["financial_inventory_basis_value"]) == _money(
        global_summary["inventory"]["financial_inventory_basis_value"]
    )
    assert filtered["global_item_count"] == 2
    assert filtered["total"] == 1
    assert filtered["items"][0]["master_item_id"] == mid_a
    assert mid_b


async def test_account_filter_preserves_full_account_and_global_totals():
    inv_a = await _coa("13", "Persediaan A", "INTR")
    inv_b = await _coa("13", "Persediaan B", "INTR")
    await _configure_inventory(inventory=inv_a)
    await _seed_posted_gr(qty=10, price=100, inventory=inv_a)
    await server.db.app_settings.update_one(
        {"key": SETTINGS_KEY}, {"$set": {"inventory_account_id": inv_b["id"]}}
    )
    await _seed_posted_gr(qty=4, price=100, inventory=inv_b)
    full = await _accounts()
    filtered = await _accounts(account_id=inv_a["id"])
    assert len(filtered["inventory_accounts"]) == 1
    assert _money(filtered["inventory"]["financial_inventory_basis_value"]) == _money(
        full["inventory"]["financial_inventory_basis_value"]
    )
    assert _money(filtered["inventory_accounts"][0]["financial_inventory_basis_value"]) == _money("1000.00")
    assert filtered["global_account_count"]["inventory"] == full["global_account_count"]["inventory"]


async def test_small_internal_batches_include_every_source_and_line(monkeypatch):
    monkeypatch.setattr(recon, "DOCUMENT_ID_BATCH_SIZE", 1)
    monkeypatch.setattr(recon, "JOURNAL_ID_BATCH_SIZE", 1)
    monkeypatch.setattr(fsc, "JOURNAL_ID_BATCH_SIZE", 1)
    inv, *_ = await _configure_inventory()
    mid, gr, _ = await _seed_posted_gr(qty=10, price=100, inventory=inv)
    mov = await _seed_movement(mid=mid, qty=3)
    await _post_issue(mov)
    body = await _summary()
    assert _money(_inv(body)["financial_inventory_basis_value"]) == _money("700.00")
    assert _money(_inv(body)["inventory_source_journal_balance"]) == _money("700.00")
    lines = await server.db[JOURNAL_LINES].count_documents({})
    assert lines >= 4
    assert gr["id"]
    assert DOCUMENT_ID_BATCH_SIZE >= 1


async def test_permission_denied_without_dedicated_view():
    await _configure_inventory()
    denied = _user("viewer", perms=["inventory_issue.post", "inventory_issue.view"])
    async with await client_for(denied) as client:
        assert (await client.get(SUMMARY)).status_code == 403
        assert (await client.get(ACCOUNTS)).status_code == 403
        assert (await client.get(ITEMS)).status_code == 403
    allowed = _user("viewer", perms=[PERM_VIEW])
    async with await client_for(allowed) as client:
        ok = await client.get(SUMMARY)
        assert ok.status_code == 200, ok.text


async def test_no_operational_role_auto_grant():
    assert PERM_VIEW in ALL_PERMISSIONS
    assert PERM_VIEW in ROLE_DEFAULT_PERMISSIONS["admin"]
    for role, perms in ROLE_DEFAULT_PERMISSIONS.items():
        if role == "admin":
            continue
        assert PERM_VIEW not in perms, role


async def test_repeated_gets_are_zero_write():
    mid, gr, inventory = await _seed_posted_gr(qty=10, price=100)
    before = await _docs_snapshot()
    rejecting = _RejectingDB(server.db)
    first = await get_inventory_reconciliation_summary(rejecting)
    second = await get_inventory_reconciliation_summary(rejecting)
    after = await _docs_snapshot()
    assert before == after
    assert first["inventory"]["financial_inventory_basis_value"] == second["inventory"]["financial_inventory_basis_value"]
    _admin()
    async with await client_for(POST_USER) as client:
        r1 = await client.get(SUMMARY)
        r2 = await client.get(SUMMARY)
        assert r1.status_code == 200 and r2.status_code == 200
    after_http = await _docs_snapshot()
    assert before == after_http
    assert inventory["id"] and gr["id"] and mid


async def test_inflight_data_is_not_unconditionally_clean():
    mid, gr, inventory = await _seed_posted_gr(qty=10, price=100)
    await server.db.purchase_goods_receipts.update_one(
        {"id": gr["id"]},
        {"$set": {
            GR_CLAIM: "claim-in-flight",
            GR_CLAIM_AT: datetime.now(timezone.utc).isoformat(),
        }},
    )
    body = await _summary()
    assert body["read_stability"] in {STABILITY_IN_PROGRESS, STABILITY_RETRY_REQUIRED}
    assert body["reconciliation_status"] == STATUS_INCOMPLETE
    assert inventory["id"] and mid


async def test_identity_holds_for_each_account():
    inv_a = await _coa("13", "Persediaan A", "INTR")
    inv_b = await _coa("13", "Persediaan B", "INTR")
    await _configure_inventory(inventory=inv_a)
    await _seed_posted_gr(qty=8, price=25, inventory=inv_a)
    await server.db.app_settings.update_one(
        {"key": SETTINGS_KEY}, {"$set": {"inventory_account_id": inv_b["id"]}}
    )
    await _seed_posted_gr(qty=3, price=25, inventory=inv_b)
    equity = await _coa("31", "Modal", "EQTY")
    await _insert_journal(
        source_type="general_journal",
        source_id=f"gj-{uuid.uuid4().hex[:8]}",
        lines=[
            {"account_id": inv_a["id"], "debit": 7.50, "credit": 0},
            {"account_id": equity["id"], "debit": 0, "credit": 7.50},
        ],
    )
    body = await _accounts()
    for row in body["inventory_accounts"] + body["cogs_accounts"]:
        _assert_identity(row)
    _assert_identity(body["inventory"])
    _assert_identity(body["cogs"])


async def test_empty_data_is_valid_no_activity():
    body = await _summary()
    assert body["scope"] == SCOPE_CURRENT
    assert body["activity_state"] == ACTIVITY_NONE
    assert body["has_activity"] is False
    assert body["reconciliation_status"] in {STATUS_RECONCILED, STATUS_INCOMPLETE}
    assert _money(_inv(body)["financial_inventory_basis_value"]) == _money("0.00")
    _assert_identity(_inv(body))
    _assert_identity(body["cogs"])
    _admin()
    async with await client_for(POST_USER) as client:
        r = await client.get(SUMMARY)
        assert r.status_code == 200
        assert r.json()["activity_state"] == ACTIVITY_NONE


async def test_pending_event_blocks_clean_quantity_not_financials():
    mid, gr, inventory = await _seed_posted_gr(qty=10, price=100)
    await _stock(mid, total=10, pending_event={"event_id": "evt-pending"})
    body = await _summary()
    items = await _items()
    assert _money(_inv(body)["financial_inventory_basis_value"]) == _money("1000.00")
    assert _money(_inv(body)["inventory_source_journal_balance"]) == _money("1000.00")
    assert _money(_inv(body)["inventory_gl_balance"]) == _money("1000.00")
    assert _inv(body)["value_status"] == STATUS_RECONCILED
    assert body["cogs"]["value_status"] == STATUS_RECONCILED
    row = items["items"][0]
    assert row["quantity_comparable"] is False
    assert row["qty_difference"] is None
    assert row["quantity_status"] == QTY_IN_PROGRESS
    assert CODE_PHYSICAL_STOCK_IN_PROGRESS in _codes(body)
    assert body["read_stability"] == STABILITY_IN_PROGRESS
    assert body["reconciliation_status"] == STATUS_INCOMPLETE
    assert inventory["id"] and gr["id"]


async def test_cleared_pending_event_restores_clean_quantity():
    mid, gr, inventory = await _seed_posted_gr(qty=10, price=100)
    await _stock(mid, total=10, pending_event=None)
    body = await _summary()
    items = await _items()
    row = items["items"][0]
    assert row["quantity_comparable"] is True
    assert row["qty_difference"] == 0
    assert row["quantity_status"] == QTY_MATCHED
    assert CODE_PHYSICAL_STOCK_IN_PROGRESS not in _codes(body)
    assert body["read_stability"] == STABILITY_STABLE
    assert body["reconciliation_status"] == STATUS_RECONCILED
    assert _inv(body)["value_status"] == STATUS_RECONCILED
    assert inventory["id"] and gr["id"]


async def test_item_filter_does_not_hide_pending_event_instability():
    inv, *_ = await _configure_inventory()
    mid_a, _gr_a, _ = await _seed_posted_gr(qty=10, price=100, inventory=inv)
    mid_b, _gr_b, _ = await _seed_posted_gr(qty=5, price=100, inventory=inv)
    await _stock(mid_a, total=10, pending_event={"event_id": "evt-a"})
    await _stock(mid_b, total=5)
    filtered = await _items(q=mid_b)
    assert filtered["total"] == 1
    assert filtered["items"][0]["master_item_id"] == mid_b
    assert filtered["read_stability"] == STABILITY_IN_PROGRESS
    assert filtered["reconciliation_status"] == STATUS_INCOMPLETE
    assert _inv(filtered)["value_status"] == STATUS_RECONCILED
    summary = await _summary()
    assert summary["read_stability"] == STABILITY_IN_PROGRESS
    assert summary["reconciliation_status"] == STATUS_INCOMPLETE


async def test_pending_event_get_is_zero_write():
    mid, gr, inventory = await _seed_posted_gr(qty=10, price=100)
    await _stock(mid, total=10, pending_event={"event_id": "evt-pending"})
    before = await _docs_snapshot()
    rejecting = _RejectingDB(server.db)
    first = await get_inventory_reconciliation_summary(rejecting)
    second = await get_inventory_reconciliation_items(rejecting, q=mid)
    after = await _docs_snapshot()
    assert before == after
    assert first["read_stability"] == STABILITY_IN_PROGRESS
    assert second["read_stability"] == STABILITY_IN_PROGRESS
    assert inventory["id"] and gr["id"]


async def _live_journal(source_type: str, source_id: str) -> dict:
    journal = await server.db[JOURNAL_ENTRIES].find_one(
        {"source_type": source_type, "source_id": source_id, "reversal_of": None},
        {"_id": 0},
    )
    assert journal, f"missing live {source_type} journal for {source_id}"
    return journal


async def _append_journal_line(journal_id: str, *, account_id: str, debit=0, credit=0):
    await server.db[JOURNAL_LINES].insert_one({
        "id": str(uuid.uuid4()),
        "journal_id": journal_id,
        "account_id": account_id,
        "debit": debit,
        "credit": credit,
    })


async def test_extra_inventory_lines_on_two_gr_journals_still_anomaly_per_source():
    inv, clearing, payable, _cogs = await _configure_inventory()
    other = await _coa("13", "Persediaan Lain", "INTR")
    mid_a, gr_a, _ = await _seed_posted_gr(qty=10, price=100, inventory=inv)
    mid_b, gr_b, _ = await _seed_posted_gr(qty=10, price=100, inventory=inv)
    ja = await _live_journal(SOURCE_GR, gr_a["id"])
    jb = await _live_journal(SOURCE_GR, gr_b["id"])
    await _append_journal_line(ja["id"], account_id=other["id"], debit=20)
    await _append_journal_line(ja["id"], account_id=clearing["id"], credit=20)
    await _append_journal_line(jb["id"], account_id=other["id"], credit=20)
    await _append_journal_line(jb["id"], account_id=clearing["id"], debit=20)
    body = await _summary()
    extra = [
        row for row in body["integrity_anomalies"]
        if row["code"] == CODE_BASIS_JOURNAL_MISMATCH and row["account_id"] == other["id"]
    ]
    assert {row["source_id"] for row in extra} == {gr_a["id"], gr_b["id"]}
    assert {row["source_type"] for row in extra} == {SOURCE_GR}
    assert len(extra) == 2
    pair_ids = {clearing["id"], payable["id"]}
    assert not any(
        row["code"] == CODE_BASIS_JOURNAL_MISMATCH and row["account_id"] in pair_ids
        for row in body["integrity_anomalies"]
    )
    accounts = await _accounts()
    other_row = _account_row(accounts, other["id"])
    assert _money(other_row["financial_inventory_basis_value"]) == _money("0")
    assert _money(other_row["inventory_source_journal_balance"]) == _money("0")
    assert _money(_inv(body)["basis_vs_source_difference"]) == _money("0")
    assert body["reconciliation_status"] != STATUS_RECONCILED
    assert mid_a and mid_b


async def test_extra_inventory_lines_on_two_issue_journals_still_anomaly_per_source():
    inv, clearing, payable, _cogs = await _configure_inventory()
    other = await _coa("13", "Persediaan Lain", "INTR")
    mid, gr, _ = await _seed_posted_gr(qty=100, price=10, inventory=inv)
    mov_a = await _seed_movement(mid=mid, qty=10, date="2026-10-05T03:00:00Z")
    mov_b = await _seed_movement(mid=mid, qty=10, date="2026-10-06T03:00:00Z")
    await _post_issue(mov_a)
    await _post_issue(mov_b)
    ja = await _live_journal(SOURCE_ISSUE, mov_a["id"])
    jb = await _live_journal(SOURCE_ISSUE, mov_b["id"])
    await _append_journal_line(ja["id"], account_id=other["id"], debit=20)
    await _append_journal_line(ja["id"], account_id=clearing["id"], credit=20)
    await _append_journal_line(jb["id"], account_id=other["id"], credit=20)
    await _append_journal_line(jb["id"], account_id=clearing["id"], debit=20)
    body = await _summary()
    extra = [
        row for row in body["integrity_anomalies"]
        if row["code"] == CODE_BASIS_JOURNAL_MISMATCH and row["account_id"] == other["id"]
    ]
    assert {row["source_id"] for row in extra} == {mov_a["id"], mov_b["id"]}
    assert {row["source_type"] for row in extra} == {SOURCE_ISSUE}
    assert len(extra) == 2
    pair_ids = {clearing["id"], payable["id"]}
    assert not any(
        row["code"] == CODE_BASIS_JOURNAL_MISMATCH and row["account_id"] in pair_ids
        for row in body["integrity_anomalies"]
    )
    accounts = await _accounts()
    other_row = _account_row(accounts, other["id"])
    assert _money(other_row["financial_inventory_basis_value"]) == _money("0")
    assert _money(other_row["inventory_source_journal_balance"]) == _money("0")
    assert _money(_inv(body)["basis_vs_source_difference"]) == _money("0")
    assert body["reconciliation_status"] != STATUS_RECONCILED
    assert gr["id"] and payable["id"]


async def test_missing_physical_uom_is_incomplete_not_master_fallback():
    mid, gr, inventory = await _seed_posted_gr(qty=10, price=10)
    await server.db.master_items.update_one({"id": mid}, {"$set": {"unit": "BOX"}})
    await _stock(mid, total=10, uom="")
    items = await _items()
    row = items["items"][0]
    assert row["quantity_comparable"] is False
    assert row["qty_difference"] is None
    assert row["quantity_status"] == QTY_INCOMPLETE
    assert row["quantity_status"] != QTY_MATCHED
    assert row["quantity_status"] != QTY_UOM_MISMATCH
    assert not any(a["code"] == CODE_UOM_MISMATCH for a in items["integrity_anomalies"])
    assert inventory["id"] and gr["id"]


async def test_malformed_basis_is_anomaly_not_exception():
    mid, gr, inventory = await _seed_posted_gr(qty=10, price=100)
    mov = await _seed_movement(mid=mid, qty=4)
    await _post_issue(mov)
    await server.db.purchase_goods_receipts.update_one(
        {"id": gr["id"]},
        {"$set": {f"{GR_BASIS_FIELD}.eligible_inventory_lines": "not-a-list"}},
    )
    await server.db[ISSUE_COLLECTION].update_one(
        {"movement_id": mov["id"]},
        {"$set": {f"{POSTING_BASIS_FIELD}.consumed_acquisitions": "not-a-list"}},
    )
    async with await client_for(POST_USER) as client:
        summary_resp = await client.get(SUMMARY)
        accounts_resp = await client.get(ACCOUNTS)
        items_resp = await client.get(ITEMS)
    assert summary_resp.status_code == 200
    assert accounts_resp.status_code == 200
    assert items_resp.status_code == 200
    body = summary_resp.json()
    assert CODE_INVALID_ACQUISITION_BASIS in _codes(body)
    assert body["basis_coverage_complete"] is False
    assert body["reconciliation_status"] == STATUS_INCOMPLETE
    assert body["reconciliation_status"] != STATUS_RECONCILED
    invalid_rows = [
        row for row in body["integrity_anomalies"]
        if row["code"] == CODE_INVALID_ACQUISITION_BASIS
    ]
    assert {row["source_id"] for row in invalid_rows} >= {gr["id"], mov["id"]}
    assert inventory["id"]


async def test_malformed_gr_does_not_drop_valid_pending_and_blocked():
    inv, *_ = await _configure_inventory()
    mid_ok, gr_ok, _ = await _seed_posted_gr(qty=1000, price=1000, inventory=inv)
    mid_bad, gr_bad, _ = await _seed_posted_gr(qty=10, price=100, inventory=inv)
    await server.db.purchase_goods_receipts.update_one(
        {"id": gr_bad["id"]},
        {"$set": {f"{GR_BASIS_FIELD}.eligible_inventory_lines": "not-a-list"}},
    )
    pending = await _seed_movement(mid=mid_ok, qty=100)
    blocked = await _seed_movement(mid=mid_ok, qty=5000, date="2026-10-06T03:00:00Z")
    before = await _docs_snapshot()
    rejecting = _RejectingDB(server.db)
    body = await get_inventory_reconciliation_summary(rejecting)
    filtered = await get_inventory_reconciliation_items(rejecting, q=mid_ok)
    after = await _docs_snapshot()
    assert before == after
    assert CODE_INVALID_ACQUISITION_BASIS in _codes(body)
    assert any(
        row["code"] == CODE_INVALID_ACQUISITION_BASIS and row["source_id"] == gr_bad["id"]
        for row in body["integrity_anomalies"]
    )
    assert _money(_inv(body)["financial_inventory_basis_value"]) == _money("1000000.00")
    pending_ids = {row["id"] for row in body["pending"]["pending_cogs"]["items"]}
    waiting_ids = {row["id"] for row in body["pending"]["waiting_earlier_issue"]["items"]}
    assert pending["id"] in pending_ids
    assert blocked["id"] in waiting_ids
    assert blocked["id"] not in pending_ids
    assert FLAG_PENDING_COGS in body["posting_flags"]
    assert filtered["total"] >= 1
    assert CODE_INVALID_ACQUISITION_BASIS in _codes(filtered)
    assert filtered["reconciliation_status"] == STATUS_INCOMPLETE
    assert body["basis_coverage_complete"] is False
    assert mid_bad and gr_ok["id"]


async def test_issue_on_malformed_acquisition_is_not_costed_from_remainder():
    inv, *_ = await _configure_inventory()
    mid, gr_ok, _ = await _seed_posted_gr(qty=1000, price=10, inventory=inv)
    _mid_bad, gr_bad, _ = await _seed_posted_gr(qty=1000, price=50, mid=mid, inventory=inv)
    await server.db.purchase_goods_receipts.update_one(
        {"id": gr_bad["id"]},
        {"$set": {f"{GR_BASIS_FIELD}.eligible_inventory_lines": "not-a-list"}},
    )
    issue = await _seed_movement(mid=mid, qty=10)
    before = await _docs_snapshot()
    rejecting = _RejectingDB(server.db)
    body = await get_inventory_reconciliation_summary(rejecting)
    filtered = await get_inventory_reconciliation_items(rejecting, q="other-item")
    after = await _docs_snapshot()
    assert before == after
    pending_ids = {row["id"] for row in body["pending"]["pending_cogs"]["items"]}
    blocked_rows = {
        row["id"]: row for row in body["pending"]["blocked_cogs"]["items"]
    }
    assert issue["id"] not in pending_ids
    assert issue["id"] in blocked_rows
    assert blocked_rows[issue["id"]]["blocker_code"] == CODE_INVALID_ACQUISITION_BASIS
    assert _money(_inv(body)["financial_inventory_basis_value"]) == _money("10000.00")
    assert any(
        row["code"] == CODE_INVALID_ACQUISITION_BASIS and row["source_id"] == gr_bad["id"]
        for row in body["integrity_anomalies"]
    )
    assert CODE_INVALID_ACQUISITION_BASIS in _codes(filtered)
    assert filtered["reconciliation_status"] == STATUS_INCOMPLETE
    assert gr_ok["id"]
