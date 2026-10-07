"""Payroll workflow simplification: REVIEW→FINAL and Bayar orchestration."""
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
os.environ["DB_NAME"] = f"eternal_packaging_payroll_workflow_simplify_test_{_worker}"
os.environ.setdefault("JWT_SECRET", "test-secret-key-12345678901234567890")
os.environ.setdefault("CORS_ORIGINS", "*")
os.environ.setdefault("STORAGE_PROVIDER", "local")

import server
from server import app, current_user
from services.accounting_periods import close_period
from services.cash_bank_master import create_cash_bank, ensure_indexes as ensure_cash_bank
from services.chart_of_accounts import create_account, ensure_indexes as ensure_coa
from services.hr_payroll import (
    ATTENDANCE,
    COMPONENTS,
    EMPLOYEES,
    KASBON,
    LINES,
    PERIODS,
    STATUS_APPROVAL,
    STATUS_DRAFT,
    STATUS_FINAL,
    STATUS_REVIEW,
    ensure_indexes,
)
from services.journal_core import ensure_indexes as ensure_journal_indexes
from services.payroll_accounting_settings import SETTINGS_KEY
from services.payroll_payment import PAYMENT_LINES, PAYMENTS, SOURCE_TYPE


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture(autouse=True)
async def bind_and_clean():
    from motor.motor_asyncio import AsyncIOMotorClient

    server.client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    server.db = server.client[os.environ["DB_NAME"]]
    await ensure_indexes(server.db)
    await ensure_coa(server.db)
    await ensure_journal_indexes(server.db)
    await ensure_cash_bank(server.db)
    await server.db.app_settings.delete_many({"key": SETTINGS_KEY})
    for coll in (EMPLOYEES, COMPONENTS, ATTENDANCE, KASBON, PERIODS, LINES, PAYMENTS, PAYMENT_LINES):
        await server.db[coll].delete_many({})
    await server.db.journal_entries.delete_many({})
    await server.db.journal_lines.delete_many({})
    await server.db.accounting_periods.delete_many({})
    await server.db.cash_bank_accounts.delete_many({})
    yield
    app.dependency_overrides.pop(current_user, None)


def _admin():
    user = {
        "id": "admin-wfs",
        "email": "admin-wfs@eternalpackaging.com",
        "name": "Admin WFS",
        "role": "admin",
    }
    app.dependency_overrides[current_user] = lambda: user
    return user


def _clerk(override: dict):
    app.dependency_overrides[current_user] = lambda: {
        "id": "clerk-wfs",
        "email": "clerk-wfs@eternalpackaging.com",
        "name": "Clerk WFS",
        "role": "payroll_clerk",
        "permissions_override": override,
    }


def _client():
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


async def _coa(code_prefix: str, name: str, account_type: str) -> dict:
    suffix = uuid.uuid4().hex[:6]
    return await create_account(
        server.db,
        {"code": f"{code_prefix}{suffix}", "name": name, "account_type": account_type},
        user={"id": "t", "name": "t", "role": "admin"},
    )


async def _setup_accounts():
    return {
        "payable": await _coa("21", "Utang Gaji", "OCLY"),
        "salary": await _coa("51", "Beban Gaji", "EXPS"),
        "bank": await _coa("11", "Bank Operasional", "BANK"),
    }


async def _bank(account: dict) -> dict:
    return await create_cash_bank(
        server.db,
        {"name": f"Bank {uuid.uuid4().hex[:4]}", "account_id": account["id"], "active": True},
        user={"id": "t", "name": "t", "role": "admin"},
    )


async def _period_to_review(c, *, year: int, month: int, map_accounts=None):
    await c.post("/api/hr-payroll/employees", json={
        "name": "Andi Workflow",
        "employee_number": f"NIK-{uuid.uuid4().hex[:8]}",
        "role": "operator",
        "division": "operator",
        "salary_profile": {"base_salary": 1_000_000, "allowances": []},
    })
    created = await c.post("/api/hr-payroll/periods", json={
        "year": year,
        "month": month,
        "start_date": f"{year}-{month:02d}-01",
        "end_date": f"{year}-{month:02d}-28",
    })
    assert created.status_code == 200, created.text
    period = created.json()["period"]
    calc = await c.post(f"/api/hr-payroll/periods/{period['id']}/calculate")
    assert calc.status_code == 200, calc.text
    if map_accounts:
        saved = await c.put("/api/settings/payroll-accounting", json={
            "payroll_payable_account_id": map_accounts["payable"]["id"],
            "component_mappings": {
                "salary_profile:BASE": {
                    "treatment": "expense_debit",
                    "account_id": map_accounts["salary"]["id"],
                },
            },
        })
        assert saved.status_code == 200, saved.text
    moved = await c.post(
        f"/api/hr-payroll/periods/{period['id']}/transition",
        json={"to_status": STATUS_REVIEW},
    )
    assert moved.status_code == 200, moved.text
    return moved.json()["period"]


