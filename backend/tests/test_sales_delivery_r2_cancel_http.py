"""Option B: HTTP DELETE cancel TERKIRIM → REVERSAL + order reconcile (isolated DB).

Does NOT touch eternal_packaging_uat_r2_manual or eternal_packaging_preview.
"""
from __future__ import annotations

import asyncio
import os
import sys
import uuid
from pathlib import Path

import bcrypt
import httpx
import pytest
from motor.motor_asyncio import AsyncIOMotorClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

TEST_DB = "eternal_packaging_test_sales_delivery_r2_cancel_http"
assert "eternal_packaging_preview" not in TEST_DB
assert "uat_r2_manual" not in TEST_DB
_PREV_DB_NAME = os.environ.get("DB_NAME")
os.environ["DB_NAME"] = TEST_DB
os.environ.setdefault("MONGO_URL", "mongodb://127.0.0.1:27017/?replicaSet=eternal_local_rs")
os.environ.setdefault("JWT_SECRET", "test-secret-key-sales-delivery-r2-cancel-32")
os.environ.setdefault("CORS_ORIGINS", "*")
os.environ.setdefault("APP_ENV", "test")

MONGO_URL = os.environ["MONGO_URL"]
if "replicaSet=" not in MONGO_URL:
    MONGO_URL = MONGO_URL.rstrip("/") + ("&" if "?" in MONGO_URL else "?") + "replicaSet=eternal_local_rs"
    os.environ["MONGO_URL"] = MONGO_URL

import server  # noqa: E402
from server import app, current_user  # noqa: E402
from services.physical_stock import (  # noqa: E402
    BALANCE_COLLECTION,
    LEDGER_COLLECTION,
    MOVEMENT_OPENING_BALANCE,
    MOVEMENT_PURCHASE_RECEIPT,
    MOVEMENT_PRODUCTION_CONSUMPTION,
    MOVEMENT_PRODUCTION_OUTPUT,
    MOVEMENT_REVERSAL,
    MOVEMENT_SALES_DELIVERY,
    MOVEMENT_MANUAL_ADJUSTMENT,
    MOVEMENT_STOCK_OPNAME_ADJUSTMENT,
    MOVEMENT_WAREHOUSE_TRANSFER_IN,
    MOVEMENT_WAREHOUSE_TRANSFER_OUT,
    OPNAME_SESSIONS,
    SALES_DELIVERY_CONFIRMATIONS,
    STOCK_OPNAME_IN_PROGRESS,
    adjust_physical_stock_to_target,
    apply_stock_opname_adjustments,
    confirm_sales_delivery,
    ensure_indexes as ensure_physical_indexes,
    post_movement,
    set_fail_after_balance,
    _client,
)
from services.r2_go_live import set_r2_authority_active  # noqa: E402
from services.sales_deliveries import get_po_delivery_summary  # noqa: E402

pytestmark = pytest.mark.anyio

LEGACY_PSS = ("product_stock_state", "stock_movements", "item_stock_manual", "durable_stock_events")
ADMIN_EMAIL = "r2-cancel-admin@test.local"


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
async def db():
    client = AsyncIOMotorClient(MONGO_URL)
    database = client[TEST_DB]
    await client.drop_database(TEST_DB)
    server.client = client
    server.db = database
    await ensure_physical_indexes(database)
    await set_r2_authority_active(database, active=True)
    set_fail_after_balance(False)

    branch_id = str(uuid.uuid4())
    wh_a = str(uuid.uuid4())
    wh_b = str(uuid.uuid4())
    item = str(uuid.uuid4())
    await database.branches.insert_one(
        {"id": branch_id, "code": "YGY", "name": "Yogyakarta", "active": True},
    )
    await database.warehouses.insert_many([
        {"id": wh_a, "branch_id": branch_id, "code": "GUD-A", "name": "Gudang A", "active": True},
        {"id": wh_b, "branch_id": branch_id, "code": "GUD-B", "name": "Gudang B", "active": True},
    ])
    await database.master_items.insert_one({
        "id": item, "item_code": "R2-CANCEL-001", "item_name": "Cancel Test Item",
        "unit": "PCS", "is_active": True, "accurate_item_id": f"LOCAL-{item[:8]}",
    })
    for wid, qty, key in ((wh_a, 11, "open-a"), (wh_b, 5, "open-b")):
        await post_movement(database, {
            "master_item_id": item, "branch_id": branch_id, "warehouse_id": wid,
            "movement_type": MOVEMENT_OPENING_BALANCE, "qty_delta": qty,
            "reference_type": "opening", "reference_id": key,
            "idempotency_key": f"r2-cancel-{key}-{item}",
        }, actor_id="seed")

    await database.users.insert_many([
        {
            "id": "user-r2-cancel-admin", "email": ADMIN_EMAIL, "name": "Admin",
            "role": "admin", "status": "active",
            "password_hash": bcrypt.hashpw(b"x", bcrypt.gensalt()).decode(),
            "permissions_override": {},
        },
        {
            "id": "user-r2-cancel-designer", "email": "r2-cancel-designer@test.local",
            "name": "Designer", "role": "designer", "status": "active",
            "password_hash": bcrypt.hashpw(b"x", bcrypt.gensalt()).decode(),
            "permissions_override": {
                "sales.delivery.delete": False,
                "sales.delivery.create": False,
                "po.edit": False,
            },
        },
    ])
    fx = {"branch_id": branch_id, "wh_a": wh_a, "wh_b": wh_b, "item": item}
    try:
        yield database, fx
    finally:
        set_fail_after_balance(False)
        app.dependency_overrides.clear()
        await client.drop_database(TEST_DB)
        client.close()
        # Never leave DB_NAME pointing at this isolated DB (or UAT/preview).
        restore = _PREV_DB_NAME if (
            _PREV_DB_NAME
            and "preview" not in _PREV_DB_NAME
            and "uat_r2_manual" not in _PREV_DB_NAME
            and _PREV_DB_NAME != TEST_DB
        ) else "eternal_packaging_test"
        os.environ["DB_NAME"] = restore


