"""RBAC + integrity tests for CRM product requests."""
import os
import sys
import uuid
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ.setdefault("MONGO_URL", "mongodb://127.0.0.1:27017")
os.environ.setdefault("DB_NAME", "eternal_packaging_test")
os.environ.setdefault("JWT_SECRET", "test-secret-key-12345678901234567890")
os.environ.setdefault("CORS_ORIGINS", "*")

import httpx

from permissions import ALL_PERMISSIONS, ROLE_DEFAULT_PERMISSIONS, effective_permissions
from server import app, current_user
from services.product_requests import COLLECTION, ensure_indexes
from services.sales_customers import COLLECTION as CUSTOMERS, ensure_sales_customer_indexes


def _user(role, *, uid=None, name=None, email=None, override=None, branch_id="br-sby", branch="Surabaya"):
    slug = str(role)
    ident = uid or f"{slug}-{uuid.uuid4().hex[:6]}"
    return {
        "id": ident,
        "user_id": ident,
        "email": email or f"{ident}@eternal.test",
        "name": name or slug.upper(),
        "role": slug,
        "permissions_override": override or {},
        "branch_id": branch_id,
        "branch": branch,
    }


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture(autouse=True)
def bind_motor():
    import server
    from motor.motor_asyncio import AsyncIOMotorClient
    server.client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    server.db = server.client[os.environ["DB_NAME"]]


async def _upsert_users(db, users):
    for user in users:
        await db.users.delete_many({"email": user["email"]})
        await db.users.insert_one({
            "id": user["id"],
            "email": user["email"],
            "name": user["name"],
            "role": user["role"],
            "status": "active",
            "branch_id": user.get("branch_id"),
            "branch": user.get("branch"),
            "permissions_override": user.get("permissions_override") or {},
        })


async def _prepare(db, users, *, wipe_requests=True):
    await ensure_indexes(db)
    await ensure_sales_customer_indexes(db)
    if wipe_requests:
        await db[COLLECTION].delete_many({})
    await _upsert_users(db, users)


async def _seed_masters(db):
    suffix = uuid.uuid4().hex[:6]
    customer = {
        "id": f"cust-{suffix}",
        "name": f"Renee Coffee {suffix}",
        "normalized_name": f"renee coffee {suffix}",
        "phone": "081234567890",
        "pic": "Budi Santoso",
        "city": "Bandung",
        "address": "Jl. Kopi",
        "email": "",
        "notes": "",
        "is_active": True,
    }
    product = {
        "id": f"item-{suffix}",
        "accurate_item_id": f"acc-{suffix}",
        "item_code": f"PC-{suffix}",
        "item_name": f"Paper Cup {suffix}",
        "unit": "pcs",
        "is_active": True,
        "item_type": "INVENTORY",
    }
    await db[CUSTOMERS].delete_many({"id": customer["id"]})
    await db.master_items.delete_many({"id": product["id"]})
    await db[CUSTOMERS].insert_one(dict(customer))
    await db.master_items.insert_one(dict(product))
    return customer, product


def _client_for(user):
    app.dependency_overrides[current_user] = lambda: user
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


COMPLETE_NEW = {
    "customer_source": "new_prospect",
    "customer_name": "Kedai Baru",
    "pic": "Sari",
    "whatsapp": "081298765432",
    "city": "Jakarta",
    "product_source": "requested_product",
    "product_name": "Paper Cup 12oz",
    "category": "Paper Cup",
    "size": "12 oz",
    "material": "Food Grade Paper",
    "model": "Custom Logo",
    "potential_qty": 50000,
    "frequency": "Bulanan",
    "target_price": 850,
    "needed_date": "2026-10-15",
    "request_note": "Customer rebranding",
    "request_channel": "Referensi Customer",
}


