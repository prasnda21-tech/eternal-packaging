"""Integrity fixes for Stage 1 Flexible PO → SJ allocations.

Covers: unified claim, idempotent release, cancel paths, bulk multi-source,
loading/POD slice fulfillment, and concurrency on standalone Mongo.
"""
from __future__ import annotations

import asyncio
import os
import sys
import uuid
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ.setdefault("MONGO_URL", "mongodb://127.0.0.1:27017")
os.environ.setdefault("DB_NAME", "eternal_packaging_test_sj_integrity")
os.environ.setdefault("JWT_SECRET", "test-secret-key-12345678901234567890")
os.environ.setdefault("CORS_ORIGINS", "*")

import httpx
import server
from server import app, current_user
from services.sales_delivery_allocations import (
    ALLOCATION_COMMIT_PENDING_FIELD,
    ALLOCATION_COUNTERS,
    CLAIM_STATE_COMMITTED,
    CLAIM_STATE_RESERVED,
    RECOVERY_FENCE_FIELD,
    activate_delivery_allocation_commit,
    assess_multi_source_slice_fulfillment,
    claim_source_line_allocation,
    commit_claimed_allocations,
    commit_source_line_allocation,
    heal_expired_reservations_for_line,
    reconcile_pending_orphan_delivery,
    release_claimed_allocations,
    release_delivery_allocations,
    release_source_line_allocation,
    renew_allocation_lease,
    require_operational_sales_delivery,
    require_reserved_allocations,
    slice_lines_for_po,
)
from services.sales_deliveries import (
    ensure_generated_sj_pdf,
    get_sales_delivery_detail,
    list_sales_deliveries,
)
from fastapi import HTTPException
from services.physical_stock import cancel_sales_delivery_document_operational


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture(autouse=True)
def bind_motor():
    from motor.motor_asyncio import AsyncIOMotorClient
    server.client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    server.db = server.client[os.environ["DB_NAME"]]


def _admin():
    return {
        "email": "admin@eternalpackaging.com",
        "name": "Admin",
        "role": "admin",
        "id": "admin-1",
    }


async def _po(*, po_number: str, qty: int, customer_id: str, address: str = "Jl. Satu") -> dict:
    po_id = str(uuid.uuid4())
    line_id = f"{po_id}_line_0"
    doc = {
        "id": po_id,
        "po_number": po_number,
        "customer": "PT Integrity",
        "customer_id": customer_id,
        "outlet": "Cabang",
        "recipient": "Budi",
        "address": address,
        "delivery_zone": "Jakarta",
        "sales": "Vira",
        "operational_branch_id": "br-ygy",
        "branch_id": "1",
        "currency": "IDR",
        "line_items": [{
            "line_id": line_id,
            "name": "Produk X",
            "quantity": qty,
            "unit": "pcs",
            "unit_price": 1000.0,
            "master_item_id": "item-x",
            "production_process": "print",
        }],
        "total_quantity": qty,
        "current_stage": 11,
        "cancelled": False,
        "created_at": "2026-08-30T10:00:00Z",
        "shipments": [],
        "loading_confirmations": [],
        "delivery_confirmations": [],
    }
    await server.db.orders.insert_one(doc)
    doc.pop("_id", None)
    return doc


async def _counter(po_id: str, line_id: str) -> dict:
    cid = f"{po_id}::{line_id}"
    return await server.db[ALLOCATION_COUNTERS].find_one({"id": cid}, {"_id": 0}) or {}


def test_multi_source_provisional_delete_only_after_claim_release():
    """Guard crash-recovery ordering when standalone shipment sync fails."""
    import inspect
    from services.sales_deliveries import _create_multi_source_sales_delivery

    source = inspect.getsource(_create_multi_source_sales_delivery)
    inner = source.split("async def _persist_without_txn() -> None:", 1)[1].split(
        "await require_reserved_allocations(db, line_claims)", 1
    )[0]
    assert 'db.sales_deliveries.delete_one({"id": delivery_id})' not in inner

    compensate = source.split("async def _compensate_provisional_multi() -> None:", 1)[1].split(
        "async def _persist_without_txn() -> None:", 1
    )[0]
    assert compensate.index("await release_claimed_allocations(db, line_claims)") < (
        compensate.index('await db.sales_deliveries.delete_one({"id": delivery_id})')
    )


@pytest.mark.anyio
async def test_missing_committed_sj_claim_is_reported_without_counter_mutation():
    """Failed allocation diagnoses orphan claim but never releases it silently."""
    po_id = str(uuid.uuid4())
    line_id = f"{po_id}_line_0"
    old_sid = str(uuid.uuid4())
    await server.db[ALLOCATION_COUNTERS].delete_many({"source_po_id": po_id})
    first = await claim_source_line_allocation(
        server.db,
        source_po_id=po_id, source_line_id=line_id, source_line_idx=0,
        ordered_qty=500, qty=500, sales_delivery_id=old_sid,
    )
    await commit_claimed_allocations(server.db, [first])
    # Simulate a missing SJ document after successful counter commit.
    assert await server.db.sales_deliveries.find_one({"id": old_sid}) is None
    with pytest.raises(HTTPException) as exc:
        await claim_source_line_allocation(
            server.db,
            source_po_id=po_id, source_line_id=line_id, source_line_idx=0,
            ordered_qty=500, qty=100, sales_delivery_id=str(uuid.uuid4()),
        )
    assert exc.value.status_code == 409
    assert "klaim SJ committed tanpa dokumen SJ" in str(exc.value.detail)
    counter = await _counter(po_id, line_id)
    assert counter["allocated_qty"] == 500
    assert old_sid in counter["claims_by_delivery"]


@pytest.mark.anyio
async def test_release_idempotent_no_double_decrement():
    po_id = str(uuid.uuid4())
    line_id = f"{po_id}_l0"
    sj = str(uuid.uuid4())
    await server.db[ALLOCATION_COUNTERS].delete_many({"source_po_id": po_id})
    claim = await claim_source_line_allocation(
        server.db,
        source_po_id=po_id,
        source_line_id=line_id,
        source_line_idx=0,
        ordered_qty=100,
        qty=40,
        sales_delivery_id=sj,
    )
    assert claim["qty"] == 40
    r1 = await release_source_line_allocation(
        server.db, source_po_id=po_id, source_line_id=line_id, qty=40, sales_delivery_id=sj,
    )
    assert r1["released"] is True
    r2 = await release_source_line_allocation(
        server.db, source_po_id=po_id, source_line_id=line_id, qty=40, sales_delivery_id=sj,
    )
    assert r2["released"] is False
    assert r2["replayed"] is True
    cur = await _counter(po_id, line_id)
    assert int(cur.get("allocated_qty") or 0) == 0
    assert sj not in (cur.get("active_delivery_ids") or [])
    assert sj not in (cur.get("claims_by_delivery") or {})


@pytest.mark.anyio
async def test_bootstrap_excludes_cancelled_and_superseded_deliveries():
    """Historical inactive SJ must not exhaust a new line allocation counter."""
    po_id = str(uuid.uuid4())
    line_id = f"{po_id}_line_0"
    await server.db[ALLOCATION_COUNTERS].delete_many({"source_po_id": po_id})
    for status, cancelled, qty in [
        ("TERKIRIM", False, 200),       # active shipment must count
        ("TERKIRIM", True, 500),        # cancelled=True legacy SJ
        ("superseded", False, 500),     # replaced SJ without cancel boolean
    ]:
        await server.db.sales_deliveries.insert_one({
            "id": str(uuid.uuid4()),
            "po_id": po_id,
            "status": status,
            "cancelled": cancelled,
            "line_items": [{"line_id": line_id, "line_idx": 0, "qty": qty}],
        })

    claim = await claim_source_line_allocation(
        server.db,
        source_po_id=po_id,
        source_line_id=line_id,
        source_line_idx=0,
        ordered_qty=1000,
        qty=500,
        sales_delivery_id=str(uuid.uuid4()),
    )
    assert claim["qty"] == 500
    counter = await _counter(po_id, line_id)
    assert int(counter["allocated_qty"]) == 700
    assert int(counter["ordered_qty"]) == 1000