async def _accrual_count(period_id: str) -> int:
    return await server.db.journal_entries.count_documents({
        "source_type": "payroll_period",
        "source_id": period_id,
        "reversal_of": None,
    })


async def _payment_journals(payment_id: str):
    return await server.db.journal_entries.find(
        {"source_type": SOURCE_TYPE, "source_id": payment_id, "reversal_of": None},
        {"_id": 0},
    ).to_list(10)


@pytest.mark.anyio
async def test_finalize_from_review_with_all_perms():
    accounts = await _setup_accounts()
    _admin()
    async with _client() as c:
        period = await _period_to_review(c, year=2097, month=1, map_accounts=accounts)
        assert period["status"] == STATUS_REVIEW
        done = await c.post(f"/api/hr-payroll/periods/{period['id']}/finalize-from-review", json={})
        assert done.status_code == 200, done.text
        fresh = done.json()["period"]
        assert fresh["status"] == STATUS_FINAL
        assert fresh.get("frozen") is True
        assert fresh.get("final_snapshot")
        assert await _accrual_count(period["id"]) == 1
        stored = await server.db[PERIODS].find_one({"id": period["id"]}, {"_id": 0})
        events = list(stored.get("audit_events") or [])
        gate = next((e for e in events if e.get("action") == "approval_gate_satisfied"), None)
        approval = next((e for e in events if e.get("action") == "transition_approval"), None)
        assert gate and gate.get("actor") and gate.get("mode") == "combined_finalize"
        assert approval and approval.get("mode") == "combined_finalize"
        assert (stored.get("last_transition") or {}).get("mode") == "combined_finalize" or (
            (stored.get("last_transition") or {}).get("to") == STATUS_FINAL
        )


@pytest.mark.anyio
async def test_finalize_from_review_missing_each_perm():
    accounts = await _setup_accounts()
    _admin()
    async with _client() as c:
        period = await _period_to_review(c, year=2097, month=2, map_accounts=accounts)
        period_id = period["id"]

        base = {
            "payroll.view": True,
            "payroll.approve": True,
            "payroll.finalize": True,
            "payroll_accounting.post": True,
        }
        for missing in ("payroll.approve", "payroll.finalize", "payroll_accounting.post"):
            override = dict(base)
            override[missing] = False
            # clear others so role defaults do not grant
            _clerk({k: v for k, v in override.items() if v})
            denied = await c.post(f"/api/hr-payroll/periods/{period_id}/finalize-from-review", json={})
            assert denied.status_code == 403, (missing, denied.text)
            assert missing in denied.text
            stored = await server.db[PERIODS].find_one({"id": period_id}, {"_id": 0, "status": 1})
            assert stored["status"] == STATUS_REVIEW
            assert await _accrual_count(period_id) == 0


@pytest.mark.anyio
async def test_finalize_from_review_retry_no_duplicate_journal():
    accounts = await _setup_accounts()
    _admin()
    async with _client() as c:
        period = await _period_to_review(c, year=2097, month=3, map_accounts=accounts)
        first = await c.post(f"/api/hr-payroll/periods/{period['id']}/finalize-from-review", json={})
        assert first.status_code == 200, first.text
        second = await c.post(f"/api/hr-payroll/periods/{period['id']}/finalize-from-review", json={})
        assert second.status_code == 200, second.text
        assert second.json()["period"]["status"] == STATUS_FINAL
        assert await _accrual_count(period["id"]) == 1


@pytest.mark.anyio
async def test_legacy_approval_to_final_still_works():
    accounts = await _setup_accounts()
    _admin()
    async with _client() as c:
        period = await _period_to_review(c, year=2097, month=4, map_accounts=accounts)
        approved = await c.post(
            f"/api/hr-payroll/periods/{period['id']}/transition",
            json={"to_status": STATUS_APPROVAL},
        )
        assert approved.status_code == 200, approved.text
        assert approved.json()["period"]["status"] == STATUS_APPROVAL
        final = await c.post(
            f"/api/hr-payroll/periods/{period['id']}/transition",
            json={"to_status": STATUS_FINAL},
        )
        assert final.status_code == 200, final.text
        assert final.json()["period"]["status"] == STATUS_FINAL
        assert await _accrual_count(period["id"]) == 1


