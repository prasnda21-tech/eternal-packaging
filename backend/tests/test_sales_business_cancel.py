"""Phase 2.2D — unified Sales Invoice & Receipt business cancellation."""
from __future__ import annotations

import os
import sys
import uuid
from decimal import Decimal
from pathlib import Path

import httpx
import pytest
from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ.setdefault("MONGO_URL", "mongodb://127.0.0.1:27018")
_worker = os.environ.get("PYTEST_XDIST_WORKER") or "main"
os.environ["DB_NAME"] = f"eternal_packaging_sales_business_cancel_test_{_worker}"
os.environ.setdefault("JWT_SECRET", "test-secret-key-12345678901234567890")
os.environ.setdefault("CORS_ORIGINS", "*")

import server
from permissions import (
    ALL_PERMISSIONS,
    DEFAULT_GRANT_PERMISSIONS,
    OPT_IN_ONLY_PERMISSIONS,
    ROLE_DEFAULT_PERMISSIONS,
    effective_permissions,
)
from server import app, current_user
from services.cash_bank_master import create_cash_bank, ensure_indexes as ensure_cb
from services.chart_of_accounts import create_account, ensure_indexes as ensure_coa
from services.journal_core import ensure_indexes as ensure_journal
from services.sales_accounting_settings import SETTINGS_KEY
from services.sales_business_cancel import (
    CANCEL_PROGRESS_FAILED,
    CANCEL_PROGRESS_PENDING,
    COLLECTION,
    DOC_INVOICE,
    ERR_CANCEL_INDEX_NOT_READY,
    INVOICE_FLAG_KEY,
    LIST_CLASS_ACTIVE,
    LIST_CLASS_CANCELLED,
    PERM_INVOICE_CANCEL,
    PERM_RECEIPT_CANCEL,
    PHASE_PENDING,
    PHASE_REVERSED,
    RECEIPT_FLAG_KEY,
    RECEIPT_FENCE_FIELD,
    REQUIRED_CANCEL_INDEX_NAME,
    CancelPaused,
    cancel_sales_invoice_business,
    cancel_sales_receipt_business,
    invoice_list_classification,
    prepare_cancel_indexes_for_tests,
    receipt_list_classification,
    require_cancel_index_ready,
    sales_business_cancel_index_ready,
)
from services.sales_invoice_posting import (
    SOURCE_TYPE as INVOICE_SOURCE,
    post_sales_invoice_accounting,
)
from services.sales_invoices import (
    FINANCIAL_CANCELLED,
    FINANCIAL_DRAFT,
    FINANCIAL_FINALIZED,
    effective_financial_status,
    query_sales_invoices,
)
from services.sales_receipt_posting import (
    receipt_counts_toward_outstanding,
    receipt_is_cancelled,
    post_sales_receipt_accounting,
)
from services.sales_receipts import query_sales_receipts

ADMIN = {
    "id": "admin-cancel-22d",
    "email": "admin@eternalpackaging.com",
    "name": "Admin Cancel",
    "role": "admin",
}

CANCEL_USER = {
    "id": "cancel-authorized",
    "email": "cancel.user@example.com",
    "name": "Cancel User",
    "role": "crm",
    "permissions_override": {
        PERM_INVOICE_CANCEL: True,
        PERM_RECEIPT_CANCEL: True,
        "sales.invoice.view": True,
        "sales.invoice.create": True,
        "sales.invoice.edit": True,
        "sales.receipt.view": True,
        "sales.receipt.create": True,
        "sales_accounting.post": True,
        "sales_accounting.reverse": True,
        "sales.receipt.accounting.post": True,
        "sales.receipt.accounting.reverse": True,
        "po.view": True,
        "po.edit": True,
    },
}

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
    await ensure_coa(server.db)
    await ensure_journal(server.db)
    await ensure_cb(server.db)
    for name in (
        "sales_invoices",
        "sales_receipts",
        "orders",
        "journal_entries",
        "journal_lines",
        COLLECTION,
        "app_settings",
        "cash_banks",
        "chart_of_accounts",
        "accounting_periods",
        "role_permissions",
    ):
        await server.db[name].delete_many({})
    try:
        await server.db[COLLECTION].drop_indexes()
    except Exception:
        pass
    await prepare_cancel_indexes_for_tests(server.db)
    yield
    await server.db.app_settings.delete_many({"key": {"$in": [SETTINGS_KEY, INVOICE_FLAG_KEY, RECEIPT_FLAG_KEY]}})


def _admin():
    app.dependency_overrides[current_user] = lambda: dict(ADMIN)


def _cancel_user():
    app.dependency_overrides[current_user] = lambda: dict(CANCEL_USER)


def _user(role: str, perms: dict):
    app.dependency_overrides[current_user] = lambda: {
        "id": f"user-{role}",
        "email": f"{role}@example.com",
        "name": role,
        "role": role,
        "permissions_override": perms,
    }


async def _enable_flags():
    await server.db.app_settings.update_one(
        {"key": INVOICE_FLAG_KEY},
        {"$set": {"key": INVOICE_FLAG_KEY, "enabled": True}},
        upsert=True,
    )
    await server.db.app_settings.update_one(
        {"key": RECEIPT_FLAG_KEY},
        {"$set": {"key": RECEIPT_FLAG_KEY, "enabled": True}},
        upsert=True,
    )


async def _coa(code_prefix: str, name: str, account_type: str) -> dict:
    suffix = uuid.uuid4().hex[:6]
    return await create_account(
        server.db,
        {"code": f"{code_prefix}{suffix}", "name": name, "account_type": account_type},
        user={"id": "t", "name": "t", "role": "admin"},
    )


async def _configure_settings():
    rec = await _coa("11", "Piutang Cancel", "AREC")
    rev = await _coa("41", "Pendapatan Cancel", "REVE")
    tax = await _coa("21", "PPN Cancel", "OCLY")
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


async def _cash_bank(name: str = "BCA Cancel") -> dict:
    bank_coa = await _coa("12", name, "BANK")
    return await create_cash_bank(
        server.db,
        {"name": name, "account_id": bank_coa["id"], "active": True},
        user=ADMIN,
    )


