"""Unified Simpan/Hapus for sales invoices. Isolated Mongo only."""
from __future__ import annotations

import asyncio
import os
import sys
import uuid
from decimal import Decimal
from pathlib import Path

import httpx
import pytest
from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ.setdefault("MONGO_URL", "mongodb://127.0.0.1:27017")
_worker = os.environ.get("PYTEST_XDIST_WORKER") or "main"
os.environ["DB_NAME"] = f"invoice_issue_delete_isolated_{_worker}"
os.environ.setdefault("JWT_SECRET", "test-secret-key-12345678901234567890")
os.environ.setdefault("CORS_ORIGINS", "*")

assert os.environ["DB_NAME"] != "eternal_packaging"

import server
from server import app, current_user
from services.accounting_periods import close_period
from services.chart_of_accounts import create_account, ensure_indexes as ensure_coa
from services.customer_tracking import ensure_tracking_token
from services.customer_tracking_invoices import customer_may_view_invoice
from services.journal_core import (
    JOURNAL_ENTRIES,
    JOURNAL_LINES,
    ensure_indexes as ensure_journal,
    reverse_journal,
)
from services.sales_accounting_settings import SETTINGS_KEY
from services.sales_invoice_issue import (
    AUDIT_ACTION_DELETE,
    AUDIT_ACTION_ISSUE,
    DELETE_REASON,
    QTY_COLLECTION,
    _qty_reserve_hook,
    issue_sales_invoice,
    remove_issued_sales_invoice,
    continue_sales_invoice_issue,
)
from services.sales_invoice_posting import SOURCE_REVERSAL, SOURCE_TYPE
from services.sales_invoices import list_sales_invoices
from services.sales_receipts import create_sales_receipt
import services.sales_invoice_issue as issue_mod

pytestmark = pytest.mark.anyio

ADMIN = {
    "id": "admin-issue",
    "email": "admin-issue@example.com",
    "name": "Admin Issue",
    "role": "admin",
}


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture(autouse=True)
def bind_motor():
    from motor.motor_asyncio import AsyncIOMotorClient

    server.client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    server.db = server.client[os.environ["DB_NAME"]]
    assert server.db.name != "eternal_packaging"


@pytest.fixture(autouse=True)
async def clean(bind_motor):
    issue_mod._qty_reserve_hook = None
    await ensure_coa(server.db)
    await ensure_journal(server.db)
    for name in (
        "sales_invoices",
        "orders",
        "sales_receipts",
        "audit_logs",
        "accounting_periods",
        QTY_COLLECTION,
        "sales_invoice_qty_holds",
        JOURNAL_ENTRIES,
        JOURNAL_LINES,
        "app_settings",
    ):
        await server.db[name].delete_many({})
    yield
    issue_mod._qty_reserve_hook = None
    app.dependency_overrides.pop(current_user, None)


def _admin():
    app.dependency_overrides[current_user] = lambda: dict(ADMIN)


def _user(role: str, perms: dict, email: str = ""):
    app.dependency_overrides[current_user] = lambda: {
        "id": f"user-{role}-{uuid.uuid4().hex[:6]}",
        "email": email or f"{role}-{uuid.uuid4().hex[:6]}@example.com",
        "name": role,
        "role": role,
        "permissions_override": perms,
    }


async def _seed_po(qty: int = 100) -> dict:
    po_id = str(uuid.uuid4())
    doc = {
        "id": po_id,
        "po_number": f"PO/ISSUE/{uuid.uuid4().hex[:8].upper()}",
        "customer": "PT Uji Faktur",
        "outlet": "Outlet Uji",
        "recipient": "Sari",
        "address": "Jl. Uji",
        "delivery_zone": "Jakarta",
        "sales": "Vira",
        "line_items": [{
            "line_idx": 0,
            "line_id": "l0",
            "name": "Cup Uji",
            "quantity": qty,
            "unit": "pcs",
        }],
        "total_quantity": qty,
        "current_stage": 5,
        "status_label": "Penjadwalan",
        "payment_status": "Waiting",
        "packed_qty": 0,
        "loaded_qty": 0,
        "events": [{"stage": 0, "name": "Order Masuk"}],
        "shipments": [],
        "created_at": "2026-09-01T00:00:00Z",
    }
    await server.db.orders.insert_one(dict(doc))
    doc.pop("_id", None)
    return doc


async def _coa(prefix: str, name: str, account_type: str) -> dict:
    return await create_account(
        server.db,
        {"code": f"{prefix}{uuid.uuid4().hex[:6]}", "name": name, "account_type": account_type},
        user={"id": "t", "name": "t", "role": "admin"},
    )


