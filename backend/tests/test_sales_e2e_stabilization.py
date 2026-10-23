"""Focused P1–P3 sales E2E stabilization tests (isolated preview DB).

P1: PO create requires valid Master Produk per goods line.
P2: Hard-delete / bulk-delete rebuild PO projections via sync_order_payment_projections.
P3: CREATE penerimaan always requires finalized invoice + posted invoice journal.
"""
from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ.setdefault("MONGO_URL", "mongodb://127.0.0.1:27017")
os.environ.setdefault("DB_NAME", "eternal_packaging_test")
os.environ.setdefault("JWT_SECRET", "test-secret-key-12345678901234567890")
os.environ.setdefault("CORS_ORIGINS", "*")

import server
from server import app, current_user
from services.cash_bank_master import create_cash_bank
from services.chart_of_accounts import create_account
from services.sales_accounting_settings import SETTINGS_KEY
from services.sales_receipt_posting import INVOICE_NOT_POSTED_MESSAGE
from services.sales_receipts import (
    DELETE_PROJECTION_SYNC_FAILED_CODE,
    DELETE_PROJECTION_SYNC_FAILED_MESSAGE,
    bulk_delete_sales_receipts,
    create_sales_receipt,
    delete_sales_receipt,
    sync_order_payment_projections,
)
from fastapi import HTTPException

ADMIN = {
    "id": "usr-e2e-admin",
    "email": "admin@eternalpackaging.com",
    "name": "Admin E2E",
    "role": "admin",
}


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture(autouse=True)
def bind_motor():
    from motor.motor_asyncio import AsyncIOMotorClient

    server.client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    server.db = server.client[os.environ["DB_NAME"]]


def _admin():
    app.dependency_overrides[current_user] = lambda: ADMIN


async def _seed_master(name: str = "Cup E2E", *, unit_price=None) -> dict:
    mid = f"mst-{uuid.uuid4().hex[:10]}"
    aid = f"aid-{uuid.uuid4().hex[:10]}"
    doc = {
        "id": mid,
        "master_item_id": mid,
        "accurate_item_id": aid,
        "item_code": f"SKU-{uuid.uuid4().hex[:6].upper()}",
        "item_name": name,
        "name": name,
        "unit": "pcs",
        "item_type": "INVENTORY",
        "is_active": True,
    }
    if unit_price is not None:
        doc["unit_price"] = unit_price
    await server.db.master_items.insert_one(dict(doc))
    return doc


async def _coa(prefix: str, name: str, account_type: str) -> dict:
    return await create_account(
        server.db,
        {"code": f"{prefix}{uuid.uuid4().hex[:6]}", "name": name, "account_type": account_type},
        user={"id": "t", "name": "t", "role": "admin"},
    )


async def _configure_settings():
    rec = await _coa("11", "Piutang E2E", "AREC")
    rev = await _coa("41", "Pendapatan E2E", "REVE")
    tax = await _coa("21", "PPN E2E", "OCLY")
    await server.db.app_settings.update_one(
        {"key": SETTINGS_KEY},
        {
            "$set": {
                "key": SETTINGS_KEY,
                "receivable_account_id": rec["id"],
                "revenue_account_id": rev["id"],
                "output_tax_account_id": tax["id"],
            }
        },
        upsert=True,
    )
    return rec, rev, tax


async def _cash_bank(name: str = "BCA E2E") -> dict:
    bank = await _coa("12", name, "BANK")
    return await create_cash_bank(
        server.db,
        {"name": name, "account_id": bank["id"], "active": True},
        user=ADMIN,
    )


async def _seed_po(**extra) -> dict:
    po_id = str(uuid.uuid4())
    doc = {
        "id": po_id,
        "po_number": f"PO/E2E/{uuid.uuid4().hex[:8].upper()}",
        "customer": "PT E2E Stab",
        "outlet": "Pusat",
        "recipient": "Budi",
        "address": "Jl. Test",
        "delivery_zone": "Jakarta",
        "sales": "Vira",
        "line_items": [{"line_idx": 0, "line_id": "l0", "name": "Cup", "quantity": 100, "unit": "pcs"}],
        "total_quantity": 100,
        "current_stage": 4,
        "status_label": "Approval Payment",
        "payment_status": "Waiting",
        "packed_qty": 0,
        "loaded_qty": 0,
        "events": [{"stage": 0, "name": "Order Masuk"}],
        "shipments": [],
        "created_at": "2026-09-01T00:00:00Z",
        "updated_at": "2026-09-01T00:00:00Z",
    }
    doc.update(extra)
    await server.db.orders.insert_one(doc)
    doc.pop("_id", None)
    return doc