@pytest.mark.anyio
async def test_review_back_to_draft_still_works():
    _admin()
    async with _client() as c:
        period = await _period_to_review(c, year=2097, month=5)
        back = await c.post(
            f"/api/hr-payroll/periods/{period['id']}/transition",
            json={"to_status": STATUS_DRAFT},
        )
        assert back.status_code == 200, back.text
        assert back.json()["period"]["status"] == STATUS_DRAFT


@pytest.mark.anyio
async def test_transition_final_from_review_missing_each_perm():
    accounts = await _setup_accounts()
    _admin()
    async with _client() as c:
        period = await _period_to_review(c, year=2097, month=6, map_accounts=accounts)
        period_id = period["id"]
        base = {
            "payroll.view": True,
            "payroll.approve": True,
            "payroll.finalize": True,
            "payroll_accounting.post": True,
        }
        for missing in ("payroll.approve", "payroll.finalize", "payroll_accounting.post"):
            override = dict(base)
            override[missing] = False
            _clerk({k: v for k, v in override.items() if v})
            denied = await c.post(
                f"/api/hr-payroll/periods/{period_id}/transition",
                json={"to_status": STATUS_FINAL},
            )
            assert denied.status_code == 403, (missing, denied.text)
            assert missing in denied.text
            stored = await server.db[PERIODS].find_one({"id": period_id}, {"_id": 0, "status": 1})
            assert stored["status"] == STATUS_REVIEW
            assert await _accrual_count(period_id) == 0


async def _final_business_state(period_id: str) -> dict:
    period = await server.db[PERIODS].find_one({"id": period_id}, {"_id": 0})
    journal = await server.db.journal_entries.find_one(
        {"source_type": "payroll_period", "source_id": period_id, "reversal_of": None},
        {"_id": 0, "id": 1, "payroll_payable": 1, "status": 1},
    )
    return {
        "status": period.get("status"),
        "frozen": bool(period.get("frozen")),
        "final_snapshot": bool(period.get("final_snapshot")),
        "finalized_by_id": ((period.get("finalized_by") or {}).get("id")),
        "accounting_journal_id": (journal or {}).get("id") or period.get("accounting_journal_id"),
        "payable_amount": ((journal or {}).get("payroll_payable") or {}).get("amount"),
        "accrual_count": await _accrual_count(period_id),
    }


async def _second_period_to_review(c, *, year: int, month: int):
    """Reuse existing employees so Path A/B compare equal payable totals."""
    created = await c.post("/api/hr-payroll/periods", json={
        "year": year,
        "month": month,
        "start_date": f"{year}-{month:02d}-01",
        "end_date": f"{year}-{month:02d}-28",
    })
    assert created.status_code == 200, created.text
    period = created.json()["period"]
    calc = await c.post(f"/api/hr-payroll/periods/{period['id']}/calculate")
    assert calc.status_code == 200, calc.text
    moved = await c.post(
        f"/api/hr-payroll/periods/{period['id']}/transition",
        json={"to_status": STATUS_REVIEW},
    )
    assert moved.status_code == 200, moved.text
    return moved.json()["period"]