def _admin():
    app.dependency_overrides[current_user] = lambda: {
        "role": "admin", "name": "Admin", "email": ADMIN_EMAIL,
        "id": "user-r2-cancel-admin", "permissions_override": {},
    }


def _designer():
    app.dependency_overrides[current_user] = lambda: {
        "role": "designer", "name": "Designer", "email": "r2-cancel-designer@test.local",
        "id": "user-r2-cancel-designer",
        "permissions_override": {
            "sales.delivery.delete": False,
            "sales.delivery.create": False,
            "po.edit": False,
        },
    }


async def _qty(db, fx, warehouse_id):
    row = await db[BALANCE_COLLECTION].find_one({
        "master_item_id": fx["item"], "warehouse_id": warehouse_id,
    })
    return int((row or {}).get("physical_qty") or 0)


async def _legacy_counts(db):
    names = set(await db.list_collection_names())
    return {
        name: (await db[name].count_documents({}) if name in names else 0)
        for name in LEGACY_PSS
    }


async def _seed_sj(
    db, fx, *, qty=15, confirmed_r2=True, terkirim=True, with_proofs=True,
    opening_a=None,
):
    """Seed one SJ. Default mirrors OWNER example: book 11 → deliver 15 → -4."""
    if opening_a is not None:
        # Adjust GUD-A opening to requested by posting delta if needed.
        cur = await _qty(db, fx, fx["wh_a"])
        delta = int(opening_a) - cur
        if delta:
            await post_movement(db, {
                "master_item_id": fx["item"], "branch_id": fx["branch_id"],
                "warehouse_id": fx["wh_a"],                 "movement_type": MOVEMENT_MANUAL_ADJUSTMENT,
                "qty_delta": delta, "reference_type": "adj",
                "reference_id": f"adj-{uuid.uuid4().hex[:8]}",
                "idempotency_key": f"adj-{uuid.uuid4().hex}",
            }, actor_id="seed")

    po_id = f"po-cancel-{uuid.uuid4().hex[:8]}"
    sj_id = f"sj-cancel-{uuid.uuid4().hex[:8]}"
    line_id = f"{po_id}_line_0"
    shipment = {
        "id": sj_id, "number": f"SJ/{sj_id[-6:].upper()}",
        "sales_delivery_id": sj_id, "total_qty": qty,
        "lines": [{"line_idx": 0, "line_id": line_id, "qty": qty, "name": "Cancel Test Item", "unit": "PCS"}],
    }
    if terkirim:
        shipment.update({
            "delivered_at": "2026-10-13T19:00:00+00:00",
            "qty_delivered": qty,
            "delivery_status": "Terkirim",
            "status": "Terkirim",
        })
    order = {
        "id": po_id,
        "po_number": f"PO/CANCEL/{sj_id[-6:].upper()}",
        "customer": "Cancel Customer",
        "branch_id": fx["branch_id"],
        "cancelled": False,
        "current_stage": 14 if terkirim else 12,
        "delivery_status": "Terkirim" if terkirim else "Belum Terkirim",
        "qty_delivered": qty if terkirim else 0,
        "line_items": [{
            "line_id": line_id, "master_item_id": fx["item"],
            "item_code": "R2-CANCEL-001", "name": "Cancel Test Item",
            "quantity": qty, "unit": "PCS", "production_process": "non_print",
        }],
        "operator_splits": [{"line_idx": 0, "qty_completed": qty}],
        "shipments": [shipment],
        "loading_confirmations": [{
            "id": str(uuid.uuid4()), "surat_jalan_id": sj_id, "shipment_id": sj_id,
            "sj_number": shipment["number"],
            "lines": [{"line_idx": 0, "line_id": line_id, "qty": qty}],
            "confirmed_at": "2026-10-13T18:00:00+00:00",
        }],
        "delivery_proofs": [],
        "delivery_confirmations": [],
        "surat_jalan_files": [{
            "id": sj_id, "shipment_id": sj_id, "sj_number": shipment["number"],
            "name": f"{shipment['number']}.pdf", "source": "SYSTEM_GENERATED",
            "lines": [{"line_idx": 0, "line_id": line_id, "qty": qty}],
        }],
        "delivery_recipient_signature": {
            "id": "sig-r", "recipient_name": "Penerima", "shipment_id": sj_id,
            "signature_storage_path": "sig.png",
        },
        "physical_stock_snapshot": {"enabled": True, "stock_out_trigger": "delivery_confirmed"},
    }
    if with_proofs:
        order["delivery_proofs"] = [
            {"id": "p1", "shipment_id": sj_id, "storage_path": "x/p1.jpg", "original_filename": "a.jpg"},
            {"id": "p2", "shipment_id": sj_id, "storage_path": "x/p2.jpg", "original_filename": "b.jpg"},
        ]
    if terkirim:
        order["delivery_confirmations"] = [{
            "id": f"conf-order-{sj_id}", "shipment_id": sj_id,
            "sales_delivery_id": sj_id, "sj_number": shipment["number"],
            "qty": qty, "confirmed_at": "2026-10-13T19:00:00+00:00",
        }]
    await db.orders.insert_one(order)
    await db.sales_deliveries.insert_one({
        "id": sj_id, "po_id": po_id, "sj_number": shipment["number"],
        "total_qty": qty, "status": "SELESAI" if terkirim else "DRAFT",
        "is_cancelled": False,
        "line_items": [{
            "line_idx": 0, "line_id": line_id, "qty": qty,
            "master_item_id": fx["item"], "name": "Cancel Test Item",
        }],
    })
    conf_id = None
    if confirmed_r2:
        conf_id = f"conf-r2-{uuid.uuid4().hex[:8]}"
        result = await confirm_sales_delivery(
            db, order_id=po_id, confirmation_id=conf_id,
            lines=[{"line_idx": 0, "qty": qty, "master_item_id": fx["item"], "line_id": line_id}],
            actor_id="seed", warehouse_id=fx["wh_a"], branch_id=fx["branch_id"],
            reason="Konfirmasi terkirim",
        )
        assert result["replayed"] is False
        await db[SALES_DELIVERY_CONFIRMATIONS].update_one(
            {"id": conf_id}, {"$set": {"sales_delivery_id": sj_id}},
        )
        await db[LEDGER_COLLECTION].update_many(
            {"metadata.confirmation_id": conf_id},
            {"$set": {"metadata.sales_delivery_id": sj_id}},
        )
    return {"po_id": po_id, "sj_id": sj_id, "conf_id": conf_id, "qty": qty}


