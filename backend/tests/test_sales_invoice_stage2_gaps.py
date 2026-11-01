"""Stage 2 gap closure: draft rebalance, orphan billing recovery, provisional gates."""
from __future__ import annotations

import asyncio
import os
import sys
import time
import uuid
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ.setdefault("MONGO_URL", "mongodb://127.0.0.1:27017")
os.environ.setdefault("DB_NAME", "eternal_packaging_test_invoice_stage2_gaps")
os.environ.setdefault("JWT_SECRET", "test-secret-key-12345678901234567890")
os.environ.setdefault("CORS_ORIGINS", "*")

import server
from fastapi import HTTPException
from services.sales_invoice_allocations import (
    BILLING_COMMIT_PENDING_FIELD,
    BILLING_COUNTERS,
    CLAIM_STATE_COMMITTED,
    CLAIM_STATE_RESERVED,
    RECOVERY_FENCE_FIELD,
    activate_invoice_billing_commit,
    assert_invoice_not_provisional,
    claim_invoice_line_billings,
    commit_claimed_billings,
    get_po_line_invoiceable_remaining,
    invoice_is_billing_pending,
    mongo_transactions_available,
    persist_invoice_with_billing_claims,
    rebalance_invoice_billing_allocations,
    reconcile_orphan_reserved_without_invoice,
    reconcile_pending_orphan_invoice,
    release_invoice_billing_allocations,
    require_operational_sales_invoice,
)
from services.sales_invoices import (
    FINANCIAL_DRAFT,
    cancel_sales_invoice,
    create_sales_invoice,
    finalize_sales_invoice,
    get_sales_invoice_detail,
    list_sales_invoices,
    update_sales_invoice,
)


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture(autouse=True)
def bind_motor():
    from motor.motor_asyncio import AsyncIOMotorClient
    server.client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    server.db = server.client[os.environ["DB_NAME"]]


USER = {"id": "u1", "email": "admin@eternalpackaging.com", "name": "Admin", "role": "admin"}


async def _order(*, qty=100, price=1000.0, customer_id="cust-gap", currency="IDR", branch="br-gap"):
    po_id = str(uuid.uuid4())
    line_id = f"{po_id}_line_0"
    doc = {
        "id": po_id,
        "po_number": f"PO-{po_id[:8]}",
        "customer": "PT Gap",
        "customer_id": customer_id,
        "currency": currency,
        "operational_branch_id": branch,
        "branch_id": "1",
        "cancelled": False,
        "line_items": [{
            "line_id": line_id,
            "name": "Produk Gap",
            "quantity": qty,
            "unit": "pcs",
            "unit_price": price,
        }],
    }
    await server.db.orders.insert_one(doc)
    doc.pop("_id", None)
    return doc


async def _po_invoiced(po_id: str, line_id: str) -> int:
    cid = f"{po_id}::{line_id}"
    cur = await getattr(server.db, BILLING_COUNTERS).find_one({"id": cid}, {"_id": 0, "invoiced_qty": 1})
    return int((cur or {}).get("invoiced_qty") or 0)


@pytest.mark.anyio
async def test_t1_draft_increase_qty():
    order = await _order(qty=100)
    line_id = order["line_items"][0]["line_id"]
    inv = await create_sales_invoice(
        server.db,
        {"po_id": order["id"], "lines": [{"line_idx": 0, "quantity": 20, "unit_price": 1000}]},
        USER,
    )
    assert await _po_invoiced(order["id"], line_id) == 20
    updated = await update_sales_invoice(
        server.db,
        inv["id"],
        {"lines": [{"line_idx": 0, "quantity": 35}]},
        USER,
    )
    assert updated["lines"][0]["quantity"] == 35
    assert await _po_invoiced(order["id"], line_id) == 35


