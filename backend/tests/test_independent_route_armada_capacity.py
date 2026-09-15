"""Armada Qty SJ capacity: persistence, derivation, 1/2-armada packing, atomic SJ."""
from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("MONGO_URL", "mongodb://127.0.0.1:27017")
os.environ.setdefault("DB_NAME", "eternal_packaging_armada_capacity_test")
os.environ.setdefault("JWT_SECRET", "test-secret-key-12345678901234567890")
os.environ.setdefault("CORS_ORIGINS", "*")
os.environ.setdefault("STORAGE_PROVIDER", "local")

import server
from server import app, current_user
from services.independent_route.constants import (
    COL_PLANS,
    COL_STOPS,
    PERM_VIEW,
    SETTINGS_KEY,
)
from services.independent_route.fleet_capacity import (
    UNASSIGNED_CAPACITY_REASON,
    CapacityError,
    apply_capacity_constraint,
    assignment_id,
    order_sj_load_qty,
    parse_capacity_qty,
    parse_required_capacity_qty,
    require_saved_armada_capacities,
    sj_load_for_shipment,
    sj_units_for_order,
)
from services.independent_route.store import ensure_indexes

PREFIX = "CAP-TEST"


def _sj_row(po, sid, load, sj_number=None):
    sj_number = sj_number or sid
    return {
        "id": sid,
        "sales_order_id": po,
        "assignment_id": sid,
        "shipment_id": sid,
        "order_number": po,
        "customer_name": f"Cust {po}",
        "urgent": False,
        "sj_load_qty": load,
        "sj_number": sj_number,
        "sj_label": sj_number,
        "sj_units": [{"shipment_id": sid, "sj_number": sj_number, "sj_load_qty": load}],
        "shipment_ids": [sid],
    }


def test_same_po_two_sj_assigned_to_different_armadas():
    """PO-X SJ-1=30.000 and SJ-2=20.000 with caps 30.000/20.000 split by SJ, never by PO."""
    sj1 = _sj_row("PO-X", "SJ-1", 30000)
    sj2 = _sj_row("PO-X", "SJ-2", 20000)
    assigned, unassigned = apply_capacity_constraint([[sj1, sj2], []], [30000, 20000])
    a1 = [assignment_id(r) for r in assigned[0]]
    a2 = [assignment_id(r) for r in assigned[1]]
    assert a1 == ["SJ-1"]
    assert a2 == ["SJ-2"]
    assert unassigned == []
    assert assigned[0][0]["sales_order_id"] == "PO-X"
    assert assigned[1][0]["sales_order_id"] == "PO-X"
    assert all(len(row["sj_units"]) == 1 for fleet in assigned for row in fleet)


def test_same_po_two_sj_unexpanded_po_row_still_splits():
    po_row = {
        "id": "PO-X",
        "sales_order_id": "PO-X",
        "order_number": "PO-X",
        "customer_name": "Cust PO-X",
        "sj_load_qty": 50000,
        "sj_units": [
            {"shipment_id": "SJ-1", "sj_number": "SJ-1", "sj_load_qty": 30000},
            {"shipment_id": "SJ-2", "sj_number": "SJ-2", "sj_load_qty": 20000},
        ],
        "shipment_ids": ["SJ-1", "SJ-2"],
    }
    assigned, unassigned = apply_capacity_constraint([[po_row], []], [30000, 20000])
    assert [assignment_id(r) for r in assigned[0]] == ["SJ-1"]
    assert [assignment_id(r) for r in assigned[1]] == ["SJ-2"]
    assert unassigned == []


def test_one_sj_larger_than_both_vehicles_unassigned_no_split():
    sj1 = _sj_row("PO-X", "SJ-1", 35000)
    assigned, unassigned = apply_capacity_constraint([[sj1], []], [20000, 20000])
    assert assigned[0] == []
    assert assigned[1] == []
    assert len(unassigned) == 1
    assert assignment_id(unassigned[0]) == "SJ-1"
    assert unassigned[0]["sj_load_qty"] == 35000
    assert unassigned[0]["reason"] == UNASSIGNED_CAPACITY_REASON
    assert len(unassigned[0]["sj_units"]) == 1


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture(autouse=True)
def bind_motor():
    from motor.motor_asyncio import AsyncIOMotorClient
    server.client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    server.db = server.client[os.environ["DB_NAME"]]


