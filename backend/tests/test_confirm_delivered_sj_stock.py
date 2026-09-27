"""Konfirmasi Terkirim claims the selected SJ rows on an isolated Mongo database."""
import os
import sys
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("MONGO_URL", "mongodb://127.0.0.1:27017")
os.environ.setdefault("DB_NAME", "eternal_confirm_sj_stock_test")
os.environ.setdefault("JWT_SECRET", "test-secret-key-12345678901234567890")
os.environ.setdefault("CORS_ORIGINS", "*")

import server
from server import app, current_user
from services.product_stock_state import get_product_stock_state, initialize_opening_balance

MASTER = "958bb5e9-1090-4676-9999-eff93ff0bd5b"
DB_NAME = "eternal_confirm_sj_stock_test"


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
async def mongo():
    from motor.motor_asyncio import AsyncIOMotorClient

    client = AsyncIOMotorClient(os.environ["MONGO_URL"], serverSelectionTimeoutMS=3000)
    await client.admin.command("ping")
    database = client[DB_NAME]
    await client.drop_database(DB_NAME)
    previous_db = server.db
    previous_client = server.client
    server.client = client
    server.db = database
    yield database
    app.dependency_overrides.pop(current_user, None)
    server.db = previous_db
    server.client = previous_client
    await client.drop_database(DB_NAME)
    client.close()


def _signature(shipment_id, kind):
    if kind == "recipient":
        return {
            "id": f"sig-{shipment_id}",
            "shipment_id": shipment_id,
            "recipient_name": "Penerima Uji",
            "signature_storage_path": f"sig/{shipment_id}-recipient.png",
            "signature_file_id": f"file-{shipment_id}-r",
        }
    return {
        "id": f"dsig-{shipment_id}",
        "shipment_id": shipment_id,
        "driver_name": "Driver Uji",
        "signature_storage_path": f"sig/{shipment_id}-driver.png",
        "driver_signature_storage_path": f"sig/{shipment_id}-driver.png",
        "signature_file_id": f"file-{shipment_id}-d",
    }


def _order(order_id="ord-sj-mongo"):
    return {
        "id": order_id,
        "po_number": "PO/2026/09/22/000011",
        "current_stage": 13,
        "updated_at": "2026-09-27T00:00:00+00:00",
        "qty_delivered": 0,
        "delivery_status": "Belum Terkirim",
        "physical_stock_snapshot": {"enabled": True, "stock_out_trigger": "delivery_confirmed"},
        "line_items": [
            {
                "master_item_id": MASTER,
                "accurate_item_id": "ACC-NAMOY",
                "production_process": "print",
                "quantity": 1000,
                "qty": 1000,
                "line_id": "L0",
                "unit": "PCS",
                "name": "Namoy",
            },
            {
                "master_item_id": MASTER,
                "accurate_item_id": "ACC-NAMOY",
                "production_process": "print",
                "quantity": 1000,
                "qty": 1000,
                "line_id": "L1",
                "unit": "PCS",
                "name": "Namoy",
            },
        ],
        "shipments": [
            {
                "id": "sj-1",
                "number": "SJ/2026/09/26/000011",
                "lines": [
                    {"line_idx": 0, "line_id": "L0", "qty": 400, "name": "Namoy"},
                    {"line_idx": 1, "line_id": "L1", "qty": 400, "name": "Namoy"},
                ],
            },
            {
                "id": "sj-2",
                "number": "SJ/2026/09/27/000012",
                "lines": [
                    {"line_idx": 0, "line_id": "L0", "qty": 600, "name": "Namoy"},
                    {"line_idx": 1, "line_id": "L1", "qty": 600, "name": "Namoy"},
                ],
            },
            {
                "id": "sj-cancel",
                "number": "SJ-CANCEL",
                "cancelled": True,
                "lines": [
                    {"line_idx": 0, "line_id": "L0", "qty": 1000},
                    {"line_idx": 1, "line_id": "L1", "qty": 1000},
                ],
            },
        ],
        "loading_confirmations": [
            {
                "id": "ld-1",
                "shipment_id": "sj-1",
                "surat_jalan_id": "sj-1",
                "lines": [{"line_idx": 0, "qty": 400}, {"line_idx": 1, "qty": 400}],
            },
            {
                "id": "ld-2",
                "shipment_id": "sj-2",
                "surat_jalan_id": "sj-2",
                "lines": [{"line_idx": 0, "qty": 600}, {"line_idx": 1, "qty": 600}],
            },
        ],
        "delivery_proofs": [
            {"id": "p1", "shipment_id": "sj-1"},
            {"id": "p2", "shipment_id": "sj-1"},
            {"id": "p3", "shipment_id": "sj-2"},
            {"id": "p4", "shipment_id": "sj-2"},
        ],
        "delivery_recipient_signature": _signature("sj-1", "recipient"),
        "delivery_driver_signature": _signature("sj-1", "driver"),
        "delivery_confirmations": [],
        "delivery_confirm_action_ids": [],
    }


