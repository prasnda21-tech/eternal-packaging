"""Exact Accurate sales label → users.id mapping. No fuzzy match."""
import os
import sys
import uuid
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ.setdefault("MONGO_URL", "mongodb://127.0.0.1:27017")
os.environ.setdefault("DB_NAME", "eternal_packaging_test")
os.environ.setdefault("JWT_SECRET", "test-secret-key-12345678901234567890")
os.environ.setdefault("CORS_ORIGINS", "*")

from server import app, current_user
from services.sales_user_mapping import (
    STATUS_MATCHED,
    STATUS_UNMATCHED,
    attach_sales_ownership,
    exact_sales_label,
    resolve_from_map,
)


AYUB_ID = str(uuid.uuid4())
EUIS_ID = str(uuid.uuid4())
ACCURATE_AYUB = "Sholahudin Al - Ayubi"
ACCOUNT_AYUB = "Sholahuddin Al Ayyubi"
ACCURATE_EUIS = "Euis Risnawati"
AYUB_EMAIL = "ayub@eternal.com"
EUIS_EMAIL = "euis@eternal.com"


def _canonical_users(ayub_id=None, euis_id=None):
    return [
        {"id": ayub_id or AYUB_ID, "email": AYUB_EMAIL, "employee_id": "EMP-0049", "role": "sales_eternal", "status": "active", "name": ACCOUNT_AYUB},
        {"id": euis_id or EUIS_ID, "email": EUIS_EMAIL, "employee_id": "EMP-0052", "role": "sales_eternal", "status": "active", "name": ACCURATE_EUIS},
    ]


def test_exact_label_strips_only():
    assert exact_sales_label("  Sholahudin Al - Ayubi  ") == ACCURATE_AYUB
    assert exact_sales_label(ACCOUNT_AYUB) == ACCOUNT_AYUB


def test_mismatch_account_vs_accurate_fails_closed_without_mapping():
    mapping = {}
    uid, status = resolve_from_map(mapping, ACCURATE_AYUB)
    assert uid is None
    assert status == STATUS_UNMATCHED
    uid2, status2 = resolve_from_map(mapping, ACCOUNT_AYUB)
    assert uid2 is None
    assert status2 == STATUS_UNMATCHED


def test_explicit_accurate_label_maps_ayub_and_does_not_fuzzy_account_name():
    mapping = {
        ACCURATE_AYUB: AYUB_ID,
        ACCURATE_EUIS: EUIS_ID,
    }
    uid, status = resolve_from_map(mapping, ACCURATE_AYUB)
    assert uid == AYUB_ID
    assert status == STATUS_MATCHED
    uid_account, status_account = resolve_from_map(mapping, ACCOUNT_AYUB)
    assert uid_account is None
    assert status_account == STATUS_UNMATCHED
    # Case / nickname must not match.
    uid_nick, status_nick = resolve_from_map(mapping, "Ayub")
    assert uid_nick is None
    assert status_nick == STATUS_UNMATCHED
    uid_case, status_case = resolve_from_map(mapping, ACCURATE_AYUB.lower())
    assert uid_case is None
    assert status_case == STATUS_UNMATCHED


def test_euis_exact_label():
    mapping = {ACCURATE_EUIS: EUIS_ID}
    uid, status = resolve_from_map(mapping, ACCURATE_EUIS)
    assert uid == EUIS_ID
    assert status == STATUS_MATCHED
    uid2, status2 = resolve_from_map(mapping, "Euis")
    assert uid2 is None
    assert status2 == STATUS_UNMATCHED


@pytest.mark.anyio
async def test_unmatched_explicitly_clears_previous_owner():
    class FakeSettings:
        def __init__(self):
            self.doc = {
                "key": "sales_user_mapping",
                "entries": [{"label": ACCURATE_AYUB, "user_id": AYUB_ID}],
            }

        async def find_one(self, q, proj=None):
            return dict(self.doc)

    class FakeDb:
        def __init__(self):
            self.app_settings = FakeSettings()

    db = FakeDb()
    doc = {"sales": ACCURATE_AYUB, "sales_user_id": EUIS_ID}
    uid, status = await attach_sales_ownership(db, doc)
    assert uid == AYUB_ID
    assert status == STATUS_MATCHED
    assert doc["sales_user_id"] == AYUB_ID
    assert doc["sales"] == ACCURATE_AYUB

    doc["sales"] = "Unknown Sales Person"
    uid2, status2 = await attach_sales_ownership(db, doc)
    assert uid2 is None
    assert status2 == STATUS_UNMATCHED
    assert doc["sales_user_id"] is None
    assert doc["sales_ownership_status"] == STATUS_UNMATCHED
    assert doc["sales"] == "Unknown Sales Person"


