"""Accounts Payable Phase 4A — journal-based current-state read model."""
from __future__ import annotations

import os
import sys
import uuid
from decimal import Decimal
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("MONGO_URL", "mongodb://127.0.0.1:27017")
_worker = os.environ.get("PYTEST_XDIST_WORKER") or "main"
os.environ["DB_NAME"] = f"eternal_packaging_accounts_payable_test_{_worker}"
os.environ.setdefault("JWT_SECRET", "test-secret-key-12345678901234567890")
os.environ.setdefault("CORS_ORIGINS", "*")

import server
from server import app, current_user
from services.accounts_payable import (
    RECONCILIATION_SCOPE,
    WARN_MISSING_PURCHASE_INVOICE_DOCUMENT,
    WARN_MULTIPLE_INVOICE_APAY_ACCOUNTS,
    WARN_PAYMENT_AP_ACCOUNT_MISMATCH,
    _find_in,
    aging_bucket_for,
    supplier_identity_key,
)
from services.cash_bank_master import create_cash_bank, ensure_indexes as ensure_cb
from services.chart_of_accounts import create_account, ensure_indexes as ensure_coa
from services.journal_core import JOURNAL_ENTRIES, JOURNAL_LINES, ensure_indexes as ensure_journal, post_balanced_journal
from services.purchase_accounting_settings import SETTINGS_KEY
from services.purchase_payments import SETTLEMENT_PAID, SUBMISSION_SUBMITTED

pytestmark = pytest.mark.anyio

AP_SUMMARY = "/api/ledger/accounts-payable/summary"
AP_AGING = "/api/ledger/accounts-payable/aging"
AP_SUPPLIERS = "/api/ledger/accounts-payable/suppliers"
AP_STATEMENT = "/api/ledger/accounts-payable/statement"
AP_GETS = (AP_SUMMARY, AP_AGING, AP_SUPPLIERS)


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
    for coll in (
        JOURNAL_ENTRIES,
        JOURNAL_LINES,
        "purchase_invoices",
        "purchase_payments",
        "app_settings",
        "ledger_accounts",
        "cash_bank_accounts",
        "accounting_periods",
    ):
        await db[coll].delete_many({})
    yield
    for coll in (JOURNAL_ENTRIES, JOURNAL_LINES, "purchase_invoices", "purchase_payments", "accounting_periods"):
        await db[coll].delete_many({})


def _admin():
    app.dependency_overrides[current_user] = lambda: {
        "id": "admin-ap",
        "email": "admin@eternalpackaging.com",
        "name": "Admin AP",
        "role": "admin",
    }


async def _coa(code_prefix: str, name: str, account_type: str) -> dict:
    suffix = uuid.uuid4().hex[:6]
    return await create_account(
        server.db,
        {"code": f"{code_prefix}{suffix}", "name": name, "account_type": account_type},
        user={"id": "t", "name": "t", "role": "admin"},
    )


async def _configure(payable=None, clearing=None):
    payable = payable or await _coa("21", "Hutang Usaha", "APAY")
    clearing = clearing or await _coa("21", "Purchase Clearing", "OCLY")
    await server.db.app_settings.update_one(
        {"key": SETTINGS_KEY},
        {"$set": {"key": SETTINGS_KEY, "payable_account_id": payable["id"], "purchase_clearing_account_id": clearing["id"]}},
        upsert=True,
    )
    return payable, clearing


async def _cash_bank(name="BCA Operasional"):
    bank = await _coa("12", name, "BANK")
    return await create_cash_bank(server.db, {"name": name, "account_id": bank["id"], "active": True}, user={"id": "t", "name": "t", "role": "admin"})


async def _seed_invoice(**extra) -> dict:
    inv_id = extra.pop("id", str(uuid.uuid4()))
    doc = {
        "id": inv_id,
        "invoice_number": extra.pop("invoice_number", f"FPB-{uuid.uuid4().hex[:8].upper()}"),
        "invoice_date": extra.pop("invoice_date", "2026-09-01"),
        "supplier": extra.pop("supplier", "PT ABC"),
        "grand_total": extra.pop("grand_total", 10_000_000),
        "cancelled": extra.pop("cancelled", False),
    }
    doc.update(extra)
    await server.db.purchase_invoices.insert_one(doc)
    doc.pop("_id", None)
    return doc


