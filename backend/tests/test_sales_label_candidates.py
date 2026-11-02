"""Unified Sales Settings — candidates, aliases, import hook, verification."""
import asyncio
import os
import sys
import uuid
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ.setdefault("MONGO_URL", "mongodb://127.0.0.1:27017")
os.environ.setdefault("DB_NAME", "eternal_packaging_test_sales_label_candidates")
os.environ.setdefault("JWT_SECRET", "test-secret-key-12345678901234567890")
os.environ.setdefault("CORS_ORIGINS", "*")

import httpx

from server import app, current_user
from services import sales_label_candidates as cand
from services import sales_master as sm
from services import sales_master_aliases as aliases
from services.sales_user_mapping import SETTINGS_KEY as MAPPING_KEY, exact_sales_label
from services.sales_reports import _resolve_sales_attribution
from services.sales_user_mapping import mapping_dict_from_entries, load_mapping_doc


AYUB = "Sholahudin Al - Ayubi"


def _user(role, *, uid=None, override=None):
    ident = uid or f"{role}-{uuid.uuid4().hex[:6]}"
    return {
        "id": ident,
        "user_id": ident,
        "email": f"{ident}@eternal.test",
        "name": role.upper(),
        "role": role,
        "permissions_override": override or {},
    }


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture(autouse=True)
def bind_motor():
    import server
    from motor.motor_asyncio import AsyncIOMotorClient
    server.client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    server.db = server.client[os.environ["DB_NAME"]]


def _override(user):
    async def _dep():
        return user
    return _dep


async def _client(user):
    app.dependency_overrides[current_user] = _override(user)
    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://test")


async def _wipe(db):
    await db[sm.COLLECTION].delete_many({})
    await db[aliases.COLLECTION].delete_many({})
    await db[cand.COLLECTION].delete_many({})
    await db.app_settings.delete_many({"key": MAPPING_KEY})
    await db.orders.delete_many({"po_number": {"$regex": r"^PO-CAND-"}})
    await db.audit_logs.delete_many({
        "action": {"$regex": r"^settings\.(sales_master|sales_master_alias|sales_label_candidate)"},
    })
    await sm.ensure_indexes(db)
    await aliases.ensure_indexes(db)
    await cand.ensure_indexes(db)


async def _seed_ayub_master(db, *, active=True, name=AYUB):
    admin = _user("admin")
    pub = await sm.create_sales_master(db, {
        "sales_name": name,
        "active": active,
        "branch_ids": [],
        "user_id": None,
    }, admin)
    return pub


@pytest.mark.anyio
async def test_known_ayub_import_no_candidate():
    import server
    await _wipe(server.db)
    master = await _seed_ayub_master(server.db)
    order = {
        "id": str(uuid.uuid4()),
        "sales": f"  {AYUB}  ",
        "sales_master_id": None,
    }
    mid, label, _ = await sm.resolve_for_po_write(
        server.db, sales_master_id=None, sales_label=order["sales"], user=None,
    )
    assert mid == master["id"]
    order["sales_master_id"] = mid
    order["sales"] = label
    await sm.after_import_sales_identity(server.db, order)
    open_rows = await cand.list_candidates(server.db, open_only=True)
    assert open_rows == []
    alias = await aliases.get_alias(server.db, AYUB)
    assert alias and alias["sales_master_id"] == master["id"]


@pytest.mark.anyio
async def test_unknown_ayub_creates_one_candidate_and_preserves_label():
    import server
    await _wipe(server.db)
    order = {"id": "po-1", "sales": AYUB, "sales_master_id": None}
    mid, label, _ = await sm.resolve_for_po_write(
        server.db, sales_master_id=None, sales_label=AYUB, user=None,
    )
    assert mid is None
    assert label == AYUB
    await sm.after_import_sales_identity(server.db, order)
    rows = await cand.list_candidates(server.db, open_only=True)
    assert len(rows) == 1
    assert rows[0]["exact_label"] == AYUB
    assert rows[0]["seen_count"] == 1
    assert await sm.get_sales_master(server.db, rows[0].get("sales_master_id") or "") is None


@pytest.mark.anyio
async def test_hundred_pos_one_candidate():
    import server
    await _wipe(server.db)
    for i in range(100):
        await sm.after_import_sales_identity(server.db, {
            "id": f"po-{i}",
            "sales": AYUB,
            "sales_master_id": None,
        })
    rows = await cand.list_candidates(server.db, open_only=True)
    assert len(rows) == 1
    assert rows[0]["seen_count"] == 100
    assert rows[0]["exact_label"] == AYUB


