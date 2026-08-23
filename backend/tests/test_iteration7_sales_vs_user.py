"""Iteration 7: split 'sales_user' into distinct `sales` (tenaga penjual)
and `created_by_user` (user pembuat PO); `imported_by`/`imported_by_name`/
`imported_at` are stored SEPARATELY on Accurate-imported orders.

- Preview payload MUST expose both `sales` and `created_by_user`; a missing
  `created_by_user` becomes JSON null, never a guess.
- Confirm persists sales/created_by_user + imported_by/imported_by_name/
  imported_at as separate keys.
- Bulk sync (_SALES_FIELDS) must NOT touch legacy `sales_user`.
- Seed/startup must NOT auto-migrate pre-existing orders lacking `sales`.
"""
import os
import asyncio
import pytest
import requests
from motor.motor_asyncio import AsyncIOMotorClient

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "").rstrip("/")
if not BASE_URL:
    for line in open("/app/frontend/.env"):
        if line.startswith("REACT_APP_BACKEND_URL="):
            BASE_URL = line.split("=", 1)[1].strip().rstrip("/")

PASSWORD = "Eternal123!"
ADMIN = "admin@eternalpackaging.com"
HOP = "production@eternalpackaging.com"


def _get_mongo():
    mongo_url = os.environ.get("MONGO_URL")
    db_name = os.environ.get("DB_NAME")
    if not mongo_url:
        for line in open("/app/backend/.env"):
            if line.startswith("MONGO_URL="):
                mongo_url = line.split("=", 1)[1].strip().strip('"').strip("'")
            if line.startswith("DB_NAME="):
                db_name = line.split("=", 1)[1].strip().strip('"').strip("'")
    return mongo_url, db_name


def _login(email):
    s = requests.Session()
    r = s.post(f"{BASE_URL}/api/auth/login", json={"email": email, "password": PASSWORD}, timeout=15)
    assert r.status_code == 200, f"login failed: {r.text}"
    return s


@pytest.fixture(scope="module")
def admin():
    return _login(ADMIN)


@pytest.fixture(scope="module")
def hop():
    return _login(HOP)


@pytest.fixture(scope="module", autouse=True)
def reset_state():
    """Delete only importable POs + logs. Do NOT touch seeded PO-1045..PO-1048."""
    mongo_url, db_name = _get_mongo()

    async def _run():
        c = AsyncIOMotorClient(mongo_url)
        db = c[db_name]
        await db.orders.delete_many({"po_number": {"$in": ["PO-1049", "PO-1050", "PO-2001", "PO-2002"]}})
        await db.import_logs.delete_many({})
        c.close()

    asyncio.new_event_loop().run_until_complete(_run())
    yield


# -------- Preview shape --------
class TestPreviewShape:
    def test_preview_1049_has_both_fields(self, admin):
        r = admin.post(f"{BASE_URL}/api/accurate/import/preview", json={"so_number": "PO-1049"}, timeout=15)
        assert r.status_code == 200, r.text
        prev = r.json()["preview"]
        assert prev["sales"] == "Vira"
        assert prev["created_by_user"] == "Rina (Sales Admin)"
        assert "sales_user" not in prev, "preview must not expose legacy sales_user"

    def test_preview_1050_missing_created_by_is_null(self, admin):
        r = admin.post(f"{BASE_URL}/api/accurate/import/preview", json={"so_number": "PO-1050"}, timeout=15)
        assert r.status_code == 200, r.text
        prev = r.json()["preview"]
        assert prev["sales"] == "Alfi"
        assert prev["created_by_user"] is None, "missing field must be null, never guessed"
        assert "sales_user" not in prev

    def test_preview_2001_and_2002_both_present(self, admin):
        for so in ("PO-2001", "PO-2002"):
            r = admin.post(f"{BASE_URL}/api/accurate/import/preview", json={"so_number": so}, timeout=15)
            assert r.status_code == 200, r.text
            prev = r.json()["preview"]
            assert prev["sales"] and prev["created_by_user"], f"{so}: sales+created_by_user required"


# -------- Confirm persists new fields separately --------
class TestConfirmPersistence:
    def test_confirm_2002_new_fields(self, admin):
        r = admin.post(f"{BASE_URL}/api/accurate/import/confirm", json={"so_number": "PO-2002"}, timeout=15)
        assert r.status_code == 200, r.text
        order = r.json()["order"]
        assert order["sales"] == "Vira"
        assert order["created_by_user"] == "Rina (Sales Admin)"
        assert order["imported_by"] == ADMIN
        assert order["imported_by_name"], "imported_by_name required"
        assert order["imported_at"]
        assert order["accurate_id"] == "ACC-SO-2002"
        # imported_by MUST NOT equal created_by_user (separate storage)
        assert order["imported_by"] != order["created_by_user"]

        # GET verifies persistence
        gr = admin.get(f"{BASE_URL}/api/orders/{order['id']}", timeout=15)
        assert gr.status_code == 200
        o2 = gr.json()["order"]
        for k in ("sales", "created_by_user", "imported_by", "imported_by_name", "imported_at", "accurate_id"):
            assert o2[k] == order[k], f"{k} did not persist"

    def test_confirm_1050_created_by_user_null_preserved(self, admin):
        r = admin.post(f"{BASE_URL}/api/accurate/import/confirm", json={"so_number": "PO-1050"}, timeout=15)
        assert r.status_code == 200, r.text
        order = r.json()["order"]
        assert order["sales"] == "Alfi"
        assert order["created_by_user"] is None, "must NOT default null to admin name"
        assert order["imported_by"] == ADMIN
        assert order["imported_by_name"], "imported_by_name still recorded"
        # Separate keys: null created_by_user does NOT become admin
        assert order["imported_by"] != order["created_by_user"]

        # Verify direct DB shape
        mongo_url, db_name = _get_mongo()

        async def _fetch():
            c = AsyncIOMotorClient(mongo_url)
            db = c[db_name]
            doc = await db.orders.find_one({"po_number": "PO-1050"}, {"_id": 0})
            c.close()
            return doc

        doc = asyncio.new_event_loop().run_until_complete(_fetch())
        assert doc["created_by_user"] is None
        assert doc["imported_by"] == ADMIN


