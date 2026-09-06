"""Chart of Accounts (Akun Perkiraan) — unit + API safety tests."""
from __future__ import annotations

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
from permissions import ALL_PERMISSIONS, ROLE_DEFAULT_PERMISSIONS, with_nav_defaults
from server import app, current_user
from services.chart_of_accounts import (
    ACCOUNT_TYPES,
    COLLECTION,
    PERM_MANAGE,
    PERM_VIEW,
    create_account,
    delete_account,
    ensure_indexes,
    list_accounts,
    load_seed_rows,
    normalize_code,
    seed_accounts,
    update_account,
    AccountConflictError,
    AccountInUseError,
    AccountValidationError,
)
from scripts.backfill_coa_account_type import apply_backfill, plan_backfill

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
async def clean_accounts(bind_motor):
    db = server.db
    await ensure_indexes(db)
    await db[COLLECTION].delete_many({})
    await db.journal_entries.delete_many({})
    yield
    await db[COLLECTION].delete_many({})
    await db.journal_entries.delete_many({})


def _user(role="admin", **kw):
    uid = kw.get("id") or f"u-{role}-{uuid.uuid4().hex[:8]}"
    override = kw.get("permissions_override")
    if override is None:
        override = {} if role == "admin" else {}
    return {
        "id": uid,
        "email": kw.get("email") or f"{uid}@example.com",
        "name": kw.get("name") or role.title(),
        "role": role,
        "status": "active",
        "user_id": uid,
        "permissions_override": override,
    }


async def client_for(user):
    app.dependency_overrides[current_user] = lambda: user
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


def _payload(code, name, account_type="BANK"):
    return {"code": code, "name": name, "account_type": account_type}


@pytest.mark.anyio
async def test_create_code_name_type_success():
    db = server.db
    doc = await create_account(
        db, {"code": " 99001 ", "name": "  Kas Preview  ", "account_type": "bank"}, user=_user()
    )
    assert doc["code"] == "99001"
    assert doc["name"] == "Kas Preview"
    assert doc["account_type"] == "BANK"
    assert doc["account_type_label"] == "Kas/Bank"
    assert doc["report_group"] == "asset"
    assert doc["normal_balance"] == "debit"
    assert isinstance(doc["code"], str)


@pytest.mark.anyio
async def test_create_requires_account_type():
    db = server.db
    with pytest.raises(AccountValidationError):
        await create_account(db, {"code": "99011", "name": "No Type"}, user=_user())
    with pytest.raises(AccountValidationError):
        await create_account(db, {"code": "99012", "name": "Bad", "account_type": "NOPE"}, user=_user())


@pytest.mark.anyio
async def test_old_record_without_type_remains_readable():
    db = server.db
    await db[COLLECTION].insert_one(
        {
            "id": str(uuid.uuid4()),
            "code": "legacy01",
            "name": "Legacy",
            "created_at": "t",
            "created_by": "t",
            "updated_at": "t",
            "updated_by": "t",
        }
    )
    items = await list_accounts(db)
    hit = next(i for i in items if i["code"] == "legacy01")
    assert hit["name"] == "Legacy"
    assert hit["account_type"] is None
    assert hit["account_type_label"] is None
    assert hit["report_group"] is None
    assert hit["normal_balance"] is None


@pytest.mark.anyio
async def test_update_account_type_and_reject_unknown():
    db = server.db
    doc = await create_account(db, _payload("99020", "A", "EXPS"), user=_user())
    updated = await update_account(db, doc["id"], {"account_type": "BANK"}, user=_user())
    assert updated["account_type"] == "BANK"
    with pytest.raises(AccountValidationError):
        await update_account(db, doc["id"], {"account_type": "ZZZZ"}, user=_user())


@pytest.mark.anyio
async def test_duplicate_code_409():
    db = server.db
    await create_account(db, _payload("99002", "A"), user=_user())
    with pytest.raises(AccountConflictError):
        await create_account(db, _payload("99002", "B"), user=_user())


@pytest.mark.anyio
async def test_duplicate_name_different_code_allowed():
    db = server.db
    a = await create_account(db, _payload("99003", "Same Name"), user=_user())
    b = await create_account(db, _payload("99004", "Same Name"), user=_user())
    assert a["name"] == b["name"]
    assert a["code"] != b["code"]


@pytest.mark.anyio
async def test_get_zero_write():
    db = server.db
    await create_account(db, _payload("99005", "ReadOnly"), user=_user())
    before = await db[COLLECTION].count_documents({})
    items = await list_accounts(db)
    after = await db[COLLECTION].count_documents({})
    assert before == after
    assert any(i["code"] == "99005" for i in items)


