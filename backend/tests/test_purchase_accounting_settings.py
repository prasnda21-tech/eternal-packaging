"""Purchase Accounting Settings — Phase 4A mappings only, zero journals."""
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
os.environ["DB_NAME"] = f"eternal_packaging_purchase_accounting_settings_test_{_worker}"
os.environ.setdefault("JWT_SECRET", "test-secret-key-12345678901234567890")
os.environ.setdefault("CORS_ORIGINS", "*")

import server
from permissions import ALL_PERMISSIONS, PERMISSION_CATALOG, ROLE_DEFAULT_PERMISSIONS
from server import app, current_user
from services.chart_of_accounts import create_account, delete_account, ensure_indexes, update_account
from services.chart_of_accounts import AccountInUseError
from services.purchase_accounting_settings import SETTINGS_KEY

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture(autouse=True)
async def bind_and_clean():
    from motor.motor_asyncio import AsyncIOMotorClient

    server.client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    server.db = server.client[os.environ["DB_NAME"]]
    await ensure_indexes(server.db)
    await server.db.app_settings.delete_many({"key": SETTINGS_KEY})
    yield
    await server.db.app_settings.delete_many({"key": SETTINGS_KEY})


def _admin():
    app.dependency_overrides[current_user] = lambda: {
        "id": "admin-pas",
        "email": "admin@eternalpackaging.com",
        "name": "Admin PAS",
        "role": "admin",
    }


def _user():
    return {"id": "t", "name": "t", "role": "admin"}


async def _coa(code_prefix: str, name: str, account_type: str) -> dict:
    suffix = uuid.uuid4().hex[:6]
    return await create_account(
        server.db,
        {"code": f"{code_prefix}{suffix}", "name": name, "account_type": account_type},
        user=_user(),
    )


@pytest.mark.anyio
async def test_unconfigured_get_is_zero_write():
    _admin()
    before_journals = await server.db.journal_entries.count_documents({})
    before_settings = await server.db.app_settings.count_documents({"key": SETTINGS_KEY})
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            res = await client.get("/api/settings/purchase-accounting")
            assert res.status_code == 200, res.text
            settings = res.json()["settings"]
            assert settings["configured"] is False
            assert settings["payable_account_id"] is None
            assert settings["purchase_clearing_account_id"] is None
            assert settings["accounts"]["payable"] is None
            assert settings["accounts"]["purchase_clearing"] is None
        assert await server.db.app_settings.find_one({"key": SETTINGS_KEY}) is None
        assert await server.db.app_settings.count_documents({"key": SETTINGS_KEY}) == before_settings
        assert await server.db.journal_entries.count_documents({}) == before_journals
    finally:
        app.dependency_overrides.pop(current_user, None)


@pytest.mark.anyio
async def test_mapping_validation_rejects_wrong_types():
    apay = await _coa("21", "Hutang Usaha", "APAY")
    clearing = await _coa("21", "GRNI", "OCLY")
    oass = await _coa("15", "Uang Muka", "OASS")
    bank = await _coa("11", "Kas", "BANK")
    reve = await _coa("41", "Pendapatan", "REVE")
    exps = await _coa("51", "Beban", "EXPS")
    oexp = await _coa("51", "Beban Lain", "OEXP")
    cogs = await _coa("51", "HPP", "COGS")
    arec = await _coa("11", "Piutang", "AREC")
    _admin()
    before_journals = await server.db.journal_entries.count_documents({})
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            for bad in (bank, reve, exps, oexp, cogs, arec):
                res = await client.put("/api/settings/purchase-accounting", json={
                    "payable_account_id": bad["id"],
                    "purchase_clearing_account_id": clearing["id"],
                })
                assert res.status_code == 400, res.text

            for bad in (bank, reve, exps, oexp, cogs, arec, apay):
                res = await client.put("/api/settings/purchase-accounting", json={
                    "payable_account_id": apay["id"],
                    "purchase_clearing_account_id": bad["id"],
                })
                assert res.status_code == 400, res.text

            same = await client.put("/api/settings/purchase-accounting", json={
                "payable_account_id": apay["id"],
                "purchase_clearing_account_id": apay["id"],
            })
            assert same.status_code == 400

            ok = await client.put("/api/settings/purchase-accounting", json={
                "payable_account_id": apay["id"],
                "purchase_clearing_account_id": clearing["id"],
            })
            assert ok.status_code == 200, ok.text
            saved = ok.json()["settings"]
            assert saved["configured"] is True
            assert saved["payable_account_id"] == apay["id"]
            assert saved["purchase_clearing_account_id"] == clearing["id"]
            assert saved["accounts"]["payable"]["account_type"] == "APAY"
            assert saved["accounts"]["purchase_clearing"]["account_type"] == "OCLY"

            ok2 = await client.put("/api/settings/purchase-accounting", json={
                "payable_account_id": apay["id"],
                "purchase_clearing_account_id": oass["id"],
            })
            assert ok2.status_code == 200, ok2.text
            assert ok2.json()["settings"]["purchase_clearing_account_id"] == oass["id"]
        assert await server.db.journal_entries.count_documents({}) == before_journals
    finally:
        app.dependency_overrides.pop(current_user, None)


@pytest.mark.anyio
async def test_mapped_accounts_cannot_change_type_or_delete():
    apay = await _coa("21", "Hutang Usaha Lock", "APAY")
    clearing = await _coa("21", "GRNI Lock", "OCLY")
    _admin()
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            ok = await client.put("/api/settings/purchase-accounting", json={
                "payable_account_id": apay["id"],
                "purchase_clearing_account_id": clearing["id"],
            })
            assert ok.status_code == 200, ok.text
        with pytest.raises(AccountInUseError):
            await delete_account(server.db, apay["id"])
        with pytest.raises(AccountInUseError):
            await delete_account(server.db, clearing["id"])
        with pytest.raises(AccountInUseError, match="terkunci"):
            await update_account(server.db, apay["id"], {"account_type": "OCLY"}, user=_user())
        with pytest.raises(AccountInUseError, match="terkunci"):
            await update_account(server.db, clearing["id"], {"account_type": "APAY"}, user=_user())
        renamed = await update_account(server.db, apay["id"], {"name": "Hutang Usaha Lama"}, user=_user())
        assert renamed["name"] == "Hutang Usaha Lama"
        assert renamed["account_type"] == "APAY"
    finally:
        app.dependency_overrides.pop(current_user, None)


@pytest.mark.anyio
async def test_permissions_not_auto_granted():
    keys = {row[1] for row in PERMISSION_CATALOG}
    assert "settings.purchase_accounting.view" in keys
    assert "settings.purchase_accounting.manage" in keys
    assert "purchase_accounting.post" in keys
    assert "purchase_accounting.reverse" in keys
    assert "purchase.payment.accounting.post" in keys
    assert "purchase.payment.accounting.reverse" in keys
    assert "accounts_payable.view" in keys
    assert "nav.ledger.accounts_payable" in keys
    assert "settings.purchase_accounting.view" in ALL_PERMISSIONS
    assert "accounts_payable.view" in ALL_PERMISSIONS
    for role, perms in ROLE_DEFAULT_PERMISSIONS.items():
        if role == "admin":
            continue
        assert "settings.purchase_accounting.view" not in perms
        assert "settings.purchase_accounting.manage" not in perms
        assert "purchase_accounting.post" not in perms
        assert "purchase_accounting.reverse" not in perms
        assert "purchase.payment.accounting.post" not in perms
        assert "purchase.payment.accounting.reverse" not in perms
        assert "accounts_payable.view" not in perms
        assert "nav.ledger.accounts_payable" not in perms
