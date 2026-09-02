"""Independent Route Optimizer HTTP + persistence tests."""
import asyncio
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
os.environ.setdefault("STORAGE_PROVIDER", "local")

import server
from server import app, current_user
from services.independent_route.constants import (
    COL_PLANS,
    COL_STOPS,
    COL_VERSIONS,
    PERM_CREATE,
    PERM_EDIT_OWN,
    PERM_MANAGE,
    PERM_OPTIMIZE,
    PERM_VIEW,
    SETTINGS_KEY,
)
from services.independent_route.store import ensure_indexes

pytestmark = pytest.mark.anyio


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


def _user(role="admin", **kw):
    uid = kw.get("id") or f"u-{role}-{uuid.uuid4().hex[:8]}"
    override = kw.get("permissions_override")
    if override is None:
        override = {} if role == "admin" else {
            PERM_VIEW: True,
            PERM_CREATE: True,
            PERM_EDIT_OWN: True,
            PERM_OPTIMIZE: True,
        }
    return {
        "id": uid,
        "email": kw.get("email") or f"{uid}@example.com",
        "name": kw.get("name") or role.title(),
        "role": role,
        "status": "active",
        "user_id": uid,
        "permissions_override": override,
    }


async def _upsert_user(doc):
    await server.db.users.delete_many({"email": doc["email"]})
    await server.db.users.insert_one(dict(doc))
    return doc


async def client_for(user):
    app.dependency_overrides[current_user] = lambda: user
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


async def _clean():
    await server.db[COL_PLANS].delete_many({"name": {"$regex": "^IRO-TEST"}})
    await server.db[COL_STOPS].delete_many({"display_name": {"$regex": "^IRO-TEST"}})
    await server.db[COL_VERSIONS].delete_many({"optimized_by.email": {"$regex": "@example.com$"}})
    await server.db.app_settings.delete_many({"key": SETTINGS_KEY})


def _stop(name, lat, lng, priority="p1", **extra):
    body = {
        "display_name": f"IRO-TEST {name}",
        "ref_kind": "ROUTE_ONLY",
        "lat": lat,
        "lng": lng,
        "location_source": "MANUAL_COORDINATE",
        "priority_id": priority,
    }
    body.update(extra)
    return body


@pytest.mark.anyio
async def test_create_add_optimize_and_history():
    await _clean()
    user = await _upsert_user(_user("driver"))
    client = await client_for(user)
    created = await client.post("/api/route-optimizer/routes", json={"name": "IRO-TEST Alpha", "start_mode": "MANUAL", "start_location": {"lat": -6.175, "lng": 106.827, "location_source": "MANUAL_COORDINATE"}, "end_mode": "LAST_STOP", "strategy": "NEAREST"})
    assert created.status_code == 200, created.text
    route_id = created.json()["route"]["id"]
    for name, lat, lng, pri in (
        ("A", -6.400, 106.850, "p1"),
        ("B", -6.180, 106.830, "p3"),
        ("C", -6.350, 107.000, "p1"),
        ("D", -6.220, 106.860, "p2"),
    ):
        r = await client.post(f"/api/route-optimizer/routes/{route_id}/stops", json=_stop(name, lat, lng, pri))
        assert r.status_code == 200, r.text
    before = await client.get(f"/api/route-optimizer/routes/{route_id}")
    stops_before = {s["id"]: (s["lat"], s["lng"], s["priority_id"]) for s in before.json()["route"]["stops"]}
    opt = await client.post(f"/api/route-optimizer/routes/{route_id}/optimize", json={"revision": before.json()["route"]["revision"]})
    assert opt.status_code == 200, opt.text
    ordered = opt.json()["result"]["ordered_stop_ids"]
    after = await client.get(f"/api/route-optimizer/routes/{route_id}")
    route = after.json()["route"]
    by_id = {s["id"]: s for s in route["stops"]}
    ranks = [by_id[sid]["priority_rank"] for sid in ordered]
    assert ranks == sorted(ranks)
    for sid, triple in stops_before.items():
        assert (by_id[sid]["lat"], by_id[sid]["lng"], by_id[sid]["priority_id"]) == triple
    versions = await client.get(f"/api/route-optimizer/routes/{route_id}/versions")
    assert versions.status_code == 200
    assert len(versions.json()["versions"]) == 1
    opt2 = await client.post(f"/api/route-optimizer/routes/{route_id}/optimize", json={"revision": route["revision"]})
    assert opt2.status_code == 200, opt2.text
    versions2 = await client.get(f"/api/route-optimizer/routes/{route_id}/versions")
    assert len(versions2.json()["versions"]) == 2
    assert opt2.json()["route"]["current_optimization_version"] == 2
    assert versions.json()["versions"][0]["id"] != versions2.json()["versions"][0]["id"] or versions2.json()["versions"][0]["optimization_version"] == 2
    await client.aclose()