@pytest.fixture
async def indexes(bind_motor):
    await ensure_indexes(server.db)


def _user(**kw):
    uid = kw.get("id") or f"u-cap-{uuid.uuid4().hex[:8]}"
    return {
        "id": uid,
        "user_id": uid,
        "email": kw.get("email") or f"{uid}@example.com",
        "name": kw.get("name") or "Cap Admin",
        "role": kw.get("role") or "admin",
        "status": "active",
        "permissions_override": kw.get("permissions_override") or {},
    }


async def _upsert_user(doc):
    await server.db.users.delete_many({"email": doc["email"]})
    await server.db.users.insert_one(dict(doc))
    return doc


async def client_for(user):
    app.dependency_overrides[current_user] = lambda: user
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


async def _clean():
    await server.db.orders.delete_many({"id": {"$regex": f"^{PREFIX}"}})
    await server.db[COL_PLANS].delete_many({"name": {"$regex": "Armada|CAP"}})
    await server.db[COL_STOPS].delete_many({"sales_order_id": {"$regex": f"^{PREFIX}"}})
    await server.db.app_settings.delete_many({"key": SETTINGS_KEY})


def _order(oid, *, lat=-6.2, lng=106.8, shipments=None, files=None, sj_ready=True, priority="normal", extra_shipments=None):
    if shipments is None:
        sid = f"sj-{oid}"
        if sj_ready:
            shipments = [{"id": sid, "number": f"SJ-{oid}", "lines": [{"line_idx": 0, "name": "Box", "qty": 100}]}]
            files = [{
                "id": sid,
                "shipment_id": sid,
                "name": f"SJ-{oid}.pdf",
                "storage_path": f"/sj/{oid}.pdf",
                "lines": [{"line_idx": 0, "name": "Box", "qty": 100}],
            }]
        else:
            shipments = [{"id": sid, "number": f"SJ-{oid}"}]
            files = []
    if extra_shipments:
        shipments = list(shipments) + list(extra_shipments)
    return {
        "id": oid,
        "po_number": f"PO-{oid}",
        "customer_name": f"Customer {oid}",
        "cancelled": False,
        "total_quantity": 999999,
        "qty_delivered": 0,
        "packing_info": {"qty_packed": 999999},
        "line_items": [{"name": "Box", "quantity": 999999}],
        "shipments": shipments,
        "surat_jalan_files": files or [],
        "delivery_location": {"lat": lat, "lng": lng, "formatted_address": "Jl Test"},
        "address": "Jl Test",
        "priority": priority,
        "delivery_status": "",
    }


def _row(oid, load, urgent=False, units=None):
    units = units or [{"shipment_id": f"sj-{oid}", "sj_number": f"SJ-{oid}", "sj_load_qty": load}]
    return {
        "id": oid,
        "sales_order_id": oid,
        "order_number": f"PO-{oid}",
        "customer_name": f"Cust {oid}",
        "urgent": urgent,
        "sj_load_qty": load,
        "sj_units": units,
        "shipment_ids": [u["shipment_id"] for u in units],
        "sj_label": units[0]["sj_number"],
    }


def test_parse_capacity_rejects_invalid():
    assert parse_capacity_qty(None) is None
    assert parse_capacity_qty("") is None
    assert parse_capacity_qty(50000) == 50000
    assert parse_capacity_qty(50000.0) == 50000
    with pytest.raises(CapacityError):
        parse_capacity_qty(-1)
    with pytest.raises(CapacityError):
        parse_capacity_qty(0)
    with pytest.raises(CapacityError):
        parse_capacity_qty("abc")
    with pytest.raises(CapacityError):
        parse_capacity_qty(float("nan"))
    with pytest.raises(CapacityError):
        parse_capacity_qty(float("inf"))
    with pytest.raises(CapacityError):
        parse_capacity_qty(10.5)
    with pytest.raises(CapacityError):
        parse_capacity_qty(True)
    with pytest.raises(CapacityError):
        parse_required_capacity_qty(None)


