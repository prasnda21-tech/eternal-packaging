"""Financial inventory opening: book value, exclusions, COGS, and recovery.

Uses an isolated database. Does not activate production dates or backfill.
"""
from __future__ import annotations

import asyncio
import os
import sys
import uuid
from decimal import Decimal
from pathlib import Path

import httpx
import pytest
from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("MONGO_URL", "mongodb://127.0.0.1:27017")
os.environ["DB_NAME"] = "eternal_packaging_financial_opening_test"
FINANCIAL_OPENING_TEST_DB = os.environ["DB_NAME"]
os.environ.setdefault("JWT_SECRET", "test-secret-key-12345678901234567890")
os.environ.setdefault("CORS_ORIGINS", "*")

import server
from server import app, current_user
from services.accounting_periods import (
    PeriodConflictError,
    close_period,
    ensure_indexes as ensure_periods,
)
from services.chart_of_accounts import create_account, ensure_indexes as ensure_coa
from services.financial_inventory_opening import (
    OpeningBlocked,
    SOURCE_TYPE,
    build_financial_book_reconciliation,
    preview_financial_opening,
    post_financial_opening,
    recover_financial_opening,
    reverse_financial_opening,
    wib_business_date,
)
from services.hpp_cutover import proven_opening_physical_qty
from services.physical_stock_engine import (
    claim_durable_stock_event,
    finalize_durable_stock_event,
    reconcile_pending_product_claim,
)
from services.product_stock_state import correct_opening_balance, initialize_opening_balance
from services.stock_engine_v2 import apply_stock_opname
from services.stock_ledger import MOVEMENT_KOREKSI, MOVEMENT_PENERIMAAN, MOVEMENT_PENGIRIMAN, MOVEMENT_SALDO_AWAL
import services.physical_stock_engine as stock_engine
import services.stock_engine_v2 as stock_engine_v2
from services.hpp_cutover import ensure_indexes as ensure_hpp
from services.inventory_issue_accounting import (
    POSTING_BASIS_FIELD,
    load_live_acquisition_coverage,
)
from services.inventory_issue_posting import post_inventory_issue_accounting
from services.inventory_valuation import build_inventory_valuation_rows, export_xlsx, filter_sort_all
from services.journal_core import JOURNAL_ENTRIES, JOURNAL_LINES, ensure_indexes as ensure_journal
from services.opening_balance import (
    OpeningBalanceConflictError,
    create_draft,
    post_voucher,
    reverse_voucher,
)
from services.purchase_accounting_settings import SETTINGS_KEY
from services.purchase_goods_receipt_posting import post_purchase_goods_receipt_accounting
from services.purchase_goods_receipts import create_purchase_goods_receipt

USER = {"id": "admin-fio", "email": "admin@eternalpackaging.com", "name": "Admin", "role": "admin"}
EFFECTIVE = "2026-10-01"
pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture(autouse=True)
def bind_motor():
    from motor.motor_asyncio import AsyncIOMotorClient

    os.environ["DB_NAME"] = FINANCIAL_OPENING_TEST_DB
    server.client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    server.db = server.client[os.environ["DB_NAME"]]


@pytest.fixture(autouse=True)
async def clean(bind_motor):
    from services.financial_inventory_opening import ensure_indexes as ensure_opening

    db = server.db
    await server.client.drop_database(os.environ["DB_NAME"])
    await ensure_coa(db)
    await ensure_journal(db)
    await ensure_periods(db)
    await ensure_hpp(db)
    await ensure_opening(db)
    await db.purchase_goods_receipts.create_index("receipt_number", unique=True)
    await db.app_settings.create_index("key", unique=True)
    yield


def _admin():
    app.dependency_overrides[current_user] = lambda: dict(USER)


def _deny():
    app.dependency_overrides[current_user] = lambda: {
        "id": "viewer",
        "email": "view@eternalpackaging.com",
        "name": "View",
        "role": "crm",
        "permissions": ["po.view"],
    }


async def _coa(prefix, name, account_type):
    return await create_account(
        server.db,
        {"code": f"{prefix}{uuid.uuid4().hex[:6]}", "name": name, "account_type": account_type},
        user=USER,
    )


async def _configure(*, effective="2026-01-01", offset=True):
    inventory = await _coa("13", "Persediaan", "INTR")
    cogs = await _coa("51", "HPP", "COGS")
    payable = await _coa("21", "Hutang", "APAY")
    clearing = await _coa("21", "GRNI", "OCLY")
    equity = await _coa("31", "Modal", "EQTY")
    fields = {
        "key": SETTINGS_KEY,
        "payable_account_id": payable["id"],
        "purchase_clearing_account_id": clearing["id"],
        "inventory_account_id": inventory["id"],
        "cogs_account_id": cogs["id"],
        "inventory_accounting_effective_date": effective,
    }
    if offset:
        fields["inventory_opening_offset_account_id"] = equity["id"]
    await server.db.app_settings.update_one({"key": SETTINGS_KEY}, {"$set": fields}, upsert=True)
    await server.db.app_settings.update_one(
        {"key": "hpp_valuation"},
        {"$set": {"key": "hpp_valuation", "method": "RATA-RATA"}},
        upsert=True,
    )
    return {"inventory": inventory, "cogs": cogs, "equity": equity, "clearing": clearing, "payable": payable}


async def _master(stock=100):
    mid = f"mid-{uuid.uuid4().hex[:8]}"
    await server.db.master_items.insert_one({
        "id": mid,
        "item_code": mid,
        "item_name": mid,
        "item_type": "INVENTORY",
        "unit": "PCS",
        "is_active": True,
    })
    await server.db.product_stock_state.insert_one({
        "_id": mid,
        "master_item_id": mid,
        "base_uom": "PCS",
        "total_qty": stock,
        "unprinted_qty": stock,
        "printed_qty": 0,
        "status": "INITIALIZED",
        "initialized": True,
        "pending_event": None,
        "version": 1,
    })
    return mid


async def _gr(mid, qty, price, receipt_date, *, post=False):
    po_id = f"po-{uuid.uuid4().hex[:8]}"
    line_id = f"ln-{uuid.uuid4().hex[:8]}"
    await server.db.purchase_orders.insert_one({
        "id": po_id,
        "po_number": f"PB-{uuid.uuid4().hex[:6]}",
        "supplier": "Supplier",
        "cancelled": False,
        "lines": [{
            "line_id": line_id,
            "ordered_qty": qty,
            "unit_price": price,
            "unit": "PCS",
            "master_item_id": mid,
        }],
    })
    rid = str(uuid.uuid4())
    await server.db.purchase_goods_receipts.insert_one({
        "id": rid,
        "receipt_number": f"GR-{uuid.uuid4().hex[:6]}",
        "purchase_order_id": po_id,
        "receipt_date": receipt_date,
        "cancelled": False,
        "lines": [{
            "line_id": f"gl-{uuid.uuid4().hex[:6]}",
            "purchase_order_line_id": line_id,
            "received_qty": qty,
            "unit": "PCS",
            "master_item_id": mid,
            "unit_price": price,
        }],
    })
    if post:
        posted = await post_purchase_goods_receipt_accounting(server.db, rid, USER, serialize_in_process=False)
        assert isinstance(posted, dict), posted
    return rid


def _opening_payload(mid, qty, cost, request_id, effective=EFFECTIVE):
    return {
        "client_request_id": request_id,
        "effective_date": effective,
        "lines": [{
            "master_item_id": mid,
            "qty": qty,
            "unit_cost": cost,
            "base_uom": "PCS",
            "source": "APPROVED_OPENING",
        }],
    }


def _covered(acquisitions, mid):
    key = f"m:{mid}"
    rows = [row for row in acquisitions if row.get("financial_inventory_item_key") == key]
    qty = sum((Decimal(str(row["base_qty"])) for row in rows), Decimal("0"))
    value = sum((Decimal(str(row["total_cost"])) for row in rows), Decimal("0"))
    return qty, value, rows


async def test_wib_boundary_around_1_october():
    assert wib_business_date("2026-09-30") == "2026-09-30"
    assert wib_business_date("2026-10-01") == "2026-10-01"
    assert wib_business_date("2026-09-30T16:59:59Z") == "2026-09-30"
    assert wib_business_date("2026-09-30T17:00:00Z") == "2026-10-01"
    assert wib_business_date("kemarin") is None


async def _stock_effect(
    mid, *, reference_type, reference_id, before, after, created_at,
    movement_id=None, claimed_at=None, stock_applied_at=None, movement_type=None, date=None,
    source=None, updated_at=None,
):
    await server.db.stock_movements.insert_one({
        "id": movement_id or str(uuid.uuid4()),
        "master_item_id": mid,
        "movement_type": movement_type or ("PENERIMAAN" if reference_type == "goods_receipt" else "PENGIRIMAN"),
        "reference_type": reference_type,
        "reference_id": reference_id,
        "base_uom": "PCS",
        "base_qty": abs(float(Decimal(str(after)) - Decimal(str(before)))),
        "transaction_qty": abs(float(Decimal(str(after)) - Decimal(str(before)))),
        "transaction_uom": "PCS",
        "conversion_factor": 1,
        "delta": float(Decimal(str(after)) - Decimal(str(before))),
        "balance_before": before,
        "balance_after": after,
        "created_at": created_at,
        "date": date or created_at,
        "updated_at": updated_at,
        "claimed_at": claimed_at,
        "stock_applied_at": stock_applied_at,
        "source": source,
        "status": "POSTED",
    })


async def _assert_unproven_blocks_journal(mid, movement_id, request_id, *, qty=100):
    preview = await preview_financial_opening(
        server.db, _opening_payload(mid, qty, 1000, f"{request_id}-preview"),
    )
    assert preview["writes"] == 0
    assert preview["can_post"] is False
    blocker = preview["blockers"][0]
    assert blocker["code"] == "STOCK_COMMIT_UNPROVEN"
    assert blocker["movement_id"] == movement_id
    assert movement_id in blocker["message"]
    assert blocker.get("evidence_reason")
    before = await server.db[JOURNAL_ENTRIES].count_documents({})
    with pytest.raises(OpeningBlocked) as exc:
        await post_financial_opening(server.db, _opening_payload(mid, qty, 1000, request_id), USER)
    assert exc.value.code == "STOCK_COMMIT_UNPROVEN"
    assert movement_id in exc.value.message
    assert exc.value.extra["blockers"][0]["movement_id"] == movement_id
    assert exc.value.extra["blockers"][0]["evidence_reason"]
    assert await server.db[JOURNAL_ENTRIES].count_documents({}) == before
    assert await server.db.financial_inventory_openings.count_documents({"status": "POSTED"}) == 0


async def test_opening_keeps_historical_gr_inside_basis_and_adds_new_gr():
    accounts = await _configure()
    mid = await _master(100)
    historical = await _gr(mid, 50, 1000, "2026-09-15", post=False)
    await _stock_effect(
        mid,
        reference_type="goods_receipt",
        reference_id=historical,
        before=50,
        after=100,
        created_at="2026-09-15T02:00:00Z",
        stock_applied_at="2026-09-15T02:00:00Z",
    )
    opening = await post_financial_opening(server.db, _opening_payload(mid, 100, 1000, "req-open"), USER)
    assert opening["status"] == "POSTED"
    assert historical in opening["excluded_receipt_ids"]
    new_id = await _gr(mid, 50, 1200, "2026-10-02", post=True)
    acquisitions, _report = await load_live_acquisition_coverage(server.db)
    qty, value, rows = _covered(acquisitions, mid)
    assert qty == Decimal("150")
    assert value == Decimal("160000.00")
    assert Decimal("160000") / Decimal("150") == Decimal("3200") / Decimal("3")
    assert all(row.get("receipt_id") != historical for row in rows)
    assert any(row.get("receipt_id") == new_id for row in rows)
    assert any(str(row.get("acquisition_key") or "").startswith("opening:") for row in rows)
    mov_id = str(uuid.uuid4())
    await server.db.stock_movements.insert_one({
        "id": mov_id,
        "movement_type": "PENGIRIMAN",
        "master_item_id": mid,
        "date": "2026-10-05T03:00:00Z",
        "created_at": "2026-10-05T03:00:00Z",
        "transaction_qty": 15,
        "transaction_uom": "PCS",
        "conversion_factor": 1,
        "base_qty": 15,
        "base_uom": "PCS",
        "status": "POSTED",
    })
    await post_inventory_issue_accounting(server.db, mov_id, USER)
    book = await build_financial_book_reconciliation(server.db)
    assert book["writes"] == 0
    assert book["opening_value"] == 100000.0
    assert book["goods_receipt_value"] == 60000.0
    assert book["cogs_value"] == 16000.0
    assert book["financial_remaining"] == 144000.0
    assert book["inventory_gl_balance"] == 144000.0
    assert book["reconciliation_status"] == "MATCH"
    assert book["historical_gr_overlap"] == 0
    assert qty - Decimal("15") == Decimal("135")
    lines = await server.db[JOURNAL_LINES].find(
        {"account_id": accounts["inventory"]["id"]},
        {"_id": 0},
    ).to_list(20)
    net = sum(Decimal(str(row.get("debit") or 0)) - Decimal(str(row.get("credit") or 0)) for row in lines)
    assert net == Decimal("144000.00")


async def test_backdated_receipt_does_not_pass_exclusion():
    await _configure()
    mid = await _master()
    await post_financial_opening(server.db, _opening_payload(mid, 100, 1000, "req-back"), USER)
    late = await _gr(mid, 50, 1200, "2026-09-30T16:59:59Z", post=False)
    with pytest.raises(HTTPException) as exc:
        await post_purchase_goods_receipt_accounting(server.db, late, USER, serialize_in_process=False)
    assert exc.value.detail["code"] == "HISTORICAL_RECEIPT_EXCLUDED"
    on_start = await _gr(mid, 10, 1200, "2026-09-30T17:00:00Z", post=True)
    acquisitions, _report = await load_live_acquisition_coverage(server.db)
    _qty, _value, rows = _covered(acquisitions, mid)
    assert any(row.get("receipt_id") == on_start for row in rows)
    assert all(row.get("receipt_id") != late for row in rows)


async def test_issue_from_opening_matches_inventory_gl():
    accounts = await _configure()
    mid = await _master()
    opening = await post_financial_opening(server.db, _opening_payload(mid, 100, 1000, "req-issue"), USER)
    await _gr(mid, 50, 1200, "2026-10-02", post=True)
    mov_id = str(uuid.uuid4())
    await server.db.stock_movements.insert_one({
        "id": mov_id,
        "movement_type": "PENGIRIMAN",
        "master_item_id": mid,
        "date": "2026-10-05T03:00:00Z",
        "created_at": "2026-10-05T03:00:00Z",
        "transaction_qty": 15,
        "transaction_uom": "PCS",
        "conversion_factor": 1,
        "base_qty": 15,
        "base_uom": "PCS",
        "reference_type": "sales_delivery",
        "reference_id": "sj-1",
        "status": "POSTED",
    })
    await post_inventory_issue_accounting(server.db, mov_id, USER)
    stored_issue = await server.db.inventory_issue_accounting.find_one({"movement_id": mov_id}, {"_id": 0})
    basis = stored_issue[POSTING_BASIS_FIELD]
    assert Decimal(str(basis["total_cogs"])) == Decimal("16000.00")
    opening_journal = await server.db[JOURNAL_ENTRIES].find_one({"source_id": opening["id"], "source_type": SOURCE_TYPE}, {"_id": 0})
    issue_journal = await server.db[JOURNAL_ENTRIES].find_one({"source_id": mov_id}, {"_id": 0})
    gr_journal = await server.db[JOURNAL_ENTRIES].find_one(
        {"source_type": "purchase_goods_receipt", "status": "posted"},
        {"_id": 0},
    )
    lines = await server.db[JOURNAL_LINES].find(
        {
            "journal_id": {"$in": [opening_journal["id"], issue_journal["id"], gr_journal["id"]]},
            "account_id": accounts["inventory"]["id"],
        },
        {"_id": 0},
    ).to_list(10)
    net = sum(Decimal(str(row.get("debit") or 0)) - Decimal(str(row.get("credit") or 0)) for row in lines)
    assert net == Decimal("144000.00")


async def test_existing_opening_voucher_is_not_duplicated():
    accounts = await _configure(offset=False)
    mid = await _master()
    draft = await create_draft(server.db, {
        "cutoff_date": EFFECTIVE,
        "description": "Saldo awal GL",
        "lines": [
            {"account_id": accounts["inventory"]["id"], "debit": 100000, "credit": 0},
            {"account_id": accounts["equity"]["id"], "debit": 0, "credit": 100000},
        ],
    }, user=USER)
    voucher = await post_voucher(server.db, draft["id"], user=USER)
    before = await server.db[JOURNAL_ENTRIES].count_documents({})
    opening = await post_financial_opening(server.db, _opening_payload(mid, 100, 1000, "req-link"), USER)
    assert opening["journal_origin"] == "existing_opening_voucher"
    assert opening["posted_journal_id"] == voucher["posted_journal_id"]
    assert await server.db[JOURNAL_ENTRIES].count_documents({}) == before
    assert await server.db[JOURNAL_ENTRIES].count_documents({"source_type": SOURCE_TYPE}) == 0


async def test_same_request_id_is_one_operation_and_other_request_conflicts():
    await _configure()
    mid = await _master()
    first = await post_financial_opening(server.db, _opening_payload(mid, 100, 1000, "same-req"), USER)
    again = await post_financial_opening(server.db, _opening_payload(mid, 100, 1000, "same-req"), USER)
    assert again["id"] == first["id"]
    assert await server.db[JOURNAL_ENTRIES].count_documents({"source_type": SOURCE_TYPE, "source_id": first["id"]}) == 1
    with pytest.raises(OpeningBlocked) as exc:
        await post_financial_opening(server.db, _opening_payload(mid, 80, 500, "other-req"), USER)
    assert exc.value.code == "OPENING_ALREADY_POSTED"


async def test_crash_between_journal_and_status_recovers_once():
    await _configure()
    mid = await _master()
    import services.financial_inventory_opening as fio

    original = fio._after_step

    async def boom(step):
        if step == "status":
            raise RuntimeError("crash before posted status")

    fio._after_step = boom
    try:
        with pytest.raises(OpeningBlocked):
            await post_financial_opening(server.db, _opening_payload(mid, 100, 1000, "crash-req"), USER)
    finally:
        fio._after_step = original
    doc = await server.db.financial_inventory_openings.find_one({"client_request_id": "crash-req"}, {"_id": 0})
    assert doc["status"] != "POSTED"
    recovered = await recover_financial_opening(server.db, client_request_id="crash-req", user=USER)
    assert recovered["status"] == "POSTED"
    assert await server.db[JOURNAL_ENTRIES].count_documents({
        "source_type": SOURCE_TYPE,
        "source_id": recovered["id"],
        "reversal_of": None,
    }) == 1
    again = await recover_financial_opening(server.db, client_request_id="crash-req", user=USER)
    assert again["id"] == recovered["id"]
    assert await server.db[JOURNAL_ENTRIES].count_documents({"source_type": SOURCE_TYPE, "source_id": recovered["id"]}) == 1


async def test_published_change_does_not_change_posted_opening():
    await _configure()
    mid = await _master()
    await server.db.customer_pricing_versions.insert_one({
        "id": "pub-1",
        "status": "PUBLISHED",
        "products": [{"master_item_id": mid, "hpp_snapshot": 999}],
    })
    opening = await post_financial_opening(server.db, _opening_payload(mid, 100, 1000, "req-pub"), USER)
    await server.db.customer_pricing_versions.update_one({"id": "pub-1"}, {"$set": {"products.0.hpp_snapshot": 1}})
    stored = await server.db.financial_inventory_openings.find_one({"id": opening["id"]}, {"_id": 0})
    assert stored["lines"][0]["unit_cost"] == 1000
    assert stored["lines"][0]["provenance"]["published_price_ignored"] is True
    with pytest.raises(OpeningBlocked) as exc:
        await post_financial_opening(server.db, {
            "client_request_id": "req-published-source",
            "effective_date": "2026-10-02",
            "lines": [{
                "master_item_id": mid,
                "qty": 1,
                "unit_cost": 10,
                "base_uom": "PCS",
                "source": "PUBLISHED_CUSTOMER_PRICING_FALLBACK",
            }],
        }, USER)
    assert exc.value.code == "PUBLISHED_NOT_BOOK_VALUE"


async def test_missing_zero_uom_and_negative_are_not_fake_success():
    await _configure()
    mid = await _master()
    missing = dict(_opening_payload(mid, 100, 1000, "bad"))
    missing["lines"][0]["unit_cost"] = None
    with pytest.raises(OpeningBlocked) as exc:
        await post_financial_opening(server.db, missing, USER)
    assert exc.value.code == "OPENING_COST_MISSING"
    zero = dict(_opening_payload(mid, 100, 0, "zero"))
    with pytest.raises(OpeningBlocked) as exc:
        await post_financial_opening(server.db, zero, USER)
    assert exc.value.code == "OPENING_COST_MISSING"
    negative = dict(_opening_payload(mid, -1, 1000, "neg"))
    with pytest.raises(OpeningBlocked) as exc:
        await post_financial_opening(server.db, negative, USER)
    assert exc.value.code == "OPENING_QTY_NEGATIVE"
    ambiguous = dict(_opening_payload(mid, 100, 1000, "uom"))
    ambiguous["lines"][0]["base_uom"] = "DUS"
    with pytest.raises(OpeningBlocked) as exc:
        await post_financial_opening(server.db, ambiguous, USER)
    assert exc.value.code == "OPENING_UOM_AMBIGUOUS"
    assert await server.db[JOURNAL_ENTRIES].count_documents({"source_type": SOURCE_TYPE}) == 0


