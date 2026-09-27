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
    address = client.address
    assert database.name == DB_NAME == "eternal_confirm_sj_stock_test"
    assert address and address[0] in {"127.0.0.1", "localhost"} and address[1] == 27017
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


async def _confirm(order_id, shipment_id, action_id="__omit__"):
    _as_admin()
    payload = {"shipment_id": shipment_id, "recipient_name": "Penerima Uji"}
    if action_id != "__omit__":
        payload["action_id"] = action_id
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        return await client.post(f"/api/orders/{order_id}/confirm-delivered", json=payload)


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


def _full_same_master_sj(order):
    order["shipments"] = [{
        "id": "sj-1",
        "number": "SJ-FULL",
        "lines": [
            {"line_idx": 0, "line_id": "L0", "qty": 1000, "name": "Namoy"},
            {"line_idx": 1, "line_id": "L1", "qty": 1000, "name": "Namoy"},
        ],
    }]
    order["loading_confirmations"] = [{
        "id": "ld-full",
        "shipment_id": "sj-1",
        "surat_jalan_id": "sj-1",
        "lines": [{"line_idx": 0, "qty": 1000}, {"line_idx": 1, "qty": 1000}],
    }]
    order["delivery_proofs"] = [
        {"id": "p1", "shipment_id": "sj-1"},
        {"id": "p2", "shipment_id": "sj-1"},
    ]
    return order


@pytest.mark.anyio
async def test_endpoint_full_same_master_sj_cuts_once(mongo):
    await mongo.master_items.insert_one({"id": MASTER, "item_name": "Namoy", "unit": "PCS"})
    await initialize_opening_balance(
        mongo, MASTER, {"unprinted_qty": 4700, "printed_qty": 2000, "base_uom": "PCS"}, user={"email": "admin@test.local"},
    )
    order = _full_same_master_sj(_order("ord-full-sj"))
    await mongo.orders.insert_one(dict(order))
    first = await _confirm(order["id"], "sj-1", "act-full")
    assert first.status_code == 200, first.text
    body = first.json()["order"]
    assert body["delivery_confirmations"][0]["qty"] == 2000
    state = await get_product_stock_state(mongo, MASTER)
    assert state["printed_qty"] == 0
    assert state["unprinted_qty"] == 4700
    assert state["total_qty"] == 4700
    assert not state.get("pending_event")
    movs = await _movements(mongo)
    assert sorted(m["transaction_qty"] for m in movs) == [1000, 1000]
    assert {m.get("line_id") for m in movs} == {"L0", "L1"}
    replay = await _confirm(order["id"], "sj-1", "act-full")
    assert replay.status_code == 200, replay.text
    state = await get_product_stock_state(mongo, MASTER)
    assert state["printed_qty"] == 0
    assert state["total_qty"] == 4700
    assert len(await _movements(mongo)) == 2
    stored = await mongo.orders.find_one({"id": order["id"]}, {"_id": 0, "delivery_confirmations": 1})
    assert len(stored["delivery_confirmations"]) == 1


@pytest.mark.anyio
async def test_endpoint_combined_same_master_qty_reserves_nothing(mongo):
    await mongo.master_items.insert_one({"id": MASTER, "item_name": "Namoy", "unit": "PCS"})
    await initialize_opening_balance(
        mongo, MASTER, {"unprinted_qty": 4700, "printed_qty": 1500, "base_uom": "PCS"}, user={"email": "admin@test.local"},
    )
    order = _full_same_master_sj(_order("ord-combined-short"))
    await mongo.orders.insert_one(dict(order))
    rejected = await _confirm(order["id"], "sj-1", "act-combined-short")
    assert rejected.status_code == 400, rejected.text
    state = await get_product_stock_state(mongo, MASTER)
    assert state["printed_qty"] == 1500
    assert state["unprinted_qty"] == 4700
    assert state["total_qty"] == 6200
    assert not state.get("pending_event")
    assert await _movements(mongo) == []
    stored = await mongo.orders.find_one({"id": order["id"]}, {"_id": 0, "delivery_confirmations": 1})
    assert stored["delivery_confirmations"] == []


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


async def _seed(mongo, order, *, printed=2000, unprinted=4700):
    if await mongo.master_items.count_documents({"id": MASTER}) == 0:
        await mongo.master_items.insert_one({"id": MASTER, "item_name": "Namoy", "unit": "PCS"})
        await initialize_opening_balance(
            mongo, MASTER,
            {"unprinted_qty": unprinted, "printed_qty": printed, "base_uom": "PCS"},
            user={"email": "admin@test.local"},
        )
    await mongo.orders.insert_one(dict(order))


def _switch_signatures(order, shipment_id):
    order["delivery_recipient_signature"] = _signature(shipment_id, "recipient")
    order["delivery_driver_signature"] = _signature(shipment_id, "driver")


async def _omit_slot(mongo, order_id, shipment_id):
    return await mongo.delivery_confirm_operations.find_one(
        {"kind": "omit", "order_id": order_id, "shipment_id": shipment_id},
        {"_id": 0, "action_id": 1, "shipment_id": 1, "holders": 1},
    )


@pytest.mark.anyio
async def test_omitted_action_id_is_stored_and_retry_does_not_double(mongo):
    order = _order("ord-omit")
    await _seed(mongo, order)
    first = await _confirm(order["id"], "sj-1")
    assert first.status_code == 200, first.text
    stored = await mongo.orders.find_one({"id": order["id"]}, {"_id": 0})
    conf = stored["delivery_confirmations"][0]
    slot = await _omit_slot(mongo, order["id"], "sj-1")
    assert conf["action_id"]
    assert conf["action_id"] == slot["action_id"]
    assert conf["action_id"] in stored["delivery_confirm_action_ids"]
    state = await get_product_stock_state(mongo, MASTER)
    assert state["printed_qty"] == 1200
    assert not state.get("pending_event")
    movs = await _movements(mongo)
    assert sorted(m["transaction_qty"] for m in movs) == [400, 400]
    assert all(conf["action_id"] in m["id"] for m in movs)

    retry = await _confirm(order["id"], "sj-1")
    assert retry.status_code == 200, retry.text
    state = await get_product_stock_state(mongo, MASTER)
    assert state["printed_qty"] == 1200
    assert len(await _movements(mongo)) == 2
    stored = await mongo.orders.find_one({"id": order["id"]}, {"_id": 0, "delivery_confirmations": 1})
    assert len(stored["delivery_confirmations"]) == 1
    assert stored["delivery_confirmations"][0]["action_id"] == conf["action_id"]


