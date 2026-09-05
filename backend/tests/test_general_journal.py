"""Jurnal Umum Phase 1 — thin voucher over journal_core."""
from __future__ import annotations

import asyncio
import os
import sys
import uuid
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("MONGO_URL", "mongodb://127.0.0.1:27017")
_worker = os.environ.get("PYTEST_XDIST_WORKER") or "main"
os.environ["DB_NAME"] = f"eternal_packaging_general_journal_test_{_worker}"
os.environ.setdefault("JWT_SECRET", "test-secret-key-12345678901234567890")
os.environ.setdefault("CORS_ORIGINS", "*")
os.environ.setdefault("STORAGE_PROVIDER", "local")

import server
from permissions import ALL_PERMISSIONS
from server import app, current_user
from services.chart_of_accounts import COLLECTION as COA_COLLECTION, create_account, ensure_indexes as ensure_coa
from services.general_journal import (
    COLLECTION as GJ_COLLECTION,
    SOURCE_GENERAL_JOURNAL,
    ensure_indexes as ensure_gj,
)
from services.journal_core import JOURNAL_ENTRIES, JOURNAL_LINES, ensure_indexes as ensure_journal

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
    await ensure_gj(db)
    await ensure_journal(db)
    for coll in (GJ_COLLECTION, COA_COLLECTION, JOURNAL_ENTRIES, JOURNAL_LINES):
        await db[coll].delete_many({})
    yield
    for coll in (GJ_COLLECTION, COA_COLLECTION, JOURNAL_ENTRIES, JOURNAL_LINES):
        await db[coll].delete_many({})


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


async def _coa(code, name, account_type="EXPS"):
    return await create_account(
        server.db, {"code": code, "name": name, "account_type": account_type}, user=_user()
    )


def _lines(a, b, amount=10000, unbalanced=False):
    return [
        {"account_id": a, "debit": amount, "credit": 0, "description": "Dr"},
        {"account_id": b, "debit": 0, "credit": 0 if unbalanced else amount, "description": "Cr"},
    ]


@pytest.mark.anyio
async def test_draft_create_zero_journal():
    admin = _user()
    async with await client_for(admin) as c:
        a = await _coa(f"A{uuid.uuid4().hex[:6]}", "Biaya", "EXPS")
        b = await _coa(f"B{uuid.uuid4().hex[:6]}", "Kas", "BANK")
        before = await server.db[JOURNAL_ENTRIES].count_documents({})
        r = await c.post(
            "/api/ledger/general-journals",
            json={"date": "2026-09-06", "description": "Draft A", "lines": _lines(a["id"], b["id"])},
        )
        assert r.status_code == 201, r.text
        body = r.json()
        assert body["status"] == "draft"
        assert body["posted_journal_id"] is None
        assert await server.db[JOURNAL_ENTRIES].count_documents({}) == before


@pytest.mark.anyio
async def test_edit_delete_draft():
    admin = _user()
    async with await client_for(admin) as c:
        a = await _coa(f"A{uuid.uuid4().hex[:6]}", "Biaya", "EXPS")
        b = await _coa(f"B{uuid.uuid4().hex[:6]}", "Kas", "BANK")
        created = (
            await c.post(
                "/api/ledger/general-journals",
                json={"date": "2026-09-06", "description": "D1", "lines": _lines(a["id"], b["id"], 50)},
            )
        ).json()
        patched = await c.patch(
            f"/api/ledger/general-journals/{created['id']}",
            json={"description": "D1 edited", "lines": _lines(a["id"], b["id"], 75)},
        )
        assert patched.status_code == 200, patched.text
        assert patched.json()["description"] == "D1 edited"
        assert patched.json()["total_debit"] == 75.0
        deleted = await c.delete(f"/api/ledger/general-journals/{created['id']}")
        assert deleted.status_code == 200
        assert await server.db[GJ_COLLECTION].find_one({"id": created["id"]}) is None


