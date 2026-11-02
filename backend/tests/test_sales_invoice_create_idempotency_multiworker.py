"""Multi-worker durable idempotency for one-click / flexible invoice create."""
from __future__ import annotations

import asyncio
import multiprocessing as mp
import os
import sys
import uuid
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ.setdefault("APP_ENV", "local")
os.environ.setdefault("MONGO_URL", "mongodb://127.0.0.1:27017")
os.environ.setdefault("DB_NAME", "eternal_packaging_test_invoice_idem_mw")
os.environ.setdefault("JWT_SECRET", "test-secret-key-12345678901234567890")
os.environ.setdefault("CORS_ORIGINS", "*")

import server
from fastapi import HTTPException
from services.sales_invoice_create_idempotency import (
    ensure_sales_invoice_create_idempotency_indexes,
    find_duplicate_idempotency_keys,
    sales_invoice_idempotency_index_ready,
    create_intent_index_ready,
)
from services.sales_invoices import (
    FINANCIAL_FINALIZED,
    cancel_sales_invoice,
    create_and_finalize_sales_invoice,
    create_sales_invoice,
    update_draft_and_finalize_sales_invoice,
)


USER = {
    "id": "u1",
    "email": "admin@eternalpackaging.com",
    "name": "Admin",
    "role": "admin",
    "branch_ids": ["br-1"],
}


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture(autouse=True)
def bind_motor():
    from motor.motor_asyncio import AsyncIOMotorClient

    os.environ["APP_ENV"] = "local"
    server.client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    server.db = server.client[os.environ["DB_NAME"]]


async def _reset():
    db = server.db
    await db.branches.update_one(
        {"id": "br-1"},
        {"$set": {"id": "br-1", "code": "YGY", "name": "Yogya", "active": True}},
        upsert=True,
    )
    for name in (
        "sales_invoices",
        "orders",
        "sales_deliveries",
        "sales_invoice_line_billing_counters",
        "sales_invoice_sj_line_billing_counters",
        "sales_invoice_create_intents",
        "journal_entries",
        "journal_lines",
    ):
        await db[name].delete_many({})
    # Drop indexes so ensure recreates cleanly per test DB.
    try:
        await db.sales_invoices.drop_indexes()
    except Exception:
        pass
    try:
        await db.sales_invoice_create_intents.drop_indexes()
    except Exception:
        pass
    await db.sales_invoices.create_index("id", unique=True)
    await db.sales_invoices.create_index("invoice_number", unique=True)
    report = await ensure_sales_invoice_create_idempotency_indexes(db)
    assert report["intent_ready"] is True
    assert report["invoice_ready"] is True


async def _order(*, qty=100, price=1000.0):
    po_id = str(uuid.uuid4())
    line_id = f"{po_id}_line_0"
    doc = {
        "id": po_id,
        "po_number": f"PO-{po_id[:8]}",
        "customer": "PT Idem",
        "customer_id": "cust-idem",
        "currency": "IDR",
        "operational_branch_id": "br-1",
        "branch_id": "1",
        "cancelled": False,
        "line_items": [{
            "line_id": line_id,
            "name": "Produk A",
            "quantity": qty,
            "unit": "pcs",
            "unit_price": price,
        }],
    }
    await server.db.orders.insert_one(doc)
    doc.pop("_id", None)
    return doc


async def _sj(order, *, qty=None):
    sj_id = str(uuid.uuid4())
    line = order["line_items"][0]
    qty = int(qty if qty is not None else line["quantity"])
    doc = {
        "id": sj_id,
        "po_id": order["id"],
        "sj_number": f"SJ-{sj_id[:8]}",
        "is_cancelled": False,
        "operational_branch_id": order.get("operational_branch_id"),
        "branch_id": order.get("branch_id"),
        "line_items": [{
            "line_id": line["line_id"],
            "line_idx": 0,
            "qty": qty,
            "source_po_id": order["id"],
            "source_line_idx": 0,
        }],
        "source_allocations": [{
            "source_po_id": order["id"],
            "source_line_id": line["line_id"],
            "source_line_idx": 0,
            "allocated_qty": qty,
            "unit_price": float(line["unit_price"]),
        }],
    }
    await server.db.sales_deliveries.insert_one(doc)
    doc.pop("_id", None)
    return doc