def test_case_a_multiline_sj_load_once():
    sh = {
        "id": "sj-1",
        "number": "SJ/001",
        "lines": [
            {"line_id": "a", "line_idx": 0, "name": "Item A", "qty": 8000},
            {"line_id": "b", "line_idx": 1, "name": "Item B", "qty": 7000},
            {"line_id": "a", "line_idx": 0, "name": "Item A alias", "qty": 8000},
        ],
    }
    order = {
        "shipments": [sh],
        "surat_jalan_files": [{
            "id": "sj-1",
            "shipment_id": "sj-1",
            "storage_path": "/x.pdf",
            "lines": sh["lines"],
        }],
    }
    assert sj_load_for_shipment(order, sh) == 15000
    units = sj_units_for_order(order)
    assert len(units) == 1
    assert units[0]["shipment_id"] == "sj-1"
    assert units[0]["sj_load_qty"] == 15000


def test_case_b_multiple_sj_same_po_separate_atoms():
    sh1 = {"id": "sj-1", "number": "SJ1", "lines": [{"line_idx": 0, "qty": 10000}]}
    sh2 = {"id": "sj-2", "number": "SJ2", "lines": [{"line_idx": 1, "qty": 15000}]}
    files = [
        {"id": "sj-1", "shipment_id": "sj-1", "storage_path": "/1.pdf", "lines": sh1["lines"]},
        {"id": "sj-2", "shipment_id": "sj-2", "storage_path": "/2.pdf", "lines": sh2["lines"]},
    ]
    order = {"shipments": [sh1, sh2], "surat_jalan_files": files, "total_quantity": 99999}
    units = sj_units_for_order(order)
    assert [u["shipment_id"] for u in units] == ["sj-1", "sj-2"]
    assert [u["sj_load_qty"] for u in units] == [10000, 15000]
    assert order_sj_load_qty(order) == 25000
    assert order_sj_load_qty(order) != 99999


def test_case_c_po_without_ready_sj_zero_load():
    order = {
        "total_quantity": 80000,
        "shipments": [{"id": "sj-x", "number": "SJ-X"}],
        "surat_jalan_files": [],
    }
    assert sj_units_for_order(order) == []
    assert order_sj_load_qty(order) == 0


def test_case_d_exact_capacity_one_armada():
    rows = [_row("A", 20000), _row("B", 10000)]
    assigned, unassigned = apply_capacity_constraint([rows], [30000])
    assert [r["sales_order_id"] for r in assigned[0]] == ["A", "B"]
    assert unassigned == []
    assert sum(r["sj_load_qty"] for r in assigned[0]) == 30000


def test_case_e_shortage_one_armada_unassigned():
    rows = [_row("A", 20000), _row("B", 15000)]
    assigned, unassigned = apply_capacity_constraint([rows], [30000])
    total = sum(r["sj_load_qty"] for r in assigned[0])
    assert total <= 30000
    assert len(assigned[0]) == 1
    assert len(unassigned) == 1
    assert unassigned[0]["reason"] == UNASSIGNED_CAPACITY_REASON
    assert total != 35000


def test_case_f_two_armada_independent_caps():
    a = _row("A", 20000)
    b = _row("B", 15000)
    c = _row("C", 10000)
    assigned, unassigned = apply_capacity_constraint([[a, c], [b]], [30000, 15000])
    load1 = sum(r["sj_load_qty"] for r in assigned[0])
    load2 = sum(r["sj_load_qty"] for r in assigned[1])
    assert load1 <= 30000
    assert load2 <= 15000
    ids = [r["sales_order_id"] for fleet in assigned for r in fleet] + [u["sales_order_id"] for u in unassigned]
    assert sorted(ids) == ["A", "B", "C"]
    for fleet in assigned:
        for row in fleet:
            for other in assigned:
                if other is fleet:
                    continue
                assert row["sales_order_id"] not in [x["sales_order_id"] for x in other]


