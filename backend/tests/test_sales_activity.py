"""Sales Activity + Outlet Ownership backend matrix. Isolated test DB only."""
import asyncio
import io
import os
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from openpyxl import Workbook

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

TEST_DB = "eternal_packaging_sa_test"
assert TEST_DB != "eternal_packaging"
assert "test" in TEST_DB

os.environ["MONGO_URL"] = os.environ.get("MONGO_URL") or "mongodb://127.0.0.1:27017"
os.environ["DB_NAME"] = TEST_DB
os.environ.setdefault("JWT_SECRET", "test-secret-key-12345678901234567890")
os.environ.setdefault("CORS_ORIGINS", "*")
os.environ["SALES_ACTIVITY_EVALUATOR_ENABLED"] = "0"

import httpx
import server
from permissions import ALL_PERMISSIONS, ROLE_DEFAULT_PERMISSIONS
from server import app, current_user
from services.sales_activity import clock
from services.sales_activity.constants import (
    AUTOMATION_ACTIVE,
    AUTOMATION_PAUSED_MANUAL,
    AUTOMATION_PAUSED_MIGRATION,
    COL_ACTIVITIES,
    COL_BATCHES,
    COL_CONFLICTS,
    COL_EVENTS,
    COL_OWNERSHIP,
    COL_OUTLETS,
    COL_POOL,
    COL_VISIT_PHOTOS,
    KIND_CUSTOMER,
    KIND_DORMANT,
    KIND_PROSPECT,
    SOURCE_IMPORT,
    SOURCE_NATIVE,
    TIMER_INACTIVITY,
)
from services.sales_activity.indexes import ensure_indexes

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

    assert os.environ["DB_NAME"] == TEST_DB
    server.client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    server.db = server.client[TEST_DB]
    assert server.db.name == TEST_DB
    await ensure_indexes(server.db)
    for name in (COL_OUTLETS, COL_ACTIVITIES, COL_OWNERSHIP, COL_EVENTS, COL_POOL, COL_BATCHES, COL_CONFLICTS, COL_VISIT_PHOTOS):
        await server.db[name].delete_many({})
    await server.db.files.delete_many({"kind": "sales_visit_photo"})
    await server.db.app_settings.delete_many({"key": {"$in": ["sales_activity", "sales_activity_evaluator_lock"]}})
    await server.db.users.delete_many({})
    await server.db.orders.delete_many({"po_number": {"$regex": r"^SA-TEST-"}})
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


def _xlsx_bytes(outlets, activities):
    wb = Workbook()
    ws = wb.active
    ws.title = "INPUT DATA"
    ws.append(["Outlet ID", "Nama Outlet", "Alamat", "WhatsApp", "PIC Sales", "Kategori", "Sumber Lead"])
    for row in outlets:
        ws.append([row.get("code"), row.get("name"), row.get("address"), row.get("wa"), row.get("sales"), row.get("cat"), row.get("src")])
    wa = wb.create_sheet("INPUT ACTIVITY")
    wa.append(["Tanggal Aktivitas", "Sales", "Outlet", "Jenis Aktivitas", "Hasil", "Notes"])
    for row in activities:
        wa.append([row.get("at"), row.get("sales"), row.get("outlet"), row.get("type"), row.get("result"), row.get("notes")])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


async def _put_settings(client, **overrides):
    current = (await client.get("/api/sales-activity/settings")).json()["settings"]
    current.update(overrides)
    res = await client.put("/api/sales-activity/settings", json=current)
    assert res.status_code == 200, res.text
    return res.json()["settings"]


@pytest.mark.anyio
async def test_case_1_native_outlet_starts_automation():
    clock.set_frozen(datetime(2026, 9, 2, 8, 0, tzinfo=timezone.utc))
    sales = _user("u-ayub", "ayub@sa-test.local", "Ayub", perms=SALES_PERMS)
    client = await _client_for(sales)
    try:
        res = await client.post("/api/sales-activity/outlets", json={"name": "Toko Native Satu", "address": "Jl. A"})
        assert res.status_code == 200, res.text
        body = res.json()
        assert body["outlet"]["source"] == SOURCE_NATIVE
        assert body["ownership"]["current_owner_id"] == "u-ayub"
        assert body["ownership"]["automation_status"] == AUTOMATION_ACTIVE
        assert body["ownership"]["ownership_started_at"].startswith("2026-09-02")
        assert body["ownership"]["eligible_at"]
        assert body["ownership"]["customer_kind"] == KIND_PROSPECT
    finally:
        await _close(client)


