"""Read-only payroll ↔ bank reconciliation Phase 1. Local DB only."""
from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("MONGO_URL", "mongodb://127.0.0.1:27017")
os.environ.setdefault("DB_NAME", "eternal_packaging_payroll_recon_test")
os.environ.setdefault("JWT_SECRET", "test-secret-key-12345678901234567890")
os.environ.setdefault("CORS_ORIGINS", "*")
os.environ.setdefault("STORAGE_PROVIDER", "local")

import server
from server import app, current_user
from services.hr_payroll import LINES, PERIODS, STATUS_DRAFT, STATUS_FINAL
from services.journal_core import JOURNAL_ENTRIES, JOURNAL_LINES, STATUS_POSTED, ensure_indexes as ensure_journal_indexes
from services.payroll_journal_posting import SOURCE_TYPE as ACCRUAL_SOURCE
from services.payroll_payment import (
    PAYMENT_LINES,
    PAYMENTS,
    SOURCE_TYPE as PAYMENT_SOURCE,
    STATUS_APPROVED,
    STATUS_CANCELLED,
    STATUS_DRAFT as PAY_DRAFT,
    STATUS_PAID,
    STATUS_REVERSED,
    ensure_indexes as ensure_payment_indexes,
)
from services.payroll_reconciliation import (
    STATUS_BELUM_ADA_PAYMENT,
    STATUS_BELUM_FINAL,
    STATUS_FINAL_BELUM_JURNAL,
    STATUS_MENUNGGU_PERSETUJUAN,
    STATUS_MISSING_BANK_JOURNAL,
    STATUS_MISMATCH_NOMINAL,
    STATUS_SESUAI,
    STATUS_SESUAI_TANPA_PEMBAYARAN,
    STATUS_SIAP_DIPOSTING,
    list_reconciliation,
)

_DB = "eternal_packaging_payroll_recon_test"


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture(autouse=True)
async def bind_and_clean():
    from motor.motor_asyncio import AsyncIOMotorClient

    previous = os.environ.get("DB_NAME")
    os.environ["DB_NAME"] = _DB
    server.client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    server.db = server.client[_DB]
    for coll in (
        PERIODS, LINES, PAYMENTS, PAYMENT_LINES, JOURNAL_ENTRIES, JOURNAL_LINES,
        "cash_bank_accounts", "chart_of_accounts",
    ):
        await server.db[coll].delete_many({})
    await ensure_journal_indexes(server.db)
    await ensure_payment_indexes(server.db)
    yield
    app.dependency_overrides.pop(current_user, None)
    if previous is None:
        os.environ.pop("DB_NAME", None)
    else:
        os.environ["DB_NAME"] = previous


def _admin():
    user = {
        "id": "recon-admin",
        "email": "recon@example.com",
        "name": "Admin",
        "role": "admin",
        "permissions": ["payroll.view", "payroll_payment.view", "cash_bank.view", "general_ledger.view"],
    }
    app.dependency_overrides[current_user] = lambda: user
    return user


def _client():
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


async def _period(*, status=STATUS_DRAFT, scope="monthly", net=4_500_000, line_count=1, start="2026-10-01", end="2026-10-07"):
    pid = f"per-{uuid.uuid4().hex[:8]}"
    await server.db[PERIODS].insert_one({
        "id": pid,
        "period_number": f"PRP-{pid[-6:]}",
        "period_scope": scope,
        "status": status,
        "start_date": start,
        "end_date": end,
        "year": 2026,
        "month": 10,
        "revision": 1,
        "group_id": str(uuid.uuid4()),
        "created_at": "2026-10-01T00:00:00+07:00",
        "updated_at": "2026-10-07T00:00:00+07:00",
    })
    for i in range(line_count):
        each = net / line_count if line_count else 0
        await server.db[LINES].insert_one({
            "id": f"ln-{uuid.uuid4().hex[:8]}",
            "period_id": pid,
            "employee_id": f"emp-{i}",
            "employee_snapshot": {"name": f"Op {i}", "employee_number": f"N{i}"},
            "gross": each,
            "total_deductions": 0,
            "net": each,
            "revision": 1,
        })
    return pid


