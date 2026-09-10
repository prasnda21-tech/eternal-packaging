"""Deleted/cancelled SJ must not keep Surat Jalan stageflow active."""
import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ.setdefault("MONGO_URL", "mongodb://127.0.0.1:27017")
os.environ.setdefault("DB_NAME", "eternal_packaging_test")
os.environ.setdefault("JWT_SECRET", "test-secret-key-12345678901234567890")
os.environ.setdefault("CORS_ORIGINS", "*")

import server
from quantity_rules import (
    eligible_delivery_qty,
    qty_packed,
    qty_shipped,
    surat_jalan_created,
    surat_jalan_status_semantic,
)
from server import app, current_user
from services.workflow_stage_status import resolve_stage_chip


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture(autouse=True)
async def bind_motor():
    from motor.motor_asyncio import AsyncIOMotorClient

    server.client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    server.db = server.client[os.environ["DB_NAME"]]
    yield


def _admin():
    app.dependency_overrides[current_user] = lambda: {
        "role": "admin",
        "name": "Admin",
        "email": "admin@eternalpackaging.com",
        "id": "admin-sj-stage",
    }


async def _seed_packed_order(*, qty: int = 3000, lines=None, prefix: str = "SJSTAGE"):
    now = datetime.now(timezone.utc).isoformat()
    order_id = str(uuid.uuid4())
    po_number = f"{prefix}-{uuid.uuid4().hex[:6].upper()}"
    if lines is None:
        lines = [{"line_idx": 0, "line_id": "l0", "name": "Cup A", "quantity": qty, "unit": "pcs"}]
    packing_lines = [
        {"line_idx": i, "line_id": li.get("line_id"), "packed_qty": int(li.get("quantity") or 0)}
        for i, li in enumerate(lines)
    ]
    total = sum(int(li.get("quantity") or 0) for li in lines)
    doc = {
        "id": order_id,
        "po_number": po_number,
        "customer": "PT SJ Stage",
        "outlet": "Outlet",
        "recipient": "Budi",
        "address": "Jl. Test",
        "sales": "Sales A",
        "order_date": "2026-09-10",
        "current_stage": 12,
        "status_label": "Surat Jalan",
        "design_status": "Approved",
        "payment_status": "Lunas",
        "line_items": lines,
        "total_quantity": total,
        "packing_info": {
            "qty_packed": total,
            "lines": packing_lines,
            "confirmed_at": now,
        },
        "operator_splits": [],
        "delivery_status": "Belum Terkirim",
        "shipments": [],
        "loading_confirmations": [],
        "delivery_proofs": [],
        "surat_jalan_files": [],
        "timeline": [],
        "created_at": now,
        "updated_at": now,
    }
    await server.db.orders.insert_one(doc)
    return doc


async def _create_sj(client, po_id, *, line_idx=0, qty=3000, line_id=None):
    item = {"line_idx": line_idx, "qty": qty}
    if line_id is not None:
        item["line_id"] = line_id
    res = await client.post(
        "/api/sales-deliveries",
        json={"po_id": po_id, "delivery_date": "2026-09-10", "line_items": [item]},
    )
    assert res.status_code == 200, res.text
    return res.json().get("delivery") or res.json()


def _chip(order: dict, idx: int):
    return resolve_stage_chip(idx, order, int(order.get("current_stage") or 0))


def _order_body(res) -> dict:
    data = res.json()
    return data.get("order") or data


@pytest.mark.anyio
async def test_create_active_sj_stage_behaves_normally():
    order = await _seed_packed_order(qty=3000)
    _admin()
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            await _create_sj(client, order["id"], qty=3000)
            got = await client.get(f"/api/orders/{order['id']}")
            assert got.status_code == 200
            body = _order_body(got)
            assert body["surat_jalan_created"] is True
            assert body["surat_jalan_status_semantic"] in ("partial", "completed")
            assert qty_shipped(body) == 3000
            sj_chip = _chip(body, 12)
            assert sj_chip["state"] in ("current", "done")
            assert sj_chip["state"] != "pending"
    finally:
        app.dependency_overrides.clear()