async def test_1_2_3_4_9_12_cancel_terkirim_restores_origin_warehouse(db):
    database, fx = db
    seeded = await _seed_sj(database, fx, qty=15, confirmed_r2=True, terkirim=True)
    assert await _qty(database, fx, fx["wh_a"]) == -4
    assert await _qty(database, fx, fx["wh_b"]) == 5
    legacy_before = await _legacy_counts(database)
    sales = await database[LEDGER_COLLECTION].find_one({
        "movement_type": MOVEMENT_SALES_DELIVERY,
        "metadata.sales_delivery_id": seeded["sj_id"],
    })
    assert int(sales["qty_delta"]) == -15
    original_id = sales["id"]

    _admin()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        res = await client.delete(f"/api/sales-deliveries/{seeded['sj_id']}")
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["soft_cancelled"] is True
    assert body["status"] == "DIBATALKAN"
    assert body["qty_delivered"] == 0
    assert body["delivery_status"] == "Belum Terkirim"

    # Append-only original + one REVERSAL +15 on GUD-A
    stored = await database[LEDGER_COLLECTION].find_one({"id": original_id})
    assert int(stored["qty_delta"]) == -15
    revs = await database[LEDGER_COLLECTION].find({
        "movement_type": MOVEMENT_REVERSAL, "reference_id": original_id,
    }).to_list(10)
    assert len(revs) == 1
    assert int(revs[0]["qty_delta"]) == 15
    assert revs[0]["warehouse_id"] == fx["wh_a"]
    assert await _qty(database, fx, fx["wh_a"]) == 11
    assert await _qty(database, fx, fx["wh_b"]) == 5

    delivery = await database.sales_deliveries.find_one({"id": seeded["sj_id"]})
    assert delivery["is_cancelled"] is True
    assert delivery["status"] == "DIBATALKAN"

    order = await database.orders.find_one({"id": seeded["po_id"]}, {"_id": 0})
    assert order["qty_delivered"] == 0
    assert order["delivery_status"] == "Belum Terkirim"
    assert order["shipments"][0]["is_cancelled"] is True
    # Cancelled business SJ must disappear from active Workflow SJ, without cancelling Loading.
    assert order["surat_jalan_files"][0]["cancelled"] is True
    assert order["surat_jalan_files"][0]["status"] == "cancelled"
    from quantity_rules import has_surat_jalan_file, validate_cancel_loading_confirmation
    assert has_surat_jalan_file(order) is False
    assert validate_cancel_loading_confirmation(order, order["loading_confirmations"][0]["id"]) is None
    assert len([p for p in order["delivery_proofs"] if not p.get("is_deleted")]) == 2
    assert order.get("delivery_recipient_signature", {}).get("signature_storage_path")
    # Batalkan SJ must NOT auto-cancel Loading — Hapus Loading is a separate step.
    loading = order.get("loading_confirmations") or []
    assert loading, "seed includes loading_confirmations"
    assert all(not c.get("cancelled") for c in loading if isinstance(c, dict))
    assert "loading_cancelled_ids" not in body or not body.get("loading_cancelled_ids")
    lc_id = loading[0]["id"]
    ledger_before_hapus = await database[LEDGER_COLLECTION].count_documents({})

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        hapus = await client.delete(
            f"/api/orders/{seeded['po_id']}/loading-confirm/{lc_id}",
        )
    assert hapus.status_code == 200, hapus.text
    order_after = await database.orders.find_one({"id": seeded["po_id"]}, {"_id": 0})
    lc_after = next(
        c for c in (order_after.get("loading_confirmations") or [])
        if str(c.get("id")) == str(lc_id)
    )
    assert lc_after.get("cancelled") is True
    assert lc_after.get("lines")  # history preserved
    assert await database[LEDGER_COLLECTION].count_documents({}) == ledger_before_hapus
    assert await _legacy_counts(database) == legacy_before

    summary = await get_po_delivery_summary(database, seeded["po_id"])
    assert summary["total_remaining"] == 15
    assert summary["total_shipped"] == 0
    assert await _legacy_counts(database) == legacy_before


