"""Phase 1 Kas & Bank owner reconciliation — read-only view tests."""
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
OWNER_DB = f"owner_recon_isolated_{_worker}_{uuid.uuid4().hex[:6]}"
os.environ["DB_NAME"] = OWNER_DB
os.environ.setdefault("JWT_SECRET", "test-secret-key-12345678901234567890")
os.environ.setdefault("CORS_ORIGINS", "*")
os.environ.setdefault("STORAGE_PROVIDER", "local")

import server
from server import app, current_user
from services.cash_bank_master import COLLECTION as CB_COLLECTION, ensure_indexes as ensure_cb
from services.cash_bank_owner_recon import (
    MAX_OWNER_RECON_ACCOUNTS,
    STATUS_BOOK_ONLY,
    STATUS_DUPLIKAT,
    STATUS_REVIEW_CANON,
    STATUS_REVERSED,
    STATUS_SESUAI,
    STATUS_STMT_ONLY,
    get_owner_recon_mtd_global,
    get_owner_recon_view,
)
from services.cash_bank_reconciliation import RECON_MATCHES, RECONCILIATIONS, ensure_indexes as ensure_recon
from services.cash_bank_statement import (
    DUP_CLEAR,
    DUP_DEFINITE,
    IMPORTS,
    LINES,
    STATUS_COMPLETE,
    ensure_indexes as ensure_statement,
)
from services.cash_bank_vouchers import PAYMENTS, RECEIPTS, TRANSFERS, ensure_indexes as ensure_vouchers
from services.chart_of_accounts import COLLECTION as COA_COLLECTION, create_account, ensure_indexes as ensure_coa
from services.journal_core import (
    JOURNAL_ENTRIES,
    JOURNAL_LINES,
    STATUS_POSTED,
    STATUS_REVERSED as JOURNAL_STATUS_REVERSED,
    ensure_indexes as ensure_journal,
)

pytestmark = pytest.mark.anyio

PERIOD = {"start_date": "2026-09-01", "end_date": "2026-09-30"}


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture(autouse=True)
def bind_motor():
    from motor.motor_asyncio import AsyncIOMotorClient
    os.environ["DB_NAME"] = OWNER_DB
    server.client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    server.db = server.client[OWNER_DB]


@pytest.fixture(autouse=True)
async def clean_db(bind_motor):
    db = server.db
    await ensure_coa(db)
    await ensure_cb(db)
    await ensure_vouchers(db)
    await ensure_journal(db)
    await ensure_recon(db)
    await ensure_statement(db)
    names = (
        CB_COLLECTION, COA_COLLECTION, RECEIPTS, PAYMENTS, TRANSFERS,
        JOURNAL_ENTRIES, JOURNAL_LINES, RECONCILIATIONS, RECON_MATCHES,
        IMPORTS, LINES, "cash_bank_manual_statement_balances",
    )
    for name in names:
        await db[name].delete_many({})
    await db["accounting_periods"].update_one(
        {"period_key": "2026-09"},
        {"$set": {"period_key": "2026-09", "status": "open", "writer_claims": []}},
        upsert=True,
    )
    yield
    for name in names:
        await db[name].delete_many({})


def _user(role="admin", perms=None, **kw):
    uid = kw.get("id") or f"u-{role}-{uuid.uuid4().hex[:8]}"
    if role == "admin":
        override = {}
    elif perms is not None:
        override = {p: True for p in perms}
    else:
        override = {}
    return {
        "id": uid, "email": f"{uid}@example.com", "name": role,
        "role": role if role != "viewer" else "operator",
        "status": "active", "user_id": uid, "permissions_override": override,
    }


async def client_for(user):
    app.dependency_overrides[current_user] = lambda: user
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


async def _coa(code, name):
    return await create_account(
        server.db, {"code": code, "name": name, "account_type": "BANK"}, user=_user()
    )


async def _bank(account_id, name="Bank Uji"):
    doc = {
        "id": str(uuid.uuid4()),
        "name": name,
        "account_id": account_id,
        "active": True,
        "currency_code": "IDR",
        "created_at": "2026-09-01T00:00:00+00:00",
    }
    await server.db[CB_COLLECTION].insert_one(doc)
    return doc


