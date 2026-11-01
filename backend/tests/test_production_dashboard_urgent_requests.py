"""Dashboard Daftar Pengajuan Urgent. Local fixtures only. No production data."""
from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("MONGO_URL", "mongodb://127.0.0.1:27017")
_worker = os.environ.get("PYTEST_XDIST_WORKER") or "main"
os.environ["DB_NAME"] = f"eternal_packaging_prod_urgent_dash_test_{_worker}"
os.environ.setdefault("JWT_SECRET", "test-secret-key-12345678901234567890")
os.environ.setdefault("CORS_ORIGINS", "*")

import server
from server import app, current_user
from permissions import ROLE_DEFAULT_PERMISSIONS, OPT_IN_ONLY_PERMISSIONS, ALL_PERMISSIONS
from services.finance_widget_layout import finance_widget_allowed
from services.production_dashboard_urgent_requests import PERM_MONITOR
from services.production_widget_layout import PRODUCTION_SPEC

pytestmark = pytest.mark.anyio

YGY = "br-urgent-ygy"
SMG = "br-urgent-smg"
MONITOR = "dashboard.production.urgent_requests.view"


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture(autouse=True)
def bind_motor():
    from motor.motor_asyncio import AsyncIOMotorClient

    server.client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    server.db = server.client[os.environ["DB_NAME"]]


@pytest.fixture
async def clean_db(bind_motor):
    await server.db.orders.delete_many({})
    await server.db.urgent_requests.delete_many({})
    await server.db.branches.delete_many({})
    await server.db.branches.insert_many([
        {"id": YGY, "code": "YGY", "name": "Yogyakarta", "active": True},
        {"id": SMG, "code": "SMG", "name": "Semarang", "active": True},
    ])
    yield
    await server.db.orders.delete_many({})
    await server.db.urgent_requests.delete_many({})
    await server.db.branches.delete_many({})
    app.dependency_overrides.pop(current_user, None)


def _user(role="head_production", *, branch_ids=None, **perms):
    user = {
        "id": f"u-{uuid.uuid4().hex[:8]}",
        "user_id": f"u-{uuid.uuid4().hex[:8]}",
        "email": f"{uuid.uuid4().hex[:8]}@example.com",
        "name": role,
        "role": role,
        "status": "active",
    }
    if branch_ids is not None:
        user["branch_ids"] = list(branch_ids)
        user["primary_branch_id"] = branch_ids[0]
        user["branch_id"] = branch_ids[0]
    if perms:
        user["permissions_override"] = perms
    return user


def _order(oid, *, branch_id=YGY, customer="Customer A", po_number=None):
    return {
        "id": oid,
        "po_number": po_number or f"PO-{oid.upper()}",
        "customer": customer,
        "customer_name": customer,
        "branch_id": branch_id,
        "operational_branch_id": branch_id,
        "cancelled": False,
        "priority": "normal",
    }


def _req(oid, order_id, *, status="pending", po_number=None, reason="Butuh cepat", created_at="2026-11-01T10:00:00+07:00", **extra):
    doc = {
        "id": oid,
        "order_id": order_id,
        "po_number": po_number or f"PO-{order_id.upper()}",
        "status": status,
        "reason": reason,
        "target_completion_date": "2026-11-05",
        "notes": "",
        "requested_by_id": "req-1",
        "requested_by_email": "req@example.com",
        "requested_by_name": "Budi",
        "requested_at": created_at,
        "decided_by_id": None,
        "decided_by_email": None,
        "decided_by_name": None,
        "decided_at": None,
        "decision_notes": "",
        "created_at": created_at,
        "updated_at": created_at,
        "history": [{
            "action": "submitted",
            "at": created_at,
            "by_id": "req-1",
            "by_email": "req@example.com",
            "by_name": "Budi",
            "note": reason,
        }],
    }
    doc.update(extra)
    return doc


async def _get(params=None):
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.get("/api/dashboard/produksi/urgent-requests", params=params or {})


def test_monitor_permission_catalog_and_head_production_default():
    assert PERM_MONITOR == MONITOR
    assert MONITOR in ALL_PERMISSIONS
    assert MONITOR not in OPT_IN_ONLY_PERMISSIONS
    assert MONITOR in ROLE_DEFAULT_PERMISSIONS["head_production"]
    assert "urgent.review" not in ROLE_DEFAULT_PERMISSIONS["head_production"]
    assert "urgent.approve" not in ROLE_DEFAULT_PERMISSIONS["head_production"]
    assert MONITOR not in ROLE_DEFAULT_PERMISSIONS["head_operations"]
    assert finance_widget_allowed("urgent_requests", {"dashboard.production.view"}, PRODUCTION_SPEC) is False
    assert finance_widget_allowed("urgent_requests", {MONITOR}, PRODUCTION_SPEC) is True
    assert finance_widget_allowed("urgent_requests", {"urgent.review"}, PRODUCTION_SPEC) is True
    assert finance_widget_allowed("urgent_requests", {"urgent.approve"}, PRODUCTION_SPEC) is True
    assert "urgent_requests" in PRODUCTION_SPEC["ready"]
    assert "urgent_requests" not in PRODUCTION_SPEC["order"]