async def _seed_payment(invoice, **extra) -> dict:
    pay_id = extra.pop("id", str(uuid.uuid4()))
    doc = {
        "id": pay_id,
        "payment_number": extra.pop("payment_number", f"PPB-{uuid.uuid4().hex[:8].upper()}"),
        "purchase_invoice_id": invoice["id"],
        "invoice_number": invoice.get("invoice_number"),
        "supplier": invoice.get("supplier"),
        "amount": extra.pop("amount", 4_000_000),
        "payment_date": extra.pop("payment_date", "2026-09-10"),
        "settlement_status": extra.pop("settlement_status", SETTLEMENT_PAID),
        "submission_status": extra.pop("submission_status", SUBMISSION_SUBMITTED),
        "cancelled": extra.pop("cancelled", False),
    }
    doc.update(extra)
    await server.db.purchase_payments.insert_one(doc)
    doc.pop("_id", None)
    return doc


async def _post_invoice(client, invoice_id):
    res = await client.post(f"/api/purchase-invoices/{invoice_id}/post-accounting")
    assert res.status_code == 200, res.text
    return res.json()["invoice"]


async def _post_payment(client, payment_id, cash_bank_id):
    res = await client.post(f"/api/purchase-payments/{payment_id}/post-accounting", json={"cash_bank_id": cash_bank_id})
    assert res.status_code == 200, res.text
    return res.json()["payment"]


@pytest.mark.anyio
async def test_pending_payment_then_posted_payment():
    payable, _ = await _configure()
    bank = await _cash_bank()
    inv = await _seed_invoice(grand_total=10_000_000, supplier="PT ABC")
    pay = await _seed_payment(inv, amount=4_000_000)
    _admin()
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            await _post_invoice(client, inv["id"])
            summary = (await client.get(AP_SUMMARY)).json()
            assert summary["total_accounting_ap"] == 10_000_000
            assert summary["total_pending_payments"] == 4_000_000
            assert summary["total_operational_outstanding"] == 6_000_000
            await _post_payment(client, pay["id"], bank["id"])
            after = (await client.get(AP_SUMMARY)).json()
            assert after["total_accounting_ap"] == 6_000_000
            assert after["total_pending_payments"] == 0
            assert after["total_operational_outstanding"] == 6_000_000
            assert after["account_breakdown"][0]["purchase_ap_balance"] == 6_000_000
            assert after["account_breakdown"][0]["account_id"] == payable["id"]
    finally:
        app.dependency_overrides.pop(current_user, None)


@pytest.mark.anyio
async def test_invoice_reversal_statement_and_zero_ap():
    await _configure()
    inv = await _seed_invoice(supplier="PT ABC")
    _admin()
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            await _post_invoice(client, inv["id"])
            rev = await client.post(f"/api/purchase-invoices/{inv['id']}/reverse-accounting", json={"reason": "koreksi"})
            assert rev.status_code == 200, rev.text
            summary = (await client.get(AP_SUMMARY)).json()
            assert summary["total_accounting_ap"] == 0
            stmt = (await client.get(AP_STATEMENT, params={
                "supplier_key": supplier_identity_key("PT ABC"),
                "start_date": "2026-09-01",
                "end_date": "2026-09-30",
            })).json()
            types = [row["source_type"] for row in stmt["items"]]
            assert "purchase_invoice" in types
            assert "purchase_invoice_reversal" in types
            invoice_row = next(r for r in stmt["items"] if r["source_type"] == "purchase_invoice")
            reversal_row = next(r for r in stmt["items"] if r["source_type"] == "purchase_invoice_reversal")
            assert invoice_row["ap_increase"] == 10_000_000
            assert reversal_row["ap_decrease"] == 10_000_000
            assert stmt["ending_balance"] == 0
            assert stmt["historical_as_of"] is False
    finally:
        app.dependency_overrides.pop(current_user, None)


