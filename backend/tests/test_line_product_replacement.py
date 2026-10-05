"""Focused tests: safe imported line product replacement (Path 1)."""
from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ.setdefault("MONGO_URL", "mongodb://127.0.0.1:27017")
os.environ.setdefault("DB_NAME", "eternal_packaging_test")
os.environ.setdefault("JWT_SECRET", "test-secret-key-12345678901234567890")
os.environ.setdefault("CORS_ORIGINS", "*")

from server import app, current_user  # noqa: E402
import server as server_mod  # noqa: E402
from services.line_product_replacement import (  # noqa: E402
    REASON_CROSS_TYPE,
    REASON_DOWNSTREAM,
    REASON_SAMPLE,
    evaluate_line_product_replacement,
    map_master_to_line_fields,
)


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture(autouse=True)
def bind_motor():
    from motor.motor_asyncio import AsyncIOMotorClient

    server_mod.client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    server_mod.db = server_mod.client[os.environ["DB_NAME"]]


def db():
    return server_mod.db


def _admin():
    return {
        "id": "u-admin",
        "email": "admin@eternalpackaging.com",
        "role": "admin",
        "name": "Admin",
    }


def _viewer():
    return {
        "id": "u-viewer-noreplace",
        "email": "viewer-noreplace@eternalpackaging.com",
        "role": "crm",
        "name": "Viewer",
        "permissions_override": {},
    }


async def _seed_master(aid: str, name: str, code: str, *, active: bool = True, production_process=None):
    mid = f"m-{aid}"
    doc = {
        "id": mid,
        "master_item_id": mid,
        "accurate_item_id": aid,
        "item_code": code,
        "item_name": name,
        "unit": "pcs",
        "is_active": active,
        "item_type": "INVENTORY",
    }
    if production_process:
        doc["production_process"] = production_process
    await db().master_items.delete_many({"accurate_item_id": aid})
    await db().master_items.insert_one(doc)
    return doc


async def _seed_order(**extra):
    oid = f"ord-repl-{uuid.uuid4().hex[:8]}"
    po = f"PO-REPL-{uuid.uuid4().hex[:6].upper()}"
    doc = {
        "id": oid,
        "po_number": po,
        "customer": "Fixture Customer",
        "current_stage": 0,
        "status_label": "Order Masuk",
        "sample_files": [],
        "sample_approved": False,
        "shipments": [],
        "loading_confirmations": [],
        "delivery_confirmations": [],
        "packing_info": {},
        "qty_delivered": 0,
        "delivery_status": "Belum Terkirim",
        "cancelled": False,
        "updated_at": "2026-09-17T00:00:00+00:00",
        "line_items": extra.pop("line_items", []),
        **extra,
    }
    await db().orders.delete_many({"id": oid})
    await db().orders.insert_one(doc)
    return doc


@pytest.mark.anyio
async def test_quantity_only_change_recalculates_total_atomically():
    master = await _seed_master("AID-QTY-1", "Cup Qty", "QTY-1")
    order = await _seed_order(
        total_quantity=125,
        line_items=[
            {
                "name": master["item_name"],
                "item_code": master["item_code"],
                "quantity": 100,
                "unit": "pcs",
                "production_process": "print",
                "accurate_item_id": master["accurate_item_id"],
                "master_item_id": master["id"],
            },
            {"name": "Lid", "quantity": 25, "unit": "pcs", "production_process": "non_print"},
        ],
    )
    app.dependency_overrides[current_user] = _admin
    import httpx

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        res = await client.patch(
            f"/api/sales-orders/{order['id']}/lines/0/product",
            json={"quantity": 80},
        )
    app.dependency_overrides.pop(current_user, None)
    assert res.status_code == 200, res.text
    assert res.json()["before_quantity"] == 100
    assert res.json()["after_quantity"] == 80
    fresh = await db().orders.find_one({"id": order["id"]}, {"_id": 0})
    assert fresh["line_items"][0]["quantity"] == 80
    assert fresh["line_items"][0]["accurate_item_id"] == "AID-QTY-1"
    assert fresh["total_quantity"] == 105