@pytest.mark.anyio
async def test_concurrent_import_one_candidate():
    import server
    await _wipe(server.db)

    async def one(i):
        await sm.after_import_sales_identity(server.db, {
            "id": f"cpo-{i}",
            "sales": AYUB,
            "sales_master_id": None,
        })

    await asyncio.gather(*[one(i) for i in range(40)])
    rows = await cand.list_candidates(server.db, open_only=True)
    assert len(rows) == 1
    assert rows[0]["seen_count"] == 40


@pytest.mark.anyio
async def test_blank_label_no_candidate():
    import server
    await _wipe(server.db)
    await sm.after_import_sales_identity(server.db, {"id": "x", "sales": "   ", "sales_master_id": None})
    await sm.after_import_sales_identity(server.db, {"id": "y", "sales": None, "sales_master_id": None})
    assert await cand.list_candidates(server.db, open_only=True) == []


@pytest.mark.anyio
async def test_exact_match_case_sensitive_spacing():
    import server
    await _wipe(server.db)
    await sm.after_import_sales_identity(server.db, {"id": "a", "sales": "Ayub", "sales_master_id": None})
    await sm.after_import_sales_identity(server.db, {"id": "b", "sales": "ayub", "sales_master_id": None})
    await sm.after_import_sales_identity(server.db, {"id": "c", "sales": "  Ayub  ", "sales_master_id": None})
    rows = await cand.list_candidates(server.db, open_only=True)
    labels = sorted(r["exact_label"] for r in rows)
    # strip-only: "Ayub" and "ayub" are distinct; leading/trailing space collapsed by strip
    assert labels == ["Ayub", "ayub"]
    ayub = next(r for r in rows if r["exact_label"] == "Ayub")
    assert ayub["seen_count"] == 2


@pytest.mark.anyio
async def test_approve_new_and_future_resolve_mapping_unchanged():
    import server
    await _wipe(server.db)
    admin = _user("admin")
    await server.db.users.delete_many({"email": admin["email"]})
    await server.db.users.insert_one({
        "id": admin["id"], "email": admin["email"], "name": admin["name"],
        "role": "admin", "status": "active", "permissions_override": {},
    })
    await sm.after_import_sales_identity(server.db, {"id": "p1", "sales": AYUB})
    rows = await cand.list_candidates(server.db, open_only=True)
    cid = rows[0]["id"]

    before_map = await load_mapping_doc(server.db)
    async with await _client(admin) as client:
        res = await client.post(f"/api/settings/sales-candidates/{cid}/approve-new", json={
            "sales_name": "Ayub Official",
        })
        assert res.status_code == 200, res.text
        body = res.json()
        assert body["mapping_unchanged"] is True
        assert body["candidate"]["status"] == cand.STATUS_APPROVED_NEW
        mid = body["master"]["id"]
        # idempotent re-approve
        res2 = await client.post(f"/api/settings/sales-candidates/{cid}/approve-new", json={})
        assert res2.status_code == 200
        assert res2.json().get("idempotent") is True

    after_map = await load_mapping_doc(server.db)
    assert (before_map.get("entries") or []) == (after_map.get("entries") or [])

    mid2, label2, _ = await sm.resolve_for_po_write(
        server.db, sales_master_id=None, sales_label=AYUB, user=None,
    )
    assert mid2 == mid
    assert exact_sales_label(label2) == exact_sales_label("Ayub Official")