@pytest.mark.anyio
async def test_wrong_ap_account_payment_does_not_reduce_invoice():
    payable_a, clearing = await _configure()
    payable_b = await _coa("21", "Hutang B", "APAY")
    bank = await _coa("12", "Bank B", "BANK")
    inv = await _seed_invoice()
    pay = await _seed_payment(inv, amount=4_000_000)
    _admin()
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            await _post_invoice(client, inv["id"])
        await post_balanced_journal(
            server.db,
            date="2026-09-10",
            source_type="purchase_payment",
            source_id=pay["id"],
            description="malformed payment",
            lines=[
                {"account_id": payable_b["id"], "debit": Decimal("4000000.00"), "credit": Decimal("0.00"), "description": "wrong AP"},
                {"account_id": bank["id"], "debit": Decimal("0.00"), "credit": Decimal("4000000.00"), "description": "bank"},
            ],
            posted_by={"id": "t", "name": "t", "role": "admin"},
        )
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            aging = (await client.get(AP_AGING)).json()
            row = next(r for r in aging["items"] if r["invoice_id"] == inv["id"])
            assert row["accounting_outstanding"] == 10_000_000
            assert WARN_PAYMENT_AP_ACCOUNT_MISMATCH in row["integrity_warnings"]
            summary = (await client.get(AP_SUMMARY)).json()
            by_id = {a["account_id"]: a for a in summary["account_breakdown"]}
            assert row["accounting_outstanding"] == 10_000_000
            assert by_id[payable_a["id"]]["purchase_ap_balance"] == 10_000_000
            assert by_id[payable_b["id"]]["purchase_ap_balance"] == 0
            assert by_id[payable_b["id"]]["gl_apay_balance"] == -4_000_000
            assert by_id[payable_b["id"]]["difference"] == -4_000_000
            assert by_id[payable_b["id"]]["difference_has_integrity_anomaly"] is True
    finally:
        app.dependency_overrides.pop(current_user, None)


@pytest.mark.anyio
async def test_multi_ap_invoice_null_account_and_recon():
    payable_a = await _coa("21", "AP A", "APAY")
    payable_b = await _coa("21", "AP B", "APAY")
    clearing = await _coa("21", "Clear", "OCLY")
    inv = await _seed_invoice()
    await post_balanced_journal(
        server.db,
        date="2026-09-01",
        source_type="purchase_invoice",
        source_id=inv["id"],
        description="split AP",
        lines=[
            {"account_id": clearing["id"], "debit": Decimal("10000000.00"), "credit": Decimal("0.00"), "description": "clear"},
            {"account_id": payable_a["id"], "debit": Decimal("0.00"), "credit": Decimal("6000000.00"), "description": "A"},
            {"account_id": payable_b["id"], "debit": Decimal("0.00"), "credit": Decimal("4000000.00"), "description": "B"},
        ],
        posted_by={"id": "t", "name": "t", "role": "admin"},
    )
    _admin()
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            aging = (await client.get(AP_AGING)).json()
            row = next(r for r in aging["items"] if r["invoice_id"] == inv["id"])
            assert row["original_ap_amount"] == 10_000_000
            assert row["actual_ap_account_id"] is None
            assert WARN_MULTIPLE_INVOICE_APAY_ACCOUNTS in row["integrity_warnings"]
            summary = (await client.get(AP_SUMMARY)).json()
            by_id = {a["account_id"]: a for a in summary["account_breakdown"]}
            assert by_id[payable_a["id"]]["purchase_ap_balance"] == 6_000_000
            assert by_id[payable_b["id"]]["purchase_ap_balance"] == 4_000_000
            assert by_id[payable_a["id"]]["gl_apay_balance"] == 6_000_000
            assert by_id[payable_b["id"]]["gl_apay_balance"] == 4_000_000
            assert by_id[payable_a["id"]]["difference"] == 0
            assert by_id[payable_b["id"]]["difference"] == 0
    finally:
        app.dependency_overrides.pop(current_user, None)


