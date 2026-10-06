"""Manual Kas & Bank payment cannot use the active payroll payable account."""
from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("MONGO_URL", "mongodb://127.0.0.1:27017")
_worker = os.environ.get("PYTEST_XDIST_WORKER") or "main"
os.environ["DB_NAME"] = f"eternal_packaging_cb_payroll_guard_{_worker}"
os.environ.setdefault("JWT_SECRET", "test-secret-key-12345678901234567890")

import server
from services.cash_bank_master import create_cash_bank, ensure_indexes as ensure_cb
from services.cash_bank_vouchers import (
    PAYROLL_PAYABLE_BLOCK_CODE,
    VoucherValidationError,
    create_payment,
    post_voucher,
    update_draft,
)
from services.chart_of_accounts import create_account, ensure_indexes as ensure_coa
from services.journal_core import JOURNAL_ENTRIES, SOURCE_PAYMENT, ensure_indexes as ensure_journal
from services.payroll_accounting_settings import SETTINGS_KEY
from services.payroll_journal_posting import SOURCE_TYPE as ACCRUAL_SOURCE
from services.payroll_payment import SOURCE_TYPE as PAYMENT_SOURCE

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
    await ensure_journal(db)
    await db.app_settings.delete_many({"key": SETTINGS_KEY})
    yield
    await db.app_settings.delete_many({"key": SETTINGS_KEY})


def _user():
    return {"id": "u-guard", "role": "admin", "email": "guard@example.com"}


async def _account(code, name, account_type):
    return await create_account(server.db, {"code": code, "name": name, "account_type": account_type}, user=_user())


async def _bank():
    coa = await _account(f"BK{uuid.uuid4().hex[:6]}", "Bank", "BANK")
    bank = await create_cash_bank(server.db, {"name": "Bank Utama", "account_id": coa["id"]}, user=_user())
    return bank


async def _map(*, payable_id=None, expense_id=None, other_treatment=None, other_id=None):
    mappings = {}
    if expense_id:
        mappings["operator_piece:UPAH_BORONGAN"] = {
            "treatment": "expense_debit",
            "account_id": expense_id,
        }
    if other_id:
        mappings["operator_bonus:BONUS_OPERATOR"] = {
            "treatment": other_treatment or "liability_credit",
            "account_id": other_id,
        }
    await server.db.app_settings.insert_one({
        "key": SETTINGS_KEY,
        "payroll_payable_account_id": payable_id,
        "component_mappings": mappings,
    })


def _payload(bank_id, counter_id):
    return {
        "date": "2026-10-06",
        "cash_bank_id": bank_id,
        "counter_account_id": counter_id,
        "amount": 250000,
        "description": "Bayar operasional",
    }


@pytest.mark.anyio
async def test_ordinary_counter_account_is_allowed():
    bank = await _bank()
    expense = await _account(f"EX{uuid.uuid4().hex[:6]}", "Beban Listrik", "EXPS")
    payable = await _account(f"UT{uuid.uuid4().hex[:6]}", "Utang Gaji", "OCLY")
    await _map(payable_id=payable["id"], expense_id=expense["id"])
    saved = await create_payment(server.db, _payload(bank["id"], expense["id"]), user=_user())
    assert saved["status"] == "draft"
    posted = await post_voucher(server.db, "payment", saved["id"], user=_user())
    assert posted["status"] == "posted"
    journal = await server.db[JOURNAL_ENTRIES].find_one({"source_id": saved["id"]})
    assert journal["source_type"] == SOURCE_PAYMENT == "cash_bank_payment"
    assert ACCRUAL_SOURCE == "payroll_period"
    assert PAYMENT_SOURCE == "payroll_payment"


@pytest.mark.anyio
async def test_payroll_payable_counter_is_rejected():
    bank = await _bank()
    payable = await _account(f"UT{uuid.uuid4().hex[:6]}", "Utang Gaji", "OCLY")
    await _map(payable_id=payable["id"])
    with pytest.raises(VoucherValidationError, match=PAYROLL_PAYABLE_BLOCK_CODE):
        await create_payment(server.db, _payload(bank["id"], payable["id"]), user=_user())


@pytest.mark.anyio
async def test_draft_update_to_payroll_payable_is_rejected():
    bank = await _bank()
    ops = await _account(f"EX{uuid.uuid4().hex[:6]}", "Beban Umum", "EXPS")
    payable = await _account(f"UT{uuid.uuid4().hex[:6]}", "Utang Gaji", "OCLY")
    saved = await create_payment(server.db, _payload(bank["id"], ops["id"]), user=_user())
    await _map(payable_id=payable["id"])
    with pytest.raises(VoucherValidationError, match=PAYROLL_PAYABLE_BLOCK_CODE):
        await update_draft(server.db, "payment", saved["id"], {"counter_account_id": payable["id"]}, user=_user())
    fresh = await server.db.cash_bank_payments.find_one({"id": saved["id"]})
    assert fresh["counter_account_id"] == ops["id"]


@pytest.mark.anyio
async def test_post_revalidates_payable_after_settings_change():
    bank = await _bank()
    later_payable = await _account(f"UT{uuid.uuid4().hex[:6]}", "Utang Gaji", "OCLY")
    saved = await create_payment(server.db, _payload(bank["id"], later_payable["id"]), user=_user())
    await _map(payable_id=later_payable["id"])
    with pytest.raises(VoucherValidationError, match=PAYROLL_PAYABLE_BLOCK_CODE):
        await post_voucher(server.db, "payment", saved["id"], user=_user())
    journal = await server.db[JOURNAL_ENTRIES].find_one({"source_id": saved["id"]})
    assert journal is None


@pytest.mark.anyio
async def test_empty_or_non_liability_payable_keeps_existing_behavior():
    bank = await _bank()
    ops = await _account(f"EX{uuid.uuid4().hex[:6]}", "Beban Umum", "EXPS")
    not_liability = await _account(f"EQ{uuid.uuid4().hex[:6]}", "Bukan Utang", "EXPS")
    saved = await create_payment(server.db, _payload(bank["id"], ops["id"]), user=_user())
    assert saved["counter_account_id"] == ops["id"]
    await _map(payable_id=not_liability["id"])
    posted = await post_voucher(server.db, "payment", saved["id"], user=_user())
    assert posted["status"] == "posted"


@pytest.mark.anyio
async def test_non_expense_mapping_is_not_a_payable_block():
    bank = await _bank()
    other = await _account(f"AP{uuid.uuid4().hex[:6]}", "Utang Usaha", "APAY")
    await _map(other_id=other["id"], other_treatment="liability_credit")
    saved = await create_payment(server.db, _payload(bank["id"], other["id"]), user=_user())
    assert saved["status"] == "draft"