@pytest.mark.anyio
async def test_link_existing_and_duplicate_alias_conflict():
    import server
    await _wipe(server.db)
    admin = _user("admin")
    await server.db.users.delete_many({"email": admin["email"]})
    await server.db.users.insert_one({
        "id": admin["id"], "email": admin["email"], "name": admin["name"],
        "role": "admin", "status": "active", "permissions_override": {},
    })
    m1 = await sm.create_sales_master(server.db, {"sales_name": "Master Satu"}, admin)
    m2 = await sm.create_sales_master(server.db, {"sales_name": "Master Dua"}, admin)
    await aliases.bind_alias(server.db, exact_label=AYUB, sales_master_id=m1["id"], user=admin)

    await sm.after_import_sales_identity(server.db, {"id": "p2", "sales": "Label Baru"})
    rows = await cand.list_candidates(server.db, open_only=True)
    cid = rows[0]["id"]

    async with await _client(admin) as client:
        ok = await client.post(f"/api/settings/sales-candidates/{cid}/link", json={
            "sales_master_id": m2["id"],
        })
        assert ok.status_code == 200, ok.text
        assert ok.json()["candidate"]["status"] == cand.STATUS_LINKED

        # Conflict: try to bind AYUB (owned by m1) via a fresh candidate forced status
        await cand.record_unrecognized_label(server.db, sales_label=AYUB, po_id="z")
        # AYUB already aliased — record_unrecognized closes open candidate; force a pending row for conflict API
        forced_id = str(uuid.uuid4())
        await server.db[cand.COLLECTION].insert_one({
            "id": forced_id,
            "exact_label": "Label Konflik",
            "status": cand.STATUS_PENDING,
            "seen_count": 1,
            "first_seen_at": "t",
            "last_seen_at": "t",
            "sample_po_ids": [],
        })
        # Bind Label Konflik to m1 first
        await aliases.bind_alias(server.db, exact_label="Label Konflik", sales_master_id=m1["id"], user=admin)
        # Re-open candidate artificially
        await server.db[cand.COLLECTION].update_one(
            {"id": forced_id}, {"$set": {"status": cand.STATUS_PENDING, "sales_master_id": None}},
        )
        bad = await client.post(f"/api/settings/sales-candidates/{forced_id}/link", json={
            "sales_master_id": m2["id"],
        })
        assert bad.status_code == 409


@pytest.mark.anyio
async def test_link_inactive_master_rejected():
    import server
    await _wipe(server.db)
    admin = _user("admin")
    await server.db.users.delete_many({"email": admin["email"]})
    await server.db.users.insert_one({
        "id": admin["id"], "email": admin["email"], "name": admin["name"],
        "role": "admin", "status": "active", "permissions_override": {},
    })
    inactive = await sm.create_sales_master(server.db, {
        "sales_name": "Nonaktif Sales", "active": False,
    }, admin)
    await sm.after_import_sales_identity(server.db, {"id": "p3", "sales": "Label Nonaktif"})
    cid = (await cand.list_candidates(server.db, open_only=True))[0]["id"]
    async with await _client(admin) as client:
        res = await client.post(f"/api/settings/sales-candidates/{cid}/link", json={
            "sales_master_id": inactive["id"],
        })
        assert res.status_code == 409


@pytest.mark.anyio
async def test_permission_denied_for_viewer():
    import server
    await _wipe(server.db)
    viewer = _user("admin", override={
        "settings.sales_master.view": True,
        "settings.sales_master.manage": False,
        "permissions.manage": False,
    })
    # Force non-admin role so override matters
    viewer["role"] = "crm"
    await server.db.users.delete_many({"email": viewer["email"]})
    await server.db.users.insert_one({
        "id": viewer["id"], "email": viewer["email"], "name": viewer["name"],
        "role": "crm", "status": "active",
        "permissions_override": viewer["permissions_override"],
    })
    await sm.after_import_sales_identity(server.db, {"id": "p4", "sales": "X Label"})
    cid = (await cand.list_candidates(server.db, open_only=True))[0]["id"]
    async with await _client(viewer) as client:
        listed = await client.get("/api/settings/sales-candidates?open_only=true")
        assert listed.status_code == 200
        denied = await client.post(f"/api/settings/sales-candidates/{cid}/approve-new", json={})
        assert denied.status_code in (401, 403)


