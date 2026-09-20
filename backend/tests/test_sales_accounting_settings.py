"""Sales Accounting Settings — Phase 0 mappings only, zero journals."""
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

import server
from permissions import ALL_PERMISSIONS, PERMISSION_CATALOG, ROLE_DEFAULT_PERMISSIONS
from server import app, current_user
from services.chart_of_accounts import create_account, ensure_indexes
from services.sales_accounting_settings import SETTINGS_KEY


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
        "id": "admin-sas",
        "email": "admin@eternalpackaging.com",
        "name": "Admin SAS",
        "role": "admin",
    }


async def _coa(code_prefix: str, name: str, account_type: str) -> dict:
    suffix = uuid.uuid4().hex[:6]
    return await create_account(
        server.db,
        {"code": f"{code_prefix}{suffix}", "name": name, "account_type": account_type},
        user={"id": "t", "name": "t", "role": "admin"},
    )


@pytest.mark.anyio
async def test_unconfigured_get_is_zero_write():
    _admin()
    before_journals = await server.db.journal_entries.count_documents({})
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            res = await client.get("/api/settings/sales-accounting")
            assert res.status_code == 200, res.text
            settings = res.json()["settings"]
            assert settings["configured"] is False
            assert settings["receivable_account_id"] is None
            assert settings["revenue_account_id"] is None
            assert settings["output_tax_account_id"] is None
            assert settings["discount_treatment"] == "NET_TO_REVENUE"
            assert settings["future_revenue_basis"] == "after_discount"
            assert settings["future_tax_basis"] == "ppn_amount"
        assert await server.db.app_settings.find_one({"key": SETTINGS_KEY}) is None
        assert await server.db.journal_entries.count_documents({}) == before_journals
    finally:
        app.dependency_overrides.pop(current_user, None)


@pytest.mark.anyio
async def test_mapping_validation_exact_ids_no_guessing():
    arec = await _coa("11", "Piutang Usaha", "AREC")
    reve = await _coa("41", "Pendapatan", "REVE")
    ocly = await _coa("21", "PPN Keluaran", "OCLY")
    bank = await _coa("11", "Kas", "BANK")
    exps = await _coa("51", "Beban", "EXPS")
    cogs = await _coa("51", "HPP", "COGS")
    _admin()
    before_journals = await server.db.journal_entries.count_documents({})
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            bad_ar = await client.put("/api/settings/sales-accounting", json={
                "receivable_account_id": bank["id"],
                "revenue_account_id": reve["id"],
                "output_tax_account_id": ocly["id"],
            })
            assert bad_ar.status_code == 400

            bad_rev = await client.put("/api/settings/sales-accounting", json={
                "receivable_account_id": arec["id"],
                "revenue_account_id": cogs["id"],
                "output_tax_account_id": ocly["id"],
            })
            assert bad_rev.status_code == 400

            bad_tax = await client.put("/api/settings/sales-accounting", json={
                "receivable_account_id": arec["id"],
                "revenue_account_id": reve["id"],
                "output_tax_account_id": exps["id"],
            })
            assert bad_tax.status_code == 400

            ok = await client.put("/api/settings/sales-accounting", json={
                "receivable_account_id": arec["id"],
                "revenue_account_id": reve["id"],
                "output_tax_account_id": ocly["id"],
            })
            assert ok.status_code == 200, ok.text
            saved = ok.json()["settings"]
            assert saved["configured"] is True
            assert saved["receivable_account_id"] == arec["id"]
            assert saved["revenue_account_id"] == reve["id"]
            assert saved["output_tax_account_id"] == ocly["id"]
            assert saved["discount_treatment"] == "NET_TO_REVENUE"
            stored = await server.db.app_settings.find_one({"key": SETTINGS_KEY}, {"_id": 0})
            assert stored["receivable_account_id"] == arec["id"]
            assert "receivable_account_code" not in stored
            assert stored.get("name") is None

            apay = await _coa("21", "Utang Usaha", "APAY")
            upd = await client.put("/api/settings/sales-accounting", json={
                "receivable_account_id": arec["id"],
                "revenue_account_id": reve["id"],
                "output_tax_account_id": apay["id"],
            })
            assert upd.status_code == 200
            assert upd.json()["settings"]["output_tax_account_id"] == apay["id"]

            empty = await client.get("/api/settings/sales-accounting")
            assert empty.status_code == 200
            assert empty.json()["settings"]["configured"] is True
        assert await server.db.journal_entries.count_documents({}) == before_journals
    finally:
        app.dependency_overrides.pop(current_user, None)


@pytest.mark.anyio
async def test_settings_permissions_not_auto_granted():
    keys = {k for _m, k, _l in PERMISSION_CATALOG}
    assert "settings.sales_accounting.view" in keys
    assert "settings.sales_accounting.manage" in keys
    assert "sales_accounting.post" in keys
    assert "sales_accounting.reverse" in keys
    assert "settings.sales_accounting.view" in ALL_PERMISSIONS
    assert "sales_accounting.post" in ALL_PERMISSIONS
    assert "sales_accounting.reverse" in ALL_PERMISSIONS
    for role, perms in ROLE_DEFAULT_PERMISSIONS.items():
        if role == "admin":
            continue
        assert "settings.sales_accounting.view" not in perms
        assert "settings.sales_accounting.manage" not in perms
        assert "sales_accounting.post" not in perms
        assert "sales_accounting.reverse" not in perms