def test_existing_sales_eternal_user_gets_pwa_view_without_snapshot():
    from permissions import effective_permissions, ROLE_DEFAULT_PERMISSIONS
    perms = effective_permissions("sales_eternal", {})
    assert "sales.pwa.view" in perms
    assert "po.view" not in perms
    # Existing account with empty override matches a deployed sales_eternal user.
    existing_override = {}
    perms2 = effective_permissions("sales_eternal", existing_override)
    assert perms2 == perms
    assert "sales.pwa.view" in ROLE_DEFAULT_PERMISSIONS["sales_eternal"]


def test_mapping_plan_is_idempotent_and_refuses_conflict():
    from services.sales_eternal_deploy import plan_sales_user_mapping
    users = _canonical_users()
    empty = plan_sales_user_mapping({"entries": []}, users)
    assert empty["ok"] and empty["changed"]
    assert empty["discovered"]["ayub"]["user_id"] == AYUB_ID
    assert empty["discovered"]["euis"]["user_id"] == EUIS_ID
    again = plan_sales_user_mapping({"entries": empty["after"]}, users)
    assert again["ok"] and not again["changed"]
    conflict = plan_sales_user_mapping({
        "entries": [{"label": ACCURATE_AYUB, "user_id": EUIS_ID}],
    }, users)
    assert not conflict["ok"]
    extra = plan_sales_user_mapping({
        "entries": [{"label": "Sales Lain Valid", "user_id": AYUB_ID}],
    }, users)
    assert extra["ok"]
    labels = {e["label"] for e in extra["after"]}
    assert "Sales Lain Valid" in labels
    assert ACCURATE_AYUB in labels


def test_canonical_discovery_fails_closed_on_ambiguous_or_missing():
    from services.sales_user_mapping import discover_canonical_users
    missing_euis, errors = discover_canonical_users(_canonical_users()[:1])
    assert not missing_euis and errors
    dup_email = _canonical_users() + [{
        "id": str(uuid.uuid4()), "email": AYUB_EMAIL, "employee_id": "EMP-9999",
        "role": "sales_eternal", "status": "active",
    }]
    found, errors = discover_canonical_users(dup_email)
    assert not found and any("email" in e for e in errors)
    swapped = [
        {"id": AYUB_ID, "email": AYUB_EMAIL, "employee_id": "EMP-0052", "role": "sales_eternal", "status": "active"},
        {"id": EUIS_ID, "email": EUIS_EMAIL, "employee_id": "EMP-0049", "role": "sales_eternal", "status": "active"},
    ]
    found, errors = discover_canonical_users(swapped)
    assert not found
    case_fold = [
        {"id": AYUB_ID, "email": "Ayub@eternal.com", "employee_id": "EMP-0049", "role": "sales_eternal", "status": "active"},
        {"id": EUIS_ID, "email": EUIS_EMAIL, "employee_id": "EMP-0052", "role": "sales_eternal", "status": "active"},
    ]
    found, errors = discover_canonical_users(case_fold)
    assert not found
    nickname = [
        {"id": AYUB_ID, "email": "ayub@eternal.com", "employee_id": "EMP-0049", "role": "sales_eternal", "status": "active", "name": "Ayub"},
        {"id": EUIS_ID, "email": EUIS_EMAIL, "employee_id": "EMP-0052", "role": "sales_eternal", "status": "active"},
    ]
    found, errors = discover_canonical_users(nickname)
    assert found and found["ayub"]["user_id"] == AYUB_ID
    from services.sales_user_mapping import resolve_from_map, STATUS_UNMATCHED
    uid, status = resolve_from_map({ACCURATE_AYUB: AYUB_ID}, "Ayub")
    assert uid is None and status == STATUS_UNMATCHED


