"""Prevent piece-operator progress without work_date + rate_per_pcs snapshot. Local DB only."""
from __future__ import annotations

import os
import sys
import uuid
from datetime import date
from pathlib import Path
from unittest.mock import patch

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("MONGO_URL", "mongodb://127.0.0.1:27017")
os.environ.setdefault("DB_NAME", "eternal_packaging_progress_rate_guard_test")
os.environ.setdefault("JWT_SECRET", "test-secret-key-12345678901234567890")
os.environ.setdefault("CORS_ORIGINS", "*")
os.environ.setdefault("STORAGE_PROVIDER", "local")

import server
from operator_analytics import wib_today
from server import app, current_user
from services.hr_payroll import EMPLOYEES, ensure_indexes
from services.operator_payroll import SETTINGS_KEY as WAGE_KEY, snapshot_on_save

_RATE_GUARD_DB = "eternal_packaging_progress_rate_guard_test"
_CONFIRM_DAY = date(2026, 10, 7)


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture(autouse=True)
async def bind_and_clean():
    from motor.motor_asyncio import AsyncIOMotorClient

    previous = os.environ.get("DB_NAME")
    os.environ["DB_NAME"] = _RATE_GUARD_DB
    server.client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    server.db = server.client[_RATE_GUARD_DB]
    await server.db[EMPLOYEES].delete_many({})
    await server.db.orders.delete_many({})
    await server.db.users.delete_many({})
    await server.db.app_settings.delete_many({"key": WAGE_KEY})
    await ensure_indexes(server.db)
    yield
    app.dependency_overrides.pop(current_user, None)
    if previous is None:
        os.environ.pop("DB_NAME", None)
    else:
        os.environ["DB_NAME"] = previous


def _admin():
    user = {"id": "rate-guard-admin", "email": "rate-guard@example.com", "name": "Admin", "role": "admin"}
    app.dependency_overrides[current_user] = lambda: user
    return user


def _client():
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


def _day(n: int) -> str:
    today = wib_today()
    year = today.year if today.month > 1 else today.year - 1
    month = today.month - 1 if today.month > 1 else 12
    return date(year, month, n).isoformat()


async def _wage(rate=100):
    doc = {"key": WAGE_KEY}
    if rate is not None:
        doc["default_rate_per_pcs"] = rate
    else:
        doc["default_rate_per_pcs"] = None
        doc["operators"] = []
    await server.db.app_settings.update_one({"key": WAGE_KEY}, {"$set": doc}, upsert=True)


async def _wage_history_oct():
    """Rate 20 from Oct 1, rate 25 from Oct 5. Confirmation day Oct 7 would wrongly pick 25."""
    first = snapshot_on_save(
        {},
        {"default_rate_per_pcs": 20},
        {"email": "admin"},
        "2026-10-01T00:00:00+07:00",
        today=date(2026, 10, 1),
    )
    later = snapshot_on_save(
        first,
        {"default_rate_per_pcs": 25},
        {"email": "admin"},
        "2026-10-05T00:00:00+07:00",
        today=date(2026, 10, 5),
    )
    later["key"] = WAGE_KEY
    await server.db.app_settings.update_one({"key": WAGE_KEY}, {"$set": later}, upsert=True)


async def _person(c, *, name, basis="piece"):
    uid = f"u-{uuid.uuid4().hex[:8]}"
    await server.db.users.insert_one({
        "id": uid, "email": f"{uid}@example.com", "name": name, "role": "operator", "status": "active",
    })
    created = await c.post("/api/hr-payroll/employees", json={
        "name": name,
        "employee_number": f"NIK-{uid[-6:]}",
        "user_id": uid,
        "role": "operator",
        "division": "operator",
        "pay_basis": basis,
        "salary_profile": {"base_salary": 5_000_000},
    })
    assert created.status_code == 200, created.text
    return created.json()["employee"], uid