def _worker_multipo(db_name, mongo, key, a, a_lid, b, b_lid, q):
    async def run():
        os.environ["APP_ENV"] = "local"
        os.environ["MONGO_URL"] = mongo
        os.environ["DB_NAME"] = db_name
        import server as srv
        from motor.motor_asyncio import AsyncIOMotorClient
        from services.sales_invoices import create_and_finalize_sales_invoice as caf

        srv.client = AsyncIOMotorClient(mongo)
        srv.db = srv.client[db_name]
        payload = {
            "source_mode": "po",
            "source_po_ids": [a, b],
            "idempotency_key": key,
            "finalize_on_save": True,
            "lines": [
                {
                    "source_po_id": a,
                    "source_po_line_id": a_lid,
                    "source_po_line_idx": 0,
                    "line_idx": 0,
                    "line_id": a_lid,
                    "quantity": 3,
                    "unit_price": 1000,
                },
                {
                    "source_po_id": b,
                    "source_po_line_id": b_lid,
                    "source_po_line_idx": 0,
                    "line_idx": 0,
                    "line_id": b_lid,
                    "quantity": 2,
                    "unit_price": 2000,
                },
            ],
        }
        try:
            inv = await caf(srv.db, payload, USER)
            q.put(("ok", inv["id"], inv["invoice_number"], inv["financial_status"], inv.get("payment_status")))
        except Exception as e:
            q.put(("err", getattr(e, "status_code", None), str(getattr(e, "detail", e))[:220]))

    asyncio.run(run())


def _worker_multisj(db_name, mongo, key, o2, o2_lid, o3, o3_lid, sj2, sj3, q):
    async def run():
        os.environ["APP_ENV"] = "local"
        os.environ["MONGO_URL"] = mongo
        os.environ["DB_NAME"] = db_name
        import server as srv
        from motor.motor_asyncio import AsyncIOMotorClient
        from services.sales_invoices import create_and_finalize_sales_invoice as caf

        srv.client = AsyncIOMotorClient(mongo)
        srv.db = srv.client[db_name]
        payload = {
            "source_mode": "sj",
            "source_sj_ids": [sj2, sj3],
            "idempotency_key": key,
            "finalize_on_save": True,
            "lines": [
                {
                    "source_sj_id": sj2,
                    "source_po_id": o2,
                    "source_po_line_id": o2_lid,
                    "source_po_line_idx": 0,
                    "line_idx": 0,
                    "line_id": o2_lid,
                    "quantity": 4,
                    "unit_price": 1000,
                },
                {
                    "source_sj_id": sj3,
                    "source_po_id": o3,
                    "source_po_line_id": o3_lid,
                    "source_po_line_idx": 0,
                    "line_idx": 0,
                    "line_id": o3_lid,
                    "quantity": 2,
                    "unit_price": 2500,
                },
            ],
        }
        try:
            inv = await caf(srv.db, payload, USER)
            q.put(("ok", inv["id"], inv["invoice_number"], inv["financial_status"]))
        except Exception as e:
            q.put(("err", getattr(e, "status_code", None), str(getattr(e, "detail", e))[:220]))

    asyncio.run(run())


@pytest.mark.anyio
async def test_t1_four_workers_same_key_multipo():
    await _reset()
    a = await _order(qty=50, price=1000)
    b = await _order(qty=50, price=2000)
    key = f"mw-multipo-{uuid.uuid4()}"
    q = mp.Queue()
    args = (
        os.environ["DB_NAME"],
        os.environ["MONGO_URL"],
        key,
        a["id"],
        a["line_items"][0]["line_id"],
        b["id"],
        b["line_items"][0]["line_id"],
    )
    procs = [mp.Process(target=_worker_multipo, args=(*args, q)) for _ in range(4)]
    for p in procs:
        p.start()
    for p in procs:
        p.join(120)
    res = []
    while not q.empty():
        res.append(q.get())
    oks = [r for r in res if r[0] == "ok"]
    assert oks, res
    ids = {r[1] for r in oks}
    nums = {r[2] for r in oks}
    assert len(ids) == 1, res
    assert len(nums) == 1, res
    count = await server.db.sales_invoices.count_documents({"idempotency_key": key})
    assert count == 1
    assert all(r[3] == FINANCIAL_FINALIZED for r in oks)
    assert all(r[4] == "BELUM DIBAYAR" for r in oks)