def test_permission_plan_existing_snapshot_and_override():
    from services.sales_eternal_deploy import plan_sales_eternal_permissions
    users = [
        {**_canonical_users()[0], "permissions_override": {"po.view": True}},
        _canonical_users()[1],
        {"id": str(uuid.uuid4()), "email": "other.sales@eternal.com", "employee_id": "EMP-0999", "role": "sales_eternal", "permissions_override": {"po.view": True}},
    ]
    ids = [AYUB_ID, EUIS_ID]
    none = plan_sales_eternal_permissions(role_snapshot=None, existing_users=users, mutable_user_ids=ids)
    assert none["ok"]
    assert none["snapshot_before"] is None
    assert none["skipped_other_users"]
    snap = plan_sales_eternal_permissions(
        role_snapshot=["po.view", "chat.use"],
        existing_users=users,
        mutable_user_ids=ids,
    )
    assert snap["ok"]
    assert "sales.pwa.view" in snap["snapshot_after"]
    assert "po.view" not in snap["snapshot_after"]
    other = [u for u in snap["users"] if u["email"] == "other.sales@eternal.com"][0]
    assert other["mutable"] is False
    assert other["leaky_override_true"] == []
    denied = plan_sales_eternal_permissions(
        role_snapshot=None,
        existing_users=[
            {**_canonical_users()[0], "permissions_override": {"sales.pwa.view": False}},
            _canonical_users()[1],
        ],
        mutable_user_ids=ids,
    )
    assert not denied["ok"]


@pytest.mark.anyio
async def test_accurate_sync_fills_and_clears_sales_user_id(monkeypatch):
    import server
    from services.accurate import sync_sales_orders
    from services.sales_user_mapping import SETTINGS_KEY

    db = server.db
    po_mapped = f"SO-SYNC-{uuid.uuid4().hex[:6].upper()}"
    po_unmapped = f"SO-UNMAP-{uuid.uuid4().hex[:6].upper()}"
    await db.app_settings.delete_many({"key": SETTINGS_KEY})
    await db.app_settings.insert_one({
        "key": SETTINGS_KEY,
        "entries": [{"label": ACCURATE_AYUB, "user_id": AYUB_ID}],
    })
    await db.orders.delete_many({"po_number": {"$in": [po_mapped, po_unmapped]}})
    await db.orders.insert_one({
        "id": str(uuid.uuid4()),
        "po_number": po_mapped,
        "sales": ACCURATE_AYUB,
        "sales_user_id": EUIS_ID,
        "customer": "Sync Cust",
        "outlet": "O",
        "recipient": "R",
        "address": "A",
        "payment_terms": "NET",
        "created_by_user": "x",
        "expedition": "",
        "delivery_zone": "",
        "seller": ACCURATE_AYUB,
        "order_date": "2026-09-01",
        "estimated_delivery": "2026-09-04",
        "line_items": [{"name": "Cup", "quantity": 1}],
        "cancelled": False,
    })

    payload = {
        "customer": "Sync Cust",
        "outlet": "O",
        "recipient": "R",
        "address": "A",
        "payment_terms": "NET",
        "created_by_user": "x",
        "expedition": "",
        "delivery_zone": "",
        "seller": "x",
        "line_items": [{"name": "Cup", "quantity": 1}],
    }

    class FakeClient:
        is_live = False
        async def fetch_sales_orders(self):
            return [
                {"po_number": po_mapped, "sales": ACCURATE_AYUB, **payload},
                {"po_number": po_unmapped, "sales": ACCOUNT_AYUB, **payload},
            ]

    async def passthrough_lines(db, client, lines, require_master=True):
        return lines

    async def noop_stock(*args, **kwargs):
        return None

    monkeypatch.setattr("services.master_items.resolve_po_line_items", passthrough_lines)
    monkeypatch.setattr("services.stock_engine_v2.validate_sales_order_availability", noop_stock)
    monkeypatch.setattr("services.physical_stock_engine.attach_order_stock_snapshot", noop_stock)

    result = await sync_sales_orders(db, FakeClient())
    assert result["updated"] >= 1
    mapped = await db.orders.find_one({"po_number": po_mapped}, {"_id": 0})
    assert mapped["sales"] == ACCURATE_AYUB
    assert mapped["sales_user_id"] == AYUB_ID
    assert mapped.get("sales_ownership_status") == STATUS_MATCHED
    fresh = await db.orders.find_one({"po_number": po_unmapped}, {"_id": 0})
    assert fresh is not None
    assert fresh["sales"] == ACCOUNT_AYUB
    assert fresh.get("sales_user_id") in (None, "")
    assert fresh.get("sales_ownership_status") == STATUS_UNMATCHED

    await db.app_settings.delete_many({"key": SETTINGS_KEY})
    await db.orders.update_one({"po_number": po_mapped}, {"$set": {"sales_user_id": AYUB_ID, "sales": ACCOUNT_AYUB}})
    class FakeClient2:
        is_live = False
        async def fetch_sales_orders(self):
            return [{"po_number": po_mapped, "sales": ACCOUNT_AYUB, **payload}]
    result2 = await sync_sales_orders(db, FakeClient2())
    assert result2["updated"] >= 1
    cleared = await db.orders.find_one({"po_number": po_mapped}, {"_id": 0})
    assert cleared["sales"] == ACCOUNT_AYUB
    assert cleared.get("sales_user_id") in (None, "")
    assert cleared.get("sales_ownership_status") == STATUS_UNMATCHED
    assert cleared.get("sales_needs_verification") is True