@pytest.mark.anyio
async def test_balanced_post_and_source():
    admin = _user()
    async with await client_for(admin) as c:
        a = await _coa(f"A{uuid.uuid4().hex[:6]}", "Biaya", "EXPS")
        b = await _coa(f"B{uuid.uuid4().hex[:6]}", "Kas", "BANK")
        draft = (
            await c.post(
                "/api/ledger/general-journals",
                json={"date": "2026-09-06", "description": "Post me", "lines": _lines(a["id"], b["id"], 12000)},
            )
        ).json()
        posted = await c.post(f"/api/ledger/general-journals/{draft['id']}/post")
        assert posted.status_code == 200, posted.text
        body = posted.json()
        assert body["status"] == "posted"
        assert body["posted_journal_id"]
        assert body["journal"]["source_type"] == SOURCE_GENERAL_JOURNAL
        assert body["journal"]["source_id"] == draft["id"]
        assert body["journal"]["status"] == "posted"
        by_acc = {ln["account_id"]: ln for ln in body["journal"]["lines"]}
        assert by_acc[a["id"]]["debit"] == 12000.0
        assert by_acc[b["id"]]["credit"] == 12000.0


@pytest.mark.anyio
async def test_unbalanced_and_invalid_coa_rejected():
    admin = _user()
    async with await client_for(admin) as c:
        a = await _coa(f"A{uuid.uuid4().hex[:6]}", "Biaya", "EXPS")
        b = await _coa(f"B{uuid.uuid4().hex[:6]}", "Kas", "BANK")
        unbalanced = await c.post(
            "/api/ledger/general-journals",
            json={
                "date": "2026-09-06",
                "description": "Bad",
                "lines": [
                    {"account_id": a["id"], "debit": 100, "credit": 0},
                    {"account_id": b["id"], "debit": 0, "credit": 40},
                ],
            },
        )
        assert unbalanced.status_code == 201  # draft may be unbalanced
        post = await c.post(f"/api/ledger/general-journals/{unbalanced.json()['id']}/post")
        assert post.status_code == 400
        assert "seimbang" in post.json()["detail"].lower() or "≠" in post.json()["detail"]

        bad = await c.post(
            "/api/ledger/general-journals",
            json={
                "date": "2026-09-06",
                "description": "Ghost",
                "lines": [
                    {"account_id": str(uuid.uuid4()), "debit": 10, "credit": 0},
                    {"account_id": b["id"], "debit": 0, "credit": 10},
                ],
            },
        )
        assert bad.status_code == 400
        assert "tidak ditemukan" in bad.json()["detail"].lower()


@pytest.mark.anyio
async def test_posted_immutable_retry_idempotent():
    admin = _user()
    async with await client_for(admin) as c:
        a = await _coa(f"A{uuid.uuid4().hex[:6]}", "Biaya", "EXPS")
        b = await _coa(f"B{uuid.uuid4().hex[:6]}", "Kas", "BANK")
        draft = (
            await c.post(
                "/api/ledger/general-journals",
                json={"date": "2026-09-06", "description": "Imm", "lines": _lines(a["id"], b["id"])},
            )
        ).json()
        p1 = await c.post(f"/api/ledger/general-journals/{draft['id']}/post")
        assert p1.status_code == 200
        jid = p1.json()["posted_journal_id"]
        edit = await c.patch(
            f"/api/ledger/general-journals/{draft['id']}",
            json={"description": "nope"},
        )
        assert edit.status_code == 409
        delete = await c.delete(f"/api/ledger/general-journals/{draft['id']}")
        assert delete.status_code == 409
        p2 = await c.post(f"/api/ledger/general-journals/{draft['id']}/post")
        assert p2.status_code == 200
        assert p2.json()["posted_journal_id"] == jid
        assert await server.db[JOURNAL_ENTRIES].count_documents({
            "source_type": SOURCE_GENERAL_JOURNAL,
            "source_id": draft["id"],
            "reversal_of": None,
        }) == 1