async def test_period_closed_insufficient_and_reversal_after_consume():
    accounts = await _configure()
    mid = await _master()
    await close_period(server.db, "2026-10", user=USER, reason="tutup periode uji")
    with pytest.raises(OpeningBlocked) as exc:
        await post_financial_opening(server.db, _opening_payload(mid, 100, 1000, "locked"), USER)
    assert exc.value.code == "PERIOD_CLOSED"
    assert await server.db[JOURNAL_ENTRIES].count_documents({"source_type": SOURCE_TYPE, "status": "posted"}) == 0
    await server.db.accounting_periods.delete_many({})
    opening = await post_financial_opening(server.db, _opening_payload(mid, 10, 1000, "reopened"), USER)
    assert opening["status"] == "POSTED"
    mov_id = str(uuid.uuid4())
    await server.db.stock_movements.insert_one({
        "id": mov_id,
        "movement_type": "PENGIRIMAN",
        "master_item_id": mid,
        "date": "2026-10-05T03:00:00Z",
        "transaction_qty": 40,
        "transaction_uom": "PCS",
        "conversion_factor": 1,
        "base_qty": 40,
        "base_uom": "PCS",
        "status": "POSTED",
    })
    with pytest.raises(HTTPException) as issue_exc:
        await post_inventory_issue_accounting(server.db, mov_id, USER)
    assert issue_exc.value.detail["code"] == "INSUFFICIENT_FINANCIAL_INVENTORY"
    await server.db.stock_movements.delete_one({"id": mov_id})
    small = str(uuid.uuid4())
    await server.db.stock_movements.insert_one({
        "id": small,
        "movement_type": "PENGIRIMAN",
        "master_item_id": mid,
        "date": "2026-10-06T03:00:00Z",
        "transaction_qty": 1,
        "transaction_uom": "PCS",
        "conversion_factor": 1,
        "base_qty": 1,
        "base_uom": "PCS",
        "status": "POSTED",
    })
    await post_inventory_issue_accounting(server.db, small, USER)
    with pytest.raises(OpeningBlocked) as rev:
        await reverse_financial_opening(server.db, opening["id"], USER, "batal opening")
    assert rev.value.code == "OPENING_ALREADY_CONSUMED"


async def _source_snapshot(journal_id: str) -> dict:
    source = await server.db[JOURNAL_ENTRIES].find_one({"id": journal_id}, {"_id": 0})
    lines = await server.db[JOURNAL_LINES].find({"journal_id": journal_id}, {"_id": 0}).to_list(20)
    return {
        "status": str((source or {}).get("status") or ""),
        "intent": (source or {}).get("reversal_intent"),
        "owner_tokens": (source or {}).get("reversal_owner_tokens"),
        "owner_expires": (source or {}).get("reversal_owner_expires_at"),
        "lines": sorted(
            (str(row.get("id")), str(row.get("account_id")), row.get("debit"), row.get("credit"))
            for row in lines
        ),
        "reversal_headers": await server.db[JOURNAL_ENTRIES].count_documents({"reversal_of": journal_id}),
        "reversal_lines": await server.db[JOURNAL_LINES].count_documents({
            "journal_id": {"$in": [
                str(row.get("id") or "")
                for row in await server.db[JOURNAL_ENTRIES].find(
                    {"reversal_of": journal_id}, {"id": 1}
                ).to_list(10)
            ]}
        }),
    }


async def test_locked_period_opening_reversal_changes_nothing():
    """A closed period rejects opening reversal before any durable reversal write."""
    await _configure()
    mid = await _master()
    opening = await post_financial_opening(
        server.db, _opening_payload(mid, 100, 1000, "locked-reverse"), USER,
    )
    source_id = opening["posted_journal_id"]
    before = await _source_snapshot(source_id)
    opening_before = await server.db.financial_inventory_openings.find_one(
        {"id": opening["id"]}, {"_id": 0, "status": 1},
    )
    await close_period(server.db, "2026-10", user=USER, reason="tutup sebelum reversal")
    with pytest.raises(OpeningBlocked) as exc:
        await reverse_financial_opening(server.db, opening["id"], USER, "batal pada periode terkunci")
    assert exc.value.code == "PERIOD_CLOSED"
    assert await _source_snapshot(source_id) == before
    opening_after = await server.db.financial_inventory_openings.find_one(
        {"id": opening["id"]}, {"_id": 0, "status": 1},
    )
    assert opening_after["status"] == opening_before["status"] == "POSTED"


async def test_reversal_period_claim_blocks_close_until_completion():
    """The writer claim covers the first durable reversal write through completion."""
    import services.journal_core as journal_core

    await _configure()
    mid = await _master()
    opening = await post_financial_opening(
        server.db, _opening_payload(mid, 100, 1000, "claim-held"), USER,
    )
    entered = asyncio.Event()
    release = asyncio.Event()
    original = journal_core._before_reversal_insert

    async def pause():
        entered.set()
        await release.wait()

    journal_core._before_reversal_insert = pause
    task = asyncio.create_task(reverse_financial_opening(
        server.db, opening["id"], USER, "reversal saat klaim periode",
    ))
    try:
        await asyncio.wait_for(entered.wait(), timeout=10)
        with pytest.raises(PeriodConflictError):
            await close_period(server.db, "2026-10", user=USER, reason="tutup saat reversal berjalan")
        release.set()
        saved = await task
        assert saved["status"] == "REVERSED"
    finally:
        release.set()
        journal_core._before_reversal_insert = original
        if not task.done():
            task.cancel()
    source = await server.db[JOURNAL_ENTRIES].find_one(
        {"id": opening["posted_journal_id"]}, {"_id": 0},
    )
    headers = await server.db[JOURNAL_ENTRIES].find(
        {"reversal_of": opening["posted_journal_id"]}, {"_id": 0},
    ).to_list(5)
    assert len(headers) == 1
    assert headers[0]["status"] == "posted"
    assert source["status"] == "reversed"
    assert source["reversal_intent"]["phase"] == "complete"
    closed = await close_period(server.db, "2026-10", user=USER, reason="tutup setelah reversal selesai")
    assert closed["status"] == "closed"


async def test_preview_and_get_do_not_write_and_role_is_denied():
    await _configure()
    mid = await _master()
    before_openings = await server.db.financial_inventory_openings.count_documents({})
    before_journals = await server.db[JOURNAL_ENTRIES].count_documents({})
    preview = await preview_financial_opening(server.db, _opening_payload(mid, 100, 1000, "preview-only"))
    assert preview["writes"] == 0
    assert preview["published_is_book_value"] is False
    assert await server.db.financial_inventory_openings.count_documents({}) == before_openings
    assert await server.db[JOURNAL_ENTRIES].count_documents({}) == before_journals
    _admin()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        listed = await client.get("/api/inventory/financial-opening")
        assert listed.status_code == 200
        assert await server.db.financial_inventory_openings.count_documents({}) == before_openings
        _deny()
        denied = await client.post("/api/inventory/financial-opening", json=_opening_payload(mid, 100, 1000, "nope"))
        assert denied.status_code == 403
        settings_denied = await client.put("/api/settings/purchase-accounting", json={
            "inventory_opening_offset_account_id": "not-an-account",
        })
        assert settings_denied.status_code == 403
    assert await server.db[JOURNAL_ENTRIES].count_documents({}) == before_journals
    app.dependency_overrides.clear()


async def test_two_concurrent_receipts_do_not_double_count_baseline():
    from services.physical_stock import (
        MOVEMENT_MANUAL_ADJUSTMENT,
        PhysicalStockUnavailableError,
        ensure_indexes as ensure_physical_indexes,
        get_balance,
        post_movement,
        require_transaction_support,
    )

    # Goods receipt posts R2 inside a Mongo transaction. CI mongo:7 is standalone.
    try:
        await require_transaction_support(server.client)
    except PhysicalStockUnavailableError:
        pytest.skip("MongoDB replica set required for concurrent R2 goods receipts")

    await ensure_physical_indexes(server.db)
    await server.db.branches.update_one(
        {"id": "branch-ygy"},
        {"$set": {"id": "branch-ygy", "code": "YGY", "name": "Yogyakarta", "active": True}},
        upsert=True,
    )
    await server.db.warehouses.update_one(
        {"id": "wh-main"},
        {"$set": {
            "id": "wh-main", "branch_id": "branch-ygy", "code": "MAIN",
            "name": "Gudang Yogyakarta", "active": True,
        }},
        upsert=True,
    )

    mid = f"mid-concurrent"
    aid = "aid-concurrent"
    await server.db.master_items.insert_one({
        "id": mid,
        "accurate_item_id": aid,
        "item_code": "CON",
        "item_name": "Concurrent",
        "item_type": "INVENTORY",
        "unit": "PCS",
        "base_unit": "PCS",
        "is_active": True,
    })
    await server.db.product_stock_state.insert_one({
        "_id": mid,
        "master_item_id": mid,
        "accurate_item_id": aid,
        "base_uom": "PCS",
        "total_qty": 100,
        "unprinted_qty": 100,
        "printed_qty": 0,
        "status": "INITIALIZED",
        "initialized": True,
        "pending_event": None,
        "version": 1,
    })
    # Canonical pre-GR physical qty lives on R2, not product_stock_state.
    await post_movement(server.db, {
        "master_item_id": mid,
        "branch_id": "branch-ygy",
        "warehouse_id": "wh-main",
        "movement_type": MOVEMENT_MANUAL_ADJUSTMENT,
        "qty_delta": 100,
        "idempotency_key": f"open-{uuid.uuid4().hex[:8]}",
        "reason": "pre-gr operational stock",
    }, actor_id="admin-fio")
    await server.db.customer_pricing_groups.insert_one({
        "id": "group-con",
        "enabled": True,
        "current_published_version_id": "pub-con",
    })
    await server.db.customer_pricing_versions.insert_one({
        "id": "pub-con",
        "pricing_group_id": "group-con",
        "version_number": 1,
        "revision": 1,
        "status": "PUBLISHED",
        "pricing_mode": "HPP_FORMULA",
        "hpp_source": "MANUAL_HPP",
        "products": [{
            "master_item_id": mid,
            "accurate_item_id": aid,
            "hpp_snapshot": 1000,
            "hpp_status": "OK",
            "manual_hpp": 1000,
        }],
    })

    async def _po(suffix):
        po_id = f"po-{suffix}"
        line_id = f"line-{suffix}"
        await server.db.purchase_orders.insert_one({
            "id": po_id,
            "po_number": f"PB-{suffix}",
            "supplier": "Supplier",
            "physical_stock_snapshot": {"enabled": True, "stock_in_trigger": "goods_receipt"},
            "lines": [{
                "line_id": line_id,
                "line_idx": 0,
                "master_item_id": mid,
                "accurate_item_id": aid,
                "item_code": "CON",
                "item_name": "Concurrent",
                "unit": "PCS",
                "ordered_qty": 50,
                "unit_price": 1200,
            }],
        })
        return po_id, line_id

    po_a, line_a = await _po("a")
    po_b, line_b = await _po("b")

    async def _receive(suffix, po_id, line_id):
        return await create_purchase_goods_receipt(server.db, {
            "purchase_order_id": po_id,
            "idempotency_key": f"idem-{suffix}",
            "lines": [{"purchase_order_line_id": line_id, "received_qty": 50}],
        }, USER)

    await asyncio.gather(_receive("a", po_a, line_a), _receive("b", po_b, line_b))
    state = await server.db.product_stock_state.find_one({"master_item_id": mid}, {"_id": 0})
    assert state["total_qty"] == 100  # GR must not write legacy product_stock_state
    physical = await get_balance(server.db, mid, "branch-ygy", "wh-main")
    assert physical["physical_qty"] == 200
    baselines = await server.db.hpp_valuation_baseline_lines.find(
        {"master_item_id": mid}, {"_id": 0},
    ).to_list(5)
    assert len(baselines) == 1
    baseline = baselines[0]
    assert baseline["stock_fisik_snapshot"] == 100
    assert baseline.get("pre_gr_operational_qty") == 100


async def test_valuation_export_uses_full_filter_not_one_page():
    await _configure()
    for index in range(3):
        mid = await _master(index + 1)
        await server.db.master_items.update_one({"id": mid}, {"$set": {"item_code": f"ZZ-{index}", "item_name": f"Item {index}"}})
    view = await build_inventory_valuation_rows(server.db)
    filtered = filter_sort_all(view["rows"], q="ZZ-", status_filter="all")
    assert len(filtered) == 3
    assert view["summary"]["valued_items"] + view["summary"]["items_without_hpp"] + 0 >= 0
    from io import BytesIO
    from openpyxl import load_workbook

    workbook = load_workbook(BytesIO(export_xlsx(filtered)))
    headers = [cell.value for cell in workbook["Nilai Persediaan"][1]]
    assert "Sumber HPP" in headers
    assert "Posting Finansial" in headers
    assert workbook["Nilai Persediaan"].max_row == 4
    assert view["summary"]["total_inventory_value"] == sum(
        row["inventory_value"] or 0
        for row in view["rows"]
        if row["hpp_status"] == "OK" and row["inventory_value"] is not None
    )


async def test_unconfigured_offset_is_not_guessed():
    await _configure(offset=False)
    mid = await _master()
    with pytest.raises(OpeningBlocked) as exc:
        await post_financial_opening(server.db, _opening_payload(mid, 100, 1000, "no-offset"), USER)
    assert exc.value.code == "OPENING_OFFSET_UNRESOLVED"
    assert await server.db[JOURNAL_ENTRIES].count_documents({"source_type": SOURCE_TYPE}) == 0


async def test_active_historical_gr_journal_blocks_opening():
    await _configure()
    mid = await _master()
    historical = await _gr(mid, 50, 1000, "2026-09-15", post=True)
    before = await server.db[JOURNAL_ENTRIES].count_documents({"source_type": SOURCE_TYPE})
    with pytest.raises(OpeningBlocked) as exc:
        await post_financial_opening(server.db, _opening_payload(mid, 100, 1000, "blocked-hist"), USER)
    assert exc.value.code == "HISTORICAL_GR_JOURNAL_ACTIVE"
    assert exc.value.extra["sources"][0]["receipt_id"] == historical
    assert await server.db[JOURNAL_ENTRIES].count_documents({"source_type": SOURCE_TYPE}) == before


async def test_same_total_voucher_with_wrong_credit_is_rejected():
    accounts = await _configure(offset=False)
    mid = await _master()
    draft = await create_draft(server.db, {
        "cutoff_date": EFFECTIVE,
        "description": "Salah scope",
        "lines": [
            {"account_id": accounts["inventory"]["id"], "debit": 100000, "credit": 0},
            {"account_id": accounts["payable"]["id"], "debit": 0, "credit": 100000},
        ],
    }, user=USER)
    await post_voucher(server.db, draft["id"], user=USER)
    before = await server.db[JOURNAL_ENTRIES].count_documents({})
    with pytest.raises(OpeningBlocked) as exc:
        await post_financial_opening(server.db, _opening_payload(mid, 100, 1000, "bad-scope"), USER)
    assert exc.value.code == "GL_OPENING_SCOPE_MISMATCH"
    assert await server.db[JOURNAL_ENTRIES].count_documents({}) == before


async def test_later_lower_balance_before_is_not_the_opening_qty():
    mid = await _master(90)
    first = "gr-first"
    second = "gr-second"
    for receipt_id in (first, second):
        await server.db.hpp_baseline_participants.insert_one({
            "master_item_id": mid,
            "receipt_id": receipt_id,
            "line_id": f"line-{receipt_id}",
        })
    await _stock_effect(mid, reference_type="goods_receipt", reference_id=first, before=100, after=150, created_at="2026-10-02T01:00:00Z")
    await _stock_effect(mid, reference_type="pengiriman", reference_id="sj-mid", before=150, after=40, created_at="2026-10-02T02:00:00Z")
    await _stock_effect(mid, reference_type="goods_receipt", reference_id=second, before=40, after=90, created_at="2026-10-02T03:00:00Z")
    proof = await proven_opening_physical_qty(server.db, mid, second)
    assert proof["ok"] is True
    assert proof["qty"] == 100
    assert proof["boundary_receipt_id"] == first
    assert proof["qty"] != 40


async def test_po_trigger_without_gr_movement_stays_unproven():
    mid = await _master(100)
    await server.db.hpp_baseline_participants.insert_one({
        "master_item_id": mid,
        "receipt_id": "gr-po-trigger",
        "line_id": "line-po",
    })
    proof = await proven_opening_physical_qty(server.db, mid, "gr-po-trigger")
    assert proof["ok"] is False
    assert proof["reason"] == "STOCK_SNAPSHOT_UNPROVEN"


async def test_same_request_id_rejects_different_payload():
    await _configure()
    mid = await _master()
    first = await post_financial_opening(server.db, _opening_payload(mid, 100, 1000, "frozen-req"), USER)
    with pytest.raises(OpeningBlocked) as exc:
        await post_financial_opening(server.db, _opening_payload(mid, 80, 1000, "frozen-req"), USER)
    assert exc.value.code == "OPENING_PAYLOAD_MISMATCH"
    assert await server.db[JOURNAL_ENTRIES].count_documents({"source_type": SOURCE_TYPE, "source_id": first["id"]}) == 1


def _opening_process_worker(db_name, mid, request_id, barrier, queue):
    try:
        if not os.environ.get("MONGO_URL"):
            raise RuntimeError("MONGO_URL wajib diwariskan dari runner tes")
        os.environ["DB_NAME"] = db_name
        barrier.wait(20)
        import asyncio
        from motor.motor_asyncio import AsyncIOMotorClient
        import server as srv
        from services.financial_inventory_opening import OpeningBlocked as Blocked
        from services.financial_inventory_opening import post_financial_opening as post_opening

        async def _run():
            client = AsyncIOMotorClient(os.environ["MONGO_URL"])
            srv.db = client[db_name]
            try:
                saved = await post_opening(srv.db, {
                    "client_request_id": request_id,
                    "effective_date": "2026-10-01",
                    "lines": [{
                        "master_item_id": mid,
                        "qty": 100,
                        "unit_cost": 1000,
                        "base_uom": "PCS",
                        "source": "APPROVED_OPENING",
                    }],
                }, {"id": f"proc-{request_id}", "email": "proc@test", "name": "Proc", "role": "admin"})
                return {"ok": True, "id": saved["id"], "status": saved["status"]}
            except Blocked as exc:
                return {"ok": False, "code": exc.code}
            finally:
                client.close()

        queue.put(asyncio.run(_run()))
    except Exception as exc:
        queue.put({"ok": False, "code": type(exc).__name__, "message": str(exc)})


async def test_two_processes_do_not_double_post():
    import multiprocessing as mp

    await _configure()
    mid = await _master()
    db_name = os.environ["DB_NAME"]
    ctx = mp.get_context("spawn")
    barrier = ctx.Barrier(2)
    queue = ctx.Queue()
    procs = [
        ctx.Process(target=_opening_process_worker, args=(db_name, mid, f"proc-{index}", barrier, queue))
        for index in (1, 2)
    ]
    for proc in procs:
        proc.start()
    results = [queue.get(timeout=60) for _ in procs]
    for proc in procs:
        proc.join(30)
    assert all(proc.exitcode == 0 for proc in procs), [proc.exitcode for proc in procs]
    posted = [row for row in results if row.get("status") == "POSTED"]
    assert len(posted) == 1, results
    assert await server.db.financial_inventory_openings.count_documents({"status": "POSTED"}) == 1
    assert await server.db[JOURNAL_ENTRIES].count_documents({"source_type": SOURCE_TYPE, "reversal_of": None}) == 1


async def test_offset_save_does_not_post_or_rewrite_opening():
    from services.purchase_accounting_settings import update_purchase_accounting_settings, get_purchase_accounting_settings

    accounts = await _configure()
    mid = await _master()
    opening = await post_financial_opening(server.db, _opening_payload(mid, 10, 1000, "keep-offset"), USER)
    other = await _coa("32", "Modal lain", "EQTY")
    before = await server.db[JOURNAL_ENTRIES].count_documents({})
    saved = await update_purchase_accounting_settings(server.db, {
        "payable_account_id": accounts["payable"]["id"],
        "purchase_clearing_account_id": accounts["clearing"]["id"],
        "inventory_account_id": accounts["inventory"]["id"],
        "cogs_account_id": accounts["cogs"]["id"],
        "inventory_accounting_effective_date": "2026-01-01",
        "inventory_opening_offset_account_id": other["id"],
    }, USER)
    assert saved["inventory_opening_offset_account_id"] == other["id"]
    again = await get_purchase_accounting_settings(server.db)
    assert again["inventory_opening_offset_account_id"] == other["id"]
    assert await server.db[JOURNAL_ENTRIES].count_documents({}) == before
    stored = await server.db.financial_inventory_openings.find_one({"id": opening["id"]}, {"_id": 0})
    assert stored["offset_account_id"] == accounts["equity"]["id"]
    assert stored["status"] == "POSTED"
    revenue = await _coa("41", "Pendapatan", "REVE")
    with pytest.raises(HTTPException) as rejected:
        await update_purchase_accounting_settings(server.db, {
            "payable_account_id": accounts["payable"]["id"],
            "purchase_clearing_account_id": accounts["clearing"]["id"],
            "inventory_account_id": accounts["inventory"]["id"],
            "cogs_account_id": accounts["cogs"]["id"],
            "inventory_accounting_effective_date": "2026-01-01",
            "inventory_opening_offset_account_id": revenue["id"],
        }, USER)
    assert rejected.value.status_code == 400
    kept = await get_purchase_accounting_settings(server.db)
    assert kept["inventory_opening_offset_account_id"] == other["id"]


async def test_fifo_policy_is_not_replaced_by_average():
    await _configure()
    await server.db.app_settings.update_one(
        {"key": "hpp_valuation"},
        {"$set": {"key": "hpp_valuation", "method": "FIFO"}},
        upsert=True,
    )
    mid = await _master()
    await post_financial_opening(server.db, _opening_payload(mid, 100, 1000, "fifo-open"), USER)
    mov_id = str(uuid.uuid4())
    await server.db.stock_movements.insert_one({
        "id": mov_id,
        "movement_type": "PENGIRIMAN",
        "master_item_id": mid,
        "date": "2026-10-05T03:00:00Z",
        "created_at": "2026-10-05T03:00:00Z",
        "transaction_qty": 10,
        "transaction_uom": "PCS",
        "conversion_factor": 1,
        "base_qty": 10,
        "base_uom": "PCS",
        "status": "POSTED",
    })
    await post_inventory_issue_accounting(server.db, mov_id, USER)
    stored = await server.db.inventory_issue_accounting.find_one({"movement_id": mov_id}, {"_id": 0})
    basis = stored[POSTING_BASIS_FIELD]
    assert basis["hpp_method"] == "FIFO"
    assert Decimal(str(basis["total_cogs"])) == Decimal("10000.00")
    assert any(str(row.get("acquisition_key") or "").startswith("opening:") for row in basis["consumed_acquisitions"])