@pytest.mark.anyio
async def test_gl_residual_and_opening_balance_not_in_aging():
    payable, clearing = await _configure()
    expense = await _coa("51", "Beban", "EXPS")
    inv = await _seed_invoice()
    _admin()
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            await _post_invoice(client, inv["id"])
        await post_balanced_journal(
            server.db,
            date="2026-09-05",
            source_type="general_journal",
            source_id=str(uuid.uuid4()),
            description="manual AP",
            lines=[
                {"account_id": expense["id"], "debit": Decimal("2000000.00"), "credit": Decimal("0.00"), "description": "exp"},
                {"account_id": payable["id"], "debit": Decimal("0.00"), "credit": Decimal("2000000.00"), "description": "ap"},
            ],
            posted_by={"id": "t", "name": "t", "role": "admin"},
        )
        await post_balanced_journal(
            server.db,
            date="2026-01-01",
            source_type="opening_balance",
            source_id=str(uuid.uuid4()),
            description="OB AP",
            lines=[
                {"account_id": expense["id"], "debit": Decimal("1000000.00"), "credit": Decimal("0.00"), "description": "plug"},
                {"account_id": payable["id"], "debit": Decimal("0.00"), "credit": Decimal("1000000.00"), "description": "ob"},
            ],
            posted_by={"id": "t", "name": "t", "role": "admin"},
        )
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            summary = (await client.get(AP_SUMMARY)).json()
            row = next(a for a in summary["account_breakdown"] if a["account_id"] == payable["id"])
            assert row["purchase_ap_balance"] == 10_000_000
            assert row["gl_apay_balance"] == 13_000_000
            assert row["difference"] == 3_000_000
            aging = (await client.get(AP_AGING)).json()
            assert all(item["invoice_id"] == inv["id"] for item in aging["items"])
            assert all(item["original_ap_amount"] == 10_000_000 for item in aging["items"])
    finally:
        app.dependency_overrides.pop(current_user, None)


@pytest.mark.anyio
async def test_aging_boundaries_and_due_date_fallback():
    await _configure()
    cases = [
        ("2026-09-20", "NOT_DUE"),
        ("2026-09-19", "1_30"),
        ("2026-08-21", "1_30"),
        ("2026-08-20", "31_60"),
        ("2026-07-22", "31_60"),
        ("2026-07-21", "61_90"),
        ("2026-06-22", "61_90"),
        ("2026-06-21", "OVER_90"),
    ]
    _admin()
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            for due, bucket in cases:
                inv = await _seed_invoice(invoice_date=due, invoice_number=f"FPB-{due}")
                await _post_invoice(client, inv["id"])
            aging = (await client.get(AP_AGING, params={"aging_reference_date": "2026-09-20"})).json()
            by_inv = {r["invoice_number"]: r for r in aging["items"]}
            for due, bucket in cases:
                row = by_inv[f"FPB-{due}"]
                assert row["aging_bucket"] == bucket, (due, row["aging_bucket"], row["days_overdue"])
                assert row["due_date_fallback"] is True
                assert row["due_date"] == due
    finally:
        app.dependency_overrides.pop(current_user, None)


@pytest.mark.anyio
async def test_supplier_normalization_and_zero_write_gets():
    await _configure()
    a = await _seed_invoice(supplier="PT ABC", invoice_number="FPB-A")
    b = await _seed_invoice(supplier="  PT   ABC ", invoice_number="FPB-B")
    _admin()
    before = {
        "invoices": await server.db.purchase_invoices.count_documents({}),
        "payments": await server.db.purchase_payments.count_documents({}),
        "journals": await server.db.journal_entries.count_documents({}),
        "lines": await server.db.journal_lines.count_documents({}),
        "settings": await server.db.app_settings.count_documents({}),
        "accounts": await server.db.ledger_accounts.count_documents({}),
    }
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            await _post_invoice(client, a["id"])
            await _post_invoice(client, b["id"])
            after_post = await server.db.journal_entries.count_documents({})
            suppliers = (await client.get(AP_SUPPLIERS)).json()
            keys = {row["supplier_key"] for row in suppliers["items"]}
            assert keys == {supplier_identity_key("PT ABC")}
            grouped = next(row for row in suppliers["items"] if row["supplier_key"] == supplier_identity_key("PT ABC"))
            assert grouped["invoice_count"] == 2
            assert grouped["total_invoiced_ap"] == 20_000_000
            raw_a = await server.db.purchase_invoices.find_one({"id": a["id"]}, {"_id": 0})
            raw_b = await server.db.purchase_invoices.find_one({"id": b["id"]}, {"_id": 0})
            assert raw_a["supplier"] == "PT ABC"
            assert raw_b["supplier"] == "  PT   ABC "
            for path in AP_GETS:
                res = await client.get(path)
                assert res.status_code == 200, res.text
            stmt = await client.get(AP_STATEMENT, params={
                "supplier_key": supplier_identity_key("PT ABC"),
                "start_date": "2026-09-01",
                "end_date": "2026-09-30",
            })
            assert stmt.status_code == 200
        assert await server.db.purchase_invoices.count_documents({}) == before["invoices"]
        assert await server.db.purchase_payments.count_documents({}) == before["payments"]
        assert await server.db.journal_entries.count_documents({}) == after_post
        assert await server.db.app_settings.count_documents({}) == before["settings"]
        assert await server.db.ledger_accounts.count_documents({}) == before["accounts"]
    finally:
        app.dependency_overrides.pop(current_user, None)