async def _journal(*, account_id, date, debit=0, credit=0, source_type="cash_bank_receipt",
                   source_id=None, status=STATUS_POSTED, number=None):  # journal status
    jid = str(uuid.uuid4())
    lid = str(uuid.uuid4())
    sid = source_id or str(uuid.uuid4())
    await server.db[JOURNAL_ENTRIES].insert_one({
        "id": jid,
        "number": number or f"JE-{jid[:6]}",
        "date": date,
        "status": status,
        "source_type": source_type,
        "source_id": sid,
        "description": f"desc {source_type}",
        "created_at": f"{date}T00:00:00+00:00",
    })
    await server.db[JOURNAL_LINES].insert_one({
        "id": lid,
        "journal_id": jid,
        "account_id": account_id,
        "debit": debit,
        "credit": credit,
        "description": f"line {source_type}",
    })
    return {"journal_id": jid, "journal_line_id": lid, "source_id": sid, "source_type": source_type}


async def _import_and_line(*, cash_bank_id, date, amount, direction="in",
                           duplicate_status=DUP_CLEAR, closing_balance=None, closing_date=None,
                           description="Mutasi bank"):
    iid = str(uuid.uuid4())
    lid = str(uuid.uuid4())
    await server.db[IMPORTS].insert_one({
        "id": iid,
        "cash_bank_id": cash_bank_id,
        "status": STATUS_COMPLETE,
        "filename": "mutasi.csv",
        "file_sha256": uuid.uuid4().hex,
        "closing_balance": closing_balance,
        "closing_date": closing_date,
        "created_at": "2026-09-01T00:00:00+00:00",
    })
    await server.db[LINES].insert_one({
        "id": lid,
        "import_id": iid,
        "cash_bank_id": cash_bank_id,
        "date": date,
        "amount": amount,
        "direction": direction,
        "description": description,
        "reference": "REF-1",
        "bank_txn_id": f"txn-{lid[:8]}",
        "duplicate_status": duplicate_status,
        "currency_code": "IDR",
        "sealed": True,
    })
    return {"import_id": iid, "statement_line_id": lid}


async def _pair(*, cash_bank_id, account_id, statement_line_id, journal_line_id, journal_id,
                amount, direction="in", date="2026-09-10"):
    doc = {
        "id": str(uuid.uuid4()),
        "match_kind": "pair",
        "status": "active",
        "cash_bank_id": cash_bank_id,
        "account_id": account_id,
        "statement_line_id": statement_line_id,
        "journal_line_id": journal_line_id,
        "journal_id": journal_id,
        "journal_number": "JE-X",
        "date": date,
        "description": "pair",
        "debit": amount if direction == "in" else 0,
        "credit": amount if direction == "out" else 0,
        "amount": float(amount),
        "direction": direction,
        "currency_code": "IDR",
        "line_status": STATUS_POSTED,
        "idempotency_key": str(uuid.uuid4()),
        "payload_hash": "h",
        "created_at": "2026-09-10T00:00:00+00:00",
    }
    await server.db[RECON_MATCHES].insert_one(doc)
    return doc


async def test_active_pair_sesuai():
    coa = await _coa("1101", "Bank")
    bank = await _bank(coa["id"])
    book = await _journal(account_id=coa["id"], date="2026-09-10", debit=100000, credit=0)
    stmt = await _import_and_line(cash_bank_id=bank["id"], date="2026-09-10", amount=100000, direction="in")
    await _pair(
        cash_bank_id=bank["id"], account_id=coa["id"],
        statement_line_id=stmt["statement_line_id"],
        journal_line_id=book["journal_line_id"], journal_id=book["journal_id"],
        amount=100000, direction="in",
    )
    view = await get_owner_recon_view(
        server.db, cash_bank_id=bank["id"], **PERIOD
    )
    assert view["read_only"] is True
    assert view["auto_match"] is False
    assert any(r["status"] == STATUS_SESUAI for r in view["items"])
    assert view["summary"]["cocok"] >= 1