async def test_cutoff_snapshot_is_100_not_balance_before_first_gr():
    """Delivery after cutoff, then the first GR, must not pull opening down to 80."""
    await _configure()
    mid = await _master(130)
    delivery_id = "mov-cutoff-issue"
    gr_id = await _gr(mid, 50, 1200, "2026-10-02", post=False)
    await _stock_effect(
        mid,
        reference_type="pengiriman",
        reference_id="sj-cutoff",
        before=100,
        after=80,
        created_at="2026-10-01T01:00:00Z",
        stock_applied_at="2026-10-01T01:00:00Z",
        movement_id=delivery_id,
    )
    await _stock_effect(
        mid,
        reference_type="goods_receipt",
        reference_id=gr_id,
        before=80,
        after=130,
        created_at="2026-10-01T02:00:00Z",
        stock_applied_at="2026-10-01T02:00:00Z",
    )
    with pytest.raises(OpeningBlocked) as early:
        await post_financial_opening(server.db, _opening_payload(mid, 80, 1000, "cutoff-80"), USER)
    assert early.value.code == "OPENING_SNAPSHOT_MISMATCH"
    assert await server.db[JOURNAL_ENTRIES].count_documents({"source_type": SOURCE_TYPE}) == 0
    opening = await post_financial_opening(server.db, _opening_payload(mid, 100, 1000, "cutoff-100"), USER)
    assert opening["effective_date"] == "2026-10-01"
    assert opening["lines"][0]["qty"] == 100
    snap = opening["stock_snapshots"][0]
    assert snap["snapshot_qty"] == 100
    assert snap["snapshot_qty"] != 80
    assert snap["snapshot_basis"] == "balance_before_first_commit_at_or_after_cutoff"
    assert snap["events_before_cutoff"] == 0
    assert snap["events_after_cutoff"] == 2
    assert gr_id not in opening["excluded_receipt_ids"]
    opening_journal = await server.db[JOURNAL_ENTRIES].find_one(
        {"source_type": SOURCE_TYPE, "source_id": opening["id"]},
        {"_id": 0},
    )
    assert opening_journal["date"] == "2026-10-01"
    await post_inventory_issue_accounting(server.db, delivery_id, USER)
    await post_inventory_issue_accounting(server.db, delivery_id, USER)
    issues = await server.db[JOURNAL_ENTRIES].find(
        {"source_type": "inventory_issue", "reversal_of": None, "status": "posted"},
        {"_id": 0},
    ).to_list(10)
    assert len(issues) == 1
    assert issues[0]["date"] == "2026-10-01"
    stored = await server.db.financial_inventory_openings.find_one({"id": opening["id"]}, {"_id": 0})
    assert stored["lines"][0]["qty"] == 100
    basis = (await server.db.inventory_issue_accounting.find_one({"movement_id": delivery_id}, {"_id": 0}))[POSTING_BASIS_FIELD]
    assert Decimal(str(basis["issue_base_qty"])) == Decimal("20")
    assert Decimal(str(basis["total_cogs"])) == Decimal("20000.00")


async def test_opname_after_cutoff_does_not_become_the_opening_qty():
    await _configure()
    mid = await _master(140)
    gr_id = await _gr(mid, 50, 1000, "2026-10-03", post=False)
    await _stock_effect(
        mid,
        reference_type="stock_opname",
        reference_id="op-1",
        before=100,
        after=90,
        created_at="2026-10-01T01:00:00Z",
        stock_applied_at="2026-10-01T01:00:00Z",
        movement_type="KOREKSI",
        source="STOCK_OPNAME",
    )
    await _stock_effect(
        mid,
        reference_type="goods_receipt",
        reference_id=gr_id,
        before=90,
        after=140,
        created_at="2026-10-01T02:00:00Z",
        stock_applied_at="2026-10-01T02:00:00Z",
    )
    opening = await post_financial_opening(server.db, _opening_payload(mid, 100, 1000, "opname-cutoff"), USER)
    snap = opening["stock_snapshots"][0]
    assert snap["snapshot_qty"] == 100
    assert snap["snapshot_qty"] != 90
    assert opening["lines"][0]["qty"] == 100


async def test_claim_before_cutoff_and_commit_after_stays_outside_snapshot():
    await _configure()
    mid = await _master(130)
    gr_id = await _gr(mid, 50, 1000, "2026-10-02", post=False)
    await _stock_effect(
        mid,
        reference_type="pengiriman",
        reference_id="sj-straddle",
        before=100,
        after=80,
        created_at="2026-09-30T16:00:00Z",
        claimed_at="2026-09-30T16:00:00Z",
        stock_applied_at="2026-09-30T17:30:00Z",
    )
    await _stock_effect(
        mid,
        reference_type="goods_receipt",
        reference_id=gr_id,
        before=80,
        after=130,
        created_at="2026-10-01T03:00:00Z",
        stock_applied_at="2026-10-01T03:00:00Z",
    )
    opening = await post_financial_opening(server.db, _opening_payload(mid, 100, 1000, "straddle"), USER)
    snap = opening["stock_snapshots"][0]
    assert snap["snapshot_qty"] == 100
    assert snap["events_before_cutoff"] == 0
    assert snap["events_after_cutoff"] == 2


async def test_claim_without_commit_blocks_cutoff_snapshot():
    await _configure()
    mid = await _master(100)
    await _stock_effect(
        mid,
        reference_type="pengiriman",
        reference_id="sj-unproven",
        before=100,
        after=80,
        created_at="2026-09-30T16:00:00Z",
        claimed_at="2026-09-30T16:00:00Z",
        movement_id="mov-claim-only",
    )
    with pytest.raises(OpeningBlocked) as exc:
        await post_financial_opening(server.db, _opening_payload(mid, 100, 1000, "no-commit"), USER)
    assert exc.value.code == "STOCK_COMMIT_UNPROVEN"
    assert exc.value.extra["blockers"][0]["movement_id"] == "mov-claim-only"
    assert "mov-claim-only" in exc.value.message
    assert await server.db[JOURNAL_ENTRIES].count_documents({"source_type": SOURCE_TYPE}) == 0


async def test_accounting_date_before_cutoff_with_stock_after_is_blocked():
    await _configure()
    mid = await _master(150)
    gr_id = await _gr(mid, 50, 1000, "2026-09-20", post=False)
    await _stock_effect(
        mid,
        reference_type="goods_receipt",
        reference_id=gr_id,
        before=100,
        after=150,
        created_at="2026-10-02T01:00:00Z",
        stock_applied_at="2026-10-02T01:00:00Z",
    )
    with pytest.raises(OpeningBlocked) as exc:
        await post_financial_opening(server.db, _opening_payload(mid, 100, 1000, "acct-before"), USER)
    assert exc.value.code == "HISTORICAL_DOCUMENT_STOCK_AFTER_CUTOFF"
    assert await server.db.financial_inventory_openings.count_documents({"status": "POSTED"}) == 0


async def test_equal_timestamps_follow_the_balance_chain():
    await _configure()
    mid = await _master(130)
    gr_id = await _gr(mid, 50, 1000, "2026-10-02", post=False)
    same = "2026-10-01T04:00:00Z"
    await _stock_effect(
        mid,
        reference_type="pengiriman",
        reference_id="sj-same",
        before=100,
        after=80,
        created_at=same,
        stock_applied_at=same,
    )
    await _stock_effect(
        mid,
        reference_type="goods_receipt",
        reference_id=gr_id,
        before=80,
        after=130,
        created_at=same,
        stock_applied_at=same,
    )
    opening = await post_financial_opening(server.db, _opening_payload(mid, 100, 1000, "same-stamp"), USER)
    assert opening["stock_snapshots"][0]["snapshot_qty"] == 100


async def test_equal_timestamps_with_ambiguous_chain_are_blocked():
    await _configure()
    mid = await _master(100)
    same = "2026-10-01T04:00:00Z"
    await _stock_effect(
        mid,
        reference_type="pengiriman",
        reference_id="sj-a",
        before=100,
        after=80,
        created_at=same,
    )
    await _stock_effect(
        mid,
        reference_type="stock_opname",
        reference_id="op-a",
        before=100,
        after=90,
        created_at=same,
        movement_type="KOREKSI",
    )
    with pytest.raises(OpeningBlocked) as exc:
        await post_financial_opening(server.db, _opening_payload(mid, 100, 1000, "ambiguous-stamp"), USER)
    assert exc.value.code == "STOCK_CHAIN_AMBIGUOUS"
    assert await server.db[JOURNAL_ENTRIES].count_documents({"source_type": SOURCE_TYPE}) == 0


def _clock(value):
    return lambda current=value: current


async def _claim(mid, event_id, movement_type, qty, *, now, is_print=None, reference_type="pengiriman"):
    kwargs = {
        "event_id": event_id,
        "master_item_id": mid,
        "movement_type": movement_type,
        "transaction_qty": qty,
        "transaction_uom": "PCS",
        "source_entity": "GUDANG",
        "destination_entity": "LAIN",
        "reference_number": event_id,
        "reference_type": reference_type,
        "reference_id": event_id,
        "now": now,
    }
    if is_print is not None:
        kwargs["is_print"] = is_print
    return await claim_durable_stock_event(server.db, **kwargs)


async def test_applied_marker_survives_crash_for_gr_delivery_and_opname(monkeypatch):
    await _configure()
    apply_clock = "2026-09-30T16:30:00+00:00"
    retry_clock = "2026-10-02T01:00:00+00:00"
    claim_clock = "2026-09-30T16:00:00+00:00"
    cases = [
        ("gr", MOVEMENT_PENERIMAAN, 50.0, None, "goods_receipt", 150.0),
        ("sj", MOVEMENT_PENGIRIMAN, 20.0, False, "sales_delivery", 80.0),
        ("op", MOVEMENT_KOREKSI, -10.0, None, "stock_opname", 90.0),
    ]
    real_record = stock_engine.record_stock_movement

    async def crash_before_movement(*_args, **_kwargs):
        raise RuntimeError("crash before movement")

    for name, movement_type, qty, is_print, reference_type, after in cases:
        mid = await _master(100)
        event_id = f"{name}-{uuid.uuid4().hex[:8]}"
        monkeypatch.setattr(stock_engine, "now_iso", _clock(apply_clock))
        claimed = await _claim(
            mid, event_id, movement_type, qty,
            now=claim_clock, is_print=is_print, reference_type=reference_type,
        )
        assert claimed["status"] == "CLAIMED"
        monkeypatch.setattr(stock_engine, "record_stock_movement", crash_before_movement)
        with pytest.raises(RuntimeError, match="crash before movement"):
            await finalize_durable_stock_event(
                server.db, event_id=event_id, master_item_id=mid, now=claim_clock,
            )
        crashed = await server.db.product_stock_state.find_one({"_id": mid})
        assert crashed["total_qty"] == after
        assert crashed["pending_event"]["stage"] == "STATE_APPLIED"
        assert crashed["pending_event"]["stock_applied_at"] == apply_clock
        assert await server.db.stock_movements.count_documents({"event_key": event_id}) == 0
        monkeypatch.setattr(stock_engine, "now_iso", _clock(retry_clock))
        monkeypatch.setattr(stock_engine, "record_stock_movement", real_record)
        movement = await finalize_durable_stock_event(
            server.db, event_id=event_id, master_item_id=mid, now=claim_clock,
        )
        assert movement["stock_applied_at"] == apply_clock
        assert movement["claimed_at"] == claim_clock
        finished = await server.db.product_stock_state.find_one({"_id": mid})
        assert finished["total_qty"] == after
        assert finished.get("pending_event") is None
        assert await server.db.stock_movements.count_documents({"master_item_id": mid, "event_key": event_id}) == 1
    assert await server.db[JOURNAL_ENTRIES].count_documents({"source_type": SOURCE_TYPE}) == 0


async def test_retry_after_cutoff_snapshot_uses_the_stored_marker(monkeypatch):
    await _configure()
    mid = await _master(100)
    claim_clock = "2026-09-30T16:00:00+00:00"
    apply_clock = "2026-09-30T16:30:00+00:00"
    retry_clock = "2026-10-02T01:00:00+00:00"
    event_id = f"sj-{uuid.uuid4().hex[:8]}"
    monkeypatch.setattr(stock_engine, "now_iso", _clock(apply_clock))
    await _claim(
        mid, event_id, MOVEMENT_PENGIRIMAN, 20.0,
        now=claim_clock, is_print=False, reference_type="sales_delivery",
    )
    real_record = stock_engine.record_stock_movement

    async def crash_before_movement(*_args, **_kwargs):
        raise RuntimeError("crash before movement")

    monkeypatch.setattr(stock_engine, "record_stock_movement", crash_before_movement)
    with pytest.raises(RuntimeError, match="crash before movement"):
        await finalize_durable_stock_event(server.db, event_id=event_id, master_item_id=mid, now=claim_clock)
    monkeypatch.setattr(stock_engine, "now_iso", _clock(retry_clock))
    monkeypatch.setattr(stock_engine, "record_stock_movement", real_record)
    movement = await finalize_durable_stock_event(
        server.db, event_id=event_id, master_item_id=mid, now=claim_clock,
    )
    assert movement["stock_applied_at"] == apply_clock
    preview = await preview_financial_opening(server.db, _opening_payload(mid, 100, 1000, "retry-preview-100"))
    assert "OPENING_SNAPSHOT_MISMATCH" in [row["code"] for row in preview["blockers"]]
    assert preview["writes"] == 0
    assert await server.db[JOURNAL_ENTRIES].count_documents({}) == 0
    opening = await post_financial_opening(server.db, _opening_payload(mid, 80, 1000, "retry-post-80"), USER)
    snap = opening["stock_snapshots"][0]
    assert snap["snapshot_qty"] == 80
    assert snap["snapshot_basis"] == "last_commit_before_cutoff"
    assert snap["events_before_cutoff"] == 1
    assert snap["events_after_cutoff"] == 0
    assert await server.db[JOURNAL_ENTRIES].count_documents({"source_type": SOURCE_TYPE, "status": "posted"}) == 1
    assert (await server.db.product_stock_state.find_one({"_id": mid}))["total_qty"] == 80


async def test_claim_before_cutoff_and_apply_after_keeps_opening_at_100(monkeypatch):
    await _configure()
    mid = await _master(100)
    claim_clock = "2026-09-30T16:00:00+00:00"
    apply_clock = "2026-09-30T17:30:00+00:00"
    event_id = f"sj-{uuid.uuid4().hex[:8]}"
    monkeypatch.setattr(stock_engine, "now_iso", _clock(apply_clock))
    await _claim(
        mid, event_id, MOVEMENT_PENGIRIMAN, 20.0,
        now=claim_clock, is_print=False, reference_type="sales_delivery",
    )
    movement = await finalize_durable_stock_event(
        server.db, event_id=event_id, master_item_id=mid, now=apply_clock,
    )
    assert movement["claimed_at"] == claim_clock
    assert movement["stock_applied_at"] == apply_clock
    assert movement["date"] == apply_clock
    assert movement["balance_before"] == 100
    assert movement["balance_after"] == 80
    blocked = await preview_financial_opening(server.db, _opening_payload(mid, 80, 1000, "apply-after-80"))
    assert "OPENING_SNAPSHOT_MISMATCH" in [row["code"] for row in blocked["blockers"]]
    assert await server.db[JOURNAL_ENTRIES].count_documents({}) == 0
    opening = await post_financial_opening(server.db, _opening_payload(mid, 100, 1000, "apply-after-100"), USER)
    snap = opening["stock_snapshots"][0]
    assert snap["snapshot_qty"] == 100
    assert snap["events_before_cutoff"] == 0
    assert snap["events_after_cutoff"] == 1
    await post_inventory_issue_accounting(server.db, event_id, USER)
    await post_inventory_issue_accounting(server.db, event_id, USER)
    issues = await server.db[JOURNAL_ENTRIES].find(
        {"source_type": "inventory_issue", "reversal_of": None, "status": "posted"},
        {"_id": 0},
    ).to_list(10)
    assert len(issues) == 1
    stored = await server.db.financial_inventory_openings.find_one({"id": opening["id"]}, {"_id": 0})
    assert stored["lines"][0]["qty"] == 100
    basis = (await server.db.inventory_issue_accounting.find_one({"movement_id": event_id}, {"_id": 0}))[POSTING_BASIS_FIELD]
    assert Decimal(str(basis["issue_base_qty"])) == Decimal("20")


async def test_pending_event_blocks_opening_before_any_journal():
    await _configure()
    mid = await _master(100)
    event_id = f"sj-{uuid.uuid4().hex[:8]}"
    claimed = await _claim(
        mid, event_id, MOVEMENT_PENGIRIMAN, 20.0,
        now="2026-09-30T16:00:00+00:00", is_print=False, reference_type="sales_delivery",
    )
    assert claimed["status"] == "CLAIMED"
    state = await server.db.product_stock_state.find_one({"_id": mid})
    assert state["total_qty"] == 100
    assert state["pending_event"]["stage"] == "CLAIMED"
    preview = await preview_financial_opening(server.db, _opening_payload(mid, 100, 1000, "pending-preview"))
    assert preview["can_post"] is False
    assert preview["blockers"][0]["code"] == "STOCK_PENDING"
    assert preview["writes"] == 0
    with pytest.raises(OpeningBlocked) as exc:
        await post_financial_opening(server.db, _opening_payload(mid, 100, 1000, "pending-post"), USER)
    assert exc.value.code == "STOCK_PENDING"
    assert await server.db[JOURNAL_ENTRIES].count_documents({}) == 0
    assert await server.db.financial_inventory_openings.count_documents({"status": "POSTED"}) == 0
    assert (await server.db.product_stock_state.find_one({"_id": mid}))["total_qty"] == 100


async def test_recovery_without_stored_marker_blocks_instead_of_restamping(monkeypatch):
    await _configure()
    mid = await _master(100)
    minted = "2026-10-02T01:00:00+00:00"
    monkeypatch.setattr(stock_engine, "now_iso", _clock(minted))
    event_id = f"sj-{uuid.uuid4().hex[:8]}"
    await _claim(
        mid, event_id, MOVEMENT_PENGIRIMAN, 20.0,
        now="2026-09-30T16:00:00+00:00", is_print=False, reference_type="sales_delivery",
    )
    await server.db.product_stock_state.update_one(
        {"_id": mid},
        {
            "$inc": {"total_qty": -20, "unprinted_qty": -20, "version": 1},
            "$set": {"pending_event.stage": "STATE_APPLIED"},
            "$unset": {"pending_event.stock_applied_at": ""},
        },
    )
    recovered = await reconcile_pending_product_claim(server.db, mid)
    assert recovered["movement"].get("stock_applied_at") in (None, "")
    assert recovered["movement"].get("stock_applied_at") != minted
    assert (await server.db.product_stock_state.find_one({"_id": mid}))["total_qty"] == 80
    await _assert_unproven_blocks_journal(mid, recovered["movement"]["id"], "unstamped", qty=80)
    await _assert_unproven_blocks_journal(mid, recovered["movement"]["id"], "unstamped-100", qty=100)


async def test_repeated_balances_100_80_100_150_are_ambiguous(monkeypatch):
    await _configure()
    mid = await _master(100)
    clocks = [
        "2026-10-01T01:00:00+00:00",
        "2026-10-01T02:00:00+00:00",
        "2026-10-01T03:00:00+00:00",
    ]
    cursor = {"index": 0}

    def next_clock():
        value = clocks[cursor["index"]]
        cursor["index"] += 1
        return value

    monkeypatch.setattr(stock_engine, "now_iso", next_clock)
    steps = [
        (MOVEMENT_PENGIRIMAN, 20.0, False, "sales_delivery", 100, 80),
        (MOVEMENT_PENERIMAAN, 20.0, None, "goods_receipt", 80, 100),
        (MOVEMENT_PENERIMAAN, 50.0, None, "goods_receipt", 100, 150),
    ]
    for movement_type, qty, is_print, reference_type, before, after in steps:
        event_id = f"chain-{uuid.uuid4().hex[:8]}"
        await _claim(
            mid, event_id, movement_type, qty,
            now="2026-10-01T00:30:00+00:00", is_print=is_print, reference_type=reference_type,
        )
        movement = await finalize_durable_stock_event(
            server.db, event_id=event_id, master_item_id=mid, now="2026-10-01T00:30:00+00:00",
        )
        assert movement["balance_before"] == before
        assert movement["balance_after"] == after
        assert movement["stock_applied_at"] in clocks
    applied = await server.db.stock_movements.distinct("stock_applied_at", {"master_item_id": mid})
    assert sorted(applied) == clocks
    assert (await server.db.product_stock_state.find_one({"_id": mid}))["total_qty"] == 150
    preview = await preview_financial_opening(server.db, _opening_payload(mid, 100, 1000, "chain-preview"))
    assert preview["blockers"][0]["code"] == "STOCK_CHAIN_AMBIGUOUS"
    assert preview["writes"] == 0
    with pytest.raises(OpeningBlocked) as exc:
        await post_financial_opening(server.db, _opening_payload(mid, 100, 1000, "chain-post"), USER)
    assert exc.value.code == "STOCK_CHAIN_AMBIGUOUS"
    assert await server.db[JOURNAL_ENTRIES].count_documents({}) == 0
    assert await server.db.financial_inventory_openings.count_documents({"status": "POSTED"}) == 0


async def _assert_opname_opening_blocked(mid, request_id):
    movement = await server.db.stock_movements.find_one({"master_item_id": mid, "source": "STOCK_OPNAME"}, {"_id": 0})
    assert movement is not None
    assert movement["reference_type"] == "stock_opname"
    assert not str(movement.get("stock_applied_at") or "").strip()
    assert str(movement.get("created_at") or "").strip()
    preview = await preview_financial_opening(server.db, _opening_payload(mid, 80, 1000, f"{request_id}-80"))
    assert preview["writes"] == 0
    assert preview["can_post"] is False
    blocker = preview["blockers"][0]
    assert blocker["code"] == "STOCK_COMMIT_UNPROVEN"
    assert blocker["movement_id"] == movement["id"]
    assert movement["id"] in blocker["message"]
    assert "created_at" in blocker["evidence_reason"]
    for qty, suffix in ((80, "after"), (100, "before")):
        with pytest.raises(OpeningBlocked) as exc:
            await post_financial_opening(
                server.db, _opening_payload(mid, qty, 1000, f"{request_id}-{suffix}"), USER,
            )
        assert exc.value.code == "STOCK_COMMIT_UNPROVEN"
        assert exc.value.extra["blockers"][0]["movement_id"] == movement["id"]
    assert await server.db[JOURNAL_ENTRIES].count_documents({}) == 0
    assert await server.db.financial_inventory_openings.count_documents({"status": "POSTED"}) == 0


