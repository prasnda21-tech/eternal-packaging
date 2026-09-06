"""Payroll Accounting Settings — Phase 0 mappings only, zero journals."""
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
from services.payroll_accounting_settings import SETTINGS_KEY, source_key_for_row


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
    before_settings = await server.db.app_settings.find_one({"key": SETTINGS_KEY})
    before_journals = await server.db.journal_entries.count_documents({})
    before_lines = await server.db.journal_lines.count_documents({})
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            res = await client.get("/api/settings/payroll-accounting")
            assert res.status_code == 200, res.text
            settings = res.json()["settings"]
            assert settings["configured"] is False
            assert settings["payroll_payable_valid"] is False
            keys = {row["source_key"] for row in settings["catalog"]}
            assert "salary_profile:BASE" in keys
            assert "overtime:OT_WEEKDAY" in keys
            assert "attendance:ATT_LATE" in keys
            assert "kasbon:KASBON" in keys
            assert all(row["source_key"] != "adjustment" for row in settings["catalog"])
        assert await server.db.app_settings.find_one({"key": SETTINGS_KEY}) == before_settings
        assert await server.db.journal_entries.count_documents({}) == before_journals
        assert await server.db.journal_lines.count_documents({}) == before_lines
    finally:
        app.dependency_overrides.pop(current_user, None)


@pytest.mark.anyio
async def test_mapping_validation_exact_ids_no_guessing():
    payable = await _coa("21", "Utang Gaji", "OCLY")
    expense = await _coa("51", "Beban Gaji", "EXPS")
    cogs = await _coa("51", "Beban Pokok", "COGS")
    other_exp = await _coa("51", "Beban Lain", "OEXP")
    asset = await _coa("11", "Piutang Karyawan", "OCAS")
    reve = await _coa("41", "Pendapatan", "REVE")
    bank = await _coa("11", "Kas", "BANK")
    _admin()
    before_journals = await server.db.journal_entries.count_documents({})
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            bad_pay = await client.put("/api/settings/payroll-accounting", json={
                "payroll_payable_account_id": expense["id"],
                "component_mappings": {},
            })
            assert bad_pay.status_code == 400

            bad_exp = await client.put("/api/settings/payroll-accounting", json={
                "payroll_payable_account_id": payable["id"],
                "component_mappings": {
                    "salary_profile:BASE": {"treatment": "expense_debit", "account_id": reve["id"]},
                },
            })
            assert bad_exp.status_code == 400

            bad_kind = await client.put("/api/settings/payroll-accounting", json={
                "payroll_payable_account_id": payable["id"],
                "component_mappings": {
                    "salary_profile:BASE": {"treatment": "liability_credit", "account_id": payable["id"]},
                },
            })
            assert bad_kind.status_code == 400

            by_name = await client.put("/api/settings/payroll-accounting", json={
                "payroll_payable_account_id": payable["id"],
                "component_mappings": {
                    "Gaji Pokok": {"treatment": "expense_debit", "account_id": expense["id"]},
                },
            })
            assert by_name.status_code == 400

            ok = await client.put("/api/settings/payroll-accounting", json={
                "payroll_payable_account_id": payable["id"],
                "component_mappings": {
                    "salary_profile:BASE": {"treatment": "expense_debit", "account_id": expense["id"]},
                    "attendance:ATT_LATE": {"treatment": "expense_credit", "account_id": cogs["id"]},
                    "kasbon:KASBON": {"treatment": "asset_credit", "account_id": asset["id"]},
                    "attendance:ATT_ALPHA": {"treatment": "liability_credit", "account_id": payable["id"]},
                },
            })
            assert ok.status_code == 200, ok.text
            saved = ok.json()["settings"]
            assert saved["payroll_payable_account_id"] == payable["id"]
            assert saved["component_mappings"]["salary_profile:BASE"]["account_id"] == expense["id"]
            stored = await server.db.app_settings.find_one({"key": SETTINGS_KEY}, {"_id": 0})
            assert stored["payroll_payable_account_id"] == payable["id"]
            assert "payroll_payable_account_code" not in stored
            assert stored["component_mappings"]["attendance:ATT_LATE"]["account_id"] == cogs["id"]
            assert stored["component_mappings"]["kasbon:KASBON"]["account_id"] == asset["id"]

            oexp_ok = await client.put("/api/settings/payroll-accounting", json={
                "payroll_payable_account_id": payable["id"],
                "component_mappings": {
                    "salary_profile:BASE": {"treatment": "expense_debit", "account_id": other_exp["id"]},
                },
            })
            assert oexp_ok.status_code == 200, oexp_ok.text

            bank_asset = await client.put("/api/settings/payroll-accounting", json={
                "payroll_payable_account_id": payable["id"],
                "component_mappings": {
                    "kasbon:KASBON": {"treatment": "asset_credit", "account_id": bank["id"]},
                },
            })
            assert bank_asset.status_code == 200
        assert await server.db.journal_entries.count_documents({}) == before_journals
    finally:
        app.dependency_overrides.pop(current_user, None)