@pytest.mark.anyio
async def test_delete_active_sj_clears_stageflow_and_restores_remain():
    order = await _seed_packed_order(qty=3000)
    packed_before = qty_packed(order)
    _admin()
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            sj = await _create_sj(client, order["id"], qty=3000)
            sj_id = sj["id"]
            mid = await client.get(f"/api/orders/{order['id']}")
            mid_body = _order_body(mid)
            assert mid_body["surat_jalan_created"] is True
            assert mid_body.get("delivery_note_number")
            assert qty_shipped(mid_body) == 3000
            assert eligible_delivery_qty(mid_body) == 0

            preview_before = await client.get(f"/api/sales-deliveries/po/{order['id']}/summary")
            if preview_before.status_code == 200:
                remain_before = preview_before.json().get("total_remaining")
                assert remain_before == 0

            deleted = await client.delete(f"/api/sales-deliveries/{sj_id}")
            assert deleted.status_code == 200

            fresh_db = await server.db.orders.find_one({"id": order["id"]}, {"_id": 0})
            assert fresh_db.get("shipments") in ([], None)
            assert not fresh_db.get("delivery_note_number")
            assert not fresh_db.get("delivery_note_created_at")
            assert not fresh_db.get("delivery_note_created_by")
            assert qty_packed(fresh_db) == packed_before
            assert qty_shipped(fresh_db) == 0
            assert surat_jalan_created(fresh_db) is False
            assert surat_jalan_status_semantic(fresh_db) == "pending"
            assert eligible_delivery_qty(fresh_db) == 0

            soft = await server.db.files.find_one({"id": sj_id, "kind": "surat_jalan"})
            if soft is not None:
                assert soft.get("is_deleted") is True

            assert await server.db.sales_deliveries.find_one({"id": sj_id}) is None

            after = await client.get(f"/api/orders/{order['id']}")
            body = _order_body(after)
            assert body["surat_jalan_created"] is False
            assert body["surat_jalan_status_semantic"] == "pending"
            assert qty_packed(body) == packed_before
            assert eligible_delivery_qty(body) == 0
            sj_chip = _chip(body, 12)
            assert sj_chip["state"] != "done"
            assert "Menunggu" in (sj_chip.get("label") or "")
            loading_chip = _chip(body, 13)
            assert loading_chip["state"] == "pending"
            pengiriman_chip = _chip(body, 14)
            assert pengiriman_chip["state"] == "pending"
            assert pengiriman_chip["state"] != "current"

            preview_after = await client.get(f"/api/sales-deliveries/po/{order['id']}/summary")
            if preview_after.status_code == 200:
                assert preview_after.json().get("total_remaining") == 3000

            retry = await client.delete(f"/api/sales-deliveries/{sj_id}")
            assert retry.status_code == 404
            again = await server.db.orders.find_one({"id": order["id"]}, {"_id": 0})
            assert qty_packed(again) == packed_before
            assert qty_shipped(again) == 0
            preview_retry = await client.get(f"/api/sales-deliveries/po/{order['id']}/summary")
            if preview_retry.status_code == 200:
                assert preview_retry.json().get("total_remaining") == 3000
    finally:
        app.dependency_overrides.clear()


@pytest.mark.anyio
async def test_stale_delivery_note_stamps_alone_are_the_bug_signal():
    """Document root cause: leftover delivery_note_* keeps SJ stage active."""
    order = {
        "line_items": [{"name": "A", "quantity": 3000}],
        "packing_info": {"qty_packed": 3000, "lines": [{"line_idx": 0, "packed_qty": 3000}]},
        "shipments": [],
        "surat_jalan_files": [],
        "loading_confirmations": [],
        "delivery_note_number": "SJ/DELETED",
        "delivery_note_created_at": "2026-09-10T00:00:00+00:00",
        "delivery_note_created_by": "admin@eternalpackaging.com",
        "current_stage": 12,
    }
    assert surat_jalan_created(order) is True
    assert surat_jalan_status_semantic(order) == "partial"
    assert _chip(order, 12)["state"] == "current"

    order["delivery_note_number"] = None
    order["delivery_note_created_at"] = None
    order["delivery_note_created_by"] = None
    order["delivery_note_created_by_name"] = None
    assert surat_jalan_created(order) is False
    assert surat_jalan_status_semantic(order) == "pending"
    assert _chip(order, 12)["state"] != "done"


@pytest.mark.anyio
async def test_deleted_sj_then_new_active_sj_drives_stage_without_double_count():
    order = await _seed_packed_order(qty=3000)
    _admin()
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            first = await _create_sj(client, order["id"], qty=3000)
            await client.delete(f"/api/sales-deliveries/{first['id']}")
            second = await _create_sj(client, order["id"], qty=3000)
            got = await client.get(f"/api/orders/{order['id']}")
            body = _order_body(got)
            assert body["surat_jalan_created"] is True
            assert qty_shipped(body) == 3000
            assert len(body.get("shipments") or []) == 1
            assert body["shipments"][0].get("sales_delivery_id") == second["id"]
            preview = await client.get(f"/api/sales-deliveries/po/{order['id']}/summary")
            if preview.status_code == 200:
                assert preview.json().get("total_remaining") == 0
    finally:
        app.dependency_overrides.clear()


