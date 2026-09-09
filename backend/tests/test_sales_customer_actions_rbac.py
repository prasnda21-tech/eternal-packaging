"""Focused tests for customer view/edit/status + RBAC (Data Pelanggan)."""
import os
import sys
import uuid
import pytest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ.setdefault("MONGO_URL", "mongodb://127.0.0.1:27017")
os.environ.setdefault("DB_NAME", "eternal_packaging_test")
os.environ.setdefault("JWT_SECRET", "test-secret-key-12345678901234567890")
os.environ.setdefault("CORS_ORIGINS", "*")

import httpx
from server import app, current_user
from services.sales_customers import (
    normalize_customer_name,
    ensure_sales_customer_indexes,
    customer_is_active,
    COLLECTION,
)
from permissions import PERMISSION_CATALOG, ROLE_DEFAULT_PERMISSIONS, effective_permissions


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture(autouse=True)
def bind_motor():
    import server
    from motor.motor_asyncio import AsyncIOMotorClient

    server.client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    server.db = server.client[os.environ["DB_NAME"]]


def _admin():
    return {"email": "admin@eternalpackaging.com", "name": "Admin", "role": "admin"}


def _crm_with(*perms):
    override = {p: True for p in perms}
    return {
        "email": "crm-customer-actions@eternalpackaging.com",
        "name": "CRM",
        "role": "crm",
        "permissions_override": override,
    }


def test_permission_catalog_includes_edit_and_manage_status():
    keys = {k for _, k, _ in PERMISSION_CATALOG}
    assert "sales.customer.edit" in keys
    assert "sales.customer.manage_status" in keys
    labels = {k: label for _, k, label in PERMISSION_CATALOG}
    assert labels["sales.customer.edit"] == "Edit Pelanggan"
    assert labels["sales.customer.manage_status"] == "Kelola Status Pelanggan"

    for role, perms in ROLE_DEFAULT_PERMISSIONS.items():
        if role == "admin":
            continue
        assert "sales.customer.edit" not in perms
        assert "sales.customer.manage_status" not in perms

    crm = effective_permissions("crm", None)
    assert "sales.customer.edit" not in crm
    assert "sales.customer.manage_status" not in crm


def test_legacy_missing_is_active_treated_as_active_zero_write_semantics():
    assert customer_is_active({}) is True
    assert customer_is_active({"name": "X"}) is True
    assert customer_is_active({"is_active": None}) is True
    assert customer_is_active({"is_active": True}) is True
    assert customer_is_active({"is_active": False}) is False