async def _order(*, uid, name, qty=1000, with_legacy=False, qty_completed=0):
    oid = f"ord-{uuid.uuid4().hex[:6]}"
    sid = f"spl-{uuid.uuid4().hex[:6]}"
    entries = []
    if with_legacy:
        entries.append({
            "id": "legacy",
            "action_id": f"legacy-operator-{sid}",
            "qty": 50,
            "stage": "operator",
            "legacy": True,
            "locked": True,
            "po_id": oid,
            "split_id": sid,
        })
        qty_completed = max(qty_completed, 50)
    await server.db.orders.insert_one({
        "id": oid,
        "po_number": f"PO-{oid[-4:]}",
        "updated_at": "2026-09-01T00:00:00+00:00",
        "design_status": "Approved",
        "payment_status": "Approved",
        "sample_approved": True,
        "sample_files": [{"id": "sample"}],
        "helper_prep": {"qty_prepared": qty},
        "screener_info": {"screen_qty": 1},
        "line_items": [{"quantity": qty, "production_process": "print", "name": "Cup"}],
        "current_stage": 8,
        "operator_splits": [{
            "id": sid,
            "quantity": qty,
            "line_idx": 0,
            "qty_completed": qty_completed,
            "operator_user_id": uid,
            "operator_email": f"{uid}@example.com",
            "operator_name": name,
            "progress_entries": entries,
        }],
    })
    return oid, sid


@pytest.mark.anyio
async def test_piece_operator_with_rate_saves_snapshot_as_of_work_date():
    _admin()
    await _wage(100)
    async with _client() as c:
        emp, uid = await _person(c, name="Budi Piece")
        oid, sid = await _order(uid=uid, name="Budi Piece")
        before = await server.db.orders.find_one({"id": oid}, {"_id": 0})
        assert before["operator_splits"][0]["qty_completed"] == 0
        work_date = _day(6)
        res = await c.post(f"/api/orders/{oid}/operator-progress", json={
            "operator_id": sid, "qty": 120, "action_id": "act-ok", "work_date": work_date,
        })
        assert res.status_code == 200, res.text
        order = await server.db.orders.find_one({"id": oid}, {"_id": 0})
        split = order["operator_splits"][0]
        assert split["qty_completed"] == 120
        entries = [e for e in split["progress_entries"] if not e.get("legacy")]
        assert len(entries) == 1
        assert entries[0]["rate_per_pcs"] == 100
        assert entries[0]["work_date"] == work_date
        assert entries[0]["qty"] == 120
        assert entries[0]["operator_user_id"] == uid


@pytest.mark.anyio
async def test_piece_backdated_work_date_uses_rate_effective_on_work_date_not_confirm_day():
    """rate 20 @ Oct 1, rate 25 @ Oct 5; confirm Oct 7; work_date Oct 3 → snapshot 20."""
    _admin()
    await _wage_history_oct()
    async with _client() as c:
        emp, uid = await _person(c, name="Budi Backdate")
        oid, sid = await _order(uid=uid, name="Budi Backdate")
        with patch.object(server, "wib_today", return_value=_CONFIRM_DAY):
            res = await c.post(f"/api/orders/{oid}/operator-progress", json={
                "operator_id": sid,
                "qty": 75,
                "action_id": "act-backdate",
                "work_date": "2026-10-03",
            })
        assert res.status_code == 200, res.text
        order = await server.db.orders.find_one({"id": oid}, {"_id": 0})
        entry = [e for e in order["operator_splits"][0]["progress_entries"] if not e.get("legacy")][0]
        assert entry["rate_per_pcs"] == 20
        assert entry["work_date"] == "2026-10-03"
        assert entry["qty"] == 75


@pytest.mark.anyio
async def test_piece_missing_work_date_rejected_no_partial_write():
    _admin()
    await _wage(100)
    async with _client() as c:
        emp, uid = await _person(c, name="Budi No Date")
        oid, sid = await _order(uid=uid, name="Budi No Date")
        res = await c.post(f"/api/orders/{oid}/operator-progress", json={
            "operator_id": sid, "qty": 80, "action_id": "act-no-work-date",
        })
        assert res.status_code == 400, res.text
        detail = res.json()["detail"]
        assert "Tanggal pekerjaan wajib diisi" in detail
        assert "borongan" in detail
        order = await server.db.orders.find_one({"id": oid}, {"_id": 0})
        split = order["operator_splits"][0]
        assert split["qty_completed"] == 0
        assert split["progress_entries"] == []