@pytest.mark.anyio
async def test_t2_draft_decrease_qty():
    order = await _order(qty=100)
    line_id = order["line_items"][0]["line_id"]
    inv = await create_sales_invoice(
        server.db,
        {"po_id": order["id"], "lines": [{"line_idx": 0, "quantity": 40, "unit_price": 1000}]},
        USER,
    )
    updated = await update_sales_invoice(
        server.db,
        inv["id"],
        {"lines": [{"line_idx": 0, "quantity": 15}]},
        USER,
    )
    assert updated["lines"][0]["quantity"] == 15
    assert await _po_invoiced(order["id"], line_id) == 15


@pytest.mark.anyio
async def test_t3_multi_line_rebalance_failure_rollback():
    po_id = str(uuid.uuid4())
    lines = []
    for i in range(2):
        lines.append({
            "line_id": f"{po_id}_line_{i}",
            "name": f"Item {i}",
            "quantity": 50,
            "unit": "pcs",
            "unit_price": 1000.0,
        })
    order = {
        "id": po_id,
        "po_number": f"PO-{po_id[:8]}",
        "customer": "PT Multi",
        "customer_id": "cust-multi",
        "currency": "IDR",
        "operational_branch_id": "br-gap",
        "branch_id": "1",
        "cancelled": False,
        "line_items": lines,
    }
    await server.db.orders.insert_one(order)
    inv = await create_sales_invoice(
        server.db,
        {
            "po_id": po_id,
            "lines": [
                {"line_idx": 0, "quantity": 10, "unit_price": 1000},
                {"line_idx": 1, "quantity": 10, "unit_price": 1000},
            ],
        },
        USER,
    )
    # Fill remaining capacity on line 0 via another invoice so increase fails.
    await create_sales_invoice(
        server.db,
        {"po_id": po_id, "lines": [{"line_idx": 0, "quantity": 40, "unit_price": 1000}]},
        USER,
    )
    before0 = await _po_invoiced(po_id, lines[0]["line_id"])
    before1 = await _po_invoiced(po_id, lines[1]["line_id"])
    with pytest.raises(HTTPException) as exc:
        await update_sales_invoice(
            server.db,
            inv["id"],
            {
                "lines": [
                    {"line_idx": 0, "quantity": 20},  # needs +10 but capacity gone
                    {"line_idx": 1, "quantity": 25},
                ]
            },
            USER,
        )
    assert exc.value.status_code in (400, 409, 503)
    # Counters restored / unchanged for failed rebalance
    assert await _po_invoiced(po_id, lines[0]["line_id"]) == before0
    assert await _po_invoiced(po_id, lines[1]["line_id"]) == before1
    fresh = await server.db.sales_invoices.find_one({"id": inv["id"]}, {"_id": 0})
    assert fresh["lines"][0]["quantity"] == 10
    assert fresh["lines"][1]["quantity"] == 10


@pytest.mark.anyio
async def test_t4_concurrent_draft_edit():
    order = await _order(qty=100)
    inv = await create_sales_invoice(
        server.db,
        {"po_id": order["id"], "lines": [{"line_idx": 0, "quantity": 20, "unit_price": 1000}]},
        USER,
    )

    async def _edit(qty):
        return await update_sales_invoice(
            server.db, inv["id"], {"lines": [{"line_idx": 0, "quantity": qty}]}, USER
        )

    results = await asyncio.gather(_edit(30), _edit(40), return_exceptions=True)
    oks = [r for r in results if not isinstance(r, Exception)]
    errs = [r for r in results if isinstance(r, Exception)]
    assert len(oks) >= 1
    # At most one wins cleanly; loser is HTTPException or lock serialization still consistent
    line_id = order["line_items"][0]["line_id"]
    invoiced = await _po_invoiced(order["id"], line_id)
    fresh = await server.db.sales_invoices.find_one({"id": inv["id"]}, {"_id": 0})
    assert invoiced == int(fresh["lines"][0]["quantity"])
    assert invoiced in (30, 40)