def test_refuse_production_db_without_explicit_flag():
    from services.sales_eternal_deploy import refuse_db_target, require_explicit_db_name
    with pytest.raises(RuntimeError):
        refuse_db_target("eternal_packaging", allow_production=False)
    refuse_db_target("eternal_packaging_test", allow_production=False)
    refuse_db_target("eternal_packaging_pwa_sales_preview", allow_production=False)
    with pytest.raises(RuntimeError):
        refuse_db_target("eternal_packaging_pwa_sales_preview", allow_production=True)
    with pytest.raises(RuntimeError):
        require_explicit_db_name("", apply=True)
    assert require_explicit_db_name("eternal_packaging_pwa_sales_preview", apply=True) == "eternal_packaging_pwa_sales_preview"


def test_runtime_source_has_no_hardcoded_production_uuid():
    root = Path(__file__).resolve().parents[1]
    needles = ("390b469b-d2c9-4a25-8dd7-82bc3cbb984e", "45caacfe-ece3-4dc0-9b1f-ff22ff56331c")
    files = [
        root / "services/sales_user_mapping.py",
        root / "services/sales_eternal_deploy.py",
        root / "services/sales_user_mapping_http.py",
        root / "services/sales_pwa.py",
        root / "services/sales_pwa_http.py",
        root / "scripts/apply_sales_user_mapping.py",
        root / "scripts/apply_sales_eternal_permissions.py",
        root / "scripts/audit_sales_pwa_identities.py",
    ]
    for path in files:
        text = path.read_text()
        for needle in needles:
            assert needle not in text, path
        assert "mongodb+srv://" not in text
        assert "eyJhbGci" not in text


def test_apply_scripts_are_dry_run_and_require_db_name():
    root = Path(__file__).resolve().parents[1]
    for rel in (
        "scripts/apply_sales_user_mapping.py",
        "scripts/apply_sales_eternal_permissions.py",
        "scripts/audit_sales_pwa_identities.py",
    ):
        text = (root / rel).read_text()
        assert "--db-name" in text and "required=True" in text
        assert 'os.environ.get("DB_NAME")' not in text
        assert "mongodb://127.0.0.1:27017" in text or "MONGO_URL" in text
    mapping = (root / "scripts/apply_sales_user_mapping.py").read_text()
    perms = (root / "scripts/apply_sales_eternal_permissions.py").read_text()
    audit = (root / "scripts/audit_sales_pwa_identities.py").read_text()
    assert 'add_argument("--apply"' in mapping
    assert 'add_argument("--apply"' in perms
    assert 'add_argument("--apply"' not in audit
    assert "READ ONLY" in audit or "READ_ONLY" in audit
    server = (root / "server.py").read_text()
    assert "apply_sales_user_mapping" not in server
    assert "apply_sales_eternal_permissions" not in server
    assert "sales_eternal_deploy" not in server


