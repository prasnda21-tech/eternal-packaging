"""New-import ownership: mapping first, Master alias fallback, conflict fail-closed."""
import os
import sys
import uuid
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ.setdefault("MONGO_URL", "mongodb://127.0.0.1:27017")
os.environ.setdefault("DB_NAME", "eternal_packaging_test_sales_ownership_pwa")
os.environ.setdefault("JWT_SECRET", "test-secret-key-12345678901234567890")
os.environ.setdefault("CORS_ORIGINS", "*")

from server import app, current_user
from services import sales_master as sm
from services import sales_master_aliases as aliases
from services.sales_user_mapping import (
    SETTINGS_KEY,
    SOURCE_CONFLICT,
    SOURCE_MAPPING,
    SOURCE_MASTER_ALIAS,
    STATUS_IDENTITY_CONFLICT,
    STATUS_MATCHED,
    STATUS_MATCHED_MASTER,
    STATUS_UNMATCHED,
    attach_sales_ownership,
    exact_sales_label,
    sales_owner_match,
)


AYUB_LABEL = "Sholahudin Al - Ayubi"
AYUB_ID = "user-ayub-pwa"
EUIS_ID = "user-euis-pwa"


def _user(role, *, uid=None, name=None):
    ident = uid or f"{role}-{uuid.uuid4().hex[:6]}"
    return {
        "id": ident,
        "user_id": ident,
        "email": f"{ident}@eternal.test",
        "name": name or role,
        "role": role,
        "status": "active",
        "permissions_override": {},
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


async def _wipe(db):
    await db[sm.COLLECTION].delete_many({})
    await db[aliases.COLLECTION].delete_many({})
    await db.app_settings.delete_many({"key": SETTINGS_KEY})
    await db.users.delete_many({"id": {"$in": [AYUB_ID, EUIS_ID]}})
    await db.orders.delete_many({"po_number": {"$regex": r"^PO-OWN-"}})
    await sm.ensure_indexes(db)
    await aliases.ensure_indexes(db)


async def _seed_users(db):
    for uid, name in ((AYUB_ID, "Ayub"), (EUIS_ID, "Euis")):
        await db.users.update_one(
            {"id": uid},
            {"$set": {
                "id": uid,
                "email": f"{uid}@eternal.test",
                "name": name,
                "role": "sales_eternal",
                "status": "active",
            }},
            upsert=True,
        )


async def _seed_master_with_alias(db, *, label, user_id, active=True, name=None):
    admin = _user("admin")
    pub = await sm.create_sales_master(db, {
        "sales_name": name or label,
        "user_id": user_id,
        "active": active,
        "branch_ids": [],
    }, admin)
    await aliases.bind_alias(
        db, exact_label=label, sales_master_id=pub["id"], user=admin, note="test",
    )
    return pub


@pytest.mark.anyio
async def test_verified_alias_plus_account_assigns_new_po():
    import server
    await _wipe(server.db)
    await _seed_users(server.db)
    await _seed_master_with_alias(server.db, label=AYUB_LABEL, user_id=AYUB_ID, name="Ayub Official")

    doc = {"sales": AYUB_LABEL}
    uid, status = await attach_sales_ownership(server.db, doc, allow_master_fallback=True)
    assert uid == AYUB_ID
    assert status == STATUS_MATCHED_MASTER
    assert doc["sales_ownership_source"] == SOURCE_MASTER_ALIAS
    assert doc["sales_needs_verification"] is False


@pytest.mark.anyio
async def test_alias_only_no_user_no_ownership():
    import server
    await _wipe(server.db)
    await _seed_users(server.db)
    await _seed_master_with_alias(server.db, label=AYUB_LABEL, user_id=None, name="Ayub No Login")

    doc = {"sales": AYUB_LABEL}
    uid, status = await attach_sales_ownership(server.db, doc, allow_master_fallback=True)
    assert uid is None
    assert status == STATUS_UNMATCHED


@pytest.mark.anyio
async def test_mapping_only_unchanged_without_fallback_flag():
    import server
    await _wipe(server.db)
    await _seed_users(server.db)
    await server.db.app_settings.update_one(
        {"key": SETTINGS_KEY},
        {"$set": {"key": SETTINGS_KEY, "entries": [{"label": AYUB_LABEL, "user_id": AYUB_ID}]}},
        upsert=True,
    )
    doc = {"sales": AYUB_LABEL}
    uid, status = await attach_sales_ownership(server.db, doc)  # default no fallback
    assert uid == AYUB_ID
    assert status == STATUS_MATCHED
    assert doc["sales_ownership_source"] == SOURCE_MAPPING


@pytest.mark.anyio
async def test_mapping_and_master_same_user_accepted():
    import server
    await _wipe(server.db)
    await _seed_users(server.db)
    await _seed_master_with_alias(server.db, label=AYUB_LABEL, user_id=AYUB_ID)
    await server.db.app_settings.update_one(
        {"key": SETTINGS_KEY},
        {"$set": {"key": SETTINGS_KEY, "entries": [{"label": AYUB_LABEL, "user_id": AYUB_ID}]}},
        upsert=True,
    )
    doc = {"sales": AYUB_LABEL}
    uid, status = await attach_sales_ownership(server.db, doc, allow_master_fallback=True)
    assert uid == AYUB_ID
    assert status == STATUS_MATCHED
    assert doc["sales_ownership_source"] == SOURCE_MAPPING


@pytest.mark.anyio
async def test_mapping_and_master_different_users_conflict():
    import server
    await _wipe(server.db)
    await _seed_users(server.db)
    await _seed_master_with_alias(server.db, label=AYUB_LABEL, user_id=AYUB_ID)
    await server.db.app_settings.update_one(
        {"key": SETTINGS_KEY},
        {"$set": {"key": SETTINGS_KEY, "entries": [{"label": AYUB_LABEL, "user_id": EUIS_ID}]}},
        upsert=True,
    )
    doc = {"sales": AYUB_LABEL, "sales_user_id": "stale"}
    uid, status = await attach_sales_ownership(server.db, doc, allow_master_fallback=True)
    assert uid is None
    assert status == STATUS_IDENTITY_CONFLICT
    assert doc["sales_user_id"] is None
    assert doc["sales_ownership_source"] == SOURCE_CONFLICT
    assert doc["sales_needs_verification"] is True
    assert "konflik" in (doc.get("sales_ownership_admin_label") or "")


@pytest.mark.anyio
async def test_inactive_master_no_fallback():
    import server
    await _wipe(server.db)
    await _seed_users(server.db)
    await _seed_master_with_alias(server.db, label=AYUB_LABEL, user_id=AYUB_ID, active=False)
    doc = {"sales": AYUB_LABEL}
    uid, status = await attach_sales_ownership(server.db, doc, allow_master_fallback=True)
    assert uid is None
    assert status == STATUS_UNMATCHED


@pytest.mark.anyio
async def test_wrong_role_or_inactive_account_no_fallback():
    import server
    await _wipe(server.db)
    bad = "user-operator"
    await server.db.users.update_one(
        {"id": bad},
        {"$set": {"id": bad, "email": "op@t", "name": "Op", "role": "operator", "status": "active"}},
        upsert=True,
    )
    await _seed_master_with_alias(server.db, label="Label Op", user_id=bad, name="Op Sales")
    doc = {"sales": "Label Op"}
    uid, status = await attach_sales_ownership(server.db, doc, allow_master_fallback=True)
    assert uid is None

    await _wipe(server.db)
    # Master row linked to inactive sales_eternal — bypass create validator.
    await server.db.users.update_one(
        {"id": AYUB_ID},
        {"$set": {"id": AYUB_ID, "email": "a@t", "name": "A", "role": "sales_eternal", "status": "inactive"}},
        upsert=True,
    )
    mid = str(uuid.uuid4())
    await server.db[sm.COLLECTION].insert_one({
        "id": mid,
        "sales_name": "Ayub Inactive",
        "name_key": sm.name_key("Ayub Inactive"),
        "user_id": AYUB_ID,
        "branch_ids": [],
        "active": True,
        "sort_order": 0,
        "doc_version": 1,
    })
    await aliases.ensure_alias_silent(server.db, exact_label=AYUB_LABEL, sales_master_id=mid)
    doc2 = {"sales": AYUB_LABEL}
    uid2, _ = await attach_sales_ownership(server.db, doc2, allow_master_fallback=True)
    assert uid2 is None


@pytest.mark.anyio
async def test_legacy_owned_visible_null_inaccessible():
    import server
    await _wipe(server.db)
    await _seed_users(server.db)
    ayub = {"id": AYUB_ID, "user_id": AYUB_ID, "role": "sales_eternal"}
    owned = {"id": "o1", "po_number": "PO-OWN-1", "sales_user_id": AYUB_ID, "cancelled": False}
    orphan = {"id": "o2", "po_number": "PO-OWN-2", "sales_user_id": None, "cancelled": False}
    await server.db.orders.insert_many([owned, orphan])
    match = sales_owner_match(ayub)
    found = await server.db.orders.find({"cancelled": {"$ne": True}, **match}, {"_id": 0, "id": 1}).to_list(10)
    ids = {r["id"] for r in found}
    assert "o1" in ids
    assert "o2" not in ids


@pytest.mark.anyio
async def test_sync_existing_does_not_reassign_ownership(monkeypatch):
    import server
    from services.accurate import AccurateClient, sync_sales_orders

    await _wipe(server.db)
    await _seed_users(server.db)
    await server.db.app_settings.update_one(
        {"key": SETTINGS_KEY},
        {"$set": {"key": SETTINGS_KEY, "entries": [{"label": AYUB_LABEL, "user_id": AYUB_ID}]}},
        upsert=True,
    )
    existing = {
        "id": str(uuid.uuid4()),
        "po_number": "PO-OWN-SYNC-1",
        "sales": AYUB_LABEL,
        "sales_user_id": EUIS_ID,  # historical owner
        "sales_ownership_status": "matched",
        "cancelled": False,
        "order_date": "2026-01-01",
        "estimated_delivery": "2026-01-05",
        "line_items": [],
        "current_stage": 0,
    }
    await server.db.orders.insert_one(dict(existing))

    class FakeClient(AccurateClient):
        def __init__(self):
            pass

        @property
        def is_live(self):
            return False

        async def fetch_sales_orders(self):
            return [{
                "po_number": "PO-OWN-SYNC-1",
                "customer": "C",
                "outlet": None,
                "recipient": None,
                "address": None,
                "payment_terms": None,
                "sales": AYUB_LABEL,
                "created_by_user": None,
                "expedition": None,
                "delivery_zone": None,
                "seller": AYUB_LABEL,
                "line_items": [],
                "order_date_offset_days": 0,
            }]

    async def _ok_lines(db, client, lines, require_master=True):
        return lines or []

    monkeypatch.setattr("services.master_items.resolve_po_line_items", _ok_lines)
    await sync_sales_orders(server.db, FakeClient())
    row = await server.db.orders.find_one({"po_number": "PO-OWN-SYNC-1"}, {"_id": 0})
    assert row["sales_user_id"] == EUIS_ID  # unchanged


@pytest.mark.anyio
async def test_persist_imported_po_uses_master_fallback(monkeypatch):
    import server
    from server import _persist_imported_po

    await _wipe(server.db)
    await _seed_users(server.db)
    await _seed_master_with_alias(server.db, label=AYUB_LABEL, user_id=AYUB_ID, name="Ayub Official")

    async def _ok_branch(*a, **k):
        return "br-test"

    async def _ok_stock(*a, **k):
        return []

    async def _ok_snap(*a, **k):
        return None

    monkeypatch.setattr("services.operational_branch.resolve_write_branch", _ok_branch)
    monkeypatch.setattr("server.validate_sales_order_availability", _ok_stock)
    monkeypatch.setattr("services.physical_stock_engine.attach_order_stock_snapshot", _ok_snap)

    admin = _user("admin")
    preview = {
        "po_number": f"PO-OWN-{uuid.uuid4().hex[:6]}",
        "order_date": "2026-01-01",
        "estimated_delivery": "2026-01-05",
        "deadline": "2026-01-05",
        "customer": "Cust",
        "sales": AYUB_LABEL,
        "line_items": [],
        "total_quantity": 0,
        "item_count": 0,
    }
    saved = await _persist_imported_po(preview, source="accurate_mock", user=admin)
    assert saved["sales_user_id"] == AYUB_ID
    assert saved["sales_ownership_status"] == STATUS_MATCHED_MASTER
    assert saved["sales_ownership_source"] == SOURCE_MASTER_ALIAS


@pytest.mark.anyio
async def test_spoof_ignored_and_foreign_po_hidden():
    """sales_owner_match ignores anything but session users.id."""
    ayub = {"id": AYUB_ID, "user_id": AYUB_ID, "role": "sales_eternal"}
    euis = {"id": EUIS_ID, "user_id": EUIS_ID, "role": "sales_eternal"}
    assert sales_owner_match(ayub) == {"sales_user_id": AYUB_ID}
    assert sales_owner_match(euis) == {"sales_user_id": EUIS_ID}
    # Non-sales role must not broaden to all orders
    assert sales_owner_match({"id": "x", "role": "admin"}) == {}
