"""Visit photo upload tests. Isolated Sales Activity DB only."""
import io
import os
import struct
import sys
import zlib
from datetime import datetime, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

TEST_DB = "eternal_packaging_sa_visit_test"
assert TEST_DB != "eternal_packaging"
assert TEST_DB != "eternal_packaging_sa_test"
assert "test" in TEST_DB

os.environ["MONGO_URL"] = os.environ.get("MONGO_URL") or "mongodb://127.0.0.1:27017"
os.environ["DB_NAME"] = TEST_DB
os.environ.setdefault("JWT_SECRET", "test-secret-key-12345678901234567890")
os.environ.setdefault("CORS_ORIGINS", "*")
os.environ["SALES_ACTIVITY_EVALUATOR_ENABLED"] = "0"
os.environ.setdefault("STORAGE_PROVIDER", "local")

import httpx
import server
from server import app, current_user
from services.sales_activity import clock
from services.sales_activity.constants import (
    AUTOMATION_ACTIVE,
    AUTOMATION_PAUSED_MIGRATION,
    COL_ACTIVITIES,
    COL_BATCHES,
    COL_CONFLICTS,
    COL_EVENTS,
    COL_OWNERSHIP,
    COL_OUTLETS,
    COL_POOL,
    COL_VISIT_PHOTOS,
    MAX_VISIT_PHOTO_BYTES,
    SOURCE_NATIVE,
)
from services.sales_activity.indexes import ensure_indexes
from services.sales_activity.photos import parse_coordinates, sniff_image_mime


SALES_PERMS = {
    "sales.activity.view": True,
    "sales.activity.create": True,
    "sales.outlet.create": True,
}


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture(autouse=True)
async def isolated_db():
    from motor.motor_asyncio import AsyncIOMotorClient

    os.environ["DB_NAME"] = TEST_DB
    server.client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    server.db = server.client[TEST_DB]
    assert server.db.name == TEST_DB
    await ensure_indexes(server.db)
    for name in (COL_OUTLETS, COL_ACTIVITIES, COL_OWNERSHIP, COL_EVENTS, COL_POOL, COL_BATCHES, COL_CONFLICTS, COL_VISIT_PHOTOS):
        await server.db[name].delete_many({})
    await server.db.files.delete_many({"kind": "sales_visit_photo"})
    await server.db.app_settings.delete_many({"key": {"$in": ["sales_activity", "sales_activity_evaluator_lock"]}})
    await server.db.users.delete_many({})
    clock.reset()
    yield
    clock.reset()


def _user(uid, email, name, role="crm", perms=None, admin=False):
    return {
        "id": uid,
        "user_id": uid,
        "email": email,
        "name": name,
        "role": "admin" if admin else role,
        "permissions_override": perms or {},
        "status": "active",
    }


async def _seed_user(user):
    await server.db.users.insert_one({
        "id": user["id"],
        "email": user["email"],
        "name": user["name"],
        "role": user["role"],
        "status": "active",
        "permissions_override": user.get("permissions_override") or {},
        "password_hash": "x",
    })


async def _client_for(user):
    await _seed_user(user)
    app.dependency_overrides[current_user] = lambda: user
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


async def _close(client):
    await client.aclose()
    app.dependency_overrides.pop(current_user, None)


def _png_bytes():
    def chunk(tag, data):
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xffffffff)
    ihdr = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)
    raw = b"\x00\xff\x00\x00"
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b"")


async def _seed_prospect_pics(*uids):
    from services.sales_activity.settings import default_settings, merge_settings

    existing = await server.db.app_settings.find_one({"key": "sales_activity"}, {"_id": 0})
    doc = merge_settings(existing) if existing else default_settings()
    doc["key"] = "sales_activity"
    doc["prospect_pic_user_ids"] = [str(u).strip() for u in uids if str(u or "").strip()]
    await server.db.app_settings.update_one({"key": "sales_activity"}, {"$set": doc}, upsert=True)


async def _make_outlet(client, name="Toko Visit Foto", owner_id=None):
    uid = owner_id
    if not uid:
        # Prefer actor from dependency override seed — resolve from JWT override user via DB last insert is fragile;
        # callers that need a specific owner should pass owner_id. Default: ensure any active user in allowlist.
        users = await server.db.users.find({}, {"id": 1}).to_list(50)
        ids = [u["id"] for u in users if u.get("id")]
        await _seed_prospect_pics(*ids)
        uid = ids[0] if ids else None
    else:
        await _seed_prospect_pics(uid)
    payload = {"name": name, "address": "Jl. Foto"}
    if uid:
        payload["owner_id"] = uid
    res = await client.post("/api/sales-activity/outlets", json=payload)
    assert res.status_code == 200, res.text
    return res.json()