@pytest.mark.anyio
async def test_claim_retry_same_sj_idempotent():
    po_id = str(uuid.uuid4())
    line_id = f"{po_id}_l0"
    sj = str(uuid.uuid4())
    await server.db[ALLOCATION_COUNTERS].delete_many({"source_po_id": po_id})
    c1 = await claim_source_line_allocation(
        server.db, source_po_id=po_id, source_line_id=line_id, source_line_idx=0,
        ordered_qty=50, qty=20, sales_delivery_id=sj,
    )
    c2 = await claim_source_line_allocation(
        server.db, source_po_id=po_id, source_line_id=line_id, source_line_idx=0,
        ordered_qty=50, qty=20, sales_delivery_id=sj,
    )
    assert c2.get("replayed") is True
    cur = await _counter(po_id, line_id)
    assert int(cur["allocated_qty"]) == 20


@pytest.mark.anyio
async def test_concurrent_bootstrap_does_not_overwrite():
    po_id = str(uuid.uuid4())
    line_id = f"{po_id}_l0"
    await server.db[ALLOCATION_COUNTERS].delete_many({"source_po_id": po_id})

    async def _claim(sj_suffix: str, qty: int):
        return await claim_source_line_allocation(
            server.db,
            source_po_id=po_id,
            source_line_id=line_id,
            source_line_idx=0,
            ordered_qty=100,
            qty=qty,
            sales_delivery_id=f"{uuid.uuid4()}-{sj_suffix}",
        )

    results = await asyncio.gather(
        _claim("a", 30),
        _claim("b", 30),
        _claim("c", 30),
        return_exceptions=True,
    )
    ok = [r for r in results if isinstance(r, dict)]
    assert len(ok) >= 2
    cur = await _counter(po_id, line_id)
    allocated = int(cur.get("allocated_qty") or 0)
    assert allocated == sum(r["qty"] for r in ok)
    assert allocated <= 100


@pytest.mark.anyio
async def test_single_vs_multi_concurrent_no_overship():
    cust = f"cust-{uuid.uuid4().hex[:8]}"
    po_a = await _po(po_number=f"PO/S/{uuid.uuid4().hex[:6].upper()}", qty=1000, customer_id=cust)
    po_b = await _po(po_number=f"PO/M/{uuid.uuid4().hex[:6].upper()}", qty=1000, customer_id=cust)
    await server.db[ALLOCATION_COUNTERS].delete_many({})
    app.dependency_overrides[current_user] = _admin
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            async def single():
                return await client.post("/api/sales-deliveries", json={
                    "po_id": po_a["id"],
                    "line_items": [{"line_idx": 0, "line_id": po_a["line_items"][0]["line_id"], "qty": 1000}],
                })

            async def multi():
                return await client.post("/api/sales-deliveries", json={
                    "source_allocations": [
                        {
                            "source_po_id": po_a["id"],
                            "source_line_idx": 0,
                            "source_line_id": po_a["line_items"][0]["line_id"],
                            "allocated_qty": 1000,
                        },
                        {
                            "source_po_id": po_b["id"],
                            "source_line_idx": 0,
                            "source_line_id": po_b["line_items"][0]["line_id"],
                            "allocated_qty": 500,
                        },
                    ],
                })

            r1, r2 = await asyncio.gather(single(), multi(), return_exceptions=True)
            codes = []
            for r in (r1, r2):
                if isinstance(r, Exception):
                    codes.append(500)
                else:
                    codes.append(r.status_code)
            assert 200 in codes
            # At most one may fully take po_a's 1000
            sa = (await client.get(f"/api/sales-deliveries/po/{po_a['id']}/summary")).json()
            assert sa["total_shipped"] == 1000
            assert sa["total_remaining"] == 0
            cur = await _counter(po_a["id"], po_a["line_items"][0]["line_id"])
            assert int(cur.get("allocated_qty") or 0) == 1000
    finally:
        app.dependency_overrides.pop(current_user, None)


@pytest.mark.anyio
async def test_failure_after_claim_releases_counter():
    po_id = str(uuid.uuid4())
    line_id = f"{po_id}_l0"
    sj = str(uuid.uuid4())
    await server.db[ALLOCATION_COUNTERS].delete_many({"source_po_id": po_id})
    claim = await claim_source_line_allocation(
        server.db, source_po_id=po_id, source_line_id=line_id, source_line_idx=0,
        ordered_qty=80, qty=25, sales_delivery_id=sj,
    )
    # Simulate create failure compensation
    await release_source_line_allocation(
        server.db, source_po_id=po_id, source_line_id=line_id, qty=claim["qty"], sales_delivery_id=sj,
    )
    cur = await _counter(po_id, line_id)
    assert int(cur.get("allocated_qty") or 0) == 0
    # Retry create with new SJ id succeeds for full remaining
    sj2 = str(uuid.uuid4())
    claim2 = await claim_source_line_allocation(
        server.db, source_po_id=po_id, source_line_id=line_id, source_line_idx=0,
        ordered_qty=80, qty=80, sales_delivery_id=sj2,
    )
    assert claim2["qty"] == 80


@pytest.mark.anyio
async def test_operational_cancel_releases_and_retry_idempotent():
    cust = f"cust-{uuid.uuid4().hex[:8]}"
    po = await _po(po_number=f"PO/C/{uuid.uuid4().hex[:6].upper()}", qty=100, customer_id=cust)
    app.dependency_overrides[current_user] = _admin
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            res = await client.post("/api/sales-deliveries", json={
                "po_id": po["id"],
                "line_items": [{"line_idx": 0, "line_id": po["line_items"][0]["line_id"], "qty": 40}],
            })
            assert res.status_code == 200, res.text
            delivery = res.json()["delivery"]
            cur = await _counter(po["id"], po["line_items"][0]["line_id"])
            assert int(cur["allocated_qty"]) == 40

            r1 = await cancel_sales_delivery_document_operational(
                server.db, delivery, actor_id="admin-1", reason="test",
            )
            assert r1["soft_cancelled"] is True
            cur = await _counter(po["id"], po["line_items"][0]["line_id"])
            assert int(cur.get("allocated_qty") or 0) == 0

            # Retry cancel must not drive counter negative
            fresh = await server.db.sales_deliveries.find_one({"id": delivery["id"]}, {"_id": 0})
            r2 = await cancel_sales_delivery_document_operational(
                server.db, fresh, actor_id="admin-1", reason="retry",
            )
            assert r2["replayed"] is True
            cur = await _counter(po["id"], po["line_items"][0]["line_id"])
            assert int(cur.get("allocated_qty") or 0) == 0
    finally:
        app.dependency_overrides.pop(current_user, None)


@pytest.mark.anyio
async def test_legacy_delivery_release_without_counter_is_noop():
    delivery = {
        "id": str(uuid.uuid4()),
        "po_id": str(uuid.uuid4()),
        "line_items": [{"line_id": "legacy-l0", "line_idx": 0, "qty": 10}],
    }
    results = await release_delivery_allocations(server.db, delivery)
    assert all(r.get("replayed") for r in results)


