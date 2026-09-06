"""Account type → report_group / normal_balance static map."""
from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("MONGO_URL", "mongodb://127.0.0.1:27017")
os.environ.setdefault("DB_NAME", "eternal_packaging_acct_type_map_test")
os.environ.setdefault("JWT_SECRET", "test-secret-key-12345678901234567890")
os.environ.setdefault("CORS_ORIGINS", "*")
os.environ.setdefault("STORAGE_PROVIDER", "local")

import server
from server import app, current_user
from services.account_type_reporting import (
    ACCOUNT_TYPE_REPORTING_MAP,
    DERIVED_ACCOUNT_FIELDS,
    VALID_STATEMENT_GROUPS,
    AccountTypeMapError,
    catalog_items,
    reporting_metadata,
    validate_account_type_reporting_map,
)
from services.chart_of_accounts import (
    ACCOUNT_TYPES,
    ACCOUNT_TYPE_SET,
    COLLECTION,
    PERM_VIEW,
    create_account,
    ensure_indexes,
    list_accounts,
)
from services.chart_of_accounts import AccountValidationError, validate_payload

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
    yield
    await db[COLLECTION].delete_many({})


def _user(role="admin", **kw):
    uid = kw.get("id") or f"u-{role}-{uuid.uuid4().hex[:8]}"
    return {
        "id": uid,
        "email": kw.get("email") or f"{uid}@example.com",
        "name": kw.get("name") or role.title(),
        "role": role,
        "status": "active",
        "user_id": uid,
        "permissions_override": kw.get("permissions_override") or {},
    }


async def client_for(user):
    app.dependency_overrides[current_user] = lambda: user
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


@pytest.mark.anyio
async def test_all_16_types_mapped_exactly_once():
    validate_account_type_reporting_map(ACCOUNT_TYPES)
    assert len(ACCOUNT_TYPES) == 16
    assert len(ACCOUNT_TYPE_REPORTING_MAP) == 16
    assert set(ACCOUNT_TYPE_REPORTING_MAP) == set(ACCOUNT_TYPES) == ACCOUNT_TYPE_SET
    assert len(ACCOUNT_TYPE_REPORTING_MAP) == len(set(ACCOUNT_TYPE_REPORTING_MAP))


@pytest.mark.anyio
async def test_every_map_entry_valid():
    for code, row in ACCOUNT_TYPE_REPORTING_MAP.items():
        assert (row["statement"], row["report_group"]) in VALID_STATEMENT_GROUPS
        meta = reporting_metadata(code)
        assert meta["account_type"] == code
        assert meta["account_type_label"] == row["label"]
        assert meta["report_group"] == row["report_group"]
        assert meta["normal_balance"] == row["normal_balance"]


@pytest.mark.anyio
async def test_map_rejects_missing_unknown_duplicate_and_invalid_combo():
    with pytest.raises(AccountTypeMapError, match="missing"):
        validate_account_type_reporting_map(ACCOUNT_TYPES + ("ZZZZ",))
    with pytest.raises(AccountTypeMapError, match="unknown"):
        validate_account_type_reporting_map(ACCOUNT_TYPES[:-1])
    with pytest.raises(AccountTypeMapError, match="combination"):
        original = ACCOUNT_TYPE_REPORTING_MAP["BANK"]
        ACCOUNT_TYPE_REPORTING_MAP["BANK"] = {
            **original,
            "statement": "INCOME_STATEMENT",
            "report_group": "asset",
        }
        try:
            validate_account_type_reporting_map(ACCOUNT_TYPES)
        finally:
            ACCOUNT_TYPE_REPORTING_MAP["BANK"] = original
    with pytest.raises(AccountTypeMapError, match="normal_balance"):
        original = ACCOUNT_TYPE_REPORTING_MAP["EXPS"]
        ACCOUNT_TYPE_REPORTING_MAP["EXPS"] = {**original, "normal_balance": "both"}
        try:
            validate_account_type_reporting_map(ACCOUNT_TYPES)
        finally:
            ACCOUNT_TYPE_REPORTING_MAP["EXPS"] = original