@pytest.mark.anyio
async def test_crash_after_claim_before_confirmation_reuses_persistent_action(mongo):
    from services.physical_stock_engine import claim_selected_delivery_stock, prepare_confirm_delivered_action

    order = _order("ord-pre-conf")
    await _seed(mongo, order)
    loaded = await mongo.orders.find_one({"id": order["id"]}, {"_id": 0})
    op = await prepare_confirm_delivered_action(mongo, loaded, "sj-1", None)
    claims = await claim_selected_delivery_stock(mongo, loaded, "sj-1", op["action_id"], None, {})
    assert claims[0]["status"] == "CLAIMED"
    slot = await _omit_slot(mongo, order["id"], "sj-1")
    state = await get_product_stock_state(mongo, MASTER)
    assert state["pending_event"]["action_id"] == slot["action_id"]
    assert state["pending_event"]["stage"] == "CLAIMED"
    assert state["printed_qty"] == 2000
    assert await _movements(mongo) == []
    stored = await mongo.orders.find_one({"id": order["id"]}, {"_id": 0, "delivery_confirmations": 1})
    assert stored["delivery_confirmations"] == []

    recovered = await _confirm(order["id"], "sj-1")
    assert recovered.status_code == 200, recovered.text
    stored = await mongo.orders.find_one({"id": order["id"]}, {"_id": 0})
    assert stored["delivery_confirmations"][0]["action_id"] == slot["action_id"]
    assert len(stored["delivery_confirmations"]) == 1
    state = await get_product_stock_state(mongo, MASTER)
    assert state["printed_qty"] == 1200
    assert not state.get("pending_event")
    movs = await _movements(mongo)
    assert sorted(m["transaction_qty"] for m in movs) == [400, 400]
    assert all(slot["action_id"] in m["id"] for m in movs)
    again = await _confirm(order["id"], "sj-1")
    assert again.status_code == 200, again.text
    state = await get_product_stock_state(mongo, MASTER)
    assert state["printed_qty"] == 1200
    assert len(await _movements(mongo)) == 2
    slots = await mongo.delivery_confirm_operations.count_documents(
        {"kind": "omit", "order_id": order["id"], "shipment_id": "sj-1"},
    )
    assert slots == 1


@pytest.mark.anyio
async def test_crash_after_confirmation_before_finalize_omitted_action_recovers_once(mongo, monkeypatch):
    import services.physical_stock_engine as engine

    order = _order("ord-post-conf")
    await _seed(mongo, order)
    real = engine.finalize_delivery_confirmed_stock

    async def stop(*args, **kwargs):
        raise RuntimeError("finalize stopped")

    monkeypatch.setattr(engine, "finalize_delivery_confirmed_stock", stop)
    monkeypatch.setattr(server, "finalize_delivery_confirmed_stock", stop, raising=False)
    with pytest.raises(RuntimeError, match="finalize stopped"):
        await _confirm(order["id"], "sj-1")
    stored = await mongo.orders.find_one({"id": order["id"]}, {"_id": 0})
    action_id = stored["delivery_confirmations"][0]["action_id"]
    assert action_id
    assert action_id in stored["delivery_confirm_action_ids"]
    state = await get_product_stock_state(mongo, MASTER)
    assert state["pending_event"]["action_id"] == action_id
    assert state["pending_event"]["stage"] == "CLAIMED"
    assert state["printed_qty"] == 2000
    assert await _movements(mongo) == []

    monkeypatch.setattr(engine, "finalize_delivery_confirmed_stock", real)
    recovered = await _confirm(order["id"], "sj-1")
    assert recovered.status_code == 200, recovered.text
    stored = await mongo.orders.find_one({"id": order["id"]}, {"_id": 0, "delivery_confirmations": 1})
    assert stored["delivery_confirmations"][0]["action_id"] == action_id
    assert len(stored["delivery_confirmations"]) == 1
    state = await get_product_stock_state(mongo, MASTER)
    assert state["printed_qty"] == 1200
    assert not state.get("pending_event")
    assert sorted(m["transaction_qty"] for m in await _movements(mongo)) == [400, 400]