@pytest.mark.anyio
async def test_multi_source_loading_pod_slice_fulfillment():
    cust = f"cust-{uuid.uuid4().hex[:8]}"
    po_a = await _po(po_number=f"PO/A/{uuid.uuid4().hex[:6].upper()}", qty=3000, customer_id=cust)
    po_b = await _po(po_number=f"PO/B/{uuid.uuid4().hex[:6].upper()}", qty=2000, customer_id=cust)
    app.dependency_overrides[current_user] = _admin
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            res = await client.post("/api/sales-deliveries", json={
                "source_allocations": [
                    {
                        "source_po_id": po_a["id"],
                        "source_line_idx": 0,
                        "source_line_id": po_a["line_items"][0]["line_id"],
                        "allocated_qty": 3000,
                    },
                    {
                        "source_po_id": po_b["id"],
                        "source_line_idx": 0,
                        "source_line_id": po_b["line_items"][0]["line_id"],
                        "allocated_qty": 2000,
                    },
                ],
            })
            assert res.status_code == 200, res.text
            delivery = res.json()["delivery"]
            detail = (await client.get(f"/api/sales-deliveries/{delivery['id']}")).json()
            fulfill = detail["slice_fulfillment"]
            assert fulfill["is_multi_source"] is True
            assert fulfill["complete"] is False
            assert fulfill["loading_complete"] is False
            assert fulfill["pod_complete"] is False
            assert len(fulfill["sources"]) == 2

            # Simulate loading + POD on PO-A only (partial physical confirm)
            oa = await server.db.orders.find_one({"id": po_a["id"]}, {"_id": 0})
            sh_a = next(s for s in oa["shipments"] if s.get("sales_delivery_id") == delivery["id"])
            slice_a = slice_lines_for_po(delivery, po_a["id"])
            assert sum(x["qty"] for x in slice_a) == 3000
            await server.db.orders.update_one(
                {"id": po_a["id"]},
                {"$set": {
                    "loading_confirmations": [{
                        "id": str(uuid.uuid4()),
                        "surat_jalan_id": delivery["id"],
                        "lines": [{"qty": 3000, "line_idx": 0}],
                    }],
                    "delivery_confirmations": [{
                        "id": str(uuid.uuid4()),
                        "sales_delivery_id": delivery["id"],
                        "shipment_id": sh_a["id"],
                        "qty": 3000,
                    }],
                    "shipments": [
                        {**sh_a, "status": "delivered", "delivered_at": "2026-11-01T00:00:00Z", "qty_delivered": 3000}
                        if sh.get("sales_delivery_id") == delivery["id"] else sh
                        for sh in oa["shipments"]
                    ],
                }},
            )
            detail2 = (await client.get(f"/api/sales-deliveries/{delivery['id']}")).json()
            f2 = detail2["slice_fulfillment"]
            by_po = {s["source_po_id"]: s for s in f2["sources"]}
            assert by_po[po_a["id"]]["loading_complete"] is True
            assert by_po[po_a["id"]]["delivered"] is True
            assert by_po[po_b["id"]]["loading_complete"] is False
            assert by_po[po_b["id"]]["delivered"] is False
            assert f2["complete"] is False

            # Confirm PO-B slice → combined SJ complete; stock events remain per-order
            # (no auto-confirm of the other PO — operator confirms each slice).
            ob = await server.db.orders.find_one({"id": po_b["id"]}, {"_id": 0})
            sh_b = next(s for s in ob["shipments"] if s.get("sales_delivery_id") == delivery["id"])
            assert sh_b["total_qty"] == 2000
            await server.db.orders.update_one(
                {"id": po_b["id"]},
                {"$set": {
                    "loading_confirmations": [{
                        "id": str(uuid.uuid4()),
                        "surat_jalan_id": delivery["id"],
                        "lines": [{"qty": 2000, "line_idx": 0}],
                    }],
                    "delivery_confirmations": [{
                        "id": str(uuid.uuid4()),
                        "sales_delivery_id": delivery["id"],
                        "shipment_id": sh_b["id"],
                        "qty": 2000,
                    }],
                    "shipments": [
                        {**sh_b, "status": "delivered", "delivered_at": "2026-11-01T00:00:00Z", "qty_delivered": 2000}
                        if sh.get("sales_delivery_id") == delivery["id"] else sh
                        for sh in ob["shipments"]
                    ],
                }},
            )
            detail3 = (await client.get(f"/api/sales-deliveries/{delivery['id']}")).json()
            assert detail3["slice_fulfillment"]["complete"] is True
            # Distinct confirmation ids per PO → no shared double-count identity
            oa2 = await server.db.orders.find_one({"id": po_a["id"]}, {"_id": 0})
            ob2 = await server.db.orders.find_one({"id": po_b["id"]}, {"_id": 0})
            conf_a = oa2["delivery_confirmations"][0]["id"]
            conf_b = ob2["delivery_confirmations"][0]["id"]
            assert conf_a != conf_b
    finally:
        app.dependency_overrides.pop(current_user, None)


@pytest.mark.anyio
async def test_hard_delete_releases_counter_and_retry_safe():
    cust = f"cust-{uuid.uuid4().hex[:8]}"
    po = await _po(po_number=f"PO/H/{uuid.uuid4().hex[:6].upper()}", qty=50, customer_id=cust)
    app.dependency_overrides[current_user] = _admin
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            res = await client.post("/api/sales-deliveries", json={
                "po_id": po["id"],
                "line_items": [{"line_idx": 0, "line_id": po["line_items"][0]["line_id"], "qty": 20}],
            })
            assert res.status_code == 200, res.text
            delivery = res.json()["delivery"]
            del_res = await client.delete(f"/api/sales-deliveries/{delivery['id']}")
            assert del_res.status_code == 200, del_res.text
            cur = await _counter(po["id"], po["line_items"][0]["line_id"])
            assert int(cur.get("allocated_qty") or 0) == 0
            # Second delete → 404; counter stays non-negative
            del2 = await client.delete(f"/api/sales-deliveries/{delivery['id']}")
            assert del2.status_code == 404
            cur = await _counter(po["id"], po["line_items"][0]["line_id"])
            assert int(cur.get("allocated_qty") or 0) == 0
    finally:
        app.dependency_overrides.pop(current_user, None)


@pytest.mark.anyio
async def test_multi_hard_delete_clears_all_mirrors_via_helper():
    """Unit-level: delivery_source_po_ids covers all mirrors for delete sync."""
    from services.sales_delivery_allocations import delivery_source_po_ids
    delivery = {
        "id": "sj-1",
        "po_id": "po-a",
        "source_po_ids": ["po-a", "po-b"],
        "source_allocations": [
            {"source_po_id": "po-a", "source_line_id": "l1", "allocated_qty": 1},
            {"source_po_id": "po-b", "source_line_id": "l2", "allocated_qty": 1},
        ],
    }
    assert delivery_source_po_ids(delivery) == ["po-a", "po-b"]


@pytest.mark.anyio
async def test_single_po_regression_stamps_and_claims():
    cust = f"cust-{uuid.uuid4().hex[:8]}"
    po = await _po(po_number=f"PO/R/{uuid.uuid4().hex[:6].upper()}", qty=100, customer_id=cust)
    app.dependency_overrides[current_user] = _admin
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            res = await client.post("/api/sales-deliveries", json={
                "po_id": po["id"],
                "line_items": [{"line_idx": 0, "line_id": po["line_items"][0]["line_id"], "qty": 30}],
            })
            assert res.status_code == 200, res.text
            delivery = res.json()["delivery"]
            assert delivery["is_multi_source"] is False
            assert delivery["source_allocations"]
            cur = await _counter(po["id"], po["line_items"][0]["line_id"])
            assert int(cur["allocated_qty"]) == 30
            assert delivery["id"] in (cur.get("active_delivery_ids") or [])
            legacy = await client.post(f"/api/orders/{po['id']}/surat-jalan", json={})
            assert legacy.status_code == 409
    finally:
        app.dependency_overrides.pop(current_user, None)


@pytest.mark.anyio
async def test_assess_partial_pod_unit():
    delivery = {
        "id": "sj-x",
        "sj_number": "SJ/X",
        "po_id": "a",
        "source_po_ids": ["a", "b"],
        "is_multi_source": True,
        "line_items": [
            {"source_po_id": "a", "source_line_id": "la", "source_line_idx": 0, "qty": 5, "name": "X"},
            {"source_po_id": "b", "source_line_id": "lb", "source_line_idx": 0, "qty": 7, "name": "X"},
        ],
    }
    orders = {
        "a": {
            "shipments": [{"id": "sj-x", "sales_delivery_id": "sj-x", "total_qty": 5}],
            "loading_confirmations": [{"surat_jalan_id": "sj-x", "lines": [{"qty": 5}]}],
            "delivery_confirmations": [{"sales_delivery_id": "sj-x", "qty": 5}],
        },
        "b": {
            "shipments": [{"id": "sj-x", "sales_delivery_id": "sj-x", "total_qty": 7}],
            "loading_confirmations": [],
            "delivery_confirmations": [],
        },
    }
    out = assess_multi_source_slice_fulfillment(delivery, orders)
    assert out["complete"] is False
    assert out["sources"][0]["loading_complete"] is True
    assert out["sources"][1]["loading_complete"] is False