@pytest.mark.anyio
async def test_product_and_quantity_change_are_persisted_together():
    source = await _seed_master("AID-BOTH-1", "Cup Lama", "BOTH-1")
    target = await _seed_master("AID-BOTH-2", "Cup Baru", "BOTH-2")
    order = await _seed_order(
        total_quantity=100,
        line_items=[
            {
                "name": source["item_name"],
                "item_code": source["item_code"],
                "quantity": 100,
                "unit": "pcs",
                "production_process": "print",
                "accurate_item_id": source["accurate_item_id"],
                "master_item_id": source["id"],
            }
        ],
    )
    app.dependency_overrides[current_user] = _admin
    import httpx

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        res = await client.patch(
            f"/api/sales-orders/{order['id']}/lines/0/product",
            json={"master_item_id": target["id"], "quantity": 80},
        )
    app.dependency_overrides.pop(current_user, None)
    assert res.status_code == 200, res.text
    fresh = await db().orders.find_one({"id": order["id"]}, {"_id": 0})
    assert fresh["line_items"][0]["accurate_item_id"] == "AID-BOTH-2"
    assert fresh["line_items"][0]["quantity"] == 80
    assert fresh["total_quantity"] == 80


@pytest.mark.anyio
async def test_invalid_quantity_is_rejected_without_mutation():
    master = await _seed_master("AID-QTY-BAD", "Cup Tetap", "QTY-BAD")
    order = await _seed_order(
        total_quantity=75,
        line_items=[
            {
                "name": master["item_name"],
                "quantity": 75,
                "unit": "pcs",
                "production_process": "print",
                "accurate_item_id": master["accurate_item_id"],
                "master_item_id": master["id"],
            }
        ],
    )
    app.dependency_overrides[current_user] = _admin
    import httpx

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        res = await client.patch(
            f"/api/sales-orders/{order['id']}/lines/0/product",
            json={"quantity": 0},
        )
    app.dependency_overrides.pop(current_user, None)
    assert res.status_code == 400
    fresh = await db().orders.find_one({"id": order["id"]}, {"_id": 0})
    assert fresh["line_items"][0]["quantity"] == 75
    assert fresh["total_quantity"] == 75


@pytest.mark.anyio
async def test_print_before_sample_no_downstream_allowed():
    m1 = await _seed_master("AID-P1", "Cup Print A", "CP-A")
    m2 = await _seed_master("AID-P2", "Cup Print B", "CP-B")
    order = await _seed_order(
        line_items=[
            {
                "name": m1["item_name"],
                "item_code": m1["item_code"],
                "quantity": 1000,
                "unit": "pcs",
                "production_process": "print",
                "accurate_item_id": m1["accurate_item_id"],
                "master_item_id": m1["id"],
            }
        ]
    )
    guard = await evaluate_line_product_replacement(db(), order, 0)
    assert guard["allowed"] is True

    app.dependency_overrides[current_user] = _admin
    import httpx

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        res = await client.patch(
            f"/api/sales-orders/{order['id']}/lines/0/product",
            json={"master_item_id": m2["id"], "accurate_item_id": m2["accurate_item_id"]},
        )
    app.dependency_overrides.pop(current_user, None)
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["line_idx"] == 0
    assert body["after"]["accurate_item_id"] == "AID-P2"
    assert body["after"]["name"] == "Cup Print B"
    assert body["line"]["quantity"] == 1000
    assert body["line"]["production_process"] == "print"
    fresh = await db().orders.find_one({"id": order["id"]}, {"_id": 0})
    assert fresh["line_items"][0]["accurate_item_id"] == "AID-P2"
    assert fresh["current_stage"] == 0
    assert fresh.get("qty_delivered", 0) == 0


@pytest.mark.anyio
async def test_print_sample_uploaded_rejected():
    m1 = await _seed_master("AID-PS1", "Sample Lock Cup", "SL-1")
    m2 = await _seed_master("AID-PS2", "Other Cup", "SL-2")
    order = await _seed_order(
        sample_files=[{"id": "sf1", "name": "sample.png"}],
        line_items=[
            {
                "name": m1["item_name"],
                "item_code": m1["item_code"],
                "quantity": 500,
                "unit": "pcs",
                "production_process": "print",
                "accurate_item_id": m1["accurate_item_id"],
                "master_item_id": m1["id"],
            }
        ],
    )
    guard = await evaluate_line_product_replacement(db(), order, 0)
    assert guard["allowed"] is False
    assert guard["reason_code"] == REASON_SAMPLE

    app.dependency_overrides[current_user] = _admin
    import httpx

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        res = await client.patch(
            f"/api/sales-orders/{order['id']}/lines/0/product",
            json={"master_item_id": m2["id"]},
        )
    app.dependency_overrides.pop(current_user, None)
    assert res.status_code == 409
    detail = res.json()["detail"]
    assert detail["code"] == REASON_SAMPLE
    fresh = await db().orders.find_one({"id": order["id"]}, {"_id": 0})
    assert fresh["line_items"][0]["accurate_item_id"] == "AID-PS1"