def test_catalog_and_role_defaults():
    for key in (
        "product_request.create",
        "product_request.read_own_draft",
        "product_request.read_all",
        "nav.sales.product_requests",
    ):
        assert key in ALL_PERMISSIONS
    crm = set(ROLE_DEFAULT_PERMISSIONS["crm"])
    assert "product_request.create" in crm
    assert "product_request.read_own_draft" in crm
    assert "product_request.read_all" not in crm
    assert "nav.sales.product_requests" in crm
    holding = effective_permissions("holding_team", None)
    direktur = effective_permissions("direktur_utama", None)
    assert "product_request.read_all" in holding
    assert "product_request.create" not in holding
    assert "product_request.read_all" in direktur
    assert "product_request.create" not in direktur
    for role in ("helper", "operator", "designer", "driver"):
        perms = set(ROLE_DEFAULT_PERMISSIONS[role])
        assert "product_request.create" not in perms
        assert "product_request.read_all" not in perms


@pytest.mark.anyio
async def test_crm_draft_own_edit_and_foreign_draft_403():
    import server
    crm = _user("crm", name="Andi", override={"product_request.create": True, "product_request.read_own_draft": True})
    other = _user("crm", name="Budi", override={"product_request.create": True, "product_request.read_own_draft": True})
    await _prepare(server.db, [crm, other])
    try:
        async with _client_for(crm) as client:
            created = await client.post("/api/product-requests", json={"customer_source": "new_prospect", "customer_name": "Draft A"})
            assert created.status_code == 200, created.text
            req = created.json()["request"]
            assert req["status"] == "draft"
            assert req["request_number"].startswith("PPR-")
            assert req["created_by_id"] == crm["id"]
            assert req["created_by_snapshot"]["name"] == "Andi"
            assert req["branch_id"] == "br-sby"
            assert req["branch_snapshot"]["name"] == "Surabaya"
            assert req["customer_id"] is None

            listed = await client.get("/api/product-requests")
            assert listed.status_code == 200
            assert any(r["id"] == req["id"] for r in listed.json()["requests"])

            patched = await client.patch(f"/api/product-requests/{req['id']}", json={
                "customer_source": "new_prospect",
                "customer_name": "Draft A Updated",
                "created_by_id": "forged-user",
                "branch_id": "forged-branch",
                "created_at": "1999-01-01T00:00:00+00:00",
            })
            assert patched.status_code == 200, patched.text
            body = patched.json()["request"]
            assert body["customer_snapshot"]["name"] == "Draft A Updated"
            assert body["created_by_id"] == crm["id"]
            assert body["branch_id"] == "br-sby"
            assert not str(body["created_at"]).startswith("1999")

            got = await client.get(f"/api/product-requests/{req['id']}")
            assert got.status_code == 200
            assert got.json()["request"]["customer_snapshot"]["name"] == "Draft A Updated"

        async with _client_for(other) as client:
            denied = await client.get(f"/api/product-requests/{req['id']}")
            assert denied.status_code == 403
            denied_patch = await client.patch(f"/api/product-requests/{req['id']}", json={"customer_name": "Hack"})
            assert denied_patch.status_code == 403
            denied_submit = await client.post(f"/api/product-requests/{req['id']}/submit", json=COMPLETE_NEW)
            assert denied_submit.status_code == 403
    finally:
        app.dependency_overrides.pop(current_user, None)