@pytest.mark.anyio
async def test_t5_finalize_with_correct_allocation():
    order = await _order(qty=100)
    line_id = order["line_items"][0]["line_id"]
    inv = await create_sales_invoice(
        server.db,
        {"po_id": order["id"], "lines": [{"line_idx": 0, "quantity": 25, "unit_price": 1000}]},
        USER,
    )
    await update_sales_invoice(
        server.db, inv["id"], {"lines": [{"line_idx": 0, "quantity": 28}]}, USER
    )
    fin = await finalize_sales_invoice(server.db, inv["id"], USER)
    assert fin["financial_status"] == "finalized"
    assert await _po_invoiced(order["id"], line_id) == 28


@pytest.mark.anyio
async def test_t6_cancel_restores_exactly_once():
    order = await _order(qty=100)
    line_id = order["line_items"][0]["line_id"]
    inv = await create_sales_invoice(
        server.db,
        {"po_id": order["id"], "lines": [{"line_idx": 0, "quantity": 30, "unit_price": 1000}]},
        USER,
    )
    await finalize_sales_invoice(server.db, inv["id"], USER)
    await cancel_sales_invoice(server.db, inv["id"], USER)
    assert await _po_invoiced(order["id"], line_id) == 0
    # Idempotent second release path via cancel replay
    again = await cancel_sales_invoice(server.db, inv["id"], USER)
    assert again["financial_status"] == "cancelled"
    assert await _po_invoiced(order["id"], line_id) == 0


@pytest.mark.anyio
async def test_t7_dual_po_sj_cap_via_rebalance_po_only():
    order = await _order(qty=50)
    line_id = order["line_items"][0]["line_id"]
    inv = await create_sales_invoice(
        server.db,
        {"po_id": order["id"], "lines": [{"line_idx": 0, "quantity": 20, "unit_price": 1000}]},
        USER,
    )
    with pytest.raises(HTTPException):
        await update_sales_invoice(
            server.db, inv["id"], {"lines": [{"line_idx": 0, "quantity": 60}]}, USER
        )
    remain = await get_po_line_invoiceable_remaining(
        server.db,
        source_po_id=order["id"],
        source_line_id=line_id,
        source_line_idx=0,
        ordered_qty=50,
    )
    assert remain == 30


@pytest.mark.anyio
async def test_t8_kill_after_claim():
    order = await _order(qty=100)
    line_id = order["line_items"][0]["line_id"]
    invoice_id = str(uuid.uuid4())
    lines = [{
        "source_po_id": order["id"],
        "source_po_line_id": line_id,
        "source_po_line_idx": 0,
        "line_id": line_id,
        "line_idx": 0,
        "ordered_qty": 100,
        "quantity": 12,
    }]
    claims = await claim_invoice_line_billings(server.db, invoice_id, lines)
    assert claims and claims[0]["state"] == CLAIM_STATE_RESERVED
    assert await _po_invoiced(order["id"], line_id) == 12
    # Simulate lease expiry + heal (no invoice doc)
    cid = f"{order['id']}::{line_id}"
    await getattr(server.db, BILLING_COUNTERS).update_one(
        {"id": cid},
        {"$set": {f"claims_by_invoice.{invoice_id}.lease_until": time.time() - 5}},
    )
    healed = await reconcile_orphan_reserved_without_invoice(
        server.db, source_po_id=order["id"], source_line_id=line_id
    )
    assert any(h.get("action") == "released_orphan" for h in healed)
    assert await _po_invoiced(order["id"], line_id) == 0


