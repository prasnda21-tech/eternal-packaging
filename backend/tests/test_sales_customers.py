"""Targeted tests for Sales Customer Master (Data Pelanggan) and PO integration.

Covers:
- Customer name normalization (whitespace, lowercase, collapsing spaces)
- Manual customer CRUD (validation, required fields, update, duplicate prevention)
- Duplicate rejection on duplicate name (exact and normalized match)
- PO creation with existing customer: reuses ID, avoids duplicate
- PO creation with new customer: automatically persists customer into master
- Subsequent PO with same customer: reuses previously auto-created customer
- Options endpoint returns master_customers and includes them in customers list
- Permission enforcement on /sales-customers endpoints
"""
import os
import sys
import uuid
import pytest
from pathlib import Path

# Add backend directory to sys.path
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
    COLLECTION,
)


@pytest.fixture
def anyio_backend():
    return 'asyncio'


@pytest.fixture(autouse=True)
def bind_motor():
    import server
    from motor.motor_asyncio import AsyncIOMotorClient
    server.client = AsyncIOMotorClient(os.environ['MONGO_URL'])
    server.db = server.client[os.environ['DB_NAME']]


def test_customer_name_normalization():
    assert normalize_customer_name("  PT   Eternal   Packaging  ") == "pt eternal packaging"
    assert normalize_customer_name("pt eternal packaging") == "pt eternal packaging"
    assert normalize_customer_name("PT ETERNAL PACKAGING") == "pt eternal packaging"
    assert normalize_customer_name("") == ""
    assert normalize_customer_name("   ") == ""


@pytest.mark.anyio
async def test_manual_customer_crud_and_duplicate_prevention():
    import server
    await ensure_sales_customer_indexes(server.db)

    app.dependency_overrides[current_user] = lambda: {
        "email": "admin@eternalpackaging.com",
        "name": "Admin",
        "role": "admin",
    }
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            # 1. Validation check: missing required fields
            r_err = await client.post("/api/sales-customers", json={
                "name": "",
                "phone": "0812345678",
                "address": "Jl. Merdeka No. 1",
            })
            assert r_err.status_code == 400

            r_err2 = await client.post("/api/sales-customers", json={
                "name": "PT Customer Test",
                "phone": "",
                "address": "Jl. Merdeka No. 1",
            })
            assert r_err2.status_code == 400

            r_err3 = await client.post("/api/sales-customers", json={
                "name": "PT Customer Test",
                "phone": "0812345678",
                "address": "",
            })
            assert r_err3.status_code == 400

            # 2. Successful creation
            unique_suffix = uuid.uuid4().hex[:6]
            cust_name = f"PT Pelanggan Sejahtera {unique_suffix}"
            r_create = await client.post("/api/sales-customers", json={
                "name": cust_name,
                "phone": "08123456789",
                "address": "Jl. Kawasan Industri 1",
                "pic": "Pak Hendra",
                "email": "hendra@sejahtera.com",
                "city": "Bekasi",
                "notes": "Pelanggan prioritas",
            })
            assert r_create.status_code == 200, r_create.text
            created = r_create.json()["customer"]
            assert created["name"] == cust_name
            assert created["normalized_name"] == normalize_customer_name(cust_name)
            assert created["phone"] == "08123456789"
            assert created["pic"] == "Pak Hendra"
            cust_id = created["id"]

            # 3. Duplicate prevention: exact same name
            r_dup1 = await client.post("/api/sales-customers", json={
                "name": cust_name,
                "phone": "0899999999",
                "address": "Jl. Lain",
            })
            assert r_dup1.status_code == 409

            # 4. Duplicate prevention: varied casing and extra spacing
            r_dup2 = await client.post("/api/sales-customers", json={
                "name": f"  pt   pelanggan   sejahtera   {unique_suffix.upper()}  ",
                "phone": "0899999999",
                "address": "Jl. Lain",
            })
            assert r_dup2.status_code == 409

            # 5. Get customer by ID
            r_get = await client.get(f"/api/sales-customers/{cust_id}")
            assert r_get.status_code == 200
            assert r_get.json()["customer"]["id"] == cust_id

            # 6. List customers with search
            r_list = await client.get(f"/api/sales-customers?q={unique_suffix}")
            assert r_list.status_code == 200
            found = [c for c in r_list.json()["customers"] if c["id"] == cust_id]
            assert len(found) == 1

            # 7. Update customer
            r_update = await client.put(f"/api/sales-customers/{cust_id}", json={
                "name": f"{cust_name} Updated",
                "phone": "081299998888",
                "address": "Jl. Kawasan Baru No. 2",
                "pic": "Pak Hendra Baru",
                "email": "hendra_new@sejahtera.com",
                "city": "Cikarang",
                "notes": "Catatan baru",
            })
            assert r_update.status_code == 200
            updated = r_update.json()["customer"]
            assert updated["name"] == f"{cust_name} Updated"
            assert updated["phone"] == "081299998888"
            assert updated["city"] == "Cikarang"
    finally:
        app.dependency_overrides.pop(current_user, None)