def _as_admin():
    app.dependency_overrides[current_user] = lambda: {
        "id": "user-admin",
        "name": "Admin Uji",
        "email": "admin@test.local",
        "role": "admin",
        "permissions_override": {},
    }


async def _confirm(order_id, shipment_id, action_id):
    _as_admin()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        return await client.post(
            f"/api/orders/{order_id}/confirm-delivered",
            json={"shipment_id": shipment_id, "action_id": action_id, "recipient_name": "Penerima Uji"},
        )


async def _movements(db):
    return await db.stock_movements.find({"movement_type": "PENGIRIMAN", "master_item_id": MASTER}).to_list(length=20)


@pytest.mark.anyio
async def test_endpoint_confirms_each_sj_qty_and_retries_without_doubling(mongo):
    await mongo.master_items.insert_one({"id": MASTER, "item_name": "Namoy", "unit": "PCS"})
    await initialize_opening_balance(
        mongo, MASTER, {"unprinted_qty": 4700, "printed_qty": 2000, "base_uom": "PCS"}, user={"email": "admin@test.local"},
    )
    order = _order()
    await mongo.orders.insert_one(dict(order))

    bare = _order("ord-bare")
    bare["shipments"] = [{"id": "sj-bare", "number": "SJ-BARE", "total_qty": 2000}]
    await mongo.orders.insert_one(bare)
    missing = await _confirm("ord-bare", "sj-bare", "act-bare")
    assert missing.status_code == 400, missing.text
    assert "qty penuh PO" in missing.text
    state = await get_product_stock_state(mongo, MASTER)
    assert state["printed_qty"] == 2000
    assert not state.get("pending_event")

    cancelled = await _confirm(order["id"], "sj-cancel", "act-cancel")
    assert cancelled.status_code == 400, cancelled.text
    assert "batal" in cancelled.text.lower()

    thin = _order("ord-thin")
    thin["delivery_proofs"] = [{"id": "only", "shipment_id": "sj-1"}]
    await mongo.orders.insert_one(thin)
    blocked = await _confirm("ord-thin", "sj-1", "act-photo")
    assert blocked.status_code == 400, blocked.text
    state = await get_product_stock_state(mongo, MASTER)
    assert state["printed_qty"] == 2000
    assert not state.get("pending_event")
    assert await mongo.orders.find_one({"id": "ord-thin"}, {"delivery_confirmations": 1})
    thin_doc = await mongo.orders.find_one({"id": "ord-thin"}, {"_id": 0, "delivery_confirmations": 1})
    assert thin_doc["delivery_confirmations"] == []

    first = await _confirm(order["id"], "sj-1", "act-sj-1")
    assert first.status_code == 200, first.text
    body = first.json()["order"]
    assert body["qty_delivered"] == 800
    confs = body["delivery_confirmations"]
    assert len(confs) == 1
    assert confs[0]["qty"] == 800
    assert sorted(item["qty"] for item in confs[0]["items"]) == [400, 400]
    state = await get_product_stock_state(mongo, MASTER)
    assert state["printed_qty"] == 1200
    assert state["total_qty"] == 5900
    assert not state.get("pending_event")
    movs = await _movements(mongo)
    assert sorted(m["transaction_qty"] for m in movs) == [400, 400]

    replay = await _confirm(order["id"], "sj-1", "act-sj-1")
    assert replay.status_code == 200, replay.text
    state = await get_product_stock_state(mongo, MASTER)
    assert state["printed_qty"] == 1200
    assert len(await _movements(mongo)) == 2
    stored = await mongo.orders.find_one({"id": order["id"]}, {"_id": 0, "delivery_confirmations": 1})
    assert len(stored["delivery_confirmations"]) == 1

    fresh = await mongo.orders.find_one({"id": order["id"]}, {"_id": 0})
    fresh["delivery_recipient_signature"] = _signature("sj-2", "recipient")
    fresh["delivery_driver_signature"] = _signature("sj-2", "driver")
    await mongo.orders.replace_one({"id": order["id"]}, fresh)
    second = await _confirm(order["id"], "sj-2", "act-sj-2")
    assert second.status_code == 200, second.text
    body = second.json()["order"]
    assert body["qty_delivered"] == 2000
    assert [c["qty"] for c in body["delivery_confirmations"]] == [800, 1200]
    state = await get_product_stock_state(mongo, MASTER)
    assert state["printed_qty"] == 0
    assert state["unprinted_qty"] == 4700
    assert state["total_qty"] == 4700
    movs = await _movements(mongo)
    assert sorted(m["transaction_qty"] for m in movs) == [400, 400, 600, 600]
    assert len({m["id"] for m in movs}) == 4

    table = [
        {
            "sj": "SJ/2026/09/26/000011",
            "po_qty": "1000+1000",
            "sj_qty_before_reconcile": 800,
            "sj_qty_after_reconcile": 800,
            "confirmation_qty": 800,
            "claim_qty": 800,
            "printed_delta": -800,
            "movements": [400, 400],
        },
        {
            "sj": "SJ/2026/09/27/000012",
            "po_qty": "1000+1000",
            "sj_qty_before_reconcile": 1200,
            "sj_qty_after_reconcile": 1200,
            "confirmation_qty": 1200,
            "claim_qty": 1200,
            "printed_delta": -1200,
            "movements": [600, 600],
        },
    ]
    out = Path("/opt/cursor/artifacts/sj_stock_qty_table.txt")
    out.parent.mkdir(parents=True, exist_ok=True)
    lines = ["sj | po_qty | sj_before | sj_after | confirmation | claim | printed_delta | movements"]
    for row in table:
        lines.append(
            f"{row['sj']} | {row['po_qty']} | {row['sj_qty_before_reconcile']} | "
            f"{row['sj_qty_after_reconcile']} | {row['confirmation_qty']} | {row['claim_qty']} | "
            f"{row['printed_delta']} | {row['movements']}"
        )
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    assert table[0]["confirmation_qty"] == table[0]["claim_qty"] == 800
    assert table[1]["confirmation_qty"] == table[1]["claim_qty"] == 1200