@pytest.mark.anyio
async def test_crash_after_balance_write_before_movement_does_not_apply_twice(mongo, monkeypatch):
    import services.physical_stock_engine as engine

    order = _order("ord-mid-finalize")
    await _seed(mongo, order)
    real = engine.record_stock_movement
    calls = {"n": 0}

    async def stop_first_movement(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("movement write stopped")
        return await real(*args, **kwargs)

    monkeypatch.setattr(engine, "record_stock_movement", stop_first_movement)
    with pytest.raises(RuntimeError, match="movement write stopped"):
        await _confirm(order["id"], "sj-1")
    state = await get_product_stock_state(mongo, MASTER)
    assert state["printed_qty"] == 1200
    assert state["pending_event"]["stage"] == "STATE_APPLIED"
    assert await _movements(mongo) == []
    stored = await mongo.orders.find_one({"id": order["id"]}, {"_id": 0, "delivery_confirmations": 1})
    action_id = stored["delivery_confirmations"][0]["action_id"]

    monkeypatch.setattr(engine, "record_stock_movement", real)
    recovered = await _confirm(order["id"], "sj-1")
    assert recovered.status_code == 200, recovered.text
    state = await get_product_stock_state(mongo, MASTER)
    assert state["printed_qty"] == 1200
    assert not state.get("pending_event")
    movs = await _movements(mongo)
    assert sorted(m["transaction_qty"] for m in movs) == [400, 400]
    assert all(action_id in m["id"] for m in movs)
    stored = await mongo.orders.find_one({"id": order["id"]}, {"_id": 0, "delivery_confirmations": 1})
    assert len(stored["delivery_confirmations"]) == 1


@pytest.mark.anyio
async def test_concurrent_omitted_requests_do_not_release_the_live_claim(mongo, monkeypatch):
    import asyncio

    import services.physical_stock_engine as engine
    from fastapi import HTTPException

    order = _order("ord-concurrent")
    await _seed(mongo, order)
    allow_winner = asyncio.Event()
    calls = {"n": 0}
    real_lock = server._with_quantity_lock
    real_release = engine.release_durable_stock_claim
    released = {"n": 0}

    async def spy_release(*args, **kwargs):
        released["n"] += 1
        return await real_release(*args, **kwargs)

    async def wrapped(order_id, mutate):
        calls["n"] += 1
        if calls["n"] == 1:
            await allow_winner.wait()
            return await real_lock(order_id, mutate)

        def fail(_order):
            raise HTTPException(400, "injected duplicate failure")

        try:
            return await real_lock(order_id, fail)
        finally:
            allow_winner.set()

    monkeypatch.setattr(server, "_with_quantity_lock", wrapped)
    monkeypatch.setattr(engine, "release_durable_stock_claim", spy_release)
    results = await asyncio.gather(
        _confirm(order["id"], "sj-1"),
        _confirm(order["id"], "sj-1"),
        return_exceptions=True,
    )
    statuses = []
    for result in results:
        assert not isinstance(result, Exception), result
        statuses.append(result.status_code)
    assert sorted(statuses) == [200, 400]
    assert released["n"] == 0
    state = await get_product_stock_state(mongo, MASTER)
    assert state["printed_qty"] == 1200
    assert not state.get("pending_event")
    movs = await _movements(mongo)
    assert sorted(m["transaction_qty"] for m in movs) == [400, 400]
    stored = await mongo.orders.find_one({"id": order["id"]}, {"_id": 0, "delivery_confirmations": 1})
    assert len(stored["delivery_confirmations"]) == 1
    assert stored["delivery_confirmations"][0]["action_id"]


@pytest.mark.anyio
async def test_explicit_action_id_retry_reuse_and_confirmed_sj_do_not_double(mongo):
    order = _order("ord-explicit")
    await _seed(mongo, order)
    first = await _confirm(order["id"], "sj-1", "act-explicit")
    assert first.status_code == 200, first.text
    retry = await _confirm(order["id"], "sj-1", "act-explicit")
    assert retry.status_code == 200, retry.text
    state = await get_product_stock_state(mongo, MASTER)
    assert state["printed_qty"] == 1200
    assert len(await _movements(mongo)) == 2

    other = await _confirm(order["id"], "sj-2", "act-explicit")
    assert other.status_code == 409, other.text
    assert "berbeda" in other.text
    state = await get_product_stock_state(mongo, MASTER)
    assert state["printed_qty"] == 1200
    assert len(await _movements(mongo)) == 2
    stored = await mongo.orders.find_one({"id": order["id"]}, {"_id": 0, "delivery_confirmations": 1})
    assert [c["shipment_id"] for c in stored["delivery_confirmations"]] == ["sj-1"]

    fresh = await mongo.orders.find_one({"id": order["id"]}, {"_id": 0})
    _switch_signatures(fresh, "sj-2")
    await mongo.orders.replace_one({"id": order["id"]}, fresh)
    second_key = await _confirm(order["id"], "sj-2", "act-other")
    assert second_key.status_code == 200, second_key.text
    state = await get_product_stock_state(mongo, MASTER)
    assert state["printed_qty"] == 0
    assert sorted(m["transaction_qty"] for m in await _movements(mongo)) == [400, 400, 600, 600]

    third = await _confirm(order["id"], "sj-2", "act-another")
    assert third.status_code == 200, third.text
    state = await get_product_stock_state(mongo, MASTER)
    assert state["printed_qty"] == 0
    assert len(await _movements(mongo)) == 4
    stored = await mongo.orders.find_one({"id": order["id"]}, {"_id": 0, "delivery_confirmations": 1})
    assert len(stored["delivery_confirmations"]) == 2

    changed = await mongo.orders.find_one({"id": order["id"]}, {"_id": 0})
    changed["shipments"][0]["lines"][0]["qty"] = 100
    await mongo.orders.replace_one({"id": order["id"]}, changed)
    payload = await _confirm(order["id"], "sj-1", "act-explicit")
    assert payload.status_code == 409, payload.text
    state = await get_product_stock_state(mongo, MASTER)
    assert state["printed_qty"] == 0
    assert len(await _movements(mongo)) == 4

    other_order = _order("ord-other-po")
    await _seed(mongo, other_order)
    cross = await _confirm(other_order["id"], "sj-1", "act-explicit")
    assert cross.status_code == 409, cross.text
    assert "berbeda" in cross.text
    state = await get_product_stock_state(mongo, MASTER)
    assert state["printed_qty"] == 0
    untouched = await mongo.orders.find_one(
        {"id": other_order["id"]}, {"_id": 0, "delivery_confirmations": 1},
    )
    assert untouched["delivery_confirmations"] == []


@pytest.mark.anyio
async def test_partial_sj_without_action_id_retries_do_not_change_totals(mongo):
    order = _order("ord-partial-omit")
    await _seed(mongo, order)
    first = await _confirm(order["id"], "sj-1")
    assert first.status_code == 200, first.text
    again = await _confirm(order["id"], "sj-1")
    assert again.status_code == 200, again.text
    state = await get_product_stock_state(mongo, MASTER)
    assert state["printed_qty"] == 1200
    first_movs = await _movements(mongo)
    assert sorted(m["transaction_qty"] for m in first_movs) == [400, 400]

    fresh = await mongo.orders.find_one({"id": order["id"]}, {"_id": 0})
    _switch_signatures(fresh, "sj-2")
    await mongo.orders.replace_one({"id": order["id"]}, fresh)
    second = await _confirm(order["id"], "sj-2")
    assert second.status_code == 200, second.text
    again = await _confirm(order["id"], "sj-2")
    assert again.status_code == 200, again.text
    state = await get_product_stock_state(mongo, MASTER)
    assert state["printed_qty"] == 0
    assert state["total_qty"] == 4700
    movs = await _movements(mongo)
    assert sorted(m["transaction_qty"] for m in movs) == [400, 400, 600, 600]
    stored = await mongo.orders.find_one({"id": order["id"]}, {"_id": 0, "delivery_confirmations": 1})
    assert [c["qty"] for c in stored["delivery_confirmations"]] == [800, 1200]
    assert stored["delivery_confirmations"][0]["action_id"] != stored["delivery_confirmations"][1]["action_id"]
    first_ids = {m["id"] for m in first_movs}
    assert first_ids.isdisjoint({m["id"] for m in movs if m["transaction_qty"] == 600})
    table = [
        "sj | confirmation_qty | reservation | printed_delta | movements",
        "sj-1 | 800 | 800 | -800 | 400+400",
        "sj-2 | 1200 | 1200 | -1200 | 600+600",
    ]
    out = Path("/opt/cursor/artifacts/sj_omit_action_qty_table.txt")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(table) + "\n", encoding="utf-8")