async def _configure_settings():
    rec = await _coa("11", "Piutang Uji", "AREC")
    rev = await _coa("41", "Pendapatan Uji", "REVE")
    tax = await _coa("21", "PPN Uji", "OCLY")
    await server.db.app_settings.update_one(
        {"key": SETTINGS_KEY},
        {"$set": {
            "key": SETTINGS_KEY,
            "receivable_account_id": rec["id"],
            "revenue_account_id": rev["id"],
            "output_tax_account_id": tax["id"],
        }},
        upsert=True,
    )
    return rec, rev, tax


def _payload(po_id: str, *, key: str = "", qty: int = 1, price: int = 1000000, invoice_date: str = "2026-09-01") -> dict:
    body = {
        "po_id": po_id,
        "invoice_date": invoice_date,
        "payment_term_days": 0,
        "discount_amount": 0,
        "ppn_percent": 0,
        "notes": "",
        "lines": [{"line_idx": 0, "line_id": "l0", "quantity": qty, "unit_price": price}],
        "other_charges": [],
    }
    if key:
        body["idempotency_key"] = key
    return body


async def _journals(invoice_id: str) -> list:
    return await server.db[JOURNAL_ENTRIES].find(
        {"source_id": invoice_id},
        {"_id": 0},
    ).to_list(20)


async def _lines(journal_id: str) -> list:
    return await server.db[JOURNAL_LINES].find({"journal_id": journal_id}, {"_id": 0}).to_list(20)


async def _audit(operation_key: str) -> list:
    return await server.db.audit_logs.find({"operation_key": operation_key}, {"_id": 0}).to_list(10)


def _client():
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


async def test_save_posts_journal_and_tracking():
    po = await _seed_po()
    rec, rev, _tax = await _configure_settings()
    token = await ensure_tracking_token(server.db, po)
    result = await issue_sales_invoice(server.db, _payload(po["id"], key="save-1"), ADMIN, serialize_in_process=False)
    invoice = result["invoice"]
    assert invoice["financial_status"] == "finalized"
    assert invoice["issue_state"] == "issued"
    assert Decimal(str(invoice["grand_total"])) == Decimal("1000000.00")
    journals = await _journals(invoice["id"])
    primary = [j for j in journals if not j.get("reversal_of")]
    assert len(primary) == 1
    assert primary[0]["status"] == "posted"
    assert primary[0]["date"] == "2026-09-01"
    lines = await _lines(primary[0]["id"])
    debit = sum(Decimal(str(row["debit"])) for row in lines)
    credit = sum(Decimal(str(row["credit"])) for row in lines)
    assert debit == credit == Decimal("1000000.00")
    by_account = {row["account_id"]: row for row in lines}
    assert Decimal(str(by_account[rec["id"]]["debit"])) == Decimal("1000000.00")
    assert Decimal(str(by_account[rev["id"]]["credit"])) == Decimal("1000000.00")
    stored = await server.db.sales_invoices.find_one({"id": invoice["id"]}, {"_id": 0})
    assert customer_may_view_invoice(stored) is True
    _admin()
    async with _client() as client:
        listed = await client.get(f"/api/public/tracking/{token}/invoices")
        assert listed.status_code == 200, listed.text
        assert any(row.get("id") == invoice["id"] or row.get("invoice_id") == invoice["id"] for row in listed.json().get("invoices") or [])
    events = await _audit(f"invoice-issue:{invoice['id']}")
    assert len(events) == 1
    assert events[0]["action"] == AUDIT_ACTION_ISSUE
    assert "tracking_token" not in events[0]


async def test_create_permission_issues_without_post_grant():
    po = await _seed_po()
    await _configure_settings()
    _user("crm", {})
    async with _client() as client:
        res = await client.post("/api/sales-invoices/issue", json=_payload(po["id"], key="perm-create"))
        assert res.status_code == 200, res.text
        invoice = res.json()["invoice"]
        denied = await client.post(f"/api/sales-invoices/{invoice['id']}/post-accounting")
        assert denied.status_code == 403
    stored = await server.db.sales_invoices.find_one({"id": invoice["id"]}, {"_id": 0})
    assert stored["issue_state"] == "issued"
    assert len(await _journals(invoice["id"])) == 1


async def test_without_create_permission_writes_nothing():
    po = await _seed_po()
    await _configure_settings()
    before_inv = await server.db.sales_invoices.count_documents({})
    before_j = await server.db[JOURNAL_ENTRIES].count_documents({})
    _user("crm", {
        "sales.invoice.create": False,
        "sales.invoice.edit": False,
        "sales_order.create": False,
        "po.edit": False,
    })
    async with _client() as client:
        res = await client.post("/api/sales-invoices/issue", json=_payload(po["id"], key="no-create"))
        assert res.status_code == 403
    assert await server.db.sales_invoices.count_documents({}) == before_inv
    assert await server.db[JOURNAL_ENTRIES].count_documents({}) == before_j