@pytest.mark.anyio
async def test_get_does_not_mutate():
    await _clean()
    user = await _upsert_user(_user("driver"))
    client = await client_for(user)
    created = await client.post("/api/route-optimizer/routes", json={"name": "IRO-TEST GET", "start_mode": "MANUAL", "start_location": {"lat": -6.17, "lng": 106.82, "location_source": "MANUAL_COORDINATE"}})
    route_id = created.json()["route"]["id"]
    first = await client.get(f"/api/route-optimizer/routes/{route_id}")
    second = await client.get(f"/api/route-optimizer/routes/{route_id}")
    assert first.json()["route"]["revision"] == second.json()["route"]["revision"]
    assert first.json()["route"]["status"] == "DRAFT"
    versions = await client.get(f"/api/route-optimizer/routes/{route_id}/versions")
    assert versions.json()["versions"] == []
    await client.aclose()


@pytest.mark.anyio
async def test_missing_location_optimize_rejected():
    await _clean()
    user = await _upsert_user(_user("driver"))
    client = await client_for(user)
    created = await client.post("/api/route-optimizer/routes", json={"name": "IRO-TEST MISS", "start_mode": "MANUAL", "start_location": {"lat": -6.17, "lng": 106.82, "location_source": "MANUAL_COORDINATE"}})
    route_id = created.json()["route"]["id"]
    await client.post(f"/api/route-optimizer/routes/{route_id}/stops", json={"display_name": "IRO-TEST NoLoc", "ref_kind": "ROUTE_ONLY", "priority_id": "p3"})
    opt = await client.post(f"/api/route-optimizer/routes/{route_id}/optimize", json={})
    assert opt.status_code == 400
    assert "lokasi" in opt.json()["detail"].lower()
    await client.aclose()


@pytest.mark.anyio
async def test_reorder_priority_guard_and_same_priority():
    await _clean()
    user = await _upsert_user(_user("driver"))
    client = await client_for(user)
    created = await client.post("/api/route-optimizer/routes", json={"name": "IRO-TEST REO", "start_mode": "MANUAL", "start_location": {"lat": -6.17, "lng": 106.82, "location_source": "MANUAL_COORDINATE"}})
    route_id = created.json()["route"]["id"]
    a = (await client.post(f"/api/route-optimizer/routes/{route_id}/stops", json=_stop("RA", -6.40, 106.85, "p1"))).json()["stop"]
    b = (await client.post(f"/api/route-optimizer/routes/{route_id}/stops", json=_stop("RB", -6.18, 106.83, "p2"))).json()["stop"]
    c = (await client.post(f"/api/route-optimizer/routes/{route_id}/stops", json=_stop("RC", -6.35, 107.00, "p1"))).json()["stop"]
    detail = await client.get(f"/api/route-optimizer/routes/{route_id}")
    rev = detail.json()["route"]["revision"]
    bad = await client.post(f"/api/route-optimizer/routes/{route_id}/reorder", json={"ordered_stop_ids": [b["id"], a["id"], c["id"]], "revision": rev})
    assert bad.status_code == 400
    good = await client.post(f"/api/route-optimizer/routes/{route_id}/reorder", json={"ordered_stop_ids": [c["id"], a["id"], b["id"]], "revision": rev})
    assert good.status_code == 200, good.text
    assert good.json()["route"]["sequence_overridden"] is True
    await client.aclose()