@pytest.mark.anyio
async def test_historical_and_financial_unchanged_after_approve():
    import server
    await _wipe(server.db)
    admin = _user("admin")
    await server.db.users.delete_many({"email": admin["email"]})
    await server.db.users.insert_one({
        "id": admin["id"], "email": admin["email"], "name": admin["name"],
        "role": "admin", "status": "active", "permissions_override": {},
    })
    # Seed mapping + historical docs
    sales_uid = "u-ayub-hist"
    await server.db.users.insert_one({
        "id": sales_uid, "email": "ayub-hist@eternal.test", "name": "Ayub",
        "role": "sales_eternal", "status": "active",
    })
    await server.db.app_settings.update_one(
        {"key": MAPPING_KEY},
        {"$set": {"key": MAPPING_KEY, "entries": [{"label": AYUB, "user_id": sales_uid}]}},
        upsert=True,
    )
    po = {"id": "hist-po", "po_number": "PO-CAND-HIST", "sales": AYUB, "sales_master_id": None}
    inv = {"id": "hist-inv", "sales": AYUB, "sales_name": AYUB}
    sj = {"id": "hist-sj", "sales": AYUB}
    await server.db.orders.insert_one(dict(po))
    await server.db.sales_invoices.insert_one(dict(inv))
    await server.db.sales_deliveries.insert_one(dict(sj))

    mapping = mapping_dict_from_entries((await load_mapping_doc(server.db)).get("entries"))
    before_attr = _resolve_sales_attribution(AYUB, mapping, set(), {sales_uid: {"id": sales_uid, "role": "sales_eternal", "status": "active", "name": "Ayub"}})

    await sm.after_import_sales_identity(server.db, {"id": "new", "sales": AYUB})
    cid = (await cand.list_candidates(server.db, open_only=True))[0]["id"]
    async with await _client(admin) as client:
        res = await client.post(f"/api/settings/sales-candidates/{cid}/approve-new", json={"sales_name": "Ayub Official"})
        assert res.status_code == 200

    po2 = await server.db.orders.find_one({"id": "hist-po"}, {"_id": 0})
    inv2 = await server.db.sales_invoices.find_one({"id": "hist-inv"}, {"_id": 0})
    sj2 = await server.db.sales_deliveries.find_one({"id": "hist-sj"}, {"_id": 0})
    assert po2["sales"] == AYUB
    assert inv2["sales"] == AYUB
    assert sj2["sales"] == AYUB

    mapping2 = mapping_dict_from_entries((await load_mapping_doc(server.db)).get("entries"))
    after_attr = _resolve_sales_attribution(AYUB, mapping2, set(), {sales_uid: {"id": sales_uid, "role": "sales_eternal", "status": "active", "name": "Ayub"}})
    assert before_attr == after_attr
    assert mapping == mapping2


@pytest.mark.anyio
async def test_legacy_settings_apis_still_work():
    import server
    await _wipe(server.db)
    admin = _user("admin")
    await server.db.users.delete_many({"email": admin["email"]})
    await server.db.users.insert_one({
        "id": admin["id"], "email": admin["email"], "name": admin["name"],
        "role": "admin", "status": "active", "permissions_override": {},
    })
    async with await _client(admin) as client:
        created = await client.post("/api/settings/sales-master", json={"sales_name": "Legacy API Sales"})
        assert created.status_code == 200, created.text
        listed = await client.get("/api/settings/sales-master")
        assert listed.status_code == 200
        assert any(i["sales_name"] == "Legacy API Sales" for i in listed.json()["items"])
        mapped = await client.get("/api/settings/sales-user-mapping")
        assert mapped.status_code == 200
        assert "settings" in mapped.json()
        aliases_res = await client.get(
            f"/api/settings/sales-master/{created.json()['item']['id']}/aliases"
        )
        assert aliases_res.status_code == 200
        assert any(a["exact_label"] == "Legacy API Sales" for a in aliases_res.json()["items"])


@pytest.mark.anyio
async def test_persist_imported_po_hooks_candidate(monkeypatch):
    import server
    await _wipe(server.db)
    admin = _user("admin")
    from server import _persist_imported_po

    async def _ok_branch(*a, **k):
        return "br-test"

    async def _ok_stock(*a, **k):
        return []

    async def _ok_snap(*a, **k):
        return None

    monkeypatch.setattr("services.operational_branch.resolve_write_branch", _ok_branch)
    monkeypatch.setattr("server.validate_sales_order_availability", _ok_stock)
    monkeypatch.setattr("services.physical_stock_engine.attach_order_stock_snapshot", _ok_snap)

    preview = {
        "po_number": f"PO-CAND-{uuid.uuid4().hex[:6]}",
        "order_date": "2026-01-01",
        "estimated_delivery": "2026-01-05",
        "deadline": "2026-01-05",
        "customer": "Cust",
        "sales": "Brand New Salesperson",
        "line_items": [],
        "total_quantity": 0,
        "item_count": 0,
    }
    saved = await _persist_imported_po(preview, source="accurate_mock", user=admin)
    assert saved["sales"] == "Brand New Salesperson"
    assert not saved.get("sales_master_id")
    rows = await cand.list_candidates(server.db, open_only=True)
    assert len(rows) == 1
    assert rows[0]["exact_label"] == "Brand New Salesperson"
