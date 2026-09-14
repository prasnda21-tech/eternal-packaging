"""Focused tests: assign/change driver from Route Review (canonical loading_checklist.driver_user_id)."""
import os
import sys
import uuid
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("MONGO_URL", "mongodb://127.0.0.1:27017")
os.environ.setdefault("DB_NAME", "eternal_packaging_driver_assign_test")
os.environ.setdefault("JWT_SECRET", "test-secret-key-12345678901234567890")
os.environ.setdefault("CORS_ORIGINS", "*")
os.environ.setdefault("STORAGE_PROVIDER", "local")

import server
from server import app, current_user
from services.delivery_driver_assignment import ASSIGN_DRIVER_PERM, ELIGIBLE_DRIVER_ROLES
from services.independent_route.constants import COL_PLANS, COL_STOPS, OPT_STATUS_OPTIMIZED
from services.independent_route.store import ensure_indexes
from quantity_rules import qty_loaded, loading_status_semantic

pytestmark = pytest.mark.anyio

PREFIX = "DRV-ASSIGN"


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
    await ensure_indexes(server.db)


def _user(**kw):
    uid = kw.get("id") or f"u-drv-assign-{uuid.uuid4().hex[:8]}"
    return {
        "id": uid,
        "user_id": uid,
        "email": kw.get("email") or f"{uid}@example.com",
        "name": kw.get("name") or "Assign Admin",
        "role": kw.get("role") or "admin",
        "status": "active",
        "permissions_override": kw.get("permissions_override") or {},
    }


async def _upsert_user(doc):
    await server.db.users.delete_many({"email": doc["email"]})
    await server.db.users.delete_many({"id": doc["id"]})
    await server.db.users.insert_one(dict(doc))
    return doc


async def client_for(user):
    app.dependency_overrides[current_user] = lambda: user
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


async def _clean():
    await server.db.orders.delete_many({"id": {"$regex": f"^{PREFIX}"}})
    await server.db.users.delete_many({"id": {"$regex": f"^{PREFIX}"}})
    await server.db.users.delete_many({"email": {"$regex": f"^{PREFIX}"}})
    await server.db[COL_PLANS].delete_many({"name": {"$regex": "Pengiriman|DRV-ASSIGN|IRO-TEST"}})
    await server.db[COL_STOPS].delete_many({"sales_order_id": {"$regex": f"^{PREFIX}"}})


def _order(oid, *, driver_id=None, driver_name=None, lat=None, lng=None, date="2026-09-11", address="Jl Test", **extra):
    loc = {}
    if lat is not None and lng is not None:
        loc = {"lat": lat, "lng": lng, "formatted_address": address}
    shipment_id = f"sj-{oid}"
    shipments = extra.pop("shipments", [{
        "id": shipment_id,
        "file_id": shipment_id,
        "number": f"SJ-{oid}",
        "lines": [{"line_idx": 0, "qty": 100}],
    }])
    surat_jalan_files = extra.pop("surat_jalan_files", [{
        "id": shipment_id,
        "shipment_id": shipment_id,
        "name": f"SJ-{oid}.pdf",
        "content_type": "application/pdf",
        "source": "SYSTEM_GENERATED",
        "storage_path": f"surat-jalan/{shipment_id}.pdf",
        "lines": [{"line_idx": 0, "qty": 100}],
    }])
    lc = {}
    if driver_id:
        lc["driver_user_id"] = driver_id
    if driver_name:
        lc["driver_name"] = driver_name
    # Preserve any completion flags if tests pass them via loading_checklist merge
    extra_lc = extra.pop("loading_checklist_extra", {}) or {}
    lc.update(extra_lc)
    doc = {
        "id": oid,
        "po_number": f"PO-{oid}",
        "customer_name": extra.pop("customer_name", f"Customer {oid}"),
        "customer_id": extra.pop("customer_id", f"cust-{oid}"),
        "deadline": date,
        "scheduled_delivery_date": date,
        "delivery_status": extra.pop("delivery_status", ""),
        "cancelled": False,
        "total_quantity": 100,
        "qty_delivered": extra.pop("qty_delivered", 0),
        "packing_info": extra.pop("packing_info", {"qty_packed": 100}),
        "line_items": [{"name": "Box", "quantity": 100}],
        "shipments": shipments,
        "surat_jalan_files": surat_jalan_files,
        "loading_checklist": lc,
        "loading_confirmations": extra.pop("loading_confirmations", []),
        "delivery_location": loc,
        "address": address,
        **extra,
    }
    return doc