@pytest.mark.anyio
async def test_t9_t10_kill_after_insert_and_commit_before_activate():
    order = await _order(qty=100)
    line_id = order["line_items"][0]["line_id"]
    invoice_id = str(uuid.uuid4())
    lines = [{
        "source_po_id": order["id"],
        "source_po_line_id": line_id,
        "source_po_line_idx": 0,
        "line_id": line_id,
        "ordered_qty": 100,
        "quantity": 8,
        "item_name": "X",
        "unit_price": 1000,
        "line_subtotal": 8000,
        "line_total": 8000,
    }]
    # T9: insert pending with reserved claims, no commit
    claims = await claim_invoice_line_billings(server.db, invoice_id, lines)
    doc = {
        "id": invoice_id,
        "invoice_number": f"TMP-{invoice_id[:8]}",
        "po_id": order["id"],
        "lines": lines,
        "financial_status": FINANCIAL_DRAFT,
        BILLING_COMMIT_PENDING_FIELD: True,
        "grand_total": 8000,
        "subtotal": 8000,
        "discount_amount": 0,
        "ppn_percent": 0,
        "ppn_amount": 0,
        "total_qty": 8,
    }
    await server.db.sales_invoices.insert_one(doc)
    # Expire lease → reconcile deletes orphan provisional
    await getattr(server.db, BILLING_COUNTERS).update_one(
        {"id": f"{order['id']}::{line_id}"},
        {"$set": {f"claims_by_invoice.{invoice_id}.lease_until": time.time() - 10}},
    )
    result = await reconcile_pending_orphan_invoice(server.db, invoice_id)
    assert result.get("action") == "deleted_orphan"
    assert await server.db.sales_invoices.find_one({"id": invoice_id}) is None
    assert await _po_invoiced(order["id"], line_id) == 0

    # T10: commit then kill before activate → reconcile activates
    invoice_id2 = str(uuid.uuid4())
    claims2 = await claim_invoice_line_billings(server.db, invoice_id2, lines)
    doc2 = {
        **doc,
        "id": invoice_id2,
        "invoice_number": f"TMP-{invoice_id2[:8]}",
        BILLING_COMMIT_PENDING_FIELD: True,
    }
    await server.db.sales_invoices.insert_one(doc2)
    await commit_claimed_billings(server.db, claims2)
    result2 = await reconcile_pending_orphan_invoice(server.db, invoice_id2)
    assert result2.get("action") == "activated_committed_claim"
    live = await server.db.sales_invoices.find_one({"id": invoice_id2}, {"_id": 0})
    assert live is not None
    assert live.get(BILLING_COMMIT_PENDING_FIELD) is not True
    assert await _po_invoiced(order["id"], line_id) == 8


@pytest.mark.anyio
async def test_t11_lost_lease_ownership():
    order = await _order(qty=100)
    line_id = order["line_items"][0]["line_id"]
    invoice_id = str(uuid.uuid4())
    lines = [{
        "source_po_id": order["id"],
        "source_po_line_id": line_id,
        "source_po_line_idx": 0,
        "line_id": line_id,
        "ordered_qty": 100,
        "quantity": 5,
    }]
    claims = await claim_invoice_line_billings(server.db, invoice_id, lines)
    # Steal lease token
    await getattr(server.db, BILLING_COUNTERS).update_one(
        {"id": f"{order['id']}::{line_id}"},
        {"$set": {f"claims_by_invoice.{invoice_id}.lease_token": "stolen-token"}},
    )
    from services.sales_invoice_allocations import require_reserved_billing_claims
    with pytest.raises(HTTPException) as exc:
        await require_reserved_billing_claims(server.db, claims)
    assert exc.value.status_code == 409
    assert "hilang" in str(exc.value.detail).lower() or "Fail-closed" in str(exc.value.detail)