@pytest.mark.anyio
async def test_unauthorized_optimize_forbidden():
    await _clean()
    owner = await _upsert_user(_user("driver"))
    stranger = await _upsert_user(_user("driver", permissions_override={PERM_VIEW: True}))
    owner_client = await client_for(owner)
    created = await owner_client.post("/api/route-optimizer/routes", json={"name": "IRO-TEST FORB", "start_mode": "MANUAL", "start_location": {"lat": -6.17, "lng": 106.82, "location_source": "MANUAL_COORDINATE"}})
    route_id = created.json()["route"]["id"]
    await owner_client.post(f"/api/route-optimizer/routes/{route_id}/stops", json=_stop("F1", -6.18, 106.83, "p3"))
    await owner_client.aclose()
    stranger_client = await client_for(stranger)
    opt = await stranger_client.post(f"/api/route-optimizer/routes/{route_id}/optimize", json={})
    assert opt.status_code == 403
    edit = await stranger_client.patch(f"/api/route-optimizer/routes/{route_id}", json={"name": "hack"})
    assert edit.status_code == 403
    await stranger_client.aclose()


@pytest.mark.anyio
async def test_route_only_stop_does_not_create_master():
    await _clean()
    before_orders = await server.db.orders.count_documents({})
    before_sales = await server.db.sales_outlets.count_documents({})
    user = await _upsert_user(_user("driver"))
    client = await client_for(user)
    created = await client.post("/api/route-optimizer/routes", json={"name": "IRO-TEST ONLY", "start_mode": "MANUAL", "start_location": {"lat": -6.17, "lng": 106.82, "location_source": "MANUAL_COORDINATE"}})
    route_id = created.json()["route"]["id"]
    added = await client.post(f"/api/route-optimizer/routes/{route_id}/stops", json=_stop("Only", -6.19, 106.84, "p3"))
    assert added.status_code == 200
    assert added.json()["stop"]["ref_kind"] == "ROUTE_ONLY"
    assert await server.db.orders.count_documents({}) == before_orders
    assert await server.db.sales_outlets.count_documents({}) == before_sales
    await client.aclose()


@pytest.mark.anyio
async def test_warehouse_settings_and_start():
    await _clean()
    admin = await _upsert_user(_user("admin"))
    client = await client_for(admin)
    missing = await client.put("/api/route-optimizer/settings", json={"warehouse": {"name": "Gudang"}})
    assert missing.status_code == 200
    assert missing.json()["settings"]["warehouse"]["configured"] is False
    saved = await client.put("/api/route-optimizer/settings", json={"warehouse": {"lat": -6.2, "lng": 106.8, "name": "Gudang Test", "location_source": "MANUAL_COORDINATE"}})
    assert saved.json()["settings"]["warehouse"]["configured"] is True
    created = await client.post("/api/route-optimizer/routes", json={"name": "IRO-TEST WH", "start_mode": "WAREHOUSE", "end_mode": "WAREHOUSE", "strategy": "NEAREST"})
    route_id = created.json()["route"]["id"]
    await client.post(f"/api/route-optimizer/routes/{route_id}/stops", json=_stop("W1", -6.21, 106.81, "p3"))
    opt = await client.post(f"/api/route-optimizer/routes/{route_id}/optimize", json={})
    assert opt.status_code == 200, opt.text
    assert opt.json()["result"]["segments"][-1]["to"] == "END"
    await client.aclose()


@pytest.mark.anyio
async def test_concurrent_optimize_keeps_one_current():
    await _clean()
    user = await _upsert_user(_user("driver"))
    client = await client_for(user)
    created = await client.post("/api/route-optimizer/routes", json={"name": "IRO-TEST CONC", "start_mode": "MANUAL", "start_location": {"lat": -6.17, "lng": 106.82, "location_source": "MANUAL_COORDINATE"}})
    route_id = created.json()["route"]["id"]
    await client.post(f"/api/route-optimizer/routes/{route_id}/stops", json=_stop("C1", -6.18, 106.83, "p3"))
    await client.post(f"/api/route-optimizer/routes/{route_id}/stops", json=_stop("C2", -6.22, 106.86, "p3"))
    detail = await client.get(f"/api/route-optimizer/routes/{route_id}")
    rev = detail.json()["route"]["revision"]

    async def _opt():
        other = await client_for(user)
        try:
            return await other.post(f"/api/route-optimizer/routes/{route_id}/optimize", json={"revision": rev})
        finally:
            await other.aclose()

    a, b = await asyncio.gather(_opt(), _opt())
    codes = sorted([a.status_code, b.status_code])
    assert 200 in codes
    assert codes[0] in (200, 409)
    assert codes.count(200) == 1 or (codes[0] == 200 and codes[1] == 409)
    detail2 = await client.get(f"/api/route-optimizer/routes/{route_id}")
    versions = await client.get(f"/api/route-optimizer/routes/{route_id}/versions")
    assert detail2.json()["route"]["current_optimization_version"] == len(versions.json()["versions"])
    await client.aclose()