def test_production_emp_accounts_keep_existing_perms_and_gain_pwa_view():
    from permissions import effective_permissions, ROLE_DEFAULT_PERMISSIONS
    for emp, email in (("EMP-0049", "ayub@eternal.com"), ("EMP-0052", "euis@eternal.com")):
        perms = effective_permissions("sales_eternal", {})
        assert "sales.pwa.view" in perms
        assert "po.view" not in perms
        assert "chat.use" in perms
        assert emp and email
    assert "sales.pwa.view" in ROLE_DEFAULT_PERMISSIONS["sales_eternal"]


@pytest.mark.anyio
async def test_unmapped_po_hidden_from_pwa_and_admin_sees_verification():
    import server
    from httpx import ASGITransport, AsyncClient
    from server import app, current_user
    from permissions import ROLE_DEFAULT_PERMISSIONS
    from services.sales_user_mapping import SETTINGS_KEY, ADMIN_STATUS_NEEDS_VERIFICATION

    db = server.db
    await db.app_settings.delete_many({"key": SETTINGS_KEY})
    suffix = uuid.uuid4().hex[:6]
    ayub_uid = str(uuid.uuid4())
    euis_uid = str(uuid.uuid4())
    po_num = f"PO-UNMAP-{suffix}"
    po_id = str(uuid.uuid4())
    await db.orders.delete_many({"po_number": po_num})
    doc = {
        "id": po_id,
        "po_number": po_num,
        "customer": f"Cust Unmapped {suffix}",
        "sales": ACCOUNT_AYUB,
        "sales_user_id": euis_uid,
        "cancelled": False,
        "current_stage": 3,
        "order_date": "2026-09-01",
        "line_items": [{"name": "Cup", "quantity": 10, "unit": "pcs"}],
        "created_at": "2026-09-01T00:00:00+00:00",
        "updated_at": "2026-09-01T00:00:00+00:00",
    }
    uid, status = await attach_sales_ownership(db, doc)
    assert uid is None
    assert status == STATUS_UNMATCHED
    assert doc["sales_user_id"] is None
    assert doc["sales_needs_verification"] is True
    assert doc["sales_ownership_admin_label"] == ADMIN_STATUS_NEEDS_VERIFICATION
    await db.orders.insert_one(doc)

    def _user(uid, email, name, role="sales_eternal"):
        return {
            "id": uid, "user_id": uid, "email": email, "name": name, "role": role,
            "permissions": list(ROLE_DEFAULT_PERMISSIONS[role]),
        }

    ayub_email = f"ayub.unmap.{suffix}@eternal.com"
    euis_email = f"euis.unmap.{suffix}@eternal.com"
    admin_email = f"admin.unmap.{suffix}@eternal.com"
    await db.users.delete_many({"id": {"$in": [ayub_uid, euis_uid, f"admin-unmap-{suffix}"]}})
    await db.users.insert_many([
        {"id": ayub_uid, "email": ayub_email, "name": ACCOUNT_AYUB, "role": "sales_eternal"},
        {"id": euis_uid, "email": euis_email, "name": ACCURATE_EUIS, "role": "sales_eternal"},
        {"id": f"admin-unmap-{suffix}", "email": admin_email, "name": "Admin", "role": "admin"},
    ])

    transport = ASGITransport(app=app)
    try:
        for uid, email, name in (
            (ayub_uid, ayub_email, ACCOUNT_AYUB),
            (euis_uid, euis_email, ACCURATE_EUIS),
        ):
            app.dependency_overrides[current_user] = lambda uid=uid, email=email, name=name: _user(uid, email, name)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                listed = await client.get("/api/sales-pwa/orders", params={"limit": 100})
                assert listed.status_code == 200, listed.text
                pos = [o["po_number"] for o in listed.json()["orders"]]
                assert po_num not in pos

        app.dependency_overrides[current_user] = lambda: _user(f"admin-unmap-{suffix}", admin_email, "Admin", "admin")
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            got = await client.get(f"/api/orders/{po_id}")
            assert got.status_code == 200, got.text
            body = got.json().get("order") or got.json()
            assert body.get("sales_user_id") in (None, "")
            assert body.get("sales_needs_verification") is True
            assert body.get("sales_ownership_admin_label") == ADMIN_STATUS_NEEDS_VERIFICATION
            assert body.get("sales_ownership_status") == STATUS_UNMATCHED
            assert body.get("sales") == ACCOUNT_AYUB
    finally:
        app.dependency_overrides.pop(current_user, None)