async def _seed_po(qty: int = 1000) -> dict:
    po_id = str(uuid.uuid4())
    doc = {
        "id": po_id,
        "po_number": f"PO/CXL/{uuid.uuid4().hex[:8].upper()}",
        "customer": "PT Cancel",
        "outlet": "Outlet CXL",
        "recipient": "Budi",
        "address": "Jl. Test",
        "delivery_zone": "Jakarta",
        "sales": "Vira",
        "line_items": [{"line_idx": 0, "line_id": "l0", "name": "Cup CXL", "quantity": qty, "unit": "pcs"}],
        "total_quantity": qty,
        "current_stage": 4,
        "status_label": "Approval Payment",
        "payment_status": "Waiting",
        "created_at": "2026-09-01T00:00:00Z",
    }
    await server.db.orders.insert_one(doc)
    doc.pop("_id", None)
    return doc


async def _create_invoice(client, po_id, **extra) -> dict:
    payload = {
        "po_id": po_id,
        "invoice_date": extra.pop("invoice_date", "2026-09-01"),
        "discount_amount": extra.pop("discount_amount", 0),
        "ppn_percent": extra.pop("ppn_percent", 0),
        "lines": [{
            "line_idx": 0,
            "quantity": extra.pop("quantity", 1),
            "unit_price": extra.pop("unit_price", 1_000_000),
        }],
    }
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


async def _create_receipt(client, invoice_id: str, amount, **extra) -> dict:
    payload = {
        "invoice_id": invoice_id,
        "receipt_date": extra.pop("receipt_date", "2026-09-05"),
        "amount": amount,
    }
    if "cash_bank_id" in extra:
        payload["cash_bank_id"] = extra.pop("cash_bank_id")
    payload.update(extra)
    res = await client.post("/api/sales-receipts", json=payload)
    assert res.status_code == 200, res.text
    return res.json()["receipt"]


def _key() -> str:
    return f"cxl-{uuid.uuid4().hex}"


# ---------------------------------------------------------------------------
# Permissions / flags
# ---------------------------------------------------------------------------

async def test_opt_in_permissions_not_in_any_role_default():
    assert PERM_INVOICE_CANCEL in OPT_IN_ONLY_PERMISSIONS
    assert PERM_RECEIPT_CANCEL in OPT_IN_ONLY_PERMISSIONS
    assert PERM_INVOICE_CANCEL in ALL_PERMISSIONS
    assert PERM_RECEIPT_CANCEL in ALL_PERMISSIONS
    assert PERM_INVOICE_CANCEL not in DEFAULT_GRANT_PERMISSIONS
    assert PERM_RECEIPT_CANCEL not in DEFAULT_GRANT_PERMISSIONS
    for role, keys in ROLE_DEFAULT_PERMISSIONS.items():
        assert PERM_INVOICE_CANCEL not in keys, role
        assert PERM_RECEIPT_CANCEL not in keys, role
    admin_eff = effective_permissions("admin", {})
    assert PERM_INVOICE_CANCEL not in admin_eff
    assert PERM_RECEIPT_CANCEL not in admin_eff
    # Create/edit must never imply cancel
    crm_eff = effective_permissions("crm", {
        "sales.invoice.create": True,
        "sales.invoice.edit": True,
        "sales.invoice.delete": True,
        "sales.receipt.create": True,
        "sales.receipt.delete": True,
    })
    assert PERM_INVOICE_CANCEL not in crm_eff
    assert PERM_RECEIPT_CANCEL not in crm_eff


async def test_feature_flags_default_off_fail_closed():
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        _cancel_user()
        po = await _seed_po()
        inv = await _create_invoice(client, po["id"])
        res = await client.post(
            f"/api/sales-invoices/{inv['id']}/business-cancel",
            json={"reason": "Batal uji flag", "idempotency_key": _key()},
        )
        assert res.status_code == 409, res.text
        assert "belum diaktifkan" in (res.json().get("detail") or "").lower() or "belum diaktifkan" in res.text.lower()


async def test_rbac_admin_without_override_denied_authorized_ok():
    await _enable_flags()
    await _configure_settings()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        _admin()
        po = await _seed_po()
        inv = await _create_invoice(client, po["id"])
        await _finalize(client, inv["id"])
        denied = await client.post(
            f"/api/sales-invoices/{inv['id']}/business-cancel",
            json={"reason": "Admin tanpa grant", "idempotency_key": _key()},
        )
        assert denied.status_code == 403, denied.text

        _cancel_user()
        ok = await client.post(
            f"/api/sales-invoices/{inv['id']}/business-cancel",
            json={"reason": "User berhak cancel", "idempotency_key": _key()},
        )
        assert ok.status_code == 200, ok.text
        body = ok.json()
        assert body["list_class"] == LIST_CLASS_CANCELLED
        assert effective_financial_status(body["invoice"]) == FINANCIAL_CANCELLED


# ---------------------------------------------------------------------------
# Invoice cancellation paths + list classification
# ---------------------------------------------------------------------------

async def test_draft_historical_invoice_soft_cancel_list_tabs():
    await _enable_flags()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        _cancel_user()
        po = await _seed_po()
        inv = await _create_invoice(client, po["id"])
        assert effective_financial_status(inv) == FINANCIAL_DRAFT
        # Historical draft may omit financial_status
        await server.db.sales_invoices.update_one(
            {"id": inv["id"]}, {"$unset": {"financial_status": ""}}
        )
        key = _key()
        res = await client.post(
            f"/api/sales-invoices/{inv['id']}/business-cancel",
            json={"reason": "Draft batal", "idempotency_key": key},
        )
        assert res.status_code == 200, res.text
        stored = await server.db.sales_invoices.find_one({"id": inv["id"]}, {"_id": 0})
        assert effective_financial_status(stored) == FINANCIAL_CANCELLED
        assert stored.get("invoice_number") == inv["invoice_number"]
        journals = await server.db.journal_entries.count_documents(
            {"source_type": INVOICE_SOURCE, "source_id": inv["id"]}
        )
        assert journals == 0

        active = await query_sales_invoices(server.db, scope="active", q=inv["invoice_number"])
        cancelled = await query_sales_invoices(server.db, scope="cancelled", q=inv["invoice_number"])
        semua = await query_sales_invoices(server.db, scope="all", q=inv["invoice_number"])
        assert all(r["id"] != inv["id"] for r in active["invoices"])
        assert any(r["id"] == inv["id"] for r in cancelled["invoices"])
        assert any(r["id"] == inv["id"] for r in semua["invoices"])
        row = next(r for r in cancelled["invoices"] if r["id"] == inv["id"])
        assert row["list_class"] == LIST_CLASS_CANCELLED
        assert row["business_cancel_progress"] == "complete"