async def _accrual(period_id, *, amount=4_500_000, zero_net=False, payable_account="acc-payable"):
    jid = f"j-acc-{uuid.uuid4().hex[:8]}"
    snap = {"account_id": payable_account, "amount": amount, "line_code": "PAYROLL_PAYABLE"}
    if zero_net:
        snap["zero_net"] = True
        snap["amount"] = 0
    await server.db[JOURNAL_ENTRIES].insert_one({
        "id": jid,
        "number": f"JE-{jid[-6:]}",
        "date": "2026-10-07",
        "source_type": ACCRUAL_SOURCE,
        "source_id": period_id,
        "status": STATUS_POSTED,
        "reversal_of": None,
        "payroll_payable": snap,
        "description": "Payroll accrual",
        "posted_at": "2026-10-07T10:00:00+07:00",
    })
    await server.db[PERIODS].update_one({"id": period_id}, {"$set": {"accounting_journal_id": jid}})
    if not zero_net and amount > 0:
        await server.db[JOURNAL_LINES].insert_many([
            {"id": str(uuid.uuid4()), "journal_id": jid, "account_id": "acc-expense", "debit": amount, "credit": 0},
            {"id": str(uuid.uuid4()), "journal_id": jid, "account_id": payable_account, "debit": 0, "credit": amount,
             "description": "Payroll — Utang Gaji"},
        ])
    return jid


async def _bank():
    bid = f"cb-{uuid.uuid4().hex[:6]}"
    coa = f"coa-bank-{uuid.uuid4().hex[:6]}"
    await server.db["cash_bank_accounts"].insert_one({
        "id": bid, "name": "BCA Operasional", "account_id": coa, "active": True,
    })
    return bid, coa


async def _payment(period_id, *, status=PAY_DRAFT, amount=4_500_000, bank_id=None, bank_coa=None,
                   payable="acc-payable", with_lines=True, accounting_journal_id=None):
    if not bank_id:
        bank_id, bank_coa = await _bank()
    pay_id = f"pay-{uuid.uuid4().hex[:8]}"
    doc = {
        "id": pay_id,
        "payment_number": f"PYB-{pay_id[-6:]}",
        "payroll_period_id": period_id,
        "status": status,
        "total_amount": amount,
        "payment_date": "2026-10-08",
        "cash_bank_account_id": bank_id,
        "bank_coa_id": bank_coa,
        "payable_account_id": payable,
        "accounting_journal_id": accounting_journal_id,
        "created_at": "2026-10-08T00:00:00+07:00",
    }
    await server.db[PAYMENTS].insert_one(doc)
    if with_lines:
        await server.db[PAYMENT_LINES].insert_one({
            "id": str(uuid.uuid4()),
            "payment_id": pay_id,
            "employee_id": "emp-0",
            "employee_name": "Op 0",
            "net_salary": amount,
        })
    return pay_id, bank_id, bank_coa


async def _payment_journal(payment_id, *, amount=4_500_000, payable="acc-payable", bank_coa="coa-bank",
                           debit=None, credit=None, bank_account=None):
    jid = f"j-pay-{uuid.uuid4().hex[:8]}"
    await server.db[JOURNAL_ENTRIES].insert_one({
        "id": jid,
        "number": f"JE-{jid[-6:]}",
        "date": "2026-10-08",
        "source_type": PAYMENT_SOURCE,
        "source_id": payment_id,
        "status": STATUS_POSTED,
        "reversal_of": None,
        "description": "Pembayaran Payroll",
        "posted_at": "2026-10-08T12:00:00+07:00",
    })
    await server.db[JOURNAL_LINES].insert_many([
        {"id": str(uuid.uuid4()), "journal_id": jid, "account_id": payable,
         "debit": debit if debit is not None else amount, "credit": 0},
        {"id": str(uuid.uuid4()), "journal_id": jid, "account_id": bank_account or bank_coa,
         "debit": 0, "credit": credit if credit is not None else amount},
    ])
    return jid


@pytest.mark.anyio
async def test_draft_period_belum_final():
    pid = await _period(status=STATUS_DRAFT)
    res = await list_reconciliation(server.db, limit=50)
    row = next(r for r in res["items"] if r["period_id"] == pid)
    assert row["reconciliation_status"] == STATUS_BELUM_FINAL


