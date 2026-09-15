"""Focused tests: Jadwal Pengiriman → 1/2-armada one-stage create+optimize Optimasi Rute bridge."""
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
    FLEET_LABEL_1,
    FLEET_LABEL_2,
    PERM_CREATE,
    PERM_EDIT_OWN,
    PERM_MANAGE,
    PERM_OPTIMIZE,
    PERM_VIEW,
    PRIORITY_ID_NORMAL,
    PRIORITY_ID_URGENT,
)
from services.independent_route.store import ensure_indexes
from services.independent_route.delivery_bridge import order_is_urgent
from services.independent_route.settings import load_settings, save_settings

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


@pytest.fixture(autouse=True)
async def seed_large_armada_capacity(indexes):
    cfg = await load_settings(server.db)
    cfg["armada_1_capacity_qty_sj"] = 10_000_000
    cfg["armada_2_capacity_qty_sj"] = 10_000_000
    await save_settings(server.db, cfg, {"id": "bridge-test", "name": "bridge-test"})


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
    await server.db[COL_PLANS].delete_many({"name": {"$regex": "Pengiriman|BRIDGE|IRO-TEST|Armada"}})
    await server.db[COL_STOPS].delete_many({"sales_order_id": {"$regex": f"^{PREFIX}"}})
    await server.db[COL_STOPS].delete_many({"display_name": {"$regex": f"^{PREFIX}|Customer"}})


def _order(
    oid,
    *,
    driver_id=None,
    driver_name=None,
    lat=None,
    lng=None,
    date="2026-09-11",
    address="Jl Test",
    sj_ready=True,
    priority="normal",
    **extra,
):
    loc = {}
    if lat is not None and lng is not None:
        loc = {"lat": lat, "lng": lng, "formatted_address": address}
    lc = {}
    if driver_id:
        lc["driver_user_id"] = driver_id
    if driver_name:
        lc["driver_name"] = driver_name
    sid = f"sj-{oid}"
    if sj_ready:
        shipments = [{"id": sid, "number": f"SJ-{oid}", "lines": [{"name": "Box", "qty": 100}]}]
        surat_jalan_files = [{
            "id": sid,
            "shipment_id": sid,
            "name": f"SJ-{oid}.pdf",
            "storage_path": f"/sj/{oid}.pdf",
            "lines": [{"name": "Box", "qty": 100}],
        }]
    else:
        shipments = [{"id": sid, "number": f"SJ-{oid}"}]
        surat_jalan_files = []
    return {
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
        "shipments": shipments,
        "surat_jalan_files": surat_jalan_files,
        "loading_checklist": lc,
        "delivery_location": loc,
        "address": address,
        "priority": priority,
        **extra,
    }


async def _ensure_driver(driver_id, name="Route Driver"):
    doc = {
        "id": driver_id,
        "user_id": driver_id,
        "email": f"{driver_id}@example.com",
        "name": name,
        "role": "driver",
        "status": "active",
    }
    await server.db.users.delete_many({"id": driver_id})
    await server.db.users.insert_one(dict(doc))
    return doc


@pytest.mark.anyio
async def test_review_zero_writes_and_filter_ids():
    await _clean()
    user = await _upsert_user(_user())
    client = await client_for(user)
    a, b, c = f"{PREFIX}-A", f"{PREFIX}-B", f"{PREFIX}-C"
    await server.db.orders.insert_many([
        _order(a, driver_id="drv-a", driver_name="Driver A", lat=-6.2, lng=106.8),
        _order(b, driver_id="drv-a", driver_name="Driver A", lat=-6.21, lng=106.81),
        _order(c, driver_id="drv-b", driver_name="Driver B", lat=-6.22, lng=106.82),
    ])
    before_plans = {d["id"] for d in await server.db[COL_PLANS].find({}, {"_id": 0, "id": 1}).to_list(5000)}
    before_stops = {d["id"] for d in await server.db[COL_STOPS].find({}, {"_id": 0, "id": 1}).to_list(5000)}
    r = await client.post("/api/route-optimizer/from-schedule/review", json={"order_ids": [a, b]})
    assert r.status_code == 200, r.text
    ids = [row["sales_order_id"] for row in r.json()["rows"]]
    assert ids == [a, b]
    assert c not in ids
    after_plans = {d["id"] for d in await server.db[COL_PLANS].find({}, {"_id": 0, "id": 1}).to_list(5000)}
    after_stops = {d["id"] for d in await server.db[COL_STOPS].find({}, {"_id": 0, "id": 1}).to_list(5000)}
    assert after_plans == before_plans
    assert after_stops == before_stops