async def test_finalized_unposted_soft_cancel_no_fabricated_reversal():
    await _enable_flags()
    await _configure_settings()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        _cancel_user()
        po = await _seed_po()
        inv = await _create_invoice(client, po["id"])
        await _finalize(client, inv["id"])
        res = await client.post(
            f"/api/sales-invoices/{inv['id']}/business-cancel",
            json={"reason": "Finalized unposted batal", "idempotency_key": _key()},
        )
        assert res.status_code == 200, res.text
        assert await server.db.journal_entries.count_documents(
            {"source_type": INVOICE_SOURCE, "source_id": inv["id"]}
        ) == 0
        stored = await server.db.sales_invoices.find_one({"id": inv["id"]}, {"_id": 0})
        assert effective_financial_status(stored) == FINANCIAL_CANCELLED


async def test_posted_invoice_reverse_once_and_soft_cancel():
    await _enable_flags()
    await _configure_settings()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        _cancel_user()
        po = await _seed_po()
        inv = await _create_invoice(client, po["id"])
        await _finalize(client, inv["id"])
        await _post_invoice(client, inv["id"])
        key = _key()
        res = await client.post(
            f"/api/sales-invoices/{inv['id']}/business-cancel",
            json={"reason": "Posted batal sekali", "idempotency_key": key},
        )
        assert res.status_code == 200, res.text
        primaries = await server.db.journal_entries.find(
            {"source_type": INVOICE_SOURCE, "source_id": inv["id"], "reversal_of": None},
            {"_id": 0},
        ).to_list(10)
        assert len(primaries) == 1
        assert primaries[0]["status"] == "reversed"
        reversals = await server.db.journal_entries.find(
            {"reversal_of": primaries[0]["id"]}, {"_id": 0}
        ).to_list(10)
        assert len(reversals) == 1

        # Idempotent replay — no second reversal
        again = await client.post(
            f"/api/sales-invoices/{inv['id']}/business-cancel",
            json={"reason": "Posted batal sekali", "idempotency_key": key},
        )
        assert again.status_code == 200, again.text
        assert again.json()["outcome"] in {"idempotent_replay", "already_cancelled"}
        assert len(await server.db.journal_entries.find(
            {"reversal_of": primaries[0]["id"]}, {"_id": 0}
        ).to_list(10)) == 1


async def test_same_key_different_payload_conflict():
    await _enable_flags()
    await _configure_settings()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        _cancel_user()
        po = await _seed_po()
        inv = await _create_invoice(client, po["id"])
        await _finalize(client, inv["id"])
        key = _key()
        ok = await client.post(
            f"/api/sales-invoices/{inv['id']}/business-cancel",
            json={"reason": "Alasan pertama", "idempotency_key": key},
        )
        assert ok.status_code == 200, ok.text
        conflict = await client.post(
            f"/api/sales-invoices/{inv['id']}/business-cancel",
            json={"reason": "Alasan berbeda xxx", "idempotency_key": key},
        )
        assert conflict.status_code == 409, conflict.text


async def test_pending_cancel_not_classified_as_batal():
    await _enable_flags()
    await _configure_settings()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        _cancel_user()
        po = await _seed_po()
        inv = await _create_invoice(client, po["id"])
        await _finalize(client, inv["id"])
        await _post_invoice(client, inv["id"])
        key = _key()
        with pytest.raises(CancelPaused):
            await cancel_sales_invoice_business(
                server.db,
                inv["id"],
                CANCEL_USER,
                reason="Pause sebelum reverse",
                idempotency_key=key,
                pause_after="reversing",
            )
        stored = await server.db.sales_invoices.find_one({"id": inv["id"]}, {"_id": 0})
        assert effective_financial_status(stored) == FINANCIAL_FINALIZED
        assert invoice_list_classification(stored) == LIST_CLASS_ACTIVE
        active = await query_sales_invoices(server.db, scope="active", q=inv["invoice_number"])
        cancelled = await query_sales_invoices(server.db, scope="cancelled", q=inv["invoice_number"])
        assert any(r["id"] == inv["id"] for r in active["invoices"])
        assert all(r["id"] != inv["id"] for r in cancelled["invoices"])
        row = next(r for r in active["invoices"] if r["id"] == inv["id"])
        assert row["business_cancel_progress"] == CANCEL_PROGRESS_PENDING
        assert row["list_class"] == LIST_CLASS_ACTIVE

        # Recovery finishes
        done = await cancel_sales_invoice_business(
            server.db,
            inv["id"],
            CANCEL_USER,
            reason="Pause sebelum reverse",
            idempotency_key=key,
        )
        assert done["list_class"] == LIST_CLASS_CANCELLED
        cancelled2 = await query_sales_invoices(server.db, scope="cancelled", q=inv["invoice_number"])
        assert any(r["id"] == inv["id"] for r in cancelled2["invoices"])