@pytest.mark.anyio
async def test_custom_components_stay_independent_by_id():
    payable = await _coa("21", "Utang Gaji", "OCLY")
    expense_a = await _coa("51", "Beban A", "EXPS")
    expense_b = await _coa("51", "Beban B", "EXPS")
    cid_a = str(uuid.uuid4())
    cid_b = str(uuid.uuid4())
    await server.db.hr_salary_components.insert_many([
        {"id": cid_a, "code": "BONUS_A", "name": "Tunjangan Sama", "kind": "earning", "amount": 1, "active": True},
        {"id": cid_b, "code": "BONUS_B", "name": "Tunjangan Sama", "kind": "earning", "amount": 1, "active": True},
    ])
    _admin()
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            res = await client.get("/api/settings/payroll-accounting")
            keys = {row["source_key"] for row in res.json()["settings"]["catalog"]}
            assert f"component:{cid_a}" in keys
            assert f"component:{cid_b}" in keys

            saved = await client.put("/api/settings/payroll-accounting", json={
                "payroll_payable_account_id": payable["id"],
                "component_mappings": {
                    f"component:{cid_a}": {"treatment": "expense_debit", "account_id": expense_a["id"]},
                    f"component:{cid_b}": {"treatment": "expense_debit", "account_id": expense_b["id"]},
                },
            })
            assert saved.status_code == 200, saved.text
            mappings = saved.json()["settings"]["component_mappings"]
            assert mappings[f"component:{cid_a}"]["account_id"] == expense_a["id"]
            assert mappings[f"component:{cid_b}"]["account_id"] == expense_b["id"]
    finally:
        await server.db.hr_salary_components.delete_many({"id": {"$in": [cid_a, cid_b]}})
        app.dependency_overrides.pop(current_user, None)


@pytest.mark.anyio
async def test_source_identity_does_not_use_names():
    assert source_key_for_row({"source": "salary_profile", "code": "BASE", "name": "Anything"}) == "salary_profile:BASE"
    assert source_key_for_row({"source": "attendance", "code": "ATT_LATE", "name": "Telat"}) == "attendance:ATT_LATE"
    assert source_key_for_row({"source": "kasbon", "code": "KASBON", "schedule_id": "x"}) == "kasbon:KASBON"
    cid = str(uuid.uuid4())
    assert source_key_for_row({"source": "component", "code": "BPJS_KARYAWAN", "component_id": cid}) == f"component:{cid}"
    assert source_key_for_row({"source": "component", "code": "BPJS_KARYAWAN"}) is None
    aid = str(uuid.uuid4())
    assert source_key_for_row({"source": "adjustment", "code": "ADJUSTMENT", "adjustment_id": aid, "name": "Bonus"}) == f"adjustment:{aid}"
    assert source_key_for_row({"source": "adjustment", "code": "ADJUSTMENT", "name": "Bonus"}) is None


@pytest.mark.anyio
async def test_settings_permissions_not_auto_granted():
    keys = {k for _m, k, _l in PERMISSION_CATALOG}
    assert "payroll_accounting.view" in keys
    assert "payroll_accounting.manage" in keys
    assert "payroll_accounting.post" not in keys
    assert "payroll_accounting.view" in ALL_PERMISSIONS
    for role, perms in ROLE_DEFAULT_PERMISSIONS.items():
        if role == "admin":
            continue
        assert "payroll_accounting.view" not in perms
        assert "payroll_accounting.manage" not in perms