@pytest.mark.anyio
async def test_piece_operator_without_rate_rejected_no_partial_write():
    _admin()
    await _wage(None)
    async with _client() as c:
        emp, uid = await _person(c, name="Vendi")
        oid, sid = await _order(uid=uid, name="Vendi")
        res = await c.post(f"/api/orders/{oid}/operator-progress", json={
            "operator_id": sid, "qty": 80, "action_id": "act-no-rate", "work_date": _day(6),
        })
        assert res.status_code == 400, res.text
        detail = res.json()["detail"]
        assert "Tarif borongan untuk Vendi belum tersedia" in detail
        assert "Lengkapi tarif" in detail
        order = await server.db.orders.find_one({"id": oid}, {"_id": 0})
        split = order["operator_splits"][0]
        assert split["qty_completed"] == 0
        assert split["progress_entries"] == []


@pytest.mark.anyio
async def test_non_piece_operator_without_rate_still_saves_without_work_date():
    _admin()
    await _wage(None)
    async with _client() as c:
        emp, uid = await _person(c, name="Sari Tetap", basis="monthly")
        oid, sid = await _order(uid=uid, name="Sari Tetap")
        res = await c.post(f"/api/orders/{oid}/operator-progress", json={
            "operator_id": sid, "qty": 55, "action_id": "act-monthly",
        })
        assert res.status_code == 200, res.text
        order = await server.db.orders.find_one({"id": oid}, {"_id": 0})
        entry = order["operator_splits"][0]["progress_entries"][0]
        assert entry["qty"] == 55
        assert "rate_per_pcs" not in entry or entry.get("rate_per_pcs") in (None, "")
        assert "work_date" not in entry or entry.get("work_date") in (None, "")


@pytest.mark.anyio
async def test_legacy_entry_untouched_when_piece_rate_missing():
    _admin()
    await _wage(None)
    async with _client() as c:
        emp, uid = await _person(c, name="Vendi Legacy")
        oid, sid = await _order(uid=uid, name="Vendi Legacy", with_legacy=True, qty_completed=50)
        before = await server.db.orders.find_one({"id": oid}, {"_id": 0})
        legacy = before["operator_splits"][0]["progress_entries"][0]
        res = await c.post(f"/api/orders/{oid}/operator-progress", json={
            "operator_id": sid, "qty": 10, "action_id": "act-after-legacy", "work_date": _day(6),
        })
        assert res.status_code == 400, res.text
        after = await server.db.orders.find_one({"id": oid}, {"_id": 0})
        split = after["operator_splits"][0]
        assert split["qty_completed"] == 50
        assert len(split["progress_entries"]) == 1
        assert split["progress_entries"][0]["action_id"] == legacy["action_id"]
        assert split["progress_entries"][0].get("legacy") is True


@pytest.mark.anyio
async def test_piece_rate_snapshot_immutable_after_master_change():
    _admin()
    await _wage(100)
    async with _client() as c:
        emp, uid = await _person(c, name="Budi Snap")
        oid, sid = await _order(uid=uid, name="Budi Snap")
        first = await c.post(f"/api/orders/{oid}/operator-progress", json={
            "operator_id": sid, "qty": 40, "action_id": "act-snap", "work_date": _day(6),
        })
        assert first.status_code == 200, first.text
        await _wage(999)
        order = await server.db.orders.find_one({"id": oid}, {"_id": 0})
        entry = [e for e in order["operator_splits"][0]["progress_entries"] if not e.get("legacy")][0]
        assert entry["rate_per_pcs"] == 100


@pytest.mark.anyio
async def test_idempotent_retry_after_success_does_not_require_rate_again():
    """Existing action_id returns early; later unset wage must not rewrite or 400 the retry."""
    _admin()
    await _wage(100)
    async with _client() as c:
        emp, uid = await _person(c, name="Budi Retry")
        oid, sid = await _order(uid=uid, name="Budi Retry")
        action = "act-idem"
        first = await c.post(f"/api/orders/{oid}/operator-progress", json={
            "operator_id": sid, "qty": 30, "action_id": action, "work_date": _day(6),
        })
        assert first.status_code == 200, first.text
        await _wage(None)
        retry = await c.post(f"/api/orders/{oid}/operator-progress", json={
            "operator_id": sid, "qty": 90, "action_id": action, "work_date": _day(7),
        })
        assert retry.status_code == 200, retry.text
        order = await server.db.orders.find_one({"id": oid}, {"_id": 0})
        entries = [e for e in order["operator_splits"][0]["progress_entries"] if not e.get("legacy")]
        assert len(entries) == 1
        assert entries[0]["qty"] == 30
        assert entries[0]["rate_per_pcs"] == 100