async def test_crash_after_reverse_before_complete_recovers():
    await _enable_flags()
    await _configure_settings()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        _cancel_user()
        po = await _seed_po()
        inv = await _create_invoice(client, po["id"])
        await _finalize(client, inv["id"])
        await _post_invoice(client, inv["id"])
        key = _key()
        with pytest.raises(CancelPaused):
            await cancel_sales_invoice_business(
                server.db,
                inv["id"],
                CANCEL_USER,
                reason="Pause setelah reverse",
                idempotency_key=key,
                pause_after=PHASE_REVERSED,
            )
        # Crash window: financial may already be cancelled, but intent incomplete → Aktif.
        stored = await server.db.sales_invoices.find_one({"id": inv["id"]}, {"_id": 0})
        intent = await server.db[COLLECTION].find_one(
            {"document_type": DOC_INVOICE, "document_id": inv["id"], "idempotency_key": key},
            {"_id": 0},
        )
        assert invoice_list_classification(stored, intent) == LIST_CLASS_ACTIVE
        active = await query_sales_invoices(server.db, scope="active", q=inv["invoice_number"])
        batal = await query_sales_invoices(server.db, scope="cancelled", q=inv["invoice_number"])
        assert any(r["id"] == inv["id"] for r in active["invoices"])
        assert all(r["id"] != inv["id"] for r in batal["invoices"])

        # Journal evidence may already be reversed; recovery must finish without duplicate.
        done = await cancel_sales_invoice_business(
            server.db,
            inv["id"],
            CANCEL_USER,
            reason="Pause setelah reverse",
            idempotency_key=key,
        )
        assert done["outcome"] in {"cancelled", "recovered", "idempotent_replay", "already_cancelled"}
        assert effective_financial_status(done["invoice"]) == FINANCIAL_CANCELLED
        assert done.get("list_class") == LIST_CLASS_CANCELLED
        batal2 = await query_sales_invoices(server.db, scope="cancelled", q=inv["invoice_number"])
        assert any(r["id"] == inv["id"] for r in batal2["invoices"])
        primaries = await server.db.journal_entries.find(
            {"source_type": INVOICE_SOURCE, "source_id": inv["id"], "reversal_of": None},
            {"_id": 0},
        ).to_list(10)
        assert len(primaries) == 1
        assert len(await server.db.journal_entries.find(
            {"reversal_of": primaries[0]["id"]}, {"_id": 0}
        ).to_list(10)) == 1


async def test_receipt_blocker_on_posted_invoice_cancel():
    await _enable_flags()
    await _configure_settings()
    bank = await _cash_bank()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        _cancel_user()
        po = await _seed_po()
        inv = await _create_invoice(client, po["id"])
        await _finalize(client, inv["id"])
        await _post_invoice(client, inv["id"])
        await _create_receipt(client, inv["id"], 100_000, cash_bank_id=bank["id"])
        res = await client.post(
            f"/api/sales-invoices/{inv['id']}/business-cancel",
            json={"reason": "Ada receipt", "idempotency_key": _key()},
        )
        assert res.status_code == 409, res.text


async def test_closed_period_blocks_posted_cancel():
    await _enable_flags()
    await _configure_settings()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        _cancel_user()
        po = await _seed_po()
        inv = await _create_invoice(client, po["id"], invoice_date="2026-01-15")
        await _finalize(client, inv["id"])
        await _post_invoice(client, inv["id"])
        await server.db.accounting_periods.update_one(
            {"period_key": "2026-01"},
            {
                "$set": {
                    "period_key": "2026-01",
                    "status": "closed",
                    "id": str(uuid.uuid4()),
                    "writer_claims": [],
                }
            },
            upsert=True,
        )
        res = await client.post(
            f"/api/sales-invoices/{inv['id']}/business-cancel",
            json={"reason": "Period closed", "idempotency_key": _key()},
        )
        assert res.status_code == 409, res.text
        stored = await server.db.sales_invoices.find_one({"id": inv["id"]}, {"_id": 0})
        assert effective_financial_status(stored) == FINANCIAL_FINALIZED


# ---------------------------------------------------------------------------
# Receipt cancellation
# ---------------------------------------------------------------------------

async def test_unposted_receipt_soft_cancel_list_and_outstanding():
    await _enable_flags()
    await _configure_settings()
    bank = await _cash_bank()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        _cancel_user()
        po = await _seed_po()
        inv = await _create_invoice(client, po["id"])
        await _finalize(client, inv["id"])
        await _post_invoice(client, inv["id"])
        rec = await _create_receipt(client, inv["id"], 250_000, cash_bank_id=bank["id"])
        assert receipt_counts_toward_outstanding(rec) is True
        res = await client.post(
            f"/api/sales-receipts/{rec['id']}/business-cancel",
            json={"reason": "Receipt unposted batal", "idempotency_key": _key()},
        )
        assert res.status_code == 200, res.text
        assert res.json()["list_class"] == LIST_CLASS_CANCELLED
        stored = await server.db.sales_receipts.find_one({"id": rec["id"]}, {"_id": 0})
        assert receipt_is_cancelled(stored)
        assert receipt_counts_toward_outstanding(stored) is False
        assert await server.db.journal_entries.count_documents(
            {"source_id": rec["id"]}
        ) == 0

        active = await query_sales_receipts(server.db, scope="active", q=rec["receipt_number"])
        cancelled = await query_sales_receipts(server.db, scope="cancelled", q=rec["receipt_number"])
        semua = await query_sales_receipts(server.db, scope="all", q=rec["receipt_number"])
        assert all(r["id"] != rec["id"] for r in active["receipts"])
        assert any(r["id"] == rec["id"] for r in cancelled["receipts"])
        assert any(r["id"] == rec["id"] for r in semua["receipts"])


async def test_posted_receipt_reverse_once_soft_cancel():
    await _enable_flags()
    await _configure_settings()
    bank = await _cash_bank()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        _cancel_user()
        po = await _seed_po()
        inv = await _create_invoice(client, po["id"])
        await _finalize(client, inv["id"])
        await _post_invoice(client, inv["id"])
        rec = await _create_receipt(client, inv["id"], 250_000, cash_bank_id=bank["id"])
        post = await client.post(
            f"/api/sales-receipts/{rec['id']}/post-accounting",
            json={"cash_bank_id": bank["id"]},
        )
        assert post.status_code == 200, post.text
        key = _key()
        res = await client.post(
            f"/api/sales-receipts/{rec['id']}/business-cancel",
            json={"reason": "Posted receipt batal", "idempotency_key": key},
        )
        assert res.status_code == 200, res.text
        primaries = await server.db.journal_entries.find(
            {"source_id": rec["id"], "reversal_of": None}, {"_id": 0}
        ).to_list(10)
        assert len(primaries) == 1
        assert primaries[0]["status"] == "reversed"
        again = await client.post(
            f"/api/sales-receipts/{rec['id']}/business-cancel",
            json={"reason": "Posted receipt batal", "idempotency_key": key},
        )
        assert again.status_code == 200, again.text
        assert len(await server.db.journal_entries.find(
            {"reversal_of": primaries[0]["id"]}, {"_id": 0}
        ).to_list(10)) == 1


