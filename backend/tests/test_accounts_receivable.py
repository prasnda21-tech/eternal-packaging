"""Accounts Receivable Phase 3 — journal-based current-state read model."""
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
os.environ["DB_NAME"] = f"eternal_packaging_accounts_receivable_test_{_worker}"
os.environ.setdefault("JWT_SECRET", "test-secret-key-12345678901234567890")
os.environ.setdefault("CORS_ORIGINS", "*")
os.environ.setdefault("STORAGE_PROVIDER", "local")

import server
from permissions import ALL_PERMISSIONS, ROLE_DEFAULT_PERMISSIONS
from server import app, current_user
from services.accounts_receivable import (
    PERM_VIEW,
    ROUNDING_TOLERANCE,
    STATEMENT_SORT_KEYS,
    WARN_MISSING_SALES_INVOICE_DOCUMENT,
    WARN_MISSING_SALES_RECEIPT_DOCUMENT,
    WARN_MULTIPLE_INVOICE_AREC_ACCOUNTS,
    WARN_MULTIPLE_RECEIPT_AREC_ACCOUNTS,
    WARN_NEGATIVE_ACCOUNTING_OUTSTANDING,
    WARN_OVER_APPLIED_ACCOUNTING_RECEIPT,
    WARN_RECEIPT_AR_ACCOUNT_MISMATCH,
    _find_in,
    _load_current_invoices,
    aging_bucket_for,
    ensure_indexes,
    INDEX_SPECS,
)
from services.cash_bank_master import COLLECTION as CB_COLLECTION, create_cash_bank, ensure_indexes as ensure_cb
from services.chart_of_accounts import COLLECTION as COA_COLLECTION, create_account, ensure_indexes as ensure_coa
from services.general_journal import COLLECTION as GJ_COLLECTION, ensure_indexes as ensure_gj
from services.journal_core import JOURNAL_ENTRIES, JOURNAL_LINES, ensure_indexes as ensure_journal
from services.opening_balance import COLLECTION as OB_COLLECTION, ensure_indexes as ensure_ob
from services.sales_accounting_settings import SETTINGS_KEY
from services.sales_customers import COLLECTION as SALES_CUSTOMERS

pytestmark = pytest.mark.anyio

AR_SUMMARY = "/api/ledger/accounts-receivable/summary"
AR_AGING = "/api/ledger/accounts-receivable/aging"
AR_CUSTOMERS = "/api/ledger/accounts-receivable/customers"
AR_STATEMENT = "/api/ledger/accounts-receivable/statement"
AR_GETS = (AR_SUMMARY, AR_AGING, AR_CUSTOMERS, AR_STATEMENT)


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
    await ensure_gj(db)
    await ensure_ob(db)
    await ensure_indexes(db)
    colls = (
        CB_COLLECTION,
        COA_COLLECTION,
        JOURNAL_ENTRIES,
        JOURNAL_LINES,
        GJ_COLLECTION,
        OB_COLLECTION,
        "sales_invoices",
        "sales_receipts",
        "orders",
        SALES_CUSTOMERS,
        "app_settings",
    )
    for coll in colls:
        await db[coll].delete_many({})
    yield
    for coll in colls:
        await db[coll].delete_many({})


def _admin():
    app.dependency_overrides[current_user] = lambda: {
        "id": "admin-ar",
        "email": "admin@eternalpackaging.com",
        "name": "Admin AR",
        "role": "admin",
    }


def _user(role="admin", perms=None, **kw):
    uid = kw.get("id") or f"u-{role}-{uuid.uuid4().hex[:8]}"
    override = kw.get("permissions_override")
    if override is None:
        if role == "admin":
            override = {}
        elif perms is not None:
            override = {p: True for p in perms}
        else:
            override = {}
    return {
        "id": uid,
        "email": kw.get("email") or f"{uid}@example.com",
        "name": kw.get("name") or role.title(),
        "role": role if role != "viewer" else "operator",
        "status": "active",
        "user_id": uid,
        "permissions_override": override,
    }


async def client_for(user):
    app.dependency_overrides[current_user] = lambda: user
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


async def _coa(code_prefix: str, name: str, account_type: str) -> dict:
    suffix = uuid.uuid4().hex[:6]
    return await create_account(
        server.db,
        {"code": f"{code_prefix}{suffix}", "name": name, "account_type": account_type},
        user={"id": "t", "name": "t", "role": "admin"},
    )


async def _configure_settings(rec=None, rev=None, tax=None):
    rec = rec or await _coa("11", "Piutang Usaha", "AREC")
    rev = rev or await _coa("41", "Pendapatan", "REVE")
    tax = tax or await _coa("21", "PPN Keluaran", "OCLY")
    await server.db.app_settings.update_one(
        {"key": SETTINGS_KEY},
        {
            "$set": {
                "key": SETTINGS_KEY,
                "receivable_account_id": rec["id"],
                "revenue_account_id": rev["id"],
                "output_tax_account_id": tax["id"],
            }
        },
        upsert=True,
    )
    return rec, rev, tax


async def _cash_bank(name: str = "BCA Operasional") -> dict:
    bank_coa = await _coa("12", name, "BANK")
    return await create_cash_bank(
        server.db,
        {"name": name, "account_id": bank_coa["id"], "active": True},
        user={"id": "admin-ar", "email": "admin@eternalpackaging.com", "name": "Admin AR", "role": "admin"},
    )


async def _seed_po(**extra) -> dict:
    po_id = str(uuid.uuid4())
    po_number = f"PO/AR/{uuid.uuid4().hex[:8].upper()}"
    doc = {
        "id": po_id,
        "po_number": po_number,
        "customer": extra.pop("customer", "PT AR"),
        "outlet": extra.pop("outlet", "Outlet AR"),
        "recipient": "Budi",
        "address": "Jl. Test",
        "delivery_zone": "Jakarta",
        "sales": extra.pop("sales", "Vira"),
        "line_items": [{"line_idx": 0, "line_id": "l0", "name": "Cup AR", "quantity": 100, "unit": "pcs"}],
        "total_quantity": 100,
        "current_stage": extra.pop("current_stage", 5),
        "status_label": "Penjadwalan",
        "payment_status": extra.pop("payment_status", "Waiting"),
        "packed_qty": extra.pop("packed_qty", 40),
        "loaded_qty": extra.pop("loaded_qty", 10),
        "events": [{"stage": 0, "name": "Order Masuk"}],
        "shipments": extra.pop("shipments", [{"id": "shp-1", "status": "draft"}]),
        "created_at": "2026-09-01T00:00:00Z",
    }
    if "customer_id" in extra:
        doc["customer_id"] = extra.pop("customer_id")
    doc.update(extra)
    await server.db.orders.insert_one(doc)
    doc.pop("_id", None)
    return doc