async def test_apply_stock_opname_across_cutoff_blocks_without_using_created_at(monkeypatch):
    """Real apply_stock_opname has no stock_applied_at. created_at must not place it."""
    await _configure()
    clocks = (
        "2026-09-15T02:00:00+00:00",
        "2026-10-02T02:00:00+00:00",
    )
    for index, clock in enumerate(clocks):
        mid = await _master(100)
        monkeypatch.setattr(stock_engine_v2, "now_iso", _clock(clock))
        result = await apply_stock_opname(
            server.db,
            master_item_id=mid,
            target_stock_current_qty=80,
            outstanding_so_qty=0,
            note="opname cutoff",
            user=USER,
            client_request_id=f"opname-{index}",
        )
        assert result["opname_action"] == "KOREKSI"
        assert result["movement"]["created_at"] == clock
        assert not str(result["movement"].get("stock_applied_at") or "").strip()
        assert result["stock_state"]["total_qty"] == 80
        await _assert_opname_opening_blocked(mid, f"opname-cutoff-{index}")
        stored = await server.db.stock_movements.find_one({"master_item_id": mid, "source": "STOCK_OPNAME"})
        assert stored["balance_before"] == 100
        assert stored["balance_after"] == 80

    raw = f"mid-{uuid.uuid4().hex[:8]}"
    await server.db.master_items.insert_one({
        "id": raw,
        "item_code": raw,
        "item_name": raw,
        "item_type": "INVENTORY",
        "unit": "PCS",
        "is_active": True,
    })
    monkeypatch.setattr(stock_engine_v2, "now_iso", _clock("2026-09-15T02:00:00+00:00"))
    opening_opname = await apply_stock_opname(
        server.db,
        master_item_id=raw,
        target_stock_current_qty=100,
        outstanding_so_qty=0,
        note="opname saldo awal",
        user=USER,
        client_request_id="opname-saldo-awal",
    )
    assert opening_opname["opname_action"] == "SALDO_AWAL"
    assert not str(opening_opname["movement"].get("stock_applied_at") or "").strip()
    await _assert_opname_opening_blocked(raw, "opname-saldo-awal")


async def _bare_master():
    mid = f"mid-{uuid.uuid4().hex[:8]}"
    await server.db.master_items.insert_one({
        "id": mid,
        "item_code": mid,
        "item_name": mid,
        "item_type": "INVENTORY",
        "unit": "PCS",
        "is_active": True,
    })
    return mid


async def test_reference_type_stock_opname_without_marker_blocks_for_any_source():
    """Relabeling source does not create apply evidence."""
    await _configure()
    for source in ("STOCK_OPNAME", "", "BUKAN_OPNAME", None):
        mid = await _master(80)
        movement_id = f"opname-src-{uuid.uuid4().hex[:8]}"
        await _stock_effect(
            mid,
            reference_type="stock_opname",
            reference_id="op-unproven",
            before=100,
            after=80,
            created_at="2026-09-15T02:00:00Z",
            date="2026-09-15",
            updated_at="2026-09-15T02:00:00Z",
            movement_id=movement_id,
            movement_type="KOREKSI",
            source=source,
        )
        label = source if source is not None else "missing"
        await _assert_unproven_blocks_journal(mid, movement_id, f"src-{label}-{movement_id}", qty=80)
        await _assert_unproven_blocks_journal(mid, movement_id, f"src100-{label}-{movement_id}", qty=100)


async def test_manual_opening_balance_without_applied_marker_blocks():
    await _configure()
    mid = await _bare_master()
    result = await initialize_opening_balance(server.db, mid, {
        "unprinted_qty": 100,
        "printed_qty": 0,
        "total_qty": 100,
        "base_uom": "PCS",
        "initialization_source": "MANUAL",
        "note": "saldo awal manual",
    }, USER)
    movement = result["movement"]
    assert movement["movement_type"] == MOVEMENT_SALDO_AWAL
    assert movement["reference_type"] == "opening_balance"
    assert movement["source"] == "MANUAL"
    assert movement["id"] == f"opening_balance:{mid}"
    assert not str(movement.get("stock_applied_at") or "").strip()
    assert str(movement.get("created_at") or "").strip()
    state = await server.db.product_stock_state.find_one({"_id": mid})
    assert state["status"] == "INITIALIZED"
    assert state.get("pending_event") in (None, {})
    await _assert_unproven_blocks_journal(mid, movement["id"], "manual-saldo", qty=100)


async def test_opening_balance_correction_without_applied_marker_blocks():
    await _configure()
    mid = await _master(100)
    result = await correct_opening_balance(
        server.db,
        mid,
        {
            "unprinted_qty": 80,
            "printed_qty": 0,
            "note": "koreksi saldo awal tanpa bukti penerapan",
            "client_request_id": "corr-1",
        },
        USER,
        app_env="local",
        mongo_url=os.environ["MONGO_URL"],
    )
    movement = result["movement"]
    assert movement["movement_type"] == MOVEMENT_KOREKSI
    assert movement["reference_type"] == "opening_balance_correction"
    assert movement["source"] == "KOREKSI_SALDO_AWAL"
    assert movement["id"] == f"opening_correction:{mid}:corr-1"
    assert not str(movement.get("stock_applied_at") or "").strip()
    state = await server.db.product_stock_state.find_one({"_id": mid})
    assert state.get("pending_event") in (None, {})
    assert state["total_qty"] == 80
    await _assert_unproven_blocks_journal(mid, movement["id"], "koreksi-80", qty=80)
    await _assert_unproven_blocks_journal(mid, movement["id"], "koreksi-100", qty=100)


async def test_missing_applied_marker_blocks_before_near_and_after_cutoff():
    """created_at, document date, and updated_at do not place a cutoff balance."""
    await _configure()
    clocks = (
        "2026-09-15T02:00:00Z",
        "2026-09-30T16:59:59Z",
        "2026-09-30T17:00:00Z",
        "2026-10-02T01:00:00Z",
    )
    for clock in clocks:
        mid = await _master(80)
        movement_id = f"bare-{uuid.uuid4().hex[:8]}"
        await _stock_effect(
            mid,
            reference_type="pengiriman",
            reference_id=movement_id,
            before=100,
            after=80,
            created_at=clock,
            date=clock,
            updated_at=clock,
            movement_id=movement_id,
            source="",
        )
        await _assert_unproven_blocks_journal(mid, movement_id, f"bare80-{movement_id}", qty=80)
        await _assert_unproven_blocks_journal(mid, movement_id, f"bare100-{movement_id}", qty=100)
        stored = await server.db.stock_movements.find_one({"id": movement_id}, {"_id": 0})
        assert stored["created_at"] == clock
        assert not str(stored.get("stock_applied_at") or "").strip()


async def test_unreadable_applied_marker_does_not_fall_back_to_created_at():
    await _configure()
    mid = await _master(80)
    movement_id = "mov-bad-marker"
    await _stock_effect(
        mid,
        reference_type="pengiriman",
        reference_id="sj-bad",
        before=100,
        after=80,
        created_at="2026-09-15T02:00:00Z",
        date="2026-09-15",
        stock_applied_at="bukan-waktu",
        movement_id=movement_id,
    )
    preview = await preview_financial_opening(server.db, _opening_payload(mid, 80, 1000, "bad-marker-preview"))
    assert preview["writes"] == 0
    assert preview["blockers"][0]["code"] == "STOCK_COMMIT_UNPROVEN"
    assert preview["blockers"][0]["movement_id"] == movement_id
    assert "tidak dapat dibaca" in preview["blockers"][0]["evidence_reason"]
    await _assert_unproven_blocks_journal(mid, movement_id, "bad-marker-80", qty=80)
    await _assert_unproven_blocks_journal(mid, movement_id, "bad-marker-100", qty=100)


async def test_applied_marker_stays_valid_when_source_label_changes():
    await _configure()
    posted = None
    for source in ("STOCK_OPNAME", "", "BUKAN_OPNAME", "MANUAL"):
        mid = await _master(80)
        movement_id = f"marked-{uuid.uuid4().hex[:8]}"
        await _stock_effect(
            mid,
            reference_type="stock_opname",
            reference_id="op-proven",
            before=100,
            after=80,
            created_at="2026-09-01T00:00:00Z",
            date="2026-09-01",
            updated_at="2026-09-01T00:00:00Z",
            stock_applied_at="2026-10-02T01:00:00Z",
            movement_id=movement_id,
            movement_type="KOREKSI",
            source=source,
        )
        preview = await preview_financial_opening(
            server.db, _opening_payload(mid, 100, 1000, f"marked-preview-{movement_id}"),
        )
        assert preview["can_post"] is True, preview["blockers"]
        assert preview["snapshots"][0]["snapshot_qty"] == 100
        assert preview["snapshots"][0]["snapshot_basis"] == "balance_before_first_commit_at_or_after_cutoff"
        if posted is None:
            posted = await post_financial_opening(
                server.db, _opening_payload(mid, 100, 1000, f"marked-post-{movement_id}"), USER,
            )
            assert posted["lines"][0]["qty"] == 100
            assert posted["stock_snapshots"][0]["snapshot_qty"] == 100
    assert posted is not None
    assert await server.db[JOURNAL_ENTRIES].count_documents({"source_type": SOURCE_TYPE, "reversal_of": None}) == 1


async def test_durable_movement_with_applied_marker_still_posts():
    await _configure()
    mid = await _master(80)
    movement_id = "durable-applied"
    await _stock_effect(
        mid,
        reference_type="pengiriman",
        reference_id="sj-durable",
        before=100,
        after=80,
        created_at="2026-09-30T16:00:00Z",
        claimed_at="2026-09-30T16:00:00Z",
        stock_applied_at="2026-10-01T01:00:00Z",
        movement_id=movement_id,
        source="GUDANG",
    )
    opening = await post_financial_opening(server.db, _opening_payload(mid, 100, 1000, "durable-ok"), USER)
    assert opening["status"] == "POSTED"
    assert opening["stock_snapshots"][0]["snapshot_qty"] == 100
    assert opening["stock_snapshots"][0]["events_before_cutoff"] == 0
    assert await server.db[JOURNAL_ENTRIES].count_documents({"source_type": SOURCE_TYPE, "source_id": opening["id"]}) == 1


def _pengiriman(mid, qty, when="2026-10-05T03:00:00Z"):
    return {
        "id": str(uuid.uuid4()),
        "movement_type": "PENGIRIMAN",
        "master_item_id": mid,
        "date": when,
        "created_at": when,
        "transaction_qty": qty,
        "transaction_uom": "PCS",
        "conversion_factor": 1,
        "base_qty": qty,
        "base_uom": "PCS",
        "status": "POSTED",
    }


async def test_same_user_and_request_cannot_take_an_active_lease():
    await _configure()
    mid = await _master()
    import services.financial_inventory_opening as fio

    entered = asyncio.Event()
    release = asyncio.Event()
    original = fio._after_step

    async def pause(step):
        if step == "lines":
            entered.set()
            await release.wait()

    fio._after_step = pause
    task = asyncio.create_task(
        post_financial_opening(server.db, _opening_payload(mid, 100, 1000, "lease-same"), USER)
    )
    try:
        await asyncio.wait_for(entered.wait(), timeout=10)
        with pytest.raises(OpeningBlocked) as exc:
            await asyncio.wait_for(
                post_financial_opening(server.db, _opening_payload(mid, 100, 1000, "lease-same"), USER),
                timeout=5,
            )
        assert exc.value.code == "OPENING_IN_PROGRESS"
        assert await server.db[JOURNAL_ENTRIES].count_documents({"source_type": SOURCE_TYPE}) == 0
    finally:
        release.set()
        fio._after_step = original
    saved = await task
    assert saved["status"] == "POSTED"
    assert await server.db[JOURNAL_ENTRIES].count_documents({
        "source_type": SOURCE_TYPE,
        "source_id": saved["id"],
        "status": "posted",
        "reversal_of": None,
    }) == 1


async def test_old_worker_cannot_write_after_a_new_lease_token():
    await _configure()
    mid = await _master()
    import services.financial_inventory_opening as fio
    from datetime import datetime, timedelta, timezone

    original = fio._after_step
    stolen = {}

    async def hijack(step):
        if step != "lines":
            return
        past = (datetime.now(timezone.utc) - timedelta(seconds=5)).isoformat()
        await server.db[fio.CLAIMS].update_one(
            {"_id": fio.CLAIM_ID},
            {"$set": {"lease_expires_at": past}},
        )
        stolen["lease"] = await fio._acquire_lease(server.db, "fence-req")

    fio._after_step = hijack
    try:
        with pytest.raises(OpeningBlocked) as exc:
            await post_financial_opening(server.db, _opening_payload(mid, 100, 1000, "fence-req"), USER)
        assert exc.value.code == "OPENING_LEASE_LOST"
    finally:
        fio._after_step = original
    assert await server.db[JOURNAL_ENTRIES].count_documents({"source_type": SOURCE_TYPE}) == 0
    claim = await server.db[fio.CLAIMS].find_one({"_id": fio.CLAIM_ID})
    assert claim["lease_token"] == stolen["lease"]["lease_token"]
    assert claim["state"] == "BUILDING"


async def test_reversal_and_cogs_cannot_both_commit():
    await _configure()
    mid = await _master()
    opening = await post_financial_opening(server.db, _opening_payload(mid, 100, 1000, "race-open"), USER)
    movement = _pengiriman(mid, 10)
    await server.db.stock_movements.insert_one(movement)
    import services.financial_inventory_opening as fio

    checked = asyncio.Event()
    cogs_finished = asyncio.Event()
    real_reverse = fio.reverse_journal

    async def reverse_after_cogs(*args, **kwargs):
        checked.set()
        await cogs_finished.wait()
        return await real_reverse(*args, **kwargs)

    fio.reverse_journal = reverse_after_cogs
    barrier = asyncio.Barrier(2)

    async def do_reverse():
        await barrier.wait()
        try:
            await reverse_financial_opening(server.db, opening["id"], USER, "race reversal")
            return "reversed"
        except OpeningBlocked as exc:
            return exc.code

    async def do_cogs():
        await barrier.wait()
        await checked.wait()
        try:
            await post_inventory_issue_accounting(server.db, movement["id"], USER)
            return "posted"
        except HTTPException as exc:
            detail = exc.detail if isinstance(exc.detail, dict) else {}
            return detail.get("code") or "http"
        finally:
            cogs_finished.set()

    try:
        results = await asyncio.gather(do_reverse(), do_cogs())
    finally:
        fio.reverse_journal = real_reverse
    stored = await server.db.financial_inventory_openings.find_one({"id": opening["id"]}, {"_id": 0})
    issue = await server.db.inventory_issue_accounting.find_one({"movement_id": movement["id"]}, {"_id": 0}) or {}
    basis = issue.get("accounting_posting_basis") or {}
    prefix = f"opening:{opening['id']}:"
    consumed = any(
        str(row.get("acquisition_key") or "").startswith(prefix)
        for row in basis.get("consumed_acquisitions") or []
    ) or any(str(key).startswith(prefix) for key in basis.get("pool_acquisition_keys") or [])
    assert not (stored["status"] == "REVERSED" and consumed), (results, stored["status"], consumed)
    assert stored["status"] in {"POSTED", "REVERSED"}
    if stored["status"] == "REVERSED":
        assert "reversed" in results
        assert not consumed
    else:
        assert "OPENING_ALREADY_CONSUMED" in results
        assert consumed


async def test_multi_item_reversal_locks_in_sorted_order():
    await _configure()
    first = await _master()
    second = await _master()
    opening = await post_financial_opening(server.db, {
        "client_request_id": "two-items",
        "effective_date": EFFECTIVE,
        "lines": [
            {"master_item_id": second, "qty": 4, "unit_cost": 1000, "base_uom": "PCS", "source": "APPROVED_OPENING"},
            {"master_item_id": first, "qty": 6, "unit_cost": 1000, "base_uom": "PCS", "source": "APPROVED_OPENING"},
        ],
    }, USER)
    import services.inventory_financial_locks as locks

    seen = []
    real = locks.acquire_item_lock

    async def spy(db, key, holder, **kwargs):
        seen.append(key)
        return await real(db, key, holder, **kwargs)

    locks.acquire_item_lock = spy
    try:
        reversed_opening = await reverse_financial_opening(server.db, opening["id"], USER, "urut kunci")
    finally:
        locks.acquire_item_lock = real
    assert reversed_opening["status"] == "REVERSED"
    assert seen == sorted(seen)
    assert len(seen) == 2


async def test_reversed_journal_is_not_an_acquisition_and_get_does_not_write():
    from services.inventory_issue_accounting import load_live_acquisition_coverage
    from services.journal_core import reverse_journal

    await _configure()
    mid = await _master()
    opening = await post_financial_opening(server.db, _opening_payload(mid, 100, 1000, "rev-crash"), USER)
    await reverse_journal(
        server.db,
        opening["posted_journal_id"],
        source_type="financial_inventory_opening_reversal",
        source_id=f"{opening['id']}:reversal",
        posted_by=USER,
        description="crash sesudah jurnal",
    )
    before_openings = await server.db.financial_inventory_openings.count_documents({})
    before_journals = await server.db[JOURNAL_ENTRIES].count_documents({})
    book = await build_financial_book_reconciliation(server.db)
    acquisitions, report = await load_live_acquisition_coverage(server.db)
    listed = await server.db.financial_inventory_openings.find_one({"id": opening["id"]}, {"_id": 0})
    assert book["writes"] == 0
    assert await server.db.financial_inventory_openings.count_documents({}) == before_openings
    assert await server.db[JOURNAL_ENTRIES].count_documents({}) == before_journals
    assert listed["status"] == "POSTED"
    assert book["reconciliation_status"] != "MATCH"
    assert book["financial_remaining"] is None
    assert book["opening_value"] is None
    assert book["blockers"][0]["code"]
    assert all(row.get("source") != "APPROVED_OPENING" for row in acquisitions)
    assert f"m:{mid}" in report["item_keys"]
    movement = _pengiriman(mid, 10)
    await server.db.stock_movements.insert_one(movement)
    with pytest.raises(HTTPException) as exc:
        await post_inventory_issue_accounting(server.db, movement["id"], USER)
    assert exc.value.detail["code"] == "INVALID_ACQUISITION_BASIS"
    recovered = await recover_financial_opening(server.db, client_request_id="rev-crash", user=USER)
    assert recovered["status"] == "REVERSED"
    assert await server.db[JOURNAL_ENTRIES].count_documents({
        "source_type": SOURCE_TYPE,
        "source_id": opening["id"],
        "status": "posted",
        "reversal_of": None,
    }) == 0


async def test_linked_voucher_reversal_cannot_skip_consumption():
    from services.inventory_issue_accounting import load_live_acquisitions

    accounts = await _configure(offset=False)
    mid = await _master()
    draft = await create_draft(server.db, {
        "cutoff_date": EFFECTIVE,
        "description": "Saldo awal tertaut",
        "lines": [
            {"account_id": accounts["inventory"]["id"], "debit": 100000, "credit": 0},
            {"account_id": accounts["equity"]["id"], "debit": 0, "credit": 100000},
        ],
    }, user=USER)
    voucher = await post_voucher(server.db, draft["id"], user=USER)
    opening = await post_financial_opening(server.db, _opening_payload(mid, 100, 1000, "linked-consume"), USER)
    movement = _pengiriman(mid, 5, "2026-10-06T03:00:00Z")
    await server.db.stock_movements.insert_one(movement)
    await post_inventory_issue_accounting(server.db, movement["id"], USER)
    with pytest.raises(OpeningBalanceConflictError):
        await reverse_voucher(server.db, voucher["id"], user=USER)
    journal = await server.db[JOURNAL_ENTRIES].find_one({"id": opening["posted_journal_id"]}, {"_id": 0})
    assert journal["status"] == "posted"
    stored = await server.db.financial_inventory_openings.find_one({"id": opening["id"]}, {"_id": 0})
    assert stored["status"] == "POSTED"
    live = await load_live_acquisitions(server.db)
    assert any(str(row.get("acquisition_key") or "").startswith(f"opening:{opening['id']}:") for row in live)

    accounts = await _configure(offset=False)
    mid = await _master()
    draft = await create_draft(server.db, {
        "cutoff_date": "2026-10-02",
        "description": "Saldo awal tanpa konsumsi",
        "lines": [
            {"account_id": accounts["inventory"]["id"], "debit": 20000, "credit": 0},
            {"account_id": accounts["equity"]["id"], "debit": 0, "credit": 20000},
        ],
    }, user=USER)
    voucher = await post_voucher(server.db, draft["id"], user=USER)
    opening = await post_financial_opening(
        server.db, _opening_payload(mid, 20, 1000, "linked-free", effective="2026-10-02"), USER,
    )
    await reverse_voucher(server.db, voucher["id"], user=USER)
    after_openings = await server.db.financial_inventory_openings.count_documents({})
    after_journals = await server.db[JOURNAL_ENTRIES].count_documents({})
    book = await build_financial_book_reconciliation(server.db)
    assert book["writes"] == 0
    assert await server.db.financial_inventory_openings.count_documents({}) == after_openings
    assert await server.db[JOURNAL_ENTRIES].count_documents({}) == after_journals
    assert book["reconciliation_status"] == "BLOCKED"
    assert book["opening_value"] is None
    assert book["financial_remaining"] is None
    live = await load_live_acquisitions(server.db)
    assert all(not str(row.get("acquisition_key") or "").startswith(f"opening:{opening['id']}:") for row in live)


async def test_retry_uses_frozen_accounts_after_settings_change():
    from services.purchase_accounting_settings import update_purchase_accounting_settings
    import services.financial_inventory_opening as fio

    accounts = await _configure()
    mid = await _master()
    original = fio._after_step

    async def crash(step):
        if step == "lines":
            raise RuntimeError("crash after frozen doc")

    fio._after_step = crash
    try:
        with pytest.raises(OpeningBlocked):
            await post_financial_opening(server.db, _opening_payload(mid, 100, 1000, "frozen-retry"), USER)
    finally:
        fio._after_step = original
    doc = await server.db.financial_inventory_openings.find_one({"client_request_id": "frozen-retry"}, {"_id": 0})
    assert doc["status"] != "POSTED"
    frozen_inventory = doc["inventory_account_id"]
    frozen_offset = doc["offset_account_id"]
    new_inventory = await _coa("13", "Persediaan baru", "INTR")
    new_offset = await _coa("32", "Modal baru", "EQTY")
    await update_purchase_accounting_settings(server.db, {
        "payable_account_id": accounts["payable"]["id"],
        "purchase_clearing_account_id": accounts["clearing"]["id"],
        "inventory_account_id": new_inventory["id"],
        "cogs_account_id": accounts["cogs"]["id"],
        "inventory_accounting_effective_date": "2026-01-01",
        "inventory_opening_offset_account_id": new_offset["id"],
    }, USER)
    recovered = await post_financial_opening(server.db, _opening_payload(mid, 100, 1000, "frozen-retry"), USER)
    assert recovered["inventory_account_id"] == frozen_inventory
    assert recovered["offset_account_id"] == frozen_offset
    assert recovered["status"] == "POSTED"
    journal = await server.db[JOURNAL_ENTRIES].find_one({"id": recovered["posted_journal_id"]}, {"_id": 0})
    lines = await server.db[JOURNAL_LINES].find({"journal_id": journal["id"]}, {"_id": 0}).to_list(10)
    debited = {row["account_id"] for row in lines if Decimal(str(row.get("debit") or 0)) > 0}
    credited = {row["account_id"] for row in lines if Decimal(str(row.get("credit") or 0)) > 0}
    assert debited == {frozen_inventory}
    assert credited == {frozen_offset}
    assert new_inventory["id"] not in debited
    assert new_offset["id"] not in credited
    assert journal["date"] == "2026-10-01"
    assert journal["source_type"] == SOURCE_TYPE
    assert journal["source_id"] == recovered["id"]