async def test_receipt_rbac_unauthorized():
    await _enable_flags()
    await _configure_settings()
    bank = await _cash_bank()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        _cancel_user()
        po = await _seed_po()
        inv = await _create_invoice(client, po["id"])
        await _finalize(client, inv["id"])
        await _post_invoice(client, inv["id"])
        rec = await _create_receipt(client, inv["id"], 100_000, cash_bank_id=bank["id"])
        _user("crm", {"sales.receipt.create": True, "sales.receipt.delete": True})
        denied = await client.post(
            f"/api/sales-receipts/{rec['id']}/business-cancel",
            json={"reason": "Tanpa cancel perm", "idempotency_key": _key()},
        )
        assert denied.status_code == 403, denied.text


async def test_legacy_cancel_endpoint_unchanged_perms():
    """Legacy /cancel still uses create/edit — not sales.invoice.cancel."""
    await _configure_settings()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        _user("crm", {
            "sales.invoice.edit": True,
            "sales.invoice.create": True,
            "po.edit": True,
            "po.view": True,
        })
        po = await _seed_po()
        inv = await _create_invoice(client, po["id"])
        await _finalize(client, inv["id"])
        res = await client.post(f"/api/sales-invoices/{inv['id']}/cancel")
        assert res.status_code == 200, res.text
        assert effective_financial_status(res.json()["invoice"]) == FINANCIAL_CANCELLED


async def test_failed_intent_not_batal():
    await _enable_flags()
    await server.db[COLLECTION].insert_one({
        "id": str(uuid.uuid4()),
        "document_type": "invoice",
        "document_id": "inv-failed-demo",
        "idempotency_key": _key(),
        "phase": "failed",
        "complete": False,
        "created_at": "2026-09-01T00:00:00Z",
    })
    await server.db.sales_invoices.insert_one({
        "id": "inv-failed-demo",
        "invoice_number": "INV/FAILED/DEMO",
        "financial_status": FINANCIAL_FINALIZED,
        "grand_total": 1000,
        "created_at": "2026-09-01T00:00:00Z",
        "customer": "Demo",
        "po_number": "PO/X",
    })
    page = await query_sales_invoices(server.db, scope="active", q="INV/FAILED/DEMO")
    row = next(r for r in page["invoices"] if r["id"] == "inv-failed-demo")
    assert row["list_class"] == LIST_CLASS_ACTIVE
    assert row["business_cancel_progress"] == CANCEL_PROGRESS_FAILED
    batal = await query_sales_invoices(server.db, scope="cancelled", q="INV/FAILED/DEMO")
    assert all(r["id"] != "inv-failed-demo" for r in batal["invoices"])


# ---------------------------------------------------------------------------
# P0/P1 safety corrections
# ---------------------------------------------------------------------------

async def test_missing_cancel_index_fail_closed_no_writes():
    await _enable_flags()
    await _configure_settings()
    await server.db[COLLECTION].drop_indexes()
    assert await sales_business_cancel_index_ready(server.db) is False
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        _cancel_user()
        po = await _seed_po()
        inv = await _create_invoice(client, po["id"])
        await _finalize(client, inv["id"])
        before = await server.db.sales_invoices.find_one({"id": inv["id"]}, {"_id": 0})
        res = await client.post(
            f"/api/sales-invoices/{inv['id']}/business-cancel",
            json={"reason": "Tanpa indeks", "idempotency_key": _key()},
        )
        assert res.status_code == 409, res.text
        assert "indeks" in (res.json().get("detail") or "").lower()
        after = await server.db.sales_invoices.find_one({"id": inv["id"]}, {"_id": 0})
        assert after.get("financial_status") == before.get("financial_status")
        assert not after.get("sales_business_cancel_fence")
        assert await server.db[COLLECTION].count_documents({"document_id": inv["id"]}) == 0


async def test_incorrect_cancel_index_shape_fail_closed():
    await server.db[COLLECTION].drop_indexes()
    await server.db[COLLECTION].create_index(
        [("document_id", 1)], unique=False, name=REQUIRED_CANCEL_INDEX_NAME
    )
    assert await sales_business_cancel_index_ready(server.db) is False
    with pytest.raises(HTTPException) as exc:
        await require_cancel_index_ready(server.db)
    assert exc.value.status_code == 409
    assert ERR_CANCEL_INDEX_NOT_READY in str(exc.value.detail)


async def test_index_ready_allows_cancellation():
    await _enable_flags()
    await _configure_settings()
    assert await sales_business_cancel_index_ready(server.db) is True
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        _cancel_user()
        po = await _seed_po()
        inv = await _create_invoice(client, po["id"])
        await _finalize(client, inv["id"])
        res = await client.post(
            f"/api/sales-invoices/{inv['id']}/business-cancel",
            json={"reason": "Indeks siap", "idempotency_key": _key()},
        )
        assert res.status_code == 200, res.text


async def test_cancel_fence_first_blocks_credit_claim():
    from services.sales_customer_credit import _acquire_claim

    await _enable_flags()
    await _configure_settings()
    bank = await _cash_bank()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        _cancel_user()
        po = await _seed_po()
        inv = await _create_invoice(client, po["id"])
        await _finalize(client, inv["id"])
        await _post_invoice(client, inv["id"])
        rec = await _create_receipt(client, inv["id"], 100_000, cash_bank_id=bank["id"])
        await server.db.sales_receipts.update_one(
            {"id": rec["id"]},
            {"$set": {RECEIPT_FENCE_FIELD: "fence-1", "sales_business_cancel_claim": "tok"}},
        )
        claimed = await _acquire_claim(server.db, rec["id"], str(uuid.uuid4()))
        assert claimed is None