@pytest.mark.anyio
async def test_derived_metadata_known_and_unknown():
    bank = reporting_metadata("bank")
    assert bank["report_group"] == "asset"
    assert bank["normal_balance"] == "debit"
    assert bank["account_type_label"] == "Kas/Bank"
    depr = reporting_metadata("DEPR")
    assert depr["report_group"] == "asset"
    assert depr["normal_balance"] == "credit"
    assert reporting_metadata(None) is None
    assert reporting_metadata("") is None
    assert reporting_metadata("NOPE") is None


@pytest.mark.anyio
async def test_unknown_account_type_rejected_by_coa_validation():
    with pytest.raises(AccountValidationError):
        validate_payload({"code": "X1", "name": "X", "account_type": "NOPE"})
    with pytest.raises(AccountValidationError):
        validate_payload({"code": "X2", "name": "X", "account_type": ""})


@pytest.mark.anyio
async def test_derived_fields_not_persisted_and_get_zero_write():
    db = server.db
    doc = await create_account(
        db, {"code": "MAP01", "name": "Kas Map", "account_type": "BANK"}, user=_user()
    )
    assert doc["account_type"] == "BANK"
    assert doc["account_type_label"] == "Kas/Bank"
    assert doc["report_group"] == "asset"
    assert doc["normal_balance"] == "debit"
    assert doc["statement"] == "BALANCE_SHEET"

    raw = await db[COLLECTION].find_one({"id": doc["id"]}, {"_id": 0})
    for field in DERIVED_ACCOUNT_FIELDS:
        assert field not in raw
    assert set(raw.keys()) >= {"id", "code", "name", "account_type"}
    assert raw["account_type"] == "BANK"

    before = await db[COLLECTION].count_documents({})
    listed = await list_accounts(db)
    after = await db[COLLECTION].count_documents({})
    assert before == after
    hit = next(i for i in listed if i["code"] == "MAP01")
    assert hit["report_group"] == "asset"
    assert hit["normal_balance"] == "debit"


@pytest.mark.anyio
async def test_legacy_untyped_has_null_derived_fields():
    db = server.db
    await db[COLLECTION].insert_one({
        "id": str(uuid.uuid4()),
        "code": "legacy-map",
        "name": "Legacy",
        "created_at": "t",
        "created_by": "t",
        "updated_at": "t",
        "updated_by": "t",
    })
    hit = next(i for i in await list_accounts(db) if i["code"] == "legacy-map")
    assert hit["account_type"] is None
    assert hit["account_type_label"] is None
    assert hit["report_group"] is None
    assert hit["normal_balance"] is None


@pytest.mark.anyio
async def test_account_types_api_zero_write_and_catalog():
    db = server.db
    await create_account(db, {"code": "MAP02", "name": "Beban Map", "account_type": "EXPS"}, user=_user())
    before_docs = await db[COLLECTION].find({}, {"_id": 0}).to_list(50)
    viewer = _user("crm", permissions_override={PERM_VIEW: True})
    async with await client_for(viewer) as client:
        r = await client.get("/api/ledger/account-types")
        assert r.status_code == 200
        body = r.json()
        assert body["total"] == 16
        codes = [i["code"] for i in body["items"]]
        assert codes == list(ACCOUNT_TYPES)
        assert len(set(codes)) == 16
        bank = next(i for i in body["items"] if i["code"] == "BANK")
        assert bank["label"] == "Kas/Bank"
        assert bank["account_type_label"] == "Kas/Bank"
        assert bank["report_group"] == "asset"
        assert bank["normal_balance"] == "debit"
        listed = await client.get("/api/ledger/accounts")
        assert listed.status_code == 200
        row = next(i for i in listed.json()["items"] if i["code"] == "MAP02")
        assert row["account_type_label"] == "Beban"
        assert row["report_group"] == "expense"
        assert row["normal_balance"] == "debit"
    after_docs = await db[COLLECTION].find({}, {"_id": 0}).to_list(50)
    assert before_docs == after_docs
    for raw in after_docs:
        for field in DERIVED_ACCOUNT_FIELDS:
            assert field not in raw


@pytest.mark.anyio
async def test_catalog_items_align_with_allowed_types():
    items = catalog_items(ACCOUNT_TYPES)
    assert [i["code"] for i in items] == list(ACCOUNT_TYPES)