async def test_5_partial_multi_delivery_cancel_one(db):
    database, fx = db
    # Boost opening so two deliveries of 5 fit without relying on negative for this case
    await post_movement(database, {
        "master_item_id": fx["item"], "branch_id": fx["branch_id"],
        "warehouse_id": fx["wh_a"], "movement_type": MOVEMENT_MANUAL_ADJUSTMENT,
        "qty_delta": 9, "reference_type": "adj", "reference_id": "boost",
        "idempotency_key": f"boost-{uuid.uuid4().hex}",
    }, actor_id="seed")
    # A=20 now
    first = await _seed_sj(database, fx, qty=5, confirmed_r2=True, terkirim=True)
    # Second SJ on same PO — rebuild manually linked to same po by patching
    # Simpler: seed second independent then move — instead create second on same PO
    po_id = first["po_id"]
    order = await database.orders.find_one({"id": po_id})
    sj2 = f"sj-cancel-{uuid.uuid4().hex[:8]}"
    line_id = order["line_items"][0]["line_id"]
    # Expand ordered qty to 10
    await database.orders.update_one({"id": po_id}, {"$set": {
        "line_items.0.quantity": 10,
        "operator_splits.0.qty_completed": 10,
    }})
    sh2 = {
        "id": sj2, "number": f"SJ/{sj2[-6:].upper()}", "sales_delivery_id": sj2,
        "total_qty": 5, "delivered_at": "2026-10-13T20:00:00+00:00",
        "qty_delivered": 5, "delivery_status": "Terkirim", "status": "Terkirim",
        "lines": [{"line_idx": 0, "line_id": line_id, "qty": 5}],
    }
    await database.orders.update_one({"id": po_id}, {
        "$push": {
            "shipments": sh2,
            "surat_jalan_files": {
                "id": sj2, "shipment_id": sj2, "sj_number": sh2["number"],
                "name": f"{sj2}.pdf", "source": "SYSTEM_GENERATED",
                "lines": [{"line_idx": 0, "line_id": line_id, "qty": 5}],
            },
            "loading_confirmations": {
                "id": f"loading-{sj2}", "shipment_id": sj2, "surat_jalan_id": sj2,
                "lines": [{"line_idx": 0, "qty": 5}],
            },
            "delivery_confirmations": {
                "id": f"conf-order-{sj2}", "shipment_id": sj2,
                "sales_delivery_id": sj2, "qty": 5,
            },
            "delivery_proofs": {"id": "p3", "shipment_id": sj2, "storage_path": "x/p3.jpg"},
        },
        "$set": {"qty_delivered": 10, "delivery_status": "Terkirim"},
    })
    await database.sales_deliveries.insert_one({
        "id": sj2, "po_id": po_id, "sj_number": sh2["number"], "total_qty": 5,
        "status": "SELESAI", "is_cancelled": False,
        "line_items": [{"line_idx": 0, "line_id": line_id, "qty": 5, "master_item_id": fx["item"]}],
    })
    conf2 = f"conf-r2-{uuid.uuid4().hex[:8]}"
    await confirm_sales_delivery(
        database, order_id=po_id, confirmation_id=conf2,
        lines=[{"line_idx": 0, "qty": 5, "master_item_id": fx["item"], "line_id": line_id}],
        actor_id="seed", warehouse_id=fx["wh_a"], branch_id=fx["branch_id"],
    )
    await database[SALES_DELIVERY_CONFIRMATIONS].update_one(
        {"id": conf2}, {"$set": {"sales_delivery_id": sj2}},
    )
    await database[LEDGER_COLLECTION].update_many(
        {"metadata.confirmation_id": conf2},
        {"$set": {"metadata.sales_delivery_id": sj2}},
    )
    before_a = await _qty(database, fx, fx["wh_a"])

    _admin()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        res = await client.delete(f"/api/sales-deliveries/{sj2}")
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["qty_delivered"] == 5
    assert body["delivery_status"] == "Sebagian Terkirim"
    assert await _qty(database, fx, fx["wh_a"]) == before_a + 5
    # Cancel only SJ-2; both Loading confirmations remain until separately cancelled.
    from quantity_rules import validate_cancel_loading_confirmation, qty_loaded_for_line
    updated = await database.orders.find_one({"id": po_id}, {"_id": 0})
    by_id = {row["id"]: row for row in updated["surat_jalan_files"]}
    assert by_id[sj2]["cancelled"] is True
    assert by_id[first["sj_id"]].get("cancelled") is not True
    confirmations = {row["shipment_id"]: row for row in updated["loading_confirmations"]}
    assert confirmations[sj2].get("cancelled") is not True
    assert confirmations[first["sj_id"]].get("cancelled") is not True
    assert qty_loaded_for_line(updated, 0) == 10
    assert validate_cancel_loading_confirmation(updated, f"loading-{sj2}") is None
    assert validate_cancel_loading_confirmation(updated, confirmations[first["sj_id"]]["id"]) is not None
    # First SJ still active
    d1 = await database.sales_deliveries.find_one({"id": first["sj_id"]})
    assert d1.get("is_cancelled") is not True


