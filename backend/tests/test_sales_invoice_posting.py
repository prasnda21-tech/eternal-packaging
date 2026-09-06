"""Sales invoice explicit accounting posting — Phase 1."""
from __future__ import annotations

import asyncio
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
os.environ["DB_NAME"] = f"eternal_packaging_sales_invoice_posting_test_{_worker}"
os.environ.setdefault("JWT_SECRET", "test-secret-key-12345678901234567890")
os.environ.setdefault("CORS_ORIGINS", "*")

import server
from permissions import ALL_PERMISSIONS, ROLE_DEFAULT_PERMISSIONS
from server import app, current_user
from services.chart_of_accounts import create_account, ensure_indexes as ensure_coa
from services.journal_core import JOURNAL_ENTRIES, JOURNAL_LINES, ensure_indexes as ensure_journal
from services.sales_accounting_settings import SETTINGS_KEY
from fastapi import HTTPException

from services.sales_invoice_posting import (
    CANCEL_CLAIM_MESSAGE,
    CANCEL_POSTED_MESSAGE,
    CLAIM_FIELD,
    SOURCE_TYPE,
    _write_source_journal,
    acquire_posting_claim,
    post_sales_invoice_accounting,
    posting_claim_is_active,
)
from services.sales_invoices import cancel_sales_invoice, get_sales_invoice_detail