async def test_expired_lease_token_cannot_clear_the_new_holder():
    from datetime import datetime, timedelta, timezone
    import services.financial_inventory_opening as fio

    past = (datetime.now(timezone.utc) - timedelta(seconds=5)).isoformat()
    await server.db[fio.CLAIMS].update_one(
        {"_id": fio.CLAIM_ID},
        {"$set": {
            "state": "BUILDING",
            "client_request_id": "fence",
            "lease_token": "old-token",
            "lease_expires_at": past,
        }},
        upsert=True,
    )
    claimed = await fio._acquire_lease(server.db, "fence")
    assert claimed["lease_token"] != "old-token"
    await fio._release_lease(server.db, "old-token", "IDLE")
    still = await server.db[fio.CLAIMS].find_one({"_id": fio.CLAIM_ID})
    assert still["lease_token"] == claimed["lease_token"]
    assert still["state"] == "BUILDING"
    await fio._release_lease(server.db, claimed["lease_token"], "IDLE")
    released = await server.db[fio.CLAIMS].find_one({"_id": fio.CLAIM_ID})
    assert not released.get("lease_token")


async def test_consumed_opening_journal_reverse_stays_posted():
    from services.journal_core import JournalConflictError, reverse_journal

    await _configure()
    mid = await _master()
    opening = await post_financial_opening(server.db, _opening_payload(mid, 100, 1000, "guard-journal"), USER)
    movement = _pengiriman(mid, 10)
    await server.db.stock_movements.insert_one(movement)
    await post_inventory_issue_accounting(server.db, movement["id"], USER)
    before = await server.db[JOURNAL_ENTRIES].count_documents({})
    with pytest.raises(JournalConflictError):
        await reverse_journal(
            server.db,
            opening["posted_journal_id"],
            source_type="financial_inventory_opening_reversal",
            source_id=f"{opening['id']}:blocked",
            posted_by=USER,
            description="jangan balik setelah konsumsi",
        )
    assert await server.db[JOURNAL_ENTRIES].count_documents({}) == before
    journal = await server.db[JOURNAL_ENTRIES].find_one({"id": opening["posted_journal_id"]}, {"_id": 0})
    assert journal["status"] == "posted"
    stored = await server.db.financial_inventory_openings.find_one({"id": opening["id"]}, {"_id": 0, "status": 1})
    assert stored["status"] == "POSTED"
    listed = await server.db.financial_inventory_openings.find_one({"id": opening["id"]}, {"_id": 0})
    from services.financial_inventory_opening import list_financial_openings

    rows = await list_financial_openings(server.db)
    match = next(row for row in rows if row["id"] == opening["id"])
    assert match["journal_authority"] == "valid"
    assert listed["status"] == "POSTED"


async def test_snapshot_is_not_backdated_and_stale_snapshot_cannot_post():
    from datetime import datetime, timezone
    import services.financial_inventory_opening as fio

    await _configure()
    mid = await _bare_master()
    await initialize_opening_balance(server.db, mid, {
        "unprinted_qty": 100,
        "printed_qty": 0,
        "total_qty": 100,
        "base_uom": "PCS",
        "initialization_source": "MANUAL",
        "client_request_id": "snap-physical",
        "note": "saldo awal snapshot",
    }, USER)
    before_qty = (await server.db.product_stock_state.find_one({"master_item_id": mid}))["total_qty"]
    original = fio._utc_now
    fio._utc_now = lambda: datetime(2026, 10, 4, 17, 0, tzinfo=timezone.utc)
    try:
        captured = await fio.capture_financial_opening_snapshot(server.db, {
            "client_request_id": "snap-1",
            "lines": [{"master_item_id": mid, "base_uom": "PCS"}],
        }, USER)
        again = await fio.capture_financial_opening_snapshot(server.db, {
            "client_request_id": "snap-1",
            "lines": [{"master_item_id": mid, "base_uom": "PCS"}],
        }, USER)
    finally:
        fio._utc_now = original
    after_qty = (await server.db.product_stock_state.find_one({"master_item_id": mid}))["total_qty"]
    assert after_qty == before_qty
    assert captured["id"] == again["id"]
    assert captured["business_date"] == "2026-10-05"
    assert captured["backdated"] is False
    assert await server.db.financial_inventory_opening_snapshots.count_documents({"client_request_id": "snap-1"}) == 1
    with pytest.raises(OpeningBlocked) as exc:
        await post_financial_opening(server.db, {
            **_opening_payload(mid, 100, 1000, "snap-back"),
            "snapshot_id": captured["id"],
            "effective_date": "2026-10-01",
        }, USER)
    assert exc.value.code == "OPENING_SNAPSHOT_BACKDATED"
    await server.db.product_stock_state.update_one({"master_item_id": mid}, {"$set": {"version": 9, "total_qty": 90}})
    stale = await preview_financial_opening(server.db, {
        **_opening_payload(mid, 100, 1000, "snap-stale-preview", effective="2026-10-05"),
        "snapshot_id": captured["id"],
    })
    assert stale["writes"] == 0
    assert stale["can_post"] is False
    assert stale["blockers"][0]["code"] == "STALE_SNAPSHOT"
    with pytest.raises(OpeningBlocked) as exc:
        await post_financial_opening(server.db, {
            **_opening_payload(mid, 100, 1000, "snap-stale", effective="2026-10-05"),
            "snapshot_id": captured["id"],
        }, USER)
    assert exc.value.code == "STALE_SNAPSHOT"
    await server.db.product_stock_state.update_one({"master_item_id": mid}, {"$set": {"version": 1, "total_qty": 100}})
    posted = await post_financial_opening(server.db, {
        **_opening_payload(mid, 100, 1000, "snap-ok", effective="2026-10-05"),
        "snapshot_id": captured["id"],
    }, USER)
    assert posted["status"] == "POSTED"
    assert posted["effective_date"] == "2026-10-05"
    assert posted["snapshot_id"] == captured["id"]


async def test_snapshot_capture_rejects_a_boundary_that_changes_mid_read():
    import services.financial_inventory_opening as fio

    await _configure()
    mid = await _bare_master()
    await initialize_opening_balance(server.db, mid, {
        "unprinted_qty": 100,
        "printed_qty": 0,
        "total_qty": 100,
        "base_uom": "PCS",
        "initialization_source": "MANUAL",
        "client_request_id": "snap-race-physical",
        "note": "saldo awal snapshot",
    }, USER)
    original = fio._read_item_boundary
    calls = {"n": 0}

    async def shifting(db, master_item_id, base_uom, *, settle_pending):
        observed = await original(db, master_item_id, base_uom, settle_pending=settle_pending)
        calls["n"] += 1
        if calls["n"] >= 2:
            state = dict(observed.get("state") or {})
            state["version"] = int(state.get("version") or 0) + 7
            state["total_qty"] = 80
            observed = {**observed, "state": state}
        return observed

    fio._read_item_boundary = shifting
    try:
        with pytest.raises(OpeningBlocked) as exc:
            await fio.capture_financial_opening_snapshot(server.db, {
                "client_request_id": "snap-race",
                "lines": [{"master_item_id": mid, "base_uom": "PCS"}],
            }, USER)
        assert exc.value.code == "STALE_SNAPSHOT"
    finally:
        fio._read_item_boundary = original
    assert await server.db.financial_inventory_opening_snapshots.count_documents({"client_request_id": "snap-race"}) == 0
    stored = await server.db.product_stock_state.find_one({"master_item_id": mid})
    assert stored["total_qty"] == 100
    assert stored["version"] == 1


async def test_operational_writer_moves_stock_and_books_match():
    from services.physical_stock_engine import handle_goods_receipt_created, handle_sales_delivery_created

    await _configure()
    mid = await _master(100)
    opening = await post_financial_opening(server.db, _opening_payload(mid, 100, 1000, "writer-open"), USER)
    assert opening["status"] == "POSTED"
    start = await server.db.product_stock_state.find_one({"master_item_id": mid})
    assert start["total_qty"] == 100

    po_id = f"po-{uuid.uuid4().hex[:8]}"
    line_id = f"ln-{uuid.uuid4().hex[:8]}"
    await server.db.purchase_orders.insert_one({
        "id": po_id,
        "po_number": "PB-WRITER",
        "supplier": "Supplier",
        "cancelled": False,
        "physical_stock_snapshot": {"enabled": True, "stock_in_trigger": "goods_receipt"},
        "lines": [{
            "line_id": line_id,
            "ordered_qty": 50,
            "unit_price": 1200,
            "unit": "PCS",
            "master_item_id": mid,
        }],
    })
    rid = str(uuid.uuid4())
    receipt = {
        "id": rid,
        "receipt_number": "GR-WRITER",
        "purchase_order_id": po_id,
        "po_number": "PB-WRITER",
        "supplier": "Supplier",
        "receipt_date": "2026-10-02",
        "cancelled": False,
        "lines": [{
            "line_id": "gl-writer",
            "purchase_order_line_id": line_id,
            "received_qty": 50,
            "unit": "PCS",
            "master_item_id": mid,
            "unit_price": 1200,
        }],
    }
    await server.db.purchase_goods_receipts.insert_one(dict(receipt))
    import services.physical_stock_engine as stock_engine

    original_now = stock_engine.now_iso
    stock_engine.now_iso = lambda: "2026-10-05T03:00:00+00:00"
    try:
        receipts = await handle_goods_receipt_created(server.db, receipt, USER)
        assert len(receipts) == 1
        after_gr = await server.db.product_stock_state.find_one({"master_item_id": mid})
        assert after_gr["total_qty"] == 150
        assert after_gr["unprinted_qty"] == 150
        posted_gr = await post_purchase_goods_receipt_accounting(server.db, rid, USER, serialize_in_process=False)
        assert isinstance(posted_gr, dict)
        order = {
            "id": "so-writer",
            "po_number": "SO-WRITER",
            "customer": "Pelanggan Uji",
            "physical_stock_snapshot": {"enabled": True, "stock_out_trigger": "sales_delivery"},
            "line_items": [{"line_idx": 0, "line_id": "ol-writer", "master_item_id": mid, "quantity": 15, "unit": "PCS"}],
        }
        delivery = {
            "id": "sj-writer",
            "sj_number": "SJ-WRITER",
            "customer": "Pelanggan Uji",
            "line_items": [{
                "line_id": "dl-writer",
                "line_idx": 0,
                "qty": 15,
                "unit": "PCS",
                "master_item_id": mid,
                "production_process": "non_print",
            }],
        }
        deliveries = await handle_sales_delivery_created(server.db, order, delivery, USER)
    finally:
        stock_engine.now_iso = original_now
    assert len(deliveries) == 1
    after_out = await server.db.product_stock_state.find_one({"master_item_id": mid})
    assert after_out["total_qty"] == 135
    movement_id = str(deliveries[0].get("id") or "")
    assert movement_id
    await post_inventory_issue_accounting(server.db, movement_id, USER)
    book = await build_financial_book_reconciliation(server.db)
    assert book["opening_qty"] == 100.0
    assert book["goods_receipt_qty"] == 50.0
    assert book["cogs_qty"] == 15.0
    assert book["financial_qty_remaining"] == 135.0
    assert book["financial_remaining"] == 144000.0
    assert book["inventory_gl_balance"] == 144000.0
    assert book["reconciliation_status"] == "MATCH"
    assert book["pending_cogs_count"] == 0
    assert book["historical_as_of"] is False
    physical = await server.db.product_stock_state.find_one({"master_item_id": mid})
    assert physical["total_qty"] == 135


async def test_books_sales_expense_and_reports_match_journals():
    from services.commercial_activity_reports import purchase_activity_report, sales_activity_report
    from services.general_journal import create_draft as create_general_draft
    from services.general_journal import post_voucher as post_general_voucher
    from services.profit_loss import get_profit_loss
    from services.sales_accounting_settings import SETTINGS_KEY as SALES_SETTINGS

    accounts = await _configure()
    revenue = await _coa("41", "Pendapatan", "REVE")
    receivable = await _coa("11", "Piutang", "AREC")
    tax = await _coa("22", "PPN", "OCLY")
    expense = await _coa("61", "Beban operasional", "EXPS")
    bank = await _coa("12", "Kas", "BANK")
    mid = await _master()
    await post_financial_opening(server.db, _opening_payload(mid, 100, 1000, "e2e-open"), USER)
    rid = await _gr(mid, 50, 1200, "2026-10-02", post=True)
    mov_id = str(uuid.uuid4())
    await server.db.stock_movements.insert_one({
        "id": mov_id,
        "movement_type": "PENGIRIMAN",
        "master_item_id": mid,
        "date": "2026-10-05T03:00:00Z",
        "transaction_qty": 15,
        "transaction_uom": "PCS",
        "conversion_factor": 1,
        "base_qty": 15,
        "base_uom": "PCS",
        "reference_type": "sales_delivery",
        "reference_number": "SJ-E2E",
        "status": "POSTED",
    })
    await post_inventory_issue_accounting(server.db, mov_id, USER)
    book = await build_financial_book_reconciliation(server.db)
    assert book["financial_remaining"] == 144000.0
    assert book["inventory_gl_balance"] == 144000.0
    assert book["opening_qty"] == 100.0
    assert book["goods_receipt_qty"] == 50.0
    assert book["cogs_qty"] == 15.0
    assert book["financial_qty_remaining"] == 135.0
    assert book["reconciliation_status"] == "MATCH"
    await server.db.app_settings.update_one(
        {"key": SALES_SETTINGS},
        {"$set": {
            "key": SALES_SETTINGS,
            "receivable_account_id": receivable["id"],
            "revenue_account_id": revenue["id"],
            "output_tax_account_id": tax["id"],
        }},
        upsert=True,
    )
    po_id = str(uuid.uuid4())
    await server.db.orders.insert_one({
        "id": po_id,
        "po_number": "SO-E2E",
        "customer": "Pelanggan Uji",
        "line_items": [{"line_idx": 0, "line_id": "l0", "name": "Cup", "quantity": 1, "unit": "pcs"}],
        "total_quantity": 1,
        "current_stage": 5,
        "status_label": "Penjadwalan",
        "payment_status": "Waiting",
        "events": [{"stage": 0, "name": "Order Masuk"}],
        "created_at": "2026-10-06T00:00:00Z",
    })
    _admin()
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            created = await client.post("/api/sales-invoices", json={
                "po_id": po_id,
                "invoice_date": "2026-10-06",
                "discount_amount": 0,
                "ppn_percent": 0,
                "lines": [{"line_idx": 0, "quantity": 1, "unit_price": 30000}],
            })
            assert created.status_code == 200, created.text
            invoice = created.json()["invoice"]
            finalized = await client.post(f"/api/sales-invoices/{invoice['id']}/finalize")
            assert finalized.status_code == 200, finalized.text
            posted = await client.post(f"/api/sales-invoices/{invoice['id']}/post-accounting")
            assert posted.status_code == 200, posted.text
            from services.cash_bank_master import create_cash_bank
            cash = await create_cash_bank(server.db, {"name": "Kas Uji", "account_id": bank["id"], "active": True}, user=USER)
            receipt = await client.post("/api/sales-receipts", json={
                "invoice_id": invoice["id"],
                "receipt_date": "2026-10-07",
                "amount": 30000,
                "cash_bank_id": cash["id"],
                "notes": "pelunasan",
            })
            assert receipt.status_code == 200, receipt.text
            posted_receipt = await client.post(f"/api/sales-receipts/{receipt.json()['receipt']['id']}/post-accounting")
            assert posted_receipt.status_code == 200, posted_receipt.text
            from services.purchase_invoice_posting import post_purchase_invoice_accounting
            from services.purchase_payment_posting import post_purchase_payment_accounting
            from services.purchase_payments import SETTLEMENT_PAID, SUBMISSION_SUBMITTED

            receipt_doc = await server.db.purchase_goods_receipts.find_one({"id": rid}, {"_id": 0})
            invoice_id = str(uuid.uuid4())
            await server.db.purchase_invoices.insert_one({
                "id": invoice_id,
                "invoice_number": "FPB-E2E",
                "invoice_date": "2026-10-03",
                "supplier": "Supplier",
                "purchase_order_id": receipt_doc["purchase_order_id"],
                "receipt_id": rid,
                "grand_total": 60000,
                "cancelled": False,
            })
            await post_purchase_invoice_accounting(server.db, invoice_id, USER, serialize_in_process=False)
            pay_id = str(uuid.uuid4())
            await server.db.purchase_payments.insert_one({
                "id": pay_id,
                "payment_number": "PPB-E2E",
                "purchase_invoice_id": invoice_id,
                "invoice_number": "FPB-E2E",
                "supplier": "Supplier",
                "amount": 60000,
                "payment_date": "2026-10-04",
                "settlement_status": SETTLEMENT_PAID,
                "submission_status": SUBMISSION_SUBMITTED,
                "cancelled": False,
            })
            await post_purchase_payment_accounting(
                server.db, pay_id, USER, cash_bank_id=cash["id"], serialize_in_process=False
            )
            await post_purchase_payment_accounting(
                server.db, pay_id, USER, cash_bank_id=cash["id"], serialize_in_process=False
            )
            assert await server.db[JOURNAL_ENTRIES].count_documents(
                {"source_type": "purchase_payment", "source_id": pay_id, "reversal_of": None}
            ) == 1
    finally:
        app.dependency_overrides.pop(current_user, None)
    draft = await create_general_draft(server.db, {
        "date": "2026-10-08",
        "description": "Beban operasional uji",
        "lines": [
            {"account_id": expense["id"], "debit": 5000, "credit": 0},
            {"account_id": bank["id"], "debit": 0, "credit": 5000},
        ],
    }, user=USER)
    await post_general_voucher(server.db, draft["id"], user=USER)
    statement = await get_profit_loss(server.db, start_date="2026-10-01", end_date="2026-10-31")
    assert statement["totals"]["revenue_total"] == 30000.0
    assert statement["totals"]["cogs_total"] == 16000.0
    assert statement["totals"]["gross_profit"] == 14000.0
    assert statement["totals"]["expense_total"] == 5000.0
    assert statement["totals"]["net_profit"] == 9000.0
    ar_lines = await server.db[JOURNAL_LINES].find({"account_id": receivable["id"]}, {"_id": 0}).to_list(10)
    ar_net = sum(Decimal(str(row.get("debit") or 0)) - Decimal(str(row.get("credit") or 0)) for row in ar_lines)
    assert ar_net == Decimal("0")
    bank_lines = await server.db[JOURNAL_LINES].find({"account_id": bank["id"]}, {"_id": 0}).to_list(10)
    bank_net = sum(Decimal(str(row.get("debit") or 0)) - Decimal(str(row.get("credit") or 0)) for row in bank_lines)
    assert bank_net == Decimal("-35000")
    ap_lines = await server.db[JOURNAL_LINES].find({"account_id": accounts["payable"]["id"]}, {"_id": 0}).to_list(10)
    ap_net = sum(Decimal(str(row.get("debit") or 0)) - Decimal(str(row.get("credit") or 0)) for row in ap_lines)
    assert ap_net == Decimal("0")
    clearing_lines = await server.db[JOURNAL_LINES].find({"account_id": accounts["clearing"]["id"]}, {"_id": 0}).to_list(10)
    clearing_net = sum(Decimal(str(row.get("debit") or 0)) - Decimal(str(row.get("credit") or 0)) for row in clearing_lines)
    assert clearing_net == Decimal("0")
    book_after_pay = await build_financial_book_reconciliation(server.db)
    assert book_after_pay["financial_remaining"] == 144000.0
    assert book_after_pay["inventory_gl_balance"] == 144000.0
    assert book_after_pay["reconciliation_status"] == "MATCH"
    purchases = await purchase_activity_report(server.db, start_date="2026-10-01", end_date="2026-10-31")
    assert purchases["writes"] == 0
    assert purchases["filtered_count"] >= 1
    assert purchases["total_amount"] == 60000.0
    purchase_row = purchases["items"][0]
    assert purchase_row["invoice_numbers"] == ["FPB-E2E"]
    assert purchase_row["invoice_posting"] == ["POSTED"]
    assert purchase_row["payment_numbers"] == ["PPB-E2E"]
    assert purchase_row["payment_posting"] == ["POSTED"]
    sales = await sales_activity_report(server.db, start_date="2026-10-01", end_date="2026-10-31")
    assert sales["writes"] == 0
    assert sales["filtered_count"] == 1
    assert sales["total_amount"] == 30000.0
    assert sales["historical_as_of"] is False
    assert accounts["cogs"]["id"]


async def _opening_owner_snapshot(opening_id: str) -> dict:
    doc = await server.db.financial_inventory_openings.find_one({"id": opening_id}, {"_id": 0})
    journal_id = str((doc or {}).get("posted_journal_id") or "")
    journal = await server.db[JOURNAL_ENTRIES].find_one({"id": journal_id}, {"_id": 0}) if journal_id else None
    lines = []
    if journal:
        lines = await server.db[JOURNAL_LINES].find({"journal_id": journal["id"]}, {"_id": 0}).to_list(10)
    return {
        "status": (doc or {}).get("status"),
        "posted_journal_id": (doc or {}).get("posted_journal_id"),
        "journal_origin": (doc or {}).get("journal_origin"),
        "inventory_account_id": (doc or {}).get("inventory_account_id"),
        "offset_account_id": (doc or {}).get("offset_account_id"),
        "effective_date": (doc or {}).get("effective_date"),
        "total": (doc or {}).get("total"),
        "posted_at": (doc or {}).get("posted_at"),
        "journal_status": (journal or {}).get("status"),
        "journal_date": (journal or {}).get("date"),
        "journal_count": await server.db[JOURNAL_ENTRIES].count_documents({
            "source_type": SOURCE_TYPE,
            "source_id": opening_id,
            "reversal_of": None,
        }),
        "line_accounts": sorted(str(row.get("account_id") or "") for row in lines),
    }