async def _create_invoice(client, po_id, **extra) -> dict:
    payload = {
        "po_id": po_id,
        "invoice_date": extra.pop("invoice_date", "2026-09-01"),
        "discount_amount": extra.pop("discount_amount", 0),
        "ppn_percent": extra.pop("ppn_percent", 0),
        "lines": [{"line_idx": 0, "quantity": extra.pop("quantity", 1), "unit_price": extra.pop("unit_price", 10_000_000)}],
    }
    if "payment_term_days" in extra:
        payload["payment_term_days"] = extra.pop("payment_term_days")
    payload.update(extra)
    res = await client.post("/api/sales-invoices", json=payload)
    assert res.status_code == 200, res.text
    return res.json()["invoice"]


async def _finalize(client, invoice_id: str) -> dict:
    res = await client.post(f"/api/sales-invoices/{invoice_id}/finalize")
    assert res.status_code == 200, res.text
    return res.json()["invoice"]


async def _post_invoice(client, invoice_id: str) -> dict:
    res = await client.post(f"/api/sales-invoices/{invoice_id}/post-accounting")
    assert res.status_code == 200, res.text
    return res.json()["invoice"]


async def _posted_invoice(client, po_id, **extra) -> dict:
    inv = await _create_invoice(client, po_id, **extra)
    await _finalize(client, inv["id"])
    return await _post_invoice(client, inv["id"])


async def _create_receipt(client, invoice_id: str, amount, **extra) -> dict:
    payload = {
        "invoice_id": invoice_id,
        "receipt_date": extra.pop("receipt_date", "2026-09-05"),
        "amount": amount,
        "notes": extra.pop("notes", "DP"),
    }
    if "cash_bank_id" in extra:
        payload["cash_bank_id"] = extra.pop("cash_bank_id")
    payload.update(extra)
    res = await client.post("/api/sales-receipts", json=payload)
    assert res.status_code == 200, res.text
    return res.json()["receipt"]


async def _post_receipt(client, receipt_id: str) -> dict:
    res = await client.post(f"/api/sales-receipts/{receipt_id}/post-accounting")
    assert res.status_code == 200, res.text
    return res.json()["receipt"]


async def _reverse_receipt(client, receipt_id: str) -> dict:
    res = await client.post(f"/api/sales-receipts/{receipt_id}/reverse-accounting")
    assert res.status_code == 200, res.text
    return res.json()["receipt"]


async def _reverse_invoice(client, invoice_id: str) -> dict:
    res = await client.post(
        f"/api/sales-invoices/{invoice_id}/reverse-accounting",
        json={"reason": "Koreksi faktur salah"},
    )
    assert res.status_code == 200, res.text
    return res.json()["invoice"]


async def _post_gj(client, a_id, b_id, amount=2_000_000, date="2026-09-10", desc="JU residual"):
    d = await client.post("/api/ledger/general-journals", json={
        "date": date,
        "description": desc,
        "lines": [
            {"account_id": a_id, "debit": amount, "credit": 0, "description": "Dr GJ"},
            {"account_id": b_id, "debit": 0, "credit": amount, "description": "Cr GJ"},
        ],
    })
    assert d.status_code == 201, d.text
    p = await client.post(f"/api/ledger/general-journals/{d.json()['id']}/post")
    assert p.status_code == 200, p.text
    return p.json()


async def _post_ob(client, a_id, b_id, amount=3_000_000, cutoff="2026-08-31", desc="SA residual"):
    d = await client.post("/api/ledger/opening-balances", json={
        "cutoff_date": cutoff,
        "description": desc,
        "lines": [
            {"account_id": a_id, "debit": amount, "credit": 0, "description": "Dr SA"},
            {"account_id": b_id, "debit": 0, "credit": amount, "description": "Cr SA"},
        ],
    })
    assert d.status_code == 201, d.text
    p = await client.post(f"/api/ledger/opening-balances/{d.json()['id']}/post")
    assert p.status_code == 200, p.text
    return p.json()


def _money(raw) -> Decimal:
    return Decimal(str(raw or 0)).quantize(Decimal("0.01"))


def _aging_row(body, invoice_id):
    return next((r for r in body.get("items") or [] if r.get("invoice_id") == invoice_id), None)


async def _primary_journal(source_type: str, source_id: str) -> dict:
    doc = await server.db[JOURNAL_ENTRIES].find_one(
        {"source_type": source_type, "source_id": source_id, "reversal_of": None},
        {"_id": 0},
    )
    assert doc, f"missing primary journal {source_type} {source_id}"
    return doc


async def _arec_credit_line(journal_id: str) -> dict:
    doc = await server.db[JOURNAL_LINES].find_one(
        {"journal_id": journal_id, "credit": {"$gt": 0}},
        {"_id": 0},
    )
    assert doc, f"missing credit line {journal_id}"
    return doc


async def _insert_journal_line(journal_id: str, account_id: str, *, debit=0, credit=0, desc="anomaly"):
    await server.db[JOURNAL_LINES].insert_one({
        "id": str(uuid.uuid4()),
        "journal_id": journal_id,
        "account_id": account_id,
        "debit": debit,
        "credit": credit,
        "description": desc,
        "created_at": "2026-09-01T00:00:00+00:00",
    })


async def _snapshot():
    db = server.db
    async def _docs(name):
        rows = await db[name].find({}, {"_id": 0}).to_list(10000)
        rows.sort(key=lambda r: str(r.get("id") or r.get("key") or ""))
        return rows
    return {
        "sales_invoices": await _docs("sales_invoices"),
        "sales_receipts": await _docs("sales_receipts"),
        "orders": await _docs("orders"),
        "journal_entries": await _docs(JOURNAL_ENTRIES),
        "journal_lines": await _docs(JOURNAL_LINES),
        "ledger_accounts": await _docs(COA_COLLECTION),
        "settings": await db.app_settings.find({"key": SETTINGS_KEY}, {"_id": 0}).to_list(10),
    }


@pytest.mark.anyio
async def test_permissions_not_auto_granted():
    assert PERM_VIEW in ALL_PERMISSIONS
    assert "nav.ledger.accounts_receivable" in ALL_PERMISSIONS
    assert "accounts_receivable.export" not in ALL_PERMISSIONS
    for role, perms in ROLE_DEFAULT_PERMISSIONS.items():
        if role == "admin":
            continue
        assert PERM_VIEW not in perms
        assert "nav.ledger.accounts_receivable" not in perms