async def test_same_key_same_payload_is_one_invoice():
    po = await _seed_po()
    await _configure_settings()
    body = _payload(po["id"], key="same-key")
    first = await issue_sales_invoice(server.db, body, ADMIN, serialize_in_process=False)
    second = await issue_sales_invoice(server.db, body, ADMIN, serialize_in_process=False)
    assert first["invoice"]["id"] == second["invoice"]["id"]
    assert await server.db.sales_invoices.count_documents({"po_id": po["id"]}) == 1
    assert len(await _journals(first["invoice"]["id"])) == 1
    assert len(await _audit(f"invoice-issue:{first['invoice']['id']}")) == 1


async def test_same_key_different_payload_conflicts():
    po = await _seed_po()
    await _configure_settings()
    first = await issue_sales_invoice(
        server.db, _payload(po["id"], key="conflict-key", price=1000000), ADMIN, serialize_in_process=False
    )
    with pytest.raises(HTTPException) as exc:
        await issue_sales_invoice(
            server.db, _payload(po["id"], key="conflict-key", price=500000), ADMIN, serialize_in_process=False
        )
    assert exc.value.status_code == 409
    detail = exc.value.detail
    assert isinstance(detail, dict) and detail.get("code") == "idempotency_conflict"
    assert await server.db.sales_invoices.count_documents({"idempotency_key": "conflict-key"}) == 1
    assert Decimal(str(first["invoice"]["grand_total"])) == Decimal("1000000.00")


async def test_two_keys_cannot_exceed_remaining_qty():
    po = await _seed_po(qty=10)
    await _configure_settings()
    first_in = asyncio.Event()
    release = asyncio.Event()
    seen = {"n": 0}

    async def hook(_hold_id, _line):
        seen["n"] += 1
        if seen["n"] == 1:
            first_in.set()
            await release.wait()
        else:
            release.set()

    issue_mod._qty_reserve_hook = hook
    tasks = [
        asyncio.create_task(issue_sales_invoice(
            server.db, _payload(po["id"], key="qty-a", qty=6, price=1000), ADMIN, serialize_in_process=False
        )),
        asyncio.create_task(issue_sales_invoice(
            server.db, _payload(po["id"], key="qty-b", qty=6, price=1000), ADMIN, serialize_in_process=False
        )),
    ]
    done, pending = await asyncio.wait(tasks, timeout=15)
    if pending:
        for task in pending:
            task.cancel()
        raise AssertionError(f"qty race did not finish, hook entries={seen['n']}")
    results = []
    for task in done:
        exc = task.exception()
        results.append(exc if exc is not None else task.result())
    successes = [row for row in results if not isinstance(row, Exception)]
    failures = [row for row in results if isinstance(row, Exception)]
    assert len(successes) == 1
    assert len(failures) == 1
    assert isinstance(failures[0], HTTPException)
    counter = await server.db[QTY_COLLECTION].find_one({"_id": f"{po['id']}:0"})
    assert int(counter["reserved"]) == 6
    assert int(counter["reserved"]) <= int(counter["cap"])
    assert await server.db.sales_invoices.count_documents({"po_id": po["id"]}) == 1


async def test_invalid_mapping_period_and_inactive_account_are_not_success():
    po = await _seed_po()
    with pytest.raises(HTTPException) as missing:
        await issue_sales_invoice(server.db, _payload(po["id"], key="no-map"), ADMIN, serialize_in_process=False)
    assert missing.value.status_code in (400, 409)
    assert await server.db.sales_invoices.count_documents({}) == 0
    assert await server.db[JOURNAL_ENTRIES].count_documents({}) == 0

    rec, _rev, _tax = await _configure_settings()
    await server.db.ledger_accounts.update_one({"id": rec["id"]}, {"$set": {"active": False}})
    with pytest.raises(HTTPException) as inactive:
        await issue_sales_invoice(server.db, _payload(po["id"], key="inactive"), ADMIN, serialize_in_process=False)
    assert inactive.value.status_code == 400
    assert await server.db.sales_invoices.count_documents({}) == 0

    await server.db.ledger_accounts.update_one({"id": rec["id"]}, {"$set": {"active": True}})
    await close_period(server.db, "2026-08", user=ADMIN, reason="tutup uji")
    with pytest.raises(HTTPException) as closed:
        await issue_sales_invoice(
            server.db, _payload(po["id"], key="closed", invoice_date="2026-08-15"), ADMIN, serialize_in_process=False
        )
    assert closed.value.status_code == 409
    assert await server.db.sales_invoices.count_documents({}) == 0
    assert await server.db[JOURNAL_ENTRIES].count_documents({}) == 0