POST_USER = {
    "id": "admin-posting",
    "email": "admin@eternalpackaging.com",
    "name": "Admin Posting",
    "role": "admin",
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
async def indexes(bind_motor):
    await ensure_coa(server.db)
    await ensure_journal(server.db)
    await server.db.app_settings.delete_many({"key": SETTINGS_KEY})
    yield
    await server.db.app_settings.delete_many({"key": SETTINGS_KEY})


def _admin():
    app.dependency_overrides[current_user] = lambda: {
        "id": "admin-posting",
        "email": "admin@eternalpackaging.com",
        "name": "Admin Posting",
        "role": "admin",
    }


def _user(role: str, perms: dict):
    app.dependency_overrides[current_user] = lambda: {
        "id": f"user-{role}",
        "email": f"{role}@example.com",
        "name": role,
        "role": role,
        "permissions_override": perms,
    }


async def _seed_po(**extra) -> dict:
    po_id = str(uuid.uuid4())
    po_number = f"PO/POST/{uuid.uuid4().hex[:8].upper()}"
    doc = {
        "id": po_id,
        "po_number": po_number,
        "customer": "PT Posting",
        "outlet": "Outlet Post",
        "recipient": "Budi",
        "address": "Jl. Test",
        "delivery_zone": "Jakarta",
        "sales": "Vira",
        "line_items": [{"line_idx": 0, "line_id": "l0", "name": "Cup Post", "quantity": 100, "unit": "pcs"}],
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
        "lines": [{"line_idx": 0, "quantity": extra.pop("quantity", 100), "unit_price": extra.pop("unit_price", 1000)}],
    }
    payload.update(extra)
    res = await client.post("/api/sales-invoices", json=payload)
    assert res.status_code == 200, res.text
    return res.json()["invoice"]


async def _coa(code_prefix: str, name: str, account_type: str) -> dict:
    suffix = uuid.uuid4().hex[:6]
    return await create_account(
        server.db,
        {"code": f"{code_prefix}{suffix}", "name": name, "account_type": account_type},
        user={"id": "t", "name": "t", "role": "admin"},
    )


async def _configure_settings(rec=None, rev=None, tax=None):
    rec = rec or await _coa("11", "Piutang Post", "AREC")
    rev = rev or await _coa("41", "Pendapatan Post", "REVE")
    tax = tax or await _coa("21", "PPN Post", "OCLY")
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


async def _finalize(client, invoice_id: str) -> dict:
    res = await client.post(f"/api/sales-invoices/{invoice_id}/finalize")
    assert res.status_code == 200, res.text
    return res.json()["invoice"]


async def _source_journals(invoice_id: str):
    return await server.db.journal_entries.find(
        {"source_type": SOURCE_TYPE, "source_id": invoice_id},
        {"_id": 0},
    ).to_list(20)


@pytest.mark.anyio
async def test_permission_not_auto_granted():
    assert "sales_accounting.post" in ALL_PERMISSIONS
    for role, perms in ROLE_DEFAULT_PERMISSIONS.items():
        if role == "admin":
            continue
        assert "sales_accounting.post" not in perms


@pytest.mark.anyio
async def test_draft_and_cancelled_rejected_finalized_accepted():
    po = await _seed_po()
    rec, rev, tax = await _configure_settings()
    _admin()
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            draft = await _create_invoice(client, po["id"], ppn_percent=11)
            before = await server.db.journal_entries.count_documents({})
            bad_draft = await client.post(f"/api/sales-invoices/{draft['id']}/post-accounting")
            assert bad_draft.status_code == 409
            assert await server.db.journal_entries.count_documents({}) == before

            other_po = await _seed_po()
            other = await _create_invoice(client, other_po["id"], quantity=10, unit_price=1000)
            await _finalize(client, other["id"])
            await client.post(f"/api/sales-invoices/{other['id']}/cancel")
            bad_cancel = await client.post(f"/api/sales-invoices/{other['id']}/post-accounting")
            assert bad_cancel.status_code == 409
            assert await server.db.journal_entries.count_documents({}) == before

            fin = await _finalize(client, draft["id"])
            ok = await client.post(f"/api/sales-invoices/{fin['id']}/post-accounting")
            assert ok.status_code == 200, ok.text
            posted = ok.json()["invoice"]
            assert posted["accounting_status"] == "posted"
            assert posted["accounting_posted"] is True
            journals = await _source_journals(fin["id"])
            assert len(journals) == 1
            assert journals[0]["source_type"] == SOURCE_TYPE
            assert journals[0]["source_id"] == fin["id"]
            assert journals[0]["date"] == "2026-09-01"
            lines = await server.db.journal_lines.find({"journal_id": journals[0]["id"]}, {"_id": 0}).to_list(10)
            by_account = {ln["account_id"]: ln for ln in lines}
            assert Decimal(str(by_account[rec["id"]]["debit"])) == Decimal(str(fin["grand_total"]))
            assert Decimal(str(by_account[rev["id"]]["credit"])) == Decimal(str(fin["after_discount"]))
            assert Decimal(str(by_account[tax["id"]]["credit"])) == Decimal(str(fin["ppn_amount"]))
            debit = sum(Decimal(str(ln["debit"])) for ln in lines)
            credit = sum(Decimal(str(ln["credit"])) for ln in lines)
            assert debit == credit
    finally:
        app.dependency_overrides.pop(current_user, None)


@pytest.mark.anyio
async def test_missing_and_invalid_mappings_rejected():
    po = await _seed_po()
    _admin()
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            inv = await _create_invoice(client, po["id"])
            await _finalize(client, inv["id"])
            before = await server.db.journal_entries.count_documents({})
            missing = await client.post(f"/api/sales-invoices/{inv['id']}/post-accounting")
            assert missing.status_code == 400
            assert await server.db.journal_entries.count_documents({}) == before

            asset = await _coa("12", "Bukan Piutang", "BANK")
            rev = await _coa("41", "Pendapatan", "REVE")
            tax = await _coa("21", "PPN", "OCLY")
            await server.db.app_settings.update_one(
                {"key": SETTINGS_KEY},
                {"$set": {
                    "key": SETTINGS_KEY,
                    "receivable_account_id": asset["id"],
                    "revenue_account_id": rev["id"],
                    "output_tax_account_id": tax["id"],
                }},
                upsert=True,
            )
            bad_arec = await client.post(f"/api/sales-invoices/{inv['id']}/post-accounting")
            assert bad_arec.status_code == 400
            assert await server.db.journal_entries.count_documents({}) == before

            rec = await _coa("11", "Piutang", "AREC")
            not_rev = await _coa("51", "Beban", "EXPS")
            await server.db.app_settings.update_one(
                {"key": SETTINGS_KEY},
                {"$set": {"receivable_account_id": rec["id"], "revenue_account_id": not_rev["id"], "output_tax_account_id": tax["id"]}},
            )
            bad_reve = await client.post(f"/api/sales-invoices/{inv['id']}/post-accounting")
            assert bad_reve.status_code == 400

            await server.db.app_settings.update_one(
                {"key": SETTINGS_KEY},
                {"$set": {"receivable_account_id": rec["id"], "revenue_account_id": rev["id"], "output_tax_account_id": rec["id"]}},
            )
            bad_tax = await client.post(f"/api/sales-invoices/{inv['id']}/post-accounting")
            assert bad_tax.status_code == 400
            assert await server.db.journal_entries.count_documents({}) == before
    finally:
        app.dependency_overrides.pop(current_user, None)


@pytest.mark.anyio
async def test_amount_eligibility_rejected():
    po = await _seed_po()
    await _configure_settings()
    _admin()
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            before = await server.db.journal_entries.count_documents({})

            async def _insert_finalized(**fields):
                inv_id = str(uuid.uuid4())
                doc = {
                    "id": inv_id,
                    "invoice_number": f"INV/POST/{inv_id[:8]}",
                    "po_id": po["id"],
                    "invoice_date": "2026-09-01",
                    "financial_status": "finalized",
                    "lines": [{"line_idx": 0, "quantity": 1, "unit_price": 100}],
                    "subtotal": 100.0,
                    "discount_amount": 0.0,
                    "after_discount": 100.0,
                    "ppn_percent": 0.0,
                    "ppn_amount": 0.0,
                    "grand_total": 100.0,
                }
                doc.update(fields)
                await server.db.sales_invoices.insert_one(doc)
                return inv_id

            zero = await _insert_finalized(grand_total=0.0, after_discount=0.0, ppn_amount=0.0)
            assert (await client.post(f"/api/sales-invoices/{zero}/post-accounting")).status_code == 400

            neg_grand = await _insert_finalized(grand_total=-10.0, after_discount=-10.0, ppn_amount=0.0)
            assert (await client.post(f"/api/sales-invoices/{neg_grand}/post-accounting")).status_code == 400

            neg_after = await _insert_finalized(grand_total=100.0, after_discount=-5.0, ppn_amount=105.0)
            assert (await client.post(f"/api/sales-invoices/{neg_after}/post-accounting")).status_code == 400

            neg_ppn = await _insert_finalized(grand_total=90.0, after_discount=100.0, ppn_amount=-10.0)
            assert (await client.post(f"/api/sales-invoices/{neg_ppn}/post-accounting")).status_code == 400

            mismatch = await _insert_finalized(grand_total=999.0, after_discount=100.0, ppn_amount=11.0)
            assert (await client.post(f"/api/sales-invoices/{mismatch}/post-accounting")).status_code == 400
            assert await server.db.journal_entries.count_documents({}) == before
    finally:
        app.dependency_overrides.pop(current_user, None)


@pytest.mark.anyio
async def test_zero_ppn_omits_tax_line_and_uses_mapped_ids_only():
    po = await _seed_po()
    rec, rev, tax = await _configure_settings()
    _admin()
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            inv = await _create_invoice(client, po["id"], unit_price="100.10", quantity=1, ppn_percent=0)
            fin = await _finalize(client, inv["id"])
            assert Decimal(str(fin["ppn_amount"])) == Decimal("0.00")
            res = await client.post(f"/api/sales-invoices/{fin['id']}/post-accounting")
            assert res.status_code == 200, res.text
            journals = await _source_journals(fin["id"])
            lines = await server.db.journal_lines.find({"journal_id": journals[0]["id"]}, {"_id": 0}).to_list(10)
            assert len(lines) == 2
            assert tax["id"] not in {ln["account_id"] for ln in lines}
            assert {ln["account_id"] for ln in lines} == {rec["id"], rev["id"]}
            assert all(Decimal(str(ln["debit"])) != 0 or Decimal(str(ln["credit"])) != 0 for ln in lines)
            debit = sum(Decimal(str(ln["debit"])) for ln in lines)
            credit = sum(Decimal(str(ln["credit"])) for ln in lines)
            assert debit == credit == Decimal(str(fin["grand_total"]))
    finally:
        app.dependency_overrides.pop(current_user, None)


@pytest.mark.anyio
async def test_idempotent_retry_and_concurrent_post():
    po = await _seed_po()
    await _configure_settings()
    _admin()
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            inv = await _create_invoice(client, po["id"], ppn_percent=11)
            fin = await _finalize(client, inv["id"])
            first = await client.post(f"/api/sales-invoices/{fin['id']}/post-accounting")
            assert first.status_code == 200
            jid = first.json()["invoice"]["accounting_journal_id"]
            retry = await client.post(f"/api/sales-invoices/{fin['id']}/post-accounting")
            assert retry.status_code == 200
            assert retry.json()["invoice"]["accounting_journal_id"] == jid
            assert len(await _source_journals(fin["id"])) == 1

            po2 = await _seed_po()
            inv2 = await _create_invoice(client, po2["id"], quantity=5, unit_price=2000, ppn_percent=11)
            fin2 = await _finalize(client, inv2["id"])
            results = await asyncio.gather(
                client.post(f"/api/sales-invoices/{fin2['id']}/post-accounting"),
                client.post(f"/api/sales-invoices/{fin2['id']}/post-accounting"),
            )
            assert all(r.status_code == 200 for r in results)
            journals = await _source_journals(fin2["id"])
            assert len(journals) == 1
            ids = {r.json()["invoice"]["accounting_journal_id"] for r in results}
            assert ids == {journals[0]["id"]}
    finally:
        app.dependency_overrides.pop(current_user, None)


@pytest.mark.anyio
async def test_get_zero_write_and_settings_change_does_not_rewrite_journal():
    po = await _seed_po()
    rec, rev, tax = await _configure_settings()
    _admin()
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            inv = await _create_invoice(client, po["id"], ppn_percent=11)
            fin = await _finalize(client, inv["id"])
            get1 = await client.get(f"/api/sales-invoices/{fin['id']}")
            assert get1.status_code == 200
            assert get1.json()["invoice"]["accounting_status"] == "not_posted"
            assert await _source_journals(fin["id"]) == []

            posted = await client.post(f"/api/sales-invoices/{fin['id']}/post-accounting")
            assert posted.status_code == 200
            jid = posted.json()["invoice"]["accounting_journal_id"]
            lines_before = await server.db.journal_lines.find({"journal_id": jid}, {"_id": 0}).to_list(10)
            account_ids = sorted(ln["account_id"] for ln in lines_before)

            rec2, rev2, tax2 = await _configure_settings()
            assert {rec2["id"], rev2["id"], tax2["id"]} != {rec["id"], rev["id"], tax["id"]}
            get2 = await client.get(f"/api/sales-invoices/{fin['id']}")
            assert get2.status_code == 200
            assert get2.json()["invoice"]["accounting_status"] == "posted"
            assert len(await _source_journals(fin["id"])) == 1
            lines_after = await server.db.journal_lines.find({"journal_id": jid}, {"_id": 0}).to_list(10)
            assert sorted(ln["account_id"] for ln in lines_after) == account_ids
            assert rec["id"] in account_ids
            assert rec2["id"] not in account_ids
    finally:
        app.dependency_overrides.pop(current_user, None)


@pytest.mark.anyio
async def test_posted_cancel_blocked_unposted_cancel_retained():
    po = await _seed_po()
    await _configure_settings()
    _admin()
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            unposted = await _create_invoice(client, po["id"], quantity=10, unit_price=1000)
            await _finalize(client, unposted["id"])
            c1 = await client.post(f"/api/sales-invoices/{unposted['id']}/cancel")
            assert c1.status_code == 200
            assert c1.json()["invoice"]["financial_status"] == "cancelled"

            posted_inv = await _create_invoice(client, po["id"], quantity=20, unit_price=1000, ppn_percent=11)
            fin = await _finalize(client, posted_inv["id"])
            post = await client.post(f"/api/sales-invoices/{fin['id']}/post-accounting")
            assert post.status_code == 200
            journals_before = await _source_journals(fin["id"])
            blocked = await client.post(f"/api/sales-invoices/{fin['id']}/cancel")
            assert blocked.status_code == 409
            assert CANCEL_POSTED_MESSAGE in str(blocked.json()["detail"])
            still = await server.db.sales_invoices.find_one({"id": fin["id"]}, {"_id": 0})
            assert still["financial_status"] == "finalized"
            journals_after = await _source_journals(fin["id"])
            assert len(journals_after) == 1
            assert journals_after[0]["id"] == journals_before[0]["id"]
            assert journals_after[0]["status"] == "posted"
    finally:
        app.dependency_overrides.pop(current_user, None)


@pytest.mark.anyio
async def test_post_vs_cancel_concurrency_invariant():
    po = await _seed_po()
    await _configure_settings()
    _admin()
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            inv = await _create_invoice(client, po["id"], ppn_percent=11)
            fin = await _finalize(client, inv["id"])
            post_res, cancel_res = await asyncio.gather(
                client.post(f"/api/sales-invoices/{fin['id']}/post-accounting"),
                client.post(f"/api/sales-invoices/{fin['id']}/cancel"),
            )
            stored = await server.db.sales_invoices.find_one({"id": fin["id"]}, {"_id": 0})
            journals = [
                j for j in await _source_journals(fin["id"])
                if j.get("status") == "posted"
            ]
            if stored["financial_status"] == "cancelled":
                assert cancel_res.status_code == 200
                assert post_res.status_code == 409
                assert journals == []
            else:
                assert stored["financial_status"] == "finalized"
                assert post_res.status_code == 200
                assert cancel_res.status_code == 409
                assert len(journals) == 1
            assert not (stored["financial_status"] == "cancelled" and journals)
    finally:
        app.dependency_overrides.pop(current_user, None)


@pytest.mark.anyio
async def test_post_does_not_mutate_workflow_receipt_or_stock():
    po = await _seed_po(current_stage=11, payment_status="DP", packed_qty=40, loaded_qty=10)
    stock_id = str(uuid.uuid4())
    stock_doc = {"id": stock_id, "item_name": "Cup Post", "qty": 77}
    await server.db.item_stock_manual.insert_one(stock_doc)
    await _configure_settings()
    _admin()
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            inv = await _create_invoice(client, po["id"], quantity=10, unit_price=1000, ppn_percent=11)
            fin = await _finalize(client, inv["id"])
            rec = await client.post("/api/sales-receipts", json={
                "invoice_id": fin["id"],
                "receipt_date": "2026-09-02",
                "amount": 2000,
            })
            assert rec.status_code == 200, rec.text
            order_after_receipt = await server.db.orders.find_one({"id": po["id"]}, {"_id": 0})
            cash_before = await server.db.journal_entries.count_documents({
                "source_type": {"$in": ["cash_bank_receipt", "cash_bank_payment", "cash_bank_transfer"]}
            })
            hpp_before = await server.db.item_stock_manual.find_one({"id": stock_id}, {"_id": 0})
            post = await client.post(f"/api/sales-invoices/{fin['id']}/post-accounting")
            assert post.status_code == 200, post.text
            order = await server.db.orders.find_one({"id": po["id"]}, {"_id": 0})
            assert order["current_stage"] == order_after_receipt["current_stage"]
            assert order["payment_status"] == order_after_receipt["payment_status"]
            assert order.get("packed_qty") == order_after_receipt.get("packed_qty")
            assert order.get("loaded_qty") == order_after_receipt.get("loaded_qty")
            assert order.get("shipments") == order_after_receipt.get("shipments")
            cash_after = await server.db.journal_entries.count_documents({
                "source_type": {"$in": ["cash_bank_receipt", "cash_bank_payment", "cash_bank_transfer"]}
            })
            assert cash_after == cash_before
            hpp_after = await server.db.item_stock_manual.find_one({"id": stock_id}, {"_id": 0})
            assert hpp_after["qty"] == 77
            sources = {j["source_type"] for j in await _source_journals(fin["id"])}
            assert sources == {SOURCE_TYPE}
    finally:
        await server.db.item_stock_manual.delete_one({"id": stock_id})
        app.dependency_overrides.pop(current_user, None)


@pytest.mark.anyio
async def test_non_admin_without_post_perm_forbidden():
    po = await _seed_po()
    await _configure_settings()
    _admin()
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            inv = await _create_invoice(client, po["id"])
            await _finalize(client, inv["id"])
        _user("crm", {"sales.invoice.edit": True, "sales.invoice.view": True})
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            res = await client.post(f"/api/sales-invoices/{inv['id']}/post-accounting")
            assert res.status_code == 403
            assert await _source_journals(inv["id"]) == []
    finally:
        app.dependency_overrides.pop(current_user, None)


def _assert_never_cancelled_with_journal(stored: dict, journals: list) -> None:
    assert not (stored.get("financial_status") == "cancelled" and journals)


async def _unlocked_post(invoice_id: str):
    try:
        return await post_sales_invoice_accounting(
            server.db, invoice_id, POST_USER, serialize_in_process=False
        )
    except HTTPException as exc:
        return exc


async def _unlocked_cancel(invoice_id: str):
    try:
        return await cancel_sales_invoice(
            server.db, invoice_id, POST_USER, serialize_in_process=False
        )
    except HTTPException as exc:
        return exc


@pytest.mark.anyio
async def test_independent_lock_post_vs_cancel_invariant():
    po = await _seed_po()
    await _configure_settings()
    _admin()
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            inv = await _create_invoice(client, po["id"], ppn_percent=11)
            fin = await _finalize(client, inv["id"])
        post_res, cancel_res = await asyncio.gather(
            _unlocked_post(fin["id"]),
            _unlocked_cancel(fin["id"]),
        )
        stored = await server.db.sales_invoices.find_one({"id": fin["id"]}, {"_id": 0})
        journals = [j for j in await _source_journals(fin["id"]) if j.get("status") == "posted"]
        _assert_never_cancelled_with_journal(stored, journals)
        if stored["financial_status"] == "cancelled":
            assert isinstance(post_res, HTTPException) and post_res.status_code == 409
            assert not isinstance(cancel_res, HTTPException)
            assert journals == []
            assert not posting_claim_is_active(stored)
        else:
            assert stored["financial_status"] == "finalized"
            assert not isinstance(post_res, HTTPException)
            assert isinstance(cancel_res, HTTPException) and cancel_res.status_code == 409
            assert len(journals) == 1
            assert not posting_claim_is_active(stored)
    finally:
        app.dependency_overrides.pop(current_user, None)


@pytest.mark.anyio
async def test_cancel_wins_before_claim_zero_journal():
    po = await _seed_po()
    await _configure_settings()
    _admin()
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            inv = await _create_invoice(client, po["id"], ppn_percent=11)
            fin = await _finalize(client, inv["id"])
        cancelled = await _unlocked_cancel(fin["id"])
        assert not isinstance(cancelled, HTTPException)
        assert cancelled["financial_status"] == "cancelled"
        posted = await _unlocked_post(fin["id"])
        assert isinstance(posted, HTTPException) and posted.status_code == 409
        assert await _source_journals(fin["id"]) == []
        stored = await server.db.sales_invoices.find_one({"id": fin["id"]}, {"_id": 0})
        assert stored["financial_status"] == "cancelled"
        assert not posting_claim_is_active(stored)
    finally:
        app.dependency_overrides.pop(current_user, None)


@pytest.mark.anyio
async def test_posting_claim_wins_cancel_rejected():
    po = await _seed_po()
    await _configure_settings()
    _admin()
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            inv = await _create_invoice(client, po["id"], ppn_percent=11)
            fin = await _finalize(client, inv["id"])
        claimed = await acquire_posting_claim(server.db, fin["id"])
        assert claimed is not None
        assert posting_claim_is_active(claimed)
        blocked = await _unlocked_cancel(fin["id"])
        assert isinstance(blocked, HTTPException)
        assert blocked.status_code == 409
        assert CANCEL_CLAIM_MESSAGE in str(blocked.detail)
        stored = await server.db.sales_invoices.find_one({"id": fin["id"]}, {"_id": 0})
        assert stored["financial_status"] == "finalized"
        assert await _source_journals(fin["id"]) == []
        posted = await _unlocked_post(fin["id"])
        assert not isinstance(posted, HTTPException)
        after = await server.db.sales_invoices.find_one({"id": fin["id"]}, {"_id": 0})
        assert after["financial_status"] == "finalized"
        assert not posting_claim_is_active(after)
        assert len(await _source_journals(fin["id"])) == 1
    finally:
        app.dependency_overrides.pop(current_user, None)


@pytest.mark.anyio
async def test_crash_after_claim_before_journal_retry_posts_once():
    po = await _seed_po()
    await _configure_settings()
    _admin()
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            inv = await _create_invoice(client, po["id"], ppn_percent=11)
            fin = await _finalize(client, inv["id"])
        await acquire_posting_claim(server.db, fin["id"], token="stale-before-journal")
        hanging = await server.db.sales_invoices.find_one({"id": fin["id"]}, {"_id": 0})
        assert hanging[CLAIM_FIELD] == "stale-before-journal"
        assert await _source_journals(fin["id"]) == []
        posted = await _unlocked_post(fin["id"])
        assert not isinstance(posted, HTTPException)
        journals = await _source_journals(fin["id"])
        assert len(journals) == 1
        stored = await server.db.sales_invoices.find_one({"id": fin["id"]}, {"_id": 0})
        assert not posting_claim_is_active(stored)
        retry = await _unlocked_post(fin["id"])
        assert retry["accounting_journal_id"] == journals[0]["id"]
        assert len(await _source_journals(fin["id"])) == 1
    finally:
        app.dependency_overrides.pop(current_user, None)


@pytest.mark.anyio
async def test_crash_after_journal_before_claim_cleanup_retry_reuses():
    po = await _seed_po()
    await _configure_settings()
    _admin()
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            inv = await _create_invoice(client, po["id"], ppn_percent=11)
            fin = await _finalize(client, inv["id"])
        claimed = await acquire_posting_claim(server.db, fin["id"], token="stale-after-journal")
        await _write_source_journal(server.db, claimed, POST_USER)
        hanging = await server.db.sales_invoices.find_one({"id": fin["id"]}, {"_id": 0})
        assert hanging[CLAIM_FIELD] == "stale-after-journal"
        journals = await _source_journals(fin["id"])
        assert len(journals) == 1
        posted = await _unlocked_post(fin["id"])
        assert not isinstance(posted, HTTPException)
        assert posted["accounting_journal_id"] == journals[0]["id"]
        assert len(await _source_journals(fin["id"])) == 1
        stored = await server.db.sales_invoices.find_one({"id": fin["id"]}, {"_id": 0})
        assert not posting_claim_is_active(stored)
    finally:
        app.dependency_overrides.pop(current_user, None)


@pytest.mark.anyio
async def test_independent_lock_concurrent_post_one_journal():
    po = await _seed_po()
    await _configure_settings()
    _admin()
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            inv = await _create_invoice(client, po["id"], ppn_percent=11)
            fin = await _finalize(client, inv["id"])
        results = await asyncio.gather(_unlocked_post(fin["id"]), _unlocked_post(fin["id"]))
        assert all(not isinstance(r, HTTPException) for r in results)
        journals = await _source_journals(fin["id"])
        assert len(journals) == 1
        ids = {r["accounting_journal_id"] for r in results}
        assert ids == {journals[0]["id"]}
        stored = await server.db.sales_invoices.find_one({"id": fin["id"]}, {"_id": 0})
        assert stored["financial_status"] == "finalized"
        assert not posting_claim_is_active(stored)
    finally:
        app.dependency_overrides.pop(current_user, None)


@pytest.mark.anyio
async def test_get_is_zero_write_and_does_not_clear_claim():
    po = await _seed_po()
    await _configure_settings()
    _admin()
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            inv = await _create_invoice(client, po["id"], ppn_percent=11)
            fin = await _finalize(client, inv["id"])
        await acquire_posting_claim(server.db, fin["id"], token="get-must-not-touch")
        journals_before = await server.db.journal_entries.count_documents({})
        detail = await get_sales_invoice_detail(server.db, fin["id"])
        assert CLAIM_FIELD not in detail
        assert detail["accounting_status"] == "not_posted"
        assert detail["accounting_posted"] is False
        assert detail["can_cancel"] is False
        stored = await server.db.sales_invoices.find_one({"id": fin["id"]}, {"_id": 0})
        assert stored[CLAIM_FIELD] == "get-must-not-touch"
        assert await server.db.journal_entries.count_documents({}) == journals_before
        http_get = None
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            http_get = await client.get(f"/api/sales-invoices/{fin['id']}")
        assert http_get.status_code == 200
        assert CLAIM_FIELD not in http_get.json()["invoice"]
        still = await server.db.sales_invoices.find_one({"id": fin["id"]}, {"_id": 0})
        assert still[CLAIM_FIELD] == "get-must-not-touch"
        assert await _source_journals(fin["id"]) == []
    finally:
        app.dependency_overrides.pop(current_user, None)
