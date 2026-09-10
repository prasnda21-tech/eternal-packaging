"""Focused tests: Jadwal Pengiriman → independent Route Optimizer bridge."""
import os
import sys
import uuid
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("MONGO_URL", "mongodb://127.0.0.1:27017")
os.environ.setdefault("DB_NAME", "eternal_packaging_route_bridge_test")
os.environ.setdefault("JWT_SECRET", "test-secret-key-12345678901234567890")
os.environ.setdefault("CORS_ORIGINS", "*")
os.environ.setdefault("STORAGE_PROVIDER", "local")

import server
from server import app, current_user
from services.independent_route.constants import (
    COL_PLANS,
    COL_STOPS,
    PERM_CREATE,
    PERM_EDIT_OWN,
    PERM_MANAGE,
    PERM_OPTIMIZE,
    PERM_VIEW,
)
from services.independent_route.store import ensure_indexes

pytestmark = pytest.mark.anyio

PREFIX = "BRIDGE-TEST"


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
    await ensure_indexes(server.db)


def _user(**kw):
    uid = kw.get("id") or f"u-bridge-{uuid.uuid4().hex[:8]}"
    return {
        "id": uid,
        "user_id": uid,
        "email": kw.get("email") or f"{uid}@example.com",
        "name": kw.get("name") or "Bridge Admin",
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
    await server.db.delivery_outlets.delete_many({"id": {"$regex": f"^{PREFIX}"}})
    await server.db[COL_PLANS].delete_many({"name": {"$regex": "Pengiriman|BRIDGE|IRO-TEST"}})
    await server.db[COL_STOPS].delete_many({"sales_order_id": {"$regex": f"^{PREFIX}"}})
    await server.db[COL_STOPS].delete_many({"display_name": {"$regex": f"^{PREFIX}|Customer"}})


def _order(oid, *, driver_id=None, driver_name=None, lat=None, lng=None, date="2026-09-11", address="Jl Test", **extra):
    loc = {}
    if lat is not None and lng is not None:
        loc = {"lat": lat, "lng": lng, "formatted_address": address}
    lc = {}
    if driver_id:
        lc["driver_user_id"] = driver_id
    if driver_name:
        lc["driver_name"] = driver_name
    doc = {
        "id": oid,
        "po_number": f"PO-{oid}",
        "customer_name": extra.pop("customer_name", f"Customer {oid}"),
        "customer_id": extra.pop("customer_id", f"cust-{oid}"),
        "deadline": date,
        "scheduled_delivery_date": date,
        "delivery_status": extra.pop("delivery_status", ""),
        "cancelled": False,
        "total_quantity": 100,
        "qty_delivered": 0,
        "packing_info": {"qty_packed": 100},
        "line_items": [{"name": "Box", "quantity": 100}],
        "shipments": [{"id": f"sj-{oid}", "number": f"SJ-{oid}", "lines": [{"qty": 100}]}],
        "loading_checklist": lc,
        "delivery_location": loc,
        "address": address,
        **extra,
    }
    return doc


@pytest.mark.anyio
async def test_review_get_zero_writes_and_filter_ids():
    await _clean()
    user = await _upsert_user(_user())
    client = await client_for(user)
    a = f"{PREFIX}-A"
    b = f"{PREFIX}-B"
    c = f"{PREFIX}-C"
    await server.db.orders.insert_many([
        _order(a, driver_id="drv-a", driver_name="Driver A", lat=-6.2, lng=106.8),
        _order(b, driver_id="drv-a", driver_name="Driver A", lat=-6.21, lng=106.81),
        _order(c, driver_id="drv-b", driver_name="Driver B", lat=-6.22, lng=106.82),
    ])
    before_plan_ids = {
        d["id"] for d in await server.db[COL_PLANS].find({}, {"_id": 0, "id": 1}).to_list(5000)
    }
    before_stop_ids = {
        d["id"] for d in await server.db[COL_STOPS].find({}, {"_id": 0, "id": 1}).to_list(5000)
    }
    before_orders = await server.db.orders.count_documents({"id": {"$regex": f"^{PREFIX}"}})
    before_order_docs = {
        d["id"]: {
            "scheduled_delivery_date": d.get("scheduled_delivery_date"),
            "loading_checklist": d.get("loading_checklist"),
            "shipments": d.get("shipments"),
            "packing_info": d.get("packing_info"),
        }
        for d in await server.db.orders.find({"id": {"$in": [a, b, c]}}, {"_id": 0}).to_list(20)
    }

    r = await client.post("/api/route-optimizer/from-schedule/review", json={"order_ids": [a, b]})
    assert r.status_code == 200, r.text
    body = r.json()
    ids = [row["sales_order_id"] for row in body["rows"]]
    assert ids == [a, b]
    assert c not in ids
    assert body["summary"]["selected"] == 2

    after_plan_ids = {
        d["id"] for d in await server.db[COL_PLANS].find({}, {"_id": 0, "id": 1}).to_list(5000)
    }
    after_stop_ids = {
        d["id"] for d in await server.db[COL_STOPS].find({}, {"_id": 0, "id": 1}).to_list(5000)
    }
    # Review must not create/remove any plan/stop docs (xdist-safe set compare).
    assert after_plan_ids == before_plan_ids
    assert after_stop_ids == before_stop_ids
    assert await server.db.orders.count_documents({"id": {"$regex": f"^{PREFIX}"}}) == before_orders
    after_order_docs = {
        d["id"]: {
            "scheduled_delivery_date": d.get("scheduled_delivery_date"),
            "loading_checklist": d.get("loading_checklist"),
            "shipments": d.get("shipments"),
            "packing_info": d.get("packing_info"),
        }
        for d in await server.db.orders.find({"id": {"$in": [a, b, c]}}, {"_id": 0}).to_list(20)
    }
    assert after_order_docs == before_order_docs
    # No delivery-linked stops for reviewed IDs.
    assert await server.db[COL_STOPS].count_documents({"sales_order_id": {"$in": [a, b]}}) == 0


@pytest.mark.anyio
async def test_driver_grouping_no_mix_and_missing_driver():
    await _clean()
    user = await _upsert_user(_user())
    client = await client_for(user)
    a = f"{PREFIX}-A2"
    b = f"{PREFIX}-B2"
    d = f"{PREFIX}-D2"
    e = f"{PREFIX}-E2"
    no = f"{PREFIX}-NO"
    await server.db.orders.insert_many([
        _order(a, driver_id="drv-a", driver_name="Driver A", lat=-6.2, lng=106.8),
        _order(b, driver_id="drv-a", driver_name="Driver A", lat=-6.21, lng=106.81),
        _order(d, driver_id="drv-b", driver_name="Driver B", lat=-6.22, lng=106.82),
        _order(e, driver_id="drv-b", driver_name="Driver B", lat=-6.23, lng=106.83),
        _order(no, driver_name="Only Name", lat=-6.24, lng=106.84),  # no driver_user_id
    ])
    r = await client.post("/api/route-optimizer/from-schedule/review", json={"order_ids": [a, b, d, e, no]})
    assert r.status_code == 200, r.text
    body = r.json()
    groups = {g["driver_user_id"]: g["sales_order_ids"] for g in body["groups"]}
    assert set(groups["drv-a"]) == {a, b}
    assert set(groups["drv-b"]) == {d, e}
    assert "drv-a" in groups and "drv-b" in groups
    # Never mix
    for g in body["groups"]:
        assert len(set(g["sales_order_ids"]) & {a, b, d, e}) in (2, 0) or (
            set(g["sales_order_ids"]).issubset({a, b}) or set(g["sales_order_ids"]).issubset({d, e})
        )
    no_row = next(x for x in body["rows"] if x["sales_order_id"] == no)
    assert no_row["eligible"] is False
    assert "Belum ada driver" in (no_row.get("reason") or "")


@pytest.mark.anyio
async def test_optimize_statuses_stale_idempotent_delete_safe():
    await _clean()
    user = await _upsert_user(_user())
    client = await client_for(user)
    a = f"{PREFIX}-A3"
    b = f"{PREFIX}-B3"
    c = f"{PREFIX}-C3"
    await server.db.orders.insert_many([
        _order(a, driver_id="drv-a", driver_name="Driver A", lat=-6.200, lng=106.800, date="2026-09-11"),
        _order(b, driver_id="drv-a", driver_name="Driver A", lat=-6.210, lng=106.810, date="2026-09-11"),
        _order(c, driver_id="drv-b", driver_name="Driver B", lat=-6.220, lng=106.820, date="2026-09-11"),
    ])

    # Belum dioptimasi
    st = await client.get(f"/api/route-optimizer/from-schedule/status?ids={a},{b},{c}")
    assert st.status_code == 200
    assert st.json()["statuses"][a]["optimization_status"] == "belum_dioptimasi"

    # Create + optimize (selected subset excludes nothing of eligible)
    opt1 = await client.post("/api/route-optimizer/from-schedule/optimize", json={"order_ids": [a, b, c]})
    assert opt1.status_code == 200, opt1.text
    body1 = opt1.json()
    assert body1["ok"] is True
    assert body1["summary"]["groups_ok"] == 2
    route_ids = [g["route_id"] for g in body1["results"] if g.get("route_id")]
    assert len(route_ids) == 2

    # Same canonical routes visible on Web list
    listed = await client.get("/api/route-optimizer/routes")
    assert listed.status_code == 200
    listed_ids = {r["id"] for r in listed.json()["routes"]}
    assert set(route_ids).issubset(listed_ids)
    sources = {r["id"]: r.get("source_type") for r in listed.json()["routes"] if r["id"] in route_ids}
    assert all(v == "delivery_schedule" for v in sources.values())

    # Sudah dioptimasi
    st2 = await client.get(f"/api/route-optimizer/from-schedule/status?ids={a}")
    assert st2.json()["statuses"][a]["optimization_status"] == "sudah_dioptimasi"

    # Duplicate submit protection
    before_stops = await server.db[COL_STOPS].count_documents({"sales_order_id": {"$in": [a, b, c]}})
    opt2 = await client.post("/api/route-optimizer/from-schedule/optimize", json={"order_ids": [a, b, c]})
    assert opt2.status_code == 200, opt2.text
    after_stops = await server.db[COL_STOPS].count_documents({"sales_order_id": {"$in": [a, b, c]}})
    assert after_stops == before_stops

    # Driver change → Perlu Optimasi Ulang
    await server.db.orders.update_one({"id": a}, {"$set": {"loading_checklist.driver_user_id": "drv-x", "loading_checklist.driver_name": "Driver X"}})
    st3 = await client.get(f"/api/route-optimizer/from-schedule/status?ids={a}")
    assert st3.json()["statuses"][a]["optimization_status"] == "perlu_optimasi_ulang"

    # Date change on b
    await server.db.orders.update_one({"id": b}, {"$set": {"scheduled_delivery_date": "2026-09-12"}})
    st4 = await client.get(f"/api/route-optimizer/from-schedule/status?ids={b}")
    assert st4.json()["statuses"][b]["optimization_status"] == "perlu_optimasi_ulang"

    # Location change on c
    await server.db.orders.update_one({"id": c}, {"$set": {"delivery_location.lat": -6.5, "delivery_location.lng": 106.9}})
    st5 = await client.get(f"/api/route-optimizer/from-schedule/status?ids={c}")
    assert st5.json()["statuses"][c]["optimization_status"] == "perlu_optimasi_ulang"

    # Unrelated field does not invalidate when snapshot still matches — reset c first then change packing note
    await server.db.orders.update_one({"id": c}, {"$set": {"delivery_location.lat": -6.220, "delivery_location.lng": 106.820}})
    # Re-optimize c alone to get fresh snapshot
    await client.post("/api/route-optimizer/from-schedule/optimize", json={"order_ids": [c]})
    await server.db.orders.update_one({"id": c}, {"$set": {"packing_info.notes": "note only"}})
    st6 = await client.get(f"/api/route-optimizer/from-schedule/status?ids={c}")
    assert st6.json()["statuses"][c]["optimization_status"] == "sudah_dioptimasi"

    # Capture source order/sj/packing/loading before deletes
    order_before = await server.db.orders.find_one({"id": c}, {"_id": 0})
    route_c = st6.json()["statuses"][c]["route_id"]
    detail = await client.get(f"/api/route-optimizer/routes/{route_c}")
    stop_id = detail.json()["route"]["stops"][0]["id"]

    # Remove stop
    rm = await client.delete(f"/api/route-optimizer/routes/{route_c}/stops/{stop_id}")
    assert rm.status_code == 200, rm.text
    assert rm.json().get("source_untouched") is True
    assert (await server.db.orders.find_one({"id": c}))["shipments"] == order_before["shipments"]
    assert (await server.db.orders.find_one({"id": c}))["packing_info"]["qty_packed"] == order_before["packing_info"]["qty_packed"]
    st7 = await client.get(f"/api/route-optimizer/from-schedule/status?ids={c}")
    assert st7.json()["statuses"][c]["optimization_status"] == "belum_dioptimasi"
    # Route marked stale if still exists with version
    left = await client.get(f"/api/route-optimizer/routes/{route_c}")
    if left.status_code == 200:
        assert left.json()["route"]["optimization_stale"] is True or left.json()["route"]["stop_count"] == 0

    # Recreate then delete route
    await client.post("/api/route-optimizer/from-schedule/optimize", json={"order_ids": [c]})
    st8 = await client.get(f"/api/route-optimizer/from-schedule/status?ids={c}")
    route_c2 = st8.json()["statuses"][c]["route_id"]
    order_mid = await server.db.orders.find_one({"id": c}, {"_id": 0})
    deleted = await client.delete(f"/api/route-optimizer/routes/{route_c2}")
    assert deleted.status_code == 200, deleted.text
    assert deleted.json()["source_untouched"] is True
    order_after = await server.db.orders.find_one({"id": c}, {"_id": 0})
    assert order_after["shipments"] == order_mid["shipments"]
    assert order_after["packing_info"] == order_mid["packing_info"]
    assert order_after.get("loading_checklist") == order_mid.get("loading_checklist")
    assert order_after.get("delivery_status") == order_mid.get("delivery_status")
    assert order_after.get("scheduled_delivery_date") == order_mid.get("scheduled_delivery_date")
    st9 = await client.get(f"/api/route-optimizer/from-schedule/status?ids={c}")
    assert st9.json()["statuses"][c]["optimization_status"] == "belum_dioptimasi"


@pytest.mark.anyio
async def test_manual_route_still_works_and_perm_enforced():
    await _clean()
    user = await _upsert_user(_user(permissions_override={
        PERM_VIEW: True,
        PERM_CREATE: True,
        PERM_EDIT_OWN: True,
        PERM_OPTIMIZE: True,
    }))
    client = await client_for(user)
    created = await client.post("/api/route-optimizer/routes", json={
        "name": "IRO-TEST Manual Bridge",
        "start_mode": "MANUAL",
        "start_location": {"lat": -6.175, "lng": 106.827, "location_source": "MANUAL_COORDINATE"},
        "end_mode": "LAST_STOP",
        "strategy": "NEAREST",
    })
    assert created.status_code == 200, created.text
    assert created.json()["route"]["source_type"] == "manual"
    rid = created.json()["route"]["id"]
    stop = await client.post(f"/api/route-optimizer/routes/{rid}/stops", json={
        "display_name": "IRO-TEST Stop",
        "lat": -6.2,
        "lng": 106.8,
        "location_source": "MANUAL_COORDINATE",
        "priority_id": "p3",
    })
    assert stop.status_code == 200
    opt = await client.post(f"/api/route-optimizer/routes/{rid}/optimize", json={"revision": 2})
    assert opt.status_code == 200, opt.text

    # Permission enforcement: view-only cannot optimize from schedule
    viewer = await _upsert_user(_user(
        id="u-view-only",
        role="crm",
        permissions_override={PERM_VIEW: True},
    ))
    vclient = await client_for(viewer)
    denied = await vclient.post("/api/route-optimizer/from-schedule/optimize", json={"order_ids": [f"{PREFIX}-X"]})
    assert denied.status_code in (403, 401)


@pytest.mark.anyio
async def test_unchecked_excluded_and_canonical_id_not_name():
    await _clean()
    user = await _upsert_user(_user())
    client = await client_for(user)
    a = f"{PREFIX}-A4"
    b = f"{PREFIX}-B4"
    await server.db.orders.insert_many([
        _order(a, driver_id="drv-a", driver_name="Driver A", lat=-6.2, lng=106.8, customer_name="Same Name"),
        _order(b, driver_id="drv-a", driver_name="Driver A", lat=-6.21, lng=106.81, customer_name="Same Name"),
    ])
    # Only pass A — B must not be optimized despite same customer name
    opt = await client.post("/api/route-optimizer/from-schedule/optimize", json={"order_ids": [a]})
    assert opt.status_code == 200, opt.text
    stops_a = await server.db[COL_STOPS].count_documents({"sales_order_id": a})
    stops_b = await server.db[COL_STOPS].count_documents({"sales_order_id": b})
    assert stops_a == 1
    assert stops_b == 0