@pytest.mark.anyio
async def test_eligibility_sj_location_driver_optional_urgency():
    await _clean()
    user = await _upsert_user(_user())
    client = await client_for(user)
    ok_no = f"{PREFIX}-OK-NODRV"
    ok_drv = f"{PREFIX}-OK-DRV"
    urg = f"{PREFIX}-URG"
    bad_loc = f"{PREFIX}-BAD-LOC"
    bad_sj = f"{PREFIX}-BAD-SJ"
    await server.db.orders.insert_many([
        _order(ok_no, lat=-6.2, lng=106.8),
        _order(ok_drv, driver_id="drv-a", driver_name="A", lat=-6.21, lng=106.81),
        _order(urg, lat=-6.22, lng=106.82, priority="Urgent"),
        _order(bad_loc, address="Jl No Coords"),
        _order(bad_sj, lat=-6.23, lng=106.83, sj_ready=False),
    ])
    r = await client.post(
        "/api/route-optimizer/from-schedule/review",
        json={"order_ids": [ok_no, ok_drv, urg, bad_loc, bad_sj]},
    )
    assert r.status_code == 200, r.text
    by_id = {row["sales_order_id"]: row for row in r.json()["rows"]}
    assert by_id[ok_no]["eligible"] is True
    assert by_id[ok_drv]["eligible"] is True
    assert by_id[urg]["eligible"] is True and by_id[urg]["urgent"] is True
    assert by_id[ok_no]["urgent"] is False
    assert by_id[bad_loc]["eligible"] is False
    assert by_id[bad_sj]["eligible"] is False
    assert "driver" not in (by_id[ok_no].get("reason") or "").lower()
    assert order_is_urgent({"priority": "urgent"}) is True
    assert order_is_urgent({"priority": "NORMAL"}) is False