async def test_credit_claim_first_blocks_receipt_cancel():
    from services.sales_customer_credit import CLAIM_AT_FIELD, CLAIM_FIELD, now_iso
    from services.sales_business_cancel import _acquire_receipt_cancel_fence

    await _enable_flags()
    await _configure_settings()
    bank = await _cash_bank()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        _cancel_user()
        po = await _seed_po()
        inv = await _create_invoice(client, po["id"])
        await _finalize(client, inv["id"])
        await _post_invoice(client, inv["id"])
        rec = await _create_receipt(client, inv["id"], 100_000, cash_bank_id=bank["id"])
        await server.db.sales_receipts.update_one(
            {"id": rec["id"]},
            {"$set": {CLAIM_FIELD: "credit-tok", CLAIM_AT_FIELD: now_iso()}},
        )
        claimed = await _acquire_receipt_cancel_fence(
            server.db, rec["id"], str(uuid.uuid4()), str(uuid.uuid4()), _key()
        )
        assert claimed is None
        # Expired TTL alone must still block cancel while token present.
        stale = "2000-01-01T00:00:00+00:00"
        await server.db.sales_receipts.update_one(
            {"id": rec["id"]}, {"$set": {CLAIM_AT_FIELD: stale}}
        )
        claimed2 = await _acquire_receipt_cancel_fence(
            server.db, rec["id"], str(uuid.uuid4()), str(uuid.uuid4()), _key()
        )
        assert claimed2 is None


async def test_credit_recovery_blocked_while_cancel_fence_active():
    from services.sales_customer_credit import hold_source_credit_claim

    rid = str(uuid.uuid4())
    await server.db.sales_receipts.insert_one({
        "id": rid,
        "receipt_number": "PAY/FENCE/1",
        "financial_status": "active",
        RECEIPT_FENCE_FIELD: "cx-1",
    })
    held = await hold_source_credit_claim(server.db, rid, str(uuid.uuid4()))
    assert held is None


async def test_settlement_cas_blocked_by_cancel_fence():
    from services.sales_receipt_posting import SOURCE_TYPE as RECEIPT_SOURCE
    from services.sales_receipts import apply_operational_settlement_for_receipt

    await _configure_settings()
    rid = str(uuid.uuid4())
    jid = str(uuid.uuid4())
    await server.db.journal_entries.insert_one({
        "id": jid,
        "source_type": RECEIPT_SOURCE,
        "source_id": rid,
        "status": "posted",
        "reversal_of": None,
        "number": f"JE-{uuid.uuid4().hex[:8]}",
    })
    await server.db.sales_receipts.insert_one({
        "id": rid,
        "receipt_number": "PAY/SET/1",
        "invoice_id": "inv-x",
        "financial_status": "active",
        "settlement_effective": False,
        "amount": 1000,
        RECEIPT_FENCE_FIELD: "cx-settle",
    })
    with pytest.raises(HTTPException) as exc:
        await apply_operational_settlement_for_receipt(server.db, rid, CANCEL_USER)
    assert exc.value.status_code == 409
    detail = exc.value.detail
    if isinstance(detail, dict):
        assert detail.get("error_code") == "receipt_cancel_in_progress"
    else:
        assert "pembatalan" in str(detail).lower()


async def test_role_permissions_only_grant_succeeds():
    await _enable_flags()
    await _configure_settings()
    await server.db.role_permissions.insert_one({
        "role": "crm",
        "permissions": [
            PERM_INVOICE_CANCEL,
            "sales.invoice.view",
            "sales.invoice.create",
            "sales.invoice.edit",
            "po.view",
            "po.edit",
            "sales_accounting.post",
        ],
    })
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        # No user override — only role_permissions grant.
        _user("crm", {})
        # Seed via a user who can create
        app.dependency_overrides[current_user] = lambda: {
            "id": "seed",
            "email": "seed@example.com",
            "name": "seed",
            "role": "crm",
            "permissions_override": {
                "sales.invoice.create": True,
                "sales.invoice.edit": True,
                "po.edit": True,
                "po.view": True,
            },
        }
        po = await _seed_po()
        inv = await _create_invoice(client, po["id"])
        await _finalize(client, inv["id"])
        _user("crm", {})  # role_permissions only
        res = await client.post(
            f"/api/sales-invoices/{inv['id']}/business-cancel",
            json={"reason": "Hak akses role", "idempotency_key": _key()},
        )
        assert res.status_code == 200, res.text


async def test_historic_cancelled_without_intent_stays_batal():
    await server.db.sales_invoices.insert_one({
        "id": "inv-hist-batal",
        "invoice_number": "INV/HIST/BATAL",
        "financial_status": FINANCIAL_CANCELLED,
        "grand_total": 1000,
        "created_at": "2025-01-01T00:00:00Z",
        "customer": "Hist",
        "po_number": "PO/H",
    })
    assert invoice_list_classification({"financial_status": FINANCIAL_CANCELLED}, None) == LIST_CLASS_CANCELLED
    batal = await query_sales_invoices(server.db, scope="cancelled", q="INV/HIST/BATAL")
    aktif = await query_sales_invoices(server.db, scope="active", q="INV/HIST/BATAL")
    semua = await query_sales_invoices(server.db, scope="all", q="INV/HIST/BATAL")
    assert any(r["id"] == "inv-hist-batal" for r in batal["invoices"])
    assert all(r["id"] != "inv-hist-batal" for r in aktif["invoices"])
    assert any(r["id"] == "inv-hist-batal" for r in semua["invoices"])


async def test_feature_flags_off_preserve_legacy_cancel():
    """Flags OFF: business-cancel blocked; legacy cancel still works."""
    await _configure_settings()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        _cancel_user()
        po = await _seed_po()
        inv = await _create_invoice(client, po["id"])
        await _finalize(client, inv["id"])
        blocked = await client.post(
            f"/api/sales-invoices/{inv['id']}/business-cancel",
            json={"reason": "Flag off", "idempotency_key": _key()},
        )
        assert blocked.status_code == 409, blocked.text
        legacy = await client.post(f"/api/sales-invoices/{inv['id']}/cancel")
        assert legacy.status_code == 200, legacy.text