@pytest.mark.anyio
async def test_blank_action_id_and_failed_validation_leave_no_active_reservation(mongo):
    order = _order("ord-blank")
    order["delivery_proofs"] = [{"id": "only", "shipment_id": "sj-1"}]
    await _seed(mongo, order)
    for token in ("   ", None, "", "__omit__"):
        if token == "__omit__":
            failed = await _confirm(order["id"], "sj-1")
        else:
            failed = await _confirm(order["id"], "sj-1", token)
        assert failed.status_code == 400, failed.text
    state = await get_product_stock_state(mongo, MASTER)
    assert state["printed_qty"] == 2000
    assert not state.get("pending_event")
    stored = await mongo.orders.find_one({"id": order["id"]}, {"_id": 0, "delivery_confirmations": 1})
    assert stored["delivery_confirmations"] == []
    slots = await mongo.delivery_confirm_operations.find(
        {"kind": "omit", "order_id": order["id"], "shipment_id": "sj-1"},
    ).to_list(length=10)
    assert len(slots) == 1
    assert slots[0]["action_id"]

    short = _order("ord-short")
    await _seed(mongo, short)
    await mongo.product_stock_state.update_one(
        {"master_item_id": MASTER},
        {"$set": {"printed_qty": 100, "total_qty": 4800}},
    )
    rejected = await _confirm(short["id"], "sj-1")
    assert rejected.status_code == 400, rejected.text
    state = await get_product_stock_state(mongo, MASTER)
    assert state["printed_qty"] == 100
    assert not state.get("pending_event")
    stored = await mongo.orders.find_one({"id": short["id"]}, {"_id": 0, "delivery_confirmations": 1})
    assert stored["delivery_confirmations"] == []
    await mongo.product_stock_state.update_one(
        {"master_item_id": MASTER},
        {"$set": {"printed_qty": 2000, "total_qty": 6700}},
    )

    await mongo.orders.update_one(
        {"id": order["id"]},
        {"$set": {"delivery_proofs": [
            {"id": "p1", "shipment_id": "sj-1"},
            {"id": "p2", "shipment_id": "sj-1"},
        ]}},
    )
    done = await _confirm(order["id"], "sj-1")
    assert done.status_code == 200, done.text
    stored = await mongo.orders.find_one({"id": order["id"]}, {"_id": 0, "delivery_confirmations": 1})
    assert stored["delivery_confirmations"][0]["action_id"] == slots[0]["action_id"]
    state = await get_product_stock_state(mongo, MASTER)
    assert state["printed_qty"] == 1200
    assert not state.get("pending_event")
    assert sorted(m["transaction_qty"] for m in await _movements(mongo)) == [400, 400]


@pytest.mark.anyio
async def test_concurrent_omitted_action_inserts_one_persistent_id(mongo):
    import asyncio

    from services.physical_stock_engine import _acquire_omitted_action

    ids = await asyncio.gather(*[
        _acquire_omitted_action(mongo, "ord-atomic", "sj-atomic") for _ in range(8)
    ])
    assert len(set(ids)) == 1
    assert ids[0]
    count = await mongo.delivery_confirm_operations.count_documents(
        {"kind": "omit", "order_id": "ord-atomic", "shipment_id": "sj-atomic"},
    )
    assert count == 1


MASTER_A = "master-a-shared-claim"
MASTER_B = "master-b-shared-claim"


def _two_master_order(order_id="ord-two-master"):
    return {
        "id": order_id,
        "po_number": "PO-TWO-MASTER",
        "current_stage": 13,
        "updated_at": "2026-09-27T00:00:00+00:00",
        "qty_delivered": 0,
        "delivery_status": "Belum Terkirim",
        "physical_stock_snapshot": {"enabled": True, "stock_out_trigger": "delivery_confirmed"},
        "line_items": [
            {
                "master_item_id": MASTER_A,
                "accurate_item_id": "ACC-A",
                "production_process": "print",
                "quantity": 1000,
                "qty": 1000,
                "line_id": "LA",
                "unit": "PCS",
                "name": "Master A",
            },
            {
                "master_item_id": MASTER_A,
                "accurate_item_id": "ACC-A",
                "production_process": "print",
                "quantity": 1000,
                "qty": 1000,
                "line_id": "LB",
                "unit": "PCS",
                "name": "Master A",
            },
            {
                "master_item_id": MASTER_B,
                "accurate_item_id": "ACC-B",
                "production_process": "print",
                "quantity": 1000,
                "qty": 1000,
                "line_id": "LC",
                "unit": "PCS",
                "name": "Master B",
            },
        ],
        "shipments": [{
            "id": "sj-share",
            "number": "SJ-TWO-MASTER",
            "lines": [
                {"line_idx": 0, "line_id": "LA", "qty": 100, "name": "Master A"},
                {"line_idx": 1, "line_id": "LB", "qty": 150, "name": "Master A"},
                {"line_idx": 2, "line_id": "LC", "qty": 80, "name": "Master B"},
            ],
        }],
        "loading_confirmations": [{
            "id": "ld-share",
            "shipment_id": "sj-share",
            "surat_jalan_id": "sj-share",
            "lines": [
                {"line_idx": 0, "qty": 100},
                {"line_idx": 1, "qty": 150},
                {"line_idx": 2, "qty": 80},
            ],
        }],
        "delivery_proofs": [
            {"id": "p1", "shipment_id": "sj-share"},
            {"id": "p2", "shipment_id": "sj-share"},
        ],
        "delivery_recipient_signature": _signature("sj-share", "recipient"),
        "delivery_driver_signature": _signature("sj-share", "driver"),
        "delivery_confirmations": [],
        "delivery_confirm_action_ids": [],
    }


@pytest.mark.anyio
async def test_failing_request_does_not_release_the_other_masters_shared_claim(mongo, monkeypatch):
    """Two masters, one shared action. B must not drop A's first-master claim."""
    import asyncio

    from fastapi import HTTPException

    import services.physical_stock_engine as engine

    for mid, name in ((MASTER_A, "Master A"), (MASTER_B, "Master B")):
        await mongo.master_items.insert_one({"id": mid, "item_name": name, "unit": "PCS"})
        await initialize_opening_balance(
            mongo, mid,
            {"unprinted_qty": 0, "printed_qty": 1000, "base_uom": "PCS"},
            user={"email": "admin@test.local"},
        )
    order = _two_master_order()
    await mongo.orders.insert_one(dict(order))

    a_paused = asyncio.Event()
    b_finished = asyncio.Event()
    reserve_calls = {"n": 0}
    durable_calls = {"n": 0}
    real_reserve = engine._reserve_delivery_master_group
    real_durable = engine.claim_durable_stock_event

    async def reserve_wrapper(*args, **kwargs):
        result = await real_reserve(*args, **kwargs)
        reserve_calls["n"] += 1
        if reserve_calls["n"] == 1:
            a_paused.set()
            await b_finished.wait()
        return result

    async def durable_wrapper(*args, **kwargs):
        durable_calls["n"] += 1
        if durable_calls["n"] == 1:
            raise HTTPException(400, "forced second master failure")
        return await real_durable(*args, **kwargs)

    monkeypatch.setattr(engine, "_reserve_delivery_master_group", reserve_wrapper)
    monkeypatch.setattr(engine, "claim_durable_stock_event", durable_wrapper)

    task_a = asyncio.create_task(_confirm(order["id"], "sj-share", "act-share"))
    await a_paused.wait()
    result_b = await _confirm(order["id"], "sj-share", "act-share")
    state_a = await get_product_stock_state(mongo, MASTER_A)
    state_b = await get_product_stock_state(mongo, MASTER_B)
    pending_a = (state_a or {}).get("pending_event")
    assert result_b.status_code == 400, result_b.text
    assert pending_a and pending_a.get("stage") == "CLAIMED", pending_a
    assert pending_a.get("action_id") == "act-share"
    assert pending_a.get("master_item_id") == MASTER_A
    assert float(pending_a.get("delta_printed") or 0) == -250.0
    assert not (state_b or {}).get("pending_event")
    assert state_a["printed_qty"] == 1000
    b_finished.set()

    result_a = await task_a
    assert result_a.status_code == 200, result_a.text
    state_a = await get_product_stock_state(mongo, MASTER_A)
    state_b = await get_product_stock_state(mongo, MASTER_B)
    assert state_a["printed_qty"] == 750
    assert state_b["printed_qty"] == 920
    assert not state_a.get("pending_event")
    assert not state_b.get("pending_event")
    movs = await mongo.stock_movements.find({"movement_type": "PENGIRIMAN"}).to_list(length=20)
    assert sorted(m["transaction_qty"] for m in movs) == [80, 100, 150]
    assert {m.get("line_id") for m in movs} == {"LA", "LB", "LC"}
    stored = await mongo.orders.find_one({"id": order["id"]}, {"_id": 0, "delivery_confirmations": 1})
    assert len(stored["delivery_confirmations"]) == 1
    assert stored["delivery_confirmations"][0]["qty"] == 330
    assert stored["delivery_confirmations"][0]["action_id"] == "act-share"