@pytest.mark.anyio
async def test_delete_unused_success_and_unknown_404():
    db = server.db
    doc = await create_account(db, _payload("99006", "Temp"), user=_user())
    deleted = await delete_account(db, doc["id"])
    assert deleted["id"] == doc["id"]
    with pytest.raises(KeyError):
        await delete_account(db, "missing-" + uuid.uuid4().hex)


@pytest.mark.anyio
async def test_delete_referenced_rejected():
    db = server.db
    doc = await create_account(db, _payload("99007", "Used"), user=_user())
    await db.journal_entries.insert_one({"id": str(uuid.uuid4()), "account_id": doc["id"], "memo": "future"})
    with pytest.raises(AccountInUseError):
        await delete_account(db, doc["id"])
    still = await db[COLLECTION].find_one({"id": doc["id"]})
    assert still is not None


@pytest.mark.anyio
async def test_code_remains_string_even_from_numeric_input():
    assert normalize_code(1102) == "1102"
    assert normalize_code(1102.0) == "1102"
    db = server.db
    doc = await create_account(db, _payload(88001, "Numeric Code"), user=_user())
    raw = await db[COLLECTION].find_one({"id": doc["id"]}, {"_id": 0})
    assert isinstance(raw["code"], str)
    assert raw["code"] == "88001"
    assert raw["account_type"] == "BANK"


@pytest.mark.anyio
async def test_seed_idempotent_and_ignores_balance_fields():
    db = server.db
    rows = [
        {"code": "77001", "name": "Seed A", "Saldo Awal": 99999, "Tipe Akun": "BANK", "balance": 1},
        {"code": "77002", "name": "Seed B", "account_type": "EXPS", "debit": 10, "kredit": 20},
    ]
    r1 = await seed_accounts(db, rows, actor="test-seed")
    r2 = await seed_accounts(db, rows, actor="test-seed")
    assert r1["inserted"] == 2
    assert r2["inserted"] == 0
    assert r2["skipped"] == 2
    docs = await db[COLLECTION].find({"code": {"$in": ["77001", "77002"]}}, {"_id": 0}).to_list(10)
    assert len(docs) == 2
    by_code = {d["code"]: d for d in docs}
    assert by_code["77001"]["account_type"] == "BANK"
    assert by_code["77002"]["account_type"] == "EXPS"
    for d in docs:
        assert "Saldo Awal" not in d
        assert "balance" not in d
        assert "debit" not in d
        assert "kredit" not in d
        assert set(d.keys()) >= {"id", "code", "name", "account_type", "created_at", "created_by", "updated_at", "updated_by"}


@pytest.mark.anyio
async def test_seed_file_has_code_name_and_type():
    rows = load_seed_rows()
    assert len(rows) == 203
    codes = set()
    for row in rows:
        assert set(row.keys()) == {"code", "name", "account_type"}
        assert isinstance(row["code"], str)
        assert row["code"]
        assert row["name"]
        assert row["account_type"] in ACCOUNT_TYPES
        codes.add(row["code"])
    assert len(codes) == 203


@pytest.mark.anyio
async def test_backfill_dry_run_zero_write_and_apply_idempotent():
    db = server.db
    # Existing untyped COA matching a seed code
    seed = load_seed_rows()
    sample = seed[0]
    aid = str(uuid.uuid4())
    await db[COLLECTION].insert_one(
        {
            "id": aid,
            "code": sample["code"],
            "name": sample["name"],
            "created_at": "t",
            "created_by": "t",
            "updated_at": "t",
            "updated_by": "t",
        }
    )
    before = await db[COLLECTION].find_one({"id": aid}, {"_id": 0})
    assert "account_type" not in before or not before.get("account_type")

    plan = await plan_backfill(db)
    assert any(x["id"] == aid for x in plan["would_update"])
    mid = await db[COLLECTION].find_one({"id": aid}, {"_id": 0})
    assert mid.get("account_type") in (None, "", False) or "account_type" not in mid

    applied = await apply_backfill(db, plan, actor="test-backfill")
    assert applied["updated_count"] == 1
    after = await db[COLLECTION].find_one({"id": aid}, {"_id": 0})
    assert after["account_type"] == sample["account_type"]

    plan2 = await plan_backfill(db)
    assert not any(x["id"] == aid for x in plan2["would_update"])
    applied2 = await apply_backfill(db, plan2, actor="test-backfill")
    assert applied2["updated_count"] == 0