# ---------------------------------------------------------------------------
# P1 — Master Produk wajib pada create PO
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_p1_create_with_valid_master_passes():
    master = await _seed_master("Cup Valid")
    _admin()
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            res = await client.post(
                "/api/sales-orders",
                json={
                    "customer": "PT Master OK",
                    "line_items": [{
                        "name": master["item_name"],
                        "quantity": 10,
                        "unit": "pcs",
                        "production_process": "print",
                        "master_item_id": master["id"],
                        "accurate_item_id": master["accurate_item_id"],
                    }],
                },
            )
            assert res.status_code == 200, res.text
            line = res.json()["order"]["line_items"][0]
            assert line["master_item_id"] == master["id"]
            assert line["accurate_item_id"] == master["accurate_item_id"]
    finally:
        app.dependency_overrides.pop(current_user, None)


@pytest.mark.anyio
async def test_p1_create_without_master_rejected():
    _admin()
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            res = await client.post(
                "/api/sales-orders",
                json={
                    "customer": "PT Tanpa Master",
                    "line_items": [{"name": "Ketik Manual", "quantity": 5, "production_process": "print"}],
                },
            )
            assert res.status_code == 400, res.text
            assert "Master Produk wajib" in res.text
            assert "Line item #1" in res.text
            assert await server.db.orders.count_documents({"customer": "PT Tanpa Master"}) == 0
    finally:
        app.dependency_overrides.pop(current_user, None)


@pytest.mark.anyio
async def test_p1_create_fake_master_id_rejected():
    _admin()
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            res = await client.post(
                "/api/sales-orders",
                json={
                    "customer": "PT Fake Master",
                    "line_items": [{
                        "name": "Cup Fake",
                        "quantity": 5,
                        "master_item_id": "mst-does-not-exist",
                        "production_process": "print",
                    }],
                },
            )
            assert res.status_code in (400, 404), res.text
            assert "Line item #1" in res.text
            assert await server.db.orders.count_documents({"customer": "PT Fake Master"}) == 0
    finally:
        app.dependency_overrides.pop(current_user, None)


@pytest.mark.anyio
async def test_p1_inactive_master_rejected():
    master = await _seed_master("Cup Inactive")
    await server.db.master_items.update_one({"id": master["id"]}, {"$set": {"is_active": False}})
    _admin()
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            res = await client.post(
                "/api/sales-orders",
                json={
                    "customer": "PT Inactive Master",
                    "line_items": [{
                        "name": master["item_name"],
                        "quantity": 2,
                        "master_item_id": master["id"],
                        "accurate_item_id": master["accurate_item_id"],
                        "production_process": "print",
                    }],
                },
            )
            assert res.status_code == 400, res.text
            assert "Line item #1" in res.text
            assert "tidak aktif" in res.text.lower() or "tidak tersedia" in res.text.lower()
            assert await server.db.orders.count_documents({"customer": "PT Inactive Master"}) == 0
    finally:
        app.dependency_overrides.pop(current_user, None)


@pytest.mark.anyio
async def test_p1_repeat_order_legacy_lines_without_master_rejected():
    """Repeat Order mapping may carry names without master ids — BE still hard-rejects."""
    _admin()
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            res = await client.post(
                "/api/sales-orders",
                json={
                    "customer": "PT Repeat Legacy",
                    "notes": "repeat from historical PO",
                    "line_items": [
                        {
                            "name": "Legacy Cup Repeat",
                            "quantity": 100,
                            "unit": "pcs",
                            "production_process": "print",
                            "master_item_id": None,
                            "accurate_item_id": None,
                        },
                        {
                            "name": "Legacy Lid Repeat",
                            "quantity": 100,
                            "unit": "pcs",
                            "production_process": "non_print",
                            "master_item_id": "",
                            "accurate_item_id": "",
                        },
                    ],
                },
            )
            assert res.status_code == 400, res.text
            assert "Master Produk wajib" in res.text
            assert await server.db.orders.count_documents({"customer": "PT Repeat Legacy"}) == 0
    finally:
        app.dependency_overrides.pop(current_user, None)