@pytest.mark.anyio
async def test_path_a_and_path_b_review_to_final_parity():
    accounts = await _setup_accounts()
    _admin()
    async with _client() as c:
        period_a = await _period_to_review(c, year=2098, month=2, map_accounts=accounts)
        period_b = await _second_period_to_review(c, year=2098, month=3)

        path_a = await c.post(
            f"/api/hr-payroll/periods/{period_a['id']}/finalize-from-review",
            json={},
        )
        assert path_a.status_code == 200, path_a.text
        assert path_a.json()["period"]["status"] == STATUS_FINAL

        path_b = await c.post(
            f"/api/hr-payroll/periods/{period_b['id']}/transition",
            json={"to_status": STATUS_FINAL},
        )
        assert path_b.status_code == 200, path_b.text
        assert path_b.json()["period"]["status"] == STATUS_FINAL

        state_a = await _final_business_state(period_a["id"])
        state_b = await _final_business_state(period_b["id"])
        assert state_a["status"] == state_b["status"] == STATUS_FINAL
        assert state_a["frozen"] is True and state_b["frozen"] is True
        assert state_a["final_snapshot"] is True and state_b["final_snapshot"] is True
        assert state_a["accrual_count"] == state_b["accrual_count"] == 1
        assert state_a["accounting_journal_id"] and state_b["accounting_journal_id"]
        assert state_a["accounting_journal_id"] != state_b["accounting_journal_id"]
        assert float(state_a["payable_amount"]) == float(state_b["payable_amount"]) == 1_000_000.0
        assert state_a["finalized_by_id"] and state_b["finalized_by_id"]

        for period_id in (period_a["id"], period_b["id"]):
            stored = await server.db[PERIODS].find_one({"id": period_id}, {"_id": 0})
            approval = next(
                (e for e in (stored.get("audit_events") or []) if e.get("action") == "transition_approval"),
                None,
            )
            gate = next(
                (e for e in (stored.get("audit_events") or []) if e.get("action") == "approval_gate_satisfied"),
                None,
            )
            assert gate and gate.get("mode") == "combined_finalize"
            assert approval and approval.get("mode") == "combined_finalize"


@pytest.mark.anyio
async def test_manual_approval_audit_distinct_from_combined():
    accounts = await _setup_accounts()
    _admin()
    async with _client() as c:
        period = await _period_to_review(c, year=2098, month=4, map_accounts=accounts)
        approved = await c.post(
            f"/api/hr-payroll/periods/{period['id']}/transition",
            json={"to_status": STATUS_APPROVAL, "note": "human approve"},
        )
        assert approved.status_code == 200, approved.text
        stored = await server.db[PERIODS].find_one({"id": period["id"]}, {"_id": 0})
        approval = next(
            (e for e in (stored.get("audit_events") or []) if e.get("action") == "transition_approval"),
            None,
        )
        assert approval and approval.get("mode") == "manual"
        assert not any(
            e.get("action") == "approval_gate_satisfied" for e in (stored.get("audit_events") or [])
        )
        assert (stored.get("last_transition") or {}).get("mode") == "manual"
        assert (stored.get("last_transition") or {}).get("combined_finalize") is not True


@pytest.mark.anyio
async def test_bayar_creates_approves_and_posts():
    accounts = await _setup_accounts()
    bank = await _bank(accounts["bank"])
    _admin()
    async with _client() as c:
        period = await _period_to_review(c, year=2097, month=7, map_accounts=accounts)
        await c.post(f"/api/hr-payroll/periods/{period['id']}/finalize-from-review", json={})
        pay = await c.post(
            f"/api/hr-payroll/periods/{period['id']}/pay",
            json={"cash_bank_account_id": bank["id"], "payment_date": "2097-07-20"},
        )
        assert pay.status_code == 200, pay.text
        body = pay.json()
        assert body["payment_status"] == "PAID"
        payment = body["payment"]
        assert payment["status"] == "PAID"
        assert payment["created_by"]
        assert payment["approved_by"]
        assert payment["paid_by"]
        assert payment["created_at"] and payment["approved_at"] and payment["paid_at"]
        assert float(payment["total_amount"]) == 1_000_000.0
        journals = await _payment_journals(payment["id"])
        assert len(journals) == 1
        lines = await server.db.journal_lines.find(
            {"journal_id": journals[0]["id"]}, {"_id": 0},
        ).to_list(20)
        debit_payable = [ln for ln in lines if ln.get("debit") and ln.get("account_id") == accounts["payable"]["id"]]
        credit_bank = [ln for ln in lines if ln.get("credit") and ln.get("account_id") == accounts["bank"]["id"]]
        assert len(debit_payable) == 1
        assert len(credit_bank) == 1
        assert float(debit_payable[0]["debit"]) == 1_000_000.0


@pytest.mark.anyio
async def test_bayar_amount_cannot_be_overridden():
    accounts = await _setup_accounts()
    bank = await _bank(accounts["bank"])
    _admin()
    async with _client() as c:
        period = await _period_to_review(c, year=2097, month=8, map_accounts=accounts)
        await c.post(f"/api/hr-payroll/periods/{period['id']}/finalize-from-review", json={})
        pay = await c.post(
            f"/api/hr-payroll/periods/{period['id']}/pay",
            json={
                "cash_bank_account_id": bank["id"],
                "payment_date": "2097-08-20",
                "total_amount": 1,
                "amount": 1,
            },
        )
        assert pay.status_code == 200, pay.text
        assert float(pay.json()["payment"]["total_amount"]) == 1_000_000.0


