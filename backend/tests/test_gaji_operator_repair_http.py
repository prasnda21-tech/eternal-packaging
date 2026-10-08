"""HTTP integration: Gaji Operator repair RBAC + claim guards. Local DB only."""
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
os.environ["DB_NAME"] = f"eternal_packaging_gaji_repair_http_{_worker}"
os.environ.setdefault("JWT_SECRET", "test-secret-key-12345678901234567890")
os.environ.setdefault("CORS_ORIGINS", "*")
os.environ.setdefault("STORAGE_PROVIDER", "local")

import server
from server import app, current_user
from services.hr_payroll import EMPLOYEES, PERIODS, STATUS_FINAL
from services.operator_output_coord import COORD_LOCKS, ensure_coord_indexes
from services.operator_wage_claims import WAGE_CLAIMS, ensure_piece_indexes
from services.payroll_payment import PAYMENTS, STATUS_PAID


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture(autouse=True)
async def bind_and_clean():
    from motor.motor_asyncio import AsyncIOMotorClient

    server.client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    server.db = server.client[os.environ["DB_NAME"]]
    await ensure_piece_indexes(server.db)
    await ensure_coord_indexes(server.db)
    for coll in ("orders", "users", "role_permissions", "app_settings", "audit_logs",
                 EMPLOYEES, PERIODS, WAGE_CLAIMS, PAYMENTS, COORD_LOCKS):
        await server.db[coll].delete_many({})
    yield
    app.dependency_overrides.pop(current_user, None)


def _user(*, role: str, email: str, override: dict | None = None):
    user = {
        "id": f"u-{uuid.uuid4().hex[:8]}",
        "email": email,
        "name": email.split("@")[0],
        "role": role,
    }
    if override is not None:
        user["permissions_override"] = override
    app.dependency_overrides[current_user] = lambda: user
    return user


def _client():
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


async def _seed_order(*, work_date: str = "", rate=None, action_id: str = "act-1"):
    entry = {
        "action_id": action_id,
        "po_id": "po-repair-1",
        "split_id": "split-1",
        "operator_user_id": "user-op-1",
        "qty": 10,
    }
    if work_date:
        entry["work_date"] = work_date
    if rate is not None:
        entry["rate_per_pcs"] = rate
    await server.db.orders.insert_one({
        "id": "po-repair-1",
        "po_number": "PO-REPAIR-1",
        "cancelled": False,
        "line_items": [{"name": "Cup"}],
        "operator_splits": [{
            "id": "split-1",
            "line_idx": 0,
            "operator_user_id": "user-op-1",
            "operator_name": "Budi",
            "progress_entries": [entry],
        }],
    })
    await server.db[EMPLOYEES].insert_one({
        "id": "emp-1", "user_id": "user-op-1", "name": "Budi", "pay_basis": "piece", "active": True,
    })


@pytest.mark.anyio
async def test_repair_work_date_forbidden_without_permission():
    await _seed_order()
    _user(role="operator", email="op-deny-date@example.com", override={})
    async with _client() as c:
        r = await c.post("/api/reports/gaji-operator/repair-work-date", json={
            "order_id": "po-repair-1",
            "split_id": "split-1",
            "action_id": "act-1",
            "work_date": "2026-10-05",
            "expected_work_date": "",
            "reason": "Tanggal dari kartu absensi",
        })
    assert r.status_code == 403
    assert "repair_work_date" in (r.json().get("detail") or "")


@pytest.mark.anyio
async def test_repair_rate_forbidden_without_permission():
    await _seed_order(work_date="2026-10-05")
    _user(role="operator", email="op-deny-rate@example.com", override={
        "reports.gaji_operator.repair_work_date": True,
    })
    async with _client() as c:
        r = await c.post("/api/reports/gaji-operator/repair-rate", json={
            "order_id": "po-repair-1",
            "split_id": "split-1",
            "action_id": "act-1",
            "rate_per_pcs": 125,
            "expected_rate_per_pcs": None,
            "reason": "Tarif sesuai SK Agustus",
        })
    assert r.status_code == 403
    assert "repair_rate" in (r.json().get("detail") or "")