@pytest.mark.anyio
async def test_t2_four_workers_same_key_multisj():
    await _reset()
    o2 = await _order(qty=30, price=1000)
    o3 = await _order(qty=30, price=2500)
    sj2 = await _sj(o2, qty=30)
    sj3 = await _sj(o3, qty=30)
    key = f"mw-multisj-{uuid.uuid4()}"
    q = mp.Queue()
    args = (
        os.environ["DB_NAME"],
        os.environ["MONGO_URL"],
        key,
        o2["id"],
        o2["line_items"][0]["line_id"],
        o3["id"],
        o3["line_items"][0]["line_id"],
        sj2["id"],
        sj3["id"],
    )
    procs = [mp.Process(target=_worker_multisj, args=(*args, q)) for _ in range(4)]
    for p in procs:
        p.start()
    for p in procs:
        p.join(120)
    res = []
    while not q.empty():
        res.append(q.get())
    oks = [r for r in res if r[0] == "ok"]
    assert oks, res
    assert len({r[1] for r in oks}) == 1, res
    assert await server.db.sales_invoices.count_documents({"idempotency_key": key}) == 1


@pytest.mark.anyio
async def test_t3_same_key_different_payload_conflict():
    await _reset()
    order = await _order(qty=40)
    key = f"conflict-{uuid.uuid4()}"
    lid = order["line_items"][0]["line_id"]
    first = await create_and_finalize_sales_invoice(
        server.db,
        {
            "po_id": order["id"],
            "idempotency_key": key,
            "lines": [{"line_idx": 0, "line_id": lid, "quantity": 5, "unit_price": 1000}],
        },
        USER,
    )
    assert first["financial_status"] == FINANCIAL_FINALIZED
    with pytest.raises(HTTPException) as exc:
        await create_and_finalize_sales_invoice(
            server.db,
            {
                "po_id": order["id"],
                "idempotency_key": key,
                "lines": [{"line_idx": 0, "line_id": lid, "quantity": 6, "unit_price": 1000}],
            },
            USER,
        )
    assert exc.value.status_code == 409
    detail = exc.value.detail
    assert isinstance(detail, dict)
    assert detail.get("code") == "idempotency_payload_conflict"
    assert await server.db.sales_invoices.count_documents({"idempotency_key": key}) == 1


@pytest.mark.anyio
async def test_t4_distinct_keys_capacity_race():
    await _reset()
    order = await _order(qty=20)
    lid = order["line_items"][0]["line_id"]

    async def one(tag, qty):
        try:
            inv = await create_and_finalize_sales_invoice(
                server.db,
                {
                    "po_id": order["id"],
                    "idempotency_key": f"cap-{tag}-{uuid.uuid4()}",
                    "lines": [{"line_idx": 0, "line_id": lid, "quantity": qty, "unit_price": 1000}],
                },
                USER,
            )
            return ("ok", inv["id"], inv["total_qty"])
        except HTTPException as e:
            return ("err", e.status_code, str(e.detail)[:120])

    a, b = await asyncio.gather(one("A", 15), one("B", 15))
    results = [a, b]
    oks = [r for r in results if r[0] == "ok"]
    errs = [r for r in results if r[0] == "err"]
    assert len(oks) == 1, results
    assert len(errs) == 1, results


@pytest.mark.anyio
async def test_t8_t9_t11_retry_after_finalize_no_journal():
    await _reset()
    order = await _order(qty=25)
    key = f"retry-fin-{uuid.uuid4()}"
    lid = order["line_items"][0]["line_id"]
    before = await server.db.journal_entries.count_documents({})
    a = await create_and_finalize_sales_invoice(
        server.db,
        {
            "po_id": order["id"],
            "idempotency_key": key,
            "lines": [{"line_idx": 0, "line_id": lid, "quantity": 4, "unit_price": 1000}],
        },
        USER,
    )
    b = await create_and_finalize_sales_invoice(
        server.db,
        {
            "po_id": order["id"],
            "idempotency_key": key,
            "lines": [{"line_idx": 0, "line_id": lid, "quantity": 4, "unit_price": 1000}],
        },
        USER,
    )
    assert a["id"] == b["id"]
    assert a["invoice_number"] == b["invoice_number"]
    assert await server.db.sales_invoices.count_documents({"idempotency_key": key}) == 1
    assert await server.db.journal_entries.count_documents({}) == before
    assert b.get("payment_status") == "BELUM DIBAYAR"


