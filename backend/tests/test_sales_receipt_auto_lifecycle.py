"""Phase 2.2B — automatic sales receipt create → post → settlement (flag-gated)."""
from __future__ import annotations

import asyncio
import os
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ.setdefault("MONGO_URL", "mongodb://127.0.0.1:27017")
_worker = os.environ.get("PYTEST_XDIST_WORKER") or "main"
os.environ["DB_NAME"] = f"eternal_packaging_sales_receipt_auto_lifecycle_{_worker}"
os.environ.setdefault("JWT_SECRET", "test-secret-key-12345678901234567890")
os.environ.setdefault("CORS_ORIGINS", "*")
os.environ.setdefault("APP_ENV", "test")

import server
from server import app, current_user
from services.accounting_periods import close_period, ensure_indexes as ensure_periods
from services.cash_bank_master import create_cash_bank, ensure_indexes as ensure_cb
from services.chart_of_accounts import create_account, ensure_indexes as ensure_coa
from services.journal_core import JOURNAL_ENTRIES, ensure_indexes as ensure_journal
from services.sales_accounting_settings import SETTINGS_KEY
from services.sales_invoice_posting import SOURCE_TYPE as INVOICE_SOURCE
from services.sales_receipt_auto_lifecycle import (
    AUTO_ELIGIBLE_FIELD,
    AUTO_FLAG_KEY,
    AUTO_STATUS_FIELD,
    AUTO_STATUS_POSTED,
    ERR_CASH_BANK,
    ERR_FLAG_OFF,
    ERR_INDEX_PREREQUISITE,
    ERR_INTENT_INDEX_PREREQUISITE,
    ERR_MISSING_IDEMPOTENCY_KEY,
    ERR_PAYLOAD_CONFLICT,
    ERR_UNAUTHORIZED_RETRY,
    GATE_FLAG_KEY,
    INTENT_COLL,
    AUTO_LIFECYCLE_ACTIVATION_BLOCKED,
    auto_receipt_lifecycle_activation_blockers,
    create_sales_receipt_with_auto_lifecycle,
    ensure_auto_receipt_intent_indexes,
    intent_idempotency_index_ready,
    is_auto_sales_receipt_lifecycle_enabled,
    is_receipt_create_requires_invoice_journal_posted,
    sales_receipt_idempotency_index_ready,
)
from services.sales_receipt_posting import (
    INVOICE_NOT_POSTED_MESSAGE,
    SETTLEMENT_EFFECTIVE_FIELD,
    SOURCE_TYPE,
    receipt_counts_toward_outstanding,
)
from services.sales_invoices import apply_invoice_payment_fields

pytestmark = pytest.mark.anyio