@pytest.mark.anyio
async def test_concurrent_post_no_duplicate():
    admin = _user()
    async with await client_for(admin) as c:
        a = await _coa(f"A{uuid.uuid4().hex[:6]}", "Biaya", "EXPS")
        b = await _coa(f"B{uuid.uuid4().hex[:6]}", "Kas", "BANK")
        draft = (
            await c.post(
                "/api/ledger/general-journals",
                json={"date": "2026-09-06", "description": "Race", "lines": _lines(a["id"], b["id"])},
            )
        ).json()

        async def _post():
            async with await client_for(admin) as c2:
                return await c2.post(f"/api/ledger/general-journals/{draft['id']}/post")

        results = await asyncio.gather(_post(), _post(), _post())
        assert all(r.status_code == 200 for r in results)
        ids = {r.json()["posted_journal_id"] for r in results}
        assert len(ids) == 1
        assert await server.db[JOURNAL_ENTRIES].count_documents({
            "source_type": SOURCE_GENERAL_JOURNAL,
            "source_id": draft["id"],
            "reversal_of": None,
        }) == 1


@pytest.mark.anyio
async def test_reversal_exact_once():
    admin = _user()
    async with await client_for(admin) as c:
        a = await _coa(f"A{uuid.uuid4().hex[:6]}", "Biaya", "EXPS")
        b = await _coa(f"B{uuid.uuid4().hex[:6]}", "Kas", "BANK")
        draft = (
            await c.post(
                "/api/ledger/general-journals",
                json={"date": "2026-09-06", "description": "Rev", "lines": _lines(a["id"], b["id"], 5000)},
            )
        ).json()
        await c.post(f"/api/ledger/general-journals/{draft['id']}/post")
        before = (await c.get(f"/api/ledger/general-journals/{draft['id']}")).json()
        orig = before["journal"]
        rev1 = await c.post(f"/api/ledger/general-journals/{draft['id']}/reverse")
        assert rev1.status_code == 200, rev1.text
        after = rev1.json()
        assert after["status"] == "reversed"
        assert after["reversal_journal"]["reversal_of"] == orig["id"]
        orig_by = {ln["account_id"]: ln for ln in orig["lines"]}
        rev_by = {ln["account_id"]: ln for ln in after["reversal_journal"]["lines"]}
        for aid in orig_by:
            assert rev_by[aid]["debit"] == orig_by[aid]["credit"]
            assert rev_by[aid]["credit"] == orig_by[aid]["debit"]
        rev2 = await c.post(f"/api/ledger/general-journals/{draft['id']}/reverse")
        assert rev2.status_code == 409


@pytest.mark.anyio
async def test_get_zero_write_and_rbac():
    admin = _user()
    async with await client_for(admin) as c:
        a = await _coa(f"A{uuid.uuid4().hex[:6]}", "Biaya", "EXPS")
        b = await _coa(f"B{uuid.uuid4().hex[:6]}", "Kas", "BANK")
        await c.post(
            "/api/ledger/general-journals",
            json={"date": "2026-09-06", "description": "ZW", "lines": _lines(a["id"], b["id"])},
        )
        before_j = await server.db[JOURNAL_ENTRIES].count_documents({})
        before_v = await server.db[GJ_COLLECTION].count_documents({})
        listed = await c.get("/api/ledger/general-journals")
        assert listed.status_code == 200
        assert await server.db[JOURNAL_ENTRIES].count_documents({}) == before_j
        assert await server.db[GJ_COLLECTION].count_documents({}) == before_v

    viewer = _user("viewer", perms=["general_journal.view", "nav.ledger", "nav.ledger.general_journal"])
    async with await client_for(viewer) as c:
        create = await c.post(
            "/api/ledger/general-journals",
            json={"date": "2026-09-06", "description": "Nope", "lines": _lines(
                (await _coa(f"X{uuid.uuid4().hex[:6]}", "X", "EXPS"))["id"],
                (await _coa(f"Y{uuid.uuid4().hex[:6]}", "Y", "BANK"))["id"],
            )},
        )
        assert create.status_code == 403
        listed = await c.get("/api/ledger/general-journals")
        assert listed.status_code == 200

    assert "general_journal.view" in ALL_PERMISSIONS
    assert "general_journal.manage" in ALL_PERMISSIONS
    assert "general_journal.post" in ALL_PERMISSIONS
    assert "general_journal.reverse" in ALL_PERMISSIONS