@pytest.mark.anyio
async def test_aging_bucket_boundaries():
    ref = "2026-09-20"
    assert aging_bucket_for("2026-09-20", ref) == ("NOT_DUE", 0)
    assert aging_bucket_for("2026-09-21", ref) == ("NOT_DUE", 0)
    assert aging_bucket_for("2026-09-19", ref) == ("1_30", 1)
    assert aging_bucket_for("2026-08-21", ref) == ("1_30", 30)
    assert aging_bucket_for("2026-08-20", ref) == ("31_60", 31)
    assert aging_bucket_for("2026-07-22", ref) == ("31_60", 60)
    assert aging_bucket_for("2026-07-21", ref) == ("61_90", 61)
    assert aging_bucket_for("2026-06-22", ref) == ("61_90", 90)
    assert aging_bucket_for("2026-06-21", ref) == ("OVER_90", 91)


@pytest.mark.anyio
async def test_posted_invoice_no_receipt():
    rec, _rev, _tax = await _configure_settings()
    po = await _seed_po()
    _admin()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        inv = await _posted_invoice(client, po["id"], payment_term_days=29)
        aging = (await client.get(AR_AGING, params={"aging_reference_date": "2026-09-20"})).json()
        row = _aging_row(aging, inv["id"])
        assert row is not None
        assert _money(row["original_ar_amount"]) == Decimal("10000000.00")
        assert _money(row["posted_receipt_amount"]) == Decimal("0.00")
        assert _money(row["accounting_outstanding"]) == Decimal("10000000.00")
        assert row["actual_ar_account_id"] == rec["id"]
        assert row["aging_bucket"] == "NOT_DUE"


@pytest.mark.anyio
async def test_posted_receipt_reduces_accounting_ar():
    await _configure_settings()
    bca = await _cash_bank()
    po = await _seed_po()
    _admin()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        inv = await _posted_invoice(client, po["id"])
        receipt = await _create_receipt(client, inv["id"], 4_000_000, cash_bank_id=bca["id"])
        await _post_receipt(client, receipt["id"])
        aging = (await client.get(AR_AGING)).json()
        row = _aging_row(aging, inv["id"])
        assert _money(row["accounting_outstanding"]) == Decimal("6000000.00")
        assert _money(row["posted_receipt_amount"]) == Decimal("4000000.00")
        assert _money(row["pending_receipt_amount"]) == Decimal("0.00")


@pytest.mark.anyio
async def test_unposted_receipt_is_pending_only():
    await _configure_settings()
    bca = await _cash_bank()
    po = await _seed_po()
    _admin()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        inv = await _posted_invoice(client, po["id"])
        await _create_receipt(client, inv["id"], 4_000_000, cash_bank_id=bca["id"])
        aging = (await client.get(AR_AGING)).json()
        row = _aging_row(aging, inv["id"])
        assert _money(row["accounting_outstanding"]) == Decimal("10000000.00")
        assert _money(row["pending_receipt_amount"]) == Decimal("4000000.00")
        assert _money(row["operational_outstanding"]) == Decimal("6000000.00")
        assert _money(row["posted_receipt_amount"]) == Decimal("0.00")


@pytest.mark.anyio
async def test_receipt_reversal_restores_ar():
    await _configure_settings()
    bca = await _cash_bank()
    po = await _seed_po()
    _admin()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        inv = await _posted_invoice(client, po["id"])
        receipt = await _create_receipt(client, inv["id"], 4_000_000, cash_bank_id=bca["id"])
        await _post_receipt(client, receipt["id"])
        await _reverse_receipt(client, receipt["id"])
        aging = (await client.get(AR_AGING)).json()
        row = _aging_row(aging, inv["id"])
        assert _money(row["accounting_outstanding"]) == Decimal("10000000.00")
        assert _money(row["posted_receipt_amount"]) == Decimal("0.00")


@pytest.mark.anyio
async def test_invoice_reversal_removes_current_ar():
    await _configure_settings()
    po = await _seed_po()
    _admin()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        inv = await _posted_invoice(client, po["id"])
        await _reverse_invoice(client, inv["id"])
        aging = (await client.get(AR_AGING)).json()
        assert _aging_row(aging, inv["id"]) is None
        summary = (await client.get(AR_SUMMARY)).json()
        assert _money(summary["total_accounting_ar"]) == Decimal("0.00")


@pytest.mark.anyio
async def test_old_ar_account_survives_settings_change():
    rec_a, rev, tax = await _configure_settings()
    po = await _seed_po()
    _admin()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        inv = await _posted_invoice(client, po["id"])
        rec_b = await _coa("11", "Piutang Baru", "AREC")
        await _configure_settings(rec=rec_b, rev=rev, tax=tax)
        bca = await _cash_bank()
        receipt = await _create_receipt(client, inv["id"], 4_000_000, cash_bank_id=bca["id"])
        await _post_receipt(client, receipt["id"])
        aging = (await client.get(AR_AGING)).json()
        row = _aging_row(aging, inv["id"])
        assert row["actual_ar_account_id"] == rec_a["id"]
        assert row["actual_ar_account_id"] != rec_b["id"]
        assert _money(row["accounting_outstanding"]) == Decimal("6000000.00")


@pytest.mark.anyio
async def test_multiple_ar_accounts_breakdown():
    rec_a, rev, tax = await _configure_settings()
    po_a = await _seed_po(customer="Cust A")
    _admin()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        await _posted_invoice(client, po_a["id"], unit_price=10_000_000)
        rec_b = await _coa("11", "Piutang Cabang", "AREC")
        await _configure_settings(rec=rec_b, rev=rev, tax=tax)
        po_b = await _seed_po(customer="Cust B")
        await _posted_invoice(client, po_b["id"], unit_price=5_000_000)
        summary = (await client.get(AR_SUMMARY)).json()
        assert _money(summary["total_accounting_ar"]) == Decimal("15000000.00")
        by_id = {row["account_id"]: row for row in summary["account_breakdown"]}
        assert _money(by_id[rec_a["id"]]["sales_ar_balance"]) == Decimal("10000000.00")
        assert _money(by_id[rec_b["id"]]["sales_ar_balance"]) == Decimal("5000000.00")


@pytest.mark.anyio
async def test_gl_residual_from_general_journal():
    rec, _rev, _tax = await _configure_settings()
    equity = await _coa("31", "Modal", "EQTY")
    po = await _seed_po()
    _admin()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        await _posted_invoice(client, po["id"])
        await _post_gj(client, rec["id"], equity["id"], 2_000_000)
        summary = (await client.get(AR_SUMMARY)).json()
        row = next(r for r in summary["account_breakdown"] if r["account_id"] == rec["id"])
        assert _money(row["sales_ar_balance"]) == Decimal("10000000.00")
        assert _money(row["gl_arec_balance"]) == Decimal("12000000.00")
        assert _money(row["difference"]) == Decimal("2000000.00")
        assert row["difference_label"] == "Selisih non-Faktur / jurnal lain"


