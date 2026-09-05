"""Kas & Bank BANK-only COA eligibility — master create/update authority."""
from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("MONGO_URL", "mongodb://127.0.0.1:27017")
_worker = os.environ.get("PYTEST_XDIST_WORKER") or "main"
os.environ["DB_NAME"] = f"eternal_packaging_cash_bank_bank_only_test_{_worker}"
os.environ.setdefault("JWT_SECRET", "test-secret-key-12345678901234567890")
os.environ.setdefault("CORS_ORIGINS", "*")
os.environ.setdefault("STORAGE_PROVIDER", "local")

import server
from server import app, current_user
from services.cash_bank_master import COLLECTION as CB_COLLECTION, ensure_indexes as ensure_cb
from services.cash_bank_vouchers import ensure_indexes as ensure_vouchers
from services.chart_of_accounts import COLLECTION as COA_COLLECTION, create_account, ensure_indexes as ensure_coa
from services.journal_core import JOURNAL_ENTRIES, JOURNAL_LINES, ensure_indexes as ensure_journal
from services.purchase_common import now_iso

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
async def clean_db(bind_motor):
    db = server.db
    await ensure_coa(db)
    await ensure_cb(db)
    await ensure_vouchers(db)
    await ensure_journal(db)
    for coll in (CB_COLLECTION, COA_COLLECTION, JOURNAL_ENTRIES, JOURNAL_LINES):
        await db[coll].delete_many({})
    yield
    for coll in (CB_COLLECTION, COA_COLLECTION, JOURNAL_ENTRIES, JOURNAL_LINES):
        await db[coll].delete_many({})


def _user(**kw):
    uid = kw.get("id") or f"u-admin-{uuid.uuid4().hex[:8]}"
    return {
        "id": uid,
        "email": kw.get("email") or f"{uid}@example.com",
        "name": kw.get("name") or "Admin",
        "role": "admin",
        "status": "active",
        "user_id": uid,
        "permissions_override": {},
    }


async def client_for(user):
    app.dependency_overrides[current_user] = lambda: user
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


async def _coa(code, name, account_type="BANK"):
    return await create_account(
        server.db, {"code": code, "name": name, "account_type": account_type}, user=_user()
    )


async def _insert_legacy_non_bank(*, name="Legacy Kas", account_type="EXPS"):
    """Bypass create_cash_bank to simulate legacy non-BANK link. No auto-fix."""
    coa = await _coa(f"LG{uuid.uuid4().hex[:6]}", f"Legacy {account_type}", account_type)
    now = now_iso()
    doc = {
        "id": str(uuid.uuid4()),
        "name": name,
        "account_id": coa["id"],
        "account_code": coa["code"],
        "account_name": coa["name"],
        "description": "legacy",
        "active": True,
        "created_at": now,
        "created_by": "legacy",
        "updated_at": now,
        "updated_by": "legacy",
    }
    await server.db[CB_COLLECTION].insert_one(dict(doc))
    return doc, coa


@pytest.mark.anyio
async def test_bank_create_pass():
    admin = _user()
    async with await client_for(admin) as c:
        bank = await _coa(f"BK{uuid.uuid4().hex[:6]}", "Bank BCA", "BANK")
        r = await c.post(
            "/api/cash-bank/accounts",
            json={"name": "Kas BCA", "account_id": bank["id"]},
        )
        assert r.status_code == 201, r.text
        assert r.json()["account_id"] == bank["id"]


@pytest.mark.anyio
async def test_non_bank_create_rejected():
    admin = _user()
    async with await client_for(admin) as c:
        exps = await _coa(f"EX{uuid.uuid4().hex[:6]}", "Beban Operasional", "EXPS")
        r = await c.post(
            "/api/cash-bank/accounts",
            json={"name": "Bad Kas", "account_id": exps["id"]},
        )
        assert r.status_code == 400, r.text
        assert "BANK" in r.json()["detail"]
        assert await server.db[CB_COLLECTION].count_documents({}) == 0


@pytest.mark.anyio
async def test_missing_account_type_create_rejected():
    admin = _user()
    async with await client_for(admin) as c:
        aid = str(uuid.uuid4())
        await server.db[COA_COLLECTION].insert_one(
            {
                "id": aid,
                "code": f"MT{uuid.uuid4().hex[:6]}",
                "name": "No Type",
                "account_type": None,
                "created_at": now_iso(),
                "created_by": "t",
                "updated_at": now_iso(),
                "updated_by": "t",
            }
        )
        r = await c.post(
            "/api/cash-bank/accounts",
            json={"name": "Missing Type Kas", "account_id": aid},
        )
        assert r.status_code == 400, r.text
        assert "BANK" in r.json()["detail"]


@pytest.mark.anyio
async def test_unknown_account_create_rejected():
    admin = _user()
    async with await client_for(admin) as c:
        r = await c.post(
            "/api/cash-bank/accounts",
            json={"name": "Ghost", "account_id": str(uuid.uuid4())},
        )
        assert r.status_code == 400, r.text
        assert "tidak ditemukan" in r.json()["detail"].lower()