async def test_6_7_duplicate_and_concurrent_cancel(db):
    database, fx = db
    seeded = await _seed_sj(database, fx, qty=15, confirmed_r2=True)
    _admin()
    transport = httpx.ASGITransport(app=app)

    async def _once():
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.delete(f"/api/sales-deliveries/{seeded['sj_id']}")

    results = await asyncio.gather(_once(), _once(), return_exceptions=True)
    ok = [r for r in results if not isinstance(r, Exception) and r.status_code == 200]
    assert ok
    sales = await database[LEDGER_COLLECTION].find_one({
        "movement_type": MOVEMENT_SALES_DELIVERY,
        "metadata.sales_delivery_id": seeded["sj_id"],
    })
    assert await database[LEDGER_COLLECTION].count_documents({
        "movement_type": MOVEMENT_REVERSAL, "reference_id": sales["id"],
    }) == 1
    assert await _qty(database, fx, fx["wh_a"]) == 11

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        again = await client.delete(f"/api/sales-deliveries/{seeded['sj_id']}")
    assert again.status_code == 200
    assert again.json().get("replayed") is True
    assert await database[LEDGER_COLLECTION].count_documents({
        "movement_type": MOVEMENT_REVERSAL, "reference_id": sales["id"],
    }) == 1


async def test_8_forced_failure_no_partial_state(db):
    database, fx = db
    seeded = await _seed_sj(database, fx, qty=15, confirmed_r2=True)
    set_fail_after_balance(True)
    _admin()
    transport = httpx.ASGITransport(app=app)
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            res = await client.delete(f"/api/sales-deliveries/{seeded['sj_id']}")
    finally:
        set_fail_after_balance(False)
    assert res.status_code >= 400
    delivery = await database.sales_deliveries.find_one({"id": seeded["sj_id"]})
    assert delivery.get("is_cancelled") is not True
    assert await _qty(database, fx, fx["wh_a"]) == -4
    assert await database[LEDGER_COLLECTION].count_documents({"movement_type": MOVEMENT_REVERSAL}) == 0
    order = await database.orders.find_one({"id": seeded["po_id"]})
    assert order["delivery_status"] == "Terkirim"
    assert order["qty_delivered"] == 15


async def test_10_permission_403(db):
    database, fx = db
    seeded = await _seed_sj(database, fx, qty=15, confirmed_r2=True)
    _designer()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        res = await client.delete(f"/api/sales-deliveries/{seeded['sj_id']}")
    assert res.status_code == 403
    assert await database.sales_deliveries.find_one({"id": seeded["sj_id"], "is_cancelled": {"$ne": True}})


async def test_11_r2_off_terkirim_soft_cancel_no_stock_write(db):
    database, fx = db
    await set_r2_authority_active(database, active=False)
    seeded = await _seed_sj(database, fx, qty=15, confirmed_r2=False, terkirim=True)
    before_a = await _qty(database, fx, fx["wh_a"])
    ledger_before = await database[LEDGER_COLLECTION].count_documents({})
    _admin()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        res = await client.delete(f"/api/sales-deliveries/{seeded['sj_id']}")
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["soft_cancelled"] is True
    assert body["qty_delivered"] == 0
    assert await _qty(database, fx, fx["wh_a"]) == before_a
    assert await database[LEDGER_COLLECTION].count_documents({}) == ledger_before
    assert await database[LEDGER_COLLECTION].count_documents({"movement_type": MOVEMENT_REVERSAL}) == 0