@pytest.mark.anyio
async def test_print_no_sample_but_sj_downstream_rejected():
    m1 = await _seed_master("AID-SJ1", "SJ Cup", "SJ-1")
    m2 = await _seed_master("AID-SJ2", "Alt Cup", "SJ-2")
    order = await _seed_order(
        sample_files=[],
        shipments=[
            {
                "id": "ship1",
                "number": "SJ-1",
                "lines": [{"line_idx": 0, "qty": 100, "name": "SJ Cup"}],
            }
        ],
        line_items=[
            {
                "name": m1["item_name"],
                "item_code": m1["item_code"],
                "quantity": 1000,
                "unit": "pcs",
                "production_process": "print",
                "accurate_item_id": m1["accurate_item_id"],
                "master_item_id": m1["id"],
            }
        ],
    )
    guard = await evaluate_line_product_replacement(db(), order, 0)
    assert guard["allowed"] is False
    assert guard["reason_code"] == REASON_DOWNSTREAM

    app.dependency_overrides[current_user] = _admin
    import httpx

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        res = await client.patch(
            f"/api/sales-orders/{order['id']}/lines/0/product",
            json={"accurate_item_id": m2["accurate_item_id"]},
        )
    app.dependency_overrides.pop(current_user, None)
    assert res.status_code == 409


@pytest.mark.anyio
async def test_nonprint_no_downstream_allowed():
    m1 = await _seed_master("AID-NP1", "Box Plain", "BX-1")
    m2 = await _seed_master("AID-NP2", "Box Plain New", "BX-2")
    order = await _seed_order(
        line_items=[
            {
                "name": m1["item_name"],
                "item_code": m1["item_code"],
                "quantity": 200,
                "unit": "pcs",
                "production_process": "non_print",
                "accurate_item_id": m1["accurate_item_id"],
                "master_item_id": m1["id"],
            }
        ]
    )
    assert (await evaluate_line_product_replacement(db(), order, 0))["allowed"] is True

    app.dependency_overrides[current_user] = _admin
    import httpx

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        res = await client.patch(
            f"/api/sales-orders/{order['id']}/lines/0/product",
            json={"master_item_id": m2["id"], "accurate_item_id": m2["accurate_item_id"]},
        )
    app.dependency_overrides.pop(current_user, None)
    assert res.status_code == 200
    assert res.json()["line"]["production_process"] == "non_print"
    assert res.json()["after"]["accurate_item_id"] == "AID-NP2"


@pytest.mark.anyio
async def test_nonprint_downstream_locked():
    m1 = await _seed_master("AID-NPD1", "Lid", "LD-1")
    m2 = await _seed_master("AID-NPD2", "Lid 2", "LD-2")
    order = await _seed_order(
        loading_confirmations=[
            {"id": "lc1", "lines": [{"line_idx": 0, "qty": 50, "name": "Lid"}]}
        ],
        line_items=[
            {
                "name": m1["item_name"],
                "item_code": m1["item_code"],
                "quantity": 50,
                "unit": "pcs",
                "production_process": "non_print",
                "accurate_item_id": m1["accurate_item_id"],
                "master_item_id": m1["id"],
            }
        ],
    )
    guard = await evaluate_line_product_replacement(db(), order, 0)
    assert guard["allowed"] is False
    assert guard["reason_code"] == REASON_DOWNSTREAM

    app.dependency_overrides[current_user] = _admin
    import httpx

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        res = await client.patch(
            f"/api/sales-orders/{order['id']}/lines/0/product",
            json={"master_item_id": m2["id"]},
        )
    app.dependency_overrides.pop(current_user, None)
    assert res.status_code == 409


@pytest.mark.anyio
async def test_sample_does_not_lock_nonprint_alone():
    m1 = await _seed_master("AID-MIX-NP", "NonPrint Mix", "NPM")
    order = await _seed_order(
        sample_files=[{"id": "sf", "name": "s.png"}],
        line_items=[
            {
                "name": "Print Mix",
                "quantity": 100,
                "production_process": "print",
                "accurate_item_id": "AID-MIX-P",
            },
            {
                "name": m1["item_name"],
                "item_code": m1["item_code"],
                "quantity": 100,
                "production_process": "non_print",
                "accurate_item_id": m1["accurate_item_id"],
                "master_item_id": m1["id"],
            },
        ],
    )
    g0 = await evaluate_line_product_replacement(db(), order, 0)
    g1 = await evaluate_line_product_replacement(db(), order, 1)
    assert g0["allowed"] is False and g0["reason_code"] == REASON_SAMPLE
    assert g1["allowed"] is True