async def _upload(client, outlet_id, data=None, filename="visit.png", content_type="image/png", **fields):
    body = data if data is not None else _png_bytes()
    form = {"outlet_id": outlet_id, **{k: str(v) for k, v in fields.items()}}
    return await client.post(
        "/api/sales-activity/visit-photos",
        data=form,
        files={"file": (filename, body, content_type)},
    )


@pytest.mark.anyio
async def test_visit_photo_routes_are_registered():
    paths = {getattr(route, "path", "") for route in server.api_router.routes}
    assert "/api/sales-activity/visit-photos" in paths
    assert "/api/sales-activity/visit-photos/{photo_id}/file" in paths
    assert "/api/sales-activity/activities/{activity_id}" in paths


@pytest.mark.anyio
async def test_sniff_and_fake_coordinates():
    png = _png_bytes()
    assert sniff_image_mime(png, "application/octet-stream") == "image/png"
    lat, lng, acc, status = parse_coordinates("0", "0", "5")
    assert status == "UNAVAILABLE"
    assert lat is None and lng is None
    lat, lng, acc, status = parse_coordinates("-6.2", "106.8", "12")
    assert status == "OK"
    assert lat == -6.2 and lng == 106.8 and acc == 12


@pytest.mark.anyio
async def test_authorized_sales_upload_derives_actor_and_coords():
    clock.set_frozen(datetime(2026, 9, 3, 3, 15, tzinfo=timezone.utc))
    sales = _user("u-foto", "foto@sa-test.local", "Foto Sales", perms=SALES_PERMS)
    client = await _client_for(sales)
    try:
        created = await _make_outlet(client)
        oid = created["outlet"]["id"]
        res = await _upload(
            client, oid,
            latitude="-6.2001", longitude="106.8166", accuracy="14",
            uploaded_by="spoof-user", sales_id="spoof-sales", owner_id="spoof-owner",
            client_captured_at="2026-09-03T02:00:00+00:00",
        )
        assert res.status_code == 200, res.text
        photo = res.json()["photo"]
        assert photo["uploaded_by"] == "u-foto"
        assert photo["uploaded_by_name"] == "Foto Sales"
        assert photo["latitude"] == -6.2001
        assert photo["longitude"] == 106.8166
        assert photo["location_accuracy_meters"] == 14
        assert photo["location_status"] == "OK"
        assert photo["visit_at"].startswith("2026-09-03T03:15")
        assert photo["client_captured_at"].startswith("2026-09-03T02:00")
        assert photo["maps_url"] == "https://www.google.com/maps?q=-6.2001,106.8166"
        stored = await server.db[COL_VISIT_PHOTOS].find_one({"id": photo["id"]})
        assert stored["uploaded_by"] == "u-foto"
        assert "storage_path" not in photo
        act = await client.post("/api/sales-activity/activities", json={
            "outlet_id": oid, "activity_type": "visit", "notes": "visit foto",
            "visit_photo_ids": [photo["id"]],
        })
        assert act.status_code == 200, act.text
        detail = await client.get(f"/api/sales-activity/activities/{act.json()['activity']['id']}")
        assert detail.status_code == 200
        body = detail.json()["activity"]
        assert body["visit_photo_count"] == 1
        assert body["has_visit_location"] is True
        assert body["visit_photos"][0]["latitude"] == -6.2001
        file_res = await client.get(body["visit_photos"][0]["file_url"])
        assert file_res.status_code == 200
        assert file_res.headers["content-type"].startswith("image/")
    finally:
        await _close(client)


@pytest.mark.anyio
async def test_rejects_non_image_and_oversized():
    sales = _user("u-badfile", "badfile@sa-test.local", "Bad File", perms=SALES_PERMS)
    client = await _client_for(sales)
    try:
        oid = (await _make_outlet(client, "Toko File"))["outlet"]["id"]
        exe = await _upload(client, oid, data=b"MZ\x90\x00not-an-image", filename="hack.exe", content_type="image/jpeg")
        assert exe.status_code == 400
        huge = await _upload(client, oid, data=_png_bytes() + b"\x00" * (MAX_VISIT_PHOTO_BYTES + 10), filename="big.png")
        assert huge.status_code == 400
        assert "5MB" in huge.text
    finally:
        await _close(client)


@pytest.mark.anyio
async def test_unauthorized_upload_forbidden():
    owner = _user("u-own", "own@sa-test.local", "Owner", perms=SALES_PERMS)
    other = _user("u-other", "other@sa-test.local", "Other", perms=SALES_PERMS)
    owner_client = await _client_for(owner)
    try:
        oid = (await _make_outlet(owner_client, "Toko Owner"))["outlet"]["id"]
    finally:
        await _close(owner_client)
    other_client = await _client_for(other)
    try:
        res = await _upload(other_client, oid, latitude="-6.2", longitude="106.8")
        assert res.status_code == 403
    finally:
        await _close(other_client)