@pytest.mark.anyio
async def test_missing_invoice_document_warning_and_find_in_no_cap():
    payable, clearing = await _configure()
    await post_balanced_journal(
        server.db,
        date="2026-09-01",
        source_type="purchase_invoice",
        source_id="missing-inv",
        description="orphan",
        lines=[
            {"account_id": clearing["id"], "debit": Decimal("100.00"), "credit": Decimal("0.00"), "description": "c"},
            {"account_id": payable["id"], "debit": Decimal("0.00"), "credit": Decimal("100.00"), "description": "ap"},
        ],
        posted_by={"id": "t", "name": "t", "role": "admin"},
    )
    _admin()
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            summary = (await client.get(AP_SUMMARY)).json()
            codes = {item["code"] for item in summary.get("integrity_anomalies") or []}
            assert WARN_MISSING_PURCHASE_INVOICE_DOCUMENT in codes
        rows = await _find_in(server.db, "purchase_invoices", "id", [str(uuid.uuid4()) for _ in range(5)])
        assert rows == []
    finally:
        app.dependency_overrides.pop(current_user, None)


@pytest.mark.anyio
async def test_aging_bucket_function_boundaries():
    assert aging_bucket_for("2026-09-20", "2026-09-20")[0] == "NOT_DUE"
    assert aging_bucket_for("2026-09-19", "2026-09-20") == ("1_30", 1)
    assert aging_bucket_for("2026-08-21", "2026-09-20") == ("1_30", 30)
    assert aging_bucket_for("2026-08-20", "2026-09-20") == ("31_60", 31)
    assert aging_bucket_for("2026-07-22", "2026-09-20") == ("31_60", 60)
    assert aging_bucket_for("2026-07-21", "2026-09-20") == ("61_90", 61)
    assert aging_bucket_for("2026-06-22", "2026-09-20") == ("61_90", 90)
    assert aging_bucket_for("2026-06-21", "2026-09-20") == ("OVER_90", 91)


@pytest.mark.anyio
async def test_filtered_summary_cards_do_not_change_gl_reconciliation():
    payable, _ = await _configure()
    inv_a = await _seed_invoice(supplier="PT A", grand_total=10_000_000, invoice_number="FPB-SA")
    inv_b = await _seed_invoice(supplier="PT B", grand_total=20_000_000, invoice_number="FPB-SB")
    _admin()
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            await _post_invoice(client, inv_a["id"])
            await _post_invoice(client, inv_b["id"])
            full = (await client.get(AP_SUMMARY)).json()
            assert full["total_accounting_ap"] == 30_000_000
            assert full["reconciliation_scope"] == RECONCILIATION_SCOPE
            by_id = {a["account_id"]: a for a in full["account_breakdown"]}
            assert by_id[payable["id"]]["purchase_ap_balance"] == 30_000_000
            assert by_id[payable["id"]]["gl_apay_balance"] == 30_000_000
            assert by_id[payable["id"]]["difference"] == 0
            filtered = (await client.get(AP_SUMMARY, params={
                "supplier_key": supplier_identity_key("PT A"),
            })).json()
            assert filtered["total_accounting_ap"] == 10_000_000
            assert filtered["reconciliation_scope"] == RECONCILIATION_SCOPE
            f_by = {a["account_id"]: a for a in filtered["account_breakdown"]}
            assert f_by[payable["id"]]["purchase_ap_balance"] == 30_000_000
            assert f_by[payable["id"]]["gl_apay_balance"] == 30_000_000
            assert f_by[payable["id"]]["difference"] == 0
            account_only = (await client.get(AP_SUMMARY, params={
                "supplier_key": supplier_identity_key("PT A"),
                "ap_account_id": payable["id"],
            })).json()
            assert account_only["total_accounting_ap"] == 10_000_000
            a_by = {a["account_id"]: a for a in account_only["account_breakdown"]}
            assert list(a_by) == [payable["id"]]
            assert a_by[payable["id"]]["purchase_ap_balance"] == 30_000_000
            assert a_by[payable["id"]]["gl_apay_balance"] == 30_000_000
            assert a_by[payable["id"]]["difference"] == 0
    finally:
        app.dependency_overrides.pop(current_user, None)