async def test_statement_unmatched():
    coa = await _coa("1102", "Bank2")
    bank = await _bank(coa["id"], "Bank2")
    await _import_and_line(cash_bank_id=bank["id"], date="2026-09-11", amount=50000, direction="out")
    view = await get_owner_recon_view(server.db, cash_bank_id=bank["id"], **PERIOD)
    assert any(r["status"] == STATUS_STMT_ONLY for r in view["items"])
    assert view["summary"]["belum_cocok"] >= 1


async def test_book_unmatched():
    coa = await _coa("1103", "Bank3")
    bank = await _bank(coa["id"], "Bank3")
    await _journal(account_id=coa["id"], date="2026-09-12", debit=0, credit=25000, source_type="cash_bank_payment")
    view = await get_owner_recon_view(server.db, cash_bank_id=bank["id"], **PERIOD)
    assert any(r["status"] == STATUS_BOOK_ONLY for r in view["items"])


async def test_duplicate_statement():
    coa = await _coa("1104", "Bank4")
    bank = await _bank(coa["id"], "Bank4")
    await _import_and_line(
        cash_bank_id=bank["id"], date="2026-09-13", amount=1000, direction="in",
        duplicate_status=DUP_DEFINITE, description="Duplikat",
    )
    view = await get_owner_recon_view(server.db, cash_bank_id=bank["id"], **PERIOD)
    assert any(r["status"] == STATUS_DUPLIKAT for r in view["items"])


async def test_review_state_perlu_review():
    coa = await _coa("1105", "Bank5")
    bank = await _bank(coa["id"], "Bank5")
    book = await _journal(
        account_id=coa["id"], date="2026-09-14", debit=90000, credit=0, status=STATUS_POSTED
    )
    await server.db[JOURNAL_ENTRIES].update_one(
        {"id": book["journal_id"]},
        {"$set": {
            "status": STATUS_POSTED,
            "reversal_intent": {"operation_id": "op-1", "phase": "lines"},
        }},
    )
    stmt = await _import_and_line(cash_bank_id=bank["id"], date="2026-09-14", amount=90000, direction="in")
    await _pair(
        cash_bank_id=bank["id"], account_id=coa["id"],
        statement_line_id=stmt["statement_line_id"],
        journal_line_id=book["journal_line_id"], journal_id=book["journal_id"],
        amount=90000,
    )
    view = await get_owner_recon_view(server.db, cash_bank_id=bank["id"], **PERIOD)
    assert any(r["status"] == STATUS_REVIEW_CANON for r in view["items"])


async def test_reversed_journal():
    coa = await _coa("1106", "Bank6")
    bank = await _bank(coa["id"], "Bank6")
    await _journal(
        account_id=coa["id"], date="2026-09-15", debit=0, credit=12000,
        source_type="cash_bank_payment", status=JOURNAL_STATUS_REVERSED,
    )
    view = await get_owner_recon_view(server.db, cash_bank_id=bank["id"], **PERIOD)
    assert any(r["status"] == STATUS_REVERSED for r in view["items"])


async def test_balance_difference_and_missing_closing():
    coa = await _coa("1107", "Bank7")
    bank = await _bank(coa["id"], "Bank7")
    await _journal(account_id=coa["id"], date="2026-09-16", debit=200000, credit=0)
    # No closing on end_date → incomplete
    view = await get_owner_recon_view(server.db, cash_bank_id=bank["id"], **PERIOD)
    assert view["statement_closing_complete"] is False
    assert view["statement_closing_note"] == "Statement closing belum lengkap"
    assert view["summary"]["saldo_statement"] is None

    await _import_and_line(
        cash_bank_id=bank["id"], date="2026-09-16", amount=200000, direction="in",
        closing_balance=250000, closing_date="2026-09-30",
    )
    view2 = await get_owner_recon_view(server.db, cash_bank_id=bank["id"], **PERIOD)
    assert view2["statement_closing_complete"] is True
    assert view2["difference"] == float(250000 - view2["book_ending_balance"])