async def _issuing_stopped_after_insert(po_id: str, key: str) -> dict:
    async def stop(_invoice):
        raise HTTPException(409, "berhenti sesudah simpan")

    with pytest.raises(HTTPException):
        await issue_sales_invoice(
            server.db,
            _payload(po_id, key=key),
            ADMIN,
            serialize_in_process=False,
            hooks={"after_insert": stop},
        )
    stored = await server.db.sales_invoices.find_one({"idempotency_key": key}, {"_id": 0})
    assert stored["issue_state"] == "issuing"
    assert stored.get("issue_journal_plan", {}).get("lines")
    assert await _journals(stored["id"]) == []
    return stored


async def test_partial_journal_recovers_without_false_posted():
    po = await _seed_po()
    await _configure_settings()
    stored = await _issuing_stopped_after_insert(po["id"], "partial-key")
    header_id = str(uuid.uuid4())
    await server.db[JOURNAL_ENTRIES].insert_one({
        "id": header_id,
        "number": f"JRN-PART-{uuid.uuid4().hex[:8]}",
        "date": "2026-09-01",
        "source_type": SOURCE_TYPE,
        "source_id": stored["id"],
        "description": "header tanpa baris",
        "status": "posted",
        "reversal_of": None,
        "posted_at": "2026-09-01T00:00:00+00:00",
        "posted_by": "uji",
        "created_at": "2026-09-01T00:00:00+00:00",
    })
    resumed = await continue_sales_invoice_issue(server.db, stored, ADMIN, operation_id="partial-key")
    assert resumed["issued"] is True
    lines = await _lines(header_id)
    plan_lines = stored["issue_journal_plan"]["lines"]
    assert len(lines) == len(plan_lines)
    assert len(await _journals(stored["id"])) == 1
    saved = await server.db.sales_invoices.find_one({"id": stored["id"]}, {"_id": 0})
    assert saved["issue_state"] == "issued"


async def test_closed_period_blocks_partial_recovery():
    po = await _seed_po()
    await _configure_settings()
    stored = await _issuing_stopped_after_insert(po["id"], "closed-recover")
    header_id = str(uuid.uuid4())
    await server.db[JOURNAL_ENTRIES].insert_one({
        "id": header_id,
        "number": f"JRN-LOCK-{uuid.uuid4().hex[:8]}",
        "date": "2026-09-01",
        "source_type": SOURCE_TYPE,
        "source_id": stored["id"],
        "description": "header tanpa baris",
        "status": "posted",
        "reversal_of": None,
        "posted_at": "2026-09-01T00:00:00+00:00",
        "posted_by": "uji",
        "created_at": "2026-09-01T00:00:00+00:00",
    })
    await close_period(server.db, "2026-09", user=ADMIN, reason="tutup uji")
    with pytest.raises(HTTPException) as exc:
        await continue_sales_invoice_issue(server.db, stored, ADMIN, operation_id="closed-recover")
    assert exc.value.status_code == 409
    assert await _lines(header_id) == []
    saved = await server.db.sales_invoices.find_one({"id": stored["id"]}, {"_id": 0})
    assert saved["issue_state"] == "issuing"


async def test_delete_posted_invoice_hides_tracking_and_pdf():
    po = await _seed_po()
    await _configure_settings()
    token = await ensure_tracking_token(server.db, po)
    issued = await issue_sales_invoice(server.db, _payload(po["id"], key="del-1"), ADMIN, serialize_in_process=False)
    invoice = issued["invoice"]
    deleted = await remove_issued_sales_invoice(server.db, invoice["id"], ADMIN, operation_key="del-op-1")
    assert deleted["deleted"] is True
    stored = await server.db.sales_invoices.find_one({"id": invoice["id"]}, {"_id": 0})
    assert stored["removed_from_active"] is True
    assert stored["financial_status"] == "cancelled"
    assert stored.get("accounting_reversal_journal_id")
    reversals = [j for j in await _journals(invoice["id"]) if j.get("reversal_of")]
    assert len(reversals) == 1
    primary = [j for j in await _journals(invoice["id"]) if not j.get("reversal_of")]
    assert primary[0]["status"] == "reversed"
    assert await _lines(primary[0]["id"])
    assert await _lines(reversals[0]["id"])
    active = await list_sales_invoices(server.db, po_id=po["id"])
    assert all(row["id"] != invoice["id"] for row in active)
    assert customer_may_view_invoice(stored) is False
    pwa_visible = await server.db.sales_invoices.count_documents({
        "id": invoice["id"],
        "removed_from_active": {"$ne": True},
        "issue_state": {"$ne": "issuing"},
    })
    assert pwa_visible == 0
    _admin()
    async with _client() as client:
        listed = await client.get(f"/api/public/tracking/{token}/invoices")
        ids = [row.get("id") or row.get("invoice_id") for row in listed.json().get("invoices") or []]
        assert invoice["id"] not in ids
        detail = await client.get(f"/api/public/tracking/{token}/invoices/{invoice['id']}")
        pdf = await client.get(f"/api/public/tracking/{token}/invoices/{invoice['id']}/pdf")
        assert detail.status_code == 404
        assert pdf.status_code == 404
    events = await _audit(f"invoice-delete:{invoice['id']}")
    assert len(events) == 1
    assert events[0]["action"] == AUDIT_ACTION_DELETE
    assert events[0]["after"]["reason"] == DELETE_REASON