@pytest.mark.anyio
async def test_final_no_accrual():
    pid = await _period(status=STATUS_FINAL)
    res = await list_reconciliation(server.db, limit=50)
    row = next(r for r in res["items"] if r["period_id"] == pid)
    assert row["reconciliation_status"] == STATUS_FINAL_BELUM_JURNAL


@pytest.mark.anyio
async def test_final_accrual_no_payment():
    pid = await _period(status=STATUS_FINAL)
    await _accrual(pid)
    res = await list_reconciliation(server.db, limit=50)
    row = next(r for r in res["items"] if r["period_id"] == pid)
    assert row["reconciliation_status"] == STATUS_BELUM_ADA_PAYMENT


@pytest.mark.anyio
async def test_draft_payment_menunggu():
    pid = await _period(status=STATUS_FINAL)
    await _accrual(pid)
    await _payment(pid, status=PAY_DRAFT)
    res = await list_reconciliation(server.db, limit=50)
    row = next(r for r in res["items"] if r["period_id"] == pid)
    assert row["reconciliation_status"] == STATUS_MENUNGGU_PERSETUJUAN


@pytest.mark.anyio
async def test_approved_payment_siap():
    pid = await _period(status=STATUS_FINAL)
    await _accrual(pid)
    await _payment(pid, status=STATUS_APPROVED)
    res = await list_reconciliation(server.db, limit=50)
    row = next(r for r in res["items"] if r["period_id"] == pid)
    assert row["reconciliation_status"] == STATUS_SIAP_DIPOSTING


@pytest.mark.anyio
async def test_paid_all_equal_sesuai():
    pid = await _period(status=STATUS_FINAL)
    await _accrual(pid, amount=4_500_000)
    pay_id, bank_id, bank_coa = await _payment(pid, status=STATUS_PAID, amount=4_500_000)
    jid = await _payment_journal(pay_id, amount=4_500_000, bank_coa=bank_coa)
    await server.db[PAYMENTS].update_one({"id": pay_id}, {"$set": {"accounting_journal_id": jid}})
    res = await list_reconciliation(server.db, limit=50)
    row = next(r for r in res["items"] if r["period_id"] == pid)
    assert row["reconciliation_status"] == STATUS_SESUAI
    assert row["payment"]["bank_name"] == "BCA Operasional"
    assert row["net_total"] == 4_500_000


@pytest.mark.anyio
async def test_net_ne_payable_mismatch():
    pid = await _period(status=STATUS_FINAL, net=4_000_000)
    await _accrual(pid, amount=4_500_000)
    res = await list_reconciliation(server.db, limit=50)
    row = next(r for r in res["items"] if r["period_id"] == pid)
    assert row["reconciliation_status"] == STATUS_MISMATCH_NOMINAL
    assert "payroll_net_ne_accrual_payable" in row["mismatches"]


@pytest.mark.anyio
async def test_payment_ne_payable_mismatch():
    pid = await _period(status=STATUS_FINAL, net=4_500_000)
    await _accrual(pid, amount=4_500_000)
    await _payment(pid, status=STATUS_APPROVED, amount=4_000_000)
    res = await list_reconciliation(server.db, limit=50)
    row = next(r for r in res["items"] if r["period_id"] == pid)
    assert row["reconciliation_status"] == STATUS_MISMATCH_NOMINAL
    assert "payment_ne_accrual_payable" in row["mismatches"]


@pytest.mark.anyio
async def test_payment_journal_amount_mismatch():
    pid = await _period(status=STATUS_FINAL)
    await _accrual(pid, amount=4_500_000)
    pay_id, _, bank_coa = await _payment(pid, status=STATUS_PAID, amount=4_500_000)
    jid = await _payment_journal(pay_id, amount=4_500_000, bank_coa=bank_coa, debit=4_000_000, credit=4_000_000)
    await server.db[PAYMENTS].update_one({"id": pay_id}, {"$set": {"accounting_journal_id": jid}})
    res = await list_reconciliation(server.db, limit=50)
    row = next(r for r in res["items"] if r["period_id"] == pid)
    assert row["reconciliation_status"] == STATUS_MISMATCH_NOMINAL