def test_case_g_one_sj_never_split():
    x = _row("X", 10000, units=[{"shipment_id": "sj-x", "sj_number": "SJ-X", "sj_load_qty": 10000}])
    assigned, unassigned = apply_capacity_constraint([[x], []], [6000, 5000])
    assert assigned[0] == []
    assert assigned[1] == []
    assert len(unassigned) == 1
    assert unassigned[0]["sales_order_id"] == "X"
    assert unassigned[0]["reason"] == UNASSIGNED_CAPACITY_REASON
    assert unassigned[0]["sj_units"][0]["sj_load_qty"] == 10000


def test_case_h_fits_second_vehicle():
    x = _row("X", 10000)
    assigned, unassigned = apply_capacity_constraint([[x], []], [6000, 12000])
    assert assigned[0] == []
    assert [r["sales_order_id"] for r in assigned[1]] == ["X"]
    assert unassigned == []
    assert sum(r["sj_load_qty"] for r in assigned[0]) == 0


def test_case_k_one_armada_ignores_armada_2():
    rows = [_row("A", 40000), _row("B", 20000)]
    assigned, unassigned = apply_capacity_constraint([rows], [50000])
    assert sum(r["sj_load_qty"] for r in assigned[0]) <= 50000
    assert sum(r["sj_load_qty"] for r in assigned[0]) != 90000
    assert len(assigned) == 1


def test_case_l_two_armada_not_shared_pool():
    rows1 = [_row("A", 50000)]
    rows2 = [_row("B", 40000), _row("C", 10000)]
    assigned, unassigned = apply_capacity_constraint([rows1, rows2], [50000, 40000])
    assert sum(r["sj_load_qty"] for r in assigned[0]) <= 50000
    assert sum(r["sj_load_qty"] for r in assigned[1]) <= 40000
    assert any(u["sales_order_id"] == "C" for u in unassigned)


def test_require_saved_capacities():
    with pytest.raises(CapacityError):
        require_saved_armada_capacities({}, 1)
    caps = require_saved_armada_capacities(
        {"armada_1_capacity_qty_sj": 50000, "armada_2_capacity_qty_sj": 40000},
        1,
    )
    assert caps == [50000]
    caps2 = require_saved_armada_capacities(
        {"armada_1_capacity_qty_sj": 50000, "armada_2_capacity_qty_sj": 40000},
        2,
    )
    assert caps2 == [50000, 40000]


@pytest.mark.anyio
async def test_http_capacity_persist_and_auth(indexes):
    await _clean()
    admin = await _upsert_user(_user())
    client = await client_for(admin)
    missing = await client.get("/api/route-optimizer/settings/armada-capacity")
    assert missing.status_code == 200
    assert missing.json()["armada_1_capacity_qty_sj"] is None
    assert missing.json()["armada_2_capacity_qty_sj"] is None

    bad = await client.put(
        "/api/route-optimizer/settings/armada-capacity",
        json={"armada_1_capacity_qty_sj": 0, "armada_2_capacity_qty_sj": 40000},
    )
    assert bad.status_code == 400
    bad2 = await client.put(
        "/api/route-optimizer/settings/armada-capacity",
        json={"armada_1_capacity_qty_sj": -1, "armada_2_capacity_qty_sj": 40000},
    )
    assert bad2.status_code == 400
    bad3 = await client.put(
        "/api/route-optimizer/settings/armada-capacity",
        json={"armada_1_capacity_qty_sj": "abc", "armada_2_capacity_qty_sj": 40000},
    )
    assert bad3.status_code == 400

    saved = await client.put(
        "/api/route-optimizer/settings/armada-capacity",
        json={"armada_1_capacity_qty_sj": 50000, "armada_2_capacity_qty_sj": 40000},
    )
    assert saved.status_code == 200, saved.text
    assert saved.json()["armada_1_capacity_qty_sj"] == 50000
    assert saved.json()["armada_2_capacity_qty_sj"] == 40000
    again = await client.get("/api/route-optimizer/settings/armada-capacity")
    assert again.json()["armada_1_capacity_qty_sj"] == 50000
    assert again.json()["armada_2_capacity_qty_sj"] == 40000

    changed = await client.put(
        "/api/route-optimizer/settings/armada-capacity",
        json={"armada_1_capacity_qty_sj": 60000, "armada_2_capacity_qty_sj": 45000},
    )
    assert changed.json()["armada_1_capacity_qty_sj"] == 60000
    assert changed.json()["armada_2_capacity_qty_sj"] == 45000

    # Warehouse settings save must not wipe capacity.
    wh = await client.put(
        "/api/route-optimizer/settings",
        json={"warehouse": {"lat": -6.2, "lng": 106.8, "name": "Gudang Cap", "location_source": "MANUAL_COORDINATE"}},
    )
    assert wh.status_code == 200, wh.text
    kept = await client.get("/api/route-optimizer/settings/armada-capacity")
    assert kept.json()["armada_1_capacity_qty_sj"] == 60000
    assert kept.json()["armada_2_capacity_qty_sj"] == 45000

    stranger = await _upsert_user(_user(
        role="staff",
        permissions_override={PERM_VIEW: True},
    ))
    other = await client_for(stranger)
    denied = await other.put(
        "/api/route-optimizer/settings/armada-capacity",
        json={"armada_1_capacity_qty_sj": 1, "armada_2_capacity_qty_sj": 1},
    )
    assert denied.status_code in (401, 403)
    await client.aclose()
    await other.aclose()