ADMIN = {
    "id": "admin-rcpt-auto",
    "email": "admin@eternalpackaging.com",
    "name": "Admin Receipt Auto",
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


@pytest.fixture(autouse=True)
async def clean_db(bind_motor):
    await ensure_coa(server.db)
    await ensure_journal(server.db)
    await ensure_periods(server.db)
    await ensure_cb(server.db)
    await ensure_auto_receipt_intent_indexes(server.db)
    await server.db.sales_receipts.create_index("id", unique=True)
    if not await sales_receipt_idempotency_index_ready(server.db):
        await server.db.sales_receipts.create_index(
            "idempotency_key",
            unique=True,
            partialFilterExpression={"idempotency_key": {"$type": "string"}},
            name="idempotency_key_partial_unique",
        )
    await server.db.sales_invoices.create_index("id", unique=True)
    await server.db.app_settings.delete_many(
        {"key": {"$in": [SETTINGS_KEY, GATE_FLAG_KEY, AUTO_FLAG_KEY]}}
    )
    await server.db.sales_invoices.delete_many({})
    await server.db.sales_receipts.delete_many({})
    await server.db[INTENT_COLL].delete_many({})
    await server.db.orders.delete_many({})
    await server.db[JOURNAL_ENTRIES].delete_many({})
    await server.db.journal_lines.delete_many({})
    await server.db.accounting_periods.delete_many({})
    yield
    await server.db.app_settings.delete_many(
        {"key": {"$in": [SETTINGS_KEY, GATE_FLAG_KEY, AUTO_FLAG_KEY]}}
    )
    app.dependency_overrides.pop(current_user, None)


def _admin():
    app.dependency_overrides[current_user] = lambda: dict(ADMIN)


async def _set_gate(enabled: bool) -> None:
    await server.db.app_settings.update_one(
        {"key": GATE_FLAG_KEY},
        {"$set": {"key": GATE_FLAG_KEY, "enabled": enabled}},
        upsert=True,
    )


async def _set_auto(enabled: bool) -> None:
    await server.db.app_settings.update_one(
        {"key": AUTO_FLAG_KEY},
        {"$set": {"key": AUTO_FLAG_KEY, "enabled": enabled}},
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
    rec = await _coa("11", "Piutang Auto Rcpt", "AREC")
    rev = await _coa("41", "Pendapatan Auto Rcpt", "REVE")
    tax = await _coa("21", "PPN Auto Rcpt", "OCLY")
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


async def _cash_bank(name: str = "BCA Auto Rcpt") -> dict:
    bank = await _coa("12", name, "BANK")
    return await create_cash_bank(
        server.db,
        {"name": name, "account_id": bank["id"], "active": True},
        user=ADMIN,
    )


async def _seed_po(**extra) -> dict:
    po_id = str(uuid.uuid4())
    doc = {
        "id": po_id,
        "po_number": f"PO/RCA/{uuid.uuid4().hex[:8].upper()}",
        "customer": "PT Receipt Auto",
        "outlet": "Outlet",
        "recipient": "Budi",
        "address": "Jl. Test",
        "delivery_zone": "Jakarta",
        "sales": "Vira",
        "line_items": [{"line_idx": 0, "line_id": "l0", "name": "Cup", "quantity": 100, "unit": "pcs"}],
        "total_quantity": 100,
        "current_stage": 4,
        "status_label": "Approval Payment",
        "payment_status": "Waiting",
        "packed_qty": 40,
        "loaded_qty": 10,
        "events": [{"stage": 0, "name": "Order Masuk"}],
        "shipments": [],
        "created_at": "2026-09-01T00:00:00Z",
    }
    doc.update(extra)
    await server.db.orders.insert_one(doc)
    doc.pop("_id", None)
    return doc


async def _posted_invoice(client, po_id: str, *, unit_price: int = 1_000_000) -> dict:
    res = await client.post(
        "/api/sales-invoices",
        json={
            "po_id": po_id,
            "invoice_date": "2026-09-01",
            "discount_amount": 0,
            "ppn_percent": 0,
            "lines": [{"line_idx": 0, "quantity": 1, "unit_price": unit_price}],
        },
    )
    assert res.status_code == 200, res.text
    inv = res.json()["invoice"]
    assert (await client.post(f"/api/sales-invoices/{inv['id']}/finalize")).status_code == 200
    assert (await client.post(f"/api/sales-invoices/{inv['id']}/post-accounting")).status_code == 200
    return inv


async def _finalized_unposted_invoice(client, po_id: str) -> dict:
    res = await client.post(
        "/api/sales-invoices",
        json={
            "po_id": po_id,
            "invoice_date": "2026-09-01",
            "discount_amount": 0,
            "ppn_percent": 0,
            "lines": [{"line_idx": 0, "quantity": 1, "unit_price": 500_000}],
        },
    )
    inv = res.json()["invoice"]
    await client.post(f"/api/sales-invoices/{inv['id']}/finalize")
    return inv


def _payload(invoice_id: str, cash_bank_id: str, **extra) -> dict:
    body = {
        "invoice_id": invoice_id,
        "receipt_date": extra.pop("receipt_date", "2026-09-05"),
        "amount": extra.pop("amount", 400_000),
        "cash_bank_id": cash_bank_id,
        "idempotency_key": extra.pop("idempotency_key", f"rcpt-auto-{uuid.uuid4()}"),
        "notes": extra.pop("notes", "DP auto"),
    }
    body.update(extra)
    return body


async def _receipt_journals(receipt_id: str):
    return await server.db.journal_entries.find(
        {"source_type": SOURCE_TYPE, "source_id": receipt_id, "reversal_of": None},
        {"_id": 0},
    ).to_list(20)


# --- Flags / counting ---


@pytest.mark.anyio
async def test_flags_default_off():
    assert await is_auto_sales_receipt_lifecycle_enabled(server.db) is False
    assert await is_receipt_create_requires_invoice_journal_posted(server.db) is False


@pytest.mark.anyio
async def test_settlement_effective_counting_semantics():
    assert receipt_counts_toward_outstanding({"financial_status": "active"}) is True
    assert receipt_counts_toward_outstanding({"financial_status": "active", SETTLEMENT_EFFECTIVE_FIELD: True}) is True
    assert receipt_counts_toward_outstanding({"financial_status": "active", SETTLEMENT_EFFECTIVE_FIELD: False}) is False
    assert receipt_counts_toward_outstanding({"financial_status": "cancelled"}) is False
    assert receipt_counts_toward_outstanding({"cancelled": True, SETTLEMENT_EFFECTIVE_FIELD: True}) is False


@pytest.mark.anyio
async def test_flag_off_legacy_create_settles_immediately_without_journal():
    """BOTH flags OFF: legacy create mutates paid_total even before receipt journal."""
    _admin()
    await _configure_settings()
    bca = await _cash_bank()
    po = await _seed_po()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        inv = await _posted_invoice(client, po["id"])
        res = await client.post(
            "/api/sales-receipts",
            json={
                "invoice_id": inv["id"],
                "receipt_date": "2026-09-05",
                "amount": 100_000,
                "cash_bank_id": bca["id"],
            },
        )
        assert res.status_code == 200, res.text
        receipt = res.json()["receipt"]
        assert SETTLEMENT_EFFECTIVE_FIELD not in receipt or receipt.get(SETTLEMENT_EFFECTIVE_FIELD) is not False
        stored_inv = await server.db.sales_invoices.find_one({"id": inv["id"]}, {"_id": 0})
        assert float(stored_inv["paid_total"]) == 100_000.0
        assert stored_inv["payment_status"] == "DP"
        assert len(await _receipt_journals(receipt["id"])) == 0
        order = await server.db.orders.find_one({"id": po["id"]}, {"_id": 0})
        assert order.get("payment_status") == "Approved"


@pytest.mark.anyio
async def test_gate_only_rejects_finalized_unposted():
    _admin()
    await _set_gate(True)
    await _configure_settings()
    bca = await _cash_bank()
    po = await _seed_po()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        inv = await _finalized_unposted_invoice(client, po["id"])
        res = await client.post(
            "/api/sales-receipts",
            json={
                "invoice_id": inv["id"],
                "receipt_date": "2026-09-05",
                "amount": 50_000,
                "cash_bank_id": bca["id"],
            },
        )
        assert res.status_code == 409, res.text
        assert INVOICE_NOT_POSTED_MESSAGE in str(res.json())
        assert await server.db.sales_receipts.count_documents({"invoice_id": inv["id"]}) == 0


@pytest.mark.anyio
async def test_gate_only_allows_posted_invoice_legacy_settle():
    _admin()
    await _set_gate(True)
    await _configure_settings()
    bca = await _cash_bank()
    po = await _seed_po()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        inv = await _posted_invoice(client, po["id"])
        res = await client.post(
            "/api/sales-receipts",
            json={
                "invoice_id": inv["id"],
                "receipt_date": "2026-09-05",
                "amount": 200_000,
                "cash_bank_id": bca["id"],
            },
        )
        assert res.status_code == 200, res.text
        stored_inv = await server.db.sales_invoices.find_one({"id": inv["id"]}, {"_id": 0})
        assert float(stored_inv["paid_total"]) == 200_000.0


@pytest.mark.anyio
async def test_auto_on_success_posts_and_settles():
    _admin()
    await _set_auto(True)
    await _configure_settings()
    bca = await _cash_bank()
    po = await _seed_po()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        inv = await _posted_invoice(client, po["id"], unit_price=1_000_000)
        res = await client.post("/api/sales-receipts", json=_payload(inv["id"], bca["id"], amount=400_000))
        assert res.status_code == 200, res.text
        body = res.json()
        assert body["financial_complete"] is True
        assert body["auto_accounting_status"] == AUTO_STATUS_POSTED
        receipt = body["receipt"]
        assert receipt.get(SETTLEMENT_EFFECTIVE_FIELD) is True or receipt.get("settlement_effective") is True
        assert len(await _receipt_journals(receipt["id"])) == 1
        stored_inv = await server.db.sales_invoices.find_one({"id": inv["id"]}, {"_id": 0})
        assert float(stored_inv["paid_total"]) == 400_000.0
        assert stored_inv["payment_status"] == "DP"
        order = await server.db.orders.find_one({"id": po["id"]}, {"_id": 0})
        assert order.get("payment_status") == "Approved"
        assert order.get("payment_fact", {}).get("receipt_id") == receipt["id"]


@pytest.mark.anyio
async def test_auto_rejects_finalized_unposted_even_if_gate_flag_off():
    _admin()
    await _set_auto(True)
    await _set_gate(False)
    await _configure_settings()
    bca = await _cash_bank()
    po = await _seed_po()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        inv = await _finalized_unposted_invoice(client, po["id"])
        res = await client.post("/api/sales-receipts", json=_payload(inv["id"], bca["id"], amount=10_000))
        assert res.status_code == 409, res.text
        detail = res.json()["detail"]
        assert detail["financial_complete"] is False
        assert await server.db.sales_receipts.count_documents({"invoice_id": inv["id"]}) == 0


@pytest.mark.anyio
async def test_auto_requires_idempotency_and_cash_bank():
    _admin()
    await _set_auto(True)
    await _configure_settings()
    bca = await _cash_bank()
    po = await _seed_po()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        inv = await _posted_invoice(client, po["id"])
        no_key = _payload(inv["id"], bca["id"])
        no_key.pop("idempotency_key")
        r1 = await client.post("/api/sales-receipts", json=no_key)
        assert r1.status_code == 409
        assert r1.json()["detail"]["error_code"] == ERR_MISSING_IDEMPOTENCY_KEY
        no_bank = _payload(inv["id"], "")
        no_bank["cash_bank_id"] = ""
        r2 = await client.post("/api/sales-receipts", json=no_bank)
        assert r2.status_code == 409
        assert r2.json()["detail"]["error_code"] == ERR_CASH_BANK


@pytest.mark.anyio
async def test_auto_idempotent_same_key_one_journal():
    _admin()
    await _set_auto(True)
    await _configure_settings()
    bca = await _cash_bank()
    po = await _seed_po()
    key = f"same-{uuid.uuid4()}"
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        inv = await _posted_invoice(client, po["id"])
        p = _payload(inv["id"], bca["id"], amount=250_000, idempotency_key=key)
        r1 = await client.post("/api/sales-receipts", json=p)
        r2 = await client.post("/api/sales-receipts", json=p)
        assert r1.status_code == 200 and r2.status_code == 200
        assert r1.json()["receipt"]["id"] == r2.json()["receipt"]["id"]
        assert await server.db.sales_receipts.count_documents({"idempotency_key": key}) == 1
        assert len(await _receipt_journals(r1.json()["receipt"]["id"])) == 1


@pytest.mark.anyio
async def test_auto_same_key_different_payload_conflict():
    _admin()
    await _set_auto(True)
    await _configure_settings()
    bca = await _cash_bank()
    po = await _seed_po()
    key = f"diff-{uuid.uuid4()}"
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        inv = await _posted_invoice(client, po["id"])
        r1 = await client.post(
            "/api/sales-receipts",
            json=_payload(inv["id"], bca["id"], amount=100_000, idempotency_key=key),
        )
        assert r1.status_code == 200
        r2 = await client.post(
            "/api/sales-receipts",
            json=_payload(inv["id"], bca["id"], amount=200_000, idempotency_key=key),
        )
        assert r2.status_code == 409
        assert r2.json()["detail"]["error_code"] == ERR_PAYLOAD_CONFLICT
        assert len(await _receipt_journals(r1.json()["receipt"]["id"])) == 1


@pytest.mark.anyio
async def test_auto_partial_and_lunas_no_false_paid_before_settle():
    _admin()
    await _set_auto(True)
    await _configure_settings()
    bca = await _cash_bank()
    po = await _seed_po()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        inv = await _posted_invoice(client, po["id"], unit_price=1_000_000)
        # Simulate crash window: insert deferred receipt without settle
        from services.sales_receipts import create_sales_receipt

        draft = await create_sales_receipt(
            server.db,
            _payload(inv["id"], bca["id"], amount=1_000_000, idempotency_key=f"crash-{uuid.uuid4()}"),
            ADMIN,
            defer_operational_settlement=True,
            require_posted_invoice_journal=True,
        )
        assert draft.get(SETTLEMENT_EFFECTIVE_FIELD) is False
        stored_inv = await server.db.sales_invoices.find_one({"id": inv["id"]}, {"_id": 0})
        assert float(stored_inv.get("paid_total") or 0) == 0.0
        order = await server.db.orders.find_one({"id": po["id"]}, {"_id": 0})
        assert order.get("payment_status") != "Approved" or not order.get("payment_fact")
        # Live Tracking projection uses apply_invoice_payment_fields
        receipts = await server.db.sales_receipts.find({"invoice_id": inv["id"]}, {"_id": 0}).to_list(20)
        projected = apply_invoice_payment_fields(dict(stored_inv), receipts)
        assert projected["payment_status"] == "BELUM DIBAYAR"
        assert float(projected["paid_total"]) == 0.0


@pytest.mark.anyio
async def test_auto_overallocation_rejected():
    _admin()
    await _set_auto(True)
    await _configure_settings()
    bca = await _cash_bank()
    po = await _seed_po()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        inv = await _posted_invoice(client, po["id"], unit_price=100_000)
        first = await client.post(
            "/api/sales-receipts",
            json=_payload(inv["id"], bca["id"], amount=100_000),
        )
        assert first.status_code == 200
        second = await client.post(
            "/api/sales-receipts",
            json=_payload(inv["id"], bca["id"], amount=1),
        )
        assert second.status_code in (400, 409), second.text
        assert await server.db.sales_receipts.count_documents({"invoice_id": inv["id"]}) == 1


@pytest.mark.anyio
async def test_auto_concurrent_same_key_one_receipt():
    _admin()
    await _set_auto(True)
    await _configure_settings()
    bca = await _cash_bank()
    po = await _seed_po()
    key = f"race-{uuid.uuid4()}"
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        inv = await _posted_invoice(client, po["id"])
        p = _payload(inv["id"], bca["id"], amount=50_000, idempotency_key=key)
        r1, r2 = await asyncio.gather(
            client.post("/api/sales-receipts", json=p),
            client.post("/api/sales-receipts", json=p),
        )
    assert r1.status_code == 200 or r2.status_code == 200
    ok = [r for r in (r1, r2) if r.status_code == 200]
    assert ok
    rid = ok[0].json()["receipt"]["id"]
    assert await server.db.sales_receipts.count_documents({"idempotency_key": key}) == 1
    assert len(await _receipt_journals(rid)) == 1


@pytest.mark.anyio
async def test_auto_unauthorized_retry_no_leak():
    await _set_auto(True)
    await _configure_settings()
    bca = await _cash_bank()
    po = await _seed_po()
    payload = _payload("inv-placeholder", bca["id"])
    _admin()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        inv = await _posted_invoice(client, po["id"])
        payload = _payload(inv["id"], bca["id"], amount=80_000)
        first = await create_sales_receipt_with_auto_lifecycle(server.db, payload, ADMIN)
        assert first["financial_complete"] is True
        other = {"id": "intruder", "email": "x@example.com", "name": "X", "role": "admin"}
        retry = await create_sales_receipt_with_auto_lifecycle(server.db, payload, other)
        assert retry["financial_complete"] is False
        assert retry["error_code"] == ERR_UNAUTHORIZED_RETRY
        assert not (retry.get("receipt") or {}).get("id")


@pytest.mark.anyio
async def test_recover_flag_off_no_side_effects():
    _admin()
    await _set_auto(True)
    await _configure_settings()
    bca = await _cash_bank()
    po = await _seed_po()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        inv = await _posted_invoice(client, po["id"])
        created = await client.post("/api/sales-receipts", json=_payload(inv["id"], bca["id"], amount=10_000))
        assert created.status_code == 200
        rid = created.json()["receipt"]["id"]
        await _set_auto(False)
        denied = await client.post(f"/api/sales-receipts/{rid}/recover-auto-accounting")
        assert denied.status_code == 409
        detail = denied.json()["detail"]
        assert detail.get("error_code") == ERR_FLAG_OFF or (
            isinstance(detail, dict) and detail.get("error_code") == ERR_FLAG_OFF
        )


@pytest.mark.anyio
async def test_crash_after_insert_before_post_recovers():
    _admin()
    await _set_auto(True)
    await _configure_settings()
    bca = await _cash_bank()
    po = await _seed_po()
    key = f"orphan-{uuid.uuid4()}"
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        inv = await _posted_invoice(client, po["id"])
        payload = _payload(inv["id"], bca["id"], amount=120_000, idempotency_key=key)
        from services.sales_receipt_auto_lifecycle import canonical_receipt_create_fingerprint, now_iso
        from services.sales_receipts import create_sales_receipt

        fp = canonical_receipt_create_fingerprint(payload)
        stamped = now_iso()
        await server.db[INTENT_COLL].insert_one(
            {
                "idempotency_key": key,
                "fingerprint": fp,
                "actor_id": ADMIN["id"],
                "invoice_id": inv["id"],
                "receipt_id": None,
                "status": "preparing",
                "created_at": stamped,
                "updated_at": stamped,
            }
        )
        draft = await create_sales_receipt(
            server.db,
            payload,
            ADMIN,
            defer_operational_settlement=True,
            require_posted_invoice_journal=True,
        )
        assert draft.get(SETTLEMENT_EFFECTIVE_FIELD) is False
        assert len(await _receipt_journals(draft["id"])) == 0
        res = await client.post("/api/sales-receipts", json=payload)
        assert res.status_code == 200, res.text
        assert res.json()["receipt"]["id"] == draft["id"]
        assert res.json()["financial_complete"] is True
        assert len(await _receipt_journals(draft["id"])) == 1
        stored = await server.db.sales_receipts.find_one({"id": draft["id"]}, {"_id": 0})
        assert stored.get(SETTLEMENT_EFFECTIVE_FIELD) is True


@pytest.mark.anyio
async def test_closed_period_incomplete_no_false_success():
    _admin()
    await _set_auto(True)
    await _configure_settings()
    bca = await _cash_bank()
    po = await _seed_po()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        inv = await _posted_invoice(client, po["id"])
        await close_period(server.db, "2026-09", user=ADMIN, reason="Uji period lock lifecycle")
        res = await client.post(
            "/api/sales-receipts",
            json=_payload(inv["id"], bca["id"], amount=30_000, receipt_date="2026-09-10"),
        )
        assert res.status_code == 409, res.text
        detail = res.json()["detail"]
        assert detail["financial_complete"] is False
        # May have persisted receipt without settle
        if detail.get("receipt", {}).get("id"):
            rid = detail["receipt"]["id"]
            stored = await server.db.sales_receipts.find_one({"id": rid}, {"_id": 0})
            if stored:
                assert stored.get(SETTLEMENT_EFFECTIVE_FIELD) is not True
            stored_inv = await server.db.sales_invoices.find_one({"id": inv["id"]}, {"_id": 0})
            assert float(stored_inv.get("paid_total") or 0) == 0.0
        else:
            assert await server.db.sales_receipts.count_documents({"invoice_id": inv["id"]}) == 0


@pytest.mark.anyio
async def test_activation_blockers_indexes_and_flag():
    await _set_auto(False)
    blockers = await auto_receipt_lifecycle_activation_blockers(server.db)
    assert ERR_FLAG_OFF in blockers
    assert await sales_receipt_idempotency_index_ready(server.db) is True
    assert await intent_idempotency_index_ready(server.db) is True
    await _set_auto(True)
    blockers_on = await auto_receipt_lifecycle_activation_blockers(server.db)
    assert ERR_FLAG_OFF not in blockers_on
    assert ERR_INDEX_PREREQUISITE not in blockers_on
    assert ERR_INTENT_INDEX_PREREQUISITE not in blockers_on
    assert AUTO_LIFECYCLE_ACTIVATION_BLOCKED not in blockers_on


@pytest.mark.anyio
async def test_historical_receipt_without_settlement_field_still_counts():
    """Legacy absent settlement_effective must still count (no unexpected historical change)."""
    legacy = {
        "id": "legacy-1",
        "financial_status": "active",
        "amount": 50,
        "allocated_amount": 50,
    }
    assert receipt_counts_toward_outstanding(legacy) is True
    inv = {"id": "inv-h", "grand_total": 100, "financial_status": "finalized"}
    projected = apply_invoice_payment_fields(dict(inv), [legacy])
    assert projected["payment_status"] == "DP"
    assert float(projected["paid_total"]) == 50.0


@pytest.mark.anyio
async def test_manual_post_perm_unchanged_when_auto_off():
    _admin()
    await _configure_settings()
    bca = await _cash_bank()
    po = await _seed_po()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        inv = await _posted_invoice(client, po["id"])
        created = await client.post(
            "/api/sales-receipts",
            json={
                "invoice_id": inv["id"],
                "receipt_date": "2026-09-05",
                "amount": 40_000,
                "cash_bank_id": bca["id"],
            },
        )
        assert created.status_code == 200
        rid = created.json()["receipt"]["id"]
        app.dependency_overrides[current_user] = lambda: {
            "id": "crm-user",
            "email": "crm@example.com",
            "name": "CRM",
            "role": "crm",
            "permissions_override": {"sales.receipt.create": True, "sales.receipt.view": True},
        }
        denied = await client.post(f"/api/sales-receipts/{rid}/post-accounting")
        assert denied.status_code == 403


# --- Final corrective: crash/race, verification, indexes, cancel ---


@pytest.mark.anyio
async def test_journal_posted_before_settlement_cas_recovers():
    _admin()
    await _set_auto(True)
    await _configure_settings()
    bca = await _cash_bank()
    po = await _seed_po()
    from services.sales_receipt_posting import post_sales_receipt_accounting
    from services.sales_receipts import create_sales_receipt
    from services.sales_receipt_auto_lifecycle import (
        AUTO_ELIGIBLE_FIELD,
        AUTO_FINGERPRINT_FIELD,
        canonical_receipt_create_fingerprint,
        recover_auto_sales_receipt_accounting,
    )

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        inv = await _posted_invoice(client, po["id"])
    payload = _payload(inv["id"], bca["id"], amount=90_000)
    draft = await create_sales_receipt(
        server.db, payload, ADMIN, defer_operational_settlement=True, require_posted_invoice_journal=True
    )
    assert draft.get(SETTLEMENT_EFFECTIVE_FIELD) is False
    await post_sales_receipt_accounting(server.db, draft["id"], ADMIN, cash_bank_id=bca["id"])
    assert len(await _receipt_journals(draft["id"])) == 1
    inv_mid = await server.db.sales_invoices.find_one({"id": inv["id"]}, {"_id": 0})
    assert float(inv_mid.get("paid_total") or 0) == 0.0
    fp = canonical_receipt_create_fingerprint(payload)
    await server.db.sales_receipts.update_one(
        {"id": draft["id"]},
        {"$set": {AUTO_ELIGIBLE_FIELD: True, AUTO_FINGERPRINT_FIELD: fp}},
    )
    result = await recover_auto_sales_receipt_accounting(server.db, draft["id"], ADMIN)
    assert result["financial_complete"] is True
    stored = await server.db.sales_receipts.find_one({"id": draft["id"]}, {"_id": 0})
    assert stored.get(SETTLEMENT_EFFECTIVE_FIELD) is True
    inv_after = await server.db.sales_invoices.find_one({"id": inv["id"]}, {"_id": 0})
    assert float(inv_after["paid_total"]) == 90_000.0
    assert len(await _receipt_journals(draft["id"])) == 1


@pytest.mark.anyio
async def test_settlement_cas_before_invoice_recalc_heals():
    _admin()
    await _set_auto(True)
    await _configure_settings()
    bca = await _cash_bank()
    po = await _seed_po()
    from services.sales_receipt_posting import post_sales_receipt_accounting
    from services.sales_receipts import create_sales_receipt, apply_operational_settlement_for_receipt
    from services.sales_receipt_auto_lifecycle import AUTO_ELIGIBLE_FIELD, AUTO_FINGERPRINT_FIELD, canonical_receipt_create_fingerprint, recover_auto_sales_receipt_accounting

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        inv = await _posted_invoice(client, po["id"])
    payload = _payload(inv["id"], bca["id"], amount=70_000)
    draft = await create_sales_receipt(
        server.db, payload, ADMIN, defer_operational_settlement=True, require_posted_invoice_journal=True
    )
    await post_sales_receipt_accounting(server.db, draft["id"], ADMIN, cash_bank_id=bca["id"])
    await apply_operational_settlement_for_receipt(server.db, draft["id"], ADMIN)
    # Simulate stale invoice snapshot after CAS
    await server.db.sales_invoices.update_one(
        {"id": inv["id"]},
        {"$set": {"paid_total": 0, "outstanding_amount": 1_000_000, "payment_status": "BELUM DIBAYAR"}},
    )
    fp = canonical_receipt_create_fingerprint(payload)
    await server.db.sales_receipts.update_one(
        {"id": draft["id"]},
        {"$set": {AUTO_ELIGIBLE_FIELD: True, AUTO_FINGERPRINT_FIELD: fp}},
    )
    result = await recover_auto_sales_receipt_accounting(server.db, draft["id"], ADMIN)
    assert result["financial_complete"] is True
    inv_after = await server.db.sales_invoices.find_one({"id": inv["id"]}, {"_id": 0})
    assert float(inv_after["paid_total"]) == 70_000.0


@pytest.mark.anyio
async def test_invoice_recalc_before_po_heals_on_recover():
    _admin()
    await _set_auto(True)
    await _configure_settings()
    bca = await _cash_bank()
    po = await _seed_po()
    from services.sales_receipt_posting import post_sales_receipt_accounting
    from services.sales_receipts import create_sales_receipt, _recalc_invoice_payment_from_receipts
    from services.sales_receipt_auto_lifecycle import AUTO_ELIGIBLE_FIELD, AUTO_FINGERPRINT_FIELD, canonical_receipt_create_fingerprint, recover_auto_sales_receipt_accounting

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        inv = await _posted_invoice(client, po["id"])
    payload = _payload(inv["id"], bca["id"], amount=55_000)
    draft = await create_sales_receipt(
        server.db, payload, ADMIN, defer_operational_settlement=True, require_posted_invoice_journal=True
    )
    await post_sales_receipt_accounting(server.db, draft["id"], ADMIN, cash_bank_id=bca["id"])
    await server.db.sales_receipts.update_one(
        {"id": draft["id"]}, {"$set": {SETTLEMENT_EFFECTIVE_FIELD: True}}
    )
    await _recalc_invoice_payment_from_receipts(server.db, inv["id"])
    order = await server.db.orders.find_one({"id": po["id"]}, {"_id": 0})
    assert order.get("payment_status") != "Approved" or not order.get("payment_fact")
    fp = canonical_receipt_create_fingerprint(payload)
    await server.db.sales_receipts.update_one(
        {"id": draft["id"]},
        {"$set": {AUTO_ELIGIBLE_FIELD: True, AUTO_FINGERPRINT_FIELD: fp}},
    )
    result = await recover_auto_sales_receipt_accounting(server.db, draft["id"], ADMIN)
    assert result["financial_complete"] is True
    order = await server.db.orders.find_one({"id": po["id"]}, {"_id": 0})
    assert order.get("payment_status") == "Approved"
    assert order.get("payment_fact", {}).get("receipt_id") == draft["id"]


@pytest.mark.anyio
async def test_two_concurrent_recover_workers_one_approval_history():
    _admin()
    await _set_auto(True)
    await _configure_settings()
    bca = await _cash_bank()
    po = await _seed_po()
    from services.sales_receipt_posting import post_sales_receipt_accounting
    from services.sales_receipts import create_sales_receipt
    from services.sales_receipt_auto_lifecycle import (
        AUTO_ELIGIBLE_FIELD,
        AUTO_FINGERPRINT_FIELD,
        canonical_receipt_create_fingerprint,
        recover_auto_sales_receipt_accounting,
    )

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        inv = await _posted_invoice(client, po["id"])
    payload = _payload(inv["id"], bca["id"], amount=33_000)
    draft = await create_sales_receipt(
        server.db, payload, ADMIN, defer_operational_settlement=True, require_posted_invoice_journal=True
    )
    await post_sales_receipt_accounting(server.db, draft["id"], ADMIN, cash_bank_id=bca["id"])
    fp = canonical_receipt_create_fingerprint(payload)
    await server.db.sales_receipts.update_one(
        {"id": draft["id"]},
        {"$set": {AUTO_ELIGIBLE_FIELD: True, AUTO_FINGERPRINT_FIELD: fp}},
    )
    r1, r2 = await asyncio.gather(
        recover_auto_sales_receipt_accounting(server.db, draft["id"], ADMIN),
        recover_auto_sales_receipt_accounting(server.db, draft["id"], ADMIN),
    )
    assert r1["financial_complete"] is True or r2["financial_complete"] is True
    order = await server.db.orders.find_one({"id": po["id"]}, {"_id": 0})
    hist = [h for h in (order.get("payment_approval_history") or []) if h.get("receipt_id") == draft["id"]]
    assert len(hist) <= 1
    assert order.get("payment_status") == "Approved"
    assert len(await _receipt_journals(draft["id"])) == 1


@pytest.mark.anyio
async def test_cancel_during_recovery_fail_closed():
    _admin()
    await _set_auto(True)
    await _configure_settings()
    bca = await _cash_bank()
    po = await _seed_po()
    from services.sales_receipt_posting import post_sales_receipt_accounting
    from services.sales_receipts import create_sales_receipt, apply_operational_settlement_for_receipt
    from services.sales_receipt_auto_lifecycle import ERR_CANCELLED

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        inv = await _posted_invoice(client, po["id"])
    payload = _payload(inv["id"], bca["id"], amount=22_000)
    draft = await create_sales_receipt(
        server.db, payload, ADMIN, defer_operational_settlement=True, require_posted_invoice_journal=True
    )
    await post_sales_receipt_accounting(server.db, draft["id"], ADMIN, cash_bank_id=bca["id"])
    await server.db.sales_receipts.update_one(
        {"id": draft["id"]},
        {"$set": {"financial_status": "cancelled", "cancelled": True}},
    )
    with pytest.raises(Exception) as ei:
        await apply_operational_settlement_for_receipt(server.db, draft["id"], ADMIN)
    detail = getattr(ei.value, "detail", None)
    if isinstance(detail, dict):
        assert detail.get("error_code") == ERR_CANCELLED
    stored = await server.db.sales_receipts.find_one({"id": draft["id"]}, {"_id": 0})
    assert stored.get(SETTLEMENT_EFFECTIVE_FIELD) is not True
    assert len(await _receipt_journals(draft["id"])) == 1  # journal preserved


@pytest.mark.anyio
async def test_invoice_reverse_blocked_while_active_deferred_receipt():
    _admin()
    await _configure_settings()
    bca = await _cash_bank()
    po = await _seed_po()
    from services.sales_receipts import create_sales_receipt
    from services.sales_invoice_posting import collect_invoice_reverse_receipt_blockers

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        inv = await _posted_invoice(client, po["id"])
    draft = await create_sales_receipt(
        server.db,
        _payload(inv["id"], bca["id"], amount=11_000),
        ADMIN,
        defer_operational_settlement=True,
        require_posted_invoice_journal=True,
    )
    assert draft.get(SETTLEMENT_EFFECTIVE_FIELD) is False
    blockers = await collect_invoice_reverse_receipt_blockers(server.db, inv["id"])
    assert any(b.get("receipt_id") == draft["id"] or b.get("id") == draft["id"] for b in blockers) or len(blockers) >= 1


@pytest.mark.anyio
async def test_missing_receipt_index_blocks_activation_and_create():
    await _set_auto(True)
    info = await server.db.sales_receipts.index_information()
    for name, meta in list(info.items()):
        keys = meta.get("key") or []
        fields = [k[0] if isinstance(k, (list, tuple)) else k for k in keys]
        if fields == ["idempotency_key"] and name != "_id_":
            await server.db.sales_receipts.drop_index(name)
    assert await sales_receipt_idempotency_index_ready(server.db) is False
    blockers = await auto_receipt_lifecycle_activation_blockers(server.db)
    assert ERR_INDEX_PREREQUISITE in blockers
    assert AUTO_LIFECYCLE_ACTIVATION_BLOCKED in blockers
    po = await _seed_po()
    bca = await _cash_bank()
    await _configure_settings()
    result = await create_sales_receipt_with_auto_lifecycle(
        server.db, _payload("inv-x", bca["id"]), ADMIN
    )
    assert result["financial_complete"] is False
    assert result["error_code"] == AUTO_LIFECYCLE_ACTIVATION_BLOCKED
    await server.db.sales_receipts.create_index(
        "idempotency_key",
        unique=True,
        partialFilterExpression={"idempotency_key": {"$type": "string"}},
        name="idempotency_key_partial_unique",
    )


@pytest.mark.anyio
async def test_missing_intent_index_blocks_activation():
    await _set_auto(True)
    info = await server.db[INTENT_COLL].index_information()
    for name, meta in list(info.items()):
        keys = meta.get("key") or []
        fields = [k[0] if isinstance(k, (list, tuple)) else k for k in keys]
        if fields == ["idempotency_key"] and name != "_id_":
            await server.db[INTENT_COLL].drop_index(name)
    assert await intent_idempotency_index_ready(server.db) is False
    blockers = await auto_receipt_lifecycle_activation_blockers(server.db)
    assert ERR_INTENT_INDEX_PREREQUISITE in blockers
    assert AUTO_LIFECYCLE_ACTIVATION_BLOCKED in blockers
    await ensure_auto_receipt_intent_indexes(server.db)


@pytest.mark.anyio
async def test_no_financial_success_if_settlement_false():
    from services.sales_receipts import create_sales_receipt, verify_settlement_projections
    from services.sales_receipt_posting import post_sales_receipt_accounting

    await _configure_settings()
    bca = await _cash_bank()
    po = await _seed_po()
    _admin()

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        inv = await _posted_invoice(client, po["id"])
    draft = await create_sales_receipt(
        server.db,
        _payload(inv["id"], bca["id"], amount=15_000),
        ADMIN,
        defer_operational_settlement=True,
        require_posted_invoice_journal=True,
    )
    await post_sales_receipt_accounting(server.db, draft["id"], ADMIN, cash_bank_id=bca["id"])
    check = await verify_settlement_projections(server.db, draft["id"])
    assert check["ok"] is False
    assert check["reason"] == "settlement_not_effective"


@pytest.mark.anyio
async def test_po_updated_before_response_recover_is_idempotent():
    """Crash after PO projection sync but before response: recover must succeed without dup effects."""
    _admin()
    await _set_auto(True)
    await _configure_settings()
    bca = await _cash_bank()
    po = await _seed_po()
    from services.sales_receipt_posting import post_sales_receipt_accounting
    from services.sales_receipts import (
        apply_operational_settlement_for_receipt,
        create_sales_receipt,
        verify_settlement_projections,
    )
    from services.sales_receipt_auto_lifecycle import (
        AUTO_ELIGIBLE_FIELD,
        AUTO_FINGERPRINT_FIELD,
        canonical_receipt_create_fingerprint,
        recover_auto_sales_receipt_accounting,
    )

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        inv = await _posted_invoice(client, po["id"])
    payload = _payload(inv["id"], bca["id"], amount=41_000)
    draft = await create_sales_receipt(
        server.db, payload, ADMIN, defer_operational_settlement=True, require_posted_invoice_journal=True
    )
    await post_sales_receipt_accounting(server.db, draft["id"], ADMIN, cash_bank_id=bca["id"])
    await apply_operational_settlement_for_receipt(server.db, draft["id"], ADMIN)
    assert (await verify_settlement_projections(server.db, draft["id"])).get("ok") is True
    fp = canonical_receipt_create_fingerprint(payload)
    await server.db.sales_receipts.update_one(
        {"id": draft["id"]},
        {"$set": {AUTO_ELIGIBLE_FIELD: True, AUTO_FINGERPRINT_FIELD: fp}},
    )
    result = await recover_auto_sales_receipt_accounting(server.db, draft["id"], ADMIN)
    assert result["financial_complete"] is True
    order = await server.db.orders.find_one({"id": po["id"]}, {"_id": 0})
    hist = [h for h in (order.get("payment_approval_history") or []) if h.get("receipt_id") == draft["id"]]
    assert len(hist) <= 1
    assert len(await _receipt_journals(draft["id"])) == 1


@pytest.mark.anyio
async def test_no_financial_success_if_invoice_snapshot_stale():
    from services.sales_receipts import (
        apply_operational_settlement_for_receipt,
        create_sales_receipt,
        verify_settlement_projections,
    )
    from services.sales_receipt_posting import post_sales_receipt_accounting

    _admin()
    await _configure_settings()
    bca = await _cash_bank()
    po = await _seed_po()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        inv = await _posted_invoice(client, po["id"])
    draft = await create_sales_receipt(
        server.db,
        _payload(inv["id"], bca["id"], amount=18_000),
        ADMIN,
        defer_operational_settlement=True,
        require_posted_invoice_journal=True,
    )
    await post_sales_receipt_accounting(server.db, draft["id"], ADMIN, cash_bank_id=bca["id"])
    await apply_operational_settlement_for_receipt(server.db, draft["id"], ADMIN)
    await server.db.sales_invoices.update_one(
        {"id": inv["id"]},
        {"$set": {"paid_total": 0, "outstanding_amount": 999, "payment_status": "BELUM DIBAYAR"}},
    )
    check = await verify_settlement_projections(server.db, draft["id"])
    assert check["ok"] is False
    assert "stale" in check["reason"]


@pytest.mark.anyio
async def test_explicit_false_never_counted_legacy_absent_counted():
    assert receipt_counts_toward_outstanding({"financial_status": "active", SETTLEMENT_EFFECTIVE_FIELD: False}) is False
    assert receipt_counts_toward_outstanding({"financial_status": "active"}) is True


@pytest.mark.anyio
async def test_flag_off_parity_create_still_settles_without_auto_fields():
    _admin()
    await _set_auto(False)
    await _set_gate(False)
    await _configure_settings()
    bca = await _cash_bank()
    po = await _seed_po()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        inv = await _posted_invoice(client, po["id"])
        res = await client.post(
            "/api/sales-receipts",
            json={
                "invoice_id": inv["id"],
                "receipt_date": "2026-09-05",
                "amount": 12_000,
                "cash_bank_id": bca["id"],
            },
        )
        assert res.status_code == 200
        body = res.json()
        assert "financial_complete" not in body
        receipt = body["receipt"]
        assert receipt.get(AUTO_ELIGIBLE_FIELD) is not True
        stored = await server.db.sales_invoices.find_one({"id": inv["id"]}, {"_id": 0})
        assert float(stored["paid_total"]) == 12_000.0


@pytest.mark.anyio
async def test_auto_settle_completes_status_po_payment_without_payment_files():
    """DP settlement fills Payment + Approval Payment projections without payment_files."""
    from services.status_po import status_po_workflow

    _admin()
    await _set_auto(True)
    await _configure_settings()
    bca = await _cash_bank()
    po = await _seed_po()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        inv = await _posted_invoice(client, po["id"], unit_price=1_000_000)
        res = await client.post("/api/sales-receipts", json=_payload(inv["id"], bca["id"], amount=250_000))
        assert res.status_code == 200, res.text
        assert res.json()["financial_complete"] is True
    order = await server.db.orders.find_one({"id": po["id"]}, {"_id": 0})
    assert order.get("payment_status") == "Approved"
    assert not (order.get("payment_files") or [])
    assert order.get("payment_fact", {}).get("receipt_id")
    wf = status_po_workflow(order)
    assert wf["payment"] == "completed"
    assert wf["appr_payment"] == "completed"


@pytest.mark.anyio
async def test_deferred_unsettled_does_not_approve_payment_workflow():
    _admin()
    await _set_auto(True)
    await _configure_settings()
    bca = await _cash_bank()
    po = await _seed_po()
    from services.sales_receipts import create_sales_receipt
    from services.status_po import status_po_workflow

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        inv = await _posted_invoice(client, po["id"])
    draft = await create_sales_receipt(
        server.db,
        _payload(inv["id"], bca["id"], amount=33_000),
        ADMIN,
        defer_operational_settlement=True,
        require_posted_invoice_journal=True,
    )
    assert draft.get(SETTLEMENT_EFFECTIVE_FIELD) is False
    order = await server.db.orders.find_one({"id": po["id"]}, {"_id": 0})
    assert order.get("payment_status") != "Approved"
    wf = status_po_workflow(order)
    assert wf["payment"] == "pending"
    assert wf["appr_payment"] == "pending"


@pytest.mark.anyio
async def test_reverse_last_receipt_resyncs_approval_when_stage_before_penjadwalan():
    _admin()
    await _set_auto(True)
    await _configure_settings()
    bca = await _cash_bank()
    # Keep design pending so current_stage stays < 5 after payment approve.
    po = await _seed_po()
    await server.db.orders.update_one(
        {"id": po["id"]},
        {"$set": {"design_status": "Waiting", "design_files": [], "current_stage": 0}},
    )
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        inv = await _posted_invoice(client, po["id"], unit_price=500_000)
        created = await client.post(
            "/api/sales-receipts",
            json=_payload(inv["id"], bca["id"], amount=100_000),
        )
        assert created.status_code == 200, created.text
        receipt_id = created.json()["receipt"]["id"]
        order = await server.db.orders.find_one({"id": po["id"]}, {"_id": 0})
        assert order.get("payment_status") == "Approved"
        assert int(order.get("current_stage") or 0) < 5
        rev = await client.post(f"/api/sales-receipts/{receipt_id}/reverse-accounting")
        assert rev.status_code == 200, rev.text
    order_after = await server.db.orders.find_one({"id": po["id"]}, {"_id": 0})
    assert order_after.get("payment_status") == "Waiting"
    assert not order_after.get("payment_fact")
    assert not (order_after.get("payment_receipts") or [])