# -------- Legacy sync must not touch sales_user --------
class TestBulkSyncDoesNotTouchLegacy:
    def test_sync_preserves_sales_user(self, admin):
        mongo_url, db_name = _get_mongo()

        async def _preset():
            c = AsyncIOMotorClient(mongo_url)
            db = c[db_name]
            # PO-1048 is a legacy seeded order that has sales_user="Vira"
            await db.orders.update_one(
                {"po_number": "PO-1048"},
                {"$set": {"sales_user": "LegacyName"}},
            )
            c.close()

        asyncio.new_event_loop().run_until_complete(_preset())

        # Run bulk sync (mock only refreshes _SALES_FIELDS on existing docs).
        # PO-1048 isn't in the mock catalog so it won't be touched — pick a mock
        # PO that IS existing to properly stress the codepath. First import
        # PO-1049 to make it an "existing" doc, seed a legacy sales_user, then
        # run sync.
        r = admin.post(f"{BASE_URL}/api/accurate/import/confirm", json={"so_number": "PO-1049"}, timeout=15)
        assert r.status_code == 200

        async def _seed_legacy():
            c = AsyncIOMotorClient(mongo_url)
            db = c[db_name]
            await db.orders.update_one(
                {"po_number": "PO-1049"},
                {"$set": {"sales_user": "LegacyName-1049"}},
            )
            c.close()

        asyncio.new_event_loop().run_until_complete(_seed_legacy())

        r = admin.post(f"{BASE_URL}/api/accurate/sync", timeout=30)
        assert r.status_code == 200, r.text

        async def _fetch():
            c = AsyncIOMotorClient(mongo_url)
            db = c[db_name]
            d1049 = await db.orders.find_one({"po_number": "PO-1049"}, {"_id": 0})
            d1048 = await db.orders.find_one({"po_number": "PO-1048"}, {"_id": 0})
            c.close()
            return d1049, d1048

        d1049, d1048 = asyncio.new_event_loop().run_until_complete(_fetch())
        # sales_user MUST be preserved by sync (not overwritten)
        assert d1049.get("sales_user") == "LegacyName-1049", "sync clobbered legacy sales_user"
        assert d1048.get("sales_user") == "LegacyName", "sync mutated an unrelated PO"
        # sales SHOULD get refreshed to mock value
        assert d1049.get("sales") == "Vira"
        assert d1049.get("created_by_user") == "Rina (Sales Admin)"


# -------- No auto-migration --------
class TestNoAutoMigration:
    def test_legacy_order_untouched_by_startup(self):
        """Directly craft a legacy-shaped order lacking `sales`, then restart-safe:
        seeding logic only fires on empty collection, so this doc must survive
        without any silent migration on subsequent GET/list operations."""
        mongo_url, db_name = _get_mongo()

        async def _insert_legacy():
            c = AsyncIOMotorClient(mongo_url)
            db = c[db_name]
            # Insert a synthetic legacy PO with only sales_user set.
            await db.orders.delete_many({"po_number": "PO-LEGACY-TEST"})
            await db.orders.insert_one({
                "id": "legacy-1",
                "po_number": "PO-LEGACY-TEST",
                "customer": "Legacy Cust",
                "outlet": "Legacy Outlet",
                "recipient": "Legacy",
                "address": "Legacy addr",
                "sales_user": "LegacyOnly",
                "current_stage": 0,
                "status_label": "Order Masuk",
                "priority": "normal",
                "payment_status": "Waiting",
                "design_status": "Waiting",
                "line_items": [],
                "operator_splits": [],
                "timeline": [],
                "created_at": "2020-01-01T00:00:00+00:00",
            })
            c.close()

        asyncio.new_event_loop().run_until_complete(_insert_legacy())

        # Simulate a normal read; server must not silently write missing fields.
        s = _login(ADMIN)
        r = s.get(f"{BASE_URL}/api/orders/legacy-1", timeout=15)
        assert r.status_code == 200
        # Now re-read DB and confirm `sales` was NOT added by any code path.

        async def _fetch():
            c = AsyncIOMotorClient(mongo_url)
            db = c[db_name]
            d = await db.orders.find_one({"id": "legacy-1"})
            c.close()
            return d

        d = asyncio.new_event_loop().run_until_complete(_fetch())
        assert "sales" not in d, "auto-migration wrote `sales` — forbidden"
        assert d["sales_user"] == "LegacyOnly"

        # Cleanup
        async def _clean():
            c = AsyncIOMotorClient(mongo_url)
            db = c[db_name]
            await db.orders.delete_many({"po_number": "PO-LEGACY-TEST"})
            c.close()

        asyncio.new_event_loop().run_until_complete(_clean())


# -------- _SALES_FIELDS spec --------
class TestSalesFieldsSpec:
    def test_sales_fields_shape(self):
        from services.accurate import _SALES_FIELDS
        assert "sales" in _SALES_FIELDS
        assert "created_by_user" in _SALES_FIELDS
        assert "sales_user" not in _SALES_FIELDS, "sync must not clobber legacy sales_user"