@pytest.mark.anyio
async def test_failed_request_releases_only_the_claim_it_created(mongo, monkeypatch):
    """One request creates master A, then fails on master B. Only A's new claim is cleared."""
    from fastapi import HTTPException

    import services.physical_stock_engine as engine

    for mid, name in ((MASTER_A, "Master A"), (MASTER_B, "Master B")):
        await mongo.master_items.insert_one({"id": mid, "item_name": name, "unit": "PCS"})
        await initialize_opening_balance(
            mongo, mid,
            {"unprinted_qty": 0, "printed_qty": 1000, "base_uom": "PCS"},
            user={"email": "admin@test.local"},
        )
    order = _two_master_order("ord-own-release")
    await mongo.orders.insert_one(dict(order))

    async def durable_wrapper(*args, **kwargs):
        raise HTTPException(400, "forced second master failure")

    monkeypatch.setattr(engine, "claim_durable_stock_event", durable_wrapper)
    result = await _confirm(order["id"], "sj-share", "act-own-fail")
    assert result.status_code == 400, result.text
    state_a = await get_product_stock_state(mongo, MASTER_A)
    state_b = await get_product_stock_state(mongo, MASTER_B)
    assert not state_a.get("pending_event")
    assert not state_b.get("pending_event")
    assert state_a["printed_qty"] == 1000
    assert state_b["printed_qty"] == 1000
    movs = await mongo.stock_movements.find({"movement_type": "PENGIRIMAN"}).to_list(length=20)
    assert movs == []
    stored = await mongo.orders.find_one({"id": order["id"]}, {"_id": 0, "delivery_confirmations": 1})
    assert stored["delivery_confirmations"] == []


def _explicit_holder_id(action_id):
    return f"act:{action_id}:delivery_confirmed"


async def _seed_two_masters(mongo, order):
    for mid, name in ((MASTER_A, "Master A"), (MASTER_B, "Master B")):
        await mongo.master_items.insert_one({"id": mid, "item_name": name, "unit": "PCS"})
        await initialize_opening_balance(
            mongo, mid,
            {"unprinted_qty": 0, "printed_qty": 1000, "base_uom": "PCS"},
            user={"email": "admin@test.local"},
        )
    await mongo.orders.insert_one(dict(order))


async def _holder_count(mongo, action_id):
    doc = await mongo.delivery_confirm_operations.find_one({"_id": _explicit_holder_id(action_id)})
    return int((doc or {}).get("holders") or 0)


async def _crash_after_master_a_claim(mongo, order, action_id):
    """Persist the holder and master A claim, then stop before finish.

    This is the process-death boundary. No exception is raised inside
    confirm_delivered, so its finally block does not run.
    """
    from services.physical_stock_engine import (
        _delivery_claim_specs,
        _delivery_line_deltas,
        _reserve_delivery_master_group,
        plan_confirmed_sj_stock,
        prepare_confirm_delivered_action,
    )

    loaded = await mongo.orders.find_one({"id": order["id"]}, {"_id": 0})
    op = await prepare_confirm_delivered_action(mongo, loaded, "sj-share", action_id)
    plan = plan_confirmed_sj_stock(loaded, loaded["shipments"][0])
    assert plan.get("error") is None and not plan.get("skip"), plan
    specs = [spec for spec in _delivery_claim_specs(loaded, plan["lines"], op["action_id"]) if spec["mid"] == MASTER_A]
    assert len(specs) == 2
    enriched = [await _delivery_line_deltas(mongo, spec) for spec in specs]
    claim = await _reserve_delivery_master_group(mongo, loaded, op["action_id"], enriched, None)
    assert claim["status"] == "CLAIMED"
    assert claim["resumed"] is False
    return op["action_id"]