@pytest.mark.anyio
async def test_four_source_combinations_do_not_create_masters_or_touch_accurate():
    import server
    crm = _user("crm", name="CRM Combo", override={"product_request.create": True, "product_request.read_own_draft": True})
    await _prepare(server.db, [crm])
    customer, product = await _seed_masters(server.db)
    customers_before = await server.db[CUSTOMERS].count_documents({})
    products_before = await server.db.master_items.count_documents({})
    stock_before = await server.db.product_stock_state.count_documents({})

    combos = [
        {
            **COMPLETE_NEW,
            "customer_source": "master_crm",
            "customer_id": customer["id"],
            "product_source": "product_master",
            "product_id": product["id"],
        },
        {
            **COMPLETE_NEW,
            "customer_source": "master_crm",
            "customer_id": customer["id"],
            "product_source": "requested_product",
            "product_id": product["id"],
        },
        {
            **COMPLETE_NEW,
            "customer_source": "new_prospect",
            "customer_id": customer["id"],
            "product_source": "product_master",
            "product_id": product["id"],
        },
        dict(COMPLETE_NEW),
    ]

    try:
        async with _client_for(crm) as client:
            receipts = []
            for payload in combos:
                created = await client.post("/api/product-requests", json=payload)
                assert created.status_code == 200, created.text
                rid = created.json()["request"]["id"]
                submitted = await client.post(f"/api/product-requests/{rid}/submit", json=payload)
                assert submitted.status_code == 200, submitted.text
                receipt = submitted.json()["receipt"]
                assert set(receipt) <= {"id", "request_number", "status", "submitted_at", "receipt_only"}
                assert receipt["status"] == "submitted"
                assert receipt["receipt_only"] is True
                assert "customer_snapshot" not in receipt
                receipts.append((rid, payload, receipt))

                crm_detail = await client.get(f"/api/product-requests/{rid}")
                assert crm_detail.status_code == 200
                assert crm_detail.json()["request"].get("receipt_only") is True
                assert "product_snapshot" not in crm_detail.json()["request"]

        stored = []
        for rid, payload, _receipt in receipts:
            doc = await server.db[COLLECTION].find_one({"id": rid}, {"_id": 0})
            stored.append(doc)
            if payload["customer_source"] == "master_crm":
                assert doc["customer_id"] == customer["id"]
                assert doc["customer_snapshot"]["name"] == customer["name"]
                assert doc["customer_snapshot"]["pic"] == customer["pic"]
                assert doc["customer_snapshot"]["city"] == customer["city"]
            else:
                assert doc["customer_id"] is None
                assert doc["customer_snapshot"]["name"] == payload["customer_name"]
            if payload["product_source"] == "product_master":
                assert doc["product_id"] == product["id"]
                assert doc["product_snapshot"]["item_code"] == product["item_code"]
                assert doc["product_snapshot"]["item_name"] == product["item_name"]
            else:
                assert doc["product_id"] is None
                assert doc["product_snapshot"]["name"] == payload["product_name"]
                assert doc["product_snapshot"]["label"] == "Requested Product"

        await server.db[CUSTOMERS].update_one({"id": customer["id"]}, {"$set": {"name": "RENAMED MASTER"}})
        await server.db.master_items.update_one({"id": product["id"]}, {"$set": {"item_name": "RENAMED PRODUCT"}})

        holding = _user("holding_team", name="Holding", override={"product_request.read_all": True, "product_request.create": False})
        await _prepare(server.db, [holding], wipe_requests=False)
        async with _client_for(holding) as client:
            listed = await client.get("/api/product-requests")
            assert listed.status_code == 200
            ids = {row["id"] for row in listed.json()["requests"]}
            for rid, payload, _receipt in receipts:
                assert rid in ids
                detail = await client.get(f"/api/product-requests/{rid}")
                assert detail.status_code == 200
                body = detail.json()["request"]
                assert body.get("receipt_only") is not True
                if payload["customer_source"] == "master_crm":
                    assert body["customer_snapshot"]["name"] == customer["name"]
                    assert body["customer_snapshot"]["name"] != "RENAMED MASTER"
                if payload["product_source"] == "product_master":
                    assert body["product_snapshot"]["item_name"] == product["item_name"]
                    assert body["product_snapshot"]["item_name"] != "RENAMED PRODUCT"

        assert await server.db[CUSTOMERS].count_documents({}) == customers_before
        assert await server.db.master_items.count_documents({}) == products_before
        assert await server.db.product_stock_state.count_documents({}) == stock_before
    finally:
        app.dependency_overrides.pop(current_user, None)