@pytest.mark.anyio
async def test_po_creation_auto_persists_new_customer_and_prevents_duplicate():
    app.dependency_overrides[current_user] = lambda: {
        "email": "admin@eternalpackaging.com",
        "name": "Admin",
        "role": "admin",
    }
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            unique = uuid.uuid4().hex[:6]
            new_cust_name = f"PT Auto Customer {unique}"

            # Verify customer does NOT exist yet in master
            r_check_before = await client.get(f"/api/sales-customers?q={unique}")
            assert len(r_check_before.json()["customers"]) == 0

            # 1. Create PO with new customer
            po_num_1 = f"PO-CUST-TEST-1-{unique.upper()}"
            po_payload_1 = {
                "po_number": po_num_1,
                "customer": new_cust_name,
                "customer_phone": "08551234567",
                "customer_address": "Jl. Industri Auto No. 1",
                "customer_pic": "Pak Anton",
                "customer_email": "anton@autocust.com",
                "customer_city": "Tangerang",
                "line_items": [{"name": "Box Custom", "quantity": 500}],
            }
            r_po1 = await client.post("/api/sales-orders", json=po_payload_1)
            assert r_po1.status_code == 200, r_po1.text
            order1 = r_po1.json()["order"]
            cust1_id = order1.get("customer_id")
            assert cust1_id is not None
            assert order1["customer"] == new_cust_name

            # Verify customer NOW exists in sales_customers
            r_check_after = await client.get(f"/api/sales-customers?q={unique}")
            custs = r_check_after.json()["customers"]
            assert len(custs) == 1
            master_cust = custs[0]
            assert master_cust["id"] == cust1_id
            assert master_cust["name"] == new_cust_name
            assert master_cust["phone"] == "08551234567"
            assert master_cust["address"] == "Jl. Industri Auto No. 1"
            assert master_cust["pic"] == "Pak Anton"

            # 2. Create second PO with same customer name (different spacing/casing)
            po_num_2 = f"PO-CUST-TEST-2-{unique.upper()}"
            po_payload_2 = {
                "po_number": po_num_2,
                "customer": f"  pt   auto   customer   {unique.upper()}  ",
                "line_items": [{"name": "Box Custom 2", "quantity": 1000}],
            }
            r_po2 = await client.post("/api/sales-orders", json=po_payload_2)
            assert r_po2.status_code == 200, r_po2.text
            order2 = r_po2.json()["order"]
            # Must reuse existing customer_id without creating duplicate!
            assert order2["customer_id"] == cust1_id

            # Verify customer count in master is still 1
            r_check_after_2 = await client.get(f"/api/sales-customers?q={unique}")
            assert len(r_check_after_2.json()["customers"]) == 1

            # 3. Verify options endpoint returns master_customers and includes customer
            r_options = await client.get("/api/sales-orders/options")
            assert r_options.status_code == 200
            opt_data = r_options.json()
            assert "master_customers" in opt_data
            assert any(c["id"] == cust1_id for c in opt_data["master_customers"])
            assert any(new_cust_name in c for c in opt_data["customers"])
    finally:
        app.dependency_overrides.pop(current_user, None)


def test_functional_permission_does_not_derive_sidebar_nav():
    from permissions import derived_nav_permissions, effective_permissions

    # 1. derived_nav_permissions must not auto-derive nav.sales.customers
    derived = derived_nav_permissions({"sales.customer.view", "sales.customer.manage"})
    assert "nav.sales.customers" not in derived

    # 2. User has sales.customer.view but does NOT have nav.sales.customers
    # -> functional permission exists
    # -> sidebar nav permission is still absent
    perms_view = effective_permissions("crm", override={"sales.customer.view": True})
    assert "sales.customer.view" in perms_view
    assert "nav.sales.customers" not in perms_view

    perms_manage = effective_permissions("crm", override={"sales.customer.manage": True})
    assert "sales.customer.manage" in perms_manage
    assert "nav.sales.customers" not in perms_manage