@pytest.mark.anyio
async def test_optimize_uses_saved_capacity_and_unassigned(indexes):
    await _clean()
    admin = await _upsert_user(_user())
    client = await client_for(admin)
    a, b = f"{PREFIX}-A", f"{PREFIX}-B"
    await server.db.orders.insert_many([
        _order(
            a,
            lat=-6.20,
            lng=106.80,
            shipments=[{"id": f"sj-{a}", "number": "SJ-A", "lines": [{"line_idx": 0, "qty": 20000}]}],
            files=[{
                "id": f"sj-{a}",
                "shipment_id": f"sj-{a}",
                "storage_path": "/a.pdf",
                "lines": [{"line_idx": 0, "qty": 20000}],
            }],
        ),
        _order(
            b,
            lat=-6.21,
            lng=106.81,
            shipments=[{"id": f"sj-{b}", "number": "SJ-B", "lines": [{"line_idx": 0, "qty": 15000}]}],
            files=[{
                "id": f"sj-{b}",
                "shipment_id": f"sj-{b}",
                "storage_path": "/b.pdf",
                "lines": [{"line_idx": 0, "qty": 15000}],
            }],
        ),
    ])
    missing = await client.post(
        "/api/route-optimizer/from-schedule/optimize",
        json={"order_ids": [a, b], "fleet_count": 1},
    )
    assert missing.status_code == 400
    assert "Kapasitas" in missing.text or "kapasitas" in missing.text.lower()

    await client.put(
        "/api/route-optimizer/settings/armada-capacity",
        json={"armada_1_capacity_qty_sj": 30000, "armada_2_capacity_qty_sj": 20000},
    )
    review = await client.post("/api/route-optimizer/from-schedule/review", json={"order_ids": [a, b]})
    by_id = {row["sales_order_id"]: row for row in review.json()["rows"]}
    assert by_id[a]["sj_load_qty"] == 20000
    assert by_id[b]["sj_load_qty"] == 15000
    assert by_id[a]["sj_units"][0]["shipment_id"] == f"sj-{a}"

    one = await client.post(
        "/api/route-optimizer/from-schedule/optimize",
        json={"order_ids": [a, b], "fleet_count": 1},
    )
    assert one.status_code == 200, one.text
    body = one.json()
    assigned_load = 0
    assigned_ids = []
    for route in body["routes"]:
        assigned_ids.extend(route["order_ids"])
        assigned_load += int(route.get("sj_load_qty") or 0)
    assert assigned_load <= 30000
    assert len(body.get("unassigned") or []) == 1
    assert body["unassigned"][0]["reason"] == UNASSIGNED_CAPACITY_REASON
    sj_ids = []
    for route in body["routes"]:
        sj_ids.extend(route["order_ids"])
    for u in body["unassigned"]:
        sj_ids.append(u["sales_order_id"])
    assert sorted(set(sj_ids)) == sorted([a, b])
    assert len(sj_ids) == len(set(sj_ids))

    # 1-armada must not consume armada 2's 20.000 (combined 50.000 would fit both).
    assert assigned_load <= 30000
    await client.aclose()