@pytest.mark.anyio
async def test_gps_start_required():
    await _clean()
    user = await _upsert_user(_user("driver"))
    client = await client_for(user)
    created = await client.post("/api/route-optimizer/routes", json={"name": "IRO-TEST GPS", "start_mode": "CURRENT_LOCATION"})
    route_id = created.json()["route"]["id"]
    await client.post(f"/api/route-optimizer/routes/{route_id}/stops", json=_stop("G1", -6.18, 106.83, "p3"))
    opt = await client.post(f"/api/route-optimizer/routes/{route_id}/optimize", json={})
    assert opt.status_code == 400
    snap = await client.post(f"/api/route-optimizer/routes/{route_id}/optimize", json={
        "start_location": {"lat": -6.111, "lng": 106.111, "accuracy_m": 12, "location_source": "CURRENT_LOCATION", "captured_at": "2026-09-03T00:00:00Z"}
    })
    assert snap.status_code == 200, snap.text
    assert snap.json()["result"]["start"]["lat"] == -6.111
    await client.aclose()


@pytest.mark.anyio
async def test_places_unconfigured_is_graceful_and_keeps_manual(monkeypatch):
    await _clean()
    monkeypatch.setattr("services.independent_route.places.is_google_places_configured", lambda: False)
    user = await _upsert_user(_user("driver"))
    client = await client_for(user)
    status = await client.get("/api/route-optimizer/places/status")
    assert status.status_code == 200
    body = status.json()
    assert body["configured"] is False
    assert "belum dikonfigurasi" in body["message"].lower()
    assert body["required_config"] == "GOOGLE_PLACES_API_KEY"
    assert "api_key" not in body
    search = await client.get("/api/route-optimizer/places/search", params={"q": "Starbucks Sudirman"})
    assert search.status_code == 200
    assert search.json()["suggestions"] == []
    created = await client.post("/api/route-optimizer/routes", json={"name": "IRO-TEST GMAPS OFF", "start_mode": "MANUAL", "start_location": {"lat": -6.17, "lng": 106.82, "location_source": "MAP_PIN"}})
    assert created.status_code == 200
    route_id = created.json()["route"]["id"]
    pinned = await client.post(f"/api/route-optimizer/routes/{route_id}/stops", json=_stop("Pin", -6.19, 106.84, "p3", location_source="MAP_PIN"))
    assert pinned.status_code == 200
    assert pinned.json()["stop"]["location_source"] == "MAP_PIN"
    await client.aclose()


@pytest.mark.anyio
async def test_places_search_candidates_are_not_persisted(monkeypatch):
    await _clean()
    from services.places_provider.base import PlacesProviderError
    from services.places_provider.types import PlaceSearchResult, PlaceSuggestion

    class _FakeProvider:
        def search(self, query):
            return PlaceSearchResult(
                provider="google",
                suggestions=[PlaceSuggestion(place_id="ChIJ-test", label="Toko ABC", formatted_address="Jl. Contoh")],
            )

    monkeypatch.setattr("services.independent_route.places.is_google_places_configured", lambda: True)
    monkeypatch.setattr("services.independent_route.places.GooglePlacesProvider", _FakeProvider)
    user = await _upsert_user(_user("driver"))
    client = await client_for(user)
    before = await server.db[COL_STOPS].count_documents({})
    search = await client.get("/api/route-optimizer/places/search", params={"q": "Toko ABC"})
    assert search.status_code == 200
    assert search.json()["suggestions"][0]["place_id"] == "ChIJ-test"
    assert await server.db[COL_STOPS].count_documents({}) == before

    class _Boom:
        def search(self, query):
            raise PlacesProviderError("quota")

    monkeypatch.setattr("services.independent_route.places.GooglePlacesProvider", _Boom)
    failed = await client.get("/api/route-optimizer/places/search", params={"q": "Toko ABC"})
    assert failed.status_code == 502
    assert failed.json().get("suggestions") in (None, [])
    await client.aclose()


