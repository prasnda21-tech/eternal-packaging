"""Operational dashboard branch scope. Local fixtures, APP_ENV=production.

Origin branch (orders.branch_id) is the authorization key, same resolver as
PO Late Tracking. Status, qty, dates, and SJ/PO formulas are not rewritten.
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone
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

import jwt

import server
from server import app, current_user

pytestmark = pytest.mark.anyio
WIB = ZoneInfo("Asia/Jakarta")

YGY = "br-ops-ygy"
SMG = "br-ops-smg"
SBY = "br-ops-sby"

YGY_ACTIVE = {"ops-ygy-a", "ops-ygy-b", "ops-ygy-buf"}
ALL_ACTIVE = YGY_ACTIVE | {"ops-smg", "ops-sby"}
YGY_SMG = YGY_ACTIVE | {"ops-smg"}
YGY_SJ = {"sj-a1", "sj-a2", "sj-b1"}


def _today() -> str:
    return datetime.now(WIB).date().isoformat()


def _shipment(sid: str, day: str) -> dict:
    return {"id": sid, "number": sid.upper(), "delivery_date": day}


def _order(oid, branch_id, qty, *, sales_user_id, shipments=None, **extra):
    return {
        "id": oid,
        "po_number": oid.upper(),
        "customer": "Cust",
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
        **({"shipments": shipments} if shipments else {}),
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


OPS = {
    "dashboard.production.view": True,
    "schedule.view": True,
    "po.view": True,
}


def _bind(user):
    async def current():
        return user

    app.dependency_overrides[current_user] = current


def _ids(rows) -> set:
    return {str(row.get("id")) for row in rows or [] if row.get("id")}


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
    name = f"eternal_packaging_operational_scope_test_{_worker}"
    previous = server.db
    db = server.client[name]
    server.db = db
    await db.orders.delete_many({})
    await db.branches.delete_many({})
    await db.users.delete_many({})
    await db.branches.insert_many([
        {"id": YGY, "code": "YGY", "name": "Yogyakarta", "active": True},
        {"id": SMG, "code": "SMG", "name": "Semarang", "active": True},
        {"id": SBY, "code": "SBY", "name": "Surabaya", "active": True},
    ])
    day = _today()
    await db.orders.insert_many([
        _order(
            "ops-ygy-a", YGY, 10, sales_user_id="sales-a", work_date=day,
            shipments=[_shipment("sj-a1", day), _shipment("sj-a2", day)],
        ),
        _order(
            "ops-ygy-b", YGY, 8, sales_user_id="sales-b",
            shipments=[_shipment("sj-b1", day)],
        ),
        _order("ops-ygy-buf", YGY, 3, sales_user_id="sales-a", buffer_stock=True),
        _order(
            "ops-smg", SMG, 40, sales_user_id="sales-b", work_date=day,
            shipments=[_shipment("sj-smg", day)],
        ),
        _order(
            "ops-sby", SBY, 7, sales_user_id="sales-a",
            shipments=[_shipment("sj-sby", day)],
        ),
        _order(
            "ops-ygy-can", YGY, 99, sales_user_id="sales-a", cancelled=True,
            shipments=[_shipment("sj-ygy-can", day)],
        ),
        _order(
            "ops-smg-can", SMG, 98, sales_user_id="sales-b", cancelled=True,
            shipments=[_shipment("sj-smg-can", day)],
        ),
    ])
    yield db
    await db.orders.delete_many({})
    await db.branches.delete_many({})
    await db.users.delete_many({})
    server.db = previous
    app.dependency_overrides.pop(current_user, None)


async def _fingerprint(db):
    docs = await db.orders.find({}, {"_id": 0}).sort("id", 1).to_list(20)
    return docs


@pytest.mark.asyncio
async def test_single_branch_kpi_matches_rincian_and_excludes_other_branches(scoped_db):
    before = await _fingerprint(scoped_db)
    _bind(_user("ygy-user", "scope_probe", [YGY], **OPS))
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        snapshot = await client.get("/api/dashboard/produksi/snapshot")
        rincian = await client.get(
            "/api/dashboard/rincian",
            params={"widget": "po_aktif", "dashboard": "produksi", "page": 1, "page_size": 1},
        )
        page2 = await client.get(
            "/api/dashboard/rincian",
            params={"widget": "po_aktif", "dashboard": "produksi", "page": 2, "page_size": 1},
        )
        orders = await client.get("/api/orders", params={"view": "dashboard", "status": "aktif"})
        cancelled = await client.get("/api/orders", params={"view": "dashboard", "status": "cancelled"})
        today = await client.get("/api/dashboard/today")
        schedule = await client.get("/api/delivery/schedule")
        production = await client.get("/api/dashboard/production", params={"include_cancelled": "true"})
        summary = await client.get("/api/dashboard/summary")
    assert snapshot.status_code == 200
    assert rincian.status_code == 200
    body = rincian.json()
    assert snapshot.json()["po_aktif"]["count"] == 3
    assert body["total"] == 3
    assert summary.status_code == 200
    assert summary.json()["kpi"]["total_active"] == 3
    assert len(body["rows"]) == 1
    assert body["rows"][0]["id"] in YGY_ACTIVE
    assert page2.status_code == 200
    assert page2.json()["total"] == 3
    assert page2.json()["rows"][0]["id"] in YGY_ACTIVE
    assert page2.json()["rows"][0]["id"] != body["rows"][0]["id"]
    assert _ids(orders.json()["orders"]) == YGY_ACTIVE
    assert _ids(cancelled.json()["orders"]) == {"ops-ygy-can"}
    assert today.status_code == 200
    assert today.json()["work_today"]["total"] == 1
    assert _ids(today.json()["work_today"]["preview"]) == {"ops-ygy-a"}
    ship_ids = {row.get("shipment_id") for row in today.json()["shipments_today"]["preview"]}
    assert today.json()["shipments_today"]["total"] == 3
    assert ship_ids == YGY_SJ
    sched = schedule.json()["rows"]
    assert schedule.status_code == 200
    assert {row.get("shipment_id") for row in sched} == YGY_SJ
    assert _ids(sched) == {"ops-ygy-a", "ops-ygy-b"}
    assert sum(1 for row in sched if row.get("id") == "ops-ygy-a") == 2
    prod_ids = _ids(production.json()["rows"])
    assert "ops-ygy-can" in prod_ids
    assert "ops-smg" not in prod_ids
    assert "ops-smg-can" not in prod_ids
    assert "ops-sby" not in prod_ids
    assert prod_ids <= YGY_ACTIVE | {"ops-ygy-can"}
    assert production.json()["completeness"] == "complete"
    assert production.json()["total"] == len(production.json()["rows"])
    assert today.json()["work_today"]["completeness"] == "complete"
    assert orders.json()["list_read"]["completeness"] == "complete"
    assert orders.json()["stage_read"]["completeness"] == "complete"
    assert await _fingerprint(scoped_db) == before


@pytest.mark.asyncio
async def test_two_branches_union_and_direktur_holding_stay_global(scoped_db):
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        _bind(_user("multi", "scope_probe", [YGY, SMG], **OPS))
        snap = await client.get("/api/dashboard/produksi/snapshot")
        orders = await client.get("/api/orders", params={"view": "dashboard", "status": "aktif"})
        assert snap.json()["po_aktif"]["count"] == 4
        assert _ids(orders.json()["orders"]) == YGY_SMG

        for role in ("direktur_utama", "holding_team"):
            _bind(_user(role, role, [YGY], **OPS))
            snap = await client.get("/api/dashboard/produksi/snapshot")
            summary = await client.get("/api/dashboard/summary")
            schedule = await client.get("/api/delivery/schedule")
            operator = await client.get("/api/dashboard/produksi/operator-analytics")
            branch = await client.get("/api/dashboard/produksi/branch-output")
            assert snap.status_code == 200
            assert snap.json()["po_aktif"]["count"] == 5
            assert summary.json()["kpi"]["total_active"] == 5
            assert {row.get("shipment_id") for row in schedule.json()["rows"]} == YGY_SJ | {"sj-smg", "sj-sby"}
            assert operator.status_code == 403
            assert branch.status_code == 403


@pytest.mark.asyncio
async def test_ygy_fallback_and_empty_when_ygy_missing(scoped_db):
    _bind(_user("fallback", "scope_probe", **OPS))
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        snap = await client.get("/api/dashboard/produksi/snapshot")
        assert snap.json()["po_aktif"]["count"] == 3
        await scoped_db.branches.delete_many({"code": "YGY"})
        snap = await client.get("/api/dashboard/produksi/snapshot")
        orders = await client.get("/api/orders", params={"view": "dashboard", "status": "aktif"})
        assert snap.json()["po_aktif"]["count"] == 0
        assert orders.json()["orders"] == []


@pytest.mark.asyncio
async def test_admin_branch_is_not_a_global_bypass(scoped_db):
    """Admin keeps the existing schedule permission bypass and stays branch-scoped.

    require_perm does not treat admin as a pass. include_cancelled does not
    add another branch.
    """
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        _bind(_user(
            "admin-denied",
            "admin",
            [SBY],
            **{
                "schedule.view": False,
                "delivery.schedule.view": False,
                "nav.delivery_schedule": False,
            },
        ))
        schedule = await client.get("/api/delivery/schedule")
        production = await client.get("/api/dashboard/production", params={"include_cancelled": "true"})
        assert schedule.status_code == 200, schedule.text
        assert {row.get("shipment_id") for row in schedule.json()["rows"]} == {"sj-sby"}
        assert production.status_code == 403

        _bind(_user("admin-sby", "admin", [SBY]))
        snap = await client.get("/api/dashboard/produksi/snapshot")
        production = await client.get("/api/dashboard/production", params={"include_cancelled": "true"})
        summary = await client.get("/api/dashboard/summary")
    assert snap.status_code == 200, snap.text
    assert snap.json()["po_aktif"]["count"] == 1
    assert production.status_code == 200, production.text
    assert _ids(production.json()["rows"]) == {"ops-sby"}
    assert summary.json()["kpi"]["total_active"] == 1


@pytest.mark.asyncio
async def test_kpi_only_stays_closed_and_missing_permission_is_403(scoped_db):
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        _bind(_user("kpi-only", "scope_probe", [YGY], **{"dashboard.production.view": True}))
        snapshot = await client.get("/api/dashboard/produksi/snapshot")
        rincian = await client.get(
            "/api/dashboard/rincian",
            params={"widget": "po_aktif", "dashboard": "produksi"},
        )
        operator = await client.get("/api/dashboard/produksi/operator-analytics")
        branch = await client.get("/api/dashboard/produksi/branch-output")
        today = await client.get("/api/dashboard/today")
        production = await client.get("/api/dashboard/production")
        schedule = await client.get("/api/delivery/schedule")
        summary = await client.get("/api/dashboard/summary")
        assert snapshot.status_code == 200
        assert summary.status_code == 200
        assert rincian.status_code == 403
        assert operator.status_code == 403
        assert branch.status_code == 403
        assert today.status_code == 403
        assert production.status_code == 403
        assert schedule.status_code == 403

        _bind(_user("none", "scope_probe", [YGY]))
        for path in (
            "/api/dashboard/produksi/snapshot",
            "/api/dashboard/today",
            "/api/dashboard/production",
            "/api/delivery/schedule",
            "/api/dashboard/summary",
        ):
            response = await client.get(path)
            assert response.status_code == 403, path


@pytest.mark.asyncio
async def test_sales_eternal_surface_stays_locked(scoped_db):
    sales = _user(
        "sales-a",
        "sales_eternal",
        [YGY],
        **OPS,
        **{"dashboard.production.operator_analytics": True},
    )
    await scoped_db.users.insert_one({**sales, "password_hash": "x"})
    token = jwt.encode(
        {"email": sales["email"], "sub": sales["id"]},
        os.environ["JWT_SECRET"],
        algorithm="HS256",
    )
    app.dependency_overrides.pop(current_user, None)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        headers = {"Authorization": f"Bearer {token}"}
        for path, params in (
            ("/api/orders", {"view": "dashboard"}),
            ("/api/orders", {"view": "summary"}),
            ("/api/orders", None),
            ("/api/dashboard/shipment-status", {"include_cancelled": "true"}),
            ("/api/dashboard/today", None),
            ("/api/dashboard/production", {"include_cancelled": "true"}),
            ("/api/delivery/schedule", None),
            ("/api/dashboard/summary", None),
            ("/api/dashboard/produksi/snapshot", None),
            ("/api/dashboard/rincian", {"widget": "po_aktif", "dashboard": "produksi"}),
        ):
            response = await client.get(path, params=params, headers=headers)
            assert response.status_code == 403, path
            assert "ops-smg" not in response.text
            assert "ops-sby" not in response.text


def _assert_order_scope(body, ids, *, aktif, cancelled):
    assert _ids(body["orders"]) == ids
    assert body["counts"]["aktif"] == aktif
    assert body["counts"]["cancelled"] == cancelled
    assert "ops-smg" not in {row.get("id") for row in body["orders"]}


@pytest.mark.asyncio
async def test_summary_default_and_shipment_status_follow_branch_scope(scoped_db):
    before = await _fingerprint(scoped_db)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        _bind(_user("ygy-user", "scope_probe", [YGY], **OPS))
        summary = await client.get("/api/orders", params={"view": "summary", "status": "aktif"})
        default = await client.get("/api/orders", params={"status": "aktif"})
        cancelled = await client.get("/api/orders", params={"status": "cancelled"})
        everything = await client.get("/api/orders", params={"status": "all"})
        ship = await client.get("/api/dashboard/shipment-status")
        ship_all = await client.get(
            "/api/dashboard/shipment-status", params={"include_cancelled": "true"},
        )
        assert summary.status_code == 200, summary.text
        assert default.status_code == 200, default.text
        _assert_order_scope(summary.json(), YGY_ACTIVE, aktif=3, cancelled=1)
        _assert_order_scope(default.json(), YGY_ACTIVE, aktif=3, cancelled=1)
        assert isinstance(default.json()["stage_counts"], dict)
        assert _ids(cancelled.json()["orders"]) == {"ops-ygy-can"}
        assert _ids(everything.json()["orders"]) == YGY_ACTIVE
        assert ship.status_code == 200, ship.text
        assert _ids(ship.json()["rows"]) == YGY_ACTIVE
        assert _ids(ship_all.json()["rows"]) == YGY_ACTIVE | {"ops-ygy-can"}

        _bind(_user("multi", "scope_probe", [YGY, SMG], **OPS))
        summary = await client.get("/api/orders", params={"view": "summary", "status": "aktif"})
        assert _ids(summary.json()["orders"]) == YGY_SMG
        assert summary.json()["counts"]["aktif"] == 4
        assert summary.json()["counts"]["cancelled"] == 2

        for role in ("direktur_utama", "holding_team"):
            _bind(_user(role, role, [YGY], **OPS))
            summary = await client.get("/api/orders", params={"view": "summary", "status": "aktif"})
            default = await client.get("/api/orders", params={"status": "aktif"})
            ship = await client.get("/api/dashboard/shipment-status")
            assert summary.json()["counts"]["aktif"] == 5
            assert _ids(summary.json()["orders"]) == ALL_ACTIVE
            assert _ids(default.json()["orders"]) == ALL_ACTIVE
            assert _ids(ship.json()["rows"]) == ALL_ACTIVE

        _bind(_user("admin-sby", "admin", [SBY]))
        summary = await client.get("/api/orders", params={"view": "summary", "status": "aktif"})
        default = await client.get("/api/orders", params={"status": "cancelled"})
        ship = await client.get(
            "/api/dashboard/shipment-status", params={"include_cancelled": "true"},
        )
        assert _ids(summary.json()["orders"]) == {"ops-sby"}
        assert summary.json()["counts"]["aktif"] == 1
        assert summary.json()["counts"]["cancelled"] == 0
        assert default.json()["orders"] == []
        assert _ids(ship.json()["rows"]) == {"ops-sby"}

        _bind(_user("fallback", "scope_probe", **OPS))
        summary = await client.get("/api/orders", params={"view": "summary", "status": "aktif"})
        assert summary.json()["counts"]["aktif"] == 3
        await scoped_db.branches.delete_many({"code": "YGY"})
        summary = await client.get("/api/orders", params={"view": "summary", "status": "aktif"})
        default = await client.get("/api/orders", params={"status": "aktif"})
        ship = await client.get("/api/dashboard/shipment-status")
        assert summary.json()["orders"] == []
        assert summary.json()["counts"]["aktif"] == 0
        assert default.json()["orders"] == []
        assert ship.json()["rows"] == []
    restored = await _fingerprint(scoped_db)
    assert {row["id"]: row for row in restored} == {row["id"]: row for row in before}


@pytest.mark.asyncio
async def test_kepala_rincian_pages_the_full_population_and_basis_is_closed(scoped_db):
    extras = []
    for index in range(1001):
        extras.append({
            "id": f"cap-{index}",
            "po_number": f"CAP-{index}",
            "customer": "Cust",
            "cancelled": False,
            "branch_id": YGY,
            "current_stage": 8,
            "design_status": "Approved",
            "payment_status": "Approved",
            "line_items": [{
                "name": "Cup",
                "quantity": 1,
                "unit": "pcs",
                "production_process": "print",
                "line_id": f"cap-{index}-0",
            }],
        })
    extras.append({
        "id": "cap-smg",
        "po_number": "CAP-SMG",
        "customer": "Cust",
        "cancelled": False,
        "branch_id": SMG,
        "current_stage": 8,
        "design_status": "Approved",
        "payment_status": "Approved",
        "line_items": [{
            "name": "Cup",
            "quantity": 1,
            "unit": "pcs",
            "production_process": "print",
            "line_id": "cap-smg-0",
        }],
    })
    await scoped_db.orders.insert_many(extras)
    _bind(_user(
        "ygy-kepala",
        "scope_probe",
        [YGY],
        **OPS,
        **{"schedule.edit": True},
    ))
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        snapshot = await client.get("/api/dashboard/produksi/snapshot")
        rincian = await client.get(
            "/api/dashboard/rincian",
            params={
                "widget": "perlu_produksi",
                "dashboard": "produksi",
                "page": 1,
                "page_size": 1,
            },
        )
        po = await client.get(
            "/api/dashboard/rincian",
            params={"widget": "po_aktif", "dashboard": "produksi", "basis": "snapshot", "page_size": 1},
        )
        empty_basis = await client.get(
            "/api/dashboard/rincian",
            params={"widget": "po_aktif", "dashboard": "produksi"},
        )
        invalid = await client.get(
            "/api/dashboard/rincian",
            params={"widget": "po_aktif", "dashboard": "produksi", "basis": "legacy"},
        )
        schedule = await client.get("/api/dashboard/production", params={"include_cancelled": "true"})
        shipment = await client.get("/api/dashboard/shipment-status", params={"include_cancelled": "true"})
        listed = await client.get("/api/orders", params={"view": "dashboard", "status": "all"})
    assert snapshot.status_code == 200, snapshot.text
    assert rincian.status_code == 200, rincian.text
    body = rincian.json()
    assert snapshot.json()["perlu_produksi"]["count"] == 1001
    assert body["total"] == 1001
    assert body["kpi_summary"]["count"] == 1001
    assert body["kpi_summary"]["line_count"] == 1001
    assert len(body["rows"]) == 1
    assert body["rows"][0]["id"] != "cap-smg"
    assert po.status_code == 200, po.text
    assert po.json()["total"] == 1001 + 3
    assert empty_basis.status_code == 200
    assert invalid.status_code == 400
    assert schedule.status_code == 200, schedule.text
    sched = schedule.json()
    assert sched["completeness"] == "incomplete"
    assert sched["read_limit"] == 1000
    assert sched["matched_count"] > sched["read_limit"]
    assert sched["total"] == len(sched["rows"]) <= 1000
    assert "cap-smg" not in _ids(sched["rows"])
    assert shipment.status_code == 200, shipment.text
    ship_body = shipment.json()
    assert ship_body["completeness"] == "incomplete"
    assert ship_body["read_limit"] == 1000
    assert ship_body["matched_count"] > ship_body["read_limit"]
    assert ship_body["total"] == len(ship_body["rows"]) <= 1000
    assert "cap-smg" not in _ids(ship_body["rows"])
    assert listed.status_code == 200, listed.text
    listed_body = listed.json()
    assert listed_body["list_read"]["completeness"] == "incomplete"
    assert listed_body["list_read"]["read_limit"] == 500
    assert listed_body["list_read"]["matched_count"] > 500
    assert listed_body["stage_read"]["completeness"] == "incomplete"
    assert listed_body["stage_read"]["read_limit"] == 1000
    assert listed_body["stage_read"]["matched_count"] > 1000
    assert "cap-smg" not in _ids(listed_body["orders"])


def _past_deadline() -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=48)).isoformat()


@pytest.mark.asyncio
async def test_reminders_actions_tasks_and_delivery_follow_branch_scope(scoped_db):
    day = _today()
    past = _past_deadline()
    reschedule = [{
        "reschedule_id": "rs-1",
        "created_at": "2026-10-01T00:00:00+00:00",
        "previous_date": "2026-09-01",
        "new_date": day,
        "status": "scheduled",
    }]
    await scoped_db.orders.insert_many([
        _order("rem-ygy", YGY, 4, sales_user_id="sales-a", deadline=past, current_stage=1),
        _order("rem-smg", SMG, 4, sales_user_id="sales-b", deadline=past, current_stage=1),
        _order("rem-sby", SBY, 4, sales_user_id="sales-a", deadline=past, current_stage=1),
        _order(
            "plan-ygy", YGY, 8, sales_user_id="sales-a",
            scheduled_delivery_date=day,
            shipments=[_shipment("sj-plan-1", day), _shipment("sj-plan-2", day)],
            line_items=[
                {"name": "Cup", "quantity": 3, "unit": "pcs", "production_process": "print", "line_id": "plan-ygy-0"},
                {"name": "Lid", "quantity": 5, "unit": "pcs", "production_process": "print", "line_id": "plan-ygy-1"},
            ],
            delivery_reschedules=[{**reschedule[0], "reschedule_id": "rs-ygy"}],
        ),
        _order(
            "plan-ygy-kg", YGY, 9, sales_user_id="sales-a",
            scheduled_delivery_date=day,
            line_items=[{
                "name": "Roll", "quantity": 9, "unit": "kg",
                "production_process": "print", "line_id": "plan-ygy-kg-0",
            }],
        ),
        _order(
            "plan-ygy-buf", YGY, 2, sales_user_id="sales-a",
            buffer_stock=True, scheduled_delivery_date=day,
        ),
        _order(
            "plan-smg", SMG, 11, sales_user_id="sales-b",
            scheduled_delivery_date=day,
            delivery_reschedules=[{**reschedule[0], "reschedule_id": "rs-smg"}],
        ),
        _order(
            "op-ygy", YGY, 2, sales_user_id="sales-a",
            operator_splits=[{"operator_email": "op-user@example.com", "quantity": 2, "qty_completed": 0}],
        ),
        _order(
            "op-smg", SMG, 2, sales_user_id="sales-b",
            operator_splits=[{"operator_email": "op-user@example.com", "quantity": 2, "qty_completed": 0}],
        ),
        _order(
            "op-ygy-other", YGY, 2, sales_user_id="sales-a",
            operator_splits=[{"operator_email": "other@example.com", "quantity": 2, "qty_completed": 0}],
        ),
    ])
    before = await _fingerprint(scoped_db)
    transport = httpx.ASGITransport(app=app)
    design = {"design.upload": True}
    schedule = {"schedule.view": True, "dashboard.production.view": True}

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        _bind(_user("hop-ygy", "head_production", [YGY], **design))
        reminders = await client.get("/api/reminders")
        actions = await client.get("/api/dashboard/action-items")
        tasks = await client.get("/api/work/division-tasks", params={"division": "designer", "scope": "all"})
        plan = await client.get("/api/delivery/plan", params={"q": "PLAN-SMG", "branch_id": SMG, "page_size": 50})
        plan_all = await client.get("/api/delivery/plan", params={"page_size": 50})
        qty = await client.get("/api/dashboard/widget/qty-rencana-pengiriman", params={"days": 7})
        detail = await client.get(
            "/api/dashboard/rincian",
            params={"widget": "delivery_qty_today", "dashboard": "produksi", "page_size": 50},
        )
        history = await client.get("/api/delivery/reschedules")

        assert reminders.status_code == 200, reminders.text
        rem_ids = {row["id"] for row in reminders.json()["reminders"]}
        assert "rem-ygy" in rem_ids
        assert "rem-smg" not in rem_ids
        assert "rem-sby" not in rem_ids
        assert reminders.json()["completeness"] == "complete"
        assert "ops-smg" not in reminders.text

        assert actions.status_code == 200, actions.text
        assert actions.json()["completeness"] == "complete"
        assert "ops-smg" not in actions.text
        assert "rem-smg" not in actions.text
        upload = next(row for row in actions.json()["actions"] if row["key"] == "design_upload")
        upload_ids = {item["id"] for item in upload["items"]}
        assert "rem-ygy" in upload_ids
        assert "plan-smg" not in upload_ids

        assert tasks.status_code == 200, tasks.text
        task_orders = {row["order_id"] for row in tasks.json()["tasks"]}
        assert "plan-ygy" in task_orders
        assert "plan-smg" not in task_orders
        assert tasks.json()["completeness"] == "complete"
        assert tasks.json()["matched_count"] == await scoped_db.orders.count_documents(
            {"cancelled": {"$ne": True}, "branch_id": YGY}
        )

        assert plan.status_code == 200, plan.text
        assert plan.json()["rows"] == []
        assert plan.json()["total"] == 0
        plan_ids = {row["id"] for row in plan_all.json()["rows"]}
        assert plan_ids == {"plan-ygy", "plan-ygy-kg", "plan-ygy-buf"}
        plan_ygy = next(row for row in plan_all.json()["rows"] if row["id"] == "plan-ygy")
        assert plan_ygy["qty_remaining"] == 8
        assert plan_all.json()["total"] == 3

        assert qty.status_code == 200, qty.text
        series = qty.json()["series"]
        today_sum = sum(row["today_qty"] for row in series)
        assert today_sum == detail.json()["kpi_summary"]["qty_remaining"] == 19
        assert len({row["unit"] for row in series}) == len(series) >= 2
        assert all(row["today_qty"] != 19 for row in series)
        detail_ids = {row["id"] for row in detail.json()["rows"]}
        assert detail_ids == plan_ids
        assert "plan-smg" not in detail.text

        assert history.status_code == 200, history.text
        hist_orders = {row["order_id"] for row in history.json()["rows"]}
        assert hist_orders == {"plan-ygy"}
        assert history.json()["completeness"] == "complete"
        assert "rs-smg" not in history.text

        _bind(_user("hop-smg", "head_operations", [SMG], **design, **schedule))
        hop_ops = await client.get("/api/reminders")
        hop_plan = await client.get("/api/delivery/plan", params={"page_size": 50})
        assert "rem-ygy" not in {row["id"] for row in hop_ops.json()["reminders"]}
        assert {row["id"] for row in hop_plan.json()["rows"]} == {"plan-smg"}

        _bind(_user("both", "head_production", [YGY, SMG], **design))
        union = await client.get("/api/reminders")
        union_ids = {row["id"] for row in union.json()["reminders"]}
        assert {"rem-ygy", "rem-smg"} <= union_ids
        assert "rem-sby" not in union_ids

        _bind(_user("director", "direktur_utama"))
        director = await client.get("/api/delivery/plan", params={"page_size": 50})
        director_ids = {row["id"] for row in director.json()["rows"]}
        assert {"plan-ygy", "plan-smg"} <= director_ids
        _bind(_user("holding", "holding_team", [YGY], **{"delivery.reschedule": True}))
        holding = await client.get("/api/delivery/reschedules")
        assert {row["order_id"] for row in holding.json()["rows"]} == {"plan-ygy", "plan-smg"}

        _bind(_user("adm", "admin", [SBY]))
        admin_rem = await client.get("/api/reminders")
        assert {row["id"] for row in admin_rem.json()["reminders"]} == {"rem-sby"}
        admin_plan = await client.get("/api/delivery/plan", params={"branch_id": YGY})
        assert admin_plan.status_code == 200
        assert "plan-ygy" not in admin_plan.text

        _bind(_user("fallback", "head_production", None, **design))
        fallback = await client.get("/api/reminders")
        fallback_ids = {row["id"] for row in fallback.json()["reminders"]}
        assert "rem-ygy" in fallback_ids
        assert "rem-smg" not in fallback_ids
        assert "rem-sby" not in fallback_ids
        _bind(_user("fallback-plan", "scope_probe", None, **schedule))
        fallback_plan = await client.get("/api/delivery/plan", params={"page_size": 50})
        assert {row["id"] for row in fallback_plan.json()["rows"]} == {"plan-ygy", "plan-ygy-kg", "plan-ygy-buf"}

        _bind(_user("nobody", "scope_probe", [YGY]))
        denied_plan = await client.get("/api/delivery/plan")
        denied_qty = await client.get("/api/dashboard/widget/qty-rencana-pengiriman")
        denied_hist = await client.get("/api/delivery/reschedules")
        denied_tasks = await client.get("/api/work/division-tasks", params={"division": "designer"})
        assert denied_plan.status_code == 403
        assert denied_qty.status_code == 403
        assert denied_hist.status_code == 403
        assert denied_tasks.status_code == 403

        _bind(_user("op-user", "scope_probe", [YGY], **{"operator.update": True}))
        op_actions = await client.get("/api/dashboard/action-items")
        op_tasks = await client.get("/api/work/division-tasks", params={"division": "operator", "scope": "all"})
        assert op_actions.status_code == 200, op_actions.text
        assert op_actions.json()["matched_count"] == 1
        assert "op-smg" not in op_actions.text
        assert "op-ygy-other" not in op_actions.text
        assert op_tasks.status_code == 200, op_tasks.text
        assert op_tasks.json()["matched_count"] == 1
        assert op_tasks.json()["scope"] == "mine"
        assert "op-smg" not in op_tasks.text

        sales = _user("sales-a", "sales_eternal", [YGY], **schedule, **design)
        await scoped_db.users.insert_one({**sales, "password_hash": "x"})
        token = jwt.encode(
            {"email": sales["email"], "sub": sales["id"]},
            os.environ["JWT_SECRET"],
            algorithm="HS256",
        )
        app.dependency_overrides.pop(current_user, None)
        headers = {"Authorization": f"Bearer {token}"}
        for path, params in (
            ("/api/reminders", None),
            ("/api/dashboard/action-items", None),
            ("/api/work/division-tasks", {"division": "designer", "scope": "all"}),
            ("/api/dashboard/widget/qty-rencana-pengiriman", {"days": 7}),
            ("/api/delivery/plan", {"q": "PLAN-SMG"}),
            ("/api/delivery/reschedules", None),
        ):
            response = await client.get(path, params=params, headers=headers)
            assert response.status_code == 403, path
            assert "plan-smg" not in response.text
            assert "rem-smg" not in response.text

    after = await _fingerprint(scoped_db)
    assert before == after

    bulk = [
        _order(f"cap-{i}", YGY, 1, sales_user_id="sales-a", deadline=past, current_stage=1)
        for i in range(1001)
    ]
    await scoped_db.orders.insert_many(bulk)
    _bind(_user("hop-ygy", "head_production", [YGY], **design))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        capped = await client.get("/api/reminders")
    assert capped.status_code == 200, capped.text
    body = capped.json()
    assert body["matched_count"] > body["read_limit"] == 1000
    assert body["completeness"] == "incomplete"
    assert len(body["reminders"]) <= 1000
    assert "rem-smg" not in {row["id"] for row in body["reminders"]}