@pytest.mark.anyio
async def test_opening_balance_residual_not_in_aging():
    rec, _rev, _tax = await _configure_settings()
    equity = await _coa("31", "Modal OB", "EQTY")
    po = await _seed_po()
    _admin()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        inv = await _posted_invoice(client, po["id"])
        await _post_ob(client, rec["id"], equity["id"], 3_000_000)
        aging = (await client.get(AR_AGING)).json()
        row = _aging_row(aging, inv["id"])
        assert _money(row["accounting_outstanding"]) == Decimal("10000000.00")
        summary = (await client.get(AR_SUMMARY)).json()
        br = next(r for r in summary["account_breakdown"] if r["account_id"] == rec["id"])
        assert _money(br["gl_arec_balance"]) == Decimal("13000000.00")
        assert _money(br["difference"]) == Decimal("3000000.00")


@pytest.mark.anyio
async def test_due_date_fallback_and_aging_api():
    await _configure_settings()
    po = await _seed_po()
    _admin()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        inv = await _create_invoice(client, po["id"], invoice_date="2026-08-21")
        await server.db.sales_invoices.update_one({"id": inv["id"]}, {"$unset": {"due_date": ""}})
        await _finalize(client, inv["id"])
        await _post_invoice(client, inv["id"])
        aging = (await client.get(AR_AGING, params={"aging_reference_date": "2026-09-20"})).json()
        row = _aging_row(aging, inv["id"])
        assert row["due_date_fallback"] is True
        assert row["due_date"] == "2026-08-21"
        assert row["aging_bucket"] == "1_30"
        assert row["days_overdue"] == 30


@pytest.mark.anyio
async def test_customer_stable_id():
    await _configure_settings()
    po = await _seed_po(customer="Kedai Stabil", customer_id="CUST-STABLE-1", outlet="Yogyakarta")
    _admin()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        inv = await _posted_invoice(client, po["id"])
        aging = (await client.get(AR_AGING)).json()
        row = _aging_row(aging, inv["id"])
        assert row["customer_key"] == "id:CUST-STABLE-1"
        assert row["customer_id"] == "CUST-STABLE-1"
        assert row["customer_identity_kind"] == "stable_id"
        assert row["outlet"] == "Yogyakarta"
        assert "|" not in row["customer_key"]


@pytest.mark.anyio
async def test_customer_name_fallback_does_not_use_outlet():
    await _configure_settings()
    await server.db[SALES_CUSTOMERS].insert_one({"id": "uuid-kedai", "name": "KEDAI TUMAN", "normalized_name": "kedai tuman"})
    po_a = await _seed_po(customer="KEDAI TUMAN", outlet="Yogyakarta")
    po_b = await _seed_po(customer="KEDAI TUMAN", outlet="Solo")
    _admin()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        inv_a = await _posted_invoice(client, po_a["id"], unit_price=4_000_000)
        inv_b = await _posted_invoice(client, po_b["id"], unit_price=6_000_000)
        aging = (await client.get(AR_AGING)).json()
        a = _aging_row(aging, inv_a["id"])
        b = _aging_row(aging, inv_b["id"])
        assert a["customer_key"] == "id:uuid-kedai"
        assert b["customer_key"] == "id:uuid-kedai"
        assert a["customer_identity_kind"] == "name_fallback"
        assert a["outlet"] == "Yogyakarta"
        assert b["outlet"] == "Solo"
        customers = (await client.get(AR_CUSTOMERS)).json()["items"]
        assert len(customers) == 1
        assert customers[0]["customer_key"] == "id:uuid-kedai"
        assert _money(customers[0]["accounting_outstanding"]) == Decimal("10000000.00")


@pytest.mark.anyio
async def test_ambiguous_customer_is_preserved():
    await _configure_settings()
    po_a = await _seed_po(customer="TOKO MAKMUR", customer_id="CUSTOMER-A", outlet="A")
    po_b = await _seed_po(customer="TOKO MAKMUR", customer_id="CUSTOMER-B", outlet="B")
    po_h = await _seed_po(customer="TOKO MAKMUR", outlet="Hist")
    _admin()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        await _posted_invoice(client, po_a["id"], unit_price=1_000_000)
        await _posted_invoice(client, po_b["id"], unit_price=2_000_000)
        inv_h = await _posted_invoice(client, po_h["id"], unit_price=3_000_000)
        aging = (await client.get(AR_AGING)).json()
        hist = _aging_row(aging, inv_h["id"])
        a = next(r for r in aging["items"] if r["invoice_id"] != inv_h["id"] and r["customer_id"] == "CUSTOMER-A")
        b = next(r for r in aging["items"] if r["customer_id"] == "CUSTOMER-B")
        assert hist["customer_key"] == "ambiguous:toko makmur"
        assert hist["customer_identity_ambiguous"] is True
        assert hist["customer_identity_kind"] == "ambiguous_name"
        assert a["customer_key"] == "id:CUSTOMER-A"
        assert b["customer_key"] == "id:CUSTOMER-B"
        customers = {row["customer_key"]: row for row in (await client.get(AR_CUSTOMERS)).json()["items"]}
        assert set(customers) == {"id:CUSTOMER-A", "id:CUSTOMER-B", "ambiguous:toko makmur"}
        assert _money(customers["id:CUSTOMER-A"]["accounting_outstanding"]) == Decimal("1000000.00")
        assert _money(customers["id:CUSTOMER-B"]["accounting_outstanding"]) == Decimal("2000000.00")
        assert _money(customers["ambiguous:toko makmur"]["accounting_outstanding"]) == Decimal("3000000.00")


@pytest.mark.anyio
async def test_statement_running_balance_and_labels():
    await _configure_settings()
    bca = await _cash_bank()
    po = await _seed_po(customer="PT Statement", customer_id="CUST-STMT")
    _admin()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        inv = await _posted_invoice(client, po["id"], invoice_date="2026-09-01")
        receipt = await _create_receipt(client, inv["id"], 4_000_000, cash_bank_id=bca["id"], receipt_date="2026-09-05")
        await _post_receipt(client, receipt["id"])
        await _reverse_receipt(client, receipt["id"])
        await _reverse_invoice(client, inv["id"])
        st = (await client.get(AR_STATEMENT, params={
            "customer_key": "id:CUST-STMT",
            "start_date": "2026-09-01",
            "end_date": "2026-09-30",
        })).json()
        by_type = {row["source_type"]: row for row in st["items"]}
        assert set(by_type) == {
            "sales_invoice",
            "sales_receipt",
            "sales_receipt_reversal",
            "sales_invoice_reversal",
        }
        assert by_type["sales_invoice"]["source_label"] == "Faktur Penjualan"
        assert by_type["sales_receipt"]["source_label"] == "Penerimaan Penjualan"
        assert by_type["sales_receipt_reversal"]["source_label"] == "Reversal Penerimaan Penjualan"
        assert by_type["sales_invoice_reversal"]["source_label"] == "Reversal Faktur Penjualan"
        assert _money(by_type["sales_invoice"]["debit"]) == Decimal("10000000.00")
        assert _money(by_type["sales_receipt"]["credit"]) == Decimal("4000000.00")
        assert _money(by_type["sales_receipt_reversal"]["debit"]) == Decimal("4000000.00")
        assert _money(by_type["sales_invoice_reversal"]["credit"]) == Decimal("10000000.00")
        assert _money(st["items"][-1]["running_balance"]) == Decimal("0.00")
        assert _money(st["ending_balance"]) == Decimal("0.00")
        assert "tanggal jurnal" in st["disclaimer"].lower()