@pytest.mark.anyio
async def test_paid_missing_payment_journal():
    pid = await _period(status=STATUS_FINAL)
    await _accrual(pid)
    await _payment(pid, status=STATUS_PAID, accounting_journal_id="missing-jid")
    res = await list_reconciliation(server.db, limit=50)
    row = next(r for r in res["items"] if r["period_id"] == pid)
    assert row["reconciliation_status"] == STATUS_MISSING_BANK_JOURNAL


@pytest.mark.anyio
async def test_bank_coa_mismatch():
    pid = await _period(status=STATUS_FINAL)
    await _accrual(pid)
    pay_id, _, bank_coa = await _payment(pid, status=STATUS_PAID)
    jid = await _payment_journal(pay_id, bank_coa=bank_coa, bank_account="other-coa")
    await server.db[PAYMENTS].update_one({"id": pay_id}, {"$set": {"accounting_journal_id": jid}})
    res = await list_reconciliation(server.db, limit=50)
    row = next(r for r in res["items"] if r["period_id"] == pid)
    assert row["reconciliation_status"] == STATUS_MISMATCH_NOMINAL
    assert "bank_coa_mismatch" in row["mismatches"]


@pytest.mark.anyio
async def test_reversed_payment_shows_belum_dibayar():
    pid = await _period(status=STATUS_FINAL)
    await _accrual(pid)
    pay_id, _, _ = await _payment(pid, status=STATUS_REVERSED)
    res = await list_reconciliation(server.db, limit=50)
    row = next(r for r in res["items"] if r["period_id"] == pid)
    assert row["reconciliation_status"] == STATUS_BELUM_ADA_PAYMENT
    assert row["history_marker"]
    assert "direversal" in row["history_marker"].lower() or "Reversal" in (row["history_marker"] or "")


@pytest.mark.anyio
async def test_cancelled_payment_shows_belum_dibayar():
    pid = await _period(status=STATUS_FINAL)
    await _accrual(pid)
    await _payment(pid, status=STATUS_CANCELLED)
    res = await list_reconciliation(server.db, limit=50)
    row = next(r for r in res["items"] if r["period_id"] == pid)
    assert row["reconciliation_status"] == STATUS_BELUM_ADA_PAYMENT
    assert row["history_marker"]


@pytest.mark.anyio
async def test_zero_net_sesuai_tanpa_pembayaran():
    pid = await _period(status=STATUS_FINAL, net=0)
    await _accrual(pid, amount=0, zero_net=True)
    res = await list_reconciliation(server.db, limit=50)
    row = next(r for r in res["items"] if r["period_id"] == pid)
    assert row["reconciliation_status"] == STATUS_SESUAI_TANPA_PEMBAYARAN
    assert row["zero_net"] is True


@pytest.mark.anyio
async def test_endpoint_no_writes():
    _admin()
    pid = await _period(status=STATUS_FINAL)
    await _accrual(pid)
    before_periods = await server.db[PERIODS].count_documents({})
    before_payments = await server.db[PAYMENTS].count_documents({})
    before_journals = await server.db[JOURNAL_ENTRIES].count_documents({})
    async with _client() as c:
        listed = await c.get("/api/hr-payroll/reconciliation", params={"limit": 50})
        assert listed.status_code == 200, listed.text
        detail = await c.get(f"/api/hr-payroll/reconciliation/{pid}")
        assert detail.status_code == 200, detail.text
        body = detail.json()["item"]
        assert body["reconciliation_status"] == STATUS_BELUM_ADA_PAYMENT
        assert any(r["code"] == "net_eq_payable" for r in body["rules"])
    assert await server.db[PERIODS].count_documents({}) == before_periods
    assert await server.db[PAYMENTS].count_documents({}) == before_payments
    assert await server.db[JOURNAL_ENTRIES].count_documents({}) == before_journals


@pytest.mark.anyio
async def test_permission_requires_both():
    user = {
        "id": "u1",
        "email": f"recon-deny-{uuid.uuid4().hex[:6]}@example.com",
        "name": "U",
        "role": "operator",
        "permissions_override": {"payroll.view": True, "payroll_payment.view": False},
    }
    await server.db.users.insert_one({"id": user["id"], "email": user["email"], "role": "operator"})
    app.dependency_overrides[current_user] = lambda: user
    async with _client() as c:
        res = await c.get("/api/hr-payroll/reconciliation")
        assert res.status_code == 403, res.text