async def test_13_unrelated_prior_adjustment_intact(db):
    database, fx = db
    # Prior legitimate non-delivery movement must survive cancel.
    await post_movement(database, {
        "master_item_id": fx["item"], "branch_id": fx["branch_id"],
        "warehouse_id": fx["wh_a"],
        "movement_type": MOVEMENT_MANUAL_ADJUSTMENT,
        "qty_delta": -1, "reference_type": "manual",
        "reference_id": "adj-keep", "idempotency_key": f"adj-keep-{uuid.uuid4().hex}",
        "metadata": {"note": "prior-adjust"},
    }, actor_id="seed")
    # A was 11 → 10, then deliver 15 → -5
    seeded = await _seed_sj(database, fx, qty=15, confirmed_r2=True)
    assert await _qty(database, fx, fx["wh_a"]) == -5
    prior_id = (await database[LEDGER_COLLECTION].find_one({
        "movement_type": MOVEMENT_MANUAL_ADJUSTMENT,
        "reference_id": "adj-keep",
    }))["id"]

    _admin()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        res = await client.delete(f"/api/sales-deliveries/{seeded['sj_id']}")
    assert res.status_code == 200
    prior = await database[LEDGER_COLLECTION].find_one({"id": prior_id})
    assert prior is not None
    assert int(prior["qty_delta"]) == -1
    assert await _qty(database, fx, fx["wh_a"]) == 10  # 11 -1 prior, delivery reversed
    assert await _qty(database, fx, fx["wh_b"]) == 5


async def test_j_missing_404(db):
    database, fx = db
    before = await _qty(database, fx, fx["wh_a"])
    _admin()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        res = await client.delete("/api/sales-deliveries/missing-id")
    assert res.status_code == 404
    assert await _qty(database, fx, fx["wh_a"]) == before


async def test_hard_delete_draft_still_works(db):
    database, fx = db
    seeded = await _seed_sj(
        database, fx, qty=5, confirmed_r2=False, terkirim=False, with_proofs=False,
    )
    # Remove loading so blocker is empty
    await database.orders.update_one({"id": seeded["po_id"]}, {"$set": {"loading_confirmations": []}})
    _admin()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        res = await client.delete(f"/api/sales-deliveries/{seeded['sj_id']}")
    assert res.status_code == 200, res.text
    assert res.json().get("deleted") is True
    assert await database.sales_deliveries.find_one({"id": seeded["sj_id"]}) is None


async def test_phase1_can_uat_style_e2e_cancel_idempotent(db):
    """CAN-UAT style: A=11 B=5 → deliver 15 → A=-4 → cancel → A=11; replay safe."""
    database, fx = db
    assert await _qty(database, fx, fx["wh_a"]) == 11
    assert await _qty(database, fx, fx["wh_b"]) == 5
    seeded = await _seed_sj(database, fx, qty=15, confirmed_r2=True, terkirim=True, with_proofs=True)
    assert await _qty(database, fx, fx["wh_a"]) == -4
    assert await _qty(database, fx, fx["wh_b"]) == 5
    legacy_before = await _legacy_counts(database)
    sales_n = await database[LEDGER_COLLECTION].count_documents({
        "movement_type": MOVEMENT_SALES_DELIVERY,
        "metadata.sales_delivery_id": seeded["sj_id"],
    })
    assert sales_n == 1

    _admin()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        res = await client.delete(f"/api/sales-deliveries/{seeded['sj_id']}")
        assert res.status_code == 200, res.text
        body = res.json()
        assert body["status"] == "DIBATALKAN"
        assert body["soft_cancelled"] is True
        assert body["qty_delivered"] == 0
        again = await client.delete(f"/api/sales-deliveries/{seeded['sj_id']}")
        assert again.status_code == 200
        assert again.json().get("replayed") is True

    assert await _qty(database, fx, fx["wh_a"]) == 11
    assert await _qty(database, fx, fx["wh_b"]) == 5
    assert await database[LEDGER_COLLECTION].count_documents({
        "movement_type": MOVEMENT_SALES_DELIVERY,
        "metadata.sales_delivery_id": seeded["sj_id"],
    }) == 1
    assert await database[LEDGER_COLLECTION].count_documents({
        "movement_type": MOVEMENT_REVERSAL,
    }) == 1
    rev = await database[LEDGER_COLLECTION].find_one({"movement_type": MOVEMENT_REVERSAL})
    assert int(rev["qty_delta"]) == 15
    order = await database.orders.find_one({"id": seeded["po_id"]}, {"_id": 0})
    assert order["qty_delivered"] == 0
    assert len([p for p in order["delivery_proofs"] if not p.get("is_deleted")]) == 2
    assert order.get("delivery_recipient_signature", {}).get("signature_storage_path")
    assert await _legacy_counts(database) == legacy_before


async def _cancel(sj_id):
    _admin()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.delete(f"/api/sales-deliveries/{sj_id}")