@pytest.mark.anyio
async def test_t12_concurrent_healer():
    order = await _order(qty=100)
    line_id = order["line_items"][0]["line_id"]
    invoice_id = str(uuid.uuid4())
    lines = [{
        "source_po_id": order["id"],
        "source_po_line_id": line_id,
        "source_po_line_idx": 0,
        "line_id": line_id,
        "ordered_qty": 100,
        "quantity": 7,
        "item_name": "Y",
        "unit_price": 1,
        "line_subtotal": 7,
        "line_total": 7,
    }]
    await claim_invoice_line_billings(server.db, invoice_id, lines)
    await server.db.sales_invoices.insert_one({
        "id": invoice_id,
        "invoice_number": f"H-{invoice_id[:8]}",
        "po_id": order["id"],
        "lines": lines,
        "financial_status": FINANCIAL_DRAFT,
        BILLING_COMMIT_PENDING_FIELD: True,
        "total_qty": 7,
        "grand_total": 7,
        "subtotal": 7,
    })
    await getattr(server.db, BILLING_COUNTERS).update_one(
        {"id": f"{order['id']}::{line_id}"},
        {"$set": {f"claims_by_invoice.{invoice_id}.lease_until": time.time() - 20}},
    )
    r1, r2 = await asyncio.gather(
        reconcile_pending_orphan_invoice(server.db, invoice_id),
        reconcile_pending_orphan_invoice(server.db, invoice_id),
    )
    actions = {r1.get("action"), r2.get("action"), r1.get("reason"), r2.get("reason")}
    # One deletes or activates; other sees already_absent / recovery_in_progress / already_active
    assert await server.db.sales_invoices.find_one({"id": invoice_id}) is None or \
        (await server.db.sales_invoices.find_one({"id": invoice_id}) or {}).get(BILLING_COMMIT_PENDING_FIELD) is not True


@pytest.mark.anyio
async def test_t13_pending_visibility_gates():
    order = await _order(qty=100)
    invoice_id = str(uuid.uuid4())
    await server.db.sales_invoices.insert_one({
        "id": invoice_id,
        "invoice_number": f"P-{invoice_id[:8]}",
        "po_id": order["id"],
        "lines": [],
        "financial_status": FINANCIAL_DRAFT,
        BILLING_COMMIT_PENDING_FIELD: True,
        "customer": "PT Gap",
        "grand_total": 0,
        "subtotal": 0,
        "total_qty": 0,
    })
    listed = await list_sales_invoices(server.db)
    assert all(r.get("id") != invoice_id for r in listed)
    with pytest.raises(HTTPException):
        assert_invoice_not_provisional({"id": invoice_id, BILLING_COMMIT_PENDING_FIELD: True})
    with pytest.raises(HTTPException):
        await finalize_sales_invoice(server.db, invoice_id, USER)


@pytest.mark.anyio
async def test_t14_pending_without_claim_reconciliation():
    order = await _order(qty=100)
    invoice_id = str(uuid.uuid4())
    await server.db.sales_invoices.insert_one({
        "id": invoice_id,
        "invoice_number": f"NC-{invoice_id[:8]}",
        "po_id": order["id"],
        "lines": [{
            "source_po_id": order["id"],
            "source_po_line_id": order["line_items"][0]["line_id"],
            "quantity": 3,
        }],
        "financial_status": FINANCIAL_DRAFT,
        BILLING_COMMIT_PENDING_FIELD: True,
        "total_qty": 3,
        "grand_total": 0,
        "subtotal": 0,
    })
    # No claims on counters — safe delete
    result = await reconcile_pending_orphan_invoice(server.db, invoice_id)
    assert result.get("action") == "deleted_orphan"
    assert await server.db.sales_invoices.find_one({"id": invoice_id}) is None


@pytest.mark.anyio
async def test_t15_t16_rs_and_standalone_probe():
    available = await mongo_transactions_available(server.db)
    # Probe must not raise; standalone typically False → compensation path used.
    assert available in (True, False)
    order = await _order(qty=20)
    inv = await create_sales_invoice(
        server.db,
        {"po_id": order["id"], "lines": [{"line_idx": 0, "quantity": 2, "unit_price": 1000}]},
        USER,
    )
    assert not invoice_is_billing_pending(inv)
    detail = await get_sales_invoice_detail(server.db, inv["id"])
    assert detail["id"] == inv["id"]