@pytest.mark.anyio
async def test_mixed_po_per_line_and_same_name_independence():
    m_a = await _seed_master("AID-SM-A", "Product X", "PX-A")
    m_b = await _seed_master("AID-SM-B", "Product X", "PX-B")
    m_y = await _seed_master("AID-SM-Y", "Product Y", "PY")
    order = await _seed_order(
        sample_files=[],
        shipments=[
            {"id": "s1", "lines": [{"line_idx": 0, "qty": 10, "name": "Product X"}]}
        ],
        line_items=[
            {
                "name": "Product X",
                "item_code": m_a["item_code"],
                "quantity": 100,
                "production_process": "print",
                "accurate_item_id": m_a["accurate_item_id"],
                "master_item_id": m_a["id"],
            },
            {
                "name": "Product X",
                "item_code": m_b["item_code"],
                "quantity": 200,
                "production_process": "non_print",
                "accurate_item_id": m_b["accurate_item_id"],
                "master_item_id": m_b["id"],
            },
        ],
    )
    assert (await evaluate_line_product_replacement(db(), order, 0))["allowed"] is False
    assert (await evaluate_line_product_replacement(db(), order, 1))["allowed"] is True

    app.dependency_overrides[current_user] = _admin
    import httpx

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        res = await client.patch(
            f"/api/sales-orders/{order['id']}/lines/1/product",
            json={"master_item_id": m_y["id"], "accurate_item_id": m_y["accurate_item_id"]},
        )
    app.dependency_overrides.pop(current_user, None)
    assert res.status_code == 200
    fresh = await db().orders.find_one({"id": order["id"]}, {"_id": 0})
    assert fresh["line_items"][0]["accurate_item_id"] == "AID-SM-A"
    assert fresh["line_items"][0]["name"] == "Product X"
    assert fresh["line_items"][1]["accurate_item_id"] == "AID-SM-Y"
    assert fresh["line_items"][1]["name"] == "Product Y"
    assert len(fresh["line_items"]) == 2
    # packing / sj / loading / delivery unchanged
    assert fresh["shipments"][0]["lines"][0]["name"] == "Product X"
    assert fresh.get("packing_info") == {}
    assert fresh.get("loading_confirmations") == []
    assert fresh.get("qty_delivered", 0) == 0


@pytest.mark.anyio
async def test_cross_type_explicit_rejected_and_same_class_ok():
    m1 = await _seed_master("AID-CT1", "Cup", "CT-1")
    m2 = await _seed_master("AID-CT2", "Cup2", "CT-2")
    order = await _seed_order(
        line_items=[
            {
                "name": m1["item_name"],
                "quantity": 10,
                "production_process": "print",
                "accurate_item_id": m1["accurate_item_id"],
                "master_item_id": m1["id"],
            }
        ]
    )
    app.dependency_overrides[current_user] = _admin
    import httpx

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        bad = await client.patch(
            f"/api/sales-orders/{order['id']}/lines/0/product",
            json={"master_item_id": m2["id"], "production_process": "non_print"},
        )
        good = await client.patch(
            f"/api/sales-orders/{order['id']}/lines/0/product",
            json={"master_item_id": m2["id"], "production_process": "print"},
        )
    app.dependency_overrides.pop(current_user, None)
    assert bad.status_code == 400
    assert bad.json()["detail"]["code"] == REASON_CROSS_TYPE
    assert good.status_code == 200
    assert good.json()["line"]["production_process"] == "print"


@pytest.mark.anyio
async def test_master_with_incompatible_production_process_rejected():
    m1 = await _seed_master("AID-IC1", "Src", "IC-1")
    m2 = await _seed_master("AID-IC2", "Tgt", "IC-2", production_process="non_print")
    order = await _seed_order(
        line_items=[
            {
                "name": m1["item_name"],
                "quantity": 10,
                "production_process": "print",
                "accurate_item_id": m1["accurate_item_id"],
                "master_item_id": m1["id"],
            }
        ]
    )
    app.dependency_overrides[current_user] = _admin
    import httpx

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        res = await client.patch(
            f"/api/sales-orders/{order['id']}/lines/0/product",
            json={"master_item_id": m2["id"]},
        )
    app.dependency_overrides.pop(current_user, None)
    assert res.status_code == 400
    assert res.json()["detail"]["code"] == REASON_CROSS_TYPE