@pytest.mark.anyio
async def test_statement_opening_balance():
    await _configure_settings()
    bca = await _cash_bank()
    po = await _seed_po(customer="PT Open", customer_id="CUST-OPEN")
    _admin()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        inv = await _posted_invoice(client, po["id"], invoice_date="2026-08-15")
        receipt = await _create_receipt(client, inv["id"], 4_000_000, cash_bank_id=bca["id"], receipt_date="2026-09-05")
        await _post_receipt(client, receipt["id"])
        st = (await client.get(AR_STATEMENT, params={
            "customer_key": "id:CUST-OPEN",
            "start_date": "2026-09-01",
            "end_date": "2026-09-30",
        })).json()
        assert _money(st["opening_balance"]) == Decimal("10000000.00")
        assert len(st["items"]) == 1
        assert st["items"][0]["source_type"] == "sales_receipt"
        assert _money(st["period_credit"]) == Decimal("4000000.00")
        assert _money(st["ending_balance"]) == Decimal("6000000.00")


@pytest.mark.anyio
async def test_pending_receipt_excluded_from_statement_running():
    await _configure_settings()
    bca = await _cash_bank()
    po = await _seed_po(customer="PT Pending", customer_id="CUST-PEND")
    _admin()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        inv = await _posted_invoice(client, po["id"])
        rec = await _create_receipt(client, inv["id"], 4_000_000, cash_bank_id=bca["id"])
        st = (await client.get(AR_STATEMENT, params={
            "customer_key": "id:CUST-PEND",
            "start_date": "2026-09-01",
            "end_date": "2026-09-30",
        })).json()
        assert all(row["source_type"] != "sales_receipt" or row.get("status") == "posted" for row in st["items"])
        assert _money(st["ending_balance"]) == Decimal("10000000.00")
        assert len(st["pending_receipts"]) == 1
        assert st["pending_receipts"][0]["receipt_id"] == rec["id"]
        assert _money(st["pending_receipts"][0]["amount"]) == Decimal("4000000.00")


@pytest.mark.anyio
async def test_zero_write_on_all_gets():
    await _configure_settings()
    bca = await _cash_bank()
    po = await _seed_po(customer="PT Zero", customer_id="CUST-ZERO")
    _admin()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        inv = await _posted_invoice(client, po["id"])
        await _create_receipt(client, inv["id"], 1_000_000, cash_bank_id=bca["id"])
        before = await _snapshot()
        assert (await client.get(AR_SUMMARY)).status_code == 200
        assert (await client.get(AR_AGING)).status_code == 200
        assert (await client.get(AR_CUSTOMERS)).status_code == 200
        assert (await client.get(AR_STATEMENT, params={
            "customer_key": "id:CUST-ZERO",
            "start_date": "2026-09-01",
            "end_date": "2026-09-30",
        })).status_code == 200
        after = await _snapshot()
        assert before == after


@pytest.mark.anyio
async def test_rbac_view_required():
    await _configure_settings()
    denied = _user(role="crm")
    allowed = _user(role="operator", perms=["accounts_receivable.view"])
    admin = _user(role="admin")
    async with await client_for(denied) as client:
        r = await client.get(AR_SUMMARY)
        assert r.status_code == 403
    async with await client_for(allowed) as client:
        r = await client.get(AR_SUMMARY)
        assert r.status_code == 200
    async with await client_for(admin) as client:
        r = await client.get(AR_AGING)
        assert r.status_code == 200
    assert PERM_VIEW not in ROLE_DEFAULT_PERMISSIONS["crm"]
    assert PERM_VIEW not in ROLE_DEFAULT_PERMISSIONS["sales_eternal"]


@pytest.mark.anyio
async def test_indexes_are_additive():
    names = await ensure_indexes(server.db)
    assert names
    for coll, field in INDEX_SPECS:
        info = await server.db[coll].index_information()
        assert "_id_" in info
        found = False
        for idx in info.values():
            key = idx.get("key") or []
            if list(key) == [(field, 1)]:
                found = True
                break
        assert found, f"missing additive index {coll}.{field}: {info}"


@pytest.mark.anyio
async def test_statement_requires_customer_and_dates():
    _admin()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        missing = await client.get(AR_STATEMENT, params={"start_date": "2026-09-01", "end_date": "2026-09-30"})
        assert missing.status_code == 400
        bad = await client.get(AR_STATEMENT, params={
            "customer_key": "id:x",
            "start_date": "2026-09-30",
            "end_date": "2026-09-01",
        })
        assert bad.status_code == 400


@pytest.mark.anyio
async def test_wrong_receipt_ar_account_does_not_reduce_invoice():
    rec_a, rev, tax = await _configure_settings()
    rec_b = await _coa("11", "Piutang Salah", "AREC")
    bca = await _cash_bank()
    po = await _seed_po(customer="PT Wrong AR", customer_id="CUST-WRONG-AR")
    _admin()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        inv = await _posted_invoice(client, po["id"])
        receipt = await _create_receipt(client, inv["id"], 4_000_000, cash_bank_id=bca["id"])
        await _post_receipt(client, receipt["id"])
        journal = await _primary_journal("sales_receipt", receipt["id"])
        credit = await _arec_credit_line(journal["id"])
        await server.db[JOURNAL_LINES].update_one({"id": credit["id"]}, {"$set": {"account_id": rec_b["id"]}})

        aging = (await client.get(AR_AGING)).json()
        row = _aging_row(aging, inv["id"])
        assert _money(row["accounting_outstanding"]) == Decimal("10000000.00")
        assert _money(row["posted_receipt_amount"]) == Decimal("0.00")
        assert row["actual_ar_account_id"] == rec_a["id"]
        assert row["integrity_warning"] is True
        assert WARN_RECEIPT_AR_ACCOUNT_MISMATCH in row["integrity_warnings"]

        summary = (await client.get(AR_SUMMARY)).json()
        by_id = {r["account_id"]: r for r in summary["account_breakdown"]}
        assert _money(by_id[rec_a["id"]]["sales_ar_balance"]) == Decimal("10000000.00")
        assert _money(by_id[rec_b["id"]]["sales_ar_balance"]) == Decimal("0.00")
        assert _money(by_id[rec_b["id"]]["gl_arec_balance"]) == Decimal("-4000000.00")
        assert _money(by_id[rec_b["id"]]["difference"]) == Decimal("-4000000.00")


