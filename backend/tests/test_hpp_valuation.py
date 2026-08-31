"""HPP FIFO / RATA-RATA, recognition points, historical recalc, query boundary."""
import os
import sys
import uuid
from pathlib import Path

import pytest
import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("MONGO_URL", "mongodb://127.0.0.1:27017")
os.environ.setdefault("DB_NAME", "eternal_packaging_test")
os.environ.setdefault("JWT_SECRET", "test-secret-key-12345678901234567890")
os.environ.setdefault("CORS_ORIGINS", "*")

import server
from server import app, current_user
from services.hpp_valuation import (
    COGS_UNAVAILABLE_REASON,
    METHOD_AVG,
    METHOD_FIFO,
    RECOG_GR,
    RECOG_PO,
    build_acquisition_events,
    fifo_consume,
    fifo_layers_from_events,
    moving_average_from_events,
    normalize_method,
    normalize_recognition,
)
from services.current_stock import build_stock_rows
from fastapi import HTTPException


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture(autouse=True)
def bind_motor():
    from motor.motor_asyncio import AsyncIOMotorClient
    server.client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    server.db = server.client[os.environ["DB_NAME"]]


@pytest.fixture(autouse=True)
async def indexes(bind_motor):
    db = server.db
    await db.hpp_cost_versions.create_index("id", unique=True)
    await db.hpp_cost_layers.create_index([("calculation_version", 1), ("layer_id", 1)], unique=True)
    await db.purchase_requests.create_index("request_number", unique=True)
    await db.purchase_orders.create_index("po_number", unique=True)
    await db.purchase_goods_receipts.create_index("receipt_number", unique=True)
    await db.purchase_invoices.create_index("invoice_number", unique=True)
    await db.purchase_payments.create_index("payment_number", unique=True)


@pytest.mark.anyio
async def test_settings_values():
    assert normalize_method("FIFO") == METHOD_FIFO
    assert normalize_method("RATA-RATA") == METHOD_AVG
    assert normalize_recognition("Saat Pesanan Pembelian") == RECOG_PO
    assert normalize_recognition("Saat Penerimaan Barang") == RECOG_GR
    with pytest.raises(HTTPException):
        normalize_method("LIFO")
    with pytest.raises(HTTPException):
        normalize_recognition("saat bayar")


@pytest.mark.anyio
async def test_fifo_earliest_consumed_and_tie_order():
    events = [
        {"event_id": "b", "source_type": "purchase_order", "source_id": "po2", "source_line_id": "l2",
         "item_identity": "a:1", "recognized_at": "2026-01-01", "recognized_qty": 5, "unit_cost": 20, "total_cost": 100,
         "item_name": "X", "item_code": "", "master_item_id": "", "accurate_item_id": "1"},
        {"event_id": "a", "source_type": "purchase_order", "source_id": "po1", "source_line_id": "l1",
         "item_identity": "a:1", "recognized_at": "2026-01-01", "recognized_qty": 5, "unit_cost": 10, "total_cost": 50,
         "item_name": "X", "item_code": "", "master_item_id": "", "accurate_item_id": "1"},
    ]
    layers = fifo_layers_from_events(events)
    # sort as recalc would
    layers.sort(key=lambda l: (l["recognized_at"], l["source_id"], l["source_line_id"], l["layer_id"]))
    updated, cogs = fifo_consume(layers, [{"issue_id": "i1", "issued_at": "2026-02-01", "item_identity": "a:1", "qty": 6}])
    assert cogs[0]["unit_hpp"] == 10
    assert cogs[0]["qty"] == 5
    assert cogs[1]["unit_hpp"] == 20
    assert cogs[1]["qty"] == 1
    remain = {l["layer_id"]: l["remaining_qty"] for l in updated}
    assert remain["a"] == 0
    assert remain["b"] == 4
    again, _ = fifo_consume(layers, [{"issue_id": "i1", "issued_at": "2026-02-01", "item_identity": "a:1", "qty": 6}])
    assert [l["remaining_qty"] for l in again] == [l["remaining_qty"] for l in updated]