async def test_B_purchase_receipt_after_delivery_then_cancel(db):
    """A=-4 → receipt +5 =1 → cancel +15 =16. Receipt history intact."""
    database, fx = db
    seeded = await _seed_sj(database, fx, qty=15, confirmed_r2=True, terkirim=True)
    assert await _qty(database, fx, fx["wh_a"]) == -4
    receipt = await post_movement(database, {
        "master_item_id": fx["item"], "branch_id": fx["branch_id"],
        "warehouse_id": fx["wh_a"],
        "movement_type": MOVEMENT_PURCHASE_RECEIPT,
        "qty_delta": 5, "reference_type": "purchase_receipt",
        "reference_id": "rcpt-after", "idempotency_key": f"rcpt-{uuid.uuid4().hex}",
    }, actor_id="seed")
    assert await _qty(database, fx, fx["wh_a"]) == 1
    res = await _cancel(seeded["sj_id"])
    assert res.status_code == 200, res.text
    assert await _qty(database, fx, fx["wh_a"]) == 16
    assert await _qty(database, fx, fx["wh_b"]) == 5
    kept = await database[LEDGER_COLLECTION].find_one({"id": receipt["id"]})
    assert kept is not None and int(kept["qty_delta"]) == 5
    assert await database[LEDGER_COLLECTION].count_documents({"movement_type": MOVEMENT_REVERSAL}) == 1


async def test_C_transfer_after_delivery_then_cancel(db):
    """A=-4,B=5 → transfer 2 A→B → A=-6,B=7 → cancel → A=9,B=7."""
    database, fx = db
    seeded = await _seed_sj(database, fx, qty=15, confirmed_r2=True, terkirim=True)
    xfer = f"xfer-{uuid.uuid4().hex[:8]}"
    out = await post_movement(database, {
        "master_item_id": fx["item"], "branch_id": fx["branch_id"],
        "warehouse_id": fx["wh_a"],
        "movement_type": MOVEMENT_WAREHOUSE_TRANSFER_OUT,
        "qty_delta": -2, "reference_type": "warehouse_transfer",
        "reference_id": xfer, "idempotency_key": f"{xfer}-out",
    }, actor_id="seed")
    inn = await post_movement(database, {
        "master_item_id": fx["item"], "branch_id": fx["branch_id"],
        "warehouse_id": fx["wh_b"],
        "movement_type": MOVEMENT_WAREHOUSE_TRANSFER_IN,
        "qty_delta": 2, "reference_type": "warehouse_transfer",
        "reference_id": xfer, "idempotency_key": f"{xfer}-in",
    }, actor_id="seed")
    assert await _qty(database, fx, fx["wh_a"]) == -6
    assert await _qty(database, fx, fx["wh_b"]) == 7
    res = await _cancel(seeded["sj_id"])
    assert res.status_code == 200, res.text
    assert await _qty(database, fx, fx["wh_a"]) == 9
    assert await _qty(database, fx, fx["wh_b"]) == 7
    assert await database[LEDGER_COLLECTION].find_one({"id": out["id"]})
    assert await database[LEDGER_COLLECTION].find_one({"id": inn["id"]})


async def test_D_opname_after_delivery_then_cancel_returns_to_current_book(db):
    """Book -4 → physical count 8 → cancel +15 → A=23; opname history unchanged."""
    database, fx = db
    seeded = await _seed_sj(database, fx, qty=15, confirmed_r2=True, terkirim=True)
    assert await _qty(database, fx, fx["wh_a"]) == -4
    session_doc = {
        "id": f"opn-{uuid.uuid4().hex[:8]}",
        "opname_number": "OP-CANCEL-D",
        "branch_id": fx["branch_id"],
        "warehouse_id": fx["wh_a"],
        "mode": "operational",
        "status": "REVIEW",
    }
    await database[OPNAME_SESSIONS].insert_one(dict(session_doc))
    lines = [{
        "line_id": "L1",
        "master_item_id": fx["item"],
        "book_qty": -4,
        "book_qty_at_posting": -4,
        "physical_count_qty": 8,
        "difference_qty": 12,  # 8 - (-4)
        "target_qty": 8,
    }]
    async with await _client(database).start_session() as session:
        async with session.start_transaction():
            moved = await apply_stock_opname_adjustments(
                database, session, session_doc, lines, actor_id="op-user",
                occurred_at="2026-10-13T20:00:00+00:00",
            )
    assert len(moved) == 1
    opname_id = moved[0]["id"]
    opname_before = await database[LEDGER_COLLECTION].find_one({"id": opname_id}, {"_id": 0})
    assert int(opname_before["qty_delta"]) == 12
    assert int(opname_before["balance_before"]) == -4
    assert int(opname_before["balance_after"]) == 8
    assert await _qty(database, fx, fx["wh_a"]) == 8

    res = await _cancel(seeded["sj_id"])
    assert res.status_code == 200, res.text
    assert await _qty(database, fx, fx["wh_a"]) == 23
    assert await _qty(database, fx, fx["wh_b"]) == 5
    opname_after = await database[LEDGER_COLLECTION].find_one({"id": opname_id}, {"_id": 0})
    assert opname_after["qty_delta"] == opname_before["qty_delta"]
    assert opname_after["balance_before"] == opname_before["balance_before"]
    assert opname_after["balance_after"] == opname_before["balance_after"]
    assert opname_after["occurred_at"] == opname_before["occurred_at"]