@pytest.mark.anyio
async def test_endpoint_finalize_crash_then_same_action_recovers_once(mongo, monkeypatch):
    import services.physical_stock_engine as engine

    await mongo.master_items.insert_one({"id": MASTER, "item_name": "Namoy", "unit": "PCS"})
    await initialize_opening_balance(
        mongo, MASTER, {"unprinted_qty": 4700, "printed_qty": 2000, "base_uom": "PCS"}, user={"email": "admin@test.local"},
    )
    order = _order("ord-crash")
    await mongo.orders.insert_one(dict(order))
    real = engine.finalize_delivery_confirmed_stock
    calls = {"n": 0}

    async def boom(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("finalize stopped")
        return await real(*args, **kwargs)

    monkeypatch.setattr(engine, "finalize_delivery_confirmed_stock", boom)
    with pytest.raises(RuntimeError, match="finalize stopped"):
        await _confirm("ord-crash", "sj-1", "act-recover")
    stored = await mongo.orders.find_one({"id": "ord-crash"}, {"_id": 0, "delivery_confirmations": 1, "qty_delivered": 1})
    assert stored["qty_delivered"] == 800
    assert stored["delivery_confirmations"][0]["qty"] == 800
    state = await get_product_stock_state(mongo, MASTER)
    assert state["pending_event"]["stage"] == "CLAIMED"
    assert state["printed_qty"] == 2000
    assert await _movements(mongo) == []

    monkeypatch.setattr(engine, "finalize_delivery_confirmed_stock", real)
    recovered = await _confirm("ord-crash", "sj-1", "act-recover")
    assert recovered.status_code == 200, recovered.text
    state = await get_product_stock_state(mongo, MASTER)
    assert state["printed_qty"] == 1200
    assert state["total_qty"] == 5900
    assert not state.get("pending_event")
    movs = await _movements(mongo)
    assert sorted(m["transaction_qty"] for m in movs) == [400, 400]
    stored = await mongo.orders.find_one({"id": "ord-crash"}, {"_id": 0, "delivery_confirmations": 1})
    assert len(stored["delivery_confirmations"]) == 1
    again = await _confirm("ord-crash", "sj-1", "act-recover")
    assert again.status_code == 200, again.text
    state = await get_product_stock_state(mongo, MASTER)
    assert state["printed_qty"] == 1200
    assert len(await _movements(mongo)) == 2