@pytest.mark.anyio
async def test_weighted_average_and_issue_at_point_in_time():
    events = [
        {"event_id": "e1", "source_type": "purchase_order", "source_id": "p1", "source_line_id": "l1",
         "item_identity": "m:1", "recognized_at": "2026-01-01", "recognized_qty": 10, "unit_cost": 100, "total_cost": 1000,
         "item_name": "Y", "item_code": "", "master_item_id": "1", "accurate_item_id": ""},
        {"event_id": "e2", "source_type": "purchase_order", "source_id": "p2", "source_line_id": "l2",
         "item_identity": "m:1", "recognized_at": "2026-03-01", "recognized_qty": 10, "unit_cost": 200, "total_cost": 2000,
         "item_name": "Y", "item_code": "", "master_item_id": "1", "accurate_item_id": ""},
    ]
    issues = [{"issue_id": "out1", "issued_at": "2026-02-01", "item_identity": "m:1", "qty": 4}]
    snaps, cogs, state = moving_average_from_events(events, issues)
    assert cogs[0]["unit_hpp"] == 100.0
    assert abs(state["m:1"]["qty"] - 16) < 0.0001
    empty_snaps, empty_cogs, empty_state = moving_average_from_events([], [])
    assert empty_snaps == [] and empty_cogs == [] and empty_state == {}


@pytest.mark.anyio
async def test_no_double_recognition_in_event_builder():
    po = {
        "id": "po1", "po_number": "PB-1", "order_date": "2026-01-10", "created_at": "2026-01-10T00:00:00+00:00",
        "lines": [{"line_id": "ln1", "ordered_qty": 10, "unit_price": 50, "item_name": "Z", "accurate_item_id": "9", "master_item_id": ""}],
    }
    gr = {
        "id": "gr1", "receipt_number": "GR-1", "purchase_order_id": "po1", "receipt_date": "2026-01-12",
        "lines": [{"line_id": "gl1", "purchase_order_line_id": "ln1", "received_qty": 10}],
    }
    po_events = build_acquisition_events(method="FIFO", recognition_point="purchase_order", purchase_orders=[po], goods_receipts=[gr], allocations=[])
    assert len(po_events) == 1
    assert po_events[0]["source_type"] == "purchase_order"
    assert po_events[0]["unit_cost"] == 50
    gr_events = build_acquisition_events(method="FIFO", recognition_point="goods_receipt", purchase_orders=[po], goods_receipts=[gr], allocations=[])
    assert len(gr_events) == 1
    assert gr_events[0]["source_type"] == "goods_receipt"
    assert gr_events[0]["recognized_qty"] == 10
    assert gr_events[0]["unit_cost"] == 50
    partial = dict(gr)
    partial["lines"] = [{"line_id": "gl1", "purchase_order_line_id": "ln1", "received_qty": 3}]
    partial2 = {"id": "gr2", "receipt_number": "GR-2", "purchase_order_id": "po1", "receipt_date": "2026-01-13",
                "lines": [{"line_id": "gl2", "purchase_order_line_id": "ln1", "received_qty": 4}]}
    two = build_acquisition_events(method="FIFO", recognition_point="goods_receipt", purchase_orders=[po], goods_receipts=[partial, partial2], allocations=[])
    assert [e["recognized_qty"] for e in two] == [3, 4]