@pytest.mark.anyio
async def test_update_link_to_bank_pass():
    admin = _user()
    async with await client_for(admin) as c:
        a1 = await _coa(f"B1{uuid.uuid4().hex[:6]}", "Bank 1", "BANK")
        a2 = await _coa(f"B2{uuid.uuid4().hex[:6]}", "Bank 2", "BANK")
        created = (
            await c.post(
                "/api/cash-bank/accounts",
                json={"name": "Kas Relink", "account_id": a1["id"]},
            )
        ).json()
        before_j = await server.db[JOURNAL_ENTRIES].count_documents({})
        before_l = await server.db[JOURNAL_LINES].count_documents({})
        r = await c.patch(
            f"/api/cash-bank/accounts/{created['id']}",
            json={"account_id": a2["id"]},
        )
        assert r.status_code == 200, r.text
        assert r.json()["account_id"] == a2["id"]
        assert await server.db[JOURNAL_ENTRIES].count_documents({}) == before_j
        assert await server.db[JOURNAL_LINES].count_documents({}) == before_l


@pytest.mark.anyio
async def test_update_link_to_non_bank_rejected():
    admin = _user()
    async with await client_for(admin) as c:
        bank = await _coa(f"BK{uuid.uuid4().hex[:6]}", "Bank OK", "BANK")
        reve = await _coa(f"RV{uuid.uuid4().hex[:6]}", "Pendapatan", "REVE")
        created = (
            await c.post(
                "/api/cash-bank/accounts",
                json={"name": "Kas Keep", "account_id": bank["id"]},
            )
        ).json()
        r = await c.patch(
            f"/api/cash-bank/accounts/{created['id']}",
            json={"account_id": reve["id"]},
        )
        assert r.status_code == 400, r.text
        assert "BANK" in r.json()["detail"]
        still = await server.db[CB_COLLECTION].find_one({"id": created["id"]}, {"_id": 0})
        assert still["account_id"] == bank["id"]


@pytest.mark.anyio
async def test_unique_coa_rule_still_enforced():
    admin = _user()
    async with await client_for(admin) as c:
        shared = await _coa(f"SH{uuid.uuid4().hex[:6]}", "Shared Bank", "BANK")
        other = await _coa(f"OT{uuid.uuid4().hex[:6]}", "Other Bank", "BANK")
        r1 = await c.post(
            "/api/cash-bank/accounts",
            json={"name": "Bank Satu", "account_id": shared["id"]},
        )
        assert r1.status_code == 201
        r2 = await c.post(
            "/api/cash-bank/accounts",
            json={"name": "Bank Dua", "account_id": shared["id"]},
        )
        assert r2.status_code == 409
        second = (
            await c.post(
                "/api/cash-bank/accounts",
                json={"name": "Bank Tiga", "account_id": other["id"]},
            )
        ).json()
        relink = await c.patch(
            f"/api/cash-bank/accounts/{second['id']}",
            json={"account_id": shared["id"]},
        )
        assert relink.status_code == 409


@pytest.mark.anyio
async def test_legacy_non_bank_master_remains_readable_and_editable():
    admin = _user()
    legacy, coa = await _insert_legacy_non_bank(name="Legacy NonBank")
    async with await client_for(admin) as c:
        listed = await c.get("/api/cash-bank/accounts")
        assert listed.status_code == 200
        items = listed.json()["items"]
        assert any(i["id"] == legacy["id"] for i in items)
        hit = next(i for i in items if i["id"] == legacy["id"])
        assert hit["account_id"] == coa["id"]

        detail = await c.get(f"/api/cash-bank/accounts/{legacy['id']}")
        assert detail.status_code == 200
        assert detail.json()["name"] == "Legacy NonBank"

        patched = await c.patch(
            f"/api/cash-bank/accounts/{legacy['id']}",
            json={"name": "Legacy Renamed", "description": "still legacy"},
        )
        assert patched.status_code == 200, patched.text
        assert patched.json()["name"] == "Legacy Renamed"
        assert patched.json()["account_id"] == coa["id"]

        # Same non-BANK account_id in payload must not force migration / rejection
        same = await c.patch(
            f"/api/cash-bank/accounts/{legacy['id']}",
            json={"account_id": coa["id"], "name": "Legacy Same Link"},
        )
        assert same.status_code == 200, same.text
        assert same.json()["account_id"] == coa["id"]


@pytest.mark.anyio
async def test_eligibility_does_not_mutate_journals():
    admin = _user()
    async with await client_for(admin) as c:
        bank = await _coa(f"BK{uuid.uuid4().hex[:6]}", "Bank J", "BANK")
        before_j = await server.db[JOURNAL_ENTRIES].count_documents({})
        before_l = await server.db[JOURNAL_LINES].count_documents({})
        await c.post(
            "/api/cash-bank/accounts",
            json={"name": "Kas J", "account_id": bank["id"]},
        )
        exps = await _coa(f"EX{uuid.uuid4().hex[:6]}", "Exp", "EXPS")
        await c.post(
            "/api/cash-bank/accounts",
            json={"name": "Bad", "account_id": exps["id"]},
        )
        assert await server.db[JOURNAL_ENTRIES].count_documents({}) == before_j
        assert await server.db[JOURNAL_LINES].count_documents({}) == before_l