async def test_E_manual_adjust_after_delivery_then_cancel(db):
    """A=-4 → target adjust to 8 → cancel +15 → A=23; adjustment history intact."""
    database, fx = db
    seeded = await _seed_sj(database, fx, qty=15, confirmed_r2=True, terkirim=True)
    adj = await adjust_physical_stock_to_target(
        database, fx["item"],
        {
            "branch_id": fx["branch_id"], "warehouse_id": fx["wh_a"],
            "target_physical_qty": 8, "reason": "Hitung fisik 8",
            "idempotency_key": f"adj-tgt-{uuid.uuid4().hex}",
        },
        actor_id="seed",
    )
    assert adj["changed"] is True
    assert await _qty(database, fx, fx["wh_a"]) == 8
    adj_row = await database[LEDGER_COLLECTION].find_one({"id": adj["movement_id"]}, {"_id": 0})
    assert adj_row is not None
    snap = {k: adj_row.get(k) for k in ("id", "qty_delta", "balance_before", "balance_after", "occurred_at")}

    res = await _cancel(seeded["sj_id"])
    assert res.status_code == 200, res.text
    assert await _qty(database, fx, fx["wh_a"]) == 23
    kept = await database[LEDGER_COLLECTION].find_one({"id": snap["id"]}, {"_id": 0})
    assert kept["qty_delta"] == snap["qty_delta"]
    assert kept["balance_before"] == snap["balance_before"]
    assert kept["balance_after"] == snap["balance_after"]


async def test_F_production_after_delivery_then_cancel(db):
    database, fx = db
    seeded = await _seed_sj(database, fx, qty=15, confirmed_r2=True, terkirim=True)
    cons = await post_movement(database, {
        "master_item_id": fx["item"], "branch_id": fx["branch_id"],
        "warehouse_id": fx["wh_a"],
        "movement_type": MOVEMENT_PRODUCTION_CONSUMPTION,
        "qty_delta": -1, "reference_type": "production",
        "reference_id": "prod-1", "idempotency_key": f"prod-c-{uuid.uuid4().hex}",
    }, actor_id="seed")
    out = await post_movement(database, {
        "master_item_id": fx["item"], "branch_id": fx["branch_id"],
        "warehouse_id": fx["wh_a"],
        "movement_type": MOVEMENT_PRODUCTION_OUTPUT,
        "qty_delta": 3, "reference_type": "production",
        "reference_id": "prod-1", "idempotency_key": f"prod-o-{uuid.uuid4().hex}",
    }, actor_id="seed")
    # -4 -1 +3 = -2
    assert await _qty(database, fx, fx["wh_a"]) == -2
    res = await _cancel(seeded["sj_id"])
    assert res.status_code == 200, res.text
    assert await _qty(database, fx, fx["wh_a"]) == 13
    assert await database[LEDGER_COLLECTION].find_one({"id": cons["id"]})
    assert await database[LEDGER_COLLECTION].find_one({"id": out["id"]})


async def test_G_unrelated_warehouse_movement_never_blocks(db):
    database, fx = db
    seeded = await _seed_sj(database, fx, qty=15, confirmed_r2=True, terkirim=True)
    await post_movement(database, {
        "master_item_id": fx["item"], "branch_id": fx["branch_id"],
        "warehouse_id": fx["wh_b"],
        "movement_type": MOVEMENT_MANUAL_ADJUSTMENT,
        "qty_delta": 10, "reference_type": "manual",
        "reference_id": "other-wh", "idempotency_key": f"other-{uuid.uuid4().hex}",
    }, actor_id="seed")
    assert await _qty(database, fx, fx["wh_b"]) == 15
    res = await _cancel(seeded["sj_id"])
    assert res.status_code == 200, res.text
    assert await _qty(database, fx, fx["wh_a"]) == 11
    assert await _qty(database, fx, fx["wh_b"]) == 15


async def test_N_active_freeze_opname_blocks_cancel_fail_closed(db):
    """Freeze-mode COUNTING session: cancel must not corrupt; no partial writes."""
    database, fx = db
    seeded = await _seed_sj(database, fx, qty=15, confirmed_r2=True, terkirim=True)
    await database[OPNAME_SESSIONS].insert_one({
        "id": f"opn-freeze-{uuid.uuid4().hex[:8]}",
        "opname_number": "OP-FREEZE",
        "branch_id": fx["branch_id"],
        "warehouse_id": fx["wh_a"],
        "mode": "freeze",
        "status": "COUNTING",
    })
    res = await _cancel(seeded["sj_id"])
    assert res.status_code == 400, res.text
    assert STOCK_OPNAME_IN_PROGRESS in res.text or "opname" in res.text.lower()
    delivery = await database.sales_deliveries.find_one({"id": seeded["sj_id"]})
    assert delivery.get("is_cancelled") is not True
    assert await database[LEDGER_COLLECTION].count_documents({"movement_type": MOVEMENT_REVERSAL}) == 0
    assert await _qty(database, fx, fx["wh_a"]) == -4
    order = await database.orders.find_one({"id": seeded["po_id"]})
    assert order["qty_delivered"] == 15
    assert order["delivery_status"] == "Terkirim"