@pytest.mark.anyio
async def test_partial_active_plus_deleted_sj_only_active_counts():
    order = await _seed_packed_order(qty=3000)
    _admin()
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            a = await _create_sj(client, order["id"], qty=1000)
            b = await _create_sj(client, order["id"], qty=2000)
            await client.delete(f"/api/sales-deliveries/{a['id']}")
            got = await client.get(f"/api/orders/{order['id']}")
            body = _order_body(got)
            assert body["surat_jalan_created"] is True
            assert qty_shipped(body) == 2000
            assert len(body.get("shipments") or []) == 1
            assert body["shipments"][0].get("sales_delivery_id") == b["id"]
            assert body.get("delivery_note_number")
            preview = await client.get(f"/api/sales-deliveries/po/{order['id']}/summary")
            if preview.status_code == 200:
                assert preview.json().get("total_remaining") == 1000
    finally:
        app.dependency_overrides.clear()


@pytest.mark.anyio
async def test_same_name_lines_independent_on_sj_delete():
    lines = [
        {"line_idx": 0, "line_id": "la", "name": "Cup Same", "quantity": 1000, "unit": "pcs"},
        {"line_idx": 1, "line_id": "lb", "name": "Cup Same", "quantity": 2000, "unit": "pcs"},
    ]
    order = await _seed_packed_order(lines=lines)
    _admin()
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            sj0 = await _create_sj(client, order["id"], line_idx=0, qty=1000, line_id="la")
            sj1 = await _create_sj(client, order["id"], line_idx=1, qty=2000, line_id="lb")
            await client.delete(f"/api/sales-deliveries/{sj0['id']}")
            got = await client.get(f"/api/orders/{order['id']}")
            body = _order_body(got)
            assert qty_shipped(body) == 2000
            ships = body.get("shipments") or []
            assert len(ships) == 1
            assert ships[0].get("sales_delivery_id") == sj1["id"]
            line_qtys = {
                int(ln.get("line_idx")): int(ln.get("qty") or 0)
                for sh in ships
                for ln in (sh.get("lines") or [])
            }
            assert line_qtys.get(0, 0) == 0
            assert line_qtys.get(1, 0) == 2000
    finally:
        app.dependency_overrides.clear()


@pytest.mark.anyio
async def test_delete_blocked_when_loading_exists():
    order = await _seed_packed_order(qty=3000)
    _admin()
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            sj = await _create_sj(client, order["id"], qty=3000)
            await server.db.orders.update_one(
                {"id": order["id"]},
                {"$set": {
                    "loading_confirmations": [{
                        "sj_number": sj["sj_number"],
                        "sales_delivery_id": sj["id"],
                        "lines": [{"line_idx": 0, "qty": 2000}],
                    }],
                }},
            )
            res = await client.delete(f"/api/sales-deliveries/{sj['id']}")
            assert res.status_code == 400
            assert "Loading" in str(res.json()["detail"])
            still = await server.db.sales_deliveries.find_one({"id": sj["id"]})
            assert still is not None
            fresh = await server.db.orders.find_one({"id": order["id"]}, {"_id": 0})
            assert fresh.get("delivery_note_number")
            assert any(
                sh.get("sales_delivery_id") == sj["id"] or sh.get("id") == sj["id"]
                for sh in (fresh.get("shipments") or [])
            )
    finally:
        app.dependency_overrides.clear()


@pytest.mark.anyio
async def test_delete_blocked_when_delivered_exists():
    order = await _seed_packed_order(qty=3000)
    _admin()
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            sj = await _create_sj(client, order["id"], qty=3000)
            await server.db.orders.update_one(
                {"id": order["id"]},
                {"$set": {
                    "loading_confirmations": [{
                        "sj_number": sj["sj_number"],
                        "sales_delivery_id": sj["id"],
                        "lines": [{"line_idx": 0, "qty": 2000}],
                    }],
                    "delivery_proofs": [{"id": "proof-1", "note": "foto"}],
                    "delivery_status": "Terkirim",
                }},
            )
            res = await client.delete(f"/api/sales-deliveries/{sj['id']}")
            assert res.status_code == 400
            assert "Pengiriman" in str(res.json()["detail"]) or "Loading" in str(res.json()["detail"])
            still = await server.db.sales_deliveries.find_one({"id": sj["id"]})
            assert still is not None
    finally:
        app.dependency_overrides.clear()