@pytest.mark.anyio
async def test_case_2_sales_cannot_spoof_owner():
    sales = _user("u-ayub2", "ayub2@sa-test.local", "Ayub", perms=SALES_PERMS)
    other = _user("u-risna2", "risna2@sa-test.local", "Risna", perms=SALES_PERMS)
    await _seed_user(other)
    client = await _client_for(sales)
    try:
        res = await client.post("/api/sales-activity/outlets", json={"name": "Toko Spoof", "owner_id": "u-risna2"})
        assert res.status_code == 403
    finally:
        await _close(client)


@pytest.mark.anyio
async def test_case_3_and_4_and_5_import_preview_paused_idempotent():
    admin = _user("u-admin", "admin@sa-test.local", "Admin", admin=True)
    await _seed_user(_user("u-ayub3", "ayub3@sa-test.local", "Ayub"))
    client = await _client_for(admin)
    clock.set_frozen(datetime(2026, 9, 2, 12, 0, tzinfo=timezone.utc))
    try:
        content = _xlsx_bytes(
            [{"code": "OUT-1", "name": "Toko Maret", "address": "Jl. M", "sales": "Ayub"}],
            [{"at": datetime(2026, 3, 10, 10, 0), "sales": "Ayub", "outlet": "Toko Maret", "type": "Visit", "result": "Chat", "notes": "hist"}],
        )
        before_outlets = await server.db[COL_OUTLETS].count_documents({})
        before_events = await server.db[COL_EVENTS].count_documents({})
        preview = await client.post(
            "/api/sales-activity/import/preview",
            files={"file": ("sistem.xlsx", content, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
        )
        assert preview.status_code == 200, preview.text
        assert await server.db[COL_OUTLETS].count_documents({}) == before_outlets
        assert await server.db[COL_EVENTS].count_documents({}) == before_events
        assert preview.json()["preview"]["message"].startswith("Import tidak menjalankan")
        batch_id = preview.json()["batch"]["id"]
        confirm = await client.post(f"/api/sales-activity/import/batches/{batch_id}/confirm")
        assert confirm.status_code == 200, confirm.text
        outlet = await server.db[COL_OUTLETS].find_one({"name": "Toko Maret"})
        snap = await server.db[COL_OWNERSHIP].find_one({"outlet_id": outlet["id"]})
        act = await server.db[COL_ACTIVITIES].find_one({"outlet_id": outlet["id"]})
        assert snap["automation_status"] == AUTOMATION_PAUSED_MIGRATION
        assert snap.get("eligible_at") is None
        assert act["activity_at"].startswith("2026-03-10")
        assert outlet["source"] == SOURCE_IMPORT
        eval_res = await client.post("/api/sales-activity/evaluate")
        assert eval_res.status_code == 200
        snap2 = await server.db[COL_OWNERSHIP].find_one({"outlet_id": outlet["id"]})
        assert snap2["automation_status"] == AUTOMATION_PAUSED_MIGRATION
        assert snap2.get("in_pool") is not True
        outlets_n = await server.db[COL_OUTLETS].count_documents({})
        acts_n = await server.db[COL_ACTIVITIES].count_documents({})
        events_n = await server.db[COL_EVENTS].count_documents({})
        confirm2 = await client.post(f"/api/sales-activity/import/batches/{batch_id}/confirm")
        assert confirm2.status_code == 200
        assert await server.db[COL_OUTLETS].count_documents({}) == outlets_n
        assert await server.db[COL_ACTIVITIES].count_documents({}) == acts_n
        assert await server.db[COL_EVENTS].count_documents({}) == events_n
    finally:
        await _close(client)


@pytest.mark.anyio
async def test_case_6_activation_uses_activation_clock():
    admin = _user("u-admin6", "admin6@sa-test.local", "Admin", admin=True)
    await _seed_user(_user("u-ayub6", "ayub6@sa-test.local", "Ayub"))
    client = await _client_for(admin)
    clock.set_frozen(datetime(2026, 9, 2, 9, 0, tzinfo=timezone.utc))
    try:
        content = _xlsx_bytes(
            [{"code": "OUT-6", "name": "Prospect Maret", "sales": "Ayub"}],
            [{"at": datetime(2026, 3, 10), "sales": "Ayub", "outlet": "Prospect Maret", "type": "Visit"}],
        )
        preview = await client.post(
            "/api/sales-activity/import/preview",
            files={"file": ("m6.xlsx", content, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
        )
        batch_id = preview.json()["batch"]["id"]
        await client.post(f"/api/sales-activity/import/batches/{batch_id}/confirm")
        act_prev = await client.get(f"/api/sales-activity/import/batches/{batch_id}/activation-preview")
        assert act_prev.status_code == 200
        assert act_prev.json()["prospects_to_activate"] >= 1
        clock.set_frozen(datetime(2026, 9, 2, 15, 0, tzinfo=timezone.utc))
        activated = await client.post(f"/api/sales-activity/import/batches/{batch_id}/activate", json={"confirm": True})
        assert activated.status_code == 200, activated.text
        outlet = await server.db[COL_OUTLETS].find_one({"name": "Prospect Maret"})
        snap = await server.db[COL_OWNERSHIP].find_one({"outlet_id": outlet["id"]})
        assert snap["automation_status"] == AUTOMATION_ACTIVE
        assert snap["ownership_started_at"].startswith("2026-09-02T15:00")
        assert "2026-04-09" not in str(snap.get("eligible_at") or "")
        act = await server.db[COL_ACTIVITIES].find_one({"outlet_id": outlet["id"]})
        assert act["activity_at"].startswith("2026-03-10")
    finally:
        await _close(client)


@pytest.mark.anyio
async def test_case_7_and_8_existing_customer_and_dormancy_preview():
    admin = _user("u-admin7", "admin7@sa-test.local", "Admin", admin=True)
    await _seed_user(_user("u-ayub7", "ayub7@sa-test.local", "Ayub"))
    await server.db.orders.insert_one({
        "id": str(uuid.uuid4()),
        "po_number": "SA-TEST-7001",
        "customer": "Toko Customer Lama",
        "outlet": "Toko Customer Lama",
        "order_date": "2026-01-01",
        "created_at": "2026-01-01T00:00:00+00:00",
        "cancelled": False,
        "line_items": [{"name": "Box", "quantity": 10}],
    })
    client = await _client_for(admin)
    clock.set_frozen(datetime(2026, 9, 2, tzinfo=timezone.utc))
    try:
        content = _xlsx_bytes(
            [{"code": "OUT-7", "name": "Toko Customer Lama", "sales": "Ayub"}],
            [{"at": datetime(2026, 3, 10), "sales": "Ayub", "outlet": "Toko Customer Lama", "type": "Closing", "result": "Sudah Deal Order"}],
        )
        preview = await client.post(
            "/api/sales-activity/import/preview",
            files={"file": ("m7.xlsx", content, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
        )
        batch_id = preview.json()["batch"]["id"]
        await client.post(f"/api/sales-activity/import/batches/{batch_id}/confirm")
        outlet = await server.db[COL_OUTLETS].find_one({"name": "Toko Customer Lama"})
        snap = await server.db[COL_OWNERSHIP].find_one({"outlet_id": outlet["id"]})
        assert snap["automation_status"] == AUTOMATION_PAUSED_MIGRATION
        assert snap["customer_kind"] == KIND_CUSTOMER
        assert snap.get("in_pool") is not True
        act_prev = await client.get(f"/api/sales-activity/import/batches/{batch_id}/activation-preview")
        body = act_prev.json()
        assert body["potential_dormant_customers"] >= 1
        assert body["records_that_will_enter_pool"] >= 1
        assert await server.db[COL_POOL].count_documents({"active": True}) == 0
        await client.post(f"/api/sales-activity/import/batches/{batch_id}/activate", json={"confirm": True})
        snap2 = await server.db[COL_OWNERSHIP].find_one({"outlet_id": outlet["id"]})
        assert snap2["customer_kind"] == KIND_DORMANT
        assert snap2["in_pool"] is True
    finally:
        await _close(client)


@pytest.mark.anyio
async def test_case_9_and_10_hybrid_then_pool():
    admin = _user("u-admin9", "admin9@sa-test.local", "Admin", admin=True)
    a = _user("u-a9", "a9@sa-test.local", "Ayub")
    b = _user("u-b9", "b9@sa-test.local", "Risna")
    await _seed_user(a)
    await _seed_user(b)
    client = await _client_for(admin)
    clock.set_frozen(datetime(2026, 9, 1, tzinfo=timezone.utc))
    try:
        settings = (await client.get("/api/sales-activity/settings")).json()["settings"]
        settings["reassignment"]["sales_sequence"] = ["u-a9", "u-b9"]
        settings["default_rule"]["ownership_duration_days"] = 30
        settings["default_rule"]["inactivity_expiration_days"] = 30
        settings["default_rule"]["warning_before_expiration_days"] = 7
        settings["default_rule"]["inactivity_warning_days"] = 7
        assert (await client.put("/api/sales-activity/settings", json=settings)).status_code == 200
        created = await client.post("/api/sales-activity/outlets", json={"name": "Prospect Rotate", "owner_id": "u-a9"})
        assert created.status_code == 200, created.text
        oid = created.json()["outlet"]["id"]
        clock.set_frozen(datetime(2026, 10, 5, tzinfo=timezone.utc))
        ev = await client.post("/api/sales-activity/evaluate")
        assert ev.status_code == 200
        snap = await server.db[COL_OWNERSHIP].find_one({"outlet_id": oid})
        assert snap["current_owner_id"] == "u-b9"
        clock.set_frozen(datetime(2026, 11, 10, tzinfo=timezone.utc))
        ev2 = await client.post("/api/sales-activity/evaluate")
        assert ev2.status_code == 200
        snap2 = await server.db[COL_OWNERSHIP].find_one({"outlet_id": oid})
        assert snap2["in_pool"] is True
        assert snap2.get("current_owner_id") in (None, "")
        pool = await server.db[COL_POOL].find_one({"outlet_id": oid, "active": True})
        assert pool is not None
    finally:
        await _close(client)


@pytest.mark.anyio
async def test_case_11_manual_wins():
    admin = _user("u-admin11", "admin11@sa-test.local", "Admin", admin=True)
    a = _user("u-a11", "a11@sa-test.local", "Ayub")
    b = _user("u-b11", "b11@sa-test.local", "Risna")
    c = _user("u-c11", "c11@sa-test.local", "Basala")
    for u in (a, b, c):
        await _seed_user(u)
    client = await _client_for(admin)
    clock.set_frozen(datetime(2026, 9, 1, tzinfo=timezone.utc))
    try:
        settings = (await client.get("/api/sales-activity/settings")).json()["settings"]
        settings["reassignment"]["sales_sequence"] = ["u-a11", "u-b11"]
        settings["default_rule"]["warning_before_expiration_days"] = 7
        settings["default_rule"]["inactivity_warning_days"] = 7
        await client.put("/api/sales-activity/settings", json=settings)
        created = await client.post("/api/sales-activity/outlets", json={"name": "Manual Wins", "owner_id": "u-a11"})
        oid = created.json()["outlet"]["id"]
        clock.set_frozen(datetime(2026, 10, 10, tzinfo=timezone.utc))
        re = await client.post(f"/api/sales-activity/outlets/{oid}/reassign", json={"owner_id": "u-c11", "reason": "Supervisor ambil alih"})
        assert re.status_code == 200, re.text
        ev = await client.post("/api/sales-activity/evaluate")
        assert ev.status_code == 200
        snap = await server.db[COL_OWNERSHIP].find_one({"outlet_id": oid})
        assert snap["current_owner_id"] == "u-c11"
        assert snap.get("in_pool") is not True
    finally:
        await _close(client)


@pytest.mark.anyio
async def test_case_12_and_13_pause_resume():
    admin = _user("u-admin12", "admin12@sa-test.local", "Admin", admin=True)
    a = _user("u-a12", "a12@sa-test.local", "Ayub")
    b = _user("u-b12", "b12@sa-test.local", "Risna")
    await _seed_user(a)
    await _seed_user(b)
    client = await _client_for(admin)
    clock.set_frozen(datetime(2026, 9, 1, tzinfo=timezone.utc))
    try:
        settings = (await client.get("/api/sales-activity/settings")).json()["settings"]
        settings["reassignment"]["sales_sequence"] = ["u-a12", "u-b12"]
        settings["default_rule"]["warning_before_expiration_days"] = 7
        settings["default_rule"]["inactivity_warning_days"] = 7
        await client.put("/api/sales-activity/settings", json=settings)
        created = await client.post("/api/sales-activity/outlets", json={"name": "Pause Resume", "owner_id": "u-a12"})
        oid = created.json()["outlet"]["id"]
        paused = await client.post(f"/api/sales-activity/outlets/{oid}/pause", json={"reason": "Libur sales"})
        assert paused.status_code == 200
        clock.set_frozen(datetime(2026, 10, 20, tzinfo=timezone.utc))
        await client.post("/api/sales-activity/evaluate")
        snap = await server.db[COL_OWNERSHIP].find_one({"outlet_id": oid})
        assert snap["automation_status"] == AUTOMATION_PAUSED_MANUAL
        assert snap["current_owner_id"] == "u-a12"
        resumed = await client.post(f"/api/sales-activity/outlets/{oid}/resume", json={"reason": "Kembali aktif"})
        assert resumed.status_code == 200
        snap2 = await server.db[COL_OWNERSHIP].find_one({"outlet_id": oid})
        assert snap2["automation_status"] == AUTOMATION_ACTIVE
    finally:
        await _close(client)


@pytest.mark.anyio
async def test_case_14_and_15_activity_timer():
    admin = _user("u-admin14", "admin14@sa-test.local", "Admin", admin=True)
    client = await _client_for(admin)
    clock.set_frozen(datetime(2026, 9, 1, tzinfo=timezone.utc))
    try:
        settings = (await client.get("/api/sales-activity/settings")).json()["settings"]
        settings["default_rule"]["timer_mode"] = TIMER_INACTIVITY
        settings["default_rule"]["inactivity_expiration_days"] = 10
        settings["default_rule"]["inactivity_warning_days"] = 3
        settings["default_rule"]["ownership_duration_days"] = 30
        settings["default_rule"]["warning_before_expiration_days"] = 7
        settings["default_rule"]["valid_activity_resets_inactivity"] = True
        settings["activity_types"] = settings["activity_types"] + [{
            "id": "note",
            "label": "Note",
            "active": True,
            "counts_as_valid": False,
            "resets_inactivity": False,
            "evidence_required": False,
            "next_follow_up_required": False,
        }]
        await client.put("/api/sales-activity/settings", json=settings)
        created = await client.post("/api/sales-activity/outlets", json={"name": "Timer Outlet"})
        oid = created.json()["outlet"]["id"]
        first_eligible = created.json()["ownership"]["eligible_at"]
        clock.set_frozen(datetime(2026, 9, 4, tzinfo=timezone.utc))
        bad = await client.post("/api/sales-activity/activities", json={"outlet_id": oid, "activity_type": "note", "notes": "internal"})
        assert bad.status_code == 200
        assert bad.json()["timer_applied"] is False
        snap = await server.db[COL_OWNERSHIP].find_one({"outlet_id": oid})
        assert snap["eligible_at"] == first_eligible
        good = await client.post("/api/sales-activity/activities", json={"outlet_id": oid, "activity_type": "visit", "notes": "visit"})
        assert good.status_code == 200
        assert good.json()["timer_applied"] is True
        snap2 = await server.db[COL_OWNERSHIP].find_one({"outlet_id": oid})
        assert snap2["eligible_at"] != first_eligible
        assert snap2["eligible_at"].startswith("2026-09-14")
    finally:
        await _close(client)


@pytest.mark.anyio
async def test_case_16_valid_so_protects_customer():
    admin = _user("u-admin16", "admin16@sa-test.local", "Admin", admin=True)
    a = _user("u-a16", "a16@sa-test.local", "Ayub")
    b = _user("u-b16", "b16@sa-test.local", "Risna")
    await _seed_user(a)
    await _seed_user(b)
    client = await _client_for(admin)
    clock.set_frozen(datetime(2026, 9, 1, tzinfo=timezone.utc))
    try:
        settings = (await client.get("/api/sales-activity/settings")).json()["settings"]
        settings["reassignment"]["sales_sequence"] = ["u-a16", "u-b16"]
        settings["default_rule"]["warning_before_expiration_days"] = 7
        settings["default_rule"]["inactivity_warning_days"] = 7
        settings["dormancy"]["threshold_days"] = 180
        settings["dormancy"]["warning_days"] = 14
        await client.put("/api/sales-activity/settings", json=settings)
        created = await client.post("/api/sales-activity/outlets", json={"name": "Toko Proteksi", "owner_id": "u-a16"})
        oid = created.json()["outlet"]["id"]
        await server.db.orders.insert_one({
            "id": str(uuid.uuid4()),
            "po_number": "SA-TEST-1601",
            "customer": "Toko Proteksi",
            "outlet": "Toko Proteksi",
            "order_date": "2026-09-02",
            "created_at": "2026-09-02T00:00:00+00:00",
            "cancelled": False,
            "line_items": [{"name": "Box", "quantity": 5}],
        })
        clock.set_frozen(datetime(2026, 10, 20, tzinfo=timezone.utc))
        await client.post("/api/sales-activity/evaluate")
        snap = await server.db[COL_OWNERSHIP].find_one({"outlet_id": oid})
        assert snap["current_owner_id"] == "u-a16"
        assert snap.get("in_pool") is not True
        assert snap["customer_kind"] in {KIND_CUSTOMER, KIND_PROSPECT}
    finally:
        await _close(client)


@pytest.mark.anyio
async def test_case_17_dormant_goes_to_pool_not_next_sales():
    admin = _user("u-admin17", "admin17@sa-test.local", "Admin", admin=True)
    a = _user("u-a17", "a17@sa-test.local", "Ayub")
    b = _user("u-b17", "b17@sa-test.local", "Risna")
    await _seed_user(a)
    await _seed_user(b)
    client = await _client_for(admin)
    clock.set_frozen(datetime(2026, 9, 1, tzinfo=timezone.utc))
    try:
        settings = (await client.get("/api/sales-activity/settings")).json()["settings"]
        settings["reassignment"]["sales_sequence"] = ["u-a17", "u-b17"]
        settings["dormancy"]["threshold_days"] = 30
        settings["dormancy"]["warning_days"] = 7
        settings["default_rule"]["warning_before_expiration_days"] = 7
        settings["default_rule"]["inactivity_warning_days"] = 7
        await client.put("/api/sales-activity/settings", json=settings)
        created = await client.post("/api/sales-activity/outlets", json={"name": "Toko Dorman", "owner_id": "u-a17"})
        oid = created.json()["outlet"]["id"]
        await server.db.orders.insert_one({
            "id": str(uuid.uuid4()),
            "po_number": "SA-TEST-1701",
            "customer": "Toko Dorman",
            "outlet": "Toko Dorman",
            "order_date": "2026-07-01",
            "created_at": "2026-07-01T00:00:00+00:00",
            "cancelled": False,
            "line_items": [{"name": "Box", "quantity": 5}],
        })
        await client.post("/api/sales-activity/evaluate")
        snap = await server.db[COL_OWNERSHIP].find_one({"outlet_id": oid})
        assert snap["in_pool"] is True
        assert snap["customer_kind"] == KIND_DORMANT
        assert snap.get("current_owner_id") in (None, "")
    finally:
        await _close(client)


@pytest.mark.anyio
async def test_case_18_pool_assignment_new_cycle():
    admin = _user("u-admin18", "admin18@sa-test.local", "Admin", admin=True)
    a = _user("u-a18", "a18@sa-test.local", "Ayub")
    b = _user("u-b18", "b18@sa-test.local", "Basala")
    await _seed_user(a)
    await _seed_user(b)
    client = await _client_for(admin)
    try:
        created = await client.post("/api/sales-activity/outlets", json={"name": "Pool Assign", "owner_id": "u-a18"})
        oid = created.json()["outlet"]["id"]
        await client.post(f"/api/sales-activity/outlets/{oid}/send-to-pool", json={"reason": "Manual pool"})
        events_before = await server.db[COL_EVENTS].count_documents({"outlet_id": oid})
        assigned = await client.post(f"/api/sales-activity/outlets/{oid}/assign-from-pool", json={"owner_id": "u-b18", "reason": "Assign Basala"})
        assert assigned.status_code == 200, assigned.text
        snap = await server.db[COL_OWNERSHIP].find_one({"outlet_id": oid})
        assert snap["current_owner_id"] == "u-b18"
        assert snap["in_pool"] is not True
        assert snap["automation_status"] == AUTOMATION_ACTIVE
        assert await server.db[COL_EVENTS].count_documents({"outlet_id": oid}) > events_before
        old_cycle = await server.db[COL_EVENTS].find_one({"outlet_id": oid, "event_type": "SENT_TO_POOL"})
        assert old_cycle is not None
    finally:
        await _close(client)


@pytest.mark.anyio
async def test_case_19_repeated_evaluator_one_transition():
    admin = _user("u-admin19", "admin19@sa-test.local", "Admin", admin=True)
    a = _user("u-a19", "a19@sa-test.local", "Ayub")
    b = _user("u-b19", "b19@sa-test.local", "Risna")
    await _seed_user(a)
    await _seed_user(b)
    client = await _client_for(admin)
    clock.set_frozen(datetime(2026, 9, 1, tzinfo=timezone.utc))
    try:
        settings = (await client.get("/api/sales-activity/settings")).json()["settings"]
        settings["reassignment"]["sales_sequence"] = ["u-a19", "u-b19"]
        settings["default_rule"]["warning_before_expiration_days"] = 7
        settings["default_rule"]["inactivity_warning_days"] = 7
        await client.put("/api/sales-activity/settings", json=settings)
        created = await client.post("/api/sales-activity/outlets", json={"name": "Concurrent Eval", "owner_id": "u-a19"})
        oid = created.json()["outlet"]["id"]
        clock.set_frozen(datetime(2026, 10, 10, tzinfo=timezone.utc))
        await asyncio.gather(
            client.post("/api/sales-activity/evaluate"),
            client.post("/api/sales-activity/evaluate"),
        )
        snap = await server.db[COL_OWNERSHIP].find_one({"outlet_id": oid})
        assert snap["current_owner_id"] == "u-b19"
        autos = await server.db[COL_EVENTS].count_documents({"outlet_id": oid, "event_type": "AUTO_REASSIGNED"})
        assert autos == 1
    finally:
        await _close(client)


@pytest.mark.anyio
async def test_case_20_unmapped_sales_conflict():
    admin = _user("u-admin20", "admin20@sa-test.local", "Admin", admin=True)
    client = await _client_for(admin)
    try:
        content = _xlsx_bytes(
            [{"code": "OUT-20", "name": "Toko Unknown", "sales": "Orang Tidak Ada"}],
            [{"at": datetime(2026, 3, 10), "sales": "Orang Tidak Ada", "outlet": "Toko Unknown", "type": "Visit"}],
        )
        preview = await client.post(
            "/api/sales-activity/import/preview",
            files={"file": ("m20.xlsx", content, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
        )
        mapping = preview.json()["preview"]["sales_mappings"]["Orang Tidak Ada"]
        assert mapping["status"] == "UNMATCHED"
        batch_id = preview.json()["batch"]["id"]
        await client.post(f"/api/sales-activity/import/batches/{batch_id}/confirm")
        outlet = await server.db[COL_OUTLETS].find_one({"name": "Toko Unknown"})
        snap = await server.db[COL_OWNERSHIP].find_one({"outlet_id": outlet["id"]})
        assert snap["ownership_unresolved"] is True
        assert snap["automation_status"] == AUTOMATION_PAUSED_MIGRATION
        assert snap.get("current_owner_id") in (None, "")
    finally:
        await _close(client)


@pytest.mark.anyio
async def test_case_21_ambiguous_po_match_is_conflict():
    admin = _user("u-admin21", "admin21@sa-test.local", "Admin", admin=True)
    await _seed_user(_user("u-ayub21", "ayub21@sa-test.local", "Ayub"))
    await server.db.orders.insert_one({
        "id": str(uuid.uuid4()),
        "po_number": "SA-TEST-2101",
        "customer": "Nama Ganda",
        "outlet": "Cabang A",
        "order_date": "2026-08-01",
        "created_at": "2026-08-01T00:00:00+00:00",
        "cancelled": False,
        "line_items": [{"name": "Box", "quantity": 1}],
    })
    await server.db.orders.insert_one({
        "id": str(uuid.uuid4()),
        "po_number": "SA-TEST-2102",
        "customer": "Nama Ganda",
        "outlet": "Cabang B",
        "order_date": "2026-08-02",
        "created_at": "2026-08-02T00:00:00+00:00",
        "cancelled": False,
        "line_items": [{"name": "Box", "quantity": 1}],
    })
    client = await _client_for(admin)
    try:
        content = _xlsx_bytes(
            [{"code": "", "name": "Nama Ganda", "sales": "Ayub"}],
            [{"at": datetime(2026, 3, 10), "sales": "Ayub", "outlet": "Nama Ganda", "type": "Visit"}],
        )
        preview = await client.post(
            "/api/sales-activity/import/preview",
            files={"file": ("m21.xlsx", content, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
        )
        body = preview.json()
        assert any(c["type"] == "AMBIGUOUS_PO" for c in body["preview"]["conflicts"])
        batch_id = body["batch"]["id"]
        await client.post(f"/api/sales-activity/import/batches/{batch_id}/confirm")
        outlet = await server.db[COL_OUTLETS].find_one({"name": "Nama Ganda"})
        snap = await server.db[COL_OWNERSHIP].find_one({"outlet_id": outlet["id"]})
        assert snap["ownership_unresolved"] is True
    finally:
        await _close(client)


@pytest.mark.anyio
async def test_case_22_unauthorized_403():
    sales = _user("u-sales22", "sales22@sa-test.local", "Sales Biasa", role="crm")
    client = await _client_for(sales)
    try:
        res = await client.post("/api/sales-activity/outlets", json={"name": "No Perm"})
        assert res.status_code == 403
        res2 = await client.get("/api/sales-activity/pool")
        assert res2.status_code == 403
        res3 = await client.put("/api/sales-activity/settings", json={})
        assert res3.status_code == 403
    finally:
        await _close(client)


@pytest.mark.anyio
async def test_case_23_rule_version_preserved():
    admin = _user("u-admin23", "admin23@sa-test.local", "Admin", admin=True)
    client = await _client_for(admin)
    try:
        created = await client.post("/api/sales-activity/outlets", json={"name": "Versioned"})
        oid = created.json()["outlet"]["id"]
        first_version = created.json()["ownership"]["rule_version"]
        settings = (await client.get("/api/sales-activity/settings")).json()["settings"]
        settings["default_rule"]["ownership_duration_days"] = 45
        settings["default_rule"]["warning_before_expiration_days"] = 10
        settings["default_rule"]["inactivity_warning_days"] = 10
        put = await client.put("/api/sales-activity/settings", json=settings)
        assert put.status_code == 200
        assert put.json()["settings"]["default_rule"]["version"] == first_version + 1
        event = await server.db[COL_EVENTS].find_one({"outlet_id": oid, "event_type": "CLAIMED"})
        assert event["rule_version"] == first_version
        snap = await server.db[COL_OWNERSHIP].find_one({"outlet_id": oid})
        assert snap["rule_version"] == first_version
    finally:
        await _close(client)


@pytest.mark.anyio
async def test_case_24_get_does_not_mutate():
    admin = _user("u-admin24", "admin24@sa-test.local", "Admin", admin=True)
    client = await _client_for(admin)
    try:
        created = await client.post("/api/sales-activity/outlets", json={"name": "GET Pure"})
        oid = created.json()["outlet"]["id"]
        before_events = await server.db[COL_EVENTS].count_documents({})
        before_snap = await server.db[COL_OWNERSHIP].find_one({"outlet_id": oid})
        await client.get("/api/sales-activity/outlets")
        await client.get(f"/api/sales-activity/outlets/{oid}")
        after_events = await server.db[COL_EVENTS].count_documents({})
        after_snap = await server.db[COL_OWNERSHIP].find_one({"outlet_id": oid})
        assert after_events == before_events
        assert after_snap["version"] == before_snap["version"]
        assert after_snap["updated_at"] == before_snap["updated_at"]
    finally:
        await _close(client)


@pytest.mark.anyio
async def test_settings_reject_invalid_and_duplicate_sequence():
    admin = _user("u-admin25", "admin25@sa-test.local", "Admin", admin=True)
    await _seed_user(_user("u-seq", "seq@sa-test.local", "Seq"))
    client = await _client_for(admin)
    try:
        settings = (await client.get("/api/sales-activity/settings")).json()["settings"]
        settings["default_rule"]["ownership_duration_days"] = -1
        bad = await client.put("/api/sales-activity/settings", json=settings)
        assert bad.status_code == 400
        settings = (await client.get("/api/sales-activity/settings")).json()["settings"]
        settings["reassignment"]["sales_sequence"] = ["u-seq", "u-seq"]
        bad2 = await client.put("/api/sales-activity/settings", json=settings)
        assert bad2.status_code == 400
        settings = (await client.get("/api/sales-activity/settings")).json()["settings"]
        settings["reassignment"]["sales_sequence"] = ["missing-user"]
        bad3 = await client.put("/api/sales-activity/settings", json=settings)
        assert bad3.status_code == 400
    finally:
        await _close(client)


@pytest.mark.anyio
async def test_non_admin_role_defaults_exclude_new_module():
    for role, keys in ROLE_DEFAULT_PERMISSIONS.items():
        if role == "admin":
            continue
        assert "sales.ownership.manage" not in keys
        assert "sales.activity.import" not in keys
        assert "nav.sales.sales_activity" not in keys
    assert "sales.activity.view" in ALL_PERMISSIONS