@pytest.mark.anyio
async def test_holding_direktur_and_unauthorized_rbac():
    import server
    crm = _user("crm", name="CRM One", override={"product_request.create": True, "product_request.read_own_draft": True})
    holding = _user("holding_team", name="Holding", override={"product_request.read_all": True, "product_request.create": False})
    direktur = _user("direktur_utama", name="Direktur", override={"product_request.read_all": True, "product_request.create": False})
    helper = _user("helper", name="Helper", override={"product_request.create": False, "product_request.read_all": False, "product_request.read_own_draft": False})
    await _prepare(server.db, [crm, holding, direktur, helper])
    try:
        async with _client_for(crm) as client:
            created = await client.post("/api/product-requests", json=COMPLETE_NEW)
            rid = created.json()["request"]["id"]
            submitted = await client.post(f"/api/product-requests/{rid}/submit", json=COMPLETE_NEW)
            assert submitted.status_code == 200

        for actor in (holding, direktur):
            async with _client_for(actor) as client:
                listed = await client.get("/api/product-requests")
                assert listed.status_code == 200
                assert any(r["id"] == rid for r in listed.json()["requests"])
                detail = await client.get(f"/api/product-requests/{rid}")
                assert detail.status_code == 200
                assert detail.json()["request"]["customer_snapshot"]["name"] == "Kedai Baru"
                create_denied = await client.post("/api/product-requests", json=COMPLETE_NEW)
                assert create_denied.status_code == 403

        async with _client_for(helper) as client:
            assert (await client.get("/api/product-requests")).status_code == 403
            assert (await client.get(f"/api/product-requests/{rid}")).status_code == 403
            assert (await client.post("/api/product-requests", json=COMPLETE_NEW)).status_code == 403
            assert (await client.post(f"/api/product-requests/{rid}/submit", json=COMPLETE_NEW)).status_code == 403
    finally:
        app.dependency_overrides.pop(current_user, None)


@pytest.mark.anyio
async def test_invalid_ids_and_payload_forgery_rejected():
    import server
    crm = _user("crm", name="CRM Valid", override={"product_request.create": True})
    await _prepare(server.db, [crm])
    try:
        async with _client_for(crm) as client:
            missing_customer = await client.post("/api/product-requests", json={
                "customer_source": "master_crm",
                "customer_id": "does-not-exist",
            })
            assert missing_customer.status_code == 400

            missing_product = await client.post("/api/product-requests", json={
                "product_source": "product_master",
                "product_id": "does-not-exist",
            })
            assert missing_product.status_code == 400

            created = await client.post("/api/product-requests", json={"customer_source": "new_prospect"})
            rid = created.json()["request"]["id"]
            bad_submit = await client.post(f"/api/product-requests/{rid}/submit", json={
                "customer_source": "new_prospect",
                "product_source": "requested_product",
                "potential_qty": 0,
                "frequency": "Bulanan",
                "target_price": -1,
                "needed_date": "31-12-2026",
            })
            assert bad_submit.status_code == 400
    finally:
        app.dependency_overrides.pop(current_user, None)


@pytest.mark.anyio
async def test_crm_can_search_existing_customer_and_product_apis():
    import server
    crm = _user("crm", name="CRM Search", override={"product_request.create": True})
    await _prepare(server.db, [crm])
    customer, product = await _seed_masters(server.db)
    try:
        async with _client_for(crm) as client:
            customers = await client.get("/api/product-requests/customers", params={"q": customer["name"][:8]})
            assert customers.status_code == 200, customers.text
            assert any(c["id"] == customer["id"] for c in customers.json()["customers"])
            assert (await client.get("/api/sales-customers")).status_code == 403
            assert (await client.post("/api/sales-customers", json={"name": "X", "phone": "0812", "address": "A"})).status_code == 403

            items = await client.get("/api/master-items", params={"q": product["item_name"][:8], "picker": "true"})
            assert items.status_code == 200, items.text
            assert any(i["id"] == product["id"] for i in items.json()["items"])

            full_catalog = await client.get("/api/master-items")
            assert full_catalog.status_code == 403
    finally:
        app.dependency_overrides.pop(current_user, None)