async def test_delete_permission_without_general_reverse():
    po = await _seed_po()
    await _configure_settings()
    issued = await issue_sales_invoice(server.db, _payload(po["id"], key="del-perm"), ADMIN, serialize_in_process=False)
    invoice_id = issued["invoice"]["id"]
    _user("crm", {
        "sales.invoice.create": False,
        "sales_order.create": False,
        "po.edit": False,
        "sales.invoice.delete": True,
        "sales_accounting.reverse": False,
        "sales_accounting.post": False,
    })
    async with _client() as client:
        blocked = await client.post(
            f"/api/sales-invoices/{invoice_id}/reverse-accounting",
            json={"reason": "manual tidak diizinkan"},
        )
        assert blocked.status_code == 403
        ok = await client.delete(f"/api/sales-invoices/{invoice_id}", params={"operation_key": "unified-del"})
        assert ok.status_code == 200, ok.text
    stored = await server.db.sales_invoices.find_one({"id": invoice_id}, {"_id": 0})
    assert stored["removed_from_active"] is True
    assert len([j for j in await _journals(invoice_id) if j.get("reversal_of")]) == 1


async def test_without_delete_permission_changes_nothing():
    po = await _seed_po()
    await _configure_settings()
    issued = await issue_sales_invoice(server.db, _payload(po["id"], key="no-del"), ADMIN, serialize_in_process=False)
    invoice_id = issued["invoice"]["id"]
    before = await server.db.sales_invoices.find_one({"id": invoice_id}, {"_id": 0})
    journal_count = await server.db[JOURNAL_ENTRIES].count_documents({})
    _user("crm", {
        "sales.invoice.delete": False,
        "sales.invoice.create": False,
        "sales_order.create": False,
        "po.edit": False,
    })
    async with _client() as client:
        res = await client.delete(f"/api/sales-invoices/{invoice_id}")
        assert res.status_code == 403
    after = await server.db.sales_invoices.find_one({"id": invoice_id}, {"_id": 0})
    assert after["financial_status"] == before["financial_status"]
    assert after.get("removed_from_active") in (None, False)
    assert await server.db[JOURNAL_ENTRIES].count_documents({}) == journal_count


async def _assert_delete_forbidden(perms: dict):
    po = await _seed_po()
    await _configure_settings()
    token = await ensure_tracking_token(server.db, po)
    issued = await issue_sales_invoice(
        server.db, _payload(po["id"], key=f"deny-{uuid.uuid4().hex[:8]}"), ADMIN, serialize_in_process=False
    )
    invoice_id = issued["invoice"]["id"]
    before = await server.db.sales_invoices.find_one({"id": invoice_id}, {"_id": 0})
    journals = await server.db[JOURNAL_ENTRIES].count_documents({})
    _user("crm", perms)
    async with _client() as client:
        res = await client.delete(f"/api/sales-invoices/{invoice_id}", params={"operation_key": "denied"})
        assert res.status_code == 403
        listed = await client.get(f"/api/public/tracking/{token}/invoices")
    after = await server.db.sales_invoices.find_one({"id": invoice_id}, {"_id": 0})
    assert after["updated_at"] == before["updated_at"]
    assert after["financial_status"] == "finalized"
    assert after["issue_state"] == "issued"
    assert after.get("removed_from_active") in (None, False)
    assert customer_may_view_invoice(after) is True
    assert await server.db[JOURNAL_ENTRIES].count_documents({}) == journals
    ids = [row.get("id") for row in listed.json().get("invoices") or []]
    assert invoice_id in ids


async def test_create_only_cannot_delete_issued_invoice():
    await _assert_delete_forbidden({
        "sales.invoice.delete": False,
        "po.edit": False,
        "sales_accounting.reverse": False,
        "sales_accounting.post": False,
    })