@pytest.mark.anyio
async def test_standalone_cancel_releases_and_retry():
    from services.warehouse_stock_standalone import cancel_sales_delivery_document_standalone

    cust = f"cust-{uuid.uuid4().hex[:8]}"
    po_a = await _po(po_number=f"PO/SA/{uuid.uuid4().hex[:6].upper()}", qty=100, customer_id=cust)
    po_b = await _po(po_number=f"PO/SB/{uuid.uuid4().hex[:6].upper()}", qty=100, customer_id=cust)
    app.dependency_overrides[current_user] = _admin
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            res = await client.post("/api/sales-deliveries", json={
                "source_allocations": [
                    {
                        "source_po_id": po_a["id"],
                        "source_line_idx": 0,
                        "source_line_id": po_a["line_items"][0]["line_id"],
                        "allocated_qty": 40,
                    },
                    {
                        "source_po_id": po_b["id"],
                        "source_line_idx": 0,
                        "source_line_id": po_b["line_items"][0]["line_id"],
                        "allocated_qty": 30,
                    },
                ],
            })
            assert res.status_code == 200, res.text
            delivery = res.json()["delivery"]
            r1 = await cancel_sales_delivery_document_standalone(
                server.db, delivery, actor_id="admin-1", reason="standalone-test",
            )
            assert r1["soft_cancelled"] is True
            assert int((await _counter(po_a["id"], po_a["line_items"][0]["line_id"])).get("allocated_qty") or 0) == 0
            assert int((await _counter(po_b["id"], po_b["line_items"][0]["line_id"])).get("allocated_qty") or 0) == 0
            # Both mirrors cancelled
            for pid in (po_a["id"], po_b["id"]):
                order = await server.db.orders.find_one({"id": pid}, {"_id": 0})
                sh = next(s for s in order["shipments"] if s.get("sales_delivery_id") == delivery["id"])
                assert sh.get("is_cancelled") is True or str(sh.get("status") or "").lower() == "cancelled"
            fresh = await server.db.sales_deliveries.find_one({"id": delivery["id"]}, {"_id": 0})
            r2 = await cancel_sales_delivery_document_standalone(
                server.db, fresh, actor_id="admin-1", reason="retry",
            )
            assert r2["replayed"] is True
            assert int((await _counter(po_a["id"], po_a["line_items"][0]["line_id"])).get("allocated_qty") or 0) == 0
    finally:
        app.dependency_overrides.pop(current_user, None)


@pytest.mark.anyio
async def test_r2_cancel_path_skipped_without_replica_set():
    """R2 transactional cancel requires replica set — do not claim PASS on standalone only."""
    from services.sales_delivery_allocations import mongo_transactions_available
    from services.physical_stock import (
        PhysicalStockUnavailableError,
        cancel_sales_delivery_document,
        require_transaction_support,
    )

    if await mongo_transactions_available(server.db):
        pytest.skip("Replica set available — covered by dedicated R2 cancel suite when wired")
    # Environment is standalone: require_transaction_support must fail-closed.
    with pytest.raises((PhysicalStockUnavailableError, Exception)):
        await require_transaction_support(server.client)
    # Direct R2 cancel without txn support must not silently succeed without release.
    delivery = {
        "id": str(uuid.uuid4()),
        "po_id": str(uuid.uuid4()),
        "sj_number": "SJ/R2SKIP",
        "source_po_ids": [],
        "line_items": [],
    }
    with pytest.raises((PhysicalStockUnavailableError, Exception)):
        await cancel_sales_delivery_document(
            server.db, delivery, actor_id="admin-1", reason="no-rs",
        )


@pytest.mark.anyio
async def test_bulk_delete_multi_source_requires_txn_or_clears_mirrors():
    """Real RS: bulk hard-delete multi-source draft SJ clears counters + mirrors atomically."""
    from services.sales_delivery_allocations import mongo_transactions_available

    if not await mongo_transactions_available(server.db):
        pytest.skip(
            "Bulk hard-delete requires Mongo replica-set transactions; "
            "standalone coverage is via single hard-delete + delivery_source_po_ids helper"
        )

    cust = f"cust-{uuid.uuid4().hex[:8]}"
    po_a = await _po(po_number=f"PO/BD/A/{uuid.uuid4().hex[:5].upper()}", qty=80, customer_id=cust)
    po_b = await _po(po_number=f"PO/BD/B/{uuid.uuid4().hex[:5].upper()}", qty=80, customer_id=cust)
    app.dependency_overrides[current_user] = _admin
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            create = await client.post("/api/sales-deliveries", json={
                "po_id": po_a["id"],
                "delivery_date": "2026-08-30",
                "source_allocations": [
                    {
                        "source_po_id": po_a["id"],
                        "source_line_id": po_a["line_items"][0]["line_id"],
                        "source_line_idx": 0,
                        "allocated_qty": 20,
                    },
                    {
                        "source_po_id": po_b["id"],
                        "source_line_id": po_b["line_items"][0]["line_id"],
                        "source_line_idx": 0,
                        "allocated_qty": 15,
                    },
                ],
            })
            assert create.status_code == 200, create.text
            delivery = create.json()["delivery"]
            sid = delivery["id"]
            assert delivery.get("is_multi_source") is True

            for pid in (po_a["id"], po_b["id"]):
                order = await server.db.orders.find_one({"id": pid}, {"_id": 0, "shipments": 1})
                assert any(
                    str((sh or {}).get("sales_delivery_id") or (sh or {}).get("id")) == sid
                    for sh in (order.get("shipments") or [])
                )

            bulk = await client.post(
                "/api/sales-deliveries-bulk/delete",
                json={"ids": [sid], "reason": "gap-closure-rs-bulk-delete"},
            )
            assert bulk.status_code == 200, bulk.text
            body = bulk.json()
            assert int(body.get("deleted_count") or 0) >= 1

            assert await server.db.sales_deliveries.find_one({"id": sid}) is None
            for pid, line in (
                (po_a["id"], po_a["line_items"][0]["line_id"]),
                (po_b["id"], po_b["line_items"][0]["line_id"]),
            ):
                cur = await _counter(pid, line)
                assert int(cur.get("allocated_qty") or 0) == 0
                order = await server.db.orders.find_one({"id": pid}, {"_id": 0, "shipments": 1})
                active_mirrors = [
                    sh for sh in (order.get("shipments") or [])
                    if isinstance(sh, dict)
                    and str(sh.get("sales_delivery_id") or sh.get("id")) == sid
                    and not sh.get("deleted_reason")
                    and not sh.get("is_deleted")
                ]
                assert active_mirrors == []
    finally:
        app.dependency_overrides.pop(current_user, None)


@pytest.mark.anyio
async def test_multi_release_delivery_allocations_both_lines():
    po_a = str(uuid.uuid4())
    po_b = str(uuid.uuid4())
    la, lb = f"{po_a}_l", f"{po_b}_l"
    sj = str(uuid.uuid4())
    await server.db[ALLOCATION_COUNTERS].delete_many({"id": {"$in": [f"{po_a}::{la}", f"{po_b}::{lb}"]}})
    await claim_source_line_allocation(
        server.db, source_po_id=po_a, source_line_id=la, source_line_idx=0,
        ordered_qty=10, qty=4, sales_delivery_id=sj,
    )
    await claim_source_line_allocation(
        server.db, source_po_id=po_b, source_line_id=lb, source_line_idx=0,
        ordered_qty=10, qty=6, sales_delivery_id=sj,
    )
    delivery = {
        "id": sj,
        "po_id": po_a,
        "source_po_ids": [po_a, po_b],
        "source_allocations": [
            {"source_po_id": po_a, "source_line_id": la, "allocated_qty": 4},
            {"source_po_id": po_b, "source_line_id": lb, "allocated_qty": 6},
        ],
        "line_items": [
            {"source_po_id": po_a, "source_line_id": la, "qty": 4},
            {"source_po_id": po_b, "source_line_id": lb, "qty": 6},
        ],
    }
    first = await release_delivery_allocations(server.db, delivery)
    assert any(r.get("released") for r in first)
    second = await release_delivery_allocations(server.db, delivery)
    assert all(r.get("replayed") for r in second)
    assert int((await _counter(po_a, la)).get("allocated_qty") or 0) == 0
    assert int((await _counter(po_b, lb)).get("allocated_qty") or 0) == 0


async def _expire_reservation(po_id: str, line_id: str, sj: str) -> None:
    """Simulate lease expiry without waiting real wall-clock time."""
    cid = f"{po_id}::{line_id}"
    await server.db[ALLOCATION_COUNTERS].update_one(
        {"id": cid},
        {"$set": {f"claims_by_delivery.{sj}.lease_until": 1.0}},
    )


@pytest.mark.anyio
async def test_t1_kill_after_claim_before_insert_recovers():
    po_id = str(uuid.uuid4())
    line_id = f"{po_id}_l0"
    sj_orphan = str(uuid.uuid4())
    await server.db[ALLOCATION_COUNTERS].delete_many({"source_po_id": po_id})
    claim = await claim_source_line_allocation(
        server.db, source_po_id=po_id, source_line_id=line_id, source_line_idx=0,
        ordered_qty=100, qty=40, sales_delivery_id=sj_orphan,
    )
    assert claim["state"] == CLAIM_STATE_RESERVED
    assert await server.db.sales_deliveries.find_one({"id": sj_orphan}) is None
    await _expire_reservation(po_id, line_id, sj_orphan)
    # New identity create-claim triggers heal then reserves remaining capacity.
    sj_new = str(uuid.uuid4())
    claim2 = await claim_source_line_allocation(
        server.db, source_po_id=po_id, source_line_id=line_id, source_line_idx=0,
        ordered_qty=100, qty=100, sales_delivery_id=sj_new,
    )
    assert claim2["qty"] == 100
    cur = await _counter(po_id, line_id)
    assert int(cur["allocated_qty"]) == 100
    assert sj_orphan not in (cur.get("claims_by_delivery") or {})
    assert sj_new in (cur.get("claims_by_delivery") or {})