async def test_source_label_fallback_and_known():
    coa = await _coa("1108", "Bank8")
    bank = await _bank(coa["id"], "Bank8")
    await _journal(
        account_id=coa["id"], date="2026-09-17", debit=0, credit=1000,
        source_type="payroll_payment",
    )
    await _journal(
        account_id=coa["id"], date="2026-09-17", debit=0, credit=2000,
        source_type="weird_custom_source",
    )
    view = await get_owner_recon_view(server.db, cash_bank_id=bank["id"], **PERIOD)
    labels = {r["source_label"] for r in view["items"]}
    assert "Pembayaran Payroll" in labels
    assert "weird_custom_source" in labels


async def test_http_owner_view_view_perm_no_writes():
    coa = await _coa("1109", "Bank9")
    bank = await _bank(coa["id"], "Bank9")
    await _import_and_line(cash_bank_id=bank["id"], date="2026-09-18", amount=111, direction="in")
    viewer = _user("viewer", perms=["cash_bank.view"])
    async with await client_for(viewer) as client:
        before_matches = await server.db[RECON_MATCHES].count_documents({})
        before_imports = await server.db[IMPORTS].count_documents({})
        before_lines = await server.db[LINES].count_documents({})
        res = await client.get(
            "/api/cash-bank/reconciliation/owner-view",
            params={"cash_bank_id": bank["id"], **PERIOD},
        )
        assert res.status_code == 200, res.text
        body = res.json()
        assert body["read_only"] is True
        assert body["auto_match"] is False
        assert "items" in body
        assert await server.db[RECON_MATCHES].count_documents({}) == before_matches
        assert await server.db[IMPORTS].count_documents({}) == before_imports
        assert await server.db[LINES].count_documents({}) == before_lines

    # Without view → denied
    bare = _user("viewer", perms=[])
    async with await client_for(bare) as client:
        denied = await client.get(
            "/api/cash-bank/reconciliation/owner-view",
            params={"cash_bank_id": bank["id"], **PERIOD},
        )
        assert denied.status_code in (401, 403)


async def test_status_precedence_overlaps():
    """REVERSED > DUPLIKAT > PERLU_REVIEW > SESUAI when conditions overlap."""
    coa = await _coa("1111", "Bank11")
    bank = await _bank(coa["id"], "Bank11")

    # Reversed journal that also has pending-review intent → REVERSED wins
    book_rev = await _journal(
        account_id=coa["id"], date="2026-09-21", debit=0, credit=8000,
        source_type="cash_bank_payment", status=JOURNAL_STATUS_REVERSED,
    )
    await server.db[JOURNAL_ENTRIES].update_one(
        {"id": book_rev["journal_id"]},
        {"$set": {"reversal_intent": {"operation_id": "op-rev", "phase": "lines"}}},
    )
    stmt_rev = await _import_and_line(
        cash_bank_id=bank["id"], date="2026-09-21", amount=8000, direction="out",
    )
    await _pair(
        cash_bank_id=bank["id"], account_id=coa["id"],
        statement_line_id=stmt_rev["statement_line_id"],
        journal_line_id=book_rev["journal_line_id"], journal_id=book_rev["journal_id"],
        amount=8000, direction="out",
    )

    # Duplicate unmatched statement → DUPLIKAT (not STATEMENT_TANPA_JURNAL)
    await _import_and_line(
        cash_bank_id=bank["id"], date="2026-09-22", amount=333, direction="in",
        duplicate_status=DUP_DEFINITE, description="Dup overlap",
    )

    # Valid amounts pair but pending reversal review → PERLU_REVIEW (not SESUAI)
    book_ok = await _journal(
        account_id=coa["id"], date="2026-09-23", debit=44000, credit=0, status=STATUS_POSTED,
    )
    await server.db[JOURNAL_ENTRIES].update_one(
        {"id": book_ok["journal_id"]},
        {"$set": {"reversal_intent": {"operation_id": "op-2", "phase": "lines"}}},
    )
    stmt_ok = await _import_and_line(
        cash_bank_id=bank["id"], date="2026-09-23", amount=44000, direction="in",
    )
    await _pair(
        cash_bank_id=bank["id"], account_id=coa["id"],
        statement_line_id=stmt_ok["statement_line_id"],
        journal_line_id=book_ok["journal_line_id"], journal_id=book_ok["journal_id"],
        amount=44000, direction="in",
    )

    view = await get_owner_recon_view(server.db, cash_bank_id=bank["id"], **PERIOD)
    rev_rows = [r for r in view["items"] if r.get("nominal") == 8000]
    dup_rows = [r for r in view["items"] if r.get("statement_text") == "Dup overlap"]
    review_rows = [r for r in view["items"] if r.get("nominal") == 44000]
    assert rev_rows and all(r["status"] == STATUS_REVERSED for r in rev_rows)
    assert dup_rows and all(r["status"] == STATUS_DUPLIKAT for r in dup_rows)
    assert review_rows and all(r["status"] == STATUS_REVIEW_CANON for r in review_rows)


