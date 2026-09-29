"""Commit of the recognized statement layout. Isolated Mongo only."""

from __future__ import annotations

import importlib.util
import os
import sys
import uuid
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("MONGO_URL", "mongodb://127.0.0.1:27017")
LAYOUT_DB = (
    f"recon_layout_isolated_{os.environ.get('PYTEST_XDIST_WORKER') or 'main'}_{uuid.uuid4().hex[:6]}"
)
os.environ["DB_NAME"] = LAYOUT_DB
os.environ.setdefault("JWT_SECRET", "test-secret-key-12345678901234567890")
os.environ.setdefault("CORS_ORIGINS", "*")
os.environ.setdefault("STORAGE_PROVIDER", "local")

import server  # noqa: E402
from services.cash_bank_master import (  # noqa: E402
    COLLECTION as CB_COLLECTION,
    ensure_indexes as ensure_cb,
    update_cash_bank,
)
from services.cash_bank_master import create_cash_bank  # noqa: E402
from services.cash_bank_reconciliation import RECON_MATCHES, ensure_indexes as ensure_recon  # noqa: E402
from services.cash_bank_statement import (  # noqa: E402
    IMPORTS,
    LINES,
    PREVIEWS,
    commit_import,
    confirm_statement_date,
    create_preview,
    ensure_indexes as ensure_statement,
    line_matchable,
)
from services.chart_of_accounts import COLLECTION as COA_COLLECTION, create_account, ensure_indexes as ensure_coa  # noqa: E402
from services.journal_core import JOURNAL_ENTRIES, ensure_indexes as ensure_journal  # noqa: E402

_FIXTURE_PATH = Path(__file__).with_name("test_cash_bank_statement_layout.py")
_spec = importlib.util.spec_from_file_location("statement_layout_fixture", _FIXTURE_PATH)
_fixture = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_fixture)
FIXTURE = _fixture.FIXTURE

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture(autouse=True)
def bind_motor():
    from motor.motor_asyncio import AsyncIOMotorClient
    os.environ["DB_NAME"] = LAYOUT_DB
    server.client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    server.db = server.client[LAYOUT_DB]


@pytest.fixture(autouse=True)
async def clean_db(bind_motor):
    db = server.db
    assert db.name == LAYOUT_DB
    for name in (CB_COLLECTION, COA_COLLECTION, IMPORTS, LINES, PREVIEWS, RECON_MATCHES, JOURNAL_ENTRIES):
        await db[name].drop()
    await ensure_coa(db)
    await ensure_cb(db)
    await ensure_recon(db)
    await ensure_journal(db)
    await ensure_statement(db)
    yield
    for name in (CB_COLLECTION, COA_COLLECTION, IMPORTS, LINES, PREVIEWS, RECON_MATCHES, JOURNAL_ENTRIES):
        await db[name].drop()


def _user():
    uid = f"u-admin-{uuid.uuid4().hex[:8]}"
    return {
        "id": uid, "email": f"{uid}@example.com", "name": "admin", "role": "admin",
        "status": "active", "user_id": uid, "permissions_override": {},
    }


async def _bank():
    user = _user()
    cash = await create_account(
        server.db, {"code": f"B{uuid.uuid4().hex[:6]}", "name": "Bank", "account_type": "BANK"}, user=user,
    )
    bank = await create_cash_bank(
        server.db, {"name": "Bank Uji", "account_id": cash["id"], "active": True}, user=user,
    )
    return await update_cash_bank(server.db, bank["id"], {"currency_code": "IDR"}, user=user), user


async def test_commit_keeps_similar_rows_and_reimport_does_not_duplicate_the_batch():
    bank, user = await _bank()
    data = FIXTURE.encode()
    preview = await create_preview(
        server.db, cash_bank_id=bank["id"], filename="mutasi.csv", data=data, user=user,
    )
    assert preview["suggested_mapping"]["amount_mode"] == "indicator"
    assert preview["suggested_mapping"]["bank_txn_id_authoritative"] is False
    assert preview["persisted_statement_lines"] is False
    journals_before = await server.db[JOURNAL_ENTRIES].count_documents({})
    first = await commit_import(
        server.db, preview_id=preview["id"], mapping_raw=preview["suggested_mapping"], user=user,
    )
    assert first["status"] == "complete"
    assert first["stored_count"] == 4
    assert first["file_count"] == 4
    assert first["ready_count"] == 3
    assert first["unconfirmed_count"] == 1
    assert first["closing_date"] is None
    assert await server.db[LINES].count_documents({"import_id": first["id"]}) == 4
    rows = await server.db[LINES].find(
        {"import_id": first["id"]},
        {"_id": 0, "id": 1, "description": 1, "duplicate_status": 1, "bank_txn_id": 1, "authoritative_txn_id": 1, "date": 1, "raw_date": 1, "date_status": 1, "direction": 1, "amount": 1, "currency_code": 1},
    ).to_list(10)
    twins = [row for row in rows if row["description"] == "SETORAN TUNAI SAMA"]
    assert len(twins) == 2
    assert {row["duplicate_status"] for row in twins} == {"pending_review"}
    assert all(row["bank_txn_id"] in (None, "") for row in rows)
    assert all(row.get("authoritative_txn_id") in (None, "") for row in rows)
    assert any("BIAYA ADMIN, CABANG\nLANJUTAN URAIAN" == row["description"] for row in rows)
    pending = next(row for row in rows if row["raw_date"] == "PEND")
    assert pending["date"] is None
    assert pending["date_status"] == "unconfirmed"
    assert "2909" in pending["description"]
    assert line_matchable(pending, "IDR") == (False, "Tanggal belum pasti. Konfirmasi tanggal sebelum pencocokan.")
    confirmed = await confirm_statement_date(
        server.db, pending["id"], statement_date="2026-10-02", reason="Tanggal diisi operator",
        idempotency_key="pend-date-1", user=user,
    )
    assert confirmed["date"] == "2026-10-02"
    assert confirmed["date_status"] == "certain"
    assert confirmed["description"] == pending["description"]
    assert confirmed["suggested_date"] is None
    assert line_matchable(confirmed, "IDR")[0] is True
    assert await server.db[RECON_MATCHES].count_documents({}) == 0
    assert await server.db[JOURNAL_ENTRIES].count_documents({}) == journals_before
    assert await server.db[RECON_MATCHES].count_documents({}) == 0

    again = await create_preview(
        server.db, cash_bank_id=bank["id"], filename="mutasi-ulang.csv", data=data, user=user,
    )
    second = await commit_import(
        server.db, preview_id=again["id"], mapping_raw=again["suggested_mapping"], user=user,
    )
    assert second["id"] == first["id"]
    assert await server.db[LINES].count_documents({"import_id": first["id"]}) == 4
    assert await server.db[IMPORTS].count_documents({"cash_bank_id": bank["id"]}) == 1