@pytest.mark.anyio
async def test_required_geolocation_blocks_and_never_stores_fake_zero():
    admin = _user("u-admin-foto", "admin-foto@sa-test.local", "Admin", admin=True)
    client = await _client_for(admin)
    try:
        settings = (await client.get("/api/sales-activity/settings")).json()["settings"]
        for t in settings["activity_types"]:
            if t["id"] == "visit":
                t["visit_photo_required"] = True
                t["evidence_required"] = True
        await client.put("/api/sales-activity/settings", json=settings)
        oid = (await _make_outlet(client, "Toko Wajib Foto"))["outlet"]["id"]
        missing = await client.post("/api/sales-activity/activities", json={"outlet_id": oid, "activity_type": "visit", "notes": "no photo"})
        assert missing.status_code == 400
        fake = await _upload(client, oid, latitude="0", longitude="0", accuracy="1")
        assert fake.status_code == 200, fake.text
        photo = fake.json()["photo"]
        assert photo["latitude"] is None
        assert photo["longitude"] is None
        assert photo["location_status"] == "UNAVAILABLE"
        blocked = await client.post("/api/sales-activity/activities", json={
            "outlet_id": oid, "activity_type": "visit", "notes": "fake geo",
            "visit_photo_ids": [photo["id"]],
        })
        assert blocked.status_code == 400
        stored = await server.db[COL_VISIT_PHOTOS].find_one({"id": photo["id"]})
        assert stored["latitude"] is None
        assert stored["longitude"] is None
        chat = await client.post("/api/sales-activity/activities", json={"outlet_id": oid, "activity_type": "chat", "notes": "chat only"})
        assert chat.status_code == 200
        assert chat.json()["activity"]["visit_photo_count"] == 0
    finally:
        await _close(client)


@pytest.mark.anyio
async def test_historical_evidence_preserved_and_upload_does_not_mutate_ownership():
    admin = _user("u-admin-hist", "admin-hist@sa-test.local", "Admin", admin=True)
    client = await _client_for(admin)
    clock.set_frozen(datetime(2026, 9, 3, 8, 0, tzinfo=timezone.utc))
    try:
        created = await _make_outlet(client, "Toko Hist")
        oid = created["outlet"]["id"]
        owner = created["ownership"]["current_owner_id"]
        await server.db[COL_OWNERSHIP].update_one({"outlet_id": oid}, {"$set": {"automation_status": AUTOMATION_PAUSED_MIGRATION}})
        await server.db[COL_ACTIVITIES].insert_one({
            "id": "act-hist-1",
            "outlet_id": oid,
            "activity_at": "2026-03-10T10:00:00+00:00",
            "sales_user_id": owner,
            "sales_name": "Admin",
            "activity_type": "visit",
            "result": "lama",
            "notes": "impor",
            "evidence_type": "foto_wa",
            "evidence_link": "https://example.test/bukti-lama",
            "source": "IMPORT",
            "visit_photo_ids": [],
            "visit_photo_count": 0,
            "has_visit_location": False,
        })
        detail = await client.get("/api/sales-activity/activities/act-hist-1")
        assert detail.status_code == 200
        hist = detail.json()["activity"]
        assert hist["evidence_type"] == "foto_wa"
        assert hist["evidence_link"] == "https://example.test/bukti-lama"
        assert hist["visit_photos"] == []
        photo = (await _upload(client, oid, latitude="-6.21", longitude="106.85")).json()["photo"]
        native = await client.post("/api/sales-activity/activities", json={
            "outlet_id": oid, "activity_type": "visit", "notes": "baru",
            "visit_photo_ids": [photo["id"]],
        })
        assert native.status_code == 200
        snap = await server.db[COL_OWNERSHIP].find_one({"outlet_id": oid})
        assert snap["automation_status"] == AUTOMATION_PAUSED_MIGRATION
        assert snap["current_owner_id"] == owner
        assert native.json()["activity"]["source"] == SOURCE_NATIVE
        assert native.json()["timer_applied"] is False
        still = await server.db[COL_ACTIVITIES].find_one({"id": "act-hist-1"})
        assert still["evidence_type"] == "foto_wa"
        assert still["evidence_link"] == "https://example.test/bukti-lama"
        active_native = await _make_outlet(client, "Toko Aktif Foto")
        assert active_native["ownership"]["automation_status"] == AUTOMATION_ACTIVE
        owner2 = active_native["ownership"]["current_owner_id"]
        photo2 = (await _upload(client, active_native["outlet"]["id"], latitude="-6.3", longitude="106.9")).json()["photo"]
        await client.post("/api/sales-activity/activities", json={
            "outlet_id": active_native["outlet"]["id"], "activity_type": "visit",
            "visit_photo_ids": [photo2["id"]],
        })
        snap2 = await server.db[COL_OWNERSHIP].find_one({"outlet_id": active_native["outlet"]["id"]})
        assert snap2["current_owner_id"] == owner2
    finally:
        await _close(client)