def _pause_before_posted_mark(fio):
    """Stop inside the status write, after the lease read has already returned."""
    entered = asyncio.Event()
    release = asyncio.Event()
    state = {"used": False}
    original = fio._mark

    async def wrapped(db, opening_id, fields, *args, **kwargs):
        if fields.get("status") == "POSTED" and not state["used"]:
            state["used"] = True
            entered.set()
            await release.wait()
        return await original(db, opening_id, fields, *args, **kwargs)

    fio._mark = wrapped
    return entered, release, original


async def test_stale_worker_cannot_overwrite_owner_after_lease_check():
    from datetime import datetime, timedelta, timezone
    import services.financial_inventory_opening as fio

    await _configure()
    mid = await _master()
    clock = {"now": datetime(2026, 10, 1, 3, 0, tzinfo=timezone.utc)}
    original_now = fio._utc_now
    fio._utc_now = lambda: clock["now"]
    entered, release, original_mark = _pause_before_posted_mark(fio)
    task = asyncio.create_task(
        post_financial_opening(server.db, _opening_payload(mid, 100, 1000, "lease-barrier"), USER)
    )
    try:
        await asyncio.wait_for(entered.wait(), timeout=10)
        clock["now"] = clock["now"] + timedelta(seconds=fio.LEASE_SECONDS + 5)
        saved = await post_financial_opening(
            server.db, _opening_payload(mid, 100, 1000, "lease-barrier"), USER,
        )
        assert saved["status"] == "POSTED"
        owner = await _opening_owner_snapshot(saved["id"])
        release.set()
        try:
            await task
        except OpeningBlocked as exc:
            assert exc.code == "OPENING_LEASE_LOST"
        assert await _opening_owner_snapshot(saved["id"]) == owner
    finally:
        release.set()
        fio._mark = original_mark
        fio._utc_now = original_now
        if not task.done():
            task.cancel()


async def test_recovery_loses_the_write_when_its_lease_expires():
    from datetime import datetime, timedelta, timezone
    import services.financial_inventory_opening as fio

    await _configure()
    mid = await _master()
    # Stamp the crashed post on the test clock. Setting the clock only after
    # the crash leaves a wall-clock write expiry in the future, so recovery
    # is rejected before it reaches the posted-mark hook.
    clock = {"now": datetime(2026, 10, 1, 4, 0, tzinfo=timezone.utc)}
    original_now = fio._utc_now
    original_step = fio._after_step
    fio._utc_now = lambda: clock["now"]

    async def crash(step):
        if step == "journal":
            raise RuntimeError("crash after journal")

    fio._after_step = crash
    try:
        with pytest.raises(OpeningBlocked):
            await post_financial_opening(server.db, _opening_payload(mid, 40, 1000, "recover-barrier"), USER)
        fio._after_step = original_step
        stored = await server.db.financial_inventory_openings.find_one(
            {"client_request_id": "recover-barrier"}, {"_id": 0},
        )
        assert stored["status"] != "POSTED"
        assert stored.get("posted_journal_id")
        entered, release, original_mark = _pause_before_posted_mark(fio)
        task = asyncio.create_task(
            recover_financial_opening(server.db, client_request_id="recover-barrier", user=USER)
        )
        try:
            await asyncio.wait_for(entered.wait(), timeout=10)
            clock["now"] = clock["now"] + timedelta(seconds=fio.LEASE_SECONDS + 5)
            saved = await recover_financial_opening(server.db, client_request_id="recover-barrier", user=USER)
            assert saved["status"] == "POSTED"
            owner = await _opening_owner_snapshot(saved["id"])
            release.set()
            try:
                await task
            except OpeningBlocked as exc:
                assert exc.code == "OPENING_LEASE_LOST"
            assert await _opening_owner_snapshot(saved["id"]) == owner
        finally:
            release.set()
            fio._mark = original_mark
            if not task.done():
                task.cancel()
    finally:
        fio._after_step = original_step
        fio._utc_now = original_now


async def test_direct_reverse_cannot_commit_after_cogs_posts_in_the_gap():
    from services.journal_core import reverse_journal
    import services.financial_inventory_opening as fio

    await _configure()
    mid = await _master()
    opening = await post_financial_opening(server.db, _opening_payload(mid, 100, 1000, "reverse-gap"), USER)
    movement = _pengiriman(mid, 10)
    await server.db.stock_movements.insert_one(movement)
    entered = asyncio.Event()
    release = asyncio.Event()
    state = {"used": False}
    original = fio.guard_consumed_opening_journal

    async def pause_after_check(db, journal):
        await original(db, journal)
        if not state["used"]:
            state["used"] = True
            entered.set()
            await release.wait()

    fio.guard_consumed_opening_journal = pause_after_check
    task = asyncio.create_task(reverse_journal(
        server.db,
        opening["posted_journal_id"],
        source_type="financial_inventory_opening_reversal",
        source_id=f"{opening['id']}:gap",
        posted_by=USER,
        description="reversal saat HPP menyusul",
    ))
    try:
        await asyncio.wait_for(entered.wait(), timeout=10)
        try:
            await post_inventory_issue_accounting(server.db, movement["id"], USER)
        except HTTPException:
            pass
        release.set()
        try:
            await task
        except Exception:
            pass
    finally:
        release.set()
        fio.guard_consumed_opening_journal = original
        if not task.done():
            task.cancel()
    journal = await server.db[JOURNAL_ENTRIES].find_one({"id": opening["posted_journal_id"]}, {"_id": 0})
    prefix = f"opening:{opening['id']}:"
    consumed = await server.db.inventory_issue_accounting.find_one(
        {"$or": [
            {"accounting_posting_basis.consumed_acquisitions.acquisition_key": {"$regex": f"^{prefix}"}},
            {"accounting_posting_basis.pool_acquisition_keys": {"$regex": f"^{prefix}"}},
        ]},
        {"_id": 1},
    )
    reversed_source = str((journal or {}).get("status") or "") == "reversed"
    assert not (reversed_source and consumed)


async def test_direct_reverse_loses_item_lock_before_reversal_insert():
    """A passed the last consumption check, then the 600s item lock expired before insert."""
    from datetime import datetime, timedelta, timezone

    import services.inventory_financial_locks as item_locks
    import services.journal_core as journal_core

    await _configure()
    mid = await _master()
    opening = await post_financial_opening(
        server.db, _opening_payload(mid, 100, 1000, "reverse-lock-expiry"), USER,
    )
    movement = _pengiriman(mid, 10)
    await server.db.stock_movements.insert_one(movement)
    clock = {"now": datetime(2026, 10, 5, 3, 0, tzinfo=timezone.utc)}
    original_now = item_locks._utc_now
    item_locks._utc_now = lambda: clock["now"]
    entered = asyncio.Event()
    release = asyncio.Event()
    original_seam = journal_core._before_reversal_insert

    async def pause():
        entered.set()
        await release.wait()

    journal_core._before_reversal_insert = pause
    task = asyncio.create_task(journal_core.reverse_journal(
        server.db,
        opening["posted_journal_id"],
        source_type="financial_inventory_opening_reversal",
        source_id=f"{opening['id']}:lock-expiry",
        posted_by=USER,
        description="reversal setelah kunci item kedaluwarsa",
    ))
    try:
        await asyncio.wait_for(entered.wait(), timeout=10)
        clock["now"] = clock["now"] + timedelta(seconds=item_locks.LEASE_SECONDS + 5)
        await post_inventory_issue_accounting(server.db, movement["id"], USER)
        release.set()
        try:
            await task
        except journal_core.JournalConflictError:
            pass
    finally:
        release.set()
        journal_core._before_reversal_insert = original_seam
        item_locks._utc_now = original_now
        if not task.done():
            task.cancel()
    journal = await server.db[JOURNAL_ENTRIES].find_one(
        {"id": opening["posted_journal_id"]}, {"_id": 0},
    )
    prefix = f"opening:{opening['id']}:"
    consumed = await server.db.inventory_issue_accounting.find_one(
        {"$or": [
            {"accounting_posting_basis.consumed_acquisitions.acquisition_key": {"$regex": f"^{prefix}"}},
            {"accounting_posting_basis.pool_acquisition_keys": {"$regex": f"^{prefix}"}},
        ]},
        {"_id": 1},
    )
    reversal = await server.db[JOURNAL_ENTRIES].find_one(
        {"reversal_of": opening["posted_journal_id"]},
        {"_id": 1, "status": 1},
    )
    reversed_source = str((journal or {}).get("status") or "") == "reversed"
    assert consumed
    assert not reversal
    assert not reversed_source
    assert not (reversed_source and consumed)


class _ProcessCrash(BaseException):
    """Stands in for a killed process. Exception handlers must not recover this."""


async def _opening_consumed(opening_id: str):
    prefix = f"opening:{opening_id}:"
    return await server.db.inventory_issue_accounting.find_one(
        {"$or": [
            {"accounting_posting_basis.consumed_acquisitions.acquisition_key": {"$regex": f"^{prefix}"}},
            {"accounting_posting_basis.pool_acquisition_keys": {"$regex": f"^{prefix}"}},
        ]},
        {"_id": 1},
    )


async def _crash_reversal_then_recover(opening, movement, *, crash):
    """Kill the writer after a durable marker, expire the lock, then retry."""
    from datetime import datetime, timedelta, timezone
    from decimal import Decimal

    import services.inventory_financial_locks as item_locks
    import services.journal_core as journal_core
    from services.financial_inventory_opening import get_financial_opening

    clock = {"now": datetime(2026, 10, 5, 3, 0, tzinfo=timezone.utc)}
    original_now = item_locks._utc_now
    original_release = item_locks.release_item_locks
    original_status = journal_core._after_reversal_status_commit
    original_header = journal_core._after_reversal_header_insert
    original_line = journal_core._after_reversal_line_inserted
    item_locks._utc_now = lambda: clock["now"]

    async def hold_release(_db, _locks):
        return None

    async def _book_view(inventory_id: str) -> dict:
        from services.general_ledger import get_general_ledger
        from services.profit_loss import get_profit_loss
        from services.trial_balance import get_trial_balance

        gl = await get_general_ledger(
            server.db,
            account_id=inventory_id,
            start_date="2026-10-01",
            end_date="2026-10-31",
        )
        tb = await get_trial_balance(server.db, start_date="2026-10-01", end_date="2026-10-31")
        pl = await get_profit_loss(server.db, start_date="2026-10-01", end_date="2026-10-31")
        return {"gl": gl, "tb": tb["totals"], "pl": pl["totals"]}

    item_locks.release_item_locks = hold_release
    crash(journal_core)
    source_id = opening["posted_journal_id"]
    stored_opening = await server.db.financial_inventory_openings.find_one(
        {"id": opening["id"]}, {"_id": 0, "inventory_account_id": 1},
    )
    books_before = await _book_view(stored_opening["inventory_account_id"])
    try:
        with pytest.raises(_ProcessCrash):
            await journal_core.reverse_journal(
                server.db,
                source_id,
                source_type="financial_inventory_opening_reversal",
                source_id=f"{opening['id']}:crash",
                posted_by=USER,
                description="reversal terputus",
            )
        item_locks.release_item_locks = original_release
        journal_core._after_reversal_status_commit = original_status
        journal_core._after_reversal_header_insert = original_header
        journal_core._after_reversal_line_inserted = original_line
        clock["now"] = clock["now"] + timedelta(seconds=item_locks.LEASE_SECONDS + 5)
        source = await server.db[JOURNAL_ENTRIES].find_one({"id": source_id}, {"_id": 0})
        assert str(source.get("status")) == "reversed"
        assert str((source.get("reversal_intent") or {}).get("phase") or "") != "complete"
        assert str(source.get("reversal_owner_expires_at") or "") <= clock["now"].isoformat()
        headers_before = await server.db[JOURNAL_ENTRIES].count_documents({"reversal_of": source_id})
        lines_before = 0
        header_before = await server.db[JOURNAL_ENTRIES].find_one({"reversal_of": source_id}, {"_id": 0})
        if header_before:
            lines_before = await server.db[JOURNAL_LINES].count_documents({"journal_id": header_before["id"]})
        assert not await journal_core.reversal_is_complete(server.db, source)
        await journal_core.get_journal(server.db, source_id)
        await get_financial_opening(server.db, opening["id"])
        assert await server.db[JOURNAL_ENTRIES].count_documents({"reversal_of": source_id}) == headers_before
        if header_before:
            assert await server.db[JOURNAL_LINES].count_documents({"journal_id": header_before["id"]}) == lines_before
        phase_after_get = (
            await server.db[JOURNAL_ENTRIES].find_one({"id": source_id}, {"_id": 0})
        )["reversal_intent"]["phase"]
        assert phase_after_get == source["reversal_intent"]["phase"]
        if header_before:
            counts_before = (
                await server.db[JOURNAL_ENTRIES].count_documents({}),
                await server.db[JOURNAL_LINES].count_documents({}),
                await server.db.financial_inventory_openings.count_documents({}),
            )
            viewed = await journal_core.get_journal(server.db, header_before["id"])
            source_view = await journal_core.get_journal(server.db, source_id)
            books_after = await _book_view(stored_opening["inventory_account_id"])
            recon = await build_financial_book_reconciliation(server.db)
            assert viewed["status"] == "incomplete"
            assert viewed["blocker_code"] == "REVERSAL_INCOMPLETE"
            assert viewed["status"] != "posted"
            assert source_view["status"] == "incomplete"
            assert source_view["blocker_code"] == "REVERSAL_INCOMPLETE"
            assert books_after["gl"]["ending_balance"] == books_before["gl"]["ending_balance"]
            assert books_after["gl"]["total_debit"] == books_before["gl"]["total_debit"]
            assert books_after["gl"]["total_credit"] == books_before["gl"]["total_credit"]
            assert all(row["journal_id"] != header_before["id"] for row in books_after["gl"]["items"])
            assert all(
                row["status"] == "incomplete" and row["blocker_code"] == "REVERSAL_INCOMPLETE"
                for row in books_after["gl"]["items"]
                if row["journal_id"] == source_id
            )
            assert books_after["tb"] == books_before["tb"]
            assert books_after["pl"] == books_before["pl"]
            assert recon["writes"] == 0
            assert recon["reconciliation_status"] == "BLOCKED"
            assert recon["financial_remaining"] is None
            assert recon["inventory_gl_balance"] is None
            counts_after = (
                await server.db[JOURNAL_ENTRIES].count_documents({}),
                await server.db[JOURNAL_LINES].count_documents({}),
                await server.db.financial_inventory_openings.count_documents({}),
            )
            assert counts_after == counts_before
            assert await server.db[JOURNAL_LINES].count_documents(
                {"journal_id": header_before["id"]}
            ) == lines_before
            assert (
                await server.db[JOURNAL_ENTRIES].find_one({"id": source_id}, {"_id": 0})
            )["reversal_intent"]["phase"] == phase_after_get
        with pytest.raises(HTTPException):
            await post_inventory_issue_accounting(server.db, movement["id"], USER)
        assert not await _opening_consumed(opening["id"])

        recovered = await journal_core.reverse_journal(
            server.db,
            source_id,
            source_type="financial_inventory_opening_reversal",
            source_id=f"{opening['id']}:retry",
            posted_by=USER,
            description="retry setelah kunci kedaluwarsa",
        )
        headers = await server.db[JOURNAL_ENTRIES].find({"reversal_of": source_id}, {"_id": 0}).to_list(10)
        assert len(headers) == 1
        assert headers[0]["status"] == "posted"
        if header_before:
            assert headers[0]["id"] == header_before["id"]
        lines = await server.db[JOURNAL_LINES].find({"journal_id": headers[0]["id"]}, {"_id": 0}).to_list(20)
        intent_lines = source["reversal_intent"]["lines"]
        assert len(lines) == len(intent_lines) >= 2
        by_id = {row["id"]: row for row in lines}
        total_dr = Decimal("0.00")
        total_cr = Decimal("0.00")
        for wanted in intent_lines:
            got = by_id[wanted["id"]]
            assert got["account_id"] == wanted["account_id"]
            total_dr += Decimal(str(got["debit"]))
            total_cr += Decimal(str(got["credit"]))
        assert total_dr == total_cr
        assert total_dr > 0
        finished = await server.db[JOURNAL_ENTRIES].find_one({"id": source_id}, {"_id": 0})
        assert finished["status"] == "reversed"
        assert finished["reversal_intent"]["phase"] == "complete"
        assert await journal_core.reversal_is_complete(server.db, finished)
        assert recovered["id"] == headers[0]["id"]
        assert len(recovered["lines"]) == len(lines)
        again = await journal_core.reverse_journal(
            server.db,
            source_id,
            source_type="financial_inventory_opening_reversal",
            source_id=f"{opening['id']}:retry-2",
            posted_by=USER,
            description="retry kedua",
        )
        assert again["id"] == headers[0]["id"]
        assert await server.db[JOURNAL_ENTRIES].count_documents({"reversal_of": source_id}) == 1
        assert not await _opening_consumed(opening["id"])
    finally:
        item_locks._utc_now = original_now
        item_locks.release_item_locks = original_release
        journal_core._after_reversal_status_commit = original_status
        journal_core._after_reversal_header_insert = original_header
        journal_core._after_reversal_line_inserted = original_line


async def test_reversal_crash_after_status_commit_recovers_one_balanced_journal():
    await _configure()
    mid = await _master()
    opening = await post_financial_opening(
        server.db, _opening_payload(mid, 100, 1000, "crash-status"), USER,
    )
    movement = _pengiriman(mid, 10)
    await server.db.stock_movements.insert_one(movement)

    def crash(module):
        async def boom():
            raise _ProcessCrash()

        module._after_reversal_status_commit = boom

    await _crash_reversal_then_recover(opening, movement, crash=crash)


async def test_reversal_crash_after_header_is_not_presented_as_posted():
    await _configure()
    mid = await _master()
    opening = await post_financial_opening(
        server.db, _opening_payload(mid, 100, 1000, "crash-header"), USER,
    )
    movement = _pengiriman(mid, 10)
    await server.db.stock_movements.insert_one(movement)
    seen = {"lines": None}

    def crash(module):
        async def after_header():
            header = await server.db[JOURNAL_ENTRIES].find_one(
                {"reversal_of": opening["posted_journal_id"]}, {"_id": 0},
            )
            seen["lines"] = await server.db[JOURNAL_LINES].count_documents({"journal_id": header["id"]})
            assert seen["lines"] == 0
            raise _ProcessCrash()

        module._after_reversal_header_insert = after_header

    await _crash_reversal_then_recover(opening, movement, crash=crash)
    assert seen["lines"] == 0


async def test_reversal_crash_after_header_before_all_lines_keeps_the_header():
    await _configure()
    mid = await _master()
    opening = await post_financial_opening(
        server.db, _opening_payload(mid, 100, 1000, "crash-lines"), USER,
    )
    movement = _pengiriman(mid, 10)
    await server.db.stock_movements.insert_one(movement)
    seen = {"lines": None}

    def crash(module):
        async def after_line(index):
            if index == 0:
                source = await server.db[JOURNAL_ENTRIES].find_one(
                    {"id": opening["posted_journal_id"]}, {"_id": 0},
                )
                header = await server.db[JOURNAL_ENTRIES].find_one(
                    {"reversal_of": opening["posted_journal_id"]}, {"_id": 0},
                )
                seen["lines"] = await server.db[JOURNAL_LINES].count_documents({"journal_id": header["id"]})
                assert source["reversal_intent"]["phase"] == "header_written"
                assert seen["lines"] == 1
                raise _ProcessCrash()

        module._after_reversal_line_inserted = after_line

    await _crash_reversal_then_recover(opening, movement, crash=crash)
    assert seen["lines"] == 1


async def _stored_counts():
    return (
        await server.db[JOURNAL_ENTRIES].count_documents({}),
        await server.db[JOURNAL_LINES].count_documents({}),
        await server.db.financial_inventory_openings.count_documents({}),
    )