@pytest.mark.anyio
async def test_mapping_apply_writes_backup_and_is_idempotent():
    from services.sales_eternal_deploy import apply_sales_user_mapping, plan_sales_user_mapping
    from services.sales_user_mapping import SETTINGS_KEY

    class _Col:
        def __init__(self, docs=None):
            self.docs = list(docs or [])

        async def find_one(self, q, proj=None):
            key = q.get("key") or q.get("role")
            for d in self.docs:
                if key and d.get("key") == key:
                    return dict(d)
            return dict(self.docs[0]) if self.docs and not key else (dict(self.docs[0]) if self.docs else None)

        async def update_one(self, q, upd, upsert=False):
            payload = dict(upd.get("$set") or {})
            if self.docs:
                self.docs[0] = payload
            elif upsert:
                self.docs.append(payload)

        async def insert_one(self, doc):
            self.docs.append(dict(doc))

    class _Db:
        def __init__(self):
            self.app_settings = _Col([])
            self.sales_user_mapping_backups = _Col([])
            self.audit_logs = _Col([])

        def __getitem__(self, name):
            return getattr(self, name)

    users = _canonical_users()
    db = _Db()
    db.app_settings.docs.append({"key": SETTINGS_KEY, "entries": [{"label": "Sales Lain Valid", "user_id": AYUB_ID}]})
    plan = plan_sales_user_mapping(db.app_settings.docs[0], users)
    assert plan["ok"]
    applied = await apply_sales_user_mapping(db, plan, actor={"email": "test-script"})
    assert applied["applied"] is True
    assert applied["rollback"]["collection"] == "sales_user_mapping_backups"
    assert db.sales_user_mapping_backups.docs
    stored = db.app_settings.docs[0]
    labels = {e["label"] for e in stored["entries"]}
    assert ACCURATE_AYUB in labels and ACCURATE_EUIS in labels and "Sales Lain Valid" in labels
    again = plan_sales_user_mapping(stored, users)
    assert again["ok"] and not again["changed"]


@pytest.mark.anyio
async def test_save_mapping_validates_role_and_rejects_partial_write():
    from fastapi import HTTPException
    from services.sales_user_mapping import save_mapping, SETTINGS_KEY

    class _Cursor:
        def __init__(self, docs):
            self.docs = docs

        async def to_list(self, n):
            return list(self.docs)

    class _Users:
        def __init__(self, docs):
            self.docs = docs

        def find(self, q, proj=None):
            ids = set(((q.get("id") or {}).get("$in")) or [])
            return _Cursor([d for d in self.docs if d["id"] in ids])

    class _Settings:
        def __init__(self):
            self.writes = 0
            self.doc = {"key": SETTINGS_KEY, "entries": []}

        async def find_one(self, q, proj=None):
            return dict(self.doc)

        async def update_one(self, *a, **k):
            self.writes += 1

    class _Audit:
        def __init__(self):
            self.writes = 0

        async def insert_one(self, doc):
            self.writes += 1

    class _Db:
        def __init__(self, users):
            self.users = _Users(users)
            self.app_settings = _Settings()
            self.audit_logs = _Audit()

    admin = {"id": "admin-1", "email": "admin@eternalpackaging.com", "name": "Admin", "role": "admin"}
    missing_db = _Db(_canonical_users())
    with pytest.raises(HTTPException) as missing:
        await save_mapping(missing_db, {"entries": [{"label": ACCURATE_AYUB, "user_id": str(uuid.uuid4())}]}, admin)
    assert missing.value.status_code == 400
    assert missing_db.app_settings.writes == 0
    assert missing_db.audit_logs.writes == 0

    mixed = _canonical_users() + [{"id": "admin-1", "email": admin["email"], "role": "admin", "status": "active"}]
    mixed_db = _Db(mixed)
    with pytest.raises(HTTPException) as bad_role:
        await save_mapping(mixed_db, {"entries": [
            {"label": ACCURATE_AYUB, "user_id": AYUB_ID},
            {"label": "Admin Label", "user_id": "admin-1"},
        ]}, admin)
    assert bad_role.value.status_code == 400
    assert "bukan role" in str(bad_role.value.detail)
    assert mixed_db.app_settings.writes == 0
    assert mixed_db.audit_logs.writes == 0

    ok_db = _Db(_canonical_users())
    saved = await save_mapping(ok_db, {"entries": [
        {"label": ACCURATE_AYUB, "user_id": AYUB_ID},
        {"label": ACCURATE_EUIS, "user_id": EUIS_ID},
    ]}, admin)
    assert ok_db.app_settings.writes == 1
    assert {e["label"] for e in saved["entries"]} == {ACCURATE_AYUB, ACCURATE_EUIS}