@pytest.mark.anyio
async def test_preview_fleet_count_1_and_2_no_writes_maps_and_invariants():
    await _clean()
    user = await _upsert_user(_user())
    client = await client_for(user)
    ids = [f"{PREFIX}-P{i}" for i in range(1, 5)]
    await server.db.orders.insert_many([
        _order(ids[0], lat=-6.20, lng=106.80, priority="urgent"),
        _order(ids[1], lat=-6.21, lng=106.81, priority="normal"),
        _order(ids[2], lat=-7.25, lng=112.75, priority="urgent"),
        _order(ids[3], lat=-7.26, lng=112.76, priority="normal"),
        _order(f"{PREFIX}-BAD-SJ", lat=-6.22, lng=106.82, sj_ready=False),
        _order(f"{PREFIX}-BAD-LOC", address="No coords"),
        _order(f"{PREFIX}-NO-DRV", lat=-6.23, lng=106.83),  # no driver — still eligible
    ])
    plans_before = await server.db[COL_PLANS].count_documents({})
    stops_before = await server.db[COL_STOPS].count_documents({})
    orders_before = await server.db.orders.find_one({"id": ids[0]})

    # invalid fleet_count
    bad = await client.post(
        "/api/route-optimizer/from-schedule/preview-fleets",
        json={"order_ids": ids, "fleet_count": 3},
    )
    assert bad.status_code == 400

    # fleet_count=1
    one = await client.post(
        "/api/route-optimizer/from-schedule/preview-fleets",
        json={"order_ids": ids + [f"{PREFIX}-BAD-SJ", f"{PREFIX}-BAD-LOC", f"{PREFIX}-NO-DRV"], "fleet_count": 1},
    )
    assert one.status_code == 200, one.text
    body1 = one.json()
    assert body1["fleet_count"] == 1
    assert len(body1["fleets"]) == 1
    assert body1["fleets"][0]["label"] == FLEET_LABEL_1
    f1_ids = body1["fleets"][0]["order_ids"]
    assert set(f1_ids) == set(body1["eligible_order_ids"])
    assert f"{PREFIX}-NO-DRV" in f1_ids
    assert f"{PREFIX}-BAD-SJ" not in f1_ids
    assert f"{PREFIX}-BAD-LOC" not in f1_ids
    assert len(f1_ids) == len(set(f1_ids))
    assert body1["fleets"][0]["google_maps_url"]
    assert "google.com/maps" in body1["fleets"][0]["google_maps_url"]
    assert any(s.get("urgent") and s.get("priority_id") == PRIORITY_ID_URGENT for s in body1["fleets"][0]["stops"])
    assert any((not s.get("urgent")) and s.get("priority_id") == PRIORITY_ID_NORMAL for s in body1["fleets"][0]["stops"])

    # fleet_count=2
    two = await client.post(
        "/api/route-optimizer/from-schedule/preview-fleets",
        json={"order_ids": ids + [f"{PREFIX}-NO-DRV"], "fleet_count": 2},
    )
    assert two.status_code == 200, two.text
    body2 = two.json()
    assert body2["fleet_count"] == 2
    assert len(body2["fleets"]) == 2
    a1, a2 = body2["fleets"][0]["order_ids"], body2["fleets"][1]["order_ids"]
    assert a1 and a2
    assert set(a1) & set(a2) == set()
    assert set(a1) | set(a2) == set(body2["eligible_order_ids"])
    assert body2["fleets"][0]["google_maps_url"] and body2["fleets"][1]["google_maps_url"]
    for fl in body2["fleets"]:
        seqs = [s.get("stop_sequence") for s in fl["stops"]]
        assert seqs == sorted(seqs)

    # zero writes
    assert await server.db[COL_PLANS].count_documents({}) == plans_before
    assert await server.db[COL_STOPS].count_documents({}) == stops_before
    orders_after = await server.db.orders.find_one({"id": ids[0]})
    assert orders_after == orders_before

    # create-fleet-pair retired
    gone = await client.post(
        "/api/route-optimizer/from-schedule/create-fleet-pair",
        json={
            "order_ids": ids,
            "preview_fingerprint": "x",
            "driver_user_id_armada_1": "a",
            "driver_user_id_armada_2": "b",
        },
    )
    assert gone.status_code in (404, 405, 422)


@pytest.mark.anyio
async def test_preview_one_requires_one_two_requires_two():
    await _clean()
    user = await _upsert_user(_user())
    client = await client_for(user)
    a = f"{PREFIX}-ONLY"
    await server.db.orders.insert_one(_order(a, lat=-6.2, lng=106.8))
    one = await client.post(
        "/api/route-optimizer/from-schedule/preview-fleets",
        json={"order_ids": [a], "fleet_count": 1},
    )
    assert one.status_code == 200, one.text
    assert len(one.json()["fleets"]) == 1
    two = await client.post(
        "/api/route-optimizer/from-schedule/preview-fleets",
        json={"order_ids": [a], "fleet_count": 2},
    )
    assert two.status_code == 400
    assert "2" in (two.json().get("detail") or str(two.json()))


@pytest.mark.anyio
async def test_preview_one_fleet_does_not_need_partition_two_uses_it(monkeypatch):
    await _clean()
    user = await _upsert_user(_user())
    client = await client_for(user)
    ids = [f"{PREFIX}-G{i}" for i in range(3)]
    await server.db.orders.insert_many([
        _order(ids[0], lat=-6.20, lng=106.80, priority="urgent"),
        _order(ids[1], lat=-6.50, lng=107.00),
        _order(ids[2], lat=-7.25, lng=112.75, priority="urgent"),
    ])
    calls = {"n": 0}
    import services.independent_route.delivery_bridge as bridge
    real = bridge.partition_into_two_fleets

    def wrapped(stops):
        calls["n"] += 1
        return real(stops)

    monkeypatch.setattr(bridge, "partition_into_two_fleets", wrapped)
    one = await client.post(
        "/api/route-optimizer/from-schedule/preview-fleets",
        json={"order_ids": ids, "fleet_count": 1},
    )
    assert one.status_code == 200
    assert calls["n"] == 0
    two = await client.post(
        "/api/route-optimizer/from-schedule/preview-fleets",
        json={"order_ids": ids, "fleet_count": 2},
    )
    assert two.status_code == 200
    assert calls["n"] == 1