@pytest.mark.anyio
async def test_p1_mixed_valid_invalid_rejects_entire_transaction():
    good = await _seed_master("Cup Good")
    _admin()
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            res = await client.post(
                "/api/sales-orders",
                json={
                    "customer": "PT Mixed Lines",
                    "line_items": [
                        {
                            "name": good["item_name"],
                            "quantity": 5,
                            "master_item_id": good["id"],
                            "accurate_item_id": good["accurate_item_id"],
                            "production_process": "print",
                        },
                        {
                            "name": "Tanpa Master",
                            "quantity": 5,
                            "production_process": "print",
                        },
                    ],
                },
            )
            assert res.status_code == 400, res.text
            assert "Line item #2" in res.text
            assert await server.db.orders.count_documents({"customer": "PT Mixed Lines"}) == 0
    finally:
        app.dependency_overrides.pop(current_user, None)


@pytest.mark.anyio
async def test_p1_empty_price_master_allowed():
    master = await _seed_master("Cup No Price")
    # Explicit empty / missing catalog price — create must still accept.
    await server.db.master_items.update_one({"id": master["id"]}, {"$unset": {"unit_price": ""}})
    _admin()
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            res = await client.post(
                "/api/sales-orders",
                json={
                    "customer": "PT Empty Price",
                    "line_items": [{
                        "name": master["item_name"],
                        "quantity": 3,
                        "master_item_id": master["id"],
                        "unit_price": None,
                        "production_process": "print",
                    }],
                },
            )
            assert res.status_code == 200, res.text
    finally:
        app.dependency_overrides.pop(current_user, None)


@pytest.mark.anyio
async def test_p1_legacy_po_color_metadata_update_not_blocked():
    """Historical PO without master ids: color metadata update remains allowed."""
    po = await _seed_po(
        line_items=[{
            "line_idx": 0,
            "line_id": "legacy-l0",
            "name": "Legacy Cup",
            "quantity": 50,
            "unit": "pcs",
            "color": "Merah",
            "production_process": "print",
            # intentionally no master_item_id / accurate_item_id
        }],
        current_stage=0,
        status_label="Order Masuk",
        payment_status="Waiting",
    )
    _admin()
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            res = await client.patch(
                f"/api/sales-orders/{po['id']}/lines/0/color",
                json={"color": "Biru", "line_id": "legacy-l0"},
            )
            assert res.status_code == 200, res.text
            stored = await server.db.orders.find_one({"id": po["id"]}, {"_id": 0})
            assert stored["line_items"][0]["color"] == "Biru"
            assert not stored["line_items"][0].get("master_item_id")
    finally:
        app.dependency_overrides.pop(current_user, None)


@pytest.mark.anyio
async def test_p1_legacy_po_product_replace_without_master_rejected():
    po = await _seed_po(
        line_items=[{
            "line_idx": 0,
            "line_id": "legacy-l0",
            "name": "Legacy Cup",
            "quantity": 50,
            "unit": "pcs",
            "production_process": "print",
        }],
        current_stage=0,
        status_label="Order Masuk",
    )
    _admin()
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            res = await client.patch(
                f"/api/sales-orders/{po['id']}/lines/0/product",
                json={"master_item_id": "mst-missing-xyz"},
            )
            assert res.status_code in (400, 404), res.text
            stored = await server.db.orders.find_one({"id": po["id"]}, {"_id": 0})
            assert stored["line_items"][0]["name"] == "Legacy Cup"
    finally:
        app.dependency_overrides.pop(current_user, None)


# ---------------------------------------------------------------------------
# P2 — Hard-delete uses sync_order_payment_projections
# ---------------------------------------------------------------------------