@pytest.mark.anyio
async def test_inactive_and_unauthorized():
    m1 = await _seed_master("AID-INA1", "Active", "INA-1")
    m2 = await _seed_master("AID-INA2", "Inactive", "INA-2", active=False)
    order = await _seed_order(
        line_items=[
            {
                "name": m1["item_name"],
                "quantity": 10,
                "production_process": "print",
                "accurate_item_id": m1["accurate_item_id"],
                "master_item_id": m1["id"],
            }
        ]
    )
    import httpx

    app.dependency_overrides[current_user] = _admin
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        inactive = await client.patch(
            f"/api/sales-orders/{order['id']}/lines/0/product",
            json={"master_item_id": m2["id"]},
        )
    app.dependency_overrides[current_user] = _viewer
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        forbidden = await client.patch(
            f"/api/sales-orders/{order['id']}/lines/0/product",
            json={"master_item_id": m1["id"]},
        )
    app.dependency_overrides.pop(current_user, None)
    assert inactive.status_code == 400
    assert forbidden.status_code == 403


@pytest.mark.anyio
async def test_backend_race_guard_recheck_sj_collection():
    m1 = await _seed_master("AID-RACE1", "Race Cup", "RC-1")
    m2 = await _seed_master("AID-RACE2", "Race Cup 2", "RC-2")
    order = await _seed_order(
        line_items=[
            {
                "name": m1["item_name"],
                "quantity": 10,
                "production_process": "print",
                "accurate_item_id": m1["accurate_item_id"],
                "master_item_id": m1["id"],
            }
        ]
    )
    # Simulate SJ created in sales_deliveries after page load
    await db().sales_deliveries.insert_one(
        {
            "id": f"sd-{uuid.uuid4().hex[:6]}",
            "po_id": order["id"],
            "is_cancelled": False,
            "line_items": [{"line_idx": 0, "qty": 5, "name": "Race Cup"}],
        }
    )
    guard = await evaluate_line_product_replacement(db(), order, 0)
    assert guard["allowed"] is False
    assert guard["reason_code"] == REASON_DOWNSTREAM

    app.dependency_overrides[current_user] = _admin
    import httpx

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        res = await client.patch(
            f"/api/sales-orders/{order['id']}/lines/0/product",
            json={"master_item_id": m2["id"]},
        )
    app.dependency_overrides.pop(current_user, None)
    assert res.status_code == 409
    await db().sales_deliveries.delete_many({"po_id": order["id"]})


@pytest.mark.anyio
async def test_stale_ids_replaced_and_mapping():
    mapped = map_master_to_line_fields(
        {
            "id": "m-new",
            "accurate_item_id": "AID-NEW",
            "item_code": "NEW-1",
            "item_name": "New Name",
            "unit": "box",
        }
    )
    assert mapped == {
        "name": "New Name",
        "item_code": "NEW-1",
        "unit": "box",
        "accurate_item_id": "AID-NEW",
        "master_item_id": "m-new",
    }


@pytest.mark.anyio
async def test_stock_claim_locks_line():
    m1 = await _seed_master("AID-STK1", "Stock Cup", "ST-1")
    m2 = await _seed_master("AID-STK2", "Stock Cup 2", "ST-2")
    order = await _seed_order(
        line_items=[
            {
                "name": m1["item_name"],
                "quantity": 10,
                "production_process": "non_print",
                "accurate_item_id": m1["accurate_item_id"],
                "master_item_id": m1["id"],
            }
        ]
    )
    await db().stock_movements.insert_one(
        {
            "id": f"mov-{uuid.uuid4().hex[:6]}",
            "order_id": order["id"],
            "line_idx": 0,
            "master_item_id": m1["id"],
        }
    )
    guard = await evaluate_line_product_replacement(db(), order, 0)
    assert guard["allowed"] is False
    assert "stock_movement" in guard["downstream_sources"]

    app.dependency_overrides[current_user] = _admin
    import httpx

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        res = await client.patch(
            f"/api/sales-orders/{order['id']}/lines/0/product",
            json={"master_item_id": m2["id"]},
        )
    app.dependency_overrides.pop(current_user, None)
    assert res.status_code == 409
    await db().stock_movements.delete_many({"order_id": order["id"]})