async def test_status_filter_belum_cocok():
    coa = await _coa("1110", "Bank10")
    bank = await _bank(coa["id"], "Bank10")
    book = await _journal(account_id=coa["id"], date="2026-09-19", debit=5000, credit=0)
    stmt = await _import_and_line(cash_bank_id=bank["id"], date="2026-09-19", amount=5000, direction="in")
    await _pair(
        cash_bank_id=bank["id"], account_id=coa["id"],
        statement_line_id=stmt["statement_line_id"],
        journal_line_id=book["journal_line_id"], journal_id=book["journal_id"],
        amount=5000,
    )
    await _import_and_line(cash_bank_id=bank["id"], date="2026-09-20", amount=77, direction="out")
    view = await get_owner_recon_view(
        server.db, cash_bank_id=bank["id"], status="BELUM_DICOCOKKAN", **PERIOD
    )
    assert all(
        r["status"] in (STATUS_STMT_ONLY, STATUS_BOOK_ONLY, "BELUM_DICOCOKKAN")
        for r in view["items"]
    )
    assert not any(r["status"] == STATUS_SESUAI for r in view["items"])


async def test_mtd_global_incomplete_preserves_counts():
    from datetime import date

    coa1 = await _coa("1121", "BankG1")
    bank1 = await _bank(coa1["id"], "BankG1")
    coa2 = await _coa("1122", "BankG2")
    bank2 = await _bank(coa2["id"], "BankG2")
    await _journal(account_id=coa1["id"], date="2026-09-10", debit=1000, credit=0)
    await _import_and_line(cash_bank_id=bank1["id"], date="2026-09-11", amount=500, direction="in")
    await _journal(account_id=coa2["id"], date="2026-09-12", debit=2000, credit=0)
    # No closing → incomplete, but counts still readable.
    summary = await get_owner_recon_mtd_global(server.db, today=date(2026, 9, 30))
    assert summary["start_date"] == "2026-09-01"
    assert summary["end_date"] == "2026-09-30"
    assert summary["status"] == "incomplete"
    assert summary["status_label"] == "Belum Lengkap"
    assert summary["balance_match_declared"] is False
    assert summary["accounts_total"] == 2
    assert summary["accounts_read"] == 2
    assert summary["unmatched_count"] is not None and summary["unmatched_count"] >= 1
    assert summary["review_count"] is not None


async def test_mtd_global_partial_read_nulls_counts(monkeypatch):
    from datetime import date

    coa = await _coa("1123", "BankG3")
    await _bank(coa["id"], "BankG3")
    calls = {"n": 0}

    async def flaky(*args, **kwargs):
        calls["n"] += 1
        raise RuntimeError("boom")

    monkeypatch.setattr(
        "services.cash_bank_owner_recon.get_owner_recon_view",
        flaky,
    )
    summary = await get_owner_recon_mtd_global(server.db, today=date(2026, 9, 15))
    assert summary["status_label"] == "Belum Lengkap"
    assert summary["unmatched_count"] is None
    assert summary["review_count"] is None
    assert summary["accounts_total"] >= 1