async def _posted_invoice(client, po_id: str, *, unit_price: int = 1_000_000) -> dict:
    res = await client.post(
        "/api/sales-invoices",
        json={
            "po_id": po_id,
            "invoice_date": "2026-09-01",
            "discount_amount": 0,
            "ppn_percent": 0,
            "lines": [{"line_idx": 0, "quantity": 1, "unit_price": unit_price}],
        },
    )
    assert res.status_code == 200, res.text
    inv = res.json()["invoice"]
    assert (await client.post(f"/api/sales-invoices/{inv['id']}/finalize")).status_code == 200
    assert (await client.post(f"/api/sales-invoices/{inv['id']}/post-accounting")).status_code == 200
    return inv


@pytest.mark.anyio
async def test_p2_hard_delete_sole_receipt_rebuilds_projections():
    await _configure_settings()
    bank = await _cash_bank()
    _admin()
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            po = await _seed_po()
            inv = await _posted_invoice(client, po["id"])
            rec = await create_sales_receipt(
                server.db,
                {"invoice_id": inv["id"], "amount": 200_000, "cash_bank_id": bank["id"], "receipt_date": "2026-09-05"},
                ADMIN,
            )
            order = await server.db.orders.find_one({"id": po["id"]}, {"_id": 0})
            assert order.get("payment_fact", {}).get("receipt_id") == rec["id"]
            assert any(r.get("receipt_id") == rec["id"] for r in (order.get("payment_receipts") or []))

            deleted = await delete_sales_receipt(server.db, rec["id"], ADMIN)
            assert deleted["deleted"] is True
            assert await server.db.sales_receipts.find_one({"id": rec["id"]}) is None

            after = await server.db.orders.find_one({"id": po["id"]}, {"_id": 0})
            assert after.get("payment_fact") is None
            assert after.get("payment_receipts") == []
            assert after.get("payment_status") == "Waiting"
            inv_after = await server.db.sales_invoices.find_one({"id": inv["id"]}, {"_id": 0})
            assert float(inv_after.get("paid_total") or 0) == 0.0
            assert float(inv_after.get("outstanding_amount") or 0) == float(inv_after.get("grand_total") or 0)
    finally:
        app.dependency_overrides.pop(current_user, None)


@pytest.mark.anyio
async def test_p2_hard_delete_keeps_sibling_effective():
    await _configure_settings()
    bank = await _cash_bank()
    _admin()
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            po = await _seed_po()
            inv = await _posted_invoice(client, po["id"], unit_price=1_000_000)
            r1 = await create_sales_receipt(
                server.db,
                {"invoice_id": inv["id"], "amount": 300_000, "cash_bank_id": bank["id"], "receipt_date": "2026-09-05"},
                ADMIN,
            )
            r2 = await create_sales_receipt(
                server.db,
                {"invoice_id": inv["id"], "amount": 200_000, "cash_bank_id": bank["id"], "receipt_date": "2026-09-06"},
                ADMIN,
            )
            await delete_sales_receipt(server.db, r1["id"], ADMIN)
            after = await server.db.orders.find_one({"id": po["id"]}, {"_id": 0})
            ids = {r.get("receipt_id") for r in (after.get("payment_receipts") or [])}
            assert r1["id"] not in ids
            assert r2["id"] in ids
            assert after.get("payment_fact", {}).get("receipt_id") == r2["id"]
            assert await server.db.sales_receipts.find_one({"id": r2["id"]}) is not None
    finally:
        app.dependency_overrides.pop(current_user, None)