@pytest.mark.anyio
async def test_legacy_optimize_order_ids_success_and_safety():
    """Legacy POST /optimize accepts {order_ids}; SJ/coords/driver gates; no auto-driver."""
    await _clean()
    user = await _upsert_user(_user())
    client = await client_for(user)
    await _ensure_driver("drv-leg", "Legacy Driver")

    ok_id = f"{PREFIX}-LEG-OK"
    no_sj = f"{PREFIX}-LEG-NOSJ"
    no_loc = f"{PREFIX}-LEG-NOLOC"
    no_drv = f"{PREFIX}-LEG-NODRV"
    await server.db.orders.insert_many([
        _order(ok_id, driver_id="drv-leg", driver_name="Legacy Driver", lat=-6.2, lng=106.8),
        _order(no_sj, driver_id="drv-leg", driver_name="Legacy Driver", lat=-6.21, lng=106.81, sj_ready=False),
        _order(no_loc, driver_id="drv-leg", driver_name="Legacy Driver", address="No coords"),
        _order(no_drv, driver_name="Name Only", lat=-6.22, lng=106.82),
    ])

    # Two-fleet review still allows missing order driver (eligible for preview).
    review = await client.post(
        "/api/route-optimizer/from-schedule/review",
        json={"order_ids": [ok_id, no_sj, no_loc, no_drv]},
    )
    assert review.status_code == 200
    by_id = {r["sales_order_id"]: r for r in review.json()["rows"]}
    assert by_id[ok_id]["eligible"] is True
    assert by_id[no_drv]["eligible"] is True
    assert by_id[no_sj]["eligible"] is False
    assert "Surat Jalan" in (by_id[no_sj].get("reason") or "")
    assert by_id[no_loc]["eligible"] is False
    assert "Koordinat" in (by_id[no_loc].get("reason") or "")

    opt = await client.post(
        "/api/route-optimizer/from-schedule/optimize",
        json={"order_ids": [ok_id, no_sj, no_loc, no_drv]},
    )
    assert opt.status_code == 200, opt.text
    body = opt.json()
    assert "results" in body and "summary" in body and "review" in body
    assert body["ok"] is True
    assert len(body["results"]) == 1
    assert body["results"][0]["ok"] is True
    assert ok_id in body["results"][0]["created_order_ids"]
    assert body["results"][0]["driver_user_id"] == "drv-leg"
    assert body["results"][0].get("route_id")

    rev_rows = {r["sales_order_id"]: r for r in body["review"]["rows"]}
    assert rev_rows[no_sj]["eligible"] is False
    assert "Surat Jalan" in (rev_rows[no_sj].get("reason") or "")
    assert rev_rows[no_loc]["eligible"] is False
    assert "Koordinat" in (rev_rows[no_loc].get("reason") or "")
    assert rev_rows[no_drv]["eligible"] is False
    assert "Belum ada driver" in (rev_rows[no_drv].get("reason") or "")

    # No automatic driver selection / no source-order driver rewrite.
    od = await server.db.orders.find_one({"id": no_drv}, {"_id": 0, "loading_checklist": 1})
    assert not (od.get("loading_checklist") or {}).get("driver_user_id")
    ok_order = await server.db.orders.find_one({"id": ok_id}, {"_id": 0, "loading_checklist": 1})
    assert (ok_order.get("loading_checklist") or {}).get("driver_user_id") == "drv-leg"

    routes = await server.db[COL_PLANS].find(
        {"driver_user_id": "drv-leg", "source_type": "delivery_schedule"},
        {"_id": 0, "id": 1, "fleet_group_id": 1},
    ).to_list(20)
    assert len(routes) == 1
    assert not routes[0].get("fleet_group_id")

    # Idempotent retry — no duplicate route / stops.
    again = await client.post(
        "/api/route-optimizer/from-schedule/optimize",
        json={"order_ids": [ok_id]},
    )
    assert again.status_code == 200, again.text
    assert await server.db[COL_PLANS].count_documents(
        {"driver_user_id": "drv-leg", "source_type": "delivery_schedule", **{
            "$or": [{"deleted_at": {"$exists": False}}, {"deleted_at": None}],
        }}
    ) == 1
    assert await server.db[COL_STOPS].count_documents(
        {"sales_order_id": ok_id, "$or": [{"deleted_at": {"$exists": False}}, {"deleted_at": None}]}
    ) == 1