async def _seed_drivers():
    a = await _upsert_user(_user(id=f"{PREFIX}-DRV-A", name="Driver A Preview", role="driver", email=f"{PREFIX}-a@ex.com"))
    b = await _upsert_user(_user(id=f"{PREFIX}-DRV-B", name="Driver B Preview", role="driver", email=f"{PREFIX}-b@ex.com"))
    helper = await _upsert_user(_user(id=f"{PREFIX}-HELPER", name="Helper Not Driver", role="helper", email=f"{PREFIX}-h@ex.com"))
    return a, b, helper


def _workflow_guard(order: dict) -> dict:
    return {
        "loading_confirmations": list(order.get("loading_confirmations") or []),
        "items_loaded": (order.get("loading_checklist") or {}).get("items_loaded"),
        "seal_ok": (order.get("loading_checklist") or {}).get("seal_ok"),
        "packing_info": order.get("packing_info"),
        "shipments": order.get("shipments"),
        "delivery_status": order.get("delivery_status"),
        "qty_delivered": order.get("qty_delivered"),
        "qty_loaded": qty_loaded(order),
        "loading_semantic": loading_status_semantic(order),
    }


@pytest.mark.anyio
async def test_eligible_driver_list_excludes_management_and_inactive():
    """Assignable Driver candidates: role==driver + active only.

    Assigner roles (admin/head) must NOT appear as candidates unless their
    user.role is literally driver (single-role model — no dual eligibility flag).
    """
    await _clean()
    a, b, helper = await _seed_drivers()
    inactive = await _upsert_user(_user(
        id=f"{PREFIX}-DRV-INACTIVE",
        name="Inactive Driver",
        role="driver",
        email=f"{PREFIX}-inactive@ex.com",
    ))
    await server.db.users.update_one({"id": inactive["id"]}, {"$set": {"status": "inactive"}})

    admin = await _upsert_user(_user(
        id=f"{PREFIX}-ADMIN",
        name="Admin Not Driver",
        role="admin",
        email=f"{PREFIX}-admin@ex.com",
    ))
    hop = await _upsert_user(_user(
        id=f"{PREFIX}-HOP",
        name="HoP Not Driver",
        role="head_production",
        email=f"{PREFIX}-hop@ex.com",
    ))
    hoo = await _upsert_user(_user(
        id=f"{PREFIX}-HOO",
        name="HoO Not Driver",
        role="head_operations",
        email=f"{PREFIX}-hoo@ex.com",
    ))

    # Authorized assigner (admin) may list candidates — but is not themselves a candidate.
    client = await client_for(admin)
    r = await client.get("/api/delivery/drivers")
    assert r.status_code == 200, r.text
    ids = {d["id"] for d in r.json()["drivers"]}
    roles = {d["id"]: d.get("role") for d in r.json()["drivers"]}

    assert a["id"] in ids
    assert b["id"] in ids
    assert helper["id"] not in ids
    assert inactive["id"] not in ids
    assert admin["id"] not in ids
    assert hop["id"] not in ids
    assert hoo["id"] not in ids
    assert all(roles[i] == "driver" for i in ids if i.startswith(PREFIX))

    # GET purity
    before = await server.db.orders.count_documents({})
    r2 = await client.get("/api/delivery/drivers")
    assert r2.status_code == 200
    assert await server.db.orders.count_documents({}) == before

    # Assigner with permission can still ASSIGN an eligible driver (not themselves).
    oid = f"{PREFIX}-ASSIGN-OK"
    await server.db.orders.insert_one(_order(
        oid,
        lat=-6.2,
        lng=106.8,
        loading_confirmations=[{"id": "lc1", "lines": [{"line_idx": 0, "qty": 3}]}],
        packing_info={"qty_packed": 100},
        qty_delivered=0,
        delivery_status="",
        loading_checklist_extra={"items_loaded": False, "seal_ok": False},
    ))
    before_order = await server.db.orders.find_one({"id": oid}, {"_id": 0})
    guard = _workflow_guard(before_order)

    # Reject assigning admin/head as driver_user_id
    for bad_id in (admin["id"], hop["id"], hoo["id"]):
        bad = await client.patch(f"/api/delivery/orders/{oid}/driver", json={"driver_user_id": bad_id})
        assert bad.status_code in (400, 404), bad.text

    ok = await client.patch(f"/api/delivery/orders/{oid}/driver", json={"driver_user_id": a["id"]})
    assert ok.status_code == 200, ok.text
    assert ok.json()["result"]["status"] == "updated"
    assert ok.json()["result"]["driver_user_id"] == a["id"]

    after = await server.db.orders.find_one({"id": oid}, {"_id": 0})
    lc = after.get("loading_checklist") or {}
    assert lc.get("driver_user_id") == a["id"]
    assert lc.get("driver_name") == a["name"]
    assert _workflow_guard(after) == guard

    # Head production with default perms (has delivery.location.manage) can assign too.
    hop_client = await client_for(hop)
    ok2 = await hop_client.patch(f"/api/delivery/orders/{oid}/driver", json={"driver_user_id": b["id"]})
    assert ok2.status_code == 200, ok2.text
    assert ok2.json()["result"]["driver_user_id"] == b["id"]
    after2 = await server.db.orders.find_one({"id": oid}, {"_id": 0})
    assert _workflow_guard(after2) == guard