@pytest.mark.anyio
async def test_google_stop_snapshot_optimize_and_route_only(monkeypatch):
    await _clean()
    from services.places_provider.types import PlaceDetails

    class _FakeDetails:
        def details(self, place_id):
            return PlaceDetails(
                place_id="ChIJ-starbucks",
                formatted_address="Jl. Sudirman, Jakarta",
                lat=-6.214612,
                lng=106.845133,
                name="Starbucks Sudirman",
            )

    monkeypatch.setattr("services.independent_route.places.is_google_places_configured", lambda: True)
    monkeypatch.setattr("services.independent_route.places.GooglePlacesProvider", _FakeDetails)
    user = await _upsert_user(_user("driver"))
    client = await client_for(user)
    details = await client.get("/api/route-optimizer/places/details", params={"place_id": "ChIJ-starbucks"})
    assert details.status_code == 200
    place = details.json()["place"]
    assert place["lat"] == -6.214612
    assert place["google_place_id"] == "ChIJ-starbucks"
    assert "query_place_id=" in place["google_maps_url"]

    before_orders = await server.db.orders.count_documents({})
    before_sales = await server.db.sales_outlets.count_documents({})
    before_customers = await server.db.customers.count_documents({}) if "customers" in await server.db.list_collection_names() else 0
    created = await client.post("/api/route-optimizer/routes", json={
        "name": "IRO-TEST GMAPS SAVE",
        "start_mode": "MANUAL",
        "start_location": {
            "lat": -6.175392,
            "lng": 106.827153,
            "location_source": "GOOGLE_PLACES",
            "google_place_id": "ChIJ-monas",
            "place_name": "Monas",
            "formatted_address": "Gambir, Jakarta",
        },
        "end_mode": "LAST_STOP",
        "strategy": "NEAREST",
    })
    assert created.status_code == 200, created.text
    start = created.json()["route"]["start_location"]
    assert start["lat"] == -6.175392
    assert start["google_place_id"] == "ChIJ-monas"
    assert start["selected_by"]["id"] == user["id"]
    route_id = created.json()["route"]["id"]
    far_p1 = await client.post(f"/api/route-optimizer/routes/{route_id}/stops", json={
        "display_name": "IRO-TEST Starbucks",
        "ref_kind": "ROUTE_ONLY",
        "lat": -6.400001,
        "lng": 106.850002,
        "location_source": "GOOGLE_PLACES",
        "google_place_id": "ChIJ-starbucks",
        "place_name": "Starbucks Sudirman",
        "formatted_address": "Jl. Sudirman, Jakarta",
        "priority_id": "p1",
    })
    assert far_p1.status_code == 200, far_p1.text
    stop = far_p1.json()["stop"]
    assert stop["ref_kind"] == "ROUTE_ONLY"
    assert stop["lat"] == -6.400001
    assert stop["lng"] == 106.850002
    assert stop["google_place_id"] == "ChIJ-starbucks"
    assert stop["location_source"] == "GOOGLE_PLACES"
    assert "query_place_id=" in stop["google_maps_url"]
    near_p3 = await client.post(f"/api/route-optimizer/routes/{route_id}/stops", json=_stop("Near", -6.180001, 106.830002, "p3"))
    assert near_p3.status_code == 200
    invalid = await client.post(f"/api/route-optimizer/routes/{route_id}/stops", json={
        "display_name": "IRO-TEST Zero",
        "ref_kind": "ROUTE_ONLY",
        "lat": 0,
        "lng": 0,
        "location_source": "GOOGLE_PLACES",
        "google_place_id": "ChIJ-zero",
        "priority_id": "p3",
    })
    assert invalid.status_code == 400
    opt = await client.post(f"/api/route-optimizer/routes/{route_id}/optimize", json={})
    assert opt.status_code == 200, opt.text
    after = await client.get(f"/api/route-optimizer/routes/{route_id}")
    by_id = {s["id"]: s for s in after.json()["route"]["stops"]}
    kept = by_id[stop["id"]]
    assert kept["lat"] == -6.400001
    assert kept["lng"] == 106.850002
    assert kept["google_place_id"] == "ChIJ-starbucks"
    assert kept["priority_id"] == "p1"
    ordered = opt.json()["result"]["ordered_stop_ids"]
    assert ordered[0] == stop["id"]
    assert await server.db.orders.count_documents({}) == before_orders
    assert await server.db.sales_outlets.count_documents({}) == before_sales
    if "customers" in await server.db.list_collection_names():
        assert await server.db.customers.count_documents({}) == before_customers
    await client.aclose()