# ---------------------------------------------------------------------------
# Receipt cancel → PO payment projection (sync_order_payment_projections)
# ---------------------------------------------------------------------------

async def test_cancel_last_receipt_before_penjadwalan_resyncs_payment():
    """Last effective receipt cancel before stage 5 → Waiting, empty projections."""
    await _enable_flags()
    await _configure_settings()
    bank = await _cash_bank()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        _cancel_user()
        po = await _seed_po()
        await server.db.orders.update_one(
            {"id": po["id"]},
            {"$set": {"current_stage": 4, "design_status": "Waiting"}},
        )
        inv = await _create_invoice(client, po["id"], unit_price=500_000)
        await _finalize(client, inv["id"])
        await _post_invoice(client, inv["id"])
        rec = await _create_receipt(client, inv["id"], 100_000, cash_bank_id=bank["id"])
        order = await server.db.orders.find_one({"id": po["id"]}, {"_id": 0})
        assert order.get("payment_status") == "Approved"
        assert any(r.get("receipt_id") == rec["id"] for r in (order.get("payment_receipts") or []))
        assert (order.get("payment_fact") or {}).get("receipt_id") == rec["id"]
        hist_before = len(order.get("payment_approval_history") or [])

        res = await client.post(
            f"/api/sales-receipts/{rec['id']}/business-cancel",
            json={"reason": "Cancel last PAY", "idempotency_key": _key()},
        )
        assert res.status_code == 200, res.text

        after = await server.db.orders.find_one({"id": po["id"]}, {"_id": 0})
        assert after.get("payment_status") == "Waiting"
        assert not (after.get("payment_receipts") or [])
        assert after.get("payment_fact") is None
        assert int(after.get("current_stage") or 0) < 5
        assert len(after.get("payment_approval_history") or []) == hist_before
        inv_after = await server.db.sales_invoices.find_one({"id": inv["id"]}, {"_id": 0})
        assert float(inv_after.get("paid_total") or 0) == 0.0


async def test_cancel_one_receipt_keeps_other_effective_projection():
    await _enable_flags()
    await _configure_settings()
    bank = await _cash_bank()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        _cancel_user()
        po = await _seed_po()
        inv = await _create_invoice(client, po["id"], unit_price=1_000_000)
        await _finalize(client, inv["id"])
        await _post_invoice(client, inv["id"])
        first = await _create_receipt(client, inv["id"], 200_000, cash_bank_id=bank["id"])
        second = await _create_receipt(client, inv["id"], 300_000, cash_bank_id=bank["id"])
        order = await server.db.orders.find_one({"id": po["id"]}, {"_id": 0})
        assert order.get("payment_status") == "Approved"
        hist_before = len(order.get("payment_approval_history") or [])

        res = await client.post(
            f"/api/sales-receipts/{first['id']}/business-cancel",
            json={"reason": "Cancel first of two", "idempotency_key": _key()},
        )
        assert res.status_code == 200, res.text

        after = await server.db.orders.find_one({"id": po["id"]}, {"_id": 0})
        ids = {r.get("receipt_id") for r in (after.get("payment_receipts") or [])}
        assert first["id"] not in ids
        assert second["id"] in ids
        assert (after.get("payment_fact") or {}).get("receipt_id") == second["id"]
        assert after.get("payment_status") == "Approved"
        assert len(after.get("payment_approval_history") or []) == hist_before


async def test_cancel_idempotent_replay_does_not_duplicate_history_or_journals():
    await _enable_flags()
    await _configure_settings()
    bank = await _cash_bank()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        _cancel_user()
        po = await _seed_po()
        inv = await _create_invoice(client, po["id"])
        await _finalize(client, inv["id"])
        await _post_invoice(client, inv["id"])
        rec = await _create_receipt(client, inv["id"], 150_000, cash_bank_id=bank["id"])
        post = await client.post(
            f"/api/sales-receipts/{rec['id']}/post-accounting",
            json={"cash_bank_id": bank["id"]},
        )
        assert post.status_code == 200, post.text
        key = _key()
        first = await client.post(
            f"/api/sales-receipts/{rec['id']}/business-cancel",
            json={"reason": "Idempotent cancel", "idempotency_key": key},
        )
        assert first.status_code == 200, first.text
        order_mid = await server.db.orders.find_one({"id": po["id"]}, {"_id": 0})
        hist_mid = list(order_mid.get("payment_approval_history") or [])
        receipts_mid = list(order_mid.get("payment_receipts") or [])
        journals = await server.db.journal_entries.find(
            {"source_id": rec["id"]}, {"_id": 0, "id": 1, "status": 1, "reversal_of": 1}
        ).to_list(20)
        assert len([j for j in journals if not j.get("reversal_of")]) == 1
        assert len([j for j in journals if j.get("reversal_of")]) == 1

        replay = await client.post(
            f"/api/sales-receipts/{rec['id']}/business-cancel",
            json={"reason": "Idempotent cancel", "idempotency_key": key},
        )
        assert replay.status_code == 200, replay.text
        assert replay.json().get("outcome") in {"idempotent_replay", "already_cancelled", "cancelled"}

        order_after = await server.db.orders.find_one({"id": po["id"]}, {"_id": 0})
        assert list(order_after.get("payment_approval_history") or []) == hist_mid
        assert list(order_after.get("payment_receipts") or []) == receipts_mid
        journals_after = await server.db.journal_entries.find(
            {"source_id": rec["id"]}, {"_id": 0}
        ).to_list(20)
        assert len(journals_after) == len(journals)