@pytest.mark.anyio
async def test_optimize_same_po_two_sj_persist_on_different_armadas(indexes):
    await _clean()
    admin = await _upsert_user(_user())
    client = await client_for(admin)
    oid = f"{PREFIX}-POX"
    await server.db.orders.insert_one(_order(
        oid,
        lat=-6.20,
        lng=106.80,
        shipments=[
            {"id": "SJ-1", "number": "SJ-1", "lines": [{"line_idx": 0, "qty": 30000}]},
            {"id": "SJ-2", "number": "SJ-2", "lines": [{"line_idx": 1, "qty": 20000}]},
        ],
        files=[
            {"id": "SJ-1", "shipment_id": "SJ-1", "storage_path": "/1.pdf", "lines": [{"line_idx": 0, "qty": 30000}]},
            {"id": "SJ-2", "shipment_id": "SJ-2", "storage_path": "/2.pdf", "lines": [{"line_idx": 1, "qty": 20000}]},
        ],
    ))
    cap = await client.put(
        "/api/route-optimizer/settings/armada-capacity",
        json={"armada_1_capacity_qty_sj": 30000, "armada_2_capacity_qty_sj": 20000},
    )
    assert cap.status_code == 200, cap.text
    resp = await client.post(
        "/api/route-optimizer/from-schedule/optimize",
        json={"order_ids": [oid], "fleet_count": 2},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    by_sj = {}
    for route in data.get("routes") or []:
        for sid in route.get("shipment_ids") or []:
            by_sj[sid] = route.get("label")
        for stop in (route.get("route") or {}).get("stops") or []:
            sid = stop.get("shipment_id")
            if sid:
                by_sj[sid] = route.get("label")
    assert by_sj.get("SJ-1") and by_sj.get("SJ-2")
    assert by_sj["SJ-1"] != by_sj["SJ-2"]
    assert not data.get("unassigned")
    stops = await server.db[COL_STOPS].find({"sales_order_id": oid}, {"_id": 0, "shipment_id": 1, "route_id": 1}).to_list(20)
    assert sorted(s.get("shipment_id") for s in stops) == ["SJ-1", "SJ-2"]
    assert len({s.get("route_id") for s in stops}) == 2
    await client.aclose()


@pytest.mark.anyio
async def test_optimize_one_sj_too_large_unassigned_no_split(indexes):
    await _clean()
    admin = await _upsert_user(_user())
    client = await client_for(admin)
    oid = f"{PREFIX}-BIG"
    await server.db.orders.insert_one(_order(
        oid,
        lat=-6.20,
        lng=106.80,
        shipments=[{"id": "SJ-1", "number": "SJ-1", "lines": [{"line_idx": 0, "qty": 35000}]}],
        files=[{"id": "SJ-1", "shipment_id": "SJ-1", "storage_path": "/b.pdf", "lines": [{"line_idx": 0, "qty": 35000}]}],
    ))
    await client.put(
        "/api/route-optimizer/settings/armada-capacity",
        json={"armada_1_capacity_qty_sj": 20000, "armada_2_capacity_qty_sj": 20000},
    )
    one = await client.post(
        "/api/route-optimizer/from-schedule/optimize",
        json={"order_ids": [oid], "fleet_count": 1},
    )
    assert one.status_code == 200, one.text
    body = one.json()
    assert body.get("routes") in ([], None) or len(body.get("routes") or []) == 0
    assert len(body.get("unassigned") or []) == 1
    assert assignment_id(body["unassigned"][0]) == "SJ-1"
    assert body["unassigned"][0]["sj_load_qty"] == 35000
    assert await server.db[COL_STOPS].count_documents({"sales_order_id": oid}) == 0
    await client.aclose()