@pytest.mark.anyio
async def test_t12_t13_legacy_empty_key_and_no_key_invoice():
    await _reset()
    order = await _order(qty=10)
    lid = order["line_items"][0]["line_id"]
    # empty key → allowed, creates draft/finalized without durable key stamp
    inv = await create_sales_invoice(
        server.db,
        {"po_id": order["id"], "idempotency_key": "", "lines": [{"line_idx": 0, "line_id": lid, "quantity": 1, "unit_price": 1000}]},
        USER,
    )
    assert inv.get("idempotency_key") in (None, "")
    # legacy invoice without key field remains readable
    legacy_id = str(uuid.uuid4())
    await server.db.sales_invoices.insert_one({
        "id": legacy_id,
        "invoice_number": f"INV/LEG/{legacy_id[:6]}",
        "po_id": order["id"],
        "po_number": order["po_number"],
        "invoice_date": "2026-01-01",
        "lines": [{"line_idx": 0, "quantity": 1, "unit_price": 1000, "line_total": 1000}],
        "subtotal": 1000.0,
        "discount_amount": 0.0,
        "after_discount": 1000.0,
        "ppn_percent": 0.0,
        "ppn_amount": 0.0,
        "grand_total": 1000.0,
        "payment_status": "BELUM DIBAYAR",
        "created_at": "2026-01-01T00:00:00Z",
    })
    stored = await server.db.sales_invoices.find_one({"id": legacy_id}, {"_id": 0})
    assert "idempotency_key" not in stored
    assert await find_duplicate_idempotency_keys(server.db) == []


@pytest.mark.anyio
async def test_t14_index_creation_with_legacy_and_ready_flags():
    await _reset()
    assert await sales_invoice_idempotency_index_ready(server.db) is True
    assert await create_intent_index_ready(server.db) is True
    report = await ensure_sales_invoice_create_idempotency_indexes(server.db)
    assert report["invoice_ready"] is True
    assert report["intent_ready"] is True


@pytest.mark.anyio
async def test_t15_t16_single_po_and_draft_finalize():
    await _reset()
    order = await _order(qty=30)
    lid = order["line_items"][0]["line_id"]
    draft = await create_sales_invoice(
        server.db,
        {"po_id": order["id"], "lines": [{"line_idx": 0, "line_id": lid, "quantity": 5, "unit_price": 1000}]},
        USER,
    )
    assert draft["financial_status"] == "draft"
    fin = await update_draft_and_finalize_sales_invoice(
        server.db,
        draft["id"],
        {
            "finalize_on_save": True,
            "lines": [{
                "invoice_line_id": (draft["lines"][0] or {}).get("invoice_line_id"),
                "line_idx": 0,
                "line_id": lid,
                "quantity": 5,
                "unit_price": 1000,
            }],
        },
        USER,
    )
    assert fin["financial_status"] == FINANCIAL_FINALIZED


@pytest.mark.anyio
async def test_t17_cancel_after_one_click():
    await _reset()
    order = await _order(qty=15)
    lid = order["line_items"][0]["line_id"]
    inv = await create_and_finalize_sales_invoice(
        server.db,
        {
            "po_id": order["id"],
            "idempotency_key": f"cancel-{uuid.uuid4()}",
            "lines": [{"line_idx": 0, "line_id": lid, "quantity": 3, "unit_price": 1000}],
        },
        USER,
    )
    cancelled = await cancel_sales_invoice(server.db, inv["id"], USER)
    assert cancelled["financial_status"] == "cancelled"


@pytest.mark.anyio
async def test_t18_provisional_not_listed_as_finalized_ops():
    await _reset()
    from services.sales_invoice_allocations import BILLING_COMMIT_PENDING_FIELD
    from services.sales_invoices import list_sales_invoices

    order = await _order(qty=10)
    pending_id = str(uuid.uuid4())
    await server.db.sales_invoices.insert_one({
        "id": pending_id,
        "invoice_number": f"FPX-PEND-{pending_id[:6]}",
        "po_id": order["id"],
        "financial_status": "finalized",
        BILLING_COMMIT_PENDING_FIELD: True,
        "grand_total": 1,
        "payment_status": "BELUM DIBAYAR",
        "created_at": "2026-11-02T00:00:00Z",
    })
    rows = await list_sales_invoices(server.db)
    assert all(r.get("id") != pending_id for r in rows)