async def test_general_ledger_summary_shows_pending_reversal_through_filters():
    """Pending reversal stays visible on the ledger summary, including opening balance."""
    from datetime import datetime, timedelta, timezone

    import services.inventory_financial_locks as item_locks
    import services.journal_core as journal_core
    from services.general_ledger import get_general_ledger
    from services.profit_loss import get_profit_loss
    from services.trial_balance import get_trial_balance

    accounts = await _configure()
    mid = await _master()
    opening = await post_financial_opening(
        server.db, _opening_payload(mid, 100, 1000, "gl-pending-summary"), USER,
    )
    inventory_id = accounts["inventory"]["id"]
    equity_id = accounts["equity"]["id"]
    cogs_id = accounts["cogs"]["id"]
    clock = {"now": datetime(2026, 10, 5, 3, 0, tzinfo=timezone.utc)}
    original_now = item_locks._utc_now
    original_release = item_locks.release_item_locks
    original_line = journal_core._after_reversal_line_inserted
    item_locks._utc_now = lambda: clock["now"]

    async def hold_release(_db, _locks):
        return None

    async def after_line(index):
        if index == 0:
            raise _ProcessCrash()

    item_locks.release_item_locks = hold_release
    journal_core._after_reversal_line_inserted = after_line
    source_id = opening["posted_journal_id"]
    try:
        with pytest.raises(_ProcessCrash):
            await journal_core.reverse_journal(
                server.db,
                source_id,
                source_type="financial_inventory_opening_reversal",
                source_id=f"{opening['id']}:gl-summary",
                posted_by=USER,
                description="reversal tertahan untuk ringkasan",
            )
        item_locks.release_item_locks = original_release
        journal_core._after_reversal_line_inserted = original_line
        clock["now"] = clock["now"] + timedelta(seconds=item_locks.LEASE_SECONDS + 5)

        before_counts = await _stored_counts()
        period = await get_general_ledger(
            server.db, account_id=inventory_id, start_date="2026-10-01", end_date="2026-10-31",
        )
        assert period["completeness_status"] == "incomplete"
        assert period["coverage_status"] == "incomplete"
        assert period["blocker_code"] == "REVERSAL_INCOMPLETE"
        assert period["opening_balance"] == 0
        assert period["ending_balance"] == 100000
        assert period["total_debit"] == 100000
        assert any(row.get("blocker_code") == "REVERSAL_INCOMPLETE" for row in period["items"])

        opening_range = await get_general_ledger(
            server.db, account_id=inventory_id, start_date="2026-11-01", end_date="2026-11-30",
        )
        assert opening_range["opening_balance"] == 100000
        assert opening_range["ending_balance"] == 100000
        assert opening_range["total_debit"] == 0
        assert opening_range["items"] == []
        assert opening_range["completeness_status"] == "incomplete"
        assert opening_range["coverage_status"] == "incomplete"
        assert opening_range["blocker_code"] == "REVERSAL_INCOMPLETE"

        hidden = await get_general_ledger(
            server.db,
            account_id=inventory_id,
            start_date="2026-10-01",
            end_date="2026-10-31",
            source_type="cash_bank_receipt",
        )
        searched = await get_general_ledger(
            server.db,
            account_id=inventory_id,
            start_date="2026-10-01",
            end_date="2026-10-31",
            q="bukan-jurnal-ini",
        )
        assert hidden["items"] == []
        assert hidden["total"] == 0
        assert hidden["period_count"] == period["period_count"]
        assert hidden["ending_balance"] == period["ending_balance"]
        assert hidden["total_debit"] == period["total_debit"]
        assert hidden["completeness_status"] == "incomplete"
        assert hidden["blocker_code"] == "REVERSAL_INCOMPLETE"
        assert searched["items"] == []
        assert searched["ending_balance"] == period["ending_balance"]
        assert searched["completeness_status"] == "incomplete"
        assert searched["blocker_code"] == "REVERSAL_INCOMPLETE"

        equity = await get_general_ledger(
            server.db, account_id=equity_id, start_date="2026-10-01", end_date="2026-10-31",
        )
        assert equity["completeness_status"] == "incomplete"
        assert equity["blocker_code"] == "REVERSAL_INCOMPLETE"
        assert equity["ending_balance"] == -100000
        cogs = await get_general_ledger(
            server.db, account_id=cogs_id, start_date="2026-10-01", end_date="2026-10-31",
        )
        assert cogs["completeness_status"] == "complete"
        assert cogs["coverage_status"] == "complete"
        assert cogs["blocker_code"] is None
        assert cogs["ending_balance"] == 0

        tb = await get_trial_balance(server.db, start_date="2026-10-01", end_date="2026-10-31")
        tb_hidden = await get_trial_balance(
            server.db, start_date="2026-10-01", end_date="2026-10-31", q="zzz-bukan-akun",
        )
        tb_opening = await get_trial_balance(server.db, start_date="2026-11-01", end_date="2026-11-30")
        assert tb["completeness_status"] == "incomplete"
        assert tb["coverage_status"] == "incomplete"
        assert tb["blocker_code"] == "REVERSAL_INCOMPLETE"
        assert tb["totals"]["period_debit"] == 100000
        assert tb["totals"]["period_credit"] == 100000
        assert tb["totals"]["period_diff"] == 0
        assert tb_hidden["items"] == []
        assert tb_hidden["totals"] == tb["totals"]
        assert tb_hidden["completeness_status"] == "incomplete"
        assert tb_hidden["blocker_code"] == "REVERSAL_INCOMPLETE"
        assert tb_opening["blocker_code"] == "REVERSAL_INCOMPLETE"
        assert tb_opening["totals"]["opening_debit"] == 100000
        assert tb_opening["totals"]["opening_credit"] == 100000
        assert tb_opening["totals"]["opening_diff"] == 0

        pl = await get_profit_loss(server.db, start_date="2026-10-01", end_date="2026-10-31")
        pl_later = await get_profit_loss(server.db, start_date="2026-11-01", end_date="2026-11-30")
        assert pl["totals"]["net_profit"] == 0
        assert pl["totals"]["cogs_total"] == 0
        assert pl_later["totals"] == pl["totals"]
        assert "blocker_code" not in pl
        assert "completeness_status" not in pl
        assert await _stored_counts() == before_counts

        await journal_core.reverse_journal(
            server.db,
            source_id,
            source_type="financial_inventory_opening_reversal",
            source_id=f"{opening['id']}:gl-summary-retry",
            posted_by=USER,
            description="selesaikan reversal untuk ringkasan",
        )
        finished = await get_general_ledger(
            server.db, account_id=inventory_id, start_date="2026-10-01", end_date="2026-10-31",
        )
        finished_opening = await get_general_ledger(
            server.db, account_id=inventory_id, start_date="2026-11-01", end_date="2026-11-30",
        )
        assert finished["completeness_status"] == "complete"
        assert finished["coverage_status"] == "complete"
        assert finished["blocker_code"] is None
        assert finished["ending_balance"] == 0
        assert all(row.get("blocker_code") is None for row in finished["items"])
        assert finished_opening["completeness_status"] == "complete"
        assert finished_opening["blocker_code"] is None
        assert finished_opening["opening_balance"] == 0
        assert finished_opening["ending_balance"] == 0
        tb_done = await get_trial_balance(server.db, start_date="2026-10-01", end_date="2026-10-31")
        tb_done_opening = await get_trial_balance(server.db, start_date="2026-11-01", end_date="2026-11-30")
        assert tb_done["completeness_status"] == "complete"
        assert tb_done["blocker_code"] is None
        assert tb_done["totals"]["ending_diff"] == 0
        assert tb_done["totals"]["period_debit"] == 200000
        assert tb_done["totals"]["period_credit"] == 200000
        assert tb_done_opening["completeness_status"] == "complete"
        assert tb_done_opening["blocker_code"] is None
        assert tb_done_opening["totals"]["opening_debit"] == 0
        assert tb_done_opening["totals"]["opening_credit"] == 0
        headers = await server.db[JOURNAL_ENTRIES].count_documents({"reversal_of": source_id})
        assert headers == 1
    finally:
        item_locks._utc_now = original_now
        item_locks.release_item_locks = original_release
        journal_core._after_reversal_line_inserted = original_line


async def _physical_opening(mid, qty=100, request_id="api-physical"):
    return await initialize_opening_balance(server.db, mid, {
        "unprinted_qty": qty,
        "printed_qty": 0,
        "total_qty": qty,
        "base_uom": "PCS",
        "initialization_source": "MANUAL",
        "client_request_id": request_id,
        "note": "saldo awal API",
    }, USER)


async def test_manual_opening_snapshot_posts_one_journal_without_backfill():
    """Path B freezes the current balance. Path A stays blocked. Markers are not backfilled."""
    from datetime import datetime, timezone
    import services.financial_inventory_opening as fio

    await _configure()
    mid = await _bare_master()
    opened = await _physical_opening(mid)
    movement = opened["movement"]
    assert movement["movement_type"] == MOVEMENT_SALDO_AWAL
    assert not str(movement.get("stock_applied_at") or "").strip()
    historical = await preview_financial_opening(server.db, _opening_payload(mid, 100, 1000, "hist-preview"))
    assert historical["historical_reconstruction"] is True
    assert historical["can_post"] is False
    assert historical["writes"] == 0
    assert historical["blockers"][0]["code"] == "STOCK_COMMIT_UNPROVEN"
    assert historical["blockers"][0]["snapshot_available"] is True
    assert "tidak memundurkan" in historical["blockers"][0]["valid_snapshot_rule"]
    before_journals = await server.db[JOURNAL_ENTRIES].count_documents({})
    with pytest.raises(OpeningBlocked) as exc:
        await post_financial_opening(server.db, _opening_payload(mid, 100, 1000, "hist-post"), USER)
    assert exc.value.code == "STOCK_COMMIT_UNPROVEN"
    assert await server.db[JOURNAL_ENTRIES].count_documents({}) == before_journals

    before_qty = (await server.db.product_stock_state.find_one({"_id": mid}))["total_qty"]
    before_movements = await server.db.stock_movements.count_documents({})
    original = fio._utc_now
    fio._utc_now = lambda: datetime(2026, 10, 4, 17, 0, tzinfo=timezone.utc)
    try:
        captured = await fio.capture_financial_opening_snapshot(server.db, {
            "client_request_id": "snap-current",
            "lines": [{"master_item_id": mid, "base_uom": "PCS"}],
        }, USER)
        replay = await fio.capture_financial_opening_snapshot(server.db, {
            "client_request_id": "snap-current",
            "lines": [{"master_item_id": mid, "base_uom": "PCS"}],
        }, USER)
    finally:
        fio._utc_now = original
    assert captured["id"] == replay["id"]
    assert captured["boundary_kind"] == "current_balance"
    assert captured["backdated"] is False
    assert captured["business_date"] == "2026-10-05"
    assert captured["lines"][0]["qty"] == 100
    assert await server.db.financial_inventory_opening_snapshots.count_documents({"client_request_id": "snap-current"}) == 1
    assert (await server.db.product_stock_state.find_one({"_id": mid}))["total_qty"] == before_qty
    assert await server.db.stock_movements.count_documents({}) == before_movements
    stored_movement = await server.db.stock_movements.find_one({"id": movement["id"]}, {"_id": 0})
    assert not str(stored_movement.get("stock_applied_at") or "").strip()

    with pytest.raises(OpeningBlocked) as exc:
        await post_financial_opening(server.db, {
            **_opening_payload(mid, 100, 1000, "date-too-early"),
            "snapshot_id": captured["id"],
            "effective_date": "2026-10-01",
        }, USER)
    assert exc.value.code == "OPENING_SNAPSHOT_BACKDATED"
    assert "2026-10-01" in exc.value.message
    assert "2026-10-05" in exc.value.message
    assert "tidak diberi tanggal mundur" in exc.value.message

    openings_before = await server.db.financial_inventory_openings.count_documents({})
    journals_before = await server.db[JOURNAL_ENTRIES].count_documents({})
    preview = await preview_financial_opening(server.db, {
        **_opening_payload(mid, 100, 1000, "snap-preview", effective="2026-10-05"),
        "snapshot_id": captured["id"],
    })
    assert preview["writes"] == 0
    assert preview["can_post"] is True
    assert preview["historical_reconstruction"] is False
    assert preview["boundary_kind"] == "current_balance"
    assert await server.db.financial_inventory_openings.count_documents({}) == openings_before
    assert await server.db[JOURNAL_ENTRIES].count_documents({}) == journals_before
    _admin()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        listed = await client.get("/api/inventory/financial-opening")
        assert listed.status_code == 200
    app.dependency_overrides.clear()
    assert await server.db[JOURNAL_ENTRIES].count_documents({}) == journals_before

    posted = await post_financial_opening(server.db, {
        **_opening_payload(mid, 100, 1000, "snap-post", effective="2026-10-05"),
        "snapshot_id": captured["id"],
    }, USER)
    again = await post_financial_opening(server.db, {
        **_opening_payload(mid, 100, 1000, "snap-post", effective="2026-10-05"),
        "snapshot_id": captured["id"],
    }, USER)
    assert posted["status"] == "POSTED"
    assert again["id"] == posted["id"]
    assert again["posted_journal_id"] == posted["posted_journal_id"]
    assert await server.db[JOURNAL_ENTRIES].count_documents({
        "source_type": SOURCE_TYPE,
        "status": "posted",
    }) == 1
    assert (await server.db.product_stock_state.find_one({"_id": mid}))["total_qty"] == 100


async def test_incomplete_or_inflight_state_cannot_capture_snapshot():
    import services.financial_inventory_opening as fio

    await _configure()
    mid = await _bare_master()
    await server.db.product_stock_state.insert_one({
        "_id": mid,
        "master_item_id": mid,
        "base_uom": "PCS",
        "total_qty": 100,
        "unprinted_qty": 100,
        "printed_qty": 0,
        "status": "INITIALIZING",
        "initialized": False,
        "pending_event": None,
        "version": 1,
        "opening_movement_id": None,
    })
    with pytest.raises(OpeningBlocked) as exc:
        await fio.capture_financial_opening_snapshot(server.db, {
            "client_request_id": "snap-init",
            "lines": [{"master_item_id": mid, "base_uom": "PCS"}],
        }, USER)
    assert exc.value.code == "STOCK_STATE_INCOMPLETE"
    assert "belum selesai" in exc.value.message
    assert await server.db.financial_inventory_opening_snapshots.count_documents({}) == 0
    assert (await server.db.product_stock_state.find_one({"_id": mid}))["total_qty"] == 100

    await server.db.product_stock_state.delete_many({"master_item_id": mid})
    await _physical_opening(mid, request_id="inflight-physical")
    await server.db.product_stock_state.update_one(
        {"_id": mid},
        {"$set": {"pending_event": {"event_id": "opening_correction:inflight", "stage": "CLAIMED"}}},
    )
    with pytest.raises(OpeningBlocked) as exc:
        await fio.capture_financial_opening_snapshot(server.db, {
            "client_request_id": "snap-pending",
            "lines": [{"master_item_id": mid, "base_uom": "PCS"}],
        }, USER)
    assert exc.value.code == "STOCK_PENDING"
    assert "masih berjalan" in exc.value.message
    await server.db.stock_movements.insert_one({
        "id": "corr-half",
        "master_item_id": mid,
        "movement_type": "KOREKSI",
        "reference_type": "opening_balance_correction",
        "reference_id": "corr-half",
        "base_uom": "PCS",
        "delta": -20,
        "balance_before": 100,
        "balance_after": 80,
        "status": "POSTED",
    })
    with pytest.raises(OpeningBlocked) as exc:
        await fio.capture_financial_opening_snapshot(server.db, {
            "client_request_id": "snap-pending-moved",
            "lines": [{"master_item_id": mid, "base_uom": "PCS"}],
        }, USER)
    assert exc.value.code == "STOCK_PENDING"
    await server.db.product_stock_state.update_one({"_id": mid}, {"$set": {"pending_event": None}})
    with pytest.raises(OpeningBlocked) as exc:
        await fio.capture_financial_opening_snapshot(server.db, {
            "client_request_id": "snap-mismatch",
            "lines": [{"master_item_id": mid, "base_uom": "PCS"}],
        }, USER)
    assert exc.value.code == "STOCK_STATE_MOVEMENT_MISMATCH"
    assert (await server.db.product_stock_state.find_one({"_id": mid}))["total_qty"] == 100
    assert await server.db.financial_inventory_opening_snapshots.count_documents({}) == 0


async def test_snapshot_membership_does_not_drop_or_double_count_receipts():
    """Receipts inside the frozen chain stay excluded. A later receipt is a new acquisition."""
    from datetime import datetime, timezone
    import services.financial_inventory_opening as fio

    await _configure()
    mid = await _bare_master()
    await _physical_opening(mid, request_id="gr-boundary-physical")
    uncovered = f"gr-open-{uuid.uuid4().hex[:8]}"
    await server.db.purchase_goods_receipts.insert_one({
        "id": uncovered,
        "receipt_number": "GR-OPEN",
        "receipt_date": "2026-10-05",
        "cancelled": False,
        "physical_stock_snapshot": {"enabled": True, "stock_in_trigger": "goods_receipt"},
        "lines": [{"master_item_id": mid, "received_qty": 20, "unit": "PCS"}],
    })
    with pytest.raises(OpeningBlocked) as exc:
        await fio.capture_financial_opening_snapshot(server.db, {
            "client_request_id": "snap-uncovered",
            "lines": [{"master_item_id": mid, "base_uom": "PCS"}],
        }, USER)
    assert exc.value.code == "STOCK_COVERAGE_UNPROVEN"
    assert "Tanggal dokumen tidak dipakai" in exc.value.message
    assert await server.db.financial_inventory_opening_snapshots.count_documents({}) == 0
    await server.db.purchase_goods_receipts.delete_one({"id": uncovered})

    po_id = f"po-{uuid.uuid4().hex[:8]}"
    line_id = f"ln-{uuid.uuid4().hex[:8]}"
    await server.db.purchase_orders.insert_one({
        "id": po_id,
        "po_number": "PB-IN",
        "supplier": "Supplier",
        "cancelled": False,
        "physical_stock_snapshot": {"enabled": True, "stock_in_trigger": "goods_receipt"},
        "lines": [{
            "line_id": line_id,
            "ordered_qty": 50,
            "unit_price": 1100,
            "unit": "PCS",
            "master_item_id": mid,
        }],
    })
    inside_id = str(uuid.uuid4())
    inside = {
        "id": inside_id,
        "receipt_number": "GR-IN",
        "purchase_order_id": po_id,
        "po_number": "PB-IN",
        "supplier": "Supplier",
        "receipt_date": "2026-10-04",
        "cancelled": False,
        "physical_stock_snapshot": {"enabled": True, "stock_in_trigger": "goods_receipt"},
        "lines": [{
            "line_id": "gl-in",
            "purchase_order_line_id": line_id,
            "received_qty": 20,
            "unit": "PCS",
            "master_item_id": mid,
            "unit_price": 1100,
        }],
    }
    await server.db.purchase_goods_receipts.insert_one(dict(inside))
    original_now = stock_engine.now_iso
    stock_engine.now_iso = lambda: "2026-10-04T10:00:00+00:00"
    try:
        await stock_engine.handle_goods_receipt_created(server.db, inside, USER)
    finally:
        stock_engine.now_iso = original_now
    assert (await server.db.product_stock_state.find_one({"_id": mid}))["total_qty"] == 120
    original = fio._utc_now
    fio._utc_now = lambda: datetime(2026, 10, 4, 17, 0, tzinfo=timezone.utc)
    try:
        captured = await fio.capture_financial_opening_snapshot(server.db, {
            "client_request_id": "snap-members",
            "lines": [{"master_item_id": mid, "base_uom": "PCS"}],
        }, USER)
    finally:
        fio._utc_now = original
    assert captured["lines"][0]["qty"] == 120
    assert inside_id in captured["lines"][0]["included_receipt_ids"]
    assert (await server.db.product_stock_state.find_one({"_id": mid}))["total_qty"] == 120
    posted = await post_financial_opening(server.db, {
        **_opening_payload(mid, 120, 1000, "snap-members-post", effective="2026-10-05"),
        "snapshot_id": captured["id"],
    }, USER)
    assert inside_id in posted["excluded_receipt_ids"]
    with pytest.raises(HTTPException) as exc:
        await post_purchase_goods_receipt_accounting(server.db, inside_id, USER, serialize_in_process=False)
    assert exc.value.status_code == 409
    assert exc.value.detail["code"] == "HISTORICAL_RECEIPT_EXCLUDED"

    outside_id = str(uuid.uuid4())
    outside = {
        "id": outside_id,
        "receipt_number": "GR-OUT",
        "purchase_order_id": po_id,
        "po_number": "PB-IN",
        "supplier": "Supplier",
        "receipt_date": "2026-10-05",
        "cancelled": False,
        "physical_stock_snapshot": {"enabled": True, "stock_in_trigger": "goods_receipt"},
        "lines": [{
            "line_id": "gl-out",
            "purchase_order_line_id": line_id,
            "received_qty": 30,
            "unit": "PCS",
            "master_item_id": mid,
            "unit_price": 1200,
        }],
    }
    await server.db.purchase_goods_receipts.insert_one(dict(outside))
    stock_engine.now_iso = lambda: "2026-10-05T02:00:00+00:00"
    try:
        await stock_engine.handle_goods_receipt_created(server.db, outside, USER)
    finally:
        stock_engine.now_iso = original_now
    assert (await server.db.product_stock_state.find_one({"_id": mid}))["total_qty"] == 150
    posted_out = await post_purchase_goods_receipt_accounting(
        server.db, outside_id, USER, serialize_in_process=False,
    )
    assert isinstance(posted_out, dict)
    acquisitions, _report = await load_live_acquisition_coverage(server.db)
    qty, value, _rows = _covered(acquisitions, mid)
    assert qty == Decimal("150")
    # Inside 20 is only in the opening book value. The later 30 uses the PO price, once.
    assert value == Decimal("153000.00")
    assert await server.db[JOURNAL_ENTRIES].count_documents({
        "source_type": "purchase_goods_receipt",
        "source_id": inside_id,
        "status": "posted",
    }) == 0
    assert await server.db[JOURNAL_ENTRIES].count_documents({
        "source_type": "purchase_goods_receipt",
        "source_id": outside_id,
        "status": "posted",
    }) == 1


async def test_snapshot_capture_rejects_a_receipt_that_appears_between_reads():
    import services.financial_inventory_opening as fio

    await _configure()
    mid = await _bare_master()
    await _physical_opening(mid, request_id="race-receipt-physical")
    original = fio._read_item_boundary
    calls = {"n": 0}

    async def appear(db, master_item_id, base_uom, *, settle_pending):
        calls["n"] += 1
        if calls["n"] == 2:
            await db.purchase_goods_receipts.insert_one({
                "id": "gr-mid-read",
                "receipt_number": "GR-MID",
                "receipt_date": "2026-10-05",
                "cancelled": False,
                "physical_stock_snapshot": {"enabled": True, "stock_in_trigger": "goods_receipt"},
                "lines": [{"master_item_id": mid, "received_qty": 5, "unit": "PCS"}],
            })
        return await original(db, master_item_id, base_uom, settle_pending=settle_pending)

    fio._read_item_boundary = appear
    try:
        with pytest.raises(OpeningBlocked) as exc:
            await fio.capture_financial_opening_snapshot(server.db, {
                "client_request_id": "snap-mid-read",
                "lines": [{"master_item_id": mid, "base_uom": "PCS"}],
            }, USER)
        assert exc.value.code == "STOCK_COVERAGE_UNPROVEN"
    finally:
        fio._read_item_boundary = original
    assert await server.db.financial_inventory_opening_snapshots.count_documents({"client_request_id": "snap-mid-read"}) == 0
    assert (await server.db.product_stock_state.find_one({"_id": mid}))["total_qty"] == 100


def _po_and_receipt(mid, qty, receipt_date, *, price=1100):
    po_id = f"po-{uuid.uuid4().hex[:8]}"
    line_id = f"ln-{uuid.uuid4().hex[:8]}"
    po_number = f"PB-{uuid.uuid4().hex[:6]}"
    receipt_id = str(uuid.uuid4())
    gl_id = f"gl-{uuid.uuid4().hex[:6]}"
    po = {
        "id": po_id,
        "po_number": po_number,
        "supplier": "Supplier",
        "cancelled": False,
        "physical_stock_snapshot": {"enabled": True, "stock_in_trigger": "goods_receipt"},
        "lines": [{
            "line_id": line_id,
            "ordered_qty": qty,
            "unit_price": price,
            "unit": "PCS",
            "master_item_id": mid,
        }],
    }
    receipt = {
        "id": receipt_id,
        "receipt_number": f"GR-{uuid.uuid4().hex[:6]}",
        "purchase_order_id": po_id,
        "po_number": po_number,
        "supplier": "Supplier",
        "receipt_date": receipt_date,
        "cancelled": False,
        "physical_stock_snapshot": {"enabled": True, "stock_in_trigger": "goods_receipt"},
        "lines": [{
            "line_id": gl_id,
            "purchase_order_line_id": line_id,
            "received_qty": qty,
            "unit": "PCS",
            "master_item_id": mid,
            "unit_price": price,
        }],
    }
    return po, receipt


async def _insert_po(po):
    await server.db.purchase_orders.insert_one(dict(po))