async def test_po_edit_only_cannot_delete_issued_invoice():
    await _assert_delete_forbidden({
        "sales.invoice.delete": False,
        "sales.invoice.create": False,
        "sales.invoice.edit": False,
        "sales_order.create": False,
        "po.edit": True,
        "sales_accounting.reverse": False,
        "sales_accounting.post": False,
    })


async def test_without_any_invoice_permission_cannot_delete():
    await _assert_delete_forbidden({
        "sales.invoice.delete": False,
        "sales.invoice.create": False,
        "sales.invoice.edit": False,
        "sales_order.create": False,
        "po.edit": False,
        "sales_accounting.reverse": False,
        "sales_accounting.post": False,
    })


async def test_double_delete_one_reversal_and_one_audit():
    po = await _seed_po()
    await _configure_settings()
    issued = await issue_sales_invoice(server.db, _payload(po["id"], key="twice"), ADMIN, serialize_in_process=False)
    invoice_id = issued["invoice"]["id"]
    entered = asyncio.Event()
    release = asyncio.Event()

    async def hold(_invoice):
        entered.set()
        await release.wait()

    async def first():
        return await remove_issued_sales_invoice(
            server.db, invoice_id, ADMIN, operation_key="twice-op", hooks={"before_reverse": hold}
        )

    task = asyncio.create_task(first())
    await entered.wait()
    with pytest.raises(HTTPException) as busy:
        await remove_issued_sales_invoice(server.db, invoice_id, ADMIN, operation_key="other-op")
    assert busy.value.status_code == 409
    release.set()
    first_result = await task
    assert first_result["deleted"] is True
    again = await remove_issued_sales_invoice(server.db, invoice_id, ADMIN, operation_key="twice-op")
    assert again["deleted"] is True
    assert len([j for j in await _journals(invoice_id) if j.get("reversal_of")]) == 1
    assert len(await _audit(f"invoice-delete:{invoice_id}")) == 1


async def test_delete_during_posting_does_not_leave_orphan_journal():
    po = await _seed_po()
    await _configure_settings()
    entered = asyncio.Event()
    release = asyncio.Event()

    async def hold(invoice):
        entered.set()
        await release.wait()

    async def issue():
        return await issue_sales_invoice(
            server.db,
            _payload(po["id"], key="race-issue"),
            ADMIN,
            serialize_in_process=False,
            hooks={"after_fence_before_journal": hold},
        )

    task = asyncio.create_task(issue())
    await entered.wait()
    stored = await server.db.sales_invoices.find_one({"idempotency_key": "race-issue"}, {"_id": 0})
    with pytest.raises(HTTPException) as exc:
        await remove_issued_sales_invoice(server.db, stored["id"], ADMIN, operation_key="race-del")
    assert exc.value.status_code == 409
    release.set()
    issued = await task
    assert issued["issued"] is True
    saved = await server.db.sales_invoices.find_one({"id": stored["id"]}, {"_id": 0})
    assert saved["issue_state"] == "issued"
    assert saved.get("removed_from_active") in (None, False)
    assert len([j for j in await _journals(stored["id"]) if not j.get("reversal_of")]) == 1


async def test_delete_during_receipt_does_not_drop_payment():
    po = await _seed_po()
    await _configure_settings()
    issued = await issue_sales_invoice(server.db, _payload(po["id"], key="race-rcpt"), ADMIN, serialize_in_process=False)
    invoice_id = issued["invoice"]["id"]
    started = asyncio.Event()
    release = asyncio.Event()
    real_coll = server.db.sales_receipts

    async def wrapped(doc, *args, **kwargs):
        started.set()
        await release.wait()
        return await real_coll.insert_one(doc, *args, **kwargs)

    class _ReceiptCollection:
        def insert_one(self, doc, *args, **kwargs):
            return wrapped(doc, *args, **kwargs)

        def __getattr__(self, name):
            return getattr(real_coll, name)

    server.db.sales_receipts = _ReceiptCollection()
    try:
        receipt_task = asyncio.create_task(create_sales_receipt(
            server.db,
            {"invoice_id": invoice_id, "amount": 100000, "receipt_date": "2026-09-02"},
            ADMIN,
        ))
        await asyncio.wait_for(started.wait(), timeout=15)
        with pytest.raises(HTTPException) as exc:
            await remove_issued_sales_invoice(server.db, invoice_id, ADMIN, operation_key="rcpt-del")
        assert exc.value.status_code == 409
        release.set()
        receipt = await receipt_task
    finally:
        server.db.sales_receipts = real_coll
    stored = await server.db.sales_invoices.find_one({"id": invoice_id}, {"_id": 0})
    assert stored.get("removed_from_active") in (None, False)
    assert stored["financial_status"] == "finalized"
    assert receipt["id"]
    assert await server.db.sales_receipts.count_documents({"invoice_id": invoice_id, "financial_status": "active"}) == 1
    assert [j for j in await _journals(invoice_id) if j.get("reversal_of")] == []