@pytest.mark.anyio
async def test_deleted_sj_does_not_inflate_eligible_delivery_qty():
    order = await _seed_packed_order(qty=3000)
    _admin()
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            sj = await _create_sj(client, order["id"], qty=3000)
            mid = _order_body(await client.get(f"/api/orders/{order['id']}"))
            assert eligible_delivery_qty(mid) == 0
            await client.delete(f"/api/sales-deliveries/{sj['id']}")
            after = _order_body(await client.get(f"/api/orders/{order['id']}"))
            assert eligible_delivery_qty(after) == 0
            assert after.get("eligible_delivery_qty", 0) == 0
    finally:
        app.dependency_overrides.clear()


@pytest.mark.anyio
async def test_hapus_file_with_shipment_ghost_clears_active_signals_immediately():
    """Hapus File on generated SJ must restore remain + Aksi PO create action."""
    order = await _seed_packed_order(qty=3000)
    packed_before = qty_packed(order)
    _admin()
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            sj = await _create_sj(client, order["id"], qty=3000)
            sj_id = sj["id"]
            mid = _order_body(await client.get(f"/api/orders/{order['id']}"))
            assert mid["surat_jalan_created"] is True
            assert (mid.get("sales_delivery_summary") or {}).get("total_remaining") == 0
            assert (mid.get("sales_delivery_summary") or {}).get("can_create_delivery") is False

            deleted = await client.delete(f"/api/orders/{order['id']}/surat-jalan/files/{sj_id}")
            assert deleted.status_code == 200, deleted.text
            body = _order_body(deleted)
            assert body.get("surat_jalan_files") in ([], None)
            assert body.get("sj_files") in ([], None)
            assert body["surat_jalan_created"] is False
            assert body["surat_jalan_status_semantic"] == "pending"
            assert qty_packed(body) == packed_before
            summary = body.get("sales_delivery_summary") or {}
            assert summary.get("total_remaining") == 3000
            assert summary.get("can_create_delivery") is True
            assert await server.db.sales_deliveries.find_one({"id": sj_id}) is None
            sj_chip = _chip(body, 12)
            assert sj_chip["state"] != "done"
            assert "Menunggu" in (sj_chip.get("label") or "")

            again = _order_body(await client.get(f"/api/orders/{order['id']}"))
            assert again["surat_jalan_created"] is False
            assert (again.get("sales_delivery_summary") or {}).get("total_remaining") == 3000
            assert (again.get("sales_delivery_summary") or {}).get("can_create_delivery") is True
    finally:
        app.dependency_overrides.clear()


@pytest.mark.anyio
async def test_bare_shipment_without_file_is_not_active_sj():
    order = {
        "line_items": [{"name": "A", "quantity": 3000}],
        "packing_info": {"qty_packed": 3000, "lines": [{"line_idx": 0, "packed_qty": 3000}]},
        "shipments": [{"id": "ghost", "sales_delivery_id": "ghost", "lines": [{"line_idx": 0, "qty": 3000}]}],
        "surat_jalan_files": [],
        "delivery_note_number": "SJ-GHOST",
        "delivery_note_created_at": "t",
        "delivery_note_created_by": "u",
        "current_stage": 12,
    }
    assert surat_jalan_created(order) is False
    assert surat_jalan_status_semantic(order) == "pending"
    assert _chip(order, 12)["state"] != "done"
    assert "Menunggu" in (_chip(order, 12).get("label") or "")


@pytest.mark.anyio
async def test_manual_active_file_preserved_when_other_generated_deleted():
    order = await _seed_packed_order(qty=3000)
    _admin()
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            sj = await _create_sj(client, order["id"], qty=1000)
            # Independent manual upload remains active.
            await server.db.orders.update_one(
                {"id": order["id"]},
                {"$push": {"surat_jalan_files": {
                    "id": "manual-keep",
                    "name": "manual.pdf",
                    "storage_path": "p/manual.pdf",
                    "source": "MANUAL_UPLOAD",
                    "uploaded_at": "2026-09-10T00:00:00+00:00",
                    "uploaded_by": "crm@eternalpackaging.com",
                    "sj_number": "SJ-MANUAL",
                    "lines": [{"line_idx": 0, "qty": 500}],
                }}},
            )
            # Generated sales-delivery delete is blocked when manual file shares sj?
            # Use different sj_number — delete generated by sales-delivery id.
            # If blocker trips on any manual file for same sj_number only.
            res = await client.delete(f"/api/sales-deliveries/{sj['id']}")
            if res.status_code == 200:
                body = _order_body(await client.get(f"/api/orders/{order['id']}"))
                assert body["surat_jalan_created"] is True
                ids = [f.get("id") for f in (body.get("surat_jalan_files") or [])]
                assert "manual-keep" in ids
            else:
                # Preserve existing safety if delete blocked by manual attachment rule.
                assert "file" in str(res.json().get("detail") or "").lower() or res.status_code == 400
    finally:
        app.dependency_overrides.clear()