@pytest.mark.anyio
async def test_t2_active_reservation_not_healed():
    po_id = str(uuid.uuid4())
    line_id = f"{po_id}_l0"
    sj = str(uuid.uuid4())
    await server.db[ALLOCATION_COUNTERS].delete_many({"source_po_id": po_id})
    claim = await claim_source_line_allocation(
        server.db, source_po_id=po_id, source_line_id=line_id, source_line_idx=0,
        ordered_qty=100, qty=40, sales_delivery_id=sj,
    )
    healed = await heal_expired_reservations_for_line(
        server.db, source_po_id=po_id, source_line_id=line_id,
    )
    assert healed == []
    cur = await _counter(po_id, line_id)
    assert int(cur["allocated_qty"]) == 40
    assert (cur["claims_by_delivery"][sj]["state"] == CLAIM_STATE_RESERVED)
    # Ownership still valid
    await require_reserved_allocations(server.db, [claim])


@pytest.mark.anyio
async def test_t3_same_identity_retry_no_double_claim():
    po_id = str(uuid.uuid4())
    line_id = f"{po_id}_l0"
    sj = str(uuid.uuid4())
    await server.db[ALLOCATION_COUNTERS].delete_many({"source_po_id": po_id})
    c1 = await claim_source_line_allocation(
        server.db, source_po_id=po_id, source_line_id=line_id, source_line_idx=0,
        ordered_qty=80, qty=25, sales_delivery_id=sj,
    )
    c2 = await claim_source_line_allocation(
        server.db, source_po_id=po_id, source_line_id=line_id, source_line_idx=0,
        ordered_qty=80, qty=25, sales_delivery_id=sj,
    )
    assert c2["replayed"] is True
    assert int((await _counter(po_id, line_id))["allocated_qty"]) == 25
    await commit_source_line_allocation(
        server.db, source_po_id=po_id, source_line_id=line_id,
        sales_delivery_id=sj, lease_token=c1["lease_token"],
    )
    c3 = await claim_source_line_allocation(
        server.db, source_po_id=po_id, source_line_id=line_id, source_line_idx=0,
        ordered_qty=80, qty=25, sales_delivery_id=sj,
    )
    assert c3["replayed"] is True
    assert c3["state"] == CLAIM_STATE_COMMITTED
    assert int((await _counter(po_id, line_id))["allocated_qty"]) == 25


@pytest.mark.anyio
async def test_t4_new_identity_after_orphan_no_overship():
    po_id = str(uuid.uuid4())
    line_id = f"{po_id}_l0"
    await server.db[ALLOCATION_COUNTERS].delete_many({"source_po_id": po_id})
    sj1 = str(uuid.uuid4())
    await claim_source_line_allocation(
        server.db, source_po_id=po_id, source_line_id=line_id, source_line_idx=0,
        ordered_qty=100, qty=60, sales_delivery_id=sj1,
    )
    await _expire_reservation(po_id, line_id, sj1)
    # Cannot take 60 again on top of unhealed... heal runs inside claim
    sj2 = str(uuid.uuid4())
    c2 = await claim_source_line_allocation(
        server.db, source_po_id=po_id, source_line_id=line_id, source_line_idx=0,
        ordered_qty=100, qty=60, sales_delivery_id=sj2,
    )
    assert c2["qty"] == 60
    with pytest.raises(HTTPException) as exc:
        await claim_source_line_allocation(
            server.db, source_po_id=po_id, source_line_id=line_id, source_line_idx=0,
            ordered_qty=100, qty=50, sales_delivery_id=str(uuid.uuid4()),
        )
    assert exc.value.status_code == 409
    assert int((await _counter(po_id, line_id))["allocated_qty"]) == 60


@pytest.mark.anyio
async def test_t5_concurrent_heal_no_double_release():
    po_id = str(uuid.uuid4())
    line_id = f"{po_id}_l0"
    sj = str(uuid.uuid4())
    await server.db[ALLOCATION_COUNTERS].delete_many({"source_po_id": po_id})
    await claim_source_line_allocation(
        server.db, source_po_id=po_id, source_line_id=line_id, source_line_idx=0,
        ordered_qty=50, qty=20, sales_delivery_id=sj,
    )
    await _expire_reservation(po_id, line_id, sj)
    results = await asyncio.gather(
        heal_expired_reservations_for_line(server.db, source_po_id=po_id, source_line_id=line_id),
        heal_expired_reservations_for_line(server.db, source_po_id=po_id, source_line_id=line_id),
    )
    released = sum(1 for batch in results for row in batch if row.get("released"))
    assert released == 1
    assert int((await _counter(po_id, line_id)).get("allocated_qty") or 0) == 0


@pytest.mark.anyio
async def test_t6_lost_lease_cannot_persist():
    po_id = str(uuid.uuid4())
    line_id = f"{po_id}_l0"
    sj = str(uuid.uuid4())
    await server.db[ALLOCATION_COUNTERS].delete_many({"source_po_id": po_id})
    claim = await claim_source_line_allocation(
        server.db, source_po_id=po_id, source_line_id=line_id, source_line_idx=0,
        ordered_qty=40, qty=10, sales_delivery_id=sj,
    )
    await _expire_reservation(po_id, line_id, sj)
    await heal_expired_reservations_for_line(
        server.db, source_po_id=po_id, source_line_id=line_id,
    )
    with pytest.raises(HTTPException) as exc:
        await require_reserved_allocations(server.db, [claim])
    assert exc.value.status_code == 409
    with pytest.raises(HTTPException):
        await renew_allocation_lease(
            server.db,
            source_po_id=po_id,
            source_line_id=line_id,
            sales_delivery_id=sj,
            lease_token=claim["lease_token"],
        )


@pytest.mark.anyio
async def test_t7_valid_claim_untouched_by_orphan_heal():
    po_id = str(uuid.uuid4())
    line_id = f"{po_id}_l0"
    sj_orphan = str(uuid.uuid4())
    sj_live = str(uuid.uuid4())
    await server.db[ALLOCATION_COUNTERS].delete_many({"source_po_id": po_id})
    await claim_source_line_allocation(
        server.db, source_po_id=po_id, source_line_id=line_id, source_line_idx=0,
        ordered_qty=100, qty=30, sales_delivery_id=sj_orphan,
    )
    live = await claim_source_line_allocation(
        server.db, source_po_id=po_id, source_line_id=line_id, source_line_idx=0,
        ordered_qty=100, qty=20, sales_delivery_id=sj_live,
    )
    await _expire_reservation(po_id, line_id, sj_orphan)
    await heal_expired_reservations_for_line(
        server.db, source_po_id=po_id, source_line_id=line_id,
    )
    cur = await _counter(po_id, line_id)
    assert int(cur["allocated_qty"]) == 20
    assert sj_orphan not in cur["claims_by_delivery"]
    assert cur["claims_by_delivery"][sj_live]["qty"] == 20
    await require_reserved_allocations(server.db, [live])


@pytest.mark.anyio
async def test_t8_crash_after_insert_promotes_not_releases():
    po_id = str(uuid.uuid4())
    line_id = f"{po_id}_l0"
    sj = str(uuid.uuid4())
    await server.db[ALLOCATION_COUNTERS].delete_many({"source_po_id": po_id})
    claim = await claim_source_line_allocation(
        server.db, source_po_id=po_id, source_line_id=line_id, source_line_idx=0,
        ordered_qty=70, qty=15, sales_delivery_id=sj,
    )
    # Simulate insert succeeded but commit never ran; lease then expired.
    await server.db.sales_deliveries.insert_one({
        "id": sj, "po_id": po_id, "is_cancelled": False, "line_items": [],
    })
    await _expire_reservation(po_id, line_id, sj)
    healed = await heal_expired_reservations_for_line(
        server.db, source_po_id=po_id, source_line_id=line_id,
    )
    assert any(h.get("action") == "promoted_committed" for h in healed)
    cur = await _counter(po_id, line_id)
    assert int(cur["allocated_qty"]) == 15
    assert cur["claims_by_delivery"][sj]["state"] == CLAIM_STATE_COMMITTED
    # Re-heal / re-commit idempotent
    again = await commit_source_line_allocation(
        server.db, source_po_id=po_id, source_line_id=line_id,
        sales_delivery_id=sj, lease_token=claim["lease_token"],
    )
    assert again.get("replayed") is True
    assert int((await _counter(po_id, line_id))["allocated_qty"]) == 15