async def test_active_customer_credit_rejects_delete_and_leaves_credit():
    po = await _seed_po()
    await _configure_settings()
    issued = await issue_sales_invoice(server.db, _payload(po["id"], key="credit-block"), ADMIN, serialize_in_process=False)
    invoice_id = issued["invoice"]["id"]
    await server.db.sales_customer_credit_applications.insert_one({
        "id": "credit-block-1",
        "invoice_id": invoice_id,
        "financial_status": "active",
        "status": "posted",
        "amount": 15000,
    })
    journals_before = await server.db[JOURNAL_ENTRIES].count_documents({})
    with pytest.raises(HTTPException) as exc:
        await remove_issued_sales_invoice(server.db, invoice_id, ADMIN, operation_key="credit-del")
    assert exc.value.status_code == 409
    detail = exc.value.detail
    assert isinstance(detail, dict)
    assert detail.get("blockers")
    assert "saldo" in str(detail.get("message") or "").lower() or "penerimaan" in str(detail.get("message") or "").lower()
    stored = await server.db.sales_invoices.find_one({"id": invoice_id}, {"_id": 0})
    assert stored.get("removed_from_active") in (None, False)
    assert stored["financial_status"] == "finalized"
    assert [j for j in await _journals(invoice_id) if j.get("reversal_of")] == []
    assert await server.db[JOURNAL_ENTRIES].count_documents({}) == journals_before
    credit = await server.db.sales_customer_credit_applications.find_one({"id": "credit-block-1"}, {"_id": 0})
    assert credit["financial_status"] == "active"
    assert credit["status"] == "posted"
    assert credit["amount"] == 15000


async def test_active_receipt_rejects_delete_and_leaves_payment():
    po = await _seed_po()
    await _configure_settings()
    issued = await issue_sales_invoice(server.db, _payload(po["id"], key="paid"), ADMIN, serialize_in_process=False)
    invoice_id = issued["invoice"]["id"]
    receipt = await create_sales_receipt(
        server.db,
        {"invoice_id": invoice_id, "amount": 250000, "receipt_date": "2026-09-02"},
        ADMIN,
    )
    with pytest.raises(HTTPException) as exc:
        await remove_issued_sales_invoice(server.db, invoice_id, ADMIN, operation_key="paid-del")
    assert exc.value.status_code == 409
    detail = exc.value.detail
    assert isinstance(detail, dict)
    assert detail.get("blockers")
    assert "penerimaan" in str(detail.get("message") or "").lower() or "saldo" in str(detail.get("message") or "").lower()
    stored = await server.db.sales_invoices.find_one({"id": invoice_id}, {"_id": 0})
    assert stored.get("removed_from_active") in (None, False)
    assert stored["financial_status"] == "finalized"
    still = await server.db.sales_receipts.find_one({"id": receipt["id"]}, {"_id": 0})
    assert still["financial_status"] == "active"
    assert still["amount"] == receipt["amount"]


async def test_retry_after_reversal_does_not_reverse_again():
    po = await _seed_po()
    await _configure_settings()
    issued = await issue_sales_invoice(server.db, _payload(po["id"], key="rev-crash"), ADMIN, serialize_in_process=False)
    invoice_id = issued["invoice"]["id"]
    primary = [j for j in await _journals(invoice_id) if not j.get("reversal_of")][0]
    await reverse_journal(
        server.db,
        primary["id"],
        source_type=SOURCE_REVERSAL,
        source_id=invoice_id,
        posted_by=ADMIN,
        description="reversal terputus sebelum penanda hapus",
    )
    assert await server.db.sales_invoices.find_one({"id": invoice_id}, {"removed_from_active": 1})
    result = await remove_issued_sales_invoice(server.db, invoice_id, ADMIN, operation_key="rev-crash-op")
    assert result["deleted"] is True
    stored = await server.db.sales_invoices.find_one({"id": invoice_id}, {"_id": 0})
    assert stored["removed_from_active"] is True
    assert len([j for j in await _journals(invoice_id) if j.get("reversal_of")]) == 1


async def test_audit_recovers_without_duplicate():
    po = await _seed_po()
    await _configure_settings()
    issued = await issue_sales_invoice(server.db, _payload(po["id"], key="audit-crash"), ADMIN, serialize_in_process=False)
    invoice_id = issued["invoice"]["id"]
    await remove_issued_sales_invoice(server.db, invoice_id, ADMIN, operation_key="audit-op")
    await server.db.audit_logs.delete_many({"operation_key": f"invoice-delete:{invoice_id}"})
    again = await remove_issued_sales_invoice(server.db, invoice_id, ADMIN, operation_key="audit-op")
    third = await remove_issued_sales_invoice(server.db, invoice_id, ADMIN, operation_key="audit-op")
    events = await _audit(f"invoice-delete:{invoice_id}")
    assert len(events) == 1
    assert again["audit_id"] == third["audit_id"] == events[0]["id"]
    assert events[0]["after"]["invoice_id"] == invoice_id
    assert events[0]["after"]["invoice_number"]