@pytest.mark.anyio
async def test_p2_bulk_delete_multi_po_sync_once_each():
    await _configure_settings()
    bank = await _cash_bank()
    sync_calls = []
    real_sync = sync_order_payment_projections

    async def spy_sync(db, po_id):
        sync_calls.append(str(po_id))
        return await real_sync(db, po_id)

    import services.sales_receipts as receipts_mod

    receipts_mod.sync_order_payment_projections = spy_sync
    _admin()
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            po_a = await _seed_po()
            po_b = await _seed_po()
            inv_a = await _posted_invoice(client, po_a["id"])
            inv_b = await _posted_invoice(client, po_b["id"])
            ra = await create_sales_receipt(
                server.db,
                {"invoice_id": inv_a["id"], "amount": 100_000, "cash_bank_id": bank["id"]},
                ADMIN,
            )
            rb = await create_sales_receipt(
                server.db,
                {"invoice_id": inv_b["id"], "amount": 100_000, "cash_bank_id": bank["id"]},
                ADMIN,
            )
            sync_calls.clear()
            result = await bulk_delete_sales_receipts(server.db, [ra["id"], rb["id"]], ADMIN)
            assert result["deleted_count"] == 2
            assert sorted(sync_calls) == sorted([po_a["id"], po_b["id"]])
            assert len(sync_calls) == 2
            for po_id in (po_a["id"], po_b["id"]):
                order = await server.db.orders.find_one({"id": po_id}, {"_id": 0})
                assert order.get("payment_receipts") == []
                assert order.get("payment_fact") is None
    finally:
        receipts_mod.sync_order_payment_projections = real_sync
        app.dependency_overrides.pop(current_user, None)


@pytest.mark.anyio
async def test_p2_hard_delete_idempotent_second_call_404():
    await _configure_settings()
    bank = await _cash_bank()
    _admin()
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            po = await _seed_po()
            inv = await _posted_invoice(client, po["id"])
            rec = await create_sales_receipt(
                server.db,
                {"invoice_id": inv["id"], "amount": 50_000, "cash_bank_id": bank["id"]},
                ADMIN,
            )
            await delete_sales_receipt(server.db, rec["id"], ADMIN)
            with pytest.raises(Exception) as exc:
                await delete_sales_receipt(server.db, rec["id"], ADMIN)
            assert getattr(exc.value, "status_code", None) == 404
    finally:
        app.dependency_overrides.pop(current_user, None)


@pytest.mark.anyio
async def test_p2_unsettled_sibling_excluded_from_projections_after_delete():
    await _configure_settings()
    bank = await _cash_bank()
    _admin()
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            po = await _seed_po()
            inv = await _posted_invoice(client, po["id"])
            settled = await create_sales_receipt(
                server.db,
                {"invoice_id": inv["id"], "amount": 150_000, "cash_bank_id": bank["id"]},
                ADMIN,
            )
            deferred = await create_sales_receipt(
                server.db,
                {"invoice_id": inv["id"], "amount": 80_000, "cash_bank_id": bank["id"]},
                ADMIN,
                defer_operational_settlement=True,
            )
            assert deferred.get("settlement_effective") is False
            await delete_sales_receipt(server.db, settled["id"], ADMIN)
            after = await server.db.orders.find_one({"id": po["id"]}, {"_id": 0})
            ids = {r.get("receipt_id") for r in (after.get("payment_receipts") or [])}
            assert settled["id"] not in ids
            assert deferred["id"] not in ids
            assert after.get("payment_fact") is None
            assert await server.db.sales_receipts.find_one({"id": deferred["id"]}) is not None
    finally:
        app.dependency_overrides.pop(current_user, None)


@pytest.mark.anyio
async def test_p2_sync_failure_after_delete_is_not_false_success():
    """Physical delete + sync failure → structured 409, not deleted:True success."""
    await _configure_settings()
    bank = await _cash_bank()
    import services.sales_receipts as receipts_mod

    real_sync = receipts_mod.sync_order_payment_projections

    async def boom_sync(db, po_id):
        raise RuntimeError("forced sync failure")

    receipts_mod.sync_order_payment_projections = boom_sync
    _admin()
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            po = await _seed_po()
            inv = await _posted_invoice(client, po["id"])
            rec = await create_sales_receipt(
                server.db,
                {"invoice_id": inv["id"], "amount": 90_000, "cash_bank_id": bank["id"]},
                ADMIN,
            )
        with pytest.raises(HTTPException) as exc:
            await delete_sales_receipt(server.db, rec["id"], ADMIN)
        assert exc.value.status_code == 409
        detail = exc.value.detail
        assert isinstance(detail, dict)
        assert detail["code"] == DELETE_PROJECTION_SYNC_FAILED_CODE
        assert detail["deleted"] is True
        assert detail["message"] == DELETE_PROJECTION_SYNC_FAILED_MESSAGE
        assert rec["id"] in {r["id"] for r in detail["deleted_receipts"]}
        assert po["id"] in detail["po_ids"]
        # Receipt is gone — retry delete must 404 (no re-delete).
        assert await server.db.sales_receipts.find_one({"id": rec["id"]}) is None
        receipts_mod.sync_order_payment_projections = real_sync
        with pytest.raises(HTTPException) as retry:
            await delete_sales_receipt(server.db, rec["id"], ADMIN)
        assert retry.value.status_code == 404
        # Safe reconcile without re-delete.
        await sync_order_payment_projections(server.db, po["id"])
        after = await server.db.orders.find_one({"id": po["id"]}, {"_id": 0})
        assert after.get("payment_fact") is None
        assert after.get("payment_receipts") == []
    finally:
        receipts_mod.sync_order_payment_projections = real_sync
        app.dependency_overrides.pop(current_user, None)