@pytest.mark.anyio
async def test_receipt_journal_amount_is_accounting_authority():
    rec_a, _rev, _tax = await _configure_settings()
    bca = await _cash_bank()
    po = await _seed_po()
    _admin()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        inv = await _posted_invoice(client, po["id"])
        receipt = await _create_receipt(client, inv["id"], 4_000_000, cash_bank_id=bca["id"])
        await _post_receipt(client, receipt["id"])
        journal = await _primary_journal("sales_receipt", receipt["id"])
        credit = await _arec_credit_line(journal["id"])
        await server.db[JOURNAL_LINES].update_one({"id": credit["id"]}, {"$set": {"credit": 3_900_000}})

        aging = (await client.get(AR_AGING)).json()
        row = _aging_row(aging, inv["id"])
        assert _money(row["posted_receipt_amount"]) == Decimal("3900000.00")
        assert _money(row["accounting_outstanding"]) == Decimal("6100000.00")
        assert _money(row["operational_outstanding"]) == Decimal("6000000.00")
        assert _money(row["pending_receipt_amount"]) == Decimal("0.00")
        assert row["actual_ar_account_id"] == rec_a["id"]


@pytest.mark.anyio
async def test_negative_accounting_ar_warning():
    await _configure_settings()
    bca = await _cash_bank()
    po = await _seed_po()
    _admin()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        inv = await _posted_invoice(client, po["id"])
        receipt = await _create_receipt(client, inv["id"], 4_000_000, cash_bank_id=bca["id"])
        await _post_receipt(client, receipt["id"])
        journal = await _primary_journal("sales_receipt", receipt["id"])
        credit = await _arec_credit_line(journal["id"])
        await server.db[JOURNAL_LINES].update_one({"id": credit["id"]}, {"$set": {"credit": 11_000_000}})

        aging = (await client.get(AR_AGING)).json()
        row = _aging_row(aging, inv["id"])
        assert row is not None
        assert _money(row["accounting_outstanding"]) == Decimal("-1000000.00")
        assert _money(row["accounting_outstanding_raw"]) == Decimal("-1000000.00")
        assert _money(row["posted_receipt_amount"]) == Decimal("11000000.00")
        assert row["integrity_warning"] is True
        assert WARN_NEGATIVE_ACCOUNTING_OUTSTANDING in row["integrity_warnings"]
        assert WARN_OVER_APPLIED_ACCOUNTING_RECEIPT in row["integrity_warnings"]


@pytest.mark.anyio
async def test_tiny_rounding_is_not_major_anomaly():
    await _configure_settings()
    bca = await _cash_bank()
    po = await _seed_po()
    _admin()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        inv = await _posted_invoice(client, po["id"])
        receipt = await _create_receipt(client, inv["id"], 4_000_000, cash_bank_id=bca["id"])
        await _post_receipt(client, receipt["id"])
        journal = await _primary_journal("sales_receipt", receipt["id"])
        credit = await _arec_credit_line(journal["id"])
        await server.db[JOURNAL_LINES].update_one({"id": credit["id"]}, {"$set": {"credit": "10000000.01"}})

        rows = await _load_current_invoices(server.db, aging_reference_date="2026-09-20")
        row = next(r for r in rows if r["invoice_id"] == inv["id"])
        assert ROUNDING_TOLERANCE == Decimal("0.01")
        assert _money(row["accounting_outstanding_raw"]) == Decimal("-0.01")
        assert _money(row["accounting_outstanding"]) == Decimal("0.00")
        assert WARN_NEGATIVE_ACCOUNTING_OUTSTANDING not in (row.get("integrity_warnings") or [])
        assert WARN_OVER_APPLIED_ACCOUNTING_RECEIPT not in (row.get("integrity_warnings") or [])
        summary = (await client.get(AR_SUMMARY)).json()
        assert _money(Decimal(str(summary["rounding_tolerance"]))) == Decimal("0.01")


@pytest.mark.anyio
async def test_multiple_invoice_arec_lines():
    rec_a, _rev, _tax = await _configure_settings()
    rec_b = await _coa("11", "Piutang Cabang 2", "AREC")
    po_same = await _seed_po(customer="PT Split Same")
    po_multi = await _seed_po(customer="PT Split Multi")
    _admin()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        inv_same = await _posted_invoice(client, po_same["id"])
        inv_multi = await _posted_invoice(client, po_multi["id"])
        same_j = await _primary_journal("sales_invoice", inv_same["id"])
        multi_j = await _primary_journal("sales_invoice", inv_multi["id"])
        await _insert_journal_line(same_j["id"], rec_a["id"], debit=1_000_000, desc="same-account split")
        await _insert_journal_line(multi_j["id"], rec_b["id"], debit=2_000_000, desc="other AREC debit")

        aging = (await client.get(AR_AGING)).json()
        same = _aging_row(aging, inv_same["id"])
        multi = _aging_row(aging, inv_multi["id"])
        assert _money(same["original_ar_amount"]) == Decimal("11000000.00")
        assert same["actual_ar_account_id"] == rec_a["id"]
        assert WARN_MULTIPLE_INVOICE_AREC_ACCOUNTS not in (same.get("integrity_warnings") or [])
        assert _money(multi["original_ar_amount"]) == Decimal("12000000.00")
        assert multi["actual_ar_account_id"] is None
        assert multi["integrity_warning"] is True
        assert WARN_MULTIPLE_INVOICE_AREC_ACCOUNTS in multi["integrity_warnings"]