@pytest.mark.anyio
async def test_legacy_and_preview_do_not_interfere():
    await _clean()
    user = await _upsert_user(_user())
    client = await client_for(user)
    await _ensure_driver("drv-leg2", "Legacy2")
    leg = f"{PREFIX}-COEX-LEG"
    p1, p2 = f"{PREFIX}-COEX-P1", f"{PREFIX}-COEX-P2"
    await server.db.orders.insert_many([
        _order(leg, driver_id="drv-leg2", driver_name="Legacy2", lat=-6.2, lng=106.8),
        _order(p1, lat=-6.5, lng=107.0),
        _order(p2, lat=-0.95, lng=100.35),
    ])
    leg_opt = await client.post("/api/route-optimizer/from-schedule/optimize", json={"order_ids": [leg]})
    assert leg_opt.status_code == 200, leg_opt.text
    leg_rid = leg_opt.json()["results"][0]["route_id"]
    plans_before = await server.db[COL_PLANS].count_documents({})
    prev = await client.post(
        "/api/route-optimizer/from-schedule/preview-fleets",
        json={"order_ids": [p1, p2], "fleet_count": 2},
    )
    assert prev.status_code == 200, prev.text
    assert await server.db[COL_PLANS].count_documents({}) == plans_before
    still = await server.db[COL_PLANS].find_one({"id": leg_rid}, {"_id": 0, "deleted_at": 1})
    assert still and not still.get("deleted_at")
    o = await server.db.orders.find_one({"id": leg}, {"_id": 0, "loading_checklist": 1})
    assert (o.get("loading_checklist") or {}).get("driver_user_id") == "drv-leg2"


@pytest.mark.anyio
async def test_manual_single_route_still_works():
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



@pytest.mark.anyio
async def test_create_fleets_one_and_two_persist_driverless_listable():
    """POST /optimize with fleet_count creates 1 or 2 driverless persisted routes."""
    await _clean()
    user = await _upsert_user(_user())
    client = await client_for(user)
    ids = [f"{PREFIX}-C{i}" for i in range(1, 5)]
    await server.db.orders.insert_many([
        _order(ids[0], lat=-6.20, lng=106.80, priority="urgent"),
        _order(ids[1], lat=-6.21, lng=106.81, priority="normal"),
        _order(ids[2], lat=-7.25, lng=112.75, priority="urgent"),
        _order(ids[3], lat=-7.26, lng=112.76, priority="normal"),
        _order(f"{PREFIX}-BAD-SJ", lat=-6.22, lng=106.82, sj_ready=False),
        _order(f"{PREFIX}-NO-DRV", lat=-6.23, lng=106.83),
    ])
    order_before = await server.db.orders.find_one({"id": ids[0]}, {"_id": 0, "loading_checklist": 1})

    bad = await client.post(
        "/api/route-optimizer/from-schedule/optimize",
        json={"order_ids": ids, "fleet_count": 3},
    )
    assert bad.status_code == 400

    one = await client.post(
        "/api/route-optimizer/from-schedule/optimize",
        json={"order_ids": ids + [f"{PREFIX}-BAD-SJ", f"{PREFIX}-NO-DRV"], "fleet_count": 1},
    )
    assert one.status_code == 200, one.text
    body1 = one.json()
    assert body1["ok"] is True
    assert body1["fleet_count"] == 1
    assert body1["created_route_count"] == 1
    assert len(body1["routes"]) == 1
    assert body1["routes"][0]["label"] == FLEET_LABEL_1
    assert body1["routes"][0].get("fleet_group_id") in (None, "")
    r1_ids = set(body1["routes"][0]["order_ids"])
    assert r1_ids == set(body1["eligible_order_ids"])
    assert f"{PREFIX}-NO-DRV" in r1_ids
    assert f"{PREFIX}-BAD-SJ" not in r1_ids
    rid1 = body1["routes"][0]["route_id"]

    # Persisted + listable + openable; driver unset
    plan = await server.db[COL_PLANS].find_one({"id": rid1}, {"_id": 0})
    assert plan and not plan.get("deleted_at")
    assert plan.get("driver_user_id") in (None, "")
    assert plan.get("source_type") == "delivery_schedule"
    listed = await client.get("/api/route-optimizer/routes")
    assert listed.status_code == 200
    list_ids = {r["id"] for r in listed.json()["routes"]}
    assert rid1 in list_ids
    detail = await client.get(f"/api/route-optimizer/routes/{rid1}")
    assert detail.status_code == 200
    assert detail.json()["route"]["id"] == rid1
    assert len(detail.json()["route"]["stops"]) == len(r1_ids)

    # Source-order driver unchanged
    order_after = await server.db.orders.find_one({"id": ids[0]}, {"_id": 0, "loading_checklist": 1})
    assert order_after == order_before

    # Soft-delete first create so second create can use same POs
    await client.delete(f"/api/route-optimizer/routes/{rid1}")

    two = await client.post(
        "/api/route-optimizer/from-schedule/optimize",
        json={"order_ids": ids + [f"{PREFIX}-NO-DRV"], "fleet_count": 2},
    )
    assert two.status_code == 200, two.text
    body2 = two.json()
    assert body2["fleet_count"] == 2
    assert body2["created_route_count"] == 2
    assert len(body2["routes"]) == 2
    assert body2["routes"][0]["label"] == FLEET_LABEL_1
    assert body2["routes"][1]["label"] == FLEET_LABEL_2
    g = body2["fleet_group_id"]
    assert g
    assert body2["routes"][0]["fleet_group_id"] == g
    assert body2["routes"][1]["fleet_group_id"] == g
    a1, a2 = set(body2["routes"][0]["order_ids"]), set(body2["routes"][1]["order_ids"])
    assert a1 and a2 and a1.isdisjoint(a2)
    assert a1 | a2 == set(body2["eligible_order_ids"])
    for route in body2["routes"]:
        assert not route.get("route", {}).get("driver_user_id")

    listed2 = await client.get("/api/route-optimizer/routes")
    list_ids2 = {r["id"] for r in listed2.json()["routes"]}
    assert body2["routes"][0]["route_id"] in list_ids2
    assert body2["routes"][1]["route_id"] in list_ids2