@pytest.mark.anyio
async def test_p2_bulk_sync_failure_surfaces_partial_failure():
    await _configure_settings()
    bank = await _cash_bank()
    import services.sales_receipts as receipts_mod

    real_sync = receipts_mod.sync_order_payment_projections

    async def boom_sync(db, po_id):
        raise RuntimeError("bulk sync boom")

    receipts_mod.sync_order_payment_projections = boom_sync
    _admin()
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            po = await _seed_po()
            inv = await _posted_invoice(client, po["id"])
            r1 = await create_sales_receipt(
                server.db,
                {"invoice_id": inv["id"], "amount": 40_000, "cash_bank_id": bank["id"]},
                ADMIN,
            )
            r2 = await create_sales_receipt(
                server.db,
                {"invoice_id": inv["id"], "amount": 30_000, "cash_bank_id": bank["id"]},
                ADMIN,
            )
        with pytest.raises(HTTPException) as exc:
            await bulk_delete_sales_receipts(server.db, [r1["id"], r2["id"]], ADMIN)
        assert exc.value.status_code == 409
        detail = exc.value.detail
        assert detail["code"] == DELETE_PROJECTION_SYNC_FAILED_CODE
        assert detail["deleted"] is True
        assert {r["id"] for r in detail["deleted_receipts"]} == {r1["id"], r2["id"]}
        assert await server.db.sales_receipts.count_documents({"id": {"$in": [r1["id"], r2["id"]]}}) == 0
    finally:
        receipts_mod.sync_order_payment_projections = real_sync
        app.dependency_overrides.pop(current_user, None)


# ---------------------------------------------------------------------------
# P3 — CREATE receipt gate always on
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_p3_create_requires_posted_invoice_journal_even_when_flag_off():
    await _configure_settings()
    # Explicitly leave gate flag unset / off.
    await server.db.app_settings.delete_many(
        {"key": {"$in": ["receipt_create_requires_invoice_journal_posted", "auto_sales_receipt_lifecycle_enabled"]}}
    )
    bank = await _cash_bank()
    _admin()
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            po = await _seed_po()
            res = await client.post(
                "/api/sales-invoices",
                json={
                    "po_id": po["id"],
                    "invoice_date": "2026-09-01",
                    "discount_amount": 0,
                    "ppn_percent": 0,
                    "lines": [{"line_idx": 0, "quantity": 1, "unit_price": 500_000}],
                },
            )
            inv = res.json()["invoice"]
            assert (await client.post(f"/api/sales-invoices/{inv['id']}/finalize")).status_code == 200

            blocked = await client.post(
                "/api/sales-receipts",
                json={
                    "invoice_id": inv["id"],
                    "receipt_date": "2026-09-05",
                    "amount": 50_000,
                    "cash_bank_id": bank["id"],
                },
            )
            assert blocked.status_code == 409, blocked.text
            assert INVOICE_NOT_POSTED_MESSAGE in str(blocked.json())
            assert await server.db.sales_receipts.count_documents({"invoice_id": inv["id"]}) == 0

            assert (await client.post(f"/api/sales-invoices/{inv['id']}/post-accounting")).status_code == 200
            ok = await client.post(
                "/api/sales-receipts",
                json={
                    "invoice_id": inv["id"],
                    "receipt_date": "2026-09-05",
                    "amount": 50_000,
                    "cash_bank_id": bank["id"],
                },
            )
            assert ok.status_code == 200, ok.text
    finally:
        app.dependency_overrides.pop(current_user, None)