@pytest.mark.anyio
async def test_hpp_recalc_switches_and_preserves_sources_and_stock():
    app.dependency_overrides[current_user] = lambda: {"role": "admin", "name": "Admin", "email": "a@x"}
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
            aid = f"hpp{uuid.uuid4().hex[:8]}"
            req = (await c.post("/api/purchase-requests", json={
                "lines": [{"item_name": "Film", "requested_qty": 8, "accurate_item_id": aid}],
            })).json()["request"]
            po = (await c.post("/api/purchase-orders", json={
                "request_id": req["id"], "supplier": "S",
                "lines": [{"request_line_id": req["lines"][0]["line_id"], "ordered_qty": 8, "unit_price": 2500}],
            })).json()["order"]
            await c.post("/api/purchase-goods-receipts", json={
                "purchase_order_id": po["id"],
                "lines": [{"purchase_order_line_id": po["lines"][0]["line_id"], "received_qty": 5}],
            })
            src_po = await server.db.purchase_orders.find_one({"id": po["id"]}, {"_id": 0})
            stock_before = build_stock_rows([], [], [], [], purchase_source="MANUAL_FALLBACK")

            ident = f"a:{aid}"
            r1 = await c.put("/api/settings/hpp", json={"method": "FIFO", "recognition_point": "purchase_order"})
            assert r1.status_code == 200, r1.text
            assert r1.json()["settings"]["last_recalculation_status"] == "SUCCESS"
            q1 = (await c.get("/api/hpp/cost-query", params={"item_identity": ident})).json()
            assert q1["method"] == "FIFO"
            assert q1["recognition_point"] == "purchase_order"
            assert q1["cogs_available"] is False
            assert q1["cogs_unavailable_reason"] == COGS_UNAVAILABLE_REASON
            assert q1["acquisitions"][0]["quantity"] == 8
            assert q1["acquisitions"][0]["unit_hpp"] == 2500

            r2 = await c.put("/api/settings/hpp", json={"method": "RATA-RATA", "recognition_point": "purchase_order"})
            assert r2.status_code == 200
            r3 = await c.put("/api/settings/hpp", json={"method": "RATA-RATA", "recognition_point": "goods_receipt"})
            assert r3.status_code == 200
            q3 = (await c.get("/api/hpp/cost-query", params={"item_identity": ident})).json()
            assert q3["recognition_point"] == "goods_receipt"
            assert q3["acquisitions"]
            assert q3["acquisitions"][0]["quantity"] == 5
            r4 = await c.put("/api/settings/hpp", json={"method": "FIFO", "recognition_point": "goods_receipt"})
            assert r4.status_code == 200
            r4b = await c.post("/api/settings/hpp/recalculate", json={})
            assert r4b.status_code == 200
            q4 = (await c.get("/api/hpp/cost-query", params={"item_identity": ident})).json()
            q4b = (await c.get("/api/hpp/cost-query", params={"item_identity": ident})).json()
            assert q4["acquisitions"] == q4b["acquisitions"]

            src_after = await server.db.purchase_orders.find_one({"id": po["id"]}, {"_id": 0})
            assert src_after["lines"][0]["unit_price"] == src_po["lines"][0]["unit_price"]
            assert src_after["po_number"] == src_po["po_number"]
            stock_after = build_stock_rows([], [], [], [], purchase_source="MANUAL_FALLBACK")
            assert stock_after == stock_before
            assert q4["active_version"] == r4b.json()["settings"]["active_version"]
            versions = await server.db.hpp_cost_versions.find({}, {"_id": 0}).to_list(50)
            actives = [v for v in versions if v.get("status") == "ACTIVE"]
            assert len(actives) == 1
    finally:
        app.dependency_overrides.pop(current_user, None)


@pytest.mark.anyio
async def test_failed_rebuild_keeps_prior_version(monkeypatch):
    from services import hpp_valuation as hv
    app.dependency_overrides[current_user] = lambda: {"role": "admin", "name": "Admin"}
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
            ok = await c.put("/api/settings/hpp", json={"method": "FIFO", "recognition_point": "goods_receipt"})
            assert ok.status_code == 200
            active = ok.json()["settings"]["active_version"]

            async def boom(*a, **k):
                raise RuntimeError("forced fail")

            monkeypatch.setattr(hv, "_load_source_docs", boom)
            bad = await c.put("/api/settings/hpp", json={"method": "RATA-RATA", "recognition_point": "purchase_order"})
            assert bad.status_code == 500
            cur = (await c.get("/api/settings/hpp")).json()["settings"]
            assert cur["last_recalculation_status"] == "FAILED"
            assert cur["active_version"] == active
            assert cur["method"] == "FIFO"
            ver = await server.db.hpp_cost_versions.find_one({"id": active}, {"_id": 0})
            assert ver["status"] == "ACTIVE"
            failed = await server.db.hpp_cost_versions.find({"status": "FAILED"}, {"_id": 0}).to_list(10)
            assert failed
    finally:
        app.dependency_overrides.pop(current_user, None)
        monkeypatch.undo()