async def test_mtd_global_caps_unbounded_account_fanout(monkeypatch):
    from datetime import date

    accounts = [
        {"id": f"cb-{i}", "name": f"B{i}", "account_id": f"acc-{i}"}
        for i in range(MAX_OWNER_RECON_ACCOUNTS + 3)
    ]

    async def many_accounts(_db):
        return accounts

    monkeypatch.setattr(
        "services.cash_bank_owner_recon.list_eligible_recon_cash_banks",
        many_accounts,
    )
    summary = await get_owner_recon_mtd_global(server.db, today=date(2026, 9, 15))
    assert summary["status"] == "unavailable"
    assert summary["unmatched_count"] is None
    assert summary["review_count"] is None
    assert MAX_OWNER_RECON_ACCOUNTS < summary["accounts_total"]


async def _group(*, cash_bank_id, account_id, journal_line_id, journal_id, members, amount=1000000):
    group_id = str(uuid.uuid4())
    await server.db[RECON_MATCHES].insert_one({
        "id": group_id,
        "match_kind": "pair_group",
        "status": "active",
        "cash_bank_id": cash_bank_id,
        "account_id": account_id,
        "journal_line_id": journal_line_id,
        "journal_id": journal_id,
        "journal_number": "JE-G",
        "date": "2026-09-10",
        "description": "group",
        "debit": amount,
        "credit": 0,
        "amount": float(amount),
        "direction": "in",
        "currency_code": "IDR",
        "line_status": STATUS_POSTED,
        "statement_line_ids": [m["statement_line_id"] for m in members],
        "member_snapshots": [
            {
                "statement_line_id": m["statement_line_id"],
                "amount": float(m["amount"]),
                "direction": "in",
                "date": m["date"],
                "description": m.get("description") or "m",
                "reference": "R",
            }
            for m in members
        ],
        "idempotency_key": str(uuid.uuid4()),
        "payload_hash": "hg",
        "created_at": "2026-09-10T00:00:00+00:00",
    })
    for m in members:
        await server.db[RECON_MATCHES].insert_one({
            "id": str(uuid.uuid4()),
            "match_kind": "pair_group_member",
            "status": "active",
            "group_id": group_id,
            "cash_bank_id": cash_bank_id,
            "statement_line_id": m["statement_line_id"],
            "amount": float(m["amount"]),
            "direction": "in",
            "date": m["date"],
            "description": m.get("description") or "m",
            "reference": "R",
            "currency_code": "IDR",
            "created_at": "2026-09-10T00:00:00+00:00",
        })
    return group_id


async def test_active_pair_group_members_sesuai_no_double_count():
    coa = await _coa("1181", "BankG")
    bank = await _bank(coa["id"], "Bank Group")
    book = await _journal(account_id=coa["id"], date="2026-09-10", debit=1000000, credit=0)
    s1 = await _import_and_line(cash_bank_id=bank["id"], date="2026-09-08", amount=200000, direction="in", description="G200")
    s2 = await _import_and_line(cash_bank_id=bank["id"], date="2026-09-09", amount=500000, direction="in", description="G500")
    s3 = await _import_and_line(cash_bank_id=bank["id"], date="2026-09-10", amount=300000, direction="in", description="G300")
    group_id = await _group(
        cash_bank_id=bank["id"],
        account_id=coa["id"],
        journal_line_id=book["journal_line_id"],
        journal_id=book["journal_id"],
        members=[
            {"statement_line_id": s1["statement_line_id"], "amount": 200000, "date": "2026-09-08"},
            {"statement_line_id": s2["statement_line_id"], "amount": 500000, "date": "2026-09-09"},
            {"statement_line_id": s3["statement_line_id"], "amount": 300000, "date": "2026-09-10"},
        ],
    )
    view = await get_owner_recon_view(server.db, cash_bank_id=bank["id"], **PERIOD)
    group_rows = [r for r in view["items"] if str(r.get("id") or "").startswith("group:")]
    assert len(group_rows) == 1
    row = group_rows[0]
    assert row["status"] == STATUS_SESUAI
    assert row["match"]["match_kind"] == "pair_group"
    assert row["match"]["member_count"] == 3
    assert row["nominal"] == 1000000.0
    # Members must not appear as unmatched statement rows.
    stmt_ids = {s1["statement_line_id"], s2["statement_line_id"], s3["statement_line_id"]}
    unmatched_stmt = [
        r for r in view["items"]
        if r.get("status") == STATUS_STMT_ONLY and (r.get("statement") or {}).get("id") in stmt_ids
    ]
    assert unmatched_stmt == []
    book_unmatched = [
        r for r in view["items"]
        if r.get("status") == STATUS_BOOK_ONLY
        and (r.get("book") or {}).get("journal_line_id") == book["journal_line_id"]
    ]
    assert book_unmatched == []
    assert view["summary"]["cocok"] == 1
    assert view["summary"]["belum_cocok"] == 0
    assert any(m.get("statement_line_id") in stmt_ids for m in (row["match"].get("members") or []))
    assert row["match"]["id"] == group_id