@pytest.mark.anyio
async def test_p3_draft_invoice_rejected_no_partial_write():
    await _configure_settings()
    bank = await _cash_bank()
    _admin()
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            po = await _seed_po()
            res = await client.post(
                "/api/sales-invoices",
                json={
                    "po_id": po["id"],
                    "invoice_date": "2026-09-01",
                    "lines": [{"line_idx": 0, "quantity": 1, "unit_price": 100_000}],
                },
            )
            inv = res.json()["invoice"]
            blocked = await client.post(
                "/api/sales-receipts",
                json={"invoice_id": inv["id"], "amount": 10_000, "cash_bank_id": bank["id"]},
            )
            assert blocked.status_code == 409, blocked.text
            assert "finalized" in blocked.text.lower() or "Finalized" in blocked.text
            assert await server.db.sales_receipts.count_documents({"invoice_id": inv["id"]}) == 0
    finally:
        app.dependency_overrides.pop(current_user, None)


@pytest.mark.anyio
async def test_p3_reversed_invoice_journal_blocks_create():
    await _configure_settings()
    bank = await _cash_bank()
    _admin()
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            po = await _seed_po()
            inv = await _posted_invoice(client, po["id"])
            # Mark source journal reversed without full reverse workflow.
            await server.db.journal_entries.update_many(
                {"source_type": "sales_invoice", "source_id": inv["id"]},
                {"$set": {"status": "reversed"}},
            )
            blocked = await client.post(
                "/api/sales-receipts",
                json={"invoice_id": inv["id"], "amount": 10_000, "cash_bank_id": bank["id"]},
            )
            assert blocked.status_code == 409, blocked.text
            assert INVOICE_NOT_POSTED_MESSAGE in str(blocked.json())
            assert await server.db.sales_receipts.count_documents({"invoice_id": inv["id"]}) == 0
    finally:
        app.dependency_overrides.pop(current_user, None)


@pytest.mark.anyio
async def test_p3_direct_service_create_cannot_bypass_gate():
    await _configure_settings()
    bank = await _cash_bank()
    po = await _seed_po()
    _admin()
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            res = await client.post(
                "/api/sales-invoices",
                json={
                    "po_id": po["id"],
                    "invoice_date": "2026-09-01",
                    "lines": [{"line_idx": 0, "quantity": 1, "unit_price": 200_000}],
                },
            )
            inv = res.json()["invoice"]
            assert (await client.post(f"/api/sales-invoices/{inv['id']}/finalize")).status_code == 200
        with pytest.raises(Exception) as exc:
            await create_sales_receipt(
                server.db,
                {"invoice_id": inv["id"], "amount": 20_000, "cash_bank_id": bank["id"]},
                ADMIN,
                require_posted_invoice_journal=False,
            )
        assert getattr(exc.value, "status_code", None) == 409
        assert await server.db.sales_receipts.count_documents({"invoice_id": inv["id"]}) == 0
    finally:
        app.dependency_overrides.pop(current_user, None)


@pytest.mark.anyio
async def test_p3_gate_failure_leaves_no_create_claim():
    await _configure_settings()
    bank = await _cash_bank()
    _admin()
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            po = await _seed_po()
            res = await client.post(
                "/api/sales-invoices",
                json={
                    "po_id": po["id"],
                    "invoice_date": "2026-09-01",
                    "lines": [{"line_idx": 0, "quantity": 1, "unit_price": 300_000}],
                },
            )
            inv = res.json()["invoice"]
            assert (await client.post(f"/api/sales-invoices/{inv['id']}/finalize")).status_code == 200
            blocked = await client.post(
                "/api/sales-receipts",
                json={"invoice_id": inv["id"], "amount": 10_000, "cash_bank_id": bank["id"]},
            )
            assert blocked.status_code == 409
            stored = await server.db.sales_invoices.find_one({"id": inv["id"]}, {"_id": 0})
            # Pre-claim journal gate: create claim must never be acquired on reject.
            assert not stored.get("receipt_creation_claim")
            assert not stored.get("receipt_creation_claim_at")
    finally:
        app.dependency_overrides.pop(current_user, None)