@pytest.mark.usefixtures("clean_db")
async def test_monitor_perm_allows_widget_but_not_legacy_list_or_approve():
    await server.db.orders.insert_one(_order("ord-a", customer="Alpha"))
    await server.db.urgent_requests.insert_one(_req("ur-1", "ord-a"))

    # Role without monitor default; only dashboard view must stay 403.
    view_only = _user(role="crm", branch_ids=[YGY], **{"dashboard.production.view": True})
    app.dependency_overrides[current_user] = lambda: view_only
    assert (await _get()).status_code == 403

    hop = _user(
        branch_ids=[YGY],
        **{
            "dashboard.production.view": True,
            MONITOR: True,
            "po.view": True,
            "urgent.review": False,
            "urgent.approve": False,
        },
    )
    app.dependency_overrides[current_user] = lambda: hop
    r = await _get()
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["total"] == 1
    assert body["can_open_po"] is True
    assert body["items"][0]["customer_name"] == "Alpha"
    assert body["items"][0]["history"][0]["action"] == "submitted"

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        legacy = await client.get("/api/urgent-requests", params={"status": "pending", "limit": 10})
        assert legacy.status_code == 403
        decide = await client.post(
            "/api/urgent-requests/ur-1/decide",
            json={"decision": "approved", "decision_notes": "no"},
        )
        assert decide.status_code == 403
        post = await client.post("/api/dashboard/produksi/urgent-requests")
        assert post.status_code in (405, 404, 422)


@pytest.mark.usefixtures("clean_db")
async def test_review_still_works_and_does_not_grant_approve():
    await server.db.orders.insert_one(_order("ord-a", customer="Alpha"))
    await server.db.urgent_requests.insert_one(_req("ur-1", "ord-a"))
    reviewer = _user(
        branch_ids=[YGY],
        **{"dashboard.production.view": True, "urgent.review": True, "po.view": True},
    )
    app.dependency_overrides[current_user] = lambda: reviewer
    r = await _get()
    assert r.status_code == 200
    assert reviewer.get("permissions_override", {}).get("urgent.approve") is not True


@pytest.mark.usefixtures("clean_db")
async def test_branch_isolation_filter_search_pagination_and_contracts():
    await server.db.orders.insert_many([
        _order("ord-y1", branch_id=YGY, customer="Kopi Nusantara", po_number="PO-YGY-1"),
        _order("ord-y2", branch_id=YGY, customer="Beta Shop", po_number="PO-YGY-2"),
        _order("ord-s1", branch_id=SMG, customer="Kopi Semarang", po_number="PO-SMG-1"),
    ])
    await server.db.urgent_requests.insert_many([
        _req("ur-y-pending", "ord-y1", status="pending", po_number="PO-YGY-1", created_at="2026-11-03T10:00:00+07:00"),
        _req("ur-y-approving", "ord-y2", status="approving", po_number="PO-YGY-2", created_at="2026-11-02T10:00:00+07:00"),
        _req(
            "ur-y-approved",
            "ord-y1",
            status="approved",
            po_number="PO-YGY-1",
            created_at="2026-10-01T10:00:00+07:00",
            decided_at="2026-10-01T12:00:00+07:00",
            decided_by_name="Approver",
            history=[
                {"action": "submitted", "at": "2026-10-01T10:00:00+07:00", "by_name": "Budi", "note": "lama"},
                {"action": "approved", "at": "2026-10-01T12:00:00+07:00", "by_name": "Approver", "note": "ok"},
            ],
        ),
        _req("ur-s-pending", "ord-s1", status="pending", po_number="PO-SMG-1", created_at="2026-11-04T10:00:00+07:00"),
    ])
    await server.db.orders.insert_one({
        **_order("ord-legacy", branch_id=YGY, customer="Legacy Co", po_number="PO-LEGACY"),
        "priority": "urgent",
    })

    scoped = _user(
        branch_ids=[YGY],
        **{"dashboard.production.view": True, MONITOR: True, "po.view": True},
    )
    app.dependency_overrides[current_user] = lambda: scoped

    active = await _get({"status": "active", "page": 1, "page_size": 5})
    assert active.status_code == 200, active.text
    body = active.json()
    assert body["filter"]["status"] == "active"
    assert body["total"] == 2
    ids = [row["id"] for row in body["items"]]
    assert ids == ["ur-y-pending", "ur-y-approving"]
    assert "ur-s-pending" not in ids
    assert "ur-y-approved" not in ids

    # Search must not surface Semarang via customer hit
    search_smg = await _get({"status": "all", "q": "Semarang"})
    assert search_smg.json()["total"] == 0
    assert search_smg.json()["items"] == []

    search = await _get({"status": "all", "q": "Nusantara"})
    assert search.json()["total"] == 2
    assert {row["id"] for row in search.json()["items"]} == {"ur-y-pending", "ur-y-approved"}

    page1 = await _get({"status": "all", "page": 1, "page_size": 2})
    page2 = await _get({"status": "all", "page": 2, "page_size": 2})
    assert page1.json()["total"] == 3
    assert len(page1.json()["items"]) == 2
    assert len(page2.json()["items"]) == 1
    assert all(row["id"] != "ur-s-pending" for row in page1.json()["items"] + page2.json()["items"])

    approved = await _get({"status": "approved"})
    assert approved.json()["items"][0]["id"] == "ur-y-approved"
    assert len(approved.json()["items"][0]["history"]) == 2

    multi = await _get({"status": "all", "q": "PO-YGY-1"})
    assert multi.json()["total"] == 2