@pytest.mark.anyio
async def test_backfill_conflict_does_not_overwrite():
    db = server.db
    seed = load_seed_rows()
    sample = next(r for r in seed if r["account_type"] == "BANK")
    other = "EXPS" if sample["account_type"] != "EXPS" else "OEXP"
    aid = str(uuid.uuid4())
    await db[COLLECTION].insert_one(
        {
            "id": aid,
            "code": sample["code"],
            "name": sample["name"],
            "account_type": other,
            "created_at": "t",
            "created_by": "t",
            "updated_at": "t",
            "updated_by": "t",
        }
    )
    plan = await plan_backfill(db)
    assert any(c.get("id") == aid for c in plan["conflicts"])
    with pytest.raises(RuntimeError):
        await apply_backfill(db, plan, actor="test-backfill")
    still = await db[COLLECTION].find_one({"id": aid}, {"_id": 0})
    assert still["account_type"] == other


@pytest.mark.anyio
async def test_api_unauthorized_view_manage_rejected():
    helper = _user("helper", permissions_override={})
    async with await client_for(helper) as client:
        r = await client.get("/api/ledger/accounts")
        assert r.status_code == 403
        r2 = await client.post("/api/ledger/accounts", json=_payload("x", "y"))
        assert r2.status_code == 403


@pytest.mark.anyio
async def test_api_view_only_cannot_manage():
    viewer = _user("crm", permissions_override={PERM_VIEW: True})
    async with await client_for(viewer) as client:
        ok = await client.get("/api/ledger/accounts")
        assert ok.status_code == 200
        types = await client.get("/api/ledger/account-types")
        assert types.status_code == 200
        assert types.json()["total"] == 16
        denied = await client.post("/api/ledger/accounts", json=_payload("99100", "Nope"))
        assert denied.status_code == 403


@pytest.mark.anyio
async def test_api_admin_create_duplicate_delete_and_patch():
    admin = _user("admin")
    async with await client_for(admin) as client:
        created = await client.post("/api/ledger/accounts", json=_payload("99101", "API A", "BANK"))
        assert created.status_code == 201
        body = created.json()
        assert body["code"] == "99101"
        assert body["account_type"] == "BANK"
        assert isinstance(body["code"], str)
        missing_type = await client.post("/api/ledger/accounts", json={"code": "99109", "name": "X"})
        assert missing_type.status_code == 422
        bad_type = await client.post(
            "/api/ledger/accounts", json={"code": "99110", "name": "X", "account_type": "NOPE"}
        )
        assert bad_type.status_code == 400
        dup = await client.post("/api/ledger/accounts", json=_payload("99101", "Other"))
        assert dup.status_code == 409
        same_name = await client.post("/api/ledger/accounts", json=_payload("99102", "API A", "EXPS"))
        assert same_name.status_code == 201
        patched = await client.patch(
            f"/api/ledger/accounts/{body['id']}", json={"account_type": "OCAS", "name": "API A2"}
        )
        assert patched.status_code == 200
        assert patched.json()["account_type"] == "OCAS"
        assert patched.json()["name"] == "API A2"
        listed = await client.get("/api/ledger/accounts")
        assert listed.status_code == 200
        assert listed.json()["total"] >= 2
        deleted = await client.delete(f"/api/ledger/accounts/{body['id']}")
        assert deleted.status_code == 200
        missing = await client.delete(f"/api/ledger/accounts/{uuid.uuid4()}")
        assert missing.status_code == 404


@pytest.mark.anyio
async def test_permissions_catalog_and_no_auto_grant_non_admin():
    assert PERM_VIEW in ALL_PERMISSIONS
    assert PERM_MANAGE in ALL_PERMISSIONS
    assert "nav.ledger" in ALL_PERMISSIONS
    assert "nav.ledger.accounts" in ALL_PERMISSIONS
    for role in ("helper", "operator", "screener", "designer", "driver", "crm", "head_production", "head_operations"):
        defaults = set(ROLE_DEFAULT_PERMISSIONS[role])
        derived = with_nav_defaults(defaults)
        assert PERM_VIEW not in defaults, role
        assert PERM_MANAGE not in defaults, role
        assert "nav.ledger" not in defaults, role
        assert "nav.ledger.accounts" not in defaults, role
        assert "nav.ledger" not in derived, role
        assert "nav.ledger.accounts" not in derived, role