@pytest.mark.anyio
async def test_unauthorized_cannot_assign_driver():
    await _clean()
    a, _, _ = await _seed_drivers()
    driver_user = await _upsert_user(_user(role="driver", name="Just Driver"))
    from permissions import ROLE_DEFAULT_PERMISSIONS
    assert ASSIGN_DRIVER_PERM not in ROLE_DEFAULT_PERMISSIONS["driver"]

    oid = f"{PREFIX}-U1"
    await server.db.orders.insert_one(_order(oid, lat=-6.2, lng=106.8))
    client = await client_for(driver_user)
    r = await client.patch(f"/api/delivery/orders/{oid}/driver", json={"driver_user_id": a["id"]})
    assert r.status_code in (401, 403), r.text
    order = await server.db.orders.find_one({"id": oid}, {"_id": 0})
    assert not (order.get("loading_checklist") or {}).get("driver_user_id")

    r2 = await client.post(
        "/api/delivery/orders/assign-driver",
        json={"order_ids": [oid], "driver_user_id": a["id"]},
    )
    assert r2.status_code in (401, 403), r2.text


@pytest.mark.anyio
async def test_per_row_assignment_exact_id_and_workflow_lock():
    await _clean()
    a, b, _ = await _seed_drivers()
    admin = await _upsert_user(_user())
    client = await client_for(admin)
    oid = f"{PREFIX}-P1"
    await server.db.orders.insert_one(_order(
        oid,
        lat=-6.2,
        lng=106.8,
        loading_confirmations=[{"id": "lc1", "lines": [{"line_idx": 0, "qty": 5}]}],
        packing_info={"qty_packed": 100},
        qty_delivered=0,
        delivery_status="",
        loading_checklist_extra={"items_loaded": False, "seal_ok": False},
    ))
    before = await server.db.orders.find_one({"id": oid}, {"_id": 0})
    guard_before = _workflow_guard(before)

    # Reject name matching — only exact user id
    r_bad = await client.patch(f"/api/delivery/orders/{oid}/driver", json={"driver_user_id": "Driver A Preview"})
    assert r_bad.status_code in (400, 404), r_bad.text

    r = await client.patch(f"/api/delivery/orders/{oid}/driver", json={"driver_user_id": a["id"]})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True
    assert body["result"]["status"] == "updated"
    assert body["result"]["driver_user_id"] == a["id"]
    assert body["result"]["sales_order_id"] == oid

    after = await server.db.orders.find_one({"id": oid}, {"_id": 0})
    lc = after.get("loading_checklist") or {}
    assert lc.get("driver_user_id") == a["id"]
    assert lc.get("driver_name") == a["name"]
    assert _workflow_guard(after) == guard_before

    # Idempotent unchanged
    r2 = await client.patch(f"/api/delivery/orders/{oid}/driver", json={"driver_user_id": a["id"]})
    assert r2.json()["result"]["status"] == "unchanged"

    # Change to B
    r3 = await client.patch(f"/api/delivery/orders/{oid}/driver", json={"driver_user_id": b["id"]})
    assert r3.json()["result"]["status"] == "updated"
    assert r3.json()["result"]["previous_driver_user_id"] == a["id"]
    after2 = await server.db.orders.find_one({"id": oid}, {"_id": 0})
    assert (after2.get("loading_checklist") or {}).get("driver_user_id") == b["id"]
    assert _workflow_guard(after2) == guard_before