@pytest.mark.anyio
async def test_t9_legacy_int_claim_not_auto_healed():
    po_id = str(uuid.uuid4())
    line_id = f"{po_id}_l0"
    sj = str(uuid.uuid4())
    cid = f"{po_id}::{line_id}"
    await server.db[ALLOCATION_COUNTERS].delete_many({"id": cid})
    await server.db[ALLOCATION_COUNTERS].insert_one({
        "id": cid,
        "source_po_id": po_id,
        "source_line_id": line_id,
        "allocated_qty": 12,
        "ordered_qty": 100,
        "claims_by_delivery": {sj: 12},  # legacy bare int
        "active_delivery_ids": [sj],
    })
    healed = await heal_expired_reservations_for_line(
        server.db, source_po_id=po_id, source_line_id=line_id,
    )
    assert healed == []
    assert int((await _counter(po_id, line_id))["allocated_qty"]) == 12
    # Cancel-style release still works
    r = await release_source_line_allocation(
        server.db, source_po_id=po_id, source_line_id=line_id, qty=12, sales_delivery_id=sj,
    )
    assert r["released"] is True
    assert int((await _counter(po_id, line_id)).get("allocated_qty") or 0) == 0


# ── Race A fail-closed (require → insert → commit) ───────────────────────────


async def _race_a_setup(ordered: int = 100, qty: int = 40):
    po_id = str(uuid.uuid4())
    line_id = f"{po_id}_l0"
    await server.db[ALLOCATION_COUNTERS].delete_many({"source_po_id": po_id})
    await server.db.sales_deliveries.delete_many({"po_id": po_id})
    return po_id, line_id


@pytest.mark.anyio
async def test_race_a_t1_t2_lost_lease_after_require_no_overship():
    """T1/T2: Worker A loses lease; B wins; A commit raises; only B SJ active."""
    po_id, line_id = await _race_a_setup()
    sj_a, sj_b = str(uuid.uuid4()), str(uuid.uuid4())

    claim_a = await claim_source_line_allocation(
        server.db, source_po_id=po_id, source_line_id=line_id, source_line_idx=0,
        ordered_qty=100, qty=40, sales_delivery_id=sj_a,
    )
    await require_reserved_allocations(server.db, [claim_a])
    await _expire_reservation(po_id, line_id, sj_a)
    healed = await heal_expired_reservations_for_line(
        server.db, source_po_id=po_id, source_line_id=line_id,
    )
    assert any(h.get("released") for h in healed)

    claim_b = await claim_source_line_allocation(
        server.db, source_po_id=po_id, source_line_id=line_id, source_line_idx=0,
        ordered_qty=100, qty=40, sales_delivery_id=sj_b,
    )
    await server.db.sales_deliveries.insert_one({
        "id": sj_b, "po_id": po_id, "is_cancelled": False,
        ALLOCATION_COMMIT_PENDING_FIELD: True,
        "line_items": [{"line_id": line_id, "qty": 40}],
    })
    await commit_claimed_allocations(server.db, [claim_b])
    await activate_delivery_allocation_commit(server.db, sj_b)

    # Worker A continues insert (provisional) then must fail closed on commit.
    await server.db.sales_deliveries.insert_one({
        "id": sj_a, "po_id": po_id, "is_cancelled": False,
        ALLOCATION_COMMIT_PENDING_FIELD: True,
        "line_items": [{"line_id": line_id, "qty": 40}],
    })
    with pytest.raises(HTTPException) as exc:
        await commit_claimed_allocations(server.db, [claim_a])
    assert exc.value.status_code == 409

    # Compensation: delete only A's provisional SJ; release is no-op for stolen claim.
    await release_claimed_allocations(server.db, [claim_a])
    await server.db.sales_deliveries.delete_one({"id": sj_a})

    assert await server.db.sales_deliveries.find_one({"id": sj_a}) is None
    b_doc = await server.db.sales_deliveries.find_one({"id": sj_b}, {"_id": 0})
    assert b_doc is not None
    assert b_doc.get(ALLOCATION_COMMIT_PENDING_FIELD) is not True
    cur = await _counter(po_id, line_id)
    assert int(cur["allocated_qty"]) == 40
    assert sj_b in cur["claims_by_delivery"]
    assert sj_a not in (cur.get("claims_by_delivery") or {})
    # No orphan mirror for A
    order = await server.db.orders.find_one({"id": po_id})
    if order:
        mirrors = [
            s for s in (order.get("shipments") or [])
            if (s.get("id") or s.get("sales_delivery_id")) == sj_a
        ]
        assert mirrors == []


@pytest.mark.anyio
async def test_race_a_t3_commit_no_claim_raises():
    po_id, line_id = await _race_a_setup()
    sj = str(uuid.uuid4())
    with pytest.raises(HTTPException) as exc:
        await commit_claimed_allocations(
            server.db,
            [{
                "source_po_id": po_id,
                "source_line_id": line_id,
                "sales_delivery_id": sj,
                "lease_token": "x",
                "qty": 10,
            }],
        )
    assert exc.value.status_code == 409
    assert "no_claim" in str(exc.value.detail) or "Fail-closed" in str(exc.value.detail)


@pytest.mark.anyio
async def test_race_a_t4_cleanup_does_not_delete_other_sj():
    po_id, line_id = await _race_a_setup()
    sj_a, sj_b = str(uuid.uuid4()), str(uuid.uuid4())
    claim_b = await claim_source_line_allocation(
        server.db, source_po_id=po_id, source_line_id=line_id, source_line_idx=0,
        ordered_qty=100, qty=25, sales_delivery_id=sj_b,
    )
    await commit_claimed_allocations(server.db, [claim_b])
    await server.db.sales_deliveries.insert_one({
        "id": sj_b, "po_id": po_id, "is_cancelled": False, "line_items": [{"qty": 25}],
    })
    await server.db.sales_deliveries.insert_one({
        "id": sj_a, "po_id": po_id, "is_cancelled": False,
        ALLOCATION_COMMIT_PENDING_FIELD: True, "line_items": [{"qty": 25}],
    })
    # Cleanup only targets A's id
    await server.db.sales_deliveries.delete_one({"id": sj_a})
    await release_claimed_allocations(server.db, [{
        "source_po_id": po_id, "source_line_id": line_id,
        "sales_delivery_id": sj_a, "qty": 25,
    }])
    assert await server.db.sales_deliveries.find_one({"id": sj_b}) is not None
    assert int((await _counter(po_id, line_id))["allocated_qty"]) == 25


@pytest.mark.anyio
async def test_race_a_t5_cleanup_release_idempotent():
    po_id, line_id = await _race_a_setup()
    sj = str(uuid.uuid4())
    claim = await claim_source_line_allocation(
        server.db, source_po_id=po_id, source_line_id=line_id, source_line_idx=0,
        ordered_qty=50, qty=10, sales_delivery_id=sj,
    )
    await commit_claimed_allocations(server.db, [claim])
    r1 = await release_claimed_allocations(server.db, [claim])
    r2 = await release_claimed_allocations(server.db, [claim])
    assert r1[0].get("released") is True
    assert r2[0].get("replayed") is True
    assert int((await _counter(po_id, line_id)).get("allocated_qty") or 0) == 0


@pytest.mark.anyio
async def test_race_a_t6_partial_commit_fail_closed():
    """One claim committed then second missing → raise; not create success."""
    po_id, line_id = await _race_a_setup()
    line2 = f"{po_id}_l1"
    sj = str(uuid.uuid4())
    c1 = await claim_source_line_allocation(
        server.db, source_po_id=po_id, source_line_id=line_id, source_line_idx=0,
        ordered_qty=50, qty=5, sales_delivery_id=sj,
    )
    with pytest.raises(HTTPException):
        await commit_claimed_allocations(
            server.db,
            [
                c1,
                {
                    "source_po_id": po_id,
                    "source_line_id": line2,
                    "sales_delivery_id": sj,
                    "lease_token": "missing",
                    "qty": 5,
                },
            ],
        )
    # First claim may already be committed; create path must compensate — simulate.
    await release_claimed_allocations(server.db, [c1])
    assert int((await _counter(po_id, line_id)).get("allocated_qty") or 0) == 0


