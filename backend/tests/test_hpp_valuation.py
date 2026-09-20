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
    COST_BASIS_UOM,
    FINANCIAL_INVENTORY_MODEL_VERSION,
    INSUFFICIENT_COST_LAYER,
    InsufficientCostLayerError,
    METHOD_AVG,
    METHOD_FIFO,
    REASON_FINANCIAL_COST_BASIS_MISMATCH,
    REASON_FINANCIAL_MODEL_VERSION_MISMATCH,
    REASON_HPP_VERSION_NOT_ACTIVE,
    REASON_INVALID_FINANCIAL_UOM_VERSION,
    REASON_LEGACY_HPP_VERSION,
    REASON_NO_ACTIVE_HPP_VERSION,
    RECOG_GR,
    RECOG_PO,
    assess_version_financial_readiness,
    build_acquisition_events,
    fifo_consume,
    fifo_layers_from_events,
    moving_average_from_events,
    normalize_method,
    normalize_recognition,
)
from services.inventory_accounting import (
    REASON_ALLOCATION_NOT_FINANCIAL,
    REASON_NON_INVENTORY_ITEM,
    REASON_PURCHASE_ORDER_RECOGNITION,
    financial_layer_view,
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
            mid = f"m-{uuid.uuid4().hex[:8]}"
            req = (await c.post("/api/purchase-requests", json={
                "lines": [{"item_name": "Film", "requested_qty": 8, "accurate_item_id": aid, "master_item_id": mid}],
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


def _layer_event(event_id, source_id, qty, cost, ident="m:1", ts="2026-01-01"):
    return {
        "event_id": event_id,
        "source_type": "goods_receipt",
        "source_id": source_id,
        "source_line_id": event_id,
        "item_identity": ident,
        "recognized_at": ts,
        "recognized_qty": qty,
        "unit_cost": cost,
        "total_cost": qty * cost,
        "item_name": "X",
        "item_code": "",
        "master_item_id": "1",
        "accurate_item_id": "",
    }


@pytest.mark.anyio
async def test_fifo_example_and_insufficient_layer():
    events = [
        _layer_event("l1", "gr1", 500, 400, ts="2026-01-01"),
        _layer_event("l2", "gr2", 800, 450, ts="2026-01-02"),
    ]
    layers = fifo_layers_from_events(events)
    updated, cogs = fifo_consume(layers, [{"issue_id": "i1", "issued_at": "2026-02-01", "item_identity": "m:1", "qty": 1200}])
    assert sum(row["total_hpp"] for row in cogs) == 515000
    remain = {row["layer_id"]: row["remaining_qty"] for row in updated}
    assert remain["l1"] == 0
    assert remain["l2"] == 100
    with pytest.raises(InsufficientCostLayerError) as exc:
        fifo_consume(
            fifo_layers_from_events([_layer_event("only", "gr1", 500, 400)]),
            [{"issue_id": "i2", "issued_at": "2026-02-01", "item_identity": "m:1", "qty": 600}],
        )
    assert exc.value.code == INSUFFICIENT_COST_LAYER
    assert INSUFFICIENT_COST_LAYER in str(exc.value)


@pytest.mark.anyio
async def test_moving_average_insufficient_and_same_date_acquisition_first():
    with pytest.raises(InsufficientCostLayerError) as exc:
        moving_average_from_events(
            [_layer_event("only", "gr1", 500, 400)],
            [{"issue_id": "i2", "issued_at": "2026-02-01", "item_identity": "m:1", "qty": 600}],
        )
    assert exc.value.code == INSUFFICIENT_COST_LAYER

    same = "2026-03-01"
    snaps, cogs, state = moving_average_from_events(
        [_layer_event("acq", "gr1", 500, 400, ts=same)],
        [{"issue_id": "iss", "issued_at": same, "item_identity": "m:1", "qty": 200}],
    )
    assert cogs[0]["unit_hpp"] == 400.0
    assert cogs[0]["qty"] == 200
    assert abs(state["m:1"]["qty"] - 300) < 0.0001
    assert snaps


@pytest.mark.anyio
async def test_cancelled_po_and_gr_are_excluded_from_acquisition():
    po_active = {
        "id": "po-a", "po_number": "PB-A", "order_date": "2026-01-10",
        "lines": [{"line_id": "ln-a", "ordered_qty": 10, "unit_price": 50, "item_name": "Z", "master_item_id": "m1"}],
    }
    po_cancelled = {
        "id": "po-c", "po_number": "PB-C", "order_date": "2026-01-11", "cancelled": True,
        "lines": [{"line_id": "ln-c", "ordered_qty": 7, "unit_price": 80, "item_name": "Z", "master_item_id": "m1"}],
    }
    po_events = build_acquisition_events(
        method="FIFO",
        recognition_point="purchase_order",
        purchase_orders=[po_active, po_cancelled],
        goods_receipts=[],
        allocations=[],
    )
    assert [e["source_id"] for e in po_events] == ["po-a"]

    gr_active = {
        "id": "gr-a", "receipt_number": "GR-A", "purchase_order_id": "po-a", "receipt_date": "2026-01-12",
        "lines": [{"line_id": "gl-a", "purchase_order_line_id": "ln-a", "received_qty": 4}],
    }
    gr_cancelled = {
        "id": "gr-c", "receipt_number": "GR-C", "purchase_order_id": "po-a", "receipt_date": "2026-01-13",
        "cancelled": True,
        "lines": [{"line_id": "gl-c", "purchase_order_line_id": "ln-a", "received_qty": 3}],
    }
    gr_events = build_acquisition_events(
        method="FIFO",
        recognition_point="goods_receipt",
        purchase_orders=[po_active],
        goods_receipts=[gr_active, gr_cancelled],
        allocations=[],
    )
    assert [e["source_id"] for e in gr_events] == ["gr-a"]
    assert gr_events[0]["recognized_qty"] == 4


@pytest.mark.anyio
async def test_legacy_and_invalid_versions_are_not_financially_ready():
    ready, reason = assess_version_financial_readiness(None)
    assert ready is False
    assert reason == REASON_NO_ACTIVE_HPP_VERSION

    legacy = {"id": "legacy-v1", "status": "ACTIVE", "method": "FIFO"}
    ready, reason = assess_version_financial_readiness(legacy)
    assert ready is False
    assert reason == REASON_LEGACY_HPP_VERSION

    invalid_uom = {
        "status": "ACTIVE",
        "financial_uom_normalization_valid": False,
        "financial_inventory_model_version": FINANCIAL_INVENTORY_MODEL_VERSION,
        "cost_basis_uom": COST_BASIS_UOM,
    }
    ready, reason = assess_version_financial_readiness(invalid_uom)
    assert ready is False
    assert reason == REASON_INVALID_FINANCIAL_UOM_VERSION

    wrong_model = dict(invalid_uom)
    wrong_model["financial_uom_normalization_valid"] = True
    wrong_model["financial_inventory_model_version"] = 99
    ready, reason = assess_version_financial_readiness(wrong_model)
    assert ready is False
    assert reason == REASON_FINANCIAL_MODEL_VERSION_MISMATCH

    missing_model = {
        "status": "ACTIVE",
        "financial_uom_normalization_valid": True,
        "cost_basis_uom": COST_BASIS_UOM,
    }
    ready, reason = assess_version_financial_readiness(missing_model)
    assert ready is False
    assert reason == REASON_LEGACY_HPP_VERSION

    wrong_basis = {
        "status": "ACTIVE",
        "financial_uom_normalization_valid": True,
        "financial_inventory_model_version": FINANCIAL_INVENTORY_MODEL_VERSION,
        "cost_basis_uom": "TRANSACTION_UOM",
    }
    ready, reason = assess_version_financial_readiness(wrong_basis)
    assert ready is False
    assert reason == REASON_FINANCIAL_COST_BASIS_MISMATCH

    missing_basis = {
        "status": "ACTIVE",
        "financial_uom_normalization_valid": True,
        "financial_inventory_model_version": FINANCIAL_INVENTORY_MODEL_VERSION,
    }
    ready, reason = assess_version_financial_readiness(missing_basis)
    assert ready is False
    assert reason == REASON_LEGACY_HPP_VERSION

    not_active = {
        "status": "SUPERSEDED",
        "financial_uom_normalization_valid": True,
        "financial_inventory_model_version": FINANCIAL_INVENTORY_MODEL_VERSION,
        "cost_basis_uom": COST_BASIS_UOM,
    }
    ready, reason = assess_version_financial_readiness(not_active)
    assert ready is False
    assert reason == REASON_HPP_VERSION_NOT_ACTIVE

    valid = {
        "status": "ACTIVE",
        "financial_uom_normalization_valid": True,
        "financial_inventory_model_version": FINANCIAL_INVENTORY_MODEL_VERSION,
        "cost_basis_uom": COST_BASIS_UOM,
    }
    ready, reason = assess_version_financial_readiness(valid)
    assert ready is True
    assert reason is None


@pytest.mark.anyio
async def test_financial_layer_view_uses_base_uom_and_rejects_non_financial():
    carton = {
        "recognized_qty": 1,
        "unit_cost": 500000,
        "financial_inventory_eligible": True,
        "financial_inventory_item_key": "m:m1",
        "canonical_source_type": "purchase_goods_receipt",
        "canonical_source_id": "gr-1",
        "base_qty": 1000,
        "base_uom": "PCS",
        "base_unit_cost": 500,
        "total_cost": 500000,
        "source_type": "goods_receipt",
    }
    view = financial_layer_view(carton)
    assert view["financial_inventory_eligible"] is True
    assert view["qty"] == 1000
    assert view["uom"] == "PCS"
    assert view["unit_cost"] == 500
    assert view["base_qty"] == 1000
    assert view["base_unit_cost"] == 500
    assert view["total_cost"] == 500000
    assert "recognized_qty" not in view

    same = {
        "recognized_qty": 100,
        "unit_cost": 500,
        "financial_inventory_eligible": True,
        "financial_inventory_item_key": "m:m1",
        "canonical_source_type": "purchase_goods_receipt",
        "canonical_source_id": "gr-2",
        "base_qty": 100,
        "base_uom": "PCS",
        "base_unit_cost": 500,
        "total_cost": 50000,
        "source_type": "goods_receipt",
    }
    same_view = financial_layer_view(same)
    assert same_view["qty"] == 100
    assert same_view["unit_cost"] == 500
    assert same_view["total_cost"] == 50000

    po_view = financial_layer_view({
        "source_type": "purchase_order",
        "canonical_source_type": "purchase_order",
        "recognized_qty": 10,
        "unit_cost": 50,
        "financial_inventory_eligible": False,
    })
    assert po_view["financial_inventory_eligible"] is False
    assert po_view["financial_ineligibility_reason"] == REASON_PURCHASE_ORDER_RECOGNITION
    assert "qty" not in po_view

    alloc_view = financial_layer_view({
        "source_type": "goods_receipt_allocation",
        "canonical_source_type": "goods_receipt_allocation",
        "recognized_qty": 10,
        "unit_cost": 50,
        "base_qty": 10,
        "base_unit_cost": 50,
        "financial_inventory_eligible": False,
    })
    assert alloc_view["financial_inventory_eligible"] is False
    assert alloc_view["financial_ineligibility_reason"] == REASON_ALLOCATION_NOT_FINANCIAL
    assert "qty" not in alloc_view

    service_view = financial_layer_view({
        "source_type": "goods_receipt",
        "canonical_source_type": "purchase_goods_receipt",
        "financial_inventory_eligible": False,
        "financial_ineligibility_reason": REASON_NON_INVENTORY_ITEM,
        "recognized_qty": 1,
        "unit_cost": 100000,
        "base_qty": 1,
        "base_unit_cost": 100000,
        "total_cost": 100000,
    })
    assert service_view["financial_inventory_eligible"] is False
    assert service_view["financial_ineligibility_reason"] == REASON_NON_INVENTORY_ITEM
    assert "qty" not in service_view