@pytest.mark.anyio
async def test_multiple_receipt_arec_lines():
    rec_a, _rev, _tax = await _configure_settings()
    rec_b = await _coa("11", "Piutang Receipt B", "AREC")
    bca = await _cash_bank()
    po_same = await _seed_po(customer="PT Rec Same")
    po_multi = await _seed_po(customer="PT Rec Multi")
    _admin()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        inv_same = await _posted_invoice(client, po_same["id"])
        inv_multi = await _posted_invoice(client, po_multi["id"])
        rec_same = await _create_receipt(client, inv_same["id"], 4_000_000, cash_bank_id=bca["id"])
        rec_multi = await _create_receipt(client, inv_multi["id"], 4_000_000, cash_bank_id=bca["id"])
        await _post_receipt(client, rec_same["id"])
        await _post_receipt(client, rec_multi["id"])
        same_j = await _primary_journal("sales_receipt", rec_same["id"])
        multi_j = await _primary_journal("sales_receipt", rec_multi["id"])
        credit = await _arec_credit_line(same_j["id"])
        await server.db[JOURNAL_LINES].update_one({"id": credit["id"]}, {"$set": {"credit": 2_000_000}})
        await _insert_journal_line(same_j["id"], rec_a["id"], credit=2_000_000, desc="same AR split")
        await _insert_journal_line(multi_j["id"], rec_b["id"], credit=1_000_000, desc="wrong AR extra")

        aging = (await client.get(AR_AGING)).json()
        same = _aging_row(aging, inv_same["id"])
        multi = _aging_row(aging, inv_multi["id"])
        assert _money(same["posted_receipt_amount"]) == Decimal("4000000.00")
        assert _money(same["accounting_outstanding"]) == Decimal("6000000.00")
        assert WARN_MULTIPLE_RECEIPT_AREC_ACCOUNTS not in (same.get("integrity_warnings") or [])
        assert _money(multi["posted_receipt_amount"]) == Decimal("4000000.00")
        assert _money(multi["accounting_outstanding"]) == Decimal("6000000.00")
        assert WARN_MULTIPLE_RECEIPT_AREC_ACCOUNTS in multi["integrity_warnings"]
        assert WARN_RECEIPT_AR_ACCOUNT_MISMATCH in multi["integrity_warnings"]


@pytest.mark.anyio
async def test_invoice_reversal_gl_net_zero():
    rec, _rev, _tax = await _configure_settings()
    po = await _seed_po()
    _admin()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        inv = await _posted_invoice(client, po["id"])
        await _reverse_invoice(client, inv["id"])
        aging = (await client.get(AR_AGING)).json()
        assert _aging_row(aging, inv["id"]) is None
        summary = (await client.get(AR_SUMMARY)).json()
        assert _money(summary["total_accounting_ar"]) == Decimal("0.00")
        by_id = {r["account_id"]: r for r in summary["account_breakdown"]}
        if rec["id"] in by_id:
            assert _money(by_id[rec["id"]]["sales_ar_balance"]) == Decimal("0.00")
            assert _money(by_id[rec["id"]]["gl_arec_balance"]) == Decimal("0.00")
        else:
            assert summary["account_breakdown"] == [] or all(
                _money(r["gl_arec_balance"]) == Decimal("0.00") for r in summary["account_breakdown"]
            )


@pytest.mark.anyio
async def test_receipt_reversal_statement_and_current_ar():
    await _configure_settings()
    bca = await _cash_bank()
    po = await _seed_po(customer="PT RecRev", customer_id="CUST-RECV")
    _admin()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        inv = await _posted_invoice(client, po["id"])
        receipt = await _create_receipt(client, inv["id"], 4_000_000, cash_bank_id=bca["id"])
        await _post_receipt(client, receipt["id"])
        await _reverse_receipt(client, receipt["id"])
        aging = (await client.get(AR_AGING)).json()
        row = _aging_row(aging, inv["id"])
        assert _money(row["accounting_outstanding"]) == Decimal("10000000.00")
        assert _money(row["posted_receipt_amount"]) == Decimal("0.00")
        st = (await client.get(AR_STATEMENT, params={
            "customer_key": "id:CUST-RECV",
            "start_date": "2026-09-01",
            "end_date": "2026-09-30",
        })).json()
        by_type = {row["source_type"]: row for row in st["items"]}
        assert _money(by_type["sales_receipt"]["credit"]) == Decimal("4000000.00")
        assert by_type["sales_receipt"]["status"] == "reversed"
        assert _money(by_type["sales_receipt_reversal"]["debit"]) == Decimal("4000000.00")
        assert by_type["sales_receipt_reversal"]["status"] == "posted"


@pytest.mark.anyio
async def test_statement_deterministic_order_repeated_get():
    await _configure_settings()
    bca = await _cash_bank()
    po = await _seed_po(customer="PT Order", customer_id="CUST-ORDER")
    _admin()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        inv = await _posted_invoice(client, po["id"], invoice_date="2026-09-01")
        receipt = await _create_receipt(client, inv["id"], 4_000_000, cash_bank_id=bca["id"], receipt_date="2026-09-01")
        await _post_receipt(client, receipt["id"])
        await _reverse_receipt(client, receipt["id"])
        await _reverse_invoice(client, inv["id"])
        params = {"customer_key": "id:CUST-ORDER", "start_date": "2026-09-01", "end_date": "2026-09-30"}
        first = (await client.get(AR_STATEMENT, params=params)).json()
        second = (await client.get(AR_STATEMENT, params=params)).json()
        assert STATEMENT_SORT_KEYS == ("date", "journal_number", "created_at", "journal_id")
        assert [r["journal_id"] for r in first["items"]] == [r["journal_id"] for r in second["items"]]
        assert [r["running_balance"] for r in first["items"]] == [r["running_balance"] for r in second["items"]]
        assert len(first["items"]) == 4
        assert _money(first["ending_balance"]) == Decimal("0.00")


@pytest.mark.anyio
async def test_statement_opening_includes_reversal_pairs():
    await _configure_settings()
    bca = await _cash_bank()
    po = await _seed_po(customer="PT OpenRev", customer_id="CUST-OPENREV")
    _admin()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        inv = await _posted_invoice(client, po["id"], invoice_date="2026-08-10")
        receipt = await _create_receipt(client, inv["id"], 4_000_000, cash_bank_id=bca["id"], receipt_date="2026-08-15")
        await _post_receipt(client, receipt["id"])
        await _reverse_receipt(client, receipt["id"])
        await _reverse_invoice(client, inv["id"])
        st = (await client.get(AR_STATEMENT, params={
            "customer_key": "id:CUST-OPENREV",
            "start_date": "2026-09-01",
            "end_date": "2026-09-30",
        })).json()
        assert _money(st["opening_balance"]) == Decimal("0.00")
        assert st["items"] == []
        assert _money(st["ending_balance"]) == Decimal("0.00")