@pytest.mark.anyio
async def test_race_a_t7_pending_sj_not_active_until_commit():
    """T7: Provisional SJ must not count as active allocation."""
    po_id, line_id = await _race_a_setup()
    sj = str(uuid.uuid4())
    await claim_source_line_allocation(
        server.db, source_po_id=po_id, source_line_id=line_id, source_line_idx=0,
        ordered_qty=100, qty=40, sales_delivery_id=sj,
    )
    await server.db.sales_deliveries.insert_one({
        "id": sj, "po_id": po_id, "is_cancelled": False,
        ALLOCATION_COMMIT_PENDING_FIELD: True,
        "line_items": [{"line_id": line_id, "qty": 40}],
    })
    from services.sales_delivery_allocations import active_deliveries_query_for_po
    active = await server.db.sales_deliveries.find_one(
        active_deliveries_query_for_po(po_id), {"_id": 0, "id": 1},
    )
    assert active is None
    # Simulate compensation failure leaving pending — still not active.
    assert await server.db.sales_deliveries.find_one({"id": sj}) is not None


@pytest.mark.anyio
async def test_race_a_t8_race_b_insert_then_commit_still_safe():
    po_id, line_id = await _race_a_setup()
    sj = str(uuid.uuid4())
    claim = await claim_source_line_allocation(
        server.db, source_po_id=po_id, source_line_id=line_id, source_line_idx=0,
        ordered_qty=70, qty=15, sales_delivery_id=sj,
    )
    await server.db.sales_deliveries.insert_one({
        "id": sj, "po_id": po_id, "is_cancelled": False,
        ALLOCATION_COMMIT_PENDING_FIELD: True, "line_items": [],
    })
    await _expire_reservation(po_id, line_id, sj)
    healed = await heal_expired_reservations_for_line(
        server.db, source_po_id=po_id, source_line_id=line_id,
    )
    assert any(h.get("action") == "promoted_committed" for h in healed)
    doc = await server.db.sales_deliveries.find_one({"id": sj}, {"_id": 0})
    assert doc.get(ALLOCATION_COMMIT_PENDING_FIELD) is not True
    assert int((await _counter(po_id, line_id))["allocated_qty"]) == 15
    # Explicit commit path still idempotent
    again = await commit_claimed_allocations(server.db, [claim])
    assert again[0].get("committed") is True


@pytest.mark.anyio
async def test_race_a_t9_healer_does_not_release_committed():
    po_id, line_id = await _race_a_setup()
    sj = str(uuid.uuid4())
    claim = await claim_source_line_allocation(
        server.db, source_po_id=po_id, source_line_id=line_id, source_line_idx=0,
        ordered_qty=40, qty=8, sales_delivery_id=sj,
    )
    await commit_claimed_allocations(server.db, [claim])
    await _expire_reservation(po_id, line_id, sj)  # lease field irrelevant once committed
    healed = await heal_expired_reservations_for_line(
        server.db, source_po_id=po_id, source_line_id=line_id,
    )
    assert healed == []
    assert (await _counter(po_id, line_id))["claims_by_delivery"][sj]["state"] == CLAIM_STATE_COMMITTED
    assert int((await _counter(po_id, line_id))["allocated_qty"]) == 8


@pytest.mark.anyio
async def test_race_a_t12_rs_txn_abort_on_ownership_lost():
    """T12: claim commit inside session must abort — no durable loser SJ."""
    from services.sales_delivery_allocations import mongo_transactions_available
    if not await mongo_transactions_available(server.db):
        pytest.skip("Mongo transactions unavailable (standalone)")
    po_id, line_id = await _race_a_setup()
    sj_a, sj_b = str(uuid.uuid4()), str(uuid.uuid4())
    claim_a = await claim_source_line_allocation(
        server.db, source_po_id=po_id, source_line_id=line_id, source_line_idx=0,
        ordered_qty=100, qty=40, sales_delivery_id=sj_a,
    )
    await require_reserved_allocations(server.db, [claim_a])
    await _expire_reservation(po_id, line_id, sj_a)
    await heal_expired_reservations_for_line(
        server.db, source_po_id=po_id, source_line_id=line_id,
    )
    claim_b = await claim_source_line_allocation(
        server.db, source_po_id=po_id, source_line_id=line_id, source_line_idx=0,
        ordered_qty=100, qty=40, sales_delivery_id=sj_b,
    )
    await server.db.sales_deliveries.insert_one({
        "id": sj_b, "po_id": po_id, "is_cancelled": False,
        ALLOCATION_COMMIT_PENDING_FIELD: True, "line_items": [{"qty": 40}],
    })
    await commit_claimed_allocations(server.db, [claim_b])
    await activate_delivery_allocation_commit(server.db, sj_b)

    client = server.db.client
    async with await client.start_session() as session:
        session.start_transaction()
        try:
            await server.db.sales_deliveries.insert_one(
                {
                    "id": sj_a, "po_id": po_id, "is_cancelled": False,
                    ALLOCATION_COMMIT_PENDING_FIELD: True, "line_items": [{"qty": 40}],
                },
                session=session,
            )
            await commit_claimed_allocations(server.db, [claim_a], session=session)
            await session.commit_transaction()
            pytest.fail("expected ownership-lost abort")
        except HTTPException:
            if session.in_transaction:
                await session.abort_transaction()

    assert await server.db.sales_deliveries.find_one({"id": sj_a}) is None
    assert await server.db.sales_deliveries.find_one({"id": sj_b}) is not None
    assert int((await _counter(po_id, line_id))["allocated_qty"]) == 40


# ── Pending visibility + orphan recovery ─────────────────────────────────────


async def _insert_pending_sj(*, po_id: str, sj: str, qty: int = 40, with_mirror: bool = False):
    line_id = f"{po_id}_l0"
    await server.db.sales_deliveries.insert_one({
        "id": sj,
        "po_id": po_id,
        "sj_number": f"SJ-P-{sj[:8]}",
        "is_cancelled": False,
        ALLOCATION_COMMIT_PENDING_FIELD: True,
        "line_items": [{"line_id": line_id, "line_idx": 0, "qty": qty, "name": "X"}],
        "total_qty": qty,
        "status": "SEBAGIAN",
        "created_at": "2026-01-01T00:00:00Z",
        "source_po_ids": [po_id],
    })
    if with_mirror:
        await server.db.orders.update_one(
            {"id": po_id},
            {"$setOnInsert": {
                "id": po_id, "po_number": "PO-P", "cancelled": False,
                "line_items": [{"line_id": line_id, "name": "X", "quantity": 100}],
                "loading_confirmations": [], "delivery_confirmations": [],
                "surat_jalan_files": [],
            }},
            upsert=True,
        )
        await server.db.orders.update_one(
            {"id": po_id},
            {"$push": {"shipments": {
                "id": sj, "sales_delivery_id": sj, "number": f"SJ-P-{sj[:8]}",
                "total_qty": qty, ALLOCATION_COMMIT_PENDING_FIELD: True,
                "lines": [{"line_idx": 0, "line_id": line_id, "qty": qty, "name": "X"}],
            }}},
        )
    return line_id


@pytest.mark.anyio
async def test_pending_t1_excluded_from_list():
    po_id = str(uuid.uuid4())
    sj_pending = str(uuid.uuid4())
    sj_ok = str(uuid.uuid4())
    await _insert_pending_sj(po_id=po_id, sj=sj_pending)
    await server.db.sales_deliveries.insert_one({
        "id": sj_ok, "po_id": po_id, "sj_number": "SJ-OK", "is_cancelled": False,
        "line_items": [{"qty": 1}], "created_at": "2026-01-02T00:00:00Z", "status": "SEBAGIAN",
    })
    await server.db.orders.update_one(
        {"id": po_id},
        {"$setOnInsert": {
            "id": po_id, "cancelled": False,
            "line_items": [{"quantity": 100, "name": "X"}], "shipments": [],
        }},
        upsert=True,
    )
    listed = await list_sales_deliveries(server.db, po_id=po_id, page=1, page_size=50)
    ids = {d["id"] for d in listed.get("deliveries") or []}
    assert sj_pending not in ids
    assert sj_ok in ids