@pytest.mark.anyio
async def test_bayar_resume_from_draft_and_approved():
    accounts = await _setup_accounts()
    bank = await _bank(accounts["bank"])
    _admin()
    async with _client() as c:
        period = await _period_to_review(c, year=2097, month=9, map_accounts=accounts)
        await c.post(f"/api/hr-payroll/periods/{period['id']}/finalize-from-review", json={})
        created = await c.post(
            f"/api/hr-payroll/periods/{period['id']}/payments",
            json={"cash_bank_account_id": bank["id"], "payment_date": "2097-09-20"},
        )
        assert created.status_code == 200, created.text
        payment_id = created.json()["payment"]["id"]
        assert created.json()["payment"]["status"] == "DRAFT"

        resumed = await c.post(f"/api/hr-payroll/periods/{period['id']}/pay", json={})
        assert resumed.status_code == 200, resumed.text
        assert resumed.json()["payment"]["id"] == payment_id
        assert resumed.json()["payment_status"] == "PAID"
        assert await server.db[PAYMENTS].count_documents({
            "payroll_period_id": period["id"],
            "status": {"$in": ["DRAFT", "APPROVED", "PAID"]},
        }) == 1
        assert len(await _payment_journals(payment_id)) == 1

        again = await c.post(f"/api/hr-payroll/periods/{period['id']}/pay", json={})
        assert again.status_code == 200, again.text
        assert again.json().get("already") is True
        assert len(await _payment_journals(payment_id)) == 1


@pytest.mark.anyio
async def test_bayar_resume_from_approved_only():
    accounts = await _setup_accounts()
    bank = await _bank(accounts["bank"])
    _admin()
    async with _client() as c:
        period = await _period_to_review(c, year=2097, month=10, map_accounts=accounts)
        await c.post(f"/api/hr-payroll/periods/{period['id']}/finalize-from-review", json={})
        created = await c.post(
            f"/api/hr-payroll/periods/{period['id']}/payments",
            json={"cash_bank_account_id": bank["id"], "payment_date": "2097-10-20"},
        )
        payment_id = created.json()["payment"]["id"]
        approved = await c.post(f"/api/hr-payroll/payments/{payment_id}/approve")
        assert approved.status_code == 200, approved.text
        pay = await c.post(f"/api/hr-payroll/periods/{period['id']}/pay", json={})
        assert pay.status_code == 200, pay.text
        assert pay.json()["payment"]["id"] == payment_id
        assert pay.json()["payment_status"] == "PAID"
        assert len(await _payment_journals(payment_id)) == 1


@pytest.mark.anyio
async def test_bayar_inactive_bank_and_locked_period_block():
    accounts = await _setup_accounts()
    bank = await _bank(accounts["bank"])
    _admin()
    async with _client() as c:
        period = await _period_to_review(c, year=2097, month=11, map_accounts=accounts)
        await c.post(f"/api/hr-payroll/periods/{period['id']}/finalize-from-review", json={})
        await server.db.cash_bank_accounts.update_one({"id": bank["id"]}, {"$set": {"active": False}})
        blocked = await c.post(
            f"/api/hr-payroll/periods/{period['id']}/pay",
            json={"cash_bank_account_id": bank["id"], "payment_date": "2097-11-20"},
        )
        assert blocked.status_code == 400, blocked.text
        assert await server.db[PAYMENTS].count_documents({"payroll_period_id": period["id"]}) == 0

        await server.db.cash_bank_accounts.update_one({"id": bank["id"]}, {"$set": {"active": True}})
        created = await c.post(
            f"/api/hr-payroll/periods/{period['id']}/payments",
            json={"cash_bank_account_id": bank["id"], "payment_date": "2097-11-20"},
        )
        payment_id = created.json()["payment"]["id"]
        await c.post(f"/api/hr-payroll/payments/{payment_id}/approve")
        await close_period(
            server.db,
            "2097-11",
            user={"id": "t", "name": "t", "role": "admin"},
            reason="tutup uji",
        )
        locked = await c.post(f"/api/hr-payroll/periods/{period['id']}/pay", json={})
        assert locked.status_code == 409, locked.text
        assert len(await _payment_journals(payment_id)) == 0
        stored = await server.db[PAYMENTS].find_one({"id": payment_id}, {"_id": 0, "status": 1})
        assert stored["status"] == "APPROVED"