async def test_other_po_token_cannot_read_invoice():
    po = await _seed_po()
    other = await _seed_po()
    await _configure_settings()
    token_other = await ensure_tracking_token(server.db, other)
    issued = await issue_sales_invoice(server.db, _payload(po["id"], key="scope"), ADMIN, serialize_in_process=False)
    invoice_id = issued["invoice"]["id"]
    _admin()
    async with _client() as client:
        listed = await client.get(f"/api/public/tracking/{token_other}/invoices")
        ids = [row.get("id") or row.get("invoice_id") for row in listed.json().get("invoices") or []]
        assert invoice_id not in ids
        detail = await client.get(f"/api/public/tracking/{token_other}/invoices/{invoice_id}")
        assert detail.status_code == 404


async def test_legacy_invoice_is_not_mass_posted():
    po = await _seed_po()
    await _configure_settings()
    _admin()
    async with _client() as client:
        created = await client.post("/api/sales-invoices", json=_payload(po["id"]))
        assert created.status_code == 200, created.text
        invoice_id = created.json()["invoice"]["id"]
        finalized = await client.post(f"/api/sales-invoices/{invoice_id}/finalize")
        assert finalized.status_code == 200, finalized.text
    stored = await server.db.sales_invoices.find_one({"id": invoice_id}, {"_id": 0})
    assert stored.get("issue_state") in (None, "")
    assert stored["financial_status"] == "finalized"
    assert await _journals(invoice_id) == []
    assert customer_may_view_invoice(stored) is True
    other = await _seed_po()
    await issue_sales_invoice(server.db, _payload(other["id"], key="other-new"), ADMIN, serialize_in_process=False)
    assert await _journals(invoice_id) == []
    active = await list_sales_invoices(server.db, po_id=po["id"])
    assert any(row["id"] == invoice_id for row in active)


async def test_reads_do_not_write_business_documents():
    po = await _seed_po()
    await _configure_settings()
    token = await ensure_tracking_token(server.db, po)
    issued = await issue_sales_invoice(server.db, _payload(po["id"], key="reads"), ADMIN, serialize_in_process=False)
    invoice_id = issued["invoice"]["id"]
    before_inv = await server.db.sales_invoices.find_one({"id": invoice_id}, {"_id": 0, "updated_at": 1})
    before_j = await server.db[JOURNAL_ENTRIES].count_documents({})
    before_audit = await server.db.audit_logs.count_documents({})
    _admin()
    async with _client() as client:
        detail = await client.get(f"/api/sales-invoices/{invoice_id}")
        tracking = await client.get(f"/api/public/tracking/{token}/invoices/{invoice_id}")
        pdf = await client.get(f"/api/public/tracking/{token}/invoices/{invoice_id}/pdf")
        audit = await client.get("/api/admin/audit-logs")
        assert detail.status_code == 200
        assert tracking.status_code == 200
        assert pdf.status_code == 200
        assert audit.status_code == 200
    after_inv = await server.db.sales_invoices.find_one({"id": invoice_id}, {"_id": 0, "updated_at": 1})
    assert after_inv["updated_at"] == before_inv["updated_at"]
    assert await server.db[JOURNAL_ENTRIES].count_documents({}) == before_j
    assert await server.db.audit_logs.count_documents({}) == before_audit


async def test_complete_journal_can_finish_issue_marker_after_period_close():
    po = await _seed_po()
    await _configure_settings()
    issued = await issue_sales_invoice(server.db, _payload(po["id"], key="marker"), ADMIN, serialize_in_process=False)
    invoice_id = issued["invoice"]["id"]
    await server.db.sales_invoices.update_one(
        {"id": invoice_id},
        {"$set": {"issue_state": "issuing"}, "$unset": {"issue_completed_at": ""}},
    )
    await close_period(server.db, "2026-09", user=ADMIN, reason="tutup uji")
    before_lines = await server.db[JOURNAL_LINES].count_documents({})
    resumed = await continue_sales_invoice_issue(
        server.db,
        await server.db.sales_invoices.find_one({"id": invoice_id}, {"_id": 0}),
        ADMIN,
        operation_id="marker",
    )
    assert resumed["issued"] is True
    assert await server.db[JOURNAL_LINES].count_documents({}) == before_lines
    assert len(await _journals(invoice_id)) == 1