@pytest.mark.anyio
async def test_pending_t2_detail_rejected():
    po_id = str(uuid.uuid4())
    sj = str(uuid.uuid4())
    await _insert_pending_sj(po_id=po_id, sj=sj)
    with pytest.raises(HTTPException) as exc:
        await get_sales_delivery_detail(server.db, sj)
    assert exc.value.status_code in (404, 409)


@pytest.mark.anyio
async def test_pending_t3_pdf_rejected():
    po_id = str(uuid.uuid4())
    sj = str(uuid.uuid4())
    await _insert_pending_sj(po_id=po_id, sj=sj)
    order = {"id": po_id, "line_items": []}
    delivery = await server.db.sales_deliveries.find_one({"id": sj}, {"_id": 0})
    entry, err = await ensure_generated_sj_pdf(server.db, order, delivery, _admin())
    assert entry is None
    assert err == "allocation_pending_or_unavailable"


@pytest.mark.anyio
async def test_pending_t4_t5_t6_loading_and_pod_gate():
    po_id = str(uuid.uuid4())
    sj = str(uuid.uuid4())
    await _insert_pending_sj(po_id=po_id, sj=sj, with_mirror=True)
    with pytest.raises(HTTPException) as exc:
        await require_operational_sales_delivery(server.db, sj, attempt_reconcile=False)
    assert exc.value.status_code == 409
    # Mirror with pending flag is inactive for quantity_rules
    from quantity_rules import shipment_is_active, resolve_loading_write_sj_identity
    order = await server.db.orders.find_one({"id": po_id}, {"_id": 0})
    sh = (order.get("shipments") or [])[0]
    assert shipment_is_active(sh) is False
    # Without SJ file, identity fails; add file still blocked by DB gate above.
    order["surat_jalan_files"] = [{
        "id": "f1", "shipment_id": sj, "surat_jalan_id": sj,
        "sj_number": sh["number"], "lines": [{"line_idx": 0, "qty": 40}],
    }]
    # Clear mirror pending to simulate old crash window without flag — DB gate still holds.
    sh.pop(ALLOCATION_COMMIT_PENDING_FIELD, None)
    identity, err = resolve_loading_write_sj_identity(order, sj)
    # Identity may resolve; operational require still fails.
    with pytest.raises(HTTPException):
        await require_operational_sales_delivery(server.db, sj, attempt_reconcile=False)


@pytest.mark.anyio
async def test_pending_t7_t11_reconcile_no_claim_idempotent():
    po_id = str(uuid.uuid4())
    sj = str(uuid.uuid4())
    await _insert_pending_sj(po_id=po_id, sj=sj, with_mirror=True)
    r1 = await reconcile_pending_orphan_delivery(server.db, sj)
    assert r1.get("reconciled") is True
    assert r1.get("action") == "deleted_orphan"
    assert await server.db.sales_deliveries.find_one({"id": sj}) is None
    order = await server.db.orders.find_one({"id": po_id}, {"_id": 0})
    assert not any(
        (s.get("id") == sj or s.get("sales_delivery_id") == sj)
        for s in (order.get("shipments") or [])
    )
    r2 = await reconcile_pending_orphan_delivery(server.db, sj)
    assert r2.get("replayed") is True or r2.get("action") == "already_absent"


@pytest.mark.anyio
async def test_pending_t8_active_lease_not_reconciled():
    po_id = str(uuid.uuid4())
    sj = str(uuid.uuid4())
    line_id = await _insert_pending_sj(po_id=po_id, sj=sj)
    await claim_source_line_allocation(
        server.db, source_po_id=po_id, source_line_id=line_id, source_line_idx=0,
        ordered_qty=100, qty=40, sales_delivery_id=sj,
    )
    r = await reconcile_pending_orphan_delivery(server.db, sj)
    assert r.get("reconciled") is False
    assert r.get("reason") == "recovery_required"
    assert r.get("detail") == "active_lease"
    doc = await server.db.sales_deliveries.find_one({"id": sj}, {"_id": 0})
    assert doc.get(ALLOCATION_COMMIT_PENDING_FIELD) is True
    assert doc.get(RECOVERY_FENCE_FIELD) is not True
    assert int((await _counter(po_id, line_id))["allocated_qty"]) == 40


@pytest.mark.anyio
async def test_pending_t9_committed_claim_activates_not_deleted():
    po_id = str(uuid.uuid4())
    sj = str(uuid.uuid4())
    line_id = await _insert_pending_sj(po_id=po_id, sj=sj)
    claim = await claim_source_line_allocation(
        server.db, source_po_id=po_id, source_line_id=line_id, source_line_idx=0,
        ordered_qty=100, qty=40, sales_delivery_id=sj,
    )
    await commit_claimed_allocations(server.db, [claim])
    # Stuck pending after commit — reconcile must activate.
    r = await reconcile_pending_orphan_delivery(server.db, sj)
    assert r.get("action") == "activated_committed_claim"
    doc = await server.db.sales_deliveries.find_one({"id": sj}, {"_id": 0})
    assert doc.get(ALLOCATION_COMMIT_PENDING_FIELD) is not True
    assert int((await _counter(po_id, line_id))["allocated_qty"]) == 40


@pytest.mark.anyio
async def test_pending_t10_parallel_recovery_one_winner():
    po_id = str(uuid.uuid4())
    sj = str(uuid.uuid4())
    await _insert_pending_sj(po_id=po_id, sj=sj, with_mirror=True)
    results = await asyncio.gather(
        reconcile_pending_orphan_delivery(server.db, sj),
        reconcile_pending_orphan_delivery(server.db, sj),
    )
    wins = [r for r in results if r.get("action") == "deleted_orphan"]
    assert len(wins) == 1
    assert await server.db.sales_deliveries.find_one({"id": sj}) is None


@pytest.mark.anyio
async def test_pending_t12_t13_recovery_does_not_touch_other_and_blocks_activate():
    po_id = str(uuid.uuid4())
    sj_a = str(uuid.uuid4())
    sj_b = str(uuid.uuid4())
    line_id = f"{po_id}_l0"
    await _insert_pending_sj(po_id=po_id, sj=sj_a, with_mirror=True)
    claim_b = await claim_source_line_allocation(
        server.db, source_po_id=po_id, source_line_id=line_id, source_line_idx=0,
        ordered_qty=100, qty=25, sales_delivery_id=sj_b,
    )
    await commit_claimed_allocations(server.db, [claim_b])
    await server.db.sales_deliveries.insert_one({
        "id": sj_b, "po_id": po_id, "is_cancelled": False, "line_items": [{"qty": 25}],
    })
    # Mid-recovery fence blocks late activate by crashed create worker.
    await server.db.sales_deliveries.update_one(
        {"id": sj_a},
        {"$set": {RECOVERY_FENCE_FIELD: True, "recovery_token": "t"}},
    )
    with pytest.raises(HTTPException) as exc:
        await activate_delivery_allocation_commit(server.db, sj_a)
    assert exc.value.status_code == 409
    # Finish cleanup with matching token (simulates same recovery worker).
    await server.db.sales_deliveries.delete_one({
        "id": sj_a, RECOVERY_FENCE_FIELD: True, "recovery_token": "t",
    })
    await server.db.orders.update_one(
        {"id": po_id}, {"$pull": {"shipments": {"id": sj_a}}},
    )
    assert await server.db.sales_deliveries.find_one({"id": sj_a}) is None
    assert await server.db.sales_deliveries.find_one({"id": sj_b}) is not None
    assert int((await _counter(po_id, line_id))["allocated_qty"]) == 25


@pytest.mark.anyio
async def test_pending_t16_legacy_without_pending_flag_valid():
    po_id = str(uuid.uuid4())
    sj = str(uuid.uuid4())
    await server.db.sales_deliveries.insert_one({
        "id": sj, "po_id": po_id, "sj_number": "LEGACY", "is_cancelled": False,
        "line_items": [{"qty": 5}], "created_at": "2026-01-01T00:00:00Z",
    })
    await server.db.orders.update_one(
        {"id": po_id},
        {"$setOnInsert": {
            "id": po_id, "cancelled": False,
            "line_items": [{"quantity": 5, "name": "X"}], "shipments": [],
        }},
        upsert=True,
    )
    doc = await require_operational_sales_delivery(server.db, sj)
    assert doc["id"] == sj
    listed = await list_sales_deliveries(server.db, po_id=po_id)
    assert sj in {d["id"] for d in listed.get("deliveries") or []}