@pytest.mark.anyio
async def test_bayar_zero_net_refused():
    """Legitimate zero-net: positive earnings fully offset by mapped deduction.

    Salary=0 alone cannot finalize (journal total would be zero). Source supports
    FINAL when balanced non-zero journal lines exist and net == 0.
    """
    accounts = await _setup_accounts()
    deduct_liab = await _coa("22", "Potongan Lain", "OCLY")
    bank = await _bank(accounts["bank"])
    _admin()
    async with _client() as c:
        await c.post("/api/hr-payroll/employees", json={
            "name": "Zero Net",
            "employee_number": f"NIK-{uuid.uuid4().hex[:8]}",
            "role": "operator",
            "division": "operator",
            "salary_profile": {"base_salary": 1_000_000, "allowances": []},
        })
        created = await c.post("/api/hr-payroll/periods", json={
            "year": 2097,
            "month": 12,
            "start_date": "2097-12-01",
            "end_date": "2097-12-28",
        })
        assert created.status_code == 200, created.text
        period = created.json()["period"]
        await c.put("/api/settings/payroll-accounting", json={
            "payroll_payable_account_id": accounts["payable"]["id"],
            "component_mappings": {
                "salary_profile:BASE": {
                    "treatment": "expense_debit",
                    "account_id": accounts["salary"]["id"],
                },
            },
        })
        calc = await c.post(f"/api/hr-payroll/periods/{period['id']}/calculate")
        assert calc.status_code == 200, calc.text
        line = calc.json()["lines"][0]
        assert float(line["net"]) == 1_000_000.0
        adjusted = await c.post(
            f"/api/hr-payroll/periods/{period['id']}/lines/{line['id']}/adjust",
            json={
                "kind": "deduction",
                "amount": 1_000_000,
                "reason": "offset penuh uji zero-net",
                "accounting_treatment": "liability_credit",
                "accounting_account_id": deduct_liab["id"],
            },
        )
        assert adjusted.status_code == 200, adjusted.text
        assert float(adjusted.json()["line"]["net"]) == 0.0

        review = await c.post(
            f"/api/hr-payroll/periods/{period['id']}/transition",
            json={"to_status": STATUS_REVIEW},
        )
        assert review.status_code == 200, review.text
        final = await c.post(f"/api/hr-payroll/periods/{period['id']}/finalize-from-review", json={})
        assert final.status_code == 200, final.text
        assert final.json()["period"]["status"] == STATUS_FINAL
        assert final.json()["period"].get("frozen") is True

        accrual = await server.db.journal_entries.find_one(
            {"source_type": "payroll_period", "source_id": period["id"], "reversal_of": None},
            {"_id": 0},
        )
        assert accrual
        payable = accrual.get("payroll_payable") or {}
        assert float(payable.get("amount") or 0) == 0.0
        assert payable.get("zero_net") is True

        refused = await c.post(
            f"/api/hr-payroll/periods/{period['id']}/pay",
            json={"cash_bank_account_id": bank["id"], "payment_date": "2097-12-20"},
        )
        assert refused.status_code == 409, refused.text
        assert await server.db[PAYMENTS].count_documents({"payroll_period_id": period["id"]}) == 0
        assert await server.db.journal_entries.count_documents({"source_type": "payroll_payment"}) == 0


@pytest.mark.anyio
async def test_bayar_requires_all_payment_perms():
    accounts = await _setup_accounts()
    bank = await _bank(accounts["bank"])
    _admin()
    async with _client() as c:
        period = await _period_to_review(c, year=2098, month=1, map_accounts=accounts)
        await c.post(f"/api/hr-payroll/periods/{period['id']}/finalize-from-review", json={})
        for missing in ("payroll_payment.create", "payroll_payment.approve", "payroll_payment.post"):
            override = {
                "payroll_payment.create": True,
                "payroll_payment.approve": True,
                "payroll_payment.post": True,
                "payroll.view": True,
            }
            override[missing] = False
            _clerk({k: v for k, v in override.items() if v})
            denied = await c.post(
                f"/api/hr-payroll/periods/{period['id']}/pay",
                json={"cash_bank_account_id": bank["id"], "payment_date": "2098-01-20"},
            )
            assert denied.status_code == 403, (missing, denied.text)
            assert missing in denied.text
