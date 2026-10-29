"""Production dashboard branch scope. Local fixtures, APP_ENV=production.

PO origin (orders.branch_id) is the authorization key, matching PO Late Tracking.
KPI builders and the late-tracking calculation are not under test here.
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("MONGO_URL", "mongodb://127.0.0.1:27017")
_worker = os.environ.get("PYTEST_XDIST_WORKER") or "main"
os.environ["DB_NAME"] = f"eternal_packaging_production_dashboard_test_{_worker}"
os.environ.setdefault("JWT_SECRET", "test-secret-key-12345678901234567890")
os.environ.setdefault("CORS_ORIGINS", "*")

import server
from server import app, current_user
from services.po_late_tracking import branch_mongo_scope, mongo_match
from services.production_dashboard_http import order_population_query

pytestmark = pytest.mark.anyio
WIB = ZoneInfo("Asia/Jakarta")

YGY = "br-scope-ygy"
SMG = "br-scope-smg"
SBY = "br-scope-sby"
QTY_YGY_OWN = 10
QTY_YGY_OTHER = 25
QTY_YGY_BUFFER = 3
QTY_SMG = 40
QTY_SBY = 7
YGY_OUTPUT = QTY_YGY_OWN + QTY_YGY_OTHER + QTY_YGY_BUFFER
GLOBAL_OUTPUT = YGY_OUTPUT + QTY_SMG + QTY_SBY
SALES_A_OUTPUT = QTY_YGY_OWN + QTY_YGY_BUFFER


def _confirmed_at() -> str:
    moment = datetime.now(WIB) - timedelta(days=1)
    return moment.replace(hour=10, minute=0, second=0, microsecond=0).isoformat()


def _order(oid, branch_id, qty, *, sales_user_id, production_branch_id, **extra):
    return {
        "id": oid,
        "po_number": oid.upper(),
        "cancelled": False,
        "branch_id": branch_id,
        "sales_user_id": sales_user_id,
        "current_stage": 8,
        "line_items": [{
            "name": "Cup",
            "quantity": qty,
            "unit": "pcs",
            "production_process": "print",
            "line_id": f"{oid}-0",
        }],
        "operator_splits": [{
            "operator_user_id": "op-1",
            "operator_email": "op@x",
            "operator_name": "Operator Satu",
            "line_idx": 0,
            "quantity": qty,
            "qty_completed": qty,
            "progress_entries": [{
                "qty": qty,
                "confirmed_at": _confirmed_at(),
                "action_id": f"{oid}-act",
                "legacy": False,
                "production_branch_id": production_branch_id,
            }],
        }],
        **extra,
    }


def _user(user_id, role, branch_ids=None, **perms):
    user = {
        "id": user_id,
        "user_id": user_id,
        "email": f"{user_id}@example.com",
        "role": role,
        "status": "active",
    }
    if branch_ids is not None:
        user["branch_ids"] = list(branch_ids)
    if perms:
        user["permissions_override"] = perms
    return user


VIEW = {"dashboard.production.view": True}
OPT = {
    "dashboard.production.view": True,
    "dashboard.production.operator_analytics": True,
    "dashboard.production.branch_output": True,
}


def _trend_pcs(body) -> int:
    series = ((body.get("trend") or {}).get("series") or {}).get("pcs") or []
    return sum(point.get("qty") or 0 for point in series)


def _operator_pcs(body) -> int:
    rows = ((body.get("performance") or {}).get("ranking") or {}).get("pcs") or []
    return sum(row.get("qty") or 0 for row in rows)


def _named_qty(rows) -> dict:
    return {row["name"]: row["qty"] for row in rows or []}


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture(autouse=True)
def production_env(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")


@pytest.fixture
def bind_motor():
    from motor.motor_asyncio import AsyncIOMotorClient

    server.client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    server.db = server.client[os.environ["DB_NAME"]]


@pytest.fixture
async def scoped_db(bind_motor):
    name = f"eternal_packaging_production_scope_test_{_worker}"
    previous = server.db
    db = server.client[name]
    server.db = db
    await db.orders.delete_many({})
    await db.branches.delete_many({})
    await db.branches.insert_many([
        {"id": YGY, "code": "YGY", "name": "Yogyakarta", "active": True},
        {"id": SMG, "code": "SMG", "name": "Semarang", "active": True},
        {"id": SBY, "code": "SBY", "name": "Surabaya", "active": True},
    ])
    await db.orders.insert_many([
        _order("scope-ygy", YGY, QTY_YGY_OWN, sales_user_id="sales-a", production_branch_id=SMG),
        _order("scope-ygy-other", YGY, QTY_YGY_OTHER, sales_user_id="sales-b", production_branch_id=YGY),
        _order(
            "scope-ygy-buffer", YGY, QTY_YGY_BUFFER,
            sales_user_id="sales-a", production_branch_id=YGY, buffer_stock=True,
        ),
        _order("scope-smg", SMG, QTY_SMG, sales_user_id="sales-b", production_branch_id=YGY),
        _order("scope-sby", SBY, QTY_SBY, sales_user_id="sales-a", production_branch_id=SBY),
        _order(
            "scope-cancelled", YGY, 99,
            sales_user_id="sales-a", production_branch_id=YGY, cancelled=True,
        ),
    ])
    yield db
    await db.orders.delete_many({})
    await db.branches.delete_many({})
    server.db = previous
    app.dependency_overrides.pop(current_user, None)


def _bind(user):
    async def current():
        return user

    app.dependency_overrides[current_user] = current


async def _reads(client):
    snapshot = await client.get("/api/dashboard/produksi/snapshot")
    plant = await client.get("/api/dashboard/produksi/analytics", params={"days": 7})
    operator = await client.get("/api/dashboard/produksi/operator-analytics", params={"days": 7})
    branch = await client.get("/api/dashboard/produksi/branch-output", params={"days": 7})
    return snapshot, plant, operator, branch


def _assert_output(plant, operator, branch, qty, *, actual, origin):
    assert plant.status_code == 200
    assert operator.status_code == 200
    assert branch.status_code == 200
    assert _trend_pcs(plant.json()) == qty
    assert _operator_pcs(operator.json()) == qty
    body = branch.json()
    assert body["company_total"]["pcs"] == qty
    assert _named_qty(body["actual"]["pcs"]) == actual
    assert _named_qty(body["origin"]["pcs"]) == origin


@pytest.mark.asyncio
async def test_assigned_branch_excludes_other_origin_and_keeps_buffer(scoped_db):
    """Other-branch POs stay out of snapshot totals and confirmed output.

    A YGY-origin PO produced at Semarang still counts. A Semarang-origin PO
    produced at Yogyakarta does not. Buffer stock stays in the dashboard.
    """
    _bind(_user("ygy-user", "head_production", [YGY], **OPT))
    before = await scoped_db.orders.find({}, {"_id": 0}).sort("id", 1).to_list(20)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        snapshot, plant, operator, branch = await _reads(client)
    assert snapshot.status_code == 200
    assert snapshot.json()["po_aktif"]["count"] == 3
    _assert_output(
        plant, operator, branch, YGY_OUTPUT,
        actual={"Semarang": QTY_YGY_OWN, "Yogyakarta": QTY_YGY_OTHER + QTY_YGY_BUFFER},
        origin={"Yogyakarta": YGY_OUTPUT},
    )
    after = await scoped_db.orders.find({}, {"_id": 0}).sort("id", 1).to_list(20)
    assert after == before
    late = mongo_match(branch_scope=branch_mongo_scope({"scope": "ASSIGNED", "branch_ids": [YGY]}))
    assert late["buffer_stock"] == {"$ne": True}
    assert "buffer_stock" not in {"cancelled": {"$ne": True}, **branch_mongo_scope({"scope": "ASSIGNED", "branch_ids": [YGY]})}


@pytest.mark.asyncio
async def test_direktur_and_holding_stay_global_and_opt_in_stays_closed(scoped_db):
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        _bind(_user("director", "direktur_utama", [YGY]))
        snapshot, plant, operator, branch = await _reads(client)
        assert snapshot.status_code == 200
        assert snapshot.json()["po_aktif"]["count"] == 5
        assert plant.status_code == 200
        assert _trend_pcs(plant.json()) == GLOBAL_OUTPUT
        assert operator.status_code == 403
        assert branch.status_code == 403

        _bind(_user("director-opt", "direktur_utama", [YGY], **{
            "dashboard.production.operator_analytics": True,
            "dashboard.production.branch_output": True,
        }))
        _, _, operator_ok, branch_ok = await _reads(client)
        assert _operator_pcs(operator_ok.json()) == GLOBAL_OUTPUT
        assert branch_ok.json()["company_total"]["pcs"] == GLOBAL_OUTPUT
        assert set(_named_qty(branch_ok.json()["origin"]["pcs"])) == {"Yogyakarta", "Semarang", "Surabaya"}

        _bind(_user("holding", "holding_team", [YGY], **VIEW))
        holding_snapshot, holding_plant, holding_op, holding_branch = await _reads(client)
        assert holding_snapshot.json()["po_aktif"]["count"] == 5
        assert _trend_pcs(holding_plant.json()) == GLOBAL_OUTPUT
        assert holding_op.status_code == 403
        assert holding_branch.status_code == 403


@pytest.mark.asyncio
async def test_multi_branch_union_and_ygy_fallback_match_po_late(scoped_db):
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        _bind(_user("multi", "head_operations", [YGY, SMG], **OPT))
        snapshot, plant, _, branch = await _reads(client)
        assert snapshot.json()["po_aktif"]["count"] == 4
        assert _trend_pcs(plant.json()) == YGY_OUTPUT + QTY_SMG
        assert branch.json()["company_total"]["pcs"] == YGY_OUTPUT + QTY_SMG
        assert "Surabaya" not in _named_qty(branch.json()["origin"]["pcs"])

        _bind(_user("fallback", "helper", None, **OPT))
        fallback_snapshot, fallback_plant, _, fallback_branch = await _reads(client)
        assert fallback_snapshot.json()["po_aktif"]["count"] == 3
        assert _trend_pcs(fallback_plant.json()) == YGY_OUTPUT
        assert fallback_branch.json()["origin"]["pcs"] == [{"branch_id": YGY, "name": "Yogyakarta", "qty": YGY_OUTPUT}]

        await scoped_db.branches.delete_many({"code": "YGY"})
        _bind(_user("no-ygy", "helper", None, **OPT))
        empty_snapshot, empty_plant, _, empty_branch = await _reads(client)
        assert empty_snapshot.json()["po_aktif"]["count"] == 0
        assert _trend_pcs(empty_plant.json()) == 0
        assert empty_branch.json()["company_total"] == {}


@pytest.mark.asyncio
async def test_production_admin_is_not_global_and_sales_stays_opt_in(scoped_db):
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        _bind(_user("admin-assigned", "admin", [SBY], **OPT))
        snapshot, plant, _, branch = await _reads(client)
        assert snapshot.json()["po_aktif"]["count"] == 1
        assert _trend_pcs(plant.json()) == QTY_SBY
        assert branch.json()["company_total"]["pcs"] == QTY_SBY

        _bind(_user("admin-open", "admin", None, **OPT))
        open_snapshot, open_plant, _, _ = await _reads(client)
        assert open_snapshot.json()["po_aktif"]["count"] == 3
        assert _trend_pcs(open_plant.json()) == YGY_OUTPUT

        _bind(_user("sales-default", "sales_eternal"))
        denied, denied_plant, denied_op, denied_branch = await _reads(client)
        assert denied.status_code == 403
        assert denied_plant.status_code == 403
        assert denied_op.status_code == 403
        assert denied_branch.status_code == 403

        _bind(_user("sales-a", "sales_eternal", [YGY, SMG], **OPT))
        granted, granted_plant, granted_op, granted_branch = await _reads(client)
        assert granted.status_code == 403
        assert granted_plant.status_code == 403
        assert granted_op.status_code == 403
        assert granted_branch.status_code == 403

    sales = _user("sales-a", "sales_eternal", [YGY, SMG])
    query = await order_population_query(scoped_db, sales)
    assert query["cancelled"] == {"$ne": True}
    assert query["branch_id"] == {"$in": [YGY, SMG]}
    assert query["sales_user_id"] == "sales-a"
    assert "buffer_stock" not in query
    head = await order_population_query(scoped_db, _user("ygy-user", "head_production", [YGY]))
    assert "sales_user_id" not in head
    assert head["branch_id"] == {"$in": [YGY]}


@pytest.mark.asyncio
async def test_view_permission_does_not_open_operator_or_branch_output(scoped_db):
    _bind(_user("view-only", "head_production", [YGY], **VIEW))
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        snapshot, plant, operator, branch = await _reads(client)
    assert snapshot.status_code == 200
    assert plant.status_code == 200
    assert operator.status_code == 403
    assert branch.status_code == 403