@pytest.mark.anyio
async def test_invalid_po_before_persistence_does_not_create_customer():
    import server
    await ensure_sales_customer_indexes(server.db)

    app.dependency_overrides[current_user] = lambda: {
        "email": "admin@eternalpackaging.com",
        "name": "Admin",
        "role": "admin",
    }
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            unique = uuid.uuid4().hex[:6]
            new_cust_name = f"PT Invalid PO Cust {unique}"
            norm = normalize_customer_name(new_cust_name)

            # Invalid PO: line item with quantity 0
            r = await client.post("/api/sales-orders", json={
                "po_number": f"PO-INVALID-{unique.upper()}",
                "customer": new_cust_name,
                "customer_phone": "0812345678",
                "customer_address": "Jl. Test",
                "line_items": [{"name": "Invalid Item", "quantity": 0}],
            })
            assert r.status_code == 400

            # PO not created
            po = await server.db.orders.find_one({"po_number": f"PO-INVALID-{unique.upper()}"})
            assert po is None

            # Customer not created
            cust = await server.db[COLLECTION].find_one({"normalized_name": norm})
            assert cust is None
    finally:
        app.dependency_overrides.pop(current_user, None)


@pytest.mark.anyio
async def test_customer_persistence_failure_rolls_back_exact_po(monkeypatch):
    import server
    await ensure_sales_customer_indexes(server.db)

    app.dependency_overrides[current_user] = lambda: {
        "email": "admin@eternalpackaging.com",
        "name": "Admin",
        "role": "admin",
    }
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            unique = uuid.uuid4().hex[:6]
            unrelated_order_id = str(uuid.uuid4())
            unrelated_po = f"PO-UNRELATED-{unique.upper()}"

            # Insert an unrelated order that must NOT be touched
            await server.db.orders.insert_one({
                "id": unrelated_order_id,
                "po_number": unrelated_po,
                "customer": "Unrelated Customer",
                "line_items": [{"name": "Box", "quantity": 1}],
            })

            new_cust_name = f"PT Customer Failure {unique}"
            norm = normalize_customer_name(new_cust_name)

            # Mock sales_customers insert_one to fail operationally
            from motor.motor_asyncio import AsyncIOMotorCollection
            real_insert = AsyncIOMotorCollection.insert_one

            async def failing_customer_insert(self, doc, *args, **kwargs):
                if self.name == "sales_customers" and doc.get("normalized_name") == norm:
                    from pymongo.errors import OperationFailure
                    raise OperationFailure("Simulated operational failure inserting customer")
                return await real_insert(self, doc, *args, **kwargs)

            monkeypatch.setattr(AsyncIOMotorCollection, "insert_one", failing_customer_insert)

            target_po = f"PO-FAIL-CUST-{unique.upper()}"
            with pytest.raises(Exception):
                await client.post("/api/sales-orders", json={
                    "po_number": target_po,
                    "customer": new_cust_name,
                    "customer_phone": "0812345678",
                    "customer_address": "Jl. Cust Fail",
                    "line_items": [{"name": "Box Fail", "quantity": 10}],
                })

            # Exact task-created PO is rolled back
            created_po = await server.db.orders.find_one({"po_number": target_po})
            assert created_po is None, "Exact PO must be rolled back on customer persistence failure"

            # Unrelated order is untouched
            unrelated = await server.db.orders.find_one({"id": unrelated_order_id})
            assert unrelated is not None, "Unrelated orders must remain untouched"
            assert unrelated["po_number"] == unrelated_po

            # No customer left in master
            cust = await server.db[COLLECTION].find_one({"normalized_name": norm})
            assert cust is None, "Customer master must not exist"
    finally:
        app.dependency_overrides.pop(current_user, None)