async def test_cancelled_pair_group_not_treated_as_active():
    coa = await _coa("1182", "BankGC")
    bank = await _bank(coa["id"], "Bank Group Cancelled")
    book = await _journal(account_id=coa["id"], date="2026-09-11", debit=1000000, credit=0)
    s1 = await _import_and_line(cash_bank_id=bank["id"], date="2026-09-08", amount=400000, direction="in")
    s2 = await _import_and_line(cash_bank_id=bank["id"], date="2026-09-09", amount=600000, direction="in")
    group_id = await _group(
        cash_bank_id=bank["id"],
        account_id=coa["id"],
        journal_line_id=book["journal_line_id"],
        journal_id=book["journal_id"],
        members=[
            {"statement_line_id": s1["statement_line_id"], "amount": 400000, "date": "2026-09-08"},
            {"statement_line_id": s2["statement_line_id"], "amount": 600000, "date": "2026-09-09"},
        ],
    )
    await server.db[RECON_MATCHES].update_one(
        {"id": group_id},
        {"$set": {"status": "cancelled"}, "$unset": {"journal_line_id": ""}},
    )
    await server.db[RECON_MATCHES].update_many(
        {"group_id": group_id, "match_kind": "pair_group_member"},
        {"$set": {"status": "cancelled"}, "$unset": {"statement_line_id": ""}},
    )
    view = await get_owner_recon_view(server.db, cash_bank_id=bank["id"], **PERIOD)
    assert not any(str(r.get("id") or "").startswith("group:") for r in view["items"])
    assert view["summary"]["cocok"] == 0
    assert view["summary"]["belum_cocok"] >= 3


async def test_pair_and_group_coexist_in_owner_view():
    coa = await _coa("1183", "BankMix")
    bank = await _bank(coa["id"], "Bank Mix")
    book1 = await _journal(account_id=coa["id"], date="2026-09-12", debit=100000, credit=0)
    book2 = await _journal(account_id=coa["id"], date="2026-09-13", debit=1000000, credit=0)
    stmt = await _import_and_line(cash_bank_id=bank["id"], date="2026-09-12", amount=100000, direction="in")
    await _pair(
        cash_bank_id=bank["id"], account_id=coa["id"],
        statement_line_id=stmt["statement_line_id"],
        journal_line_id=book1["journal_line_id"], journal_id=book1["journal_id"],
        amount=100000, direction="in", date="2026-09-12",
    )
    s1 = await _import_and_line(cash_bank_id=bank["id"], date="2026-09-08", amount=200000, direction="in")
    s2 = await _import_and_line(cash_bank_id=bank["id"], date="2026-09-09", amount=800000, direction="in")
    await _group(
        cash_bank_id=bank["id"],
        account_id=coa["id"],
        journal_line_id=book2["journal_line_id"],
        journal_id=book2["journal_id"],
        members=[
            {"statement_line_id": s1["statement_line_id"], "amount": 200000, "date": "2026-09-08"},
            {"statement_line_id": s2["statement_line_id"], "amount": 800000, "date": "2026-09-09"},
        ],
    )
    view = await get_owner_recon_view(server.db, cash_bank_id=bank["id"], **PERIOD)
    kinds = {(r.get("match") or {}).get("match_kind") for r in view["items"] if r.get("match")}
    assert "pair" in kinds
    assert "pair_group" in kinds
    assert view["summary"]["cocok"] == 2