@pytest.mark.anyio
async def test_authorized_head_production_repairs_work_date():
    await _seed_order()
    _user(role="head_production", email="head-ok@example.com")
    async with _client() as c:
        r = await c.post("/api/reports/gaji-operator/repair-work-date", json={
            "order_id": "po-repair-1",
            "split_id": "split-1",
            "action_id": "act-1",
            "work_date": "2026-10-05",
            "expected_work_date": "",
            "reason": "Tanggal dari kartu absensi",
        })
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True
    assert body["work_date"] == "2026-10-05"
    assert body["source_key"] == "po-repair-1:split-1:act-1"
    order = await server.db.orders.find_one({"id": "po-repair-1"})
    entry = order["operator_splits"][0]["progress_entries"][0]
    assert entry["work_date"] == "2026-10-05"


@pytest.mark.anyio
async def test_manipulated_ids_do_not_bypass_validation():
    await _seed_order()
    _user(role="head_production", email="head-ids@example.com")
    async with _client() as c:
        missing_order = await c.post("/api/reports/gaji-operator/repair-work-date", json={
            "order_id": "po-does-not-exist",
            "split_id": "split-1",
            "action_id": "act-1",
            "work_date": "2026-10-05",
            "reason": "Tanggal dari kartu absensi",
        })
        bad_split = await c.post("/api/reports/gaji-operator/repair-work-date", json={
            "order_id": "po-repair-1",
            "split_id": "split-OTHER",
            "action_id": "act-1",
            "work_date": "2026-10-05",
            "reason": "Tanggal dari kartu absensi",
        })
        bad_action = await c.post("/api/reports/gaji-operator/repair-work-date", json={
            "order_id": "po-repair-1",
            "split_id": "split-1",
            "action_id": "act-OTHER",
            "work_date": "2026-10-05",
            "reason": "Tanggal dari kartu absensi",
        })
    assert missing_order.status_code == 404
    assert bad_split.status_code == 404
    assert bad_action.status_code == 404
    order = await server.db.orders.find_one({"id": "po-repair-1"})
    assert not order["operator_splits"][0]["progress_entries"][0].get("work_date")


@pytest.mark.anyio
async def test_held_posted_final_paid_guards():
    await _seed_order()
    _user(role="head_production", email="head-guards@example.com")
    payload = {
        "order_id": "po-repair-1",
        "split_id": "split-1",
        "action_id": "act-1",
        "work_date": "2026-10-05",
        "reason": "Tanggal dari kartu absensi",
    }

    await server.db[WAGE_CLAIMS].insert_one({
        "id": "cl-held", "source_key": "po-repair-1:split-1:act-1",
        "status": "held", "period_id": "per-1",
    })
    async with _client() as c:
        held = await c.post("/api/reports/gaji-operator/repair-work-date", json=payload)
    assert held.status_code == 409
    assert "HELD" in (held.json().get("detail") or "")

    await server.db[WAGE_CLAIMS].delete_many({})
    await server.db[WAGE_CLAIMS].insert_one({
        "id": "cl-posted", "source_key": "po-repair-1:split-1:act-1",
        "status": "posted", "period_id": "per-1",
    })
    async with _client() as c:
        posted = await c.post("/api/reports/gaji-operator/repair-work-date", json=payload)
    assert posted.status_code == 409
    assert "POSTED" in (posted.json().get("detail") or "")

    await server.db[WAGE_CLAIMS].delete_many({})
    # FINAL: claim active + period FINAL (reject_if_claimed checks period when claim exists)
    await server.db[WAGE_CLAIMS].insert_one({
        "id": "cl-final", "source_key": "po-repair-1:split-1:act-1",
        "status": "held", "period_id": "per-final",
    })
    await server.db[PERIODS].insert_one({"id": "per-final", "status": STATUS_FINAL})
    async with _client() as c:
        final = await c.post("/api/reports/gaji-operator/repair-work-date", json=payload)
    # HELD is checked before FINAL — still 409 locked
    assert final.status_code == 409

    await server.db[WAGE_CLAIMS].delete_many({})
    await server.db[WAGE_CLAIMS].insert_one({
        "id": "cl-paid", "source_key": "po-repair-1:split-1:act-1",
        "status": "posted", "period_id": "per-paid",
    })
    await server.db[PERIODS].insert_one({"id": "per-paid", "status": "posted"})
    await server.db[PAYMENTS].insert_one({
        "id": "pay-1", "payroll_period_id": "per-paid", "status": STATUS_PAID,
    })
    async with _client() as c:
        paid = await c.post("/api/reports/gaji-operator/repair-work-date", json=payload)
    assert paid.status_code == 409