@pytest.mark.anyio
async def test_bulk_assignment_exact_selected_only_and_partial():
    await _clean()
    a, b, _ = await _seed_drivers()
    admin = await _upsert_user(_user())
    client = await client_for(admin)
    selected = [f"{PREFIX}-B1", f"{PREFIX}-B2"]
    untouched = f"{PREFIX}-B3"
    missing = f"{PREFIX}-MISSING"
    await server.db.orders.insert_many([
        _order(selected[0], lat=-6.2, lng=106.8),
        _order(selected[1], driver_id=a["id"], driver_name=a["name"], lat=-6.21, lng=106.81),
        _order(untouched, driver_id=a["id"], driver_name=a["name"], lat=-6.22, lng=106.82),
    ])
    before_untouched = await server.db.orders.find_one({"id": untouched}, {"_id": 0})

    r = await client.post(
        "/api/delivery/orders/assign-driver",
        json={"order_ids": selected + [missing], "driver_user_id": b["id"]},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["partial"] is True or body["ok"] is False
    by_id = {row["sales_order_id"]: row for row in body["results"]}
    assert by_id[selected[0]]["status"] == "updated"
    assert by_id[selected[1]]["status"] == "updated"
    assert by_id[missing]["status"] == "failed"
    assert body["counts"]["failed"] == 1
    assert body["counts"]["updated"] == 2

    after_untouched = await server.db.orders.find_one({"id": untouched}, {"_id": 0})
    assert after_untouched.get("loading_checklist") == before_untouched.get("loading_checklist")

    # Same driver again → unchanged for already-B rows
    r2 = await client.post(
        "/api/delivery/orders/assign-driver",
        json={"order_ids": selected, "driver_user_id": b["id"]},
    )
    assert r2.status_code == 200
    assert r2.json()["counts"]["unchanged"] == 2
    assert r2.json()["ok"] is True


@pytest.mark.anyio
async def test_review_eligibility_ignores_driver_and_grouping_updates_after_assignment():
    await _clean()
    a, _, _ = await _seed_drivers()
    admin = await _upsert_user(_user())
    client = await client_for(admin)
    x = f"{PREFIX}-G1"
    y = f"{PREFIX}-G2"
    await server.db.orders.insert_many([
        _order(x, lat=-6.2, lng=106.8),
        _order(y, lat=-6.21, lng=106.81),
    ])
    rev = await client.post("/api/route-optimizer/from-schedule/review", json={"order_ids": [x, y]})
    assert rev.status_code == 200
    rows = {r["sales_order_id"]: r for r in rev.json()["rows"]}
    assert rows[x]["eligible"] is True
    assert not rows[x].get("driver_user_id")
    assert "Belum ada driver" not in (rows[x].get("reasons") or [])

    assign = await client.post(
        "/api/delivery/orders/assign-driver",
        json={"order_ids": [x, y], "driver_user_id": a["id"]},
    )
    assert assign.status_code == 200
    assert assign.json()["ok"] is True

    rev2 = await client.post("/api/route-optimizer/from-schedule/review", json={"order_ids": [x, y]})
    body = rev2.json()
    rows2 = {r["sales_order_id"]: r for r in body["rows"]}
    assert rows2[x]["eligible"] is True
    assert rows2[y]["eligible"] is True
    assert rows2[x]["driver_user_id"] == a["id"]
    groups = {g["driver_user_id"]: g["sales_order_ids"] for g in body["groups"]}
    assert set(groups[a["id"]]) == {x, y}


@pytest.mark.anyio
async def test_optimized_route_remains_valid_when_order_driver_changes():
    await _clean()
    a, b, _ = await _seed_drivers()
    admin = await _upsert_user(_user())
    client = await client_for(admin)
    oid = f"{PREFIX}-STALE"
    other = f"{PREFIX}-OTHER"
    await server.db.orders.insert_many([
        _order(oid, driver_id=a["id"], driver_name=a["name"], lat=-6.2, lng=106.8),
        _order(other, driver_id=a["id"], driver_name=a["name"], lat=-6.25, lng=106.85),
    ])
    opt = await client.post("/api/route-optimizer/from-schedule/optimize", json={"order_ids": [oid]})
    assert opt.status_code == 200, opt.text
    assert opt.json().get("ok") or opt.json().get("partial")

    st = await client.get("/api/route-optimizer/from-schedule/status", params={"ids": oid})
    assert st.json()["statuses"][oid]["optimization_status"] == "sudah_dioptimasi"
    route_id = st.json()["statuses"][oid]["route_id"]

    before_other_plans = await server.db[COL_PLANS].count_documents({})
    before_other_stops = await server.db[COL_STOPS].count_documents({"sales_order_id": other})

    ch = await client.patch(f"/api/delivery/orders/{oid}/driver", json={"driver_user_id": b["id"]})
    assert ch.status_code == 200
    assert ch.json()["result"]["status"] == "updated"

    st2 = await client.get("/api/route-optimizer/from-schedule/status", params={"ids": oid})
    # Route driver is frozen when the route is saved. Editing the order's
    # operational driver does not invalidate or rewrite that optimized route.
    assert st2.json()["statuses"][oid]["optimization_status"] == OPT_STATUS_OPTIMIZED
    assert st2.json()["statuses"][oid]["route_id"] == route_id
    assert await server.db[COL_PLANS].count_documents({}) == before_other_plans
    assert await server.db[COL_STOPS].count_documents({"sales_order_id": other}) == before_other_stops


@pytest.mark.anyio
async def test_assign_when_loading_checklist_is_null_no_http_500():
    """Imported/manual POs often store loading_checklist: null — must not 500 on dotted $set."""
    await _clean()
    a, _, _ = await _seed_drivers()
    admin = await _upsert_user(_user())
    client = await client_for(admin)
    oid = f"{PREFIX}-NULL-LC"
    doc = _order(oid, lat=-6.2, lng=106.8)
    doc["loading_checklist"] = None  # exact legacy/current import shape
    await server.db.orders.insert_one(doc)
    before = await server.db.orders.find_one({"id": oid}, {"_id": 0})
    guard_before = _workflow_guard(before)

    r = await client.patch(f"/api/delivery/orders/{oid}/driver", json={"driver_user_id": a["id"]})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True
    assert body["result"]["status"] == "updated"
    assert body["result"]["driver_user_id"] == a["id"]
    assert body["result"]["driver_name"] == a["name"]

    after = await server.db.orders.find_one({"id": oid}, {"_id": 0})
    lc = after.get("loading_checklist")
    assert isinstance(lc, dict)
    assert lc.get("driver_user_id") == a["id"]
    assert lc.get("driver_name") == a["name"]
    assert _workflow_guard(after) == guard_before

    # Persist readable on review reload (Driver: N source)
    rev = await client.post("/api/route-optimizer/from-schedule/review", json={"order_ids": [oid]})
    assert rev.status_code == 200, rev.text
    row = rev.json()["rows"][0]
    assert row["driver_user_id"] == a["id"]
    assert row["driver_name"] == a["name"]
    # Eligibility unchanged by assignment alone (still needs coords etc. — here coords present,
    # but "Belum ada driver" cleared; do not assert eligible True if other rules apply).
    assert "Belum ada driver" not in (row.get("reasons") or [])

    # Idempotent second save
    r2 = await client.patch(f"/api/delivery/orders/{oid}/driver", json={"driver_user_id": a["id"]})
    assert r2.status_code == 200
    assert r2.json()["result"]["status"] == "unchanged"

    # Invalid/missing driver id → controlled 4xx, not uncontrolled 500
    r_bad = await client.patch(f"/api/delivery/orders/{oid}/driver", json={"driver_user_id": ""})
    assert r_bad.status_code == 400, r_bad.text
    r_missing = await client.patch(
        f"/api/delivery/orders/{oid}/driver",
        json={"driver_user_id": f"{PREFIX}-NO-SUCH-DRIVER"},
    )
    assert r_missing.status_code in (400, 404), r_missing.text
    assert r_missing.status_code != 500


@pytest.mark.anyio
async def test_unassign_not_supported():
    await _clean()
    a, _, _ = await _seed_drivers()
    admin = await _upsert_user(_user())
    client = await client_for(admin)
    oid = f"{PREFIX}-NO-CLEAR"
    await server.db.orders.insert_one(_order(oid, driver_id=a["id"], driver_name=a["name"], lat=-6.2, lng=106.8))
    r = await client.patch(f"/api/delivery/orders/{oid}/driver", json={"driver_user_id": ""})
    assert r.status_code == 400, r.text
    after = await server.db.orders.find_one({"id": oid}, {"_id": 0})
    assert (after.get("loading_checklist") or {}).get("driver_user_id") == a["id"]


@pytest.mark.anyio
async def test_current_driver_returned_on_review():
    await _clean()
    a, _, _ = await _seed_drivers()
    admin = await _upsert_user(_user())
    client = await client_for(admin)
    oid = f"{PREFIX}-CUR"
    await server.db.orders.insert_one(_order(oid, driver_id=a["id"], driver_name=a["name"], lat=-6.2, lng=106.8))
    r = await client.post("/api/route-optimizer/from-schedule/review", json={"order_ids": [oid]})
    row = r.json()["rows"][0]
    assert row["driver_user_id"] == a["id"]
    assert row["driver_name"] == a["name"]


@pytest.mark.anyio
async def test_eligible_roles_constant_matches_canonical():
    assert ELIGIBLE_DRIVER_ROLES == frozenset({"driver"})
    assert ASSIGN_DRIVER_PERM == "delivery.location.manage"
    # Separation: assigner permission is not an assignable-driver role.
    assert ASSIGN_DRIVER_PERM not in ELIGIBLE_DRIVER_ROLES