@pytest.mark.anyio
async def test_crash_before_finish_then_failed_retry_still_recovers(mongo, monkeypatch):
    """Dead process leaves holder and master A. A failed retry must not strand the SJ."""
    from fastapi import HTTPException

    import services.physical_stock_engine as engine

    action_id = "act-crash-hold"
    order = _two_master_order("ord-crash-hold")
    await _seed_two_masters(mongo, order)
    effective = await _crash_after_master_a_claim(mongo, order, action_id)
    assert effective == action_id
    assert await _holder_count(mongo, action_id) == 1
    state_a = await get_product_stock_state(mongo, MASTER_A)
    pending_a = state_a.get("pending_event")
    assert pending_a["stage"] == "CLAIMED"
    assert pending_a["action_id"] == action_id
    assert pending_a["event_id"] == f"stock_out:delivery:{order['id']}:{action_id}:master:{MASTER_A}"
    assert float(pending_a["delta_printed"]) == -250.0
    assert not (await get_product_stock_state(mongo, MASTER_B)).get("pending_event")

    real_durable = engine.claim_durable_stock_event

    async def fail_master_b(*args, **kwargs):
        raise HTTPException(400, "forced second master failure")

    monkeypatch.setattr(engine, "claim_durable_stock_event", fail_master_b)
    failed = await _confirm(order["id"], "sj-share", action_id)
    monkeypatch.setattr(engine, "claim_durable_stock_event", real_durable)

    assert failed.status_code == 400, failed.text
    assert await _holder_count(mongo, action_id) == 1
    state_a = await get_product_stock_state(mongo, MASTER_A)
    state_b = await get_product_stock_state(mongo, MASTER_B)
    pending_a = state_a.get("pending_event")
    assert pending_a["stage"] == "CLAIMED"
    assert pending_a["event_id"] == f"stock_out:delivery:{order['id']}:{action_id}:master:{MASTER_A}"
    assert not state_b.get("pending_event")
    assert state_a["printed_qty"] == 1000
    assert state_b["printed_qty"] == 1000
    stored = await mongo.orders.find_one({"id": order["id"]}, {"_id": 0, "delivery_confirmations": 1})
    assert stored["delivery_confirmations"] == []
    assert await mongo.stock_movements.count_documents({"movement_type": "PENGIRIMAN"}) == 0

    recovered = await _confirm(order["id"], "sj-share", action_id)
    assert recovered.status_code == 200, recovered.text
    state_a = await get_product_stock_state(mongo, MASTER_A)
    state_b = await get_product_stock_state(mongo, MASTER_B)
    assert state_a["printed_qty"] == 750
    assert state_b["printed_qty"] == 920
    assert not state_a.get("pending_event")
    assert not state_b.get("pending_event")
    movs = await mongo.stock_movements.find({"movement_type": "PENGIRIMAN"}).to_list(length=20)
    assert sorted(m["transaction_qty"] for m in movs) == [80, 100, 150]
    assert {m.get("line_id") for m in movs} == {"LA", "LB", "LC"}
    stored = await mongo.orders.find_one({"id": order["id"]}, {"_id": 0, "delivery_confirmations": 1})
    assert len(stored["delivery_confirmations"]) == 1
    assert stored["delivery_confirmations"][0]["qty"] == 330
    assert stored["delivery_confirmations"][0]["action_id"] == action_id
    assert await _holder_count(mongo, action_id) == 1
    again = await _confirm(order["id"], "sj-share", action_id)
    assert again.status_code == 200, again.text
    state_a = await get_product_stock_state(mongo, MASTER_A)
    state_b = await get_product_stock_state(mongo, MASTER_B)
    assert state_a["printed_qty"] == 750
    assert state_b["printed_qty"] == 920
    assert await mongo.stock_movements.count_documents({"movement_type": "PENGIRIMAN"}) == 3


@pytest.mark.anyio
async def test_crash_before_finish_successful_retry_completes_once(mongo):
    action_id = "act-crash-ok"
    order = _two_master_order("ord-crash-ok")
    await _seed_two_masters(mongo, order)
    await _crash_after_master_a_claim(mongo, order, action_id)
    assert await _holder_count(mongo, action_id) == 1

    recovered = await _confirm(order["id"], "sj-share", action_id)
    assert recovered.status_code == 200, recovered.text
    state_a = await get_product_stock_state(mongo, MASTER_A)
    state_b = await get_product_stock_state(mongo, MASTER_B)
    assert state_a["printed_qty"] == 750
    assert state_b["printed_qty"] == 920
    assert not state_a.get("pending_event")
    assert not state_b.get("pending_event")
    movs = await mongo.stock_movements.find({"movement_type": "PENGIRIMAN"}).to_list(length=20)
    assert sorted(m["transaction_qty"] for m in movs) == [80, 100, 150]
    assert {m.get("line_id") for m in movs} == {"LA", "LB", "LC"}
    stored = await mongo.orders.find_one({"id": order["id"]}, {"_id": 0, "delivery_confirmations": 1})
    assert len(stored["delivery_confirmations"]) == 1
    assert stored["delivery_confirmations"][0]["qty"] == 330
    assert await _holder_count(mongo, action_id) == 1
    again = await _confirm(order["id"], "sj-share", action_id)
    assert again.status_code == 200, again.text
    assert await _holder_count(mongo, action_id) == 1
    assert (await get_product_stock_state(mongo, MASTER_A))["printed_qty"] == 750
    assert (await get_product_stock_state(mongo, MASTER_B))["printed_qty"] == 920


async def _seed_full_sj(mongo, order_id):
    await mongo.master_items.insert_one({"id": MASTER, "item_name": "Namoy", "unit": "PCS"})
    await initialize_opening_balance(
        mongo, MASTER,
        {"unprinted_qty": 4700, "printed_qty": 2000, "base_uom": "PCS"},
        user={"email": "admin@test.local"},
    )
    order = _full_same_master_sj(_order(order_id))
    await mongo.orders.insert_one(dict(order))
    return order