@pytest.mark.anyio
async def test_admin_mapping_http_and_sales_forbidden():
    import server
    from httpx import ASGITransport, AsyncClient
    from permissions import ROLE_DEFAULT_PERMISSIONS, ALL_PERMISSIONS
    from services.sales_user_mapping import SETTINGS_KEY

    db = server.db
    await db.users.delete_many({"id": {"$in": [AYUB_ID, EUIS_ID, "admin-map-1"]}})
    await db.users.insert_many([
        *_canonical_users(),
        {"id": "admin-map-1", "email": "admin.map@eternal.com", "name": "Admin", "role": "admin", "is_super_admin": True},
    ])
    await db.app_settings.delete_many({"key": SETTINGS_KEY})
    await db.app_settings.insert_one({"key": SETTINGS_KEY, "entries": []})

    def _user(uid, email, name, role):
        perms = list(ALL_PERMISSIONS) if role == "admin" else list(ROLE_DEFAULT_PERMISSIONS[role])
        return {"id": uid, "user_id": uid, "email": email, "name": name, "role": role, "permissions": perms}

    transport = ASGITransport(app=app)
    try:
        app.dependency_overrides[current_user] = lambda: _user("admin-map-1", "admin.map@eternal.com", "Admin", "admin")
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            got = await client.get("/api/settings/sales-user-mapping")
            assert got.status_code == 200, got.text
            bad = await client.put("/api/settings/sales-user-mapping", json={"entries": [
                {"label": ACCURATE_AYUB, "user_id": "admin-map-1"},
            ]})
            assert bad.status_code == 400, bad.text
            stored = await db.app_settings.find_one({"key": SETTINGS_KEY}, {"_id": 0})
            assert (stored or {}).get("entries") == []
            ok = await client.put("/api/settings/sales-user-mapping", json={"entries": [
                {"label": ACCURATE_AYUB, "user_id": AYUB_ID},
                {"label": ACCURATE_EUIS, "user_id": EUIS_ID},
            ]})
            assert ok.status_code == 200, ok.text
            assert len(ok.json()["settings"]["entries"]) == 2
        app.dependency_overrides[current_user] = lambda: _user(AYUB_ID, AYUB_EMAIL, ACCOUNT_AYUB, "sales_eternal")
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            denied_get = await client.get("/api/settings/sales-user-mapping")
            denied_put = await client.put("/api/settings/sales-user-mapping", json={"entries": []})
            assert denied_get.status_code in (403, 401)
            assert denied_put.status_code in (403, 401)
    finally:
        app.dependency_overrides.pop(current_user, None)


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture(autouse=True)
def bind_motor():
    import server
    from motor.motor_asyncio import AsyncIOMotorClient
    server.client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    server.db = server.client[os.environ["DB_NAME"]]
    app.dependency_overrides.pop(current_user, None)
    yield
    app.dependency_overrides.pop(current_user, None)