@pytest.mark.anyio
async def test_customer_status_edit_rbac_and_history_materialize():
    import server
    from services.order_prediction_import import COLLECTION as HISTORY_COLLECTION

    await ensure_sales_customer_indexes(server.db)
    suffix = uuid.uuid4().hex[:8]
    master_id = f"cust-status-{suffix}"
    master_name = f"Status Master {suffix}"
    hist_name = f"History Edit {suffix}"
    hist_id = f"hist-edit-{suffix}"
    same_a = f"Same Name A {suffix}"
    same_b = f"Same Name B {suffix}"
    id_a = f"id-same-a-{suffix}"
    id_b = f"id-same-b-{suffix}"
    order_hist_po = f"ord-keep-{suffix}"
    mat_id = None

    await server.db[COLLECTION].insert_many(
        [
            {
                "id": master_id,
                "name": master_name,
                "normalized_name": normalize_customer_name(master_name),
                "phone": "0811",
                "address": "Jl Master",
                "pic": "PIC",
                "email": "",
                "city": "Bekasi",
                "notes": "awal",
            },
            {
                "id": id_a,
                "name": same_a,
                "normalized_name": normalize_customer_name(same_a),
                "phone": "1",
                "address": "A",
                "pic": "",
                "email": "",
                "city": "",
                "notes": "",
            },
            {
                "id": id_b,
                "name": same_b,
                "normalized_name": normalize_customer_name(same_b),
                "phone": "2",
                "address": "B",
                "pic": "",
                "email": "",
                "city": "",
                "notes": "",
            },
        ]
    )
    await server.db[HISTORY_COLLECTION].insert_one(
        {
            "id": hist_id,
            "customer": hist_name,
            "customer_name": hist_name,
            "customer_id": None,
            "outlet": "Solo",
            "order_date": "2026-01-01",
            "cancelled": False,
        }
    )
    await server.db.orders.insert_one(
        {
            "id": order_hist_po,
            "po_number": f"PO-KEEP-{suffix}",
            "customer": hist_name,
            "customer_id": None,
            "cancelled": False,
            "line_items": [{"name": "Box", "quantity": 1}],
        }
    )

    before_orders = await server.db.orders.count_documents({"id": order_hist_po})
    before_hist = await server.db[HISTORY_COLLECTION].count_documents({"id": hist_id})
    before_master_doc = await server.db[COLLECTION].find_one({"id": master_id})
    assert "is_active" not in before_master_doc

    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            app.dependency_overrides[current_user] = _admin
            r_list = await client.get(
                "/api/sales-customers", params={"view": "unified", "q": suffix}
            )
            assert r_list.status_code == 200
            rows = r_list.json()["customers"]
            by_id = {r["id"]: r for r in rows if r.get("id")}
            by_name = {}
            for r in rows:
                by_name.setdefault(r["name"], []).append(r)

            assert by_id[master_id]["is_active"] is True
            assert by_id[master_id]["name"] == master_name
            assert len(by_name[same_a]) == 1 and by_name[same_a][0]["id"] == id_a
            assert len(by_name[same_b]) == 1 and by_name[same_b][0]["id"] == id_b

            hist_rows = by_name[hist_name]
            assert len(hist_rows) == 1
            assert hist_rows[0]["is_master"] is False
            assert hist_rows[0]["source"] in ("prediction_history", "mixed", "live_po")
            assert hist_rows[0]["is_active"] is True
            hist_key = hist_rows[0]["customer_key"]
            assert hist_key.startswith("name:")

            after_get = await server.db[COLLECTION].find_one({"id": master_id})
            assert "is_active" not in after_get

            app.dependency_overrides[current_user] = lambda: _crm_with("sales.customer.view")
            r_forbidden_edit = await client.put(
                f"/api/sales-customers/{master_id}",
                json={
                    "name": master_name,
                    "phone": "0811999",
                    "address": "Jl Baru",
                    "pic": "",
                    "email": "",
                    "city": "",
                    "notes": "",
                },
            )
            assert r_forbidden_edit.status_code == 403

            app.dependency_overrides[current_user] = lambda: _crm_with(
                "sales.customer.view", "sales.customer.edit"
            )
            r_edit = await client.put(
                f"/api/sales-customers/{master_id}",
                json={
                    "name": master_name,
                    "phone": "0811999",
                    "address": "Jl Baru",
                    "pic": "PIC Baru",
                    "email": "",
                    "city": "Cikarang",
                    "notes": "catatan edit",
                },
            )
            assert r_edit.status_code == 200, r_edit.text
            edited = r_edit.json()["customer"]
            assert edited["id"] == master_id
            assert edited["phone"] == "0811999"
            assert edited["city"] == "Cikarang"
            count_master = await server.db[COLLECTION].count_documents(
                {"normalized_name": normalize_customer_name(master_name)}
            )
            assert count_master == 1

            r_edit_a = await client.put(
                f"/api/sales-customers/{id_a}",
                json={
                    "name": same_a,
                    "phone": "111",
                    "address": "AA",
                    "pic": "",
                    "email": "",
                    "city": "",
                    "notes": "only-a",
                },
            )
            assert r_edit_a.status_code == 200
            still_b = await server.db[COLLECTION].find_one({"id": id_b}, {"_id": 0})
            assert still_b["notes"] == ""
            assert still_b["phone"] == "2"

            app.dependency_overrides[current_user] = lambda: _crm_with(
                "sales.customer.view", "sales.customer.edit"
            )
            r_forbid_status = await client.patch(
                f"/api/sales-customers/{master_id}/status",
                json={"is_active": False},
            )
            assert r_forbid_status.status_code == 403

            app.dependency_overrides[current_user] = lambda: _crm_with(
                "sales.customer.view", "sales.customer.manage_status"
            )
            r_deact = await client.patch(
                f"/api/sales-customers/{master_id}/status",
                json={"is_active": False},
            )
            assert r_deact.status_code == 200, r_deact.text
            assert r_deact.json()["customer"]["is_active"] is False

            r_deact2 = await client.patch(
                f"/api/sales-customers/{master_id}/status",
                json={"is_active": False},
            )
            assert r_deact2.status_code == 200
            assert r_deact2.json()["customer"]["is_active"] is False

            app.dependency_overrides[current_user] = _admin
            r_list2 = await client.get(
                "/api/sales-customers", params={"view": "unified", "q": master_name}
            )
            assert r_list2.status_code == 200
            found = [c for c in r_list2.json()["customers"] if c.get("id") == master_id]
            assert len(found) == 1
            assert found[0]["is_active"] is False

            app.dependency_overrides[current_user] = lambda: _crm_with(
                "sales.customer.manage_status"
            )
            r_react = await client.patch(
                f"/api/sales-customers/{master_id}/status",
                json={"is_active": True},
            )
            assert r_react.status_code == 200
            assert r_react.json()["customer"]["is_active"] is True

            app.dependency_overrides[current_user] = lambda: _crm_with("sales.customer.edit")
            r_hist_edit = await client.put(
                "/api/sales-customers/by-key",
                json={
                    "customer_key": hist_key,
                    "name": hist_name,
                    "phone": "0855111222",
                    "address": "Jl Histori 1",
                    "pic": "Bu Hist",
                    "email": "",
                    "city": "Solo",
                    "notes": "dari histori",
                },
            )
            assert r_hist_edit.status_code == 200, r_hist_edit.text
            materialized = r_hist_edit.json()["customer"]
            assert materialized["id"]
            assert materialized["name"] == hist_name
            assert materialized["phone"] == "0855111222"
            mat_id = materialized["id"]

            r_hist_edit2 = await client.put(
                "/api/sales-customers/by-key",
                json={
                    "customer_key": hist_key,
                    "name": hist_name,
                    "phone": "0855333444",
                    "address": "Jl Histori 2",
                    "pic": "Bu Hist",
                    "email": "",
                    "city": "Solo",
                    "notes": "update 2",
                },
            )
            assert r_hist_edit2.status_code == 200
            assert r_hist_edit2.json()["customer"]["id"] == mat_id
            assert r_hist_edit2.json()["customer"]["phone"] == "0855333444"
            dup_count = await server.db[COLLECTION].count_documents(
                {"normalized_name": normalize_customer_name(hist_name)}
            )
            assert dup_count == 1

            after_orders = await server.db.orders.count_documents({"id": order_hist_po})
            after_hist = await server.db[HISTORY_COLLECTION].count_documents({"id": hist_id})
            assert after_orders == before_orders
            assert after_hist == before_hist
            order_doc = await server.db.orders.find_one({"id": order_hist_po})
            assert order_doc["customer"] == hist_name
            hist_doc = await server.db[HISTORY_COLLECTION].find_one({"id": hist_id})
            assert hist_doc is not None

            app.dependency_overrides[current_user] = lambda: _crm_with(
                "sales.customer.manage_status"
            )
            r_hist_deact = await client.patch(
                "/api/sales-customers/by-key/status",
                json={"customer_key": f"id:{mat_id}", "is_active": False},
            )
            assert r_hist_deact.status_code == 200
            assert r_hist_deact.json()["customer"]["is_active"] is False

            await server.db[COLLECTION].update_one({"id": id_a}, {"$unset": {"is_active": ""}})
            r_legacy_aktif = await client.patch(
                f"/api/sales-customers/{id_a}/status",
                json={"is_active": True},
            )
            assert r_legacy_aktif.status_code == 200
            legacy_doc = await server.db[COLLECTION].find_one({"id": id_a})
            assert "is_active" not in legacy_doc or legacy_doc.get("is_active") is None
    finally:
        app.dependency_overrides.pop(current_user, None)
        ids = [master_id, id_a, id_b]
        if mat_id:
            ids.append(mat_id)
        await server.db[COLLECTION].delete_many({"id": {"$in": ids}})
        await server.db[COLLECTION].delete_many(
            {
                "normalized_name": {
                    "$in": [
                        normalize_customer_name(master_name),
                        normalize_customer_name(hist_name),
                        normalize_customer_name(same_a),
                        normalize_customer_name(same_b),
                    ]
                }
            }
        )
        await server.db.orders.delete_many({"id": order_hist_po})
        await server.db[HISTORY_COLLECTION].delete_many({"id": hist_id})