async def _fail_movement_write(monkeypatch, fail_on_call):
    import services.physical_stock_engine as engine

    real = engine.record_stock_movement
    calls = {"n": 0}

    async def flaky(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == fail_on_call:
            raise RuntimeError("movement write stopped")
        return await real(*args, **kwargs)

    monkeypatch.setattr(engine, "record_stock_movement", flaky)
    return real


def _assert_full_stock_recovered(state, movs, stored):
    assert state["printed_qty"] == 0
    assert state["unprinted_qty"] == 4700
    assert state["total_qty"] == 4700
    assert not state.get("pending_event")
    assert sorted(m["transaction_qty"] for m in movs) == [1000, 1000]
    assert {m.get("line_id") for m in movs} == {"L0", "L1"}
    assert sum(float(m.get("delta_printed") or 0) for m in movs) == -2000
    assert len(stored["delivery_confirmations"]) == 1
    assert stored["delivery_confirmations"][0]["qty"] == 2000


@pytest.mark.anyio
async def test_retry_after_applied_zero_balance_before_any_movement(mongo, monkeypatch):
    order = await _seed_full_sj(mongo, "ord-r1")
    real = await _fail_movement_write(monkeypatch, 1)
    with pytest.raises(RuntimeError, match="movement write stopped"):
        await _confirm(order["id"], "sj-1", "act-r1")
    state = await get_product_stock_state(mongo, MASTER)
    assert state["printed_qty"] == 0
    assert state["pending_event"]["stage"] == "STATE_APPLIED"
    assert await _movements(mongo) == []
    stored = await mongo.orders.find_one({"id": order["id"]}, {"_id": 0, "delivery_confirmations": 1})
    assert len(stored["delivery_confirmations"]) == 1
    import services.physical_stock_engine as engine
    monkeypatch.setattr(engine, "record_stock_movement", real)
    recovered = await _confirm(order["id"], "sj-1", "act-r1")
    assert recovered.status_code == 200, recovered.text
    state = await get_product_stock_state(mongo, MASTER)
    movs = await _movements(mongo)
    stored = await mongo.orders.find_one({"id": order["id"]}, {"_id": 0, "delivery_confirmations": 1})
    _assert_full_stock_recovered(state, movs, stored)
    again = await _confirm(order["id"], "sj-1", "act-r1")
    assert again.status_code == 200, again.text
    assert (await get_product_stock_state(mongo, MASTER))["printed_qty"] == 0
    assert len(await _movements(mongo)) == 2


@pytest.mark.anyio
async def test_retry_after_applied_zero_balance_before_second_movement(mongo, monkeypatch):
    order = await _seed_full_sj(mongo, "ord-r2")
    real = await _fail_movement_write(monkeypatch, 2)
    with pytest.raises(RuntimeError, match="movement write stopped"):
        await _confirm(order["id"], "sj-1", "act-r2")
    state = await get_product_stock_state(mongo, MASTER)
    assert state["printed_qty"] == 0
    assert state["pending_event"]["stage"] == "STATE_APPLIED"
    movs = await _movements(mongo)
    assert len(movs) == 1
    assert movs[0]["line_id"] == "L0"
    import services.physical_stock_engine as engine
    monkeypatch.setattr(engine, "record_stock_movement", real)
    recovered = await _confirm(order["id"], "sj-1", "act-r2")
    assert recovered.status_code == 200, recovered.text
    state = await get_product_stock_state(mongo, MASTER)
    movs = await _movements(mongo)
    stored = await mongo.orders.find_one({"id": order["id"]}, {"_id": 0, "delivery_confirmations": 1})
    _assert_full_stock_recovered(state, movs, stored)
    again = await _confirm(order["id"], "sj-1", "act-r2")
    assert again.status_code == 200, again.text
    assert len(await _movements(mongo)) == 2
    assert (await get_product_stock_state(mongo, MASTER))["total_qty"] == 4700


@pytest.mark.anyio
async def test_retry_finishes_applied_master_without_redebiting_the_finalized_one(mongo, monkeypatch):
    import services.physical_stock_engine as engine

    for mid, name in ((MASTER_A, "Master A"), (MASTER_B, "Master B")):
        await mongo.master_items.insert_one({"id": mid, "item_name": name, "unit": "PCS"})
        await initialize_opening_balance(
            mongo, mid,
            {"unprinted_qty": 0, "printed_qty": 1000, "base_uom": "PCS"},
            user={"email": "admin@test.local"},
        )
    order = _two_master_order("ord-partial-applied")
    await mongo.orders.insert_one(dict(order))
    real = engine.record_stock_movement

    async def fail_master_b(*args, **kwargs):
        if kwargs.get("master_item_id") == MASTER_B:
            raise RuntimeError("movement write stopped")
        return await real(*args, **kwargs)

    monkeypatch.setattr(engine, "record_stock_movement", fail_master_b)
    with pytest.raises(RuntimeError, match="movement write stopped"):
        await _confirm(order["id"], "sj-share", "act-partial-applied")
    monkeypatch.setattr(engine, "record_stock_movement", real)
    state_a = await get_product_stock_state(mongo, MASTER_A)
    state_b = await get_product_stock_state(mongo, MASTER_B)
    assert state_a["printed_qty"] == 750
    assert not state_a.get("pending_event")
    assert state_b["printed_qty"] == 920
    assert state_b["pending_event"]["stage"] == "STATE_APPLIED"
    recovered = await _confirm(order["id"], "sj-share", "act-partial-applied")
    assert recovered.status_code == 200, recovered.text
    state_a = await get_product_stock_state(mongo, MASTER_A)
    state_b = await get_product_stock_state(mongo, MASTER_B)
    assert state_a["printed_qty"] == 750
    assert state_b["printed_qty"] == 920
    assert not state_a.get("pending_event")
    assert not state_b.get("pending_event")
    movs = await mongo.stock_movements.find({"movement_type": "PENGIRIMAN"}).to_list(length=20)
    assert sorted(m["transaction_qty"] for m in movs) == [80, 100, 150]
    assert {m.get("line_id") for m in movs} == {"LA", "LB", "LC"}
    stored = await mongo.orders.find_one({"id": order["id"]}, {"_id": 0, "delivery_confirmations": 1})
    assert len(stored["delivery_confirmations"]) == 1
    again = await _confirm(order["id"], "sj-share", "act-partial-applied")
    assert again.status_code == 200, again.text
    assert (await get_product_stock_state(mongo, MASTER_A))["printed_qty"] == 750
    assert (await get_product_stock_state(mongo, MASTER_B))["printed_qty"] == 920
    assert await mongo.stock_movements.count_documents({"movement_type": "PENGIRIMAN"}) == 3


def _one_line_order(order_id, master_id, qty, line_id="L0"):
    order = _order(order_id)
    order["line_items"] = [{
        "master_item_id": master_id,
        "accurate_item_id": "ACC-X",
        "production_process": "print",
        "quantity": qty,
        "qty": qty,
        "line_id": line_id,
        "unit": "PCS",
        "name": "Barang Uji",
    }]
    order["shipments"] = [{
        "id": "sj-1",
        "number": "SJ-ONE",
        "lines": [{"line_idx": 0, "line_id": line_id, "qty": qty, "name": "Barang Uji"}],
    }]
    order["loading_confirmations"] = [{
        "id": "ld-one",
        "shipment_id": "sj-1",
        "surat_jalan_id": "sj-1",
        "lines": [{"line_idx": 0, "qty": qty}],
    }]
    order["delivery_proofs"] = [
        {"id": "p1", "shipment_id": "sj-1"},
        {"id": "p2", "shipment_id": "sj-1"},
    ]
    return order


@pytest.mark.anyio
async def test_uninitialized_product_confirms_without_stock_mutation(mongo):
    master_id = "master-no-opening"
    await mongo.master_items.insert_one({"id": master_id, "item_name": "Tanpa Saldo", "unit": "PCS"})
    order = _one_line_order("ord-no-opening", master_id, 100)
    await mongo.orders.insert_one(dict(order))
    result = await _confirm(order["id"], "sj-1", "act-no-opening")
    assert result.status_code == 200, result.text
    assert result.json()["order"]["delivery_confirmations"][0]["qty"] == 100
    assert await mongo.product_stock_state.count_documents({"master_item_id": master_id}) == 0
    assert await mongo.stock_movements.count_documents({"master_item_id": master_id}) == 0
    again = await _confirm(order["id"], "sj-1", "act-no-opening")
    assert again.status_code == 200, again.text
    stored = await mongo.orders.find_one({"id": order["id"]}, {"_id": 0, "delivery_confirmations": 1})
    assert len(stored["delivery_confirmations"]) == 1
    assert await mongo.stock_movements.count_documents({"master_item_id": master_id}) == 0


@pytest.mark.anyio
async def test_mixed_initialized_sj_reserves_only_the_eligible_line(mongo):
    ready = "master-ready-mix"
    missing = "master-missing-mix"
    await mongo.master_items.insert_one({"id": ready, "item_name": "Siap", "unit": "PCS"})
    await mongo.master_items.insert_one({"id": missing, "item_name": "Kosong", "unit": "PCS"})
    await initialize_opening_balance(
        mongo, ready,
        {"unprinted_qty": 0, "printed_qty": 1000, "base_uom": "PCS"},
        user={"email": "admin@test.local"},
    )
    order = _order("ord-mix-opening")
    order["line_items"] = [
        {
            "master_item_id": ready,
            "production_process": "print",
            "quantity": 1000,
            "qty": 1000,
            "line_id": "LA",
            "unit": "PCS",
            "name": "Siap",
        },
        {
            "master_item_id": missing,
            "production_process": "print",
            "quantity": 1000,
            "qty": 1000,
            "line_id": "LB",
            "unit": "PCS",
            "name": "Kosong",
        },
    ]
    order["shipments"] = [{
        "id": "sj-1",
        "number": "SJ-MIX",
        "lines": [
            {"line_idx": 0, "line_id": "LA", "qty": 100, "name": "Siap"},
            {"line_idx": 1, "line_id": "LB", "qty": 50, "name": "Kosong"},
        ],
    }]
    order["loading_confirmations"] = [{
        "id": "ld-mix",
        "shipment_id": "sj-1",
        "surat_jalan_id": "sj-1",
        "lines": [{"line_idx": 0, "qty": 100}, {"line_idx": 1, "qty": 50}],
    }]
    order["delivery_proofs"] = [
        {"id": "p1", "shipment_id": "sj-1"},
        {"id": "p2", "shipment_id": "sj-1"},
    ]
    await mongo.orders.insert_one(dict(order))
    result = await _confirm(order["id"], "sj-1", "act-mix-opening")
    assert result.status_code == 200, result.text
    assert result.json()["order"]["delivery_confirmations"][0]["qty"] == 150
    state = await get_product_stock_state(mongo, ready)
    assert state["printed_qty"] == 900
    assert state["total_qty"] == 900
    assert not state.get("pending_event")
    assert await mongo.product_stock_state.count_documents({"master_item_id": missing}) == 0
    movs = await mongo.stock_movements.find({"movement_type": "PENGIRIMAN"}).to_list(length=10)
    assert len(movs) == 1
    assert movs[0]["line_id"] == "LA"
    assert movs[0]["transaction_qty"] == 100
    assert movs[0]["master_item_id"] == ready
    again = await _confirm(order["id"], "sj-1", "act-mix-opening")
    assert again.status_code == 200, again.text
    assert (await get_product_stock_state(mongo, ready))["printed_qty"] == 900
    assert await mongo.stock_movements.count_documents({"movement_type": "PENGIRIMAN"}) == 1
    assert await mongo.product_stock_state.count_documents({"master_item_id": missing}) == 0


@pytest.mark.anyio
async def test_opening_predicate_variants_do_not_block_confirmation(mongo):
    cases = [
        ("ord-absent", "master-absent", None),
        ("ord-flag", "master-flag", {"initialized": False, "status": "INITIALIZED", "printed_qty": 40, "unprinted_qty": 10, "total_qty": 50}),
        ("ord-status", "master-status", {"initialized": True, "status": "DRAFT", "printed_qty": 40, "unprinted_qty": 10, "total_qty": 50}),
    ]
    for order_id, master_id, state in cases:
        await mongo.master_items.insert_one({"id": master_id, "item_name": master_id, "unit": "PCS"})
        if state is not None:
            await mongo.product_stock_state.insert_one({
                "_id": master_id,
                "master_item_id": master_id,
                **state,
            })
        order = _one_line_order(order_id, master_id, 25)
        await mongo.orders.insert_one(dict(order))
        result = await _confirm(order_id, "sj-1", f"act-{order_id}")
        assert result.status_code == 200, result.text
        assert result.json()["order"]["delivery_confirmations"][0]["qty"] == 25
        assert await mongo.stock_movements.count_documents({"master_item_id": master_id}) == 0
        if state is None:
            assert await mongo.product_stock_state.count_documents({"master_item_id": master_id}) == 0
        else:
            stored = await mongo.product_stock_state.find_one({"master_item_id": master_id})
            assert stored["printed_qty"] == 40
            assert stored["unprinted_qty"] == 10
            assert stored["total_qty"] == 50
            assert not stored.get("pending_event")


@pytest.mark.anyio
async def test_uninitialized_product_still_requires_delivery_proof(mongo):
    master_id = "master-no-proof"
    await mongo.master_items.insert_one({"id": master_id, "item_name": "Tanpa Bukti", "unit": "PCS"})
    order = _one_line_order("ord-no-proof", master_id, 80)
    order["delivery_proofs"] = [{"id": "only", "shipment_id": "sj-1"}]
    await mongo.orders.insert_one(dict(order))
    rejected = await _confirm(order["id"], "sj-1", "act-no-proof")
    assert rejected.status_code == 400, rejected.text
    stored = await mongo.orders.find_one({"id": order["id"]}, {"_id": 0, "delivery_confirmations": 1})
    assert stored["delivery_confirmations"] == []
    assert await mongo.product_stock_state.count_documents({"master_item_id": master_id}) == 0
    assert await mongo.stock_movements.count_documents({"master_item_id": master_id}) == 0


@pytest.mark.anyio
async def test_retry_after_opening_balance_does_not_backfill_stock(mongo):
    master_id = "master-later-opening"
    await mongo.master_items.insert_one({"id": master_id, "item_name": "Nanti", "unit": "PCS"})
    order = _one_line_order("ord-later-opening", master_id, 60)
    await mongo.orders.insert_one(dict(order))
    first = await _confirm(order["id"], "sj-1", "act-later")
    assert first.status_code == 200, first.text
    assert await mongo.stock_movements.count_documents({"master_item_id": master_id}) == 0
    await initialize_opening_balance(
        mongo, master_id,
        {"unprinted_qty": 0, "printed_qty": 500, "base_uom": "PCS"},
        user={"email": "admin@test.local"},
    )
    retry = await _confirm(order["id"], "sj-1", "act-later")
    assert retry.status_code == 200, retry.text
    state = await get_product_stock_state(mongo, master_id)
    assert state["printed_qty"] == 500
    assert state["total_qty"] == 500
    assert not state.get("pending_event")
    movements = await mongo.stock_movements.find(
        {"master_item_id": master_id}, {"_id": 0, "movement_type": 1},
    ).to_list(length=10)
    assert [row["movement_type"] for row in movements] == ["SALDO_AWAL"]
    stored = await mongo.orders.find_one({"id": order["id"]}, {"_id": 0, "delivery_confirmations": 1})
    assert len(stored["delivery_confirmations"]) == 1