@pytest.mark.anyio
async def test_duplicate_normalized_customer_race_reuses_winner(monkeypatch):
    import server
    await ensure_sales_customer_indexes(server.db)

    app.dependency_overrides[current_user] = lambda: {
        "email": "admin@eternalpackaging.com",
        "name": "Admin",
        "role": "admin",
    }
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            unique = uuid.uuid4().hex[:6]
            race_cust_name = f"PT Race Winner {unique}"
            norm = normalize_customer_name(race_cust_name)

            winner_id = str(uuid.uuid4())
            winner_doc = {
                "id": winner_id,
                "name": race_cust_name,
                "normalized_name": norm,
                "phone": "0811223344",
                "address": "Jl. Race Winner",
            }

            from motor.motor_asyncio import AsyncIOMotorCollection
            real_insert = AsyncIOMotorCollection.insert_one
            race_triggered = [False]

            async def race_simulating_insert(self, doc, *args, **kwargs):
                if self.name == "sales_customers" and doc.get("normalized_name") == norm:
                    # Winner got inserted concurrently by another request
                    await real_insert(self, winner_doc)
                    race_triggered[0] = True
                    from pymongo.errors import DuplicateKeyError
                    raise DuplicateKeyError(f"E11000 duplicate key error: normalized_name '{norm}'")
                return await real_insert(self, doc, *args, **kwargs)

            monkeypatch.setattr(AsyncIOMotorCollection, "insert_one", race_simulating_insert)

            po_num = f"PO-RACE-{unique.upper()}"
            r = await client.post("/api/sales-orders", json={
                "po_number": po_num,
                "customer": race_cust_name,
                "customer_phone": "089999",
                "customer_address": "Jl. Race",
                "line_items": [{"name": "Item A", "quantity": 10}],
            })
            assert r.status_code == 200, r.text
            assert race_triggered[0] is True

            # Winner is reused
            order_in_db = await server.db.orders.find_one({"po_number": po_num})
            assert order_in_db is not None
            assert order_in_db["customer_id"] == winner_id

            # Only 1 customer exists in sales_customers
            cust_count = await server.db[COLLECTION].count_documents({"normalized_name": norm})
            assert cust_count == 1
    finally:
        app.dependency_overrides.pop(current_user, None)


@pytest.mark.anyio
async def test_existing_customer_po_failure_preserves_customer():
    import server
    await ensure_sales_customer_indexes(server.db)

    app.dependency_overrides[current_user] = lambda: {
        "email": "admin@eternalpackaging.com",
        "name": "Admin",
        "role": "admin",
    }
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            unique = uuid.uuid4().hex[:6]
            existing_name = f"PT PreExisting Cust {unique}"
            norm = normalize_customer_name(existing_name)

            # 1. Pre-create customer in sales_customers
            ins = await server.db[COLLECTION].insert_one({
                "id": str(uuid.uuid4()),
                "name": existing_name,
                "normalized_name": norm,
                "phone": "081234567",
                "address": "Jl. Existing",
            })

            # 2. Insert an existing PO with a specific po_number to cause duplicate 409
            conflict_po_number = f"PO-CONFLICT-{unique.upper()}"
            await server.db.orders.insert_one({
                "id": str(uuid.uuid4()),
                "po_number": conflict_po_number,
                "customer": "Someone Else",
                "line_items": [{"name": "Box", "quantity": 1}],
            })

            # 3. Attempt to create PO with same duplicate po_number using existing customer
            r = await client.post("/api/sales-orders", json={
                "po_number": conflict_po_number,
                "customer": existing_name,
                "line_items": [{"name": "Box", "quantity": 10}],
            })
            assert r.status_code == 409

            # 4. Existing customer must NOT be deleted
            still_there = await server.db[COLLECTION].find_one({"normalized_name": norm})
            assert still_there is not None, "Existing customer must never be deleted on PO failure"
    finally:
        app.dependency_overrides.pop(current_user, None)


@pytest.mark.anyio
async def test_non_admin_cannot_access_sales_customers_by_default():
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        # Non-admin roles (head_production, head_operations, crm) must be 403 Forbidden by default
        for role in ["head_production", "head_operations", "crm", "operator"]:
            app.dependency_overrides[current_user] = (lambda r: lambda: {
                "email": f"{r}@eternalpackaging.com",
                "name": r.title(),
                "role": r,
            })(role)
            try:
                r_get = await client.get("/api/sales-customers")
                assert r_get.status_code == 403, f"Role {role} should not have sales.customer.view by default"

                r_post = await client.post("/api/sales-customers", json={
                    "name": "Test Cust",
                    "phone": "0812",
                    "address": "Jl. Test",
                })
                assert r_post.status_code == 403, f"Role {role} should not have sales.customer.manage by default"
            finally:
                app.dependency_overrides.pop(current_user, None)


@pytest.mark.anyio
async def test_ensure_sales_customer_indexes_raises_on_failure(monkeypatch):
    from pymongo.errors import OperationFailure

    class MockCollection:
        async def create_index(self, *args, **kwargs):
            raise OperationFailure("Mock index build failed")

    class MockDB(dict):
        def __getitem__(self, item):
            return MockCollection()

    with pytest.raises(RuntimeError) as exc_info:
        await ensure_sales_customer_indexes(MockDB())
    assert "Failed to ensure unique index" in str(exc_info.value)