async def test_unset_settlement_receipt_not_counted_after_sibling_cancel():
    """settlement_effective=False never appears in PO projections."""
    from services.sales_receipts import create_sales_receipt

    await _enable_flags()
    await _configure_settings()
    bank = await _cash_bank()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        _cancel_user()
        po = await _seed_po()
        inv = await _create_invoice(client, po["id"], unit_price=1_000_000)
        await _finalize(client, inv["id"])
        await _post_invoice(client, inv["id"])
        settled = await _create_receipt(client, inv["id"], 250_000, cash_bank_id=bank["id"])
        deferred = await create_sales_receipt(
            server.db,
            {
                "invoice_id": inv["id"],
                "receipt_date": "2026-09-06",
                "amount": 100_000,
                "cash_bank_id": bank["id"],
            },
            CANCEL_USER,
            defer_operational_settlement=True,
            require_posted_invoice_journal=True,
        )
        assert deferred.get("settlement_effective") is False
        order = await server.db.orders.find_one({"id": po["id"]}, {"_id": 0})
        ids = {r.get("receipt_id") for r in (order.get("payment_receipts") or [])}
        assert settled["id"] in ids
        assert deferred["id"] not in ids

        res = await client.post(
            f"/api/sales-receipts/{settled['id']}/business-cancel",
            json={"reason": "Cancel settled only", "idempotency_key": _key()},
        )
        assert res.status_code == 200, res.text
        after = await server.db.orders.find_one({"id": po["id"]}, {"_id": 0})
        assert deferred["id"] not in {r.get("receipt_id") for r in (after.get("payment_receipts") or [])}
        assert after.get("payment_status") == "Waiting"
        assert after.get("payment_fact") is None


async def test_already_cancelled_heals_stale_payment_projections():
    """already_cancelled path rebuilds PO projections when receipt already cancelled."""
    await _enable_flags()
    await _configure_settings()
    bank = await _cash_bank()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        _cancel_user()
        po = await _seed_po()
        inv = await _create_invoice(client, po["id"])
        await _finalize(client, inv["id"])
        await _post_invoice(client, inv["id"])
        rec = await _create_receipt(client, inv["id"], 175_000, cash_bank_id=bank["id"])

        # Simulate stale cancel: business cancelled without projection sync.
        await server.db.sales_receipts.update_one(
            {"id": rec["id"]},
            {
                "$set": {
                    "financial_status": "cancelled",
                    "cancelled": True,
                    "settlement_effective": False,
                }
            },
        )
        stale = await server.db.orders.find_one({"id": po["id"]}, {"_id": 0})
        assert stale.get("payment_status") == "Approved"
        assert any(r.get("receipt_id") == rec["id"] for r in (stale.get("payment_receipts") or []))

        res = await client.post(
            f"/api/sales-receipts/{rec['id']}/business-cancel",
            json={"reason": "Heal already cancelled", "idempotency_key": _key()},
        )
        assert res.status_code == 200, res.text
        assert res.json().get("outcome") == "already_cancelled"

        after = await server.db.orders.find_one({"id": po["id"]}, {"_id": 0})
        assert after.get("payment_status") == "Waiting"
        assert not (after.get("payment_receipts") or [])
        assert after.get("payment_fact") is None
        inv_after = await server.db.sales_invoices.find_one({"id": inv["id"]}, {"_id": 0})
        assert float(inv_after.get("paid_total") or 0) == 0.0


async def test_cancel_last_receipt_after_penjadwalan_keeps_approved():
    """After stage >= 5, cancel last receipt clears projections but does not rewind Approved."""
    await _enable_flags()
    await _configure_settings()
    bank = await _cash_bank()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        _cancel_user()
        po = await _seed_po()
        inv = await _create_invoice(client, po["id"], unit_price=500_000)
        await _finalize(client, inv["id"])
        await _post_invoice(client, inv["id"])
        rec = await _create_receipt(client, inv["id"], 100_000, cash_bank_id=bank["id"])
        await server.db.orders.update_one(
            {"id": po["id"]},
            {
                "$set": {
                    "current_stage": 5,
                    "status_label": "Penjadwalan",
                    "payment_status": "Approved",
                    "payment_approved_by": "system",
                }
            },
        )
        res = await client.post(
            f"/api/sales-receipts/{rec['id']}/business-cancel",
            json={"reason": "Cancel after Penjadwalan", "idempotency_key": _key()},
        )
        assert res.status_code == 200, res.text
        after = await server.db.orders.find_one({"id": po["id"]}, {"_id": 0})
        assert after.get("payment_status") == "Approved"
        assert not (after.get("payment_receipts") or [])
        assert after.get("payment_fact") is None
        assert int(after.get("current_stage") or 0) == 5


async def test_already_cancelled_clean_projections_skip_order_write(monkeypatch):
    """already_cancelled with reconciled projections must not call sync/recalc writers."""
    import services.sales_receipts as receipts_mod

    await _enable_flags()
    await _configure_settings()
    bank = await _cash_bank()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        _cancel_user()
        po = await _seed_po()
        inv = await _create_invoice(client, po["id"])
        await _finalize(client, inv["id"])
        await _post_invoice(client, inv["id"])
        rec = await _create_receipt(client, inv["id"], 80_000, cash_bank_id=bank["id"])
        first = await client.post(
            f"/api/sales-receipts/{rec['id']}/business-cancel",
            json={"reason": "Clean cancel", "idempotency_key": _key()},
        )
        assert first.status_code == 200, first.text
        mid = await server.db.orders.find_one({"id": po["id"]}, {"_id": 0})

        calls = {"sync": 0, "recalc": 0}
        real_sync = receipts_mod.sync_order_payment_projections
        real_recalc = receipts_mod._recalc_invoice_payment_from_receipts

        async def spy_sync(db, po_id):
            calls["sync"] += 1
            return await real_sync(db, po_id)

        async def spy_recalc(db, invoice_id):
            calls["recalc"] += 1
            return await real_recalc(db, invoice_id)

        monkeypatch.setattr(receipts_mod, "sync_order_payment_projections", spy_sync)
        monkeypatch.setattr(receipts_mod, "_recalc_invoice_payment_from_receipts", spy_recalc)

        second = await client.post(
            f"/api/sales-receipts/{rec['id']}/business-cancel",
            json={"reason": "Already clean", "idempotency_key": _key()},
        )
        assert second.status_code == 200, second.text
        assert second.json().get("outcome") == "already_cancelled"
        assert calls["sync"] == 0
        assert calls["recalc"] == 0

        after = await server.db.orders.find_one({"id": po["id"]}, {"_id": 0})
        assert after.get("payment_status") == mid.get("payment_status")
        assert list(after.get("payment_receipts") or []) == list(mid.get("payment_receipts") or [])
        assert after.get("payment_fact") == mid.get("payment_fact")