@pytest.mark.anyio
async def test_create_fleets_idempotent_and_no_orphan_on_failure(monkeypatch):
    await _clean()
    user = await _upsert_user(_user())
    client = await client_for(user)
    ids = [f"{PREFIX}-I{i}" for i in range(3)]
    await server.db.orders.insert_many([
        _order(ids[0], lat=-6.20, lng=106.80, priority="urgent"),
        _order(ids[1], lat=-6.50, lng=107.00),
        _order(ids[2], lat=-7.25, lng=112.75, priority="urgent"),
    ])

    first = await client.post(
        "/api/route-optimizer/from-schedule/optimize",
        json={"order_ids": ids, "fleet_count": 2},
    )
    assert first.status_code == 200, first.text
    body = first.json()
    route_ids = [r["route_id"] for r in body["routes"]]
    assert len(route_ids) == 2

    again = await client.post(
        "/api/route-optimizer/from-schedule/optimize",
        json={"order_ids": ids, "fleet_count": 2},
    )
    assert again.status_code == 200, again.text
    again_body = again.json()
    assert again_body.get("reused") is True
    assert {r["route_id"] for r in again_body["routes"]} == set(route_ids)
    active = await server.db[COL_PLANS].count_documents(
        {"id": {"$in": route_ids}, "$or": [{"deleted_at": {"$exists": False}}, {"deleted_at": None}]}
    )
    assert active == 2

    # Partial failure: Armada 2 optimize fails → no orphan active routes from the attempt
    await _clean()
    await server.db.orders.insert_many([
        _order(ids[0], lat=-6.20, lng=106.80, priority="urgent"),
        _order(ids[1], lat=-6.50, lng=107.00),
        _order(ids[2], lat=-7.25, lng=112.75, priority="urgent"),
    ])
    import services.independent_route.delivery_bridge as bridge
    real_opt = bridge.store.optimize
    calls = {"n": 0}

    async def flaky_optimize(db, route_id, payload, actor, perms):
        calls["n"] += 1
        if calls["n"] >= 2:
            raise RuntimeError("simulated armada-2 optimize failure")
        return await real_opt(db, route_id, payload, actor, perms)

    monkeypatch.setattr(bridge.store, "optimize", flaky_optimize)
    before_ids = set(await server.db[COL_PLANS].distinct("id", {"deleted_at": None}))
    fail = await client.post(
        "/api/route-optimizer/from-schedule/optimize",
        json={"order_ids": ids, "fleet_count": 2},
    )
    assert fail.status_code >= 400
    after = await server.db[COL_PLANS].find(
        {"deleted_at": None, "source_type": "delivery_schedule", "name": {"$regex": "^Armada"}},
        {"_id": 0, "id": 1},
    ).to_list(20)
    # No active incomplete Armada pair left from failed attempt
    assert after == [] or all(doc["id"] in before_ids for doc in after)