@pytest.mark.anyio
async def test_orphan_sales_journals_do_not_invent_customer():
    rec, _rev, _tax = await _configure_settings()
    equity = await _coa("31", "Modal Orphan", "EQTY")
    po = await _seed_po(customer="PT Real", customer_id="CUST-REAL")
    _admin()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        inv = await _posted_invoice(client, po["id"])
        orphan_inv_id = f"missing-inv-{uuid.uuid4().hex[:8]}"
        orphan_rec_id = f"missing-rec-{uuid.uuid4().hex[:8]}"
        inv_jid = str(uuid.uuid4())
        rec_jid = str(uuid.uuid4())
        await server.db[JOURNAL_ENTRIES].insert_many([
            {
                "id": inv_jid,
                "number": "JE-ORPHAN-INV",
                "date": "2026-09-12",
                "source_type": "sales_invoice",
                "source_id": orphan_inv_id,
                "status": "posted",
                "reversal_of": None,
                "description": "orphan invoice journal",
                "created_at": "2026-09-12T00:00:00+00:00",
            },
            {
                "id": rec_jid,
                "number": "JE-ORPHAN-REC",
                "date": "2026-09-13",
                "source_type": "sales_receipt",
                "source_id": orphan_rec_id,
                "status": "posted",
                "reversal_of": None,
                "description": "orphan receipt journal",
                "created_at": "2026-09-13T00:00:00+00:00",
            },
        ])
        await server.db[JOURNAL_LINES].insert_many([
            {"id": str(uuid.uuid4()), "journal_id": inv_jid, "account_id": rec["id"], "debit": 2_000_000, "credit": 0},
            {"id": str(uuid.uuid4()), "journal_id": inv_jid, "account_id": equity["id"], "debit": 0, "credit": 2_000_000},
            {"id": str(uuid.uuid4()), "journal_id": rec_jid, "account_id": equity["id"], "debit": 1_000_000, "credit": 0},
            {"id": str(uuid.uuid4()), "journal_id": rec_jid, "account_id": rec["id"], "debit": 0, "credit": 1_000_000},
        ])

        aging = (await client.get(AR_AGING)).json()
        assert _aging_row(aging, inv["id"]) is not None
        assert _aging_row(aging, orphan_inv_id) is None
        assert all(row.get("invoice_id") != orphan_inv_id for row in aging["items"])
        customers = (await client.get(AR_CUSTOMERS)).json()["items"]
        assert all(row.get("customer_key") != "" for row in customers)
        summary = (await client.get(AR_SUMMARY)).json()
        codes = {a["code"] for a in summary["integrity_anomalies"]}
        assert WARN_MISSING_SALES_INVOICE_DOCUMENT in codes
        assert WARN_MISSING_SALES_RECEIPT_DOCUMENT in codes
        br = next(r for r in summary["account_breakdown"] if r["account_id"] == rec["id"])
        assert _money(br["sales_ar_balance"]) == Decimal("10000000.00")
        assert _money(br["gl_arec_balance"]) == Decimal("11000000.00")
        assert _money(br["difference"]) == Decimal("1000000.00")


@pytest.mark.anyio
async def test_cancelled_document_with_posted_journal_mismatch():
    await _configure_settings()
    po = await _seed_po()
    _admin()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        inv = await _posted_invoice(client, po["id"])
        await server.db.sales_invoices.update_one(
            {"id": inv["id"]},
            {"$set": {"financial_status": "cancelled"}},
        )
        aging = (await client.get(AR_AGING)).json()
        row = _aging_row(aging, inv["id"])
        assert _money(row["accounting_outstanding"]) == Decimal("10000000.00")
        assert _money(row["operational_outstanding"]) == Decimal("0.00")
        assert row["document_mismatch"] is True
        assert row["financial_status"] == "cancelled"


@pytest.mark.anyio
async def test_accounting_pending_operational_transitions():
    await _configure_settings()
    bca = await _cash_bank()
    po = await _seed_po()
    _admin()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        inv = await _posted_invoice(client, po["id"])
        receipt = await _create_receipt(client, inv["id"], 4_000_000, cash_bank_id=bca["id"])
        pending = _aging_row((await client.get(AR_AGING)).json(), inv["id"])
        assert _money(pending["accounting_outstanding"]) == Decimal("10000000.00")
        assert _money(pending["pending_receipt_amount"]) == Decimal("4000000.00")
        assert _money(pending["operational_outstanding"]) == Decimal("6000000.00")

        await _post_receipt(client, receipt["id"])
        posted = _aging_row((await client.get(AR_AGING)).json(), inv["id"])
        assert _money(posted["accounting_outstanding"]) == Decimal("6000000.00")
        assert _money(posted["pending_receipt_amount"]) == Decimal("0.00")
        assert _money(posted["operational_outstanding"]) == Decimal("6000000.00")

        await _reverse_receipt(client, receipt["id"])
        reversed_row = _aging_row((await client.get(AR_AGING)).json(), inv["id"])
        assert _money(reversed_row["accounting_outstanding"]) == Decimal("10000000.00")
        assert _money(reversed_row["pending_receipt_amount"]) == Decimal("0.00")
        assert _money(reversed_row["operational_outstanding"]) == Decimal("10000000.00")


@pytest.mark.anyio
async def test_no_silent_truncation_strategy_and_many_gl_lines():
    import inspect

    rec, _rev, _tax = await _configure_settings()
    equity = await _coa("31", "Modal Many", "EQTY")
    po = await _seed_po()
    src = inspect.getsource(_find_in)
    assert ".to_list(" not in src
    assert "_cursor_all" in src
    _admin()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        await _posted_invoice(client, po["id"])
        jid = str(uuid.uuid4())
        await server.db[JOURNAL_ENTRIES].insert_one({
            "id": jid,
            "number": "JE-MANY-AREC",
            "date": "2026-09-10",
            "source_type": "general_journal",
            "source_id": f"gj-many-{uuid.uuid4().hex[:8]}",
            "status": "posted",
            "reversal_of": None,
            "description": "many AREC lines",
            "created_at": "2026-09-10T00:00:00+00:00",
        })
        lines = []
        for i in range(80):
            lines.append({
                "id": str(uuid.uuid4()),
                "journal_id": jid,
                "account_id": rec["id"],
                "debit": 1_000,
                "credit": 0,
                "description": f"arec-{i}",
            })
        lines.append({
            "id": str(uuid.uuid4()),
            "journal_id": jid,
            "account_id": equity["id"],
            "debit": 0,
            "credit": 80_000,
            "description": "offset",
        })
        await server.db[JOURNAL_LINES].insert_many(lines)
        summary = (await client.get(AR_SUMMARY)).json()
        br = next(r for r in summary["account_breakdown"] if r["account_id"] == rec["id"])
        assert _money(br["sales_ar_balance"]) == Decimal("10000000.00")
        assert _money(br["gl_arec_balance"]) == Decimal("10080000.00")
        assert _money(br["difference"]) == Decimal("80000.00")


@pytest.mark.anyio
async def test_customer_identity_helpers_are_the_authority():
    import services.accounts_receivable as ar

    assert ar.build_identity_context is not None
    assert ar.resolve_customer_identity is not None
    src = open(ar.__file__, encoding="utf-8").read()
    assert "from services.customer_identity import" in src
    assert "build_identity_context" in src
    assert "resolve_customer_identity" in src
    assert "def normalize_customer_name" not in src
    assert "def resolve_customer_identity" not in src