def _snap_lines(mid):
    return {"client_request_id": f"snap-{uuid.uuid4().hex[:8]}", "lines": [{"master_item_id": mid, "base_uom": "PCS"}]}


async def test_barrier_gr_claim_before_qty_change_blocks_capture():
    """Seam: claim_durable_stock_event returns, before finalize changes qty."""
    import services.financial_inventory_opening as fio

    await _configure()
    mid = await _bare_master()
    await _physical_opening(mid, request_id="barrier-claim-open")
    po, receipt = _po_and_receipt(mid, 20, "2026-09-20")
    await _insert_po(po)
    await server.db.purchase_goods_receipts.insert_one(dict(receipt))
    before = await server.db.product_stock_state.find_one({"_id": mid})
    real_claim = stock_engine.claim_durable_stock_event
    seen = {}

    async def claim_then_capture(db, **kwargs):
        result = await real_claim(db, **kwargs)
        state = await server.db.product_stock_state.find_one({"_id": mid})
        seen["qty"] = state["total_qty"]
        seen["version"] = int(state.get("version") or 0)
        seen["stage"] = (state.get("pending_event") or {}).get("stage")
        seen["event_id"] = (state.get("pending_event") or {}).get("event_id")
        assert state["total_qty"] == before["total_qty"]
        assert int(state.get("version") or 0) == int(before.get("version") or 0)
        assert seen["stage"] == "CLAIMED"
        assert await server.db.stock_movements.count_documents({"event_key": seen["event_id"]}) == 0
        with pytest.raises(OpeningBlocked) as exc:
            await fio.capture_financial_opening_snapshot(server.db, _snap_lines(mid), USER)
        assert exc.value.code == "STOCK_PENDING"
        seen["blocked"] = exc.value.code
        assert await server.db.financial_inventory_opening_snapshots.count_documents({}) == 0
        return result

    stock_engine.claim_durable_stock_event = claim_then_capture
    try:
        movements = await stock_engine.handle_goods_receipt_created(server.db, receipt, USER)
    finally:
        stock_engine.claim_durable_stock_event = real_claim
    assert seen["blocked"] == "STOCK_PENDING"
    assert movements
    state = await server.db.product_stock_state.find_one({"_id": mid})
    assert state["total_qty"] == 120
    assert int(state["version"]) == int(before.get("version") or 0) + 1
    assert not state.get("pending_event")
    captured = await fio.capture_financial_opening_snapshot(server.db, _snap_lines(mid), USER)
    assert captured["lines"][0]["qty"] == 120
    assert captured["lines"][0]["included_receipt_ids"] == [receipt["id"]]
    posted = await post_financial_opening(server.db, {
        **_opening_payload(mid, 120, 1000, "barrier-claim-post", effective=captured["business_date"]),
        "snapshot_id": captured["id"],
    }, USER)
    assert receipt["id"] in posted["excluded_receipt_ids"]
    with pytest.raises(HTTPException) as exc:
        await post_purchase_goods_receipt_accounting(server.db, receipt["id"], USER, serialize_in_process=False)
    assert exc.value.status_code == 409
    assert exc.value.detail["code"] == "HISTORICAL_RECEIPT_EXCLUDED"
    assert await server.db.purchase_goods_receipts.count_documents({"id": receipt["id"]}) == 1
    assert await server.db[JOURNAL_ENTRIES].count_documents({
        "source_type": "purchase_goods_receipt",
        "source_id": receipt["id"],
        "status": "posted",
    }) == 0
    acquisitions, _report = await load_live_acquisition_coverage(server.db)
    qty, _value, _rows = _covered(acquisitions, mid)
    assert qty == Decimal("120")


async def test_barrier_gr_after_qty_before_movement_blocks_capture():
    """Seam: physical_stock_engine.record_stock_movement, after STATE_APPLIED."""
    import services.financial_inventory_opening as fio

    await _configure()
    mid = await _bare_master()
    await _physical_opening(mid, request_id="barrier-applied-open")
    po, receipt = _po_and_receipt(mid, 20, "2026-09-20")
    await _insert_po(po)
    await server.db.purchase_goods_receipts.insert_one(dict(receipt))
    before = await server.db.product_stock_state.find_one({"_id": mid})
    real_record = stock_engine.record_stock_movement
    seen = {}

    async def pause_before_movement(db, **kwargs):
        state = await server.db.product_stock_state.find_one({"_id": mid})
        pending = state.get("pending_event") or {}
        seen["qty"] = state["total_qty"]
        seen["version"] = int(state.get("version") or 0)
        seen["stage"] = pending.get("stage")
        event_id = kwargs.get("event_key")
        assert state["total_qty"] == 120
        assert seen["version"] == int(before.get("version") or 0) + 1
        assert seen["stage"] == "STATE_APPLIED"
        assert await server.db.stock_movements.count_documents({"event_key": event_id}) == 0
        with pytest.raises(OpeningBlocked) as exc:
            await fio.capture_financial_opening_snapshot(server.db, _snap_lines(mid), USER)
        assert exc.value.code == "STOCK_PENDING"
        seen["blocked"] = exc.value.code
        assert await server.db.financial_inventory_opening_snapshots.count_documents({}) == 0
        return await real_record(db, **kwargs)

    stock_engine.record_stock_movement = pause_before_movement
    try:
        await stock_engine.handle_goods_receipt_created(server.db, receipt, USER)
    finally:
        stock_engine.record_stock_movement = real_record
    assert seen["blocked"] == "STOCK_PENDING"
    state = await server.db.product_stock_state.find_one({"_id": mid})
    assert state["total_qty"] == 120
    assert not state.get("pending_event")
    captured = await fio.capture_financial_opening_snapshot(server.db, _snap_lines(mid), USER)
    assert captured["lines"][0]["qty"] == 120
    assert captured["lines"][0]["included_receipt_ids"].count(receipt["id"]) == 1
    assert await server.db.stock_movements.count_documents({
        "reference_type": "goods_receipt",
        "reference_id": receipt["id"],
    }) == 1


async def test_barrier_gr_committed_during_snapshot_insert_is_not_saved():
    """Seam: _insert_snapshot_document, after the accepted read and before the row is kept."""
    import services.financial_inventory_opening as fio

    await _configure()
    mid = await _bare_master()
    await _physical_opening(mid, request_id="barrier-insert-open")
    po, receipt = _po_and_receipt(mid, 20, "2026-09-20")
    await _insert_po(po)
    journals_before = await server.db[JOURNAL_ENTRIES].count_documents({})
    real_insert = fio._insert_snapshot_document
    seen = {}

    async def gr_then_insert(db, doc):
        seen["attempted_qty"] = doc["lines"][0]["qty"]
        seen["snapshot_id"] = doc["id"]
        await server.db.purchase_goods_receipts.insert_one(dict(receipt))
        moves = await stock_engine.handle_goods_receipt_created(server.db, receipt, USER)
        assert moves
        await real_insert(db, doc)

    fio._insert_snapshot_document = gr_then_insert
    try:
        with pytest.raises(OpeningBlocked) as exc:
            await fio.capture_financial_opening_snapshot(server.db, _snap_lines(mid), USER)
        assert exc.value.code == "STALE_SNAPSHOT"
    finally:
        fio._insert_snapshot_document = real_insert
    assert seen["attempted_qty"] == 100
    assert await server.db.financial_inventory_opening_snapshots.count_documents({"id": seen["snapshot_id"]}) == 0
    assert await server.db[JOURNAL_ENTRIES].count_documents({}) == journals_before
    state = await server.db.product_stock_state.find_one({"_id": mid})
    assert state["total_qty"] == 120
    assert await server.db.purchase_goods_receipts.count_documents({"id": receipt["id"]}) == 1
    captured = await fio.capture_financial_opening_snapshot(server.db, _snap_lines(mid), USER)
    assert captured["lines"][0]["qty"] == 120
    assert captured["lines"][0]["included_receipt_ids"].count(receipt["id"]) == 1
    posted = await post_financial_opening(server.db, {
        **_opening_payload(mid, 120, 1000, "barrier-insert-post", effective=captured["business_date"]),
        "snapshot_id": captured["id"],
    }, USER)
    assert receipt["id"] in posted["excluded_receipt_ids"]
    with pytest.raises(HTTPException) as exc:
        await post_purchase_goods_receipt_accounting(server.db, receipt["id"], USER, serialize_in_process=False)
    assert exc.value.detail["code"] == "HISTORICAL_RECEIPT_EXCLUDED"
    acquisitions, _report = await load_live_acquisition_coverage(server.db)
    qty, value, _rows = _covered(acquisitions, mid)
    assert qty == Decimal("120")
    assert value == Decimal("120000.00")


async def test_barrier_manual_opening_partial_writes_block_capture():
    """Seams: entry and return of product_stock_state.record_stock_movement."""
    import services.financial_inventory_opening as fio
    import services.product_stock_state as product_stock

    await _configure()
    mid = await _bare_master()
    real_record = product_stock.record_stock_movement
    seen = {"entry": False, "after": False}

    async def opening_record(db, *args, **kwargs):
        if seen["entry"]:
            return await real_record(db, *args, **kwargs)
        seen["entry"] = True
        state = await server.db.product_stock_state.find_one({"_id": mid})
        assert state["status"] == "INITIALIZING"
        assert state["total_qty"] == 100
        assert int(state.get("version") or 0) == 1
        assert state.get("opening_movement_id") in (None, "")
        assert await server.db.stock_movements.count_documents({"master_item_id": mid}) == 0
        with pytest.raises(OpeningBlocked) as exc:
            await fio.capture_financial_opening_snapshot(server.db, _snap_lines(mid), USER)
        assert exc.value.code == "STOCK_STATE_INCOMPLETE"
        movement = await real_record(db, *args, **kwargs)
        state = await server.db.product_stock_state.find_one({"_id": mid})
        assert state["status"] == "INITIALIZING"
        assert state["total_qty"] == 100
        assert int(state.get("version") or 0) == 1
        assert state.get("opening_movement_id") in (None, "")
        assert await server.db.stock_movements.count_documents({"id": movement["id"]}) == 1
        with pytest.raises(OpeningBlocked) as after_exc:
            await fio.capture_financial_opening_snapshot(server.db, _snap_lines(mid), USER)
        assert after_exc.value.code == "STOCK_STATE_INCOMPLETE"
        seen["after"] = True
        assert await server.db.financial_inventory_opening_snapshots.count_documents({}) == 0
        return movement

    product_stock.record_stock_movement = opening_record
    try:
        opened = await _physical_opening(mid, request_id="barrier-manual-open")
    finally:
        product_stock.record_stock_movement = real_record
    assert seen["entry"] and seen["after"]
    state = await server.db.product_stock_state.find_one({"_id": mid})
    assert state["status"] == "INITIALIZED"
    assert int(state.get("version") or 0) == 1
    assert state["opening_movement_id"]
    assert not str(opened["movement"].get("stock_applied_at") or "").strip()
    captured = await fio.capture_financial_opening_snapshot(server.db, _snap_lines(mid), USER)
    assert captured["lines"][0]["qty"] == 100
    assert captured["lines"][0]["opening_movement_id"] == state["opening_movement_id"]


async def test_barrier_opening_correction_before_state_apply_blocks_capture():
    """Seam: product_stock_state.record_stock_movement inside correct_opening_balance."""
    import services.financial_inventory_opening as fio
    import services.product_stock_state as product_stock

    await _configure()
    mid = await _bare_master()
    await _physical_opening(mid, request_id="barrier-corr-open")
    before = await server.db.product_stock_state.find_one({"_id": mid})
    real_record = product_stock.record_stock_movement
    seen = {}

    async def correction_record(db, *args, **kwargs):
        state = await server.db.product_stock_state.find_one({"_id": mid})
        assert state.get("pending_event")
        assert state["total_qty"] == 100
        assert int(state.get("version") or 0) == int(before.get("version") or 0)
        with pytest.raises(OpeningBlocked) as exc:
            await fio.capture_financial_opening_snapshot(server.db, _snap_lines(mid), USER)
        assert exc.value.code == "STOCK_PENDING"
        seen["before_code"] = exc.value.code
        movement = await real_record(db, *args, **kwargs)
        state = await server.db.product_stock_state.find_one({"_id": mid})
        assert state.get("pending_event")
        assert state["total_qty"] == 100
        assert int(state.get("version") or 0) == int(before.get("version") or 0)
        assert await server.db.stock_movements.count_documents({"id": movement["id"]}) == 1
        with pytest.raises(OpeningBlocked) as after_exc:
            await fio.capture_financial_opening_snapshot(server.db, _snap_lines(mid), USER)
        assert after_exc.value.code == "STOCK_PENDING"
        seen["after_code"] = after_exc.value.code
        assert await server.db.financial_inventory_opening_snapshots.count_documents({}) == 0
        return movement

    product_stock.record_stock_movement = correction_record
    try:
        corrected = await correct_opening_balance(
            server.db,
            mid,
            {"unprinted_qty": 80, "printed_qty": 0, "note": "koreksi barrier", "client_request_id": "barrier-corr"},
            USER,
            app_env="local",
            mongo_url=os.environ["MONGO_URL"],
        )
    finally:
        product_stock.record_stock_movement = real_record
    assert seen["before_code"] == "STOCK_PENDING"
    assert seen["after_code"] == "STOCK_PENDING"
    state = await server.db.product_stock_state.find_one({"_id": mid})
    assert state["total_qty"] == 80
    assert int(state.get("version") or 0) == int(before.get("version") or 0) + 1
    assert not state.get("pending_event")
    assert not str(corrected["movement"].get("stock_applied_at") or "").strip()
    captured = await fio.capture_financial_opening_snapshot(server.db, _snap_lines(mid), USER)
    assert captured["lines"][0]["qty"] == 80


async def test_barrier_opname_after_movement_before_state_blocks_capture():
    """Seam: stock_engine_v2.record_stock_movement, before the opname state update. Version stays put."""
    import services.financial_inventory_opening as fio

    await _configure()
    mid = await _bare_master()
    await _physical_opening(mid, request_id="barrier-opname-open")
    before = await server.db.product_stock_state.find_one({"_id": mid})
    real_record = stock_engine_v2.record_stock_movement
    seen = {}

    async def after_opname_movement(db, *args, **kwargs):
        movement = await real_record(db, *args, **kwargs)
        state = await server.db.product_stock_state.find_one({"_id": mid})
        assert int(state.get("version") or 0) == int(before.get("version") or 0)
        assert state["total_qty"] == 100
        assert not state.get("pending_event")
        assert movement["balance_before"] == 100
        assert movement["balance_after"] == 80
        with pytest.raises(OpeningBlocked) as exc:
            await fio.capture_financial_opening_snapshot(server.db, _snap_lines(mid), USER)
        assert exc.value.code == "STOCK_STATE_MOVEMENT_MISMATCH"
        seen["code"] = exc.value.code
        assert await server.db.financial_inventory_opening_snapshots.count_documents({}) == 0
        return movement

    stock_engine_v2.record_stock_movement = after_opname_movement
    try:
        result = await apply_stock_opname(
            server.db,
            master_item_id=mid,
            target_stock_current_qty=80,
            outstanding_so_qty=0,
            note="opname barrier",
            user=USER,
            client_request_id="barrier-opname",
        )
    finally:
        stock_engine_v2.record_stock_movement = real_record
    assert seen["code"] == "STOCK_STATE_MOVEMENT_MISMATCH"
    assert result["stock_state"]["total_qty"] == 80
    state = await server.db.product_stock_state.find_one({"_id": mid})
    assert int(state.get("version") or 0) == int(before.get("version") or 0)
    captured = await fio.capture_financial_opening_snapshot(server.db, _snap_lines(mid), USER)
    assert captured["lines"][0]["qty"] == 80
    assert captured["lines"][0]["revision"] == int(before.get("version") or 0)


async def test_barrier_goods_receipt_after_capture_before_post_is_stale():
    """Real GR after a saved snapshot, before opening post."""
    import services.financial_inventory_opening as fio

    await _configure()
    mid = await _bare_master()
    await _physical_opening(mid, request_id="barrier-stale-open")
    captured = await fio.capture_financial_opening_snapshot(server.db, _snap_lines(mid), USER)
    frozen_qty = captured["lines"][0]["qty"]
    journals_before = await server.db[JOURNAL_ENTRIES].count_documents({})
    po, receipt = _po_and_receipt(mid, 25, captured["business_date"])
    await _insert_po(po)
    await server.db.purchase_goods_receipts.insert_one(dict(receipt))
    await stock_engine.handle_goods_receipt_created(server.db, receipt, USER)
    assert (await server.db.product_stock_state.find_one({"_id": mid}))["total_qty"] == 125
    preview = await preview_financial_opening(server.db, {
        **_opening_payload(mid, frozen_qty, 1000, "barrier-stale-preview", effective=captured["business_date"]),
        "snapshot_id": captured["id"],
    })
    assert preview["writes"] == 0
    assert preview["can_post"] is False
    assert preview["blockers"][0]["code"] == "STALE_SNAPSHOT"
    with pytest.raises(OpeningBlocked) as exc:
        await post_financial_opening(server.db, {
            **_opening_payload(mid, frozen_qty, 1000, "barrier-stale-post", effective=captured["business_date"]),
            "snapshot_id": captured["id"],
        }, USER)
    assert exc.value.code == "STALE_SNAPSHOT"
    assert await server.db[JOURNAL_ENTRIES].count_documents({}) == journals_before
    assert await server.db.financial_inventory_openings.count_documents({"status": "POSTED"}) == 0
    stored = await server.db.financial_inventory_opening_snapshots.find_one({"id": captured["id"]}, {"_id": 0})
    assert stored["lines"][0]["qty"] == frozen_qty
    assert receipt["id"] not in stored["lines"][0]["included_receipt_ids"]
    assert await server.db.stock_movements.count_documents({
        "reference_type": "goods_receipt",
        "reference_id": receipt["id"],
    }) == 1


async def test_barrier_older_document_date_after_snapshot_is_explicit():
    """A post-snapshot GR with an older document date is blocked, not deleted."""
    import services.financial_inventory_opening as fio

    await _configure()
    mid = await _bare_master()
    await _physical_opening(mid, request_id="barrier-old-date-open")
    captured = await fio.capture_financial_opening_snapshot(server.db, _snap_lines(mid), USER)
    posted = await post_financial_opening(server.db, {
        **_opening_payload(mid, 100, 1000, "barrier-old-date-post", effective=captured["business_date"]),
        "snapshot_id": captured["id"],
    }, USER)
    assert posted["status"] == "POSTED"
    po, receipt = _po_and_receipt(mid, 20, "2026-09-01")
    await _insert_po(po)
    await server.db.purchase_goods_receipts.insert_one(dict(receipt))
    await stock_engine.handle_goods_receipt_created(server.db, receipt, USER)
    assert (await server.db.product_stock_state.find_one({"_id": mid}))["total_qty"] == 120
    with pytest.raises(HTTPException) as exc:
        await post_purchase_goods_receipt_accounting(server.db, receipt["id"], USER, serialize_in_process=False)
    assert exc.value.status_code == 409
    assert exc.value.detail["code"] == "HISTORICAL_RECEIPT_EXCLUDED"
    assert await server.db.purchase_goods_receipts.count_documents({"id": receipt["id"]}) == 1
    assert await server.db.stock_movements.count_documents({
        "reference_type": "goods_receipt",
        "reference_id": receipt["id"],
    }) == 1
    assert await server.db[JOURNAL_ENTRIES].count_documents({
        "source_type": "purchase_goods_receipt",
        "source_id": receipt["id"],
    }) == 0
    assert await server.db[JOURNAL_ENTRIES].count_documents({
        "source_type": SOURCE_TYPE,
        "status": "posted",
    }) == 1
    acquisitions, _report = await load_live_acquisition_coverage(server.db)
    qty, _value, _rows = _covered(acquisitions, mid)
    assert qty == Decimal("100")
    book = await build_financial_book_reconciliation(server.db)
    assert book["writes"] == 0
    assert book["reconciliation_status"] != "MATCH"
    blocked = [row for row in book["blocked_stock_receipts"] if row["receipt_id"] == receipt["id"]]
    assert len(blocked) == 1
    assert blocked[0]["code"] == "HISTORICAL_RECEIPT_EXCLUDED"
    assert receipt["id"] in {row["receipt_id"] for row in book["blockers"]}


async def test_barrier_snapshot_date_follows_wib_midnight_confirmation():
    """fio._utc_now alone crosses WIB midnight. Stock writer clocks stay unpinned."""
    from datetime import datetime, timezone
    import services.financial_inventory_opening as fio

    await _configure()
    mid = await _bare_master()
    opened = await _physical_opening(mid, request_id="barrier-midnight-open")
    movement_stamp = str(opened["movement"].get("created_at") or "")
    pre = datetime(2026, 10, 4, 16, 59, 59, tzinfo=timezone.utc)
    post = datetime(2026, 10, 4, 17, 0, 0, tzinfo=timezone.utc)
    assert wib_business_date(pre.isoformat()) == "2026-10-04"
    assert wib_business_date(post.isoformat()) == "2026-10-05"
    clock = {"at": pre}
    original_now = fio._utc_now
    original_read = fio._read_item_boundary
    calls = {"n": 0}

    def stamped_now():
        return clock["at"]

    async def cross_after_first_read(db, master_item_id, base_uom, *, settle_pending):
        calls["n"] += 1
        observed = await original_read(db, master_item_id, base_uom, settle_pending=settle_pending)
        if calls["n"] == 1:
            assert clock["at"] == pre
            clock["at"] = post
        return observed

    fio._utc_now = stamped_now
    fio._read_item_boundary = cross_after_first_read
    try:
        captured = await fio.capture_financial_opening_snapshot(server.db, _snap_lines(mid), USER)
    finally:
        fio._utc_now = original_now
        fio._read_item_boundary = original_read
    assert calls["n"] >= 2
    assert captured["captured_at"] == post.isoformat()
    assert captured["boundary_at"] == captured["captured_at"]
    assert captured["business_date"] == "2026-10-05"
    assert wib_business_date(captured["captured_at"]) == captured["business_date"]
    assert not movement_stamp.startswith("2026-10-04T16:59:59")
    assert not movement_stamp.startswith("2026-10-04T17:00:00")
    assert (await server.db.product_stock_state.find_one({"_id": mid}))["total_qty"] == 100
    posted = await post_financial_opening(server.db, {
        **_opening_payload(mid, 100, 1000, "barrier-midnight-post", effective=captured["business_date"]),
        "snapshot_id": captured["id"],
    }, USER)
    assert posted["effective_date"] == "2026-10-05"
    assert posted["effective_date"] == captured["business_date"]