@pytest.mark.anyio
async def test_create_one_no_partition_two_partitions(monkeypatch):
    await _clean()
    user = await _upsert_user(_user())
    client = await client_for(user)
    ids = [f"{PREFIX}-P{i}" for i in range(3)]
    await server.db.orders.insert_many([
        _order(ids[0], lat=-6.20, lng=106.80, priority="urgent"),
        _order(ids[1], lat=-6.50, lng=107.00),
        _order(ids[2], lat=-7.25, lng=112.75, priority="urgent"),
    ])
    calls = {"n": 0}
    import services.independent_route.delivery_bridge as bridge
    real = bridge.partition_into_two_fleets

    def wrapped(stops):
        calls["n"] += 1
        return real(stops)

    monkeypatch.setattr(bridge, "partition_into_two_fleets", wrapped)
    one = await client.post(
        "/api/route-optimizer/from-schedule/optimize",
        json={"order_ids": ids, "fleet_count": 1},
    )
    assert one.status_code == 200, one.text
    assert calls["n"] == 0
    # cleanup for second call
    for r in one.json()["routes"]:
        await client.delete(f"/api/route-optimizer/routes/{r['route_id']}")
    two = await client.post(
        "/api/route-optimizer/from-schedule/optimize",
        json={"order_ids": ids, "fleet_count": 2},
    )
    assert two.status_code == 200, two.text
    assert calls["n"] == 1


@pytest.mark.anyio
async def test_create_two_requires_two_eligible():
    await _clean()
    user = await _upsert_user(_user())
    client = await client_for(user)
    a = f"{PREFIX}-ONLY2"
    await server.db.orders.insert_one(_order(a, lat=-6.2, lng=106.8))
    one = await client.post(
        "/api/route-optimizer/from-schedule/optimize",
        json={"order_ids": [a], "fleet_count": 1},
    )
    assert one.status_code == 200, one.text
    two = await client.post(
        "/api/route-optimizer/from-schedule/optimize",
        json={"order_ids": [a], "fleet_count": 2},
    )
    assert two.status_code == 400


@pytest.mark.anyio
async def test_expand_atoms_use_per_sj_effective_location():
    from services.independent_route.fleet_capacity import expand_eligible_to_sj_atoms
    loc_a = {"lat": -6.2, "lng": 106.8}
    loc_b = {"lat": -6.3, "lng": 106.9}
    loc_c = {"lat": -6.4, "lng": 107.0}
    row = {
        "sales_order_id": "po-1",
        "lat": loc_a["lat"],
        "lng": loc_a["lng"],
        "planning_snapshot": {"lat": loc_a["lat"], "lng": loc_a["lng"], "address": "PO-A"},
        "sj_units": [
            {"shipment_id": "s1", "sj_number": "SJ1", "sj_load_qty": 100},
            {"shipment_id": "s2", "sj_number": "SJ2", "sj_load_qty": 100, "delivery_location": loc_b},
            {"shipment_id": "s3", "sj_number": "SJ3", "sj_load_qty": 100, "delivery_location": loc_c},
        ],
    }
    atoms = expand_eligible_to_sj_atoms([row])
    by_id = {a["shipment_id"]: a for a in atoms}
    assert by_id["s1"]["lat"] == -6.2 and by_id["s1"]["lng"] == 106.8
    assert by_id["s2"]["lat"] == -6.3 and by_id["s2"]["lng"] == 106.9
    assert by_id["s3"]["lat"] == -6.4 and by_id["s3"]["lng"] == 107.0
    assert by_id["s2"]["planning_snapshot"]["lat"] == -6.3
    invalid_row = {
        "sales_order_id": "po-2",
        "lat": loc_a["lat"],
        "lng": loc_a["lng"],
        "planning_snapshot": {"lat": loc_a["lat"], "lng": loc_a["lng"]},
        "sj_units": [
            {"shipment_id": "s4", "sj_number": "SJ4", "sj_load_qty": 50, "delivery_location": {"lat": -6.9}},
            {"shipment_id": "s5", "sj_number": "SJ5", "sj_load_qty": 50, "delivery_location": {"lat": 0, "lng": 0}},
        ],
    }
    inv = expand_eligible_to_sj_atoms([invalid_row])
    assert all(a["lat"] == -6.2 and a["lng"] == 106.8 for a in inv)

    sj_only = {
        "sales_order_id": "po-3",
        "lat": None,
        "lng": None,
        "planning_snapshot": {},
        "sj_units": [
            {"shipment_id": "s6", "sj_number": "SJ6", "sj_load_qty": 10, "delivery_location": loc_b},
        ],
    }
    only = expand_eligible_to_sj_atoms([sj_only])
    assert only[0]["lat"] == -6.3 and only[0]["lng"] == 106.9