@pytest.mark.anyio
async def test_p3_posted_invoice_allows_partial_then_lunas():
    await _configure_settings()
    bank = await _cash_bank()
    _admin()
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            po = await _seed_po()
            inv = await _posted_invoice(client, po["id"], unit_price=500_000)
            r1 = await client.post(
                "/api/sales-receipts",
                json={"invoice_id": inv["id"], "amount": 200_000, "cash_bank_id": bank["id"], "receipt_date": "2026-09-05"},
            )
            assert r1.status_code == 200, r1.text
            assert r1.json()["receipt"]["payment_state"] == "DP"
            mid = await server.db.sales_invoices.find_one({"id": inv["id"]}, {"_id": 0})
            assert float(mid["paid_total"]) == 200_000.0
            assert float(mid["outstanding_amount"]) == 300_000.0
            r2 = await client.post(
                "/api/sales-receipts",
                json={"invoice_id": inv["id"], "amount": 300_000, "cash_bank_id": bank["id"], "receipt_date": "2026-09-06"},
            )
            assert r2.status_code == 200, r2.text
            assert r2.json()["receipt"]["payment_state"] == "LUNAS"
            done = await server.db.sales_invoices.find_one({"id": inv["id"]}, {"_id": 0})
            assert float(done["paid_total"]) == 500_000.0
            assert float(done["outstanding_amount"]) == 0.0
            assert done["payment_status"] == "LUNAS"
    finally:
        app.dependency_overrides.pop(current_user, None)


@pytest.mark.anyio
async def test_p3_auto_lifecycle_path_also_requires_posted_journal():
    from services.sales_receipt_auto_lifecycle import (
        AUTO_FLAG_KEY,
        ERR_INVOICE_NOT_POSTED,
        create_sales_receipt_with_auto_lifecycle,
        ensure_auto_receipt_intent_indexes,
        sales_receipt_idempotency_index_ready,
    )

    await _configure_settings()
    await ensure_auto_receipt_intent_indexes(server.db)
    if not await sales_receipt_idempotency_index_ready(server.db):
        await server.db.sales_receipts.create_index(
            "idempotency_key",
            unique=True,
            partialFilterExpression={"idempotency_key": {"$type": "string"}},
            name="idempotency_key_partial_unique",
        )
    await server.db.app_settings.update_one(
        {"key": AUTO_FLAG_KEY},
        {"$set": {"key": AUTO_FLAG_KEY, "enabled": True}},
        upsert=True,
    )
    bank = await _cash_bank()
    _admin()
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            po = await _seed_po()
            res = await client.post(
                "/api/sales-invoices",
                json={
                    "po_id": po["id"],
                    "invoice_date": "2026-09-01",
                    "lines": [{"line_idx": 0, "quantity": 1, "unit_price": 400_000}],
                },
            )
            inv = res.json()["invoice"]
            assert (await client.post(f"/api/sales-invoices/{inv['id']}/finalize")).status_code == 200
        # Auto-lifecycle catches HTTPException and returns conflict envelope (no receipt write).
        result = await create_sales_receipt_with_auto_lifecycle(
            server.db,
            {
                "invoice_id": inv["id"],
                "amount": 50_000,
                "cash_bank_id": bank["id"],
                "receipt_date": "2026-09-05",
                "idempotency_key": f"auto-gate-{uuid.uuid4().hex}",
            },
            ADMIN,
        )
        assert result.get("financial_complete") is False
        assert int(result.get("http_status") or 0) == 409
        assert result.get("error_code") == ERR_INVOICE_NOT_POSTED or INVOICE_NOT_POSTED_MESSAGE in str(
            result.get("message") or ""
        )
        assert await server.db.sales_receipts.count_documents({"invoice_id": inv["id"]}) == 0
    finally:
        await server.db.app_settings.delete_one({"key": AUTO_FLAG_KEY})
        app.dependency_overrides.pop(current_user, None)