@pytest.mark.anyio
async def test_preview_fleets_uses_per_sj_coordinates():
    await _clean()
    user = await _upsert_user(_user())
    client = await client_for(user)
    oid = f"{PREFIX}-MULTI-SJ"
    loc_a = {"lat": -6.20, "lng": 106.80, "formatted_address": "PO-A"}
    loc_b = {"lat": -6.31, "lng": 106.91, "formatted_address": "SJ-B"}
    loc_c = {"lat": -6.42, "lng": 107.02, "formatted_address": "SJ-C"}
    s1, s2 = f"sj-{oid}-1", f"sj-{oid}-2"
    order = _order(oid, lat=-6.20, lng=106.80, address="PO-A")
    order["delivery_location"] = loc_a
    order["shipments"] = [
        {"id": s1, "number": "SJ-B", "lines": [{"name": "Box", "qty": 100}], "delivery_location": loc_b},
        {"id": s2, "number": "SJ-C", "lines": [{"name": "Box", "qty": 100}], "delivery_location": loc_c},
    ]
    order["surat_jalan_files"] = [
        {"id": s1, "shipment_id": s1, "name": "SJ-B.pdf", "storage_path": f"/sj/{s1}.pdf", "lines": [{"name": "Box", "qty": 100}]},
        {"id": s2, "shipment_id": s2, "name": "SJ-C.pdf", "storage_path": f"/sj/{s2}.pdf", "lines": [{"name": "Box", "qty": 100}]},
    ]
    await server.db.orders.insert_one(order)
    sj_only_id = f"{PREFIX}-SJ-ONLY"
    sj_only = _order(sj_only_id, address="No PO pin")
    sj_only["delivery_location"] = {}
    sj_only["shipments"][0]["delivery_location"] = loc_b
    await server.db.orders.insert_one(sj_only)

    review = await client.post(
        "/api/route-optimizer/from-schedule/review",
        json={"order_ids": [oid, sj_only_id]},
    )
    assert review.status_code == 200, review.text
    by_id = {row["sales_order_id"]: row for row in review.json()["rows"]}
    assert by_id[oid]["eligible"] is True
    assert by_id[sj_only_id]["eligible"] is True

    preview = await client.post(
        "/api/route-optimizer/from-schedule/preview-fleets",
        json={"order_ids": [oid], "fleet_count": 1},
    )
    assert preview.status_code == 200, preview.text
    stops = preview.json()["fleets"][0]["stops"]
    by_ship = {s.get("shipment_id") or s.get("assignment_id"): s for s in stops}
    assert by_ship[s1]["lat"] == loc_b["lat"]
    assert by_ship[s1]["lng"] == loc_b["lng"]
    assert by_ship[s2]["lat"] == loc_c["lat"]
    assert by_ship[s2]["lng"] == loc_c["lng"]
    fresh = await server.db.orders.find_one({"id": oid}, {"_id": 0})
    assert fresh["delivery_location"]["lat"] == loc_a["lat"]
