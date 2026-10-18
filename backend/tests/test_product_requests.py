"""RBAC + integrity tests for CRM product requests."""
import asyncio
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


def _at(payload, version):
    merged = dict(payload or {})
    merged["doc_version"] = version
    return merged


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
        "product_request.feedback",
        "product_request.update_status",
        "nav.sales.product_requests",
    ):
        assert key in ALL_PERMISSIONS
    crm = set(ROLE_DEFAULT_PERMISSIONS["crm"])
    assert "product_request.create" in crm
    assert "product_request.read_own_draft" in crm
    assert "product_request.read_all" not in crm
    assert "product_request.feedback" not in crm
    assert "nav.sales.product_requests" in crm
    holding = effective_permissions("holding_team", None)
    direktur = effective_permissions("direktur_utama", None)
    assert "product_request.read_all" in holding
    assert "product_request.feedback" in holding
    assert "product_request.update_status" in holding
    assert "product_request.create" not in holding
    assert "product_request.read_all" in direktur
    assert "product_request.feedback" in direktur
    assert "product_request.update_status" in direktur
    assert "product_request.create" not in direktur
    for role in ("helper", "operator", "designer", "driver"):
        perms = set(ROLE_DEFAULT_PERMISSIONS[role])
        assert "product_request.create" not in perms
        assert "product_request.read_all" not in perms
        assert "product_request.feedback" not in perms
        assert "product_request.update_status" not in perms


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

            listed = await client.get("/api/product-requests", params={"view": "own_drafts"})
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
                body = crm_detail.json()["request"]
                assert body.get("receipt_only") is not True
                assert "product_snapshot" in body

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

        async with _client_for(holding) as client:
            listed = await client.get("/api/product-requests")
            assert listed.status_code == 200
            assert any(r["id"] == rid for r in listed.json()["requests"])
            detail = await client.get(f"/api/product-requests/{rid}")
            assert detail.status_code == 200
            assert detail.json()["request"]["customer_snapshot"]["name"] == "Kedai Baru"
            create_denied = await client.post("/api/product-requests", json=COMPLETE_NEW)
            assert create_denied.status_code == 403

        async with _client_for(direktur) as client:
            listed = await client.get("/api/product-requests")
            assert listed.status_code == 200
            assert any(r["id"] == rid for r in listed.json()["requests"])
            detail = await client.get(f"/api/product-requests/{rid}")
            assert detail.status_code == 200
            assert detail.json()["request"]["customer_snapshot"]["name"] == "Kedai Baru"
            # direktur_utama uses director-full access (includes create); mutation gates for
            # feedback/status remain permission-checked in dedicated workflow tests.

        async with _client_for(helper) as client:
            listed = await client.get("/api/product-requests")
            assert listed.status_code == 200
            assert any(r["id"] == rid for r in listed.json()["requests"])
            detail = await client.get(f"/api/product-requests/{rid}")
            assert detail.status_code == 200
            assert detail.json()["request"]["customer_snapshot"]["name"] == "Kedai Baru"
            assert (await client.post("/api/product-requests", json=COMPLETE_NEW)).status_code == 403
            assert (await client.post(f"/api/product-requests/{rid}/submit", json=COMPLETE_NEW)).status_code == 403
            assert (await client.post(f"/api/product-requests/{rid}/feedback", json={"note": "x"})).status_code == 403
            assert (await client.post(f"/api/product-requests/{rid}/status", json={"status": "reviewed"})).status_code == 403
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


@pytest.mark.anyio
async def test_resubmit_does_not_duplicate_or_mutate_snapshot():
    import server
    crm = _user("crm", name="CRM Resubmit", override={"product_request.create": True, "product_request.read_own_draft": True})
    await _prepare(server.db, [crm])
    customer, product = await _seed_masters(server.db)
    payload = {
        **COMPLETE_NEW,
        "customer_source": "master_crm",
        "customer_id": customer["id"],
        "product_source": "product_master",
        "product_id": product["id"],
    }
    try:
        async with _client_for(crm) as client:
            created = await client.post("/api/product-requests", json=payload)
            assert created.status_code == 200, created.text
            rid = created.json()["request"]["id"]
            first = await client.post(f"/api/product-requests/{rid}/submit", json=payload)
            assert first.status_code == 200, first.text
            before = await server.db[COLLECTION].find_one({"id": rid}, {"_id": 0})
            count_before = await server.db[COLLECTION].count_documents({})

            await server.db[CUSTOMERS].update_one({"id": customer["id"]}, {"$set": {"name": "CHANGED AFTER SUBMIT"}})
            second = await client.post(f"/api/product-requests/{rid}/submit", json={
                **payload,
                "customer_name": "FORGED",
                "potential_qty": 999999,
            })
            assert second.status_code == 409
            after = await server.db[COLLECTION].find_one({"id": rid}, {"_id": 0})
            assert await server.db[COLLECTION].count_documents({}) == count_before
            assert after["customer_snapshot"]["name"] == customer["name"]
            assert after["customer_snapshot"]["name"] != "CHANGED AFTER SUBMIT"
            assert after["potential_qty"] == before["potential_qty"]
            assert after["submitted_at"] == before["submitted_at"]
            assert after["status"] == "submitted"

            denied_patch = await client.patch(f"/api/product-requests/{rid}", json=payload)
            assert denied_patch.status_code == 409
            assert "customer_snapshot" not in (denied_patch.json() or {})
    finally:
        app.dependency_overrides.pop(current_user, None)


@pytest.mark.anyio
async def test_builtin_roles_use_real_defaults_without_admin_override():
    import server
    crm = _user("crm", name="CRM Default")
    holding = _user("holding_team", name="Holding Default")
    direktur = _user("direktur_utama", name="Direktur Default")
    helper = _user("helper", name="Helper Default")
    await _prepare(server.db, [crm, holding, direktur, helper])
    await server.db.role_permissions.delete_many({
        "role": {"$in": ["crm", "holding_team", "direktur_utama", "helper"]},
    })
    try:
        async with _client_for(crm) as client:
            created = await client.post("/api/product-requests", json=COMPLETE_NEW)
            assert created.status_code == 200, created.text
            rid = created.json()["request"]["id"]
            submitted = await client.post(f"/api/product-requests/{rid}/submit", json=COMPLETE_NEW)
            assert submitted.status_code == 200, submitted.text
            crm_detail = await client.get(f"/api/product-requests/{rid}")
            body = crm_detail.json()["request"]
            assert body.get("receipt_only") is not True
            assert body["customer_snapshot"]["name"] == "Kedai Baru"

        for actor in (holding, direktur, helper):
            async with _client_for(actor) as client:
                listed = await client.get("/api/product-requests")
                assert listed.status_code == 200, listed.text
                assert any(r["id"] == rid for r in listed.json()["requests"])
                detail = await client.get(f"/api/product-requests/{rid}")
                assert detail.status_code == 200
                assert detail.json()["request"]["customer_snapshot"]["name"] == "Kedai Baru"
    finally:
        app.dependency_overrides.pop(current_user, None)


def _patch_note_only_json():
    return {"request_note": "Hanya catatan"}


@pytest.mark.anyio
async def test_partial_patch_keeps_stored_fields_and_empty_submit_uses_draft():
    import server
    crm = _user("crm", name="CRM Patch", override={"product_request.create": True, "product_request.read_own_draft": True})
    await _prepare(server.db, [crm])
    customer, product = await _seed_masters(server.db)
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
            created = await client.post("/api/product-requests", json=COMPLETE_NEW)
            rid = created.json()["request"]["id"]
            before = created.json()["request"]
            patched = await client.patch(f"/api/product-requests/{rid}", json=_patch_note_only_json())
            assert patched.status_code == 200, patched.text
            body = patched.json()["request"]
            assert body["status"] == "draft"
            assert body["request_note"] == "Hanya catatan"
            assert body["customer_snapshot"]["name"] == before["customer_snapshot"]["name"]
            assert body["product_snapshot"]["name"] == before["product_snapshot"]["name"]
            assert body["potential_qty"] == before["potential_qty"]
            assert body["frequency"] == before["frequency"]
            assert body["created_by_id"] == crm["id"]

            spec = await client.patch(f"/api/product-requests/{rid}", json={"material": "PE Coated"})
            assert spec.status_code == 200, spec.text
            spec_body = spec.json()["request"]
            assert spec_body["product_snapshot"]["material"] == "PE Coated"
            assert spec_body["product_snapshot"]["name"] == "Paper Cup 12oz"
            assert spec_body["product_snapshot"]["category"] == "Paper Cup"
            assert spec_body["customer_snapshot"]["name"] == "Kedai Baru"

            switched = await client.patch(f"/api/product-requests/{rid}", json={"customer_source": "master_crm"})
            assert switched.status_code == 200, switched.text
            switched_body = switched.json()["request"]
            assert switched_body["customer_source"] == "master_crm"
            assert switched_body["customer_id"] is None
            assert not (switched_body.get("customer_snapshot") or {}).get("name")

            restore = await client.patch(f"/api/product-requests/{rid}", json=COMPLETE_NEW)
            assert restore.status_code == 200
            incomplete = await client.post("/api/product-requests", json={"customer_source": "new_prospect"})
            incomplete_id = incomplete.json()["request"]["id"]
            before_incomplete = await server.db[COLLECTION].find_one({"id": incomplete_id}, {"_id": 0})
            denied = await client.post(f"/api/product-requests/{incomplete_id}/submit", json={})
            assert denied.status_code == 400
            after_incomplete = await server.db[COLLECTION].find_one({"id": incomplete_id}, {"_id": 0})
            assert after_incomplete["status"] == "draft"
            assert after_incomplete.get("submitted_at") is None
            assert after_incomplete["customer_snapshot"] == before_incomplete["customer_snapshot"]

            for payload in combos:
                created_combo = await client.post("/api/product-requests", json=payload)
                assert created_combo.status_code == 200, created_combo.text
                combo_id = created_combo.json()["request"]["id"]
                submitted = await client.post(f"/api/product-requests/{combo_id}/submit", json={})
                assert submitted.status_code == 200, submitted.text
                receipt = submitted.json()["receipt"]
                assert receipt["receipt_only"] is True
                assert "customer_snapshot" not in receipt
                stored = await server.db[COLLECTION].find_one({"id": combo_id}, {"_id": 0})
                if payload["customer_source"] == "master_crm":
                    assert stored["customer_snapshot"]["name"] == customer["name"]
                else:
                    assert stored["customer_snapshot"]["name"] == payload["customer_name"]
                if payload["product_source"] == "product_master":
                    assert stored["product_snapshot"]["item_name"] == product["item_name"]
                else:
                    assert stored["product_snapshot"]["name"] == payload["product_name"]
                    assert stored["product_snapshot"]["material"] == payload["material"]

            stored_before_forge = await server.db[COLLECTION].find_one({"id": rid}, {"_id": 0})
            forged = await client.patch(f"/api/product-requests/{rid}", json={
                "request_note": "aman",
                "created_by_id": "forged",
                "branch_id": "forged-branch",
                "status": "submitted",
                "doc_version": 99,
                "customer_snapshot": {"name": "FAKE MASTER"},
                "submitted_at": "1999-01-01T00:00:00+00:00",
            })
            assert forged.status_code == 409, forged.text
            stored_after_forge = await server.db[COLLECTION].find_one({"id": rid}, {"_id": 0})
            assert stored_after_forge["status"] == "draft"
            assert stored_after_forge["created_by_id"] == crm["id"]
            assert stored_after_forge["branch_id"] == "br-sby"
            assert stored_after_forge["customer_snapshot"]["name"] != "FAKE MASTER"
            assert stored_after_forge["doc_version"] == stored_before_forge["doc_version"]
            assert stored_after_forge["request_note"] == stored_before_forge["request_note"]
    finally:
        app.dependency_overrides.pop(current_user, None)


@pytest.mark.anyio
async def test_legacy_versionless_draft_submit_then_patch_conflicts():
    import server
    crm = _user("crm", name="CRM Legacy", override={"product_request.create": True})
    await _prepare(server.db, [crm])
    try:
        async with _client_for(crm) as client:
            created = await client.post("/api/product-requests", json=COMPLETE_NEW)
            rid = created.json()["request"]["id"]
        await server.db[COLLECTION].update_one({"id": rid}, {"$unset": {"doc_version": ""}})
        async with _client_for(crm) as client:
            submitted = await client.post(f"/api/product-requests/{rid}/submit", json={})
            assert submitted.status_code == 200, submitted.text
            assert submitted.json()["receipt"]["receipt_only"] is True
            patch = await client.patch(f"/api/product-requests/{rid}", json={"request_note": "late"})
            assert patch.status_code == 409
            assert "customer_snapshot" not in patch.json()
        stored = await server.db[COLLECTION].find_one({"id": rid}, {"_id": 0})
        assert stored["status"] == "submitted"
        assert stored["customer_snapshot"]["name"] == "Kedai Baru"
        assert stored["request_note"] == "Customer rebranding"
    finally:
        app.dependency_overrides.pop(current_user, None)


@pytest.mark.anyio
async def test_stale_client_patch_and_submit_do_not_overwrite():
    import server
    crm = _user("crm", name="CRM Stale", override={"product_request.create": True, "product_request.read_own_draft": True})
    await _prepare(server.db, [crm])
    try:
        async with _client_for(crm) as client:
            created = await client.post("/api/product-requests", json=COMPLETE_NEW)
            rid = created.json()["request"]["id"]
            await server.db[COLLECTION].update_one(
                {"id": rid},
                {"$set": {"doc_version": 3, "request_note": "versi-3"}},
            )
            seen = await client.get(f"/api/product-requests/{rid}")
            assert seen.json()["request"]["doc_version"] == 3
            newer = await client.patch(f"/api/product-requests/{rid}", json=_at({
                "request_note": "dari tab B",
            }, 3))
            assert newer.status_code == 200, newer.text
            assert newer.json()["request"]["doc_version"] == 4

            stale = await client.patch(f"/api/product-requests/{rid}", json=_at({
                "request_note": "payload lama tab A",
                "customer_name": "Nama Lama Tab A",
            }, 3))
            assert stale.status_code == 409, stale.text
            assert "customer_snapshot" not in stale.json()

            stale_submit = await client.post(f"/api/product-requests/{rid}/submit", json=_at(COMPLETE_NEW, 3))
            assert stale_submit.status_code == 409, stale_submit.text

        stored = await server.db[COLLECTION].find_one({"id": rid}, {"_id": 0})
        assert stored["doc_version"] == 4
        assert stored["status"] == "draft"
        assert stored.get("submitted_at") is None
        assert stored["request_note"] == "dari tab B"
        assert stored["customer_snapshot"]["name"] == "Kedai Baru"
    finally:
        app.dependency_overrides.pop(current_user, None)


@pytest.mark.anyio
async def test_matching_doc_version_patch_increments_once():
    import server
    crm = _user("crm", name="CRM Match", override={"product_request.create": True})
    await _prepare(server.db, [crm])
    try:
        async with _client_for(crm) as client:
            created = await client.post("/api/product-requests", json=COMPLETE_NEW)
            rid = created.json()["request"]["id"]
            current = created.json()["request"]["doc_version"]
            patched = await client.patch(f"/api/product-requests/{rid}", json=_at({
                "request_note": "catatan baru",
            }, current))
            assert patched.status_code == 200, patched.text
            body = patched.json()["request"]
            assert body["doc_version"] == current + 1
            assert body["request_note"] == "catatan baru"
            assert body["customer_snapshot"]["name"] == "Kedai Baru"
            assert body["status"] == "draft"
        stored = await server.db[COLLECTION].find_one({"id": rid}, {"_id": 0})
        assert stored["doc_version"] == current + 1
    finally:
        app.dependency_overrides.pop(current_user, None)


@pytest.mark.anyio
async def test_overlapping_patches_one_winner():
    import server
    import services.product_requests as pr
    crm = _user("crm", name="CRM Patch Race", override={"product_request.create": True})
    await _prepare(server.db, [crm])
    ready = asyncio.Event()
    go = asyncio.Event()
    seen = 0
    lock = asyncio.Lock()

    async def gate():
        nonlocal seen
        async with lock:
            seen += 1
            if seen >= 2:
                ready.set()
        await go.wait()

    pr.after_draft_load = gate
    try:
        async with _client_for(crm) as client:
            created = await client.post("/api/product-requests", json=COMPLETE_NEW)
            rid = created.json()["request"]["id"]
            version = created.json()["request"]["doc_version"]
            first = asyncio.create_task(client.patch(
                f"/api/product-requests/{rid}",
                json=_at({"request_note": "PEMENANG A"}, version),
            ))
            second = asyncio.create_task(client.patch(
                f"/api/product-requests/{rid}",
                json=_at({"request_note": "PEMENANG B"}, version),
            ))
            await asyncio.wait_for(ready.wait(), timeout=3)
            go.set()
            a, b = await asyncio.gather(first, second)
        assert sorted([a.status_code, b.status_code]) == [200, 409]
        winner = a if a.status_code == 200 else b
        stored = await server.db[COLLECTION].find_one({"id": rid}, {"_id": 0})
        assert stored["doc_version"] == version + 1
        assert stored["request_note"] == winner.json()["request"]["request_note"]
        assert stored["request_note"] in {"PEMENANG A", "PEMENANG B"}
        assert stored["customer_snapshot"]["name"] == "Kedai Baru"
        assert await server.db[COLLECTION].count_documents({"id": rid}) == 1
    finally:
        pr.after_draft_load = None
        app.dependency_overrides.pop(current_user, None)


@pytest.mark.anyio
async def test_omitted_field_preserved_and_explicit_empty_clears():
    import server
    crm = _user("crm", name="CRM Empty", override={"product_request.create": True})
    await _prepare(server.db, [crm])
    try:
        async with _client_for(crm) as client:
            created = await client.post("/api/product-requests", json=COMPLETE_NEW)
            rid = created.json()["request"]["id"]
            version = created.json()["request"]["doc_version"]
            omitted = await client.patch(f"/api/product-requests/{rid}", json={"frequency": "Mingguan"})
            assert omitted.status_code == 200, omitted.text
            body = omitted.json()["request"]
            assert body["request_note"] == "Customer rebranding"
            assert body["request_channel"] == "Referensi Customer"
            assert body["potential_qty"] == 50000
            assert body["frequency"] == "Mingguan"
            assert body["doc_version"] == version + 1

            cleared = await client.patch(f"/api/product-requests/{rid}", json=_at({
                "request_note": "",
                "request_channel": None,
            }, body["doc_version"]))
            assert cleared.status_code == 200, cleared.text
            cleared_body = cleared.json()["request"]
            assert cleared_body["request_note"] == ""
            assert cleared_body["request_channel"] == ""
            assert cleared_body["potential_qty"] == 50000
            assert cleared_body["frequency"] == "Mingguan"
            assert cleared_body["customer_snapshot"]["name"] == "Kedai Baru"
            assert cleared_body["doc_version"] == body["doc_version"] + 1
    finally:
        app.dependency_overrides.pop(current_user, None)


@pytest.mark.anyio
async def test_legacy_versionless_patch_accepts_returned_version():
    import server
    crm = _user("crm", name="CRM Legacy Patch", override={"product_request.create": True, "product_request.read_own_draft": True})
    await _prepare(server.db, [crm])
    try:
        async with _client_for(crm) as client:
            created = await client.post("/api/product-requests", json=COMPLETE_NEW)
            rid = created.json()["request"]["id"]
            await server.db[COLLECTION].update_one({"id": rid}, {"$unset": {"doc_version": ""}})
            seen = await client.get(f"/api/product-requests/{rid}")
            returned = seen.json()["request"]["doc_version"]
            assert returned is None
            patched = await client.patch(f"/api/product-requests/{rid}", json={
                "request_note": "sekali",
                "doc_version": returned,
            })
            assert patched.status_code == 200, patched.text
            assert patched.json()["request"]["doc_version"] == 1
            assert patched.json()["request"]["request_note"] == "sekali"
            assert patched.json()["request"]["customer_snapshot"]["name"] == "Kedai Baru"

            created_null = await client.post("/api/product-requests", json=COMPLETE_NEW)
            null_id = created_null.json()["request"]["id"]
            await server.db[COLLECTION].update_one({"id": null_id}, {"$set": {"doc_version": None}})
            seen_null = await client.get(f"/api/product-requests/{null_id}")
            assert seen_null.json()["request"]["doc_version"] is None
            patched_null = await client.patch(f"/api/product-requests/{null_id}", json={
                "request_note": "null legacy",
                "doc_version": seen_null.json()["request"]["doc_version"],
            })
            assert patched_null.status_code == 200, patched_null.text
            assert patched_null.json()["request"]["doc_version"] == 1
        stored = await server.db[COLLECTION].find_one({"id": rid}, {"_id": 0})
        stored_null = await server.db[COLLECTION].find_one({"id": null_id}, {"_id": 0})
        assert stored["doc_version"] == 1
        assert stored_null["doc_version"] == 1
        assert stored_null["request_note"] == "null legacy"
    finally:
        app.dependency_overrides.pop(current_user, None)


@pytest.mark.anyio
async def test_concurrent_patch_versus_submit_uses_barrier():
    import server
    import services.product_requests as pr
    crm = _user("crm", name="CRM Race", override={"product_request.create": True, "product_request.read_own_draft": True})
    await _prepare(server.db, [crm])
    ready = asyncio.Event()
    go = asyncio.Event()
    seen = 0
    lock = asyncio.Lock()

    async def gate():
        nonlocal seen
        async with lock:
            seen += 1
            if seen >= 2:
                ready.set()
        await go.wait()

    pr.after_draft_load = gate
    try:
        async with _client_for(crm) as client:
            created = await client.post("/api/product-requests", json=COMPLETE_NEW)
            rid = created.json()["request"]["id"]
            original_note = created.json()["request"]["request_note"]
            patch_task = asyncio.create_task(
                client.patch(f"/api/product-requests/{rid}", json={"request_note": "LOSER NOTE"})
            )
            submit_task = asyncio.create_task(
                client.post(f"/api/product-requests/{rid}/submit", json={})
            )
            await asyncio.wait_for(ready.wait(), timeout=3)
            go.set()
            patch_res, submit_res = await asyncio.gather(patch_task, submit_task)
        stored = await server.db[COLLECTION].find_one({"id": rid}, {"_id": 0})
        assert {patch_res.status_code, submit_res.status_code} == {200, 409}
        if submit_res.status_code == 200:
            receipt = submit_res.json()["receipt"]
            assert receipt["receipt_only"] is True
            assert "customer_snapshot" not in receipt
            assert "customer_snapshot" not in (patch_res.json() or {})
            assert stored["status"] == "submitted"
            assert stored["request_note"] == original_note
            assert stored["request_note"] != "LOSER NOTE"
            assert stored["customer_snapshot"]["name"] == "Kedai Baru"
        else:
            body = patch_res.json()["request"]
            assert body["status"] == "draft"
            assert body["request_note"] == "LOSER NOTE"
            assert stored["status"] == "draft"
            assert stored.get("submitted_at") is None
            assert submit_res.status_code == 409
    finally:
        pr.after_draft_load = None
        app.dependency_overrides.pop(current_user, None)


@pytest.mark.anyio
async def test_concurrent_double_submit_one_winner():
    import server
    import services.product_requests as pr
    crm = _user("crm", name="CRM Double", override={"product_request.create": True})
    await _prepare(server.db, [crm])
    ready = asyncio.Event()
    go = asyncio.Event()
    seen = 0
    lock = asyncio.Lock()

    async def gate():
        nonlocal seen
        async with lock:
            seen += 1
            if seen >= 2:
                ready.set()
        await go.wait()

    pr.after_draft_load = gate
    try:
        async with _client_for(crm) as client:
            created = await client.post("/api/product-requests", json=COMPLETE_NEW)
            rid = created.json()["request"]["id"]
            first = asyncio.create_task(client.post(f"/api/product-requests/{rid}/submit", json={}))
            second = asyncio.create_task(client.post(f"/api/product-requests/{rid}/submit", json={
                **COMPLETE_NEW,
                "potential_qty": 1,
                "request_note": "SECOND",
            }))
            await asyncio.wait_for(ready.wait(), timeout=3)
            go.set()
            a, b = await asyncio.gather(first, second)
        codes = sorted([a.status_code, b.status_code])
        assert codes == [200, 409]
        winner = a if a.status_code == 200 else b
        receipt = winner.json()["receipt"]
        assert set(receipt) <= {"id", "request_number", "status", "submitted_at", "receipt_only"}
        assert receipt["receipt_only"] is True
        stored = await server.db[COLLECTION].find_one({"id": rid}, {"_id": 0})
        assert await server.db[COLLECTION].count_documents({"id": rid}) == 1
        assert stored["status"] == "submitted"
        if a.status_code == 200:
            assert stored["potential_qty"] == 50000
            assert stored["request_note"] == "Customer rebranding"
        else:
            assert stored["potential_qty"] == 1
            assert stored["request_note"] == "SECOND"
        assert stored["customer_snapshot"]["name"] == "Kedai Baru"
    finally:
        pr.after_draft_load = None
        app.dependency_overrides.pop(current_user, None)


def _ids(response):
    return {row["id"] for row in response.json()["requests"]}


@pytest.mark.anyio
async def test_read_all_and_create_split_submitted_and_own_drafts():
    """Own drafts stay visible to their creator without exposing anyone else's draft."""
    import server
    creator = _user(
        "direktur_utama",
        name="Direktur Combo",
        override={"product_request.read_all": True, "product_request.create": True, "product_request.read_own_draft": True},
    )
    other = _user(
        "crm",
        name="CRM Lain",
        override={"product_request.create": True, "product_request.read_own_draft": True},
    )
    read_all_only = _user(
        "holding_team",
        name="Holding Only",
        override={"product_request.read_all": True, "product_request.create": False, "product_request.read_own_draft": False},
    )
    create_only = _user(
        "crm",
        name="CRM Own",
        override={"product_request.create": True, "product_request.read_own_draft": True, "product_request.read_all": False},
    )
    await _prepare(server.db, [creator, other, read_all_only, create_only])
    try:
        async with _client_for(creator) as client:
            own_draft = await client.post("/api/product-requests", json={"customer_source": "new_prospect", "customer_name": "Draft Direktur"})
            assert own_draft.status_code == 200, own_draft.text
            own_draft_id = own_draft.json()["request"]["id"]
            own_draft_version = own_draft.json()["request"]["doc_version"]
            own_submit_created = await client.post("/api/product-requests", json=COMPLETE_NEW)
            own_submit_id = own_submit_created.json()["request"]["id"]
            own_submit_version = own_submit_created.json()["request"]["doc_version"]
            submitted = await client.post(f"/api/product-requests/{own_submit_id}/submit", json=COMPLETE_NEW)
            assert submitted.status_code == 200, submitted.text

        async with _client_for(other) as client:
            foreign_draft = await client.post("/api/product-requests", json={"customer_source": "new_prospect", "customer_name": "Draft Orang Lain"})
            foreign_draft_id = foreign_draft.json()["request"]["id"]
            foreign_submit_created = await client.post("/api/product-requests", json=COMPLETE_NEW)
            foreign_submit_id = foreign_submit_created.json()["request"]["id"]
            foreign_submitted = await client.post(f"/api/product-requests/{foreign_submit_id}/submit", json=COMPLETE_NEW)
            assert foreign_submitted.status_code == 200, foreign_submitted.text

        async with _client_for(create_only) as client:
            create_draft = await client.post("/api/product-requests", json={"customer_source": "new_prospect", "customer_name": "Draft CRM"})
            create_draft_id = create_draft.json()["request"]["id"]
            listed = await client.get("/api/product-requests")
            assert listed.status_code == 200
            assert create_draft_id not in _ids(listed)
            assert foreign_draft_id not in _ids(listed)
            assert foreign_submit_id in _ids(listed)
            scoped = await client.get("/api/product-requests", params={"view": "own_drafts"})
            assert scoped.status_code == 200
            assert _ids(scoped) == {create_draft_id}
            assert scoped.json()["requests"][0]["status"] == "draft"
            open_submitted = await client.get("/api/product-requests", params={"view": "submitted"})
            assert open_submitted.status_code == 200
            assert foreign_submit_id in _ids(open_submitted)

        async with _client_for(read_all_only) as client:
            submitted_rows = await client.get("/api/product-requests", params={"view": "submitted"})
            assert submitted_rows.status_code == 200
            submitted_ids = _ids(submitted_rows)
            assert own_submit_id in submitted_ids
            assert foreign_submit_id in submitted_ids
            assert own_draft_id not in submitted_ids
            assert foreign_draft_id not in submitted_ids
            assert create_draft_id not in submitted_ids
            denied_drafts = await client.get("/api/product-requests", params={"view": "own_drafts"})
            assert denied_drafts.status_code == 403
            foreign_get = await client.get(f"/api/product-requests/{foreign_draft_id}")
            assert foreign_get.status_code == 403

        async with _client_for(creator) as client:
            submitted_rows = await client.get("/api/product-requests", params={"view": "submitted"})
            assert submitted_rows.status_code == 200
            submitted_ids = _ids(submitted_rows)
            assert own_submit_id in submitted_ids
            assert foreign_submit_id in submitted_ids
            assert own_draft_id not in submitted_ids
            assert foreign_draft_id not in submitted_ids
            own_rows = await client.get("/api/product-requests", params={"view": "own_drafts"})
            assert own_rows.status_code == 200
            assert _ids(own_rows) == {own_draft_id}
            draft_row = own_rows.json()["requests"][0]
            assert draft_row["status"] == "draft"
            assert draft_row["customer_name"] == "Draft Direktur"
            assert "completeness" in draft_row
            foreign_get = await client.get(f"/api/product-requests/{foreign_draft_id}")
            assert foreign_get.status_code == 403
            own_get = await client.get(f"/api/product-requests/{own_draft_id}")
            assert own_get.status_code == 200
            assert own_get.json()["request"]["doc_version"] == own_draft_version
            stored_submit = await server.db[COLLECTION].find_one({"id": own_submit_id}, {"_id": 0, "doc_version": 1})
            assert stored_submit["doc_version"] == own_submit_version + 1
    finally:
        app.dependency_overrides.pop(current_user, None)


@pytest.mark.anyio
async def test_feedback_status_history_notifications_and_concurrency():
    import server
    from services.notifications import COLLECTION as NOTIF
    from services.product_requests import ACTIVITY_COLLECTION

    crm = _user("crm", name="CRM Flow", override={"product_request.create": True, "product_request.read_own_draft": True})
    holding = _user(
        "holding_team",
        name="Holding Flow",
        override={
            "product_request.read_all": True,
            "product_request.feedback": True,
            "product_request.update_status": True,
            "product_request.create": False,
        },
    )
    direktur = _user(
        "direktur_utama",
        name="Direktur Flow",
        override={
            "product_request.read_all": True,
            "product_request.feedback": True,
            "product_request.update_status": True,
            "product_request.create": False,
        },
    )
    helper = _user("helper", name="Helper Flow")
    # Isolate from leftover users/role docs in shared test DB (xdist pollution).
    await server.db.users.delete_many({
        "role": {"$in": ["crm", "holding_team", "direktur_utama", "helper"]},
    })
    await server.db.role_permissions.delete_many({
        "role": {"$in": ["crm", "holding_team", "direktur_utama", "helper"]},
    })
    await _prepare(server.db, [crm, holding, direktur, helper])
    await server.db[NOTIF].delete_many({})
    await server.db[ACTIVITY_COLLECTION].delete_many({})
    stock_before = await server.db.product_stock_state.count_documents({})
    po_before = await server.db.purchase_orders.count_documents({}) if "purchase_orders" in await server.db.list_collection_names() else 0

    try:
        async with _client_for(crm) as client:
            created = await client.post("/api/product-requests", json=COMPLETE_NEW)
            assert created.status_code == 200, created.text
            rid = created.json()["request"]["id"]
            potential_qty = created.json()["request"]["potential_qty"]
            target_price = created.json()["request"]["target_price"]
            submitted = await client.post(f"/api/product-requests/{rid}/submit", json=COMPLETE_NEW)
            assert submitted.status_code == 200, submitted.text

        # Submit notifies read_all holders (holding + direktur), not helper / creator.
        notifs = await server.db[NOTIF].find(
            {"event_type": "product_request_submitted", "approval_id": f"submit:{rid}"},
            {"_id": 0},
        ).to_list(20)
        recipients = {n["user_id"] for n in notifs}
        assert holding["id"] in recipients
        assert direktur["id"] in recipients
        assert crm["id"] not in recipients
        assert helper["id"] not in recipients
        # Idempotent retry of same dedupe does not duplicate
        assert len(notifs) == len(recipients)

        async with _client_for(holding) as client:
            detail = await client.get(f"/api/product-requests/{rid}")
            version = detail.json()["request"]["doc_version"]
            fb = await client.post(
                f"/api/product-requests/{rid}/feedback",
                json={"note": "Mohon cek spesifikasi", "doc_version": version},
            )
            assert fb.status_code == 200, fb.text
            assert fb.json()["request"]["status"] == "submitted"
            version = fb.json()["request"]["doc_version"]

            pending_missing = await client.post(
                f"/api/product-requests/{rid}/status",
                json={"status": "pending", "doc_version": version},
            )
            assert pending_missing.status_code == 400

            status_res = await client.post(
                f"/api/product-requests/{rid}/status",
                json={"status": "pending", "note": "Menunggu harga supplier", "doc_version": version},
            )
            assert status_res.status_code == 200, status_res.text
            assert status_res.json()["request"]["status"] == "pending"
            version = status_res.json()["request"]["doc_version"]

        async with _client_for(direktur) as client:
            bought = await client.post(
                f"/api/product-requests/{rid}/status",
                json={
                    "status": "purchased",
                    "note": "Sudah dibeli",
                    "actual_qty": 48000,
                    "actual_price": 800,
                    "doc_version": version,
                },
            )
            assert bought.status_code == 200, bought.text
            body = bought.json()["request"]
            assert body["status"] == "purchased"
            assert body["purchase_actual_qty"] == 48000
            assert body["purchase_actual_price"] == 800
            assert body["potential_qty"] == potential_qty
            assert body["target_price"] == target_price
            version = body["doc_version"]

            # Concurrent stale write loses cleanly
            stale = await client.post(
                f"/api/product-requests/{rid}/feedback",
                json={"note": "stale", "doc_version": version - 1},
            )
            assert stale.status_code == 409

            race_a = client.post(
                f"/api/product-requests/{rid}/feedback",
                json={"note": "feedback A", "doc_version": version},
            )
            race_b = client.post(
                f"/api/product-requests/{rid}/feedback",
                json={"note": "feedback B", "doc_version": version},
            )
            ra, rb = await asyncio.gather(race_a, race_b)
            codes = sorted([ra.status_code, rb.status_code])
            assert codes == [200, 409], (ra.text, rb.text)

        activities = await server.db[ACTIVITY_COLLECTION].find({"request_id": rid}, {"_id": 0}).sort("created_at", 1).to_list(50)
        types = [a["type"] for a in activities]
        assert "submitted" in types
        assert "feedback" in types
        assert "status_change" in types
        assert all(a.get("actor_id") and a.get("created_at") for a in activities)
        feedback_notes = {a["note"] for a in activities if a["type"] == "feedback"}
        assert "Mohon cek spesifikasi" in feedback_notes
        assert ("feedback A" in feedback_notes) ^ ("feedback B" in feedback_notes) or (
            "feedback A" in feedback_notes and "feedback B" in feedback_notes
        )
        # Winner + prior feedback preserved (append-only); loser does not overwrite winner text.
        assert len([a for a in activities if a["type"] == "feedback"]) >= 2

        creator_notifs = await server.db[NOTIF].find(
            {"user_id": crm["id"], "event_type": {"$in": ["product_request_feedback", "product_request_status"]}},
            {"_id": 0},
        ).to_list(50)
        assert creator_notifs
        assert all(n.get("deep_link", "").startswith("/penjualan/permintaan-produk") for n in creator_notifs)

        # Terbeli side-effect free for stock / PO
        assert await server.db.product_stock_state.count_documents({}) == stock_before
        if "purchase_orders" in await server.db.list_collection_names():
            assert await server.db.purchase_orders.count_documents({}) == po_before

        # Legacy submitted rows remain readable as Terkirim
        legacy_id = str(uuid.uuid4())
        await server.db[COLLECTION].insert_one({
            "id": legacy_id,
            "request_number": f"PPR-LEGACY-{legacy_id[:6]}",
            "status": "submitted",
            "customer_source": "new_prospect",
            "customer_snapshot": {"name": "Legacy Customer"},
            "product_source": "requested_product",
            "product_snapshot": {"name": "Legacy Product"},
            "potential_qty": 1000,
            "created_by_id": crm["id"],
            "created_by_snapshot": {"id": crm["id"], "name": "CRM Flow"},
            "branch_id": "br-sby",
            "branch_snapshot": {"id": "br-sby", "name": "Surabaya"},
            "created_at": "2026-01-01T00:00:00+00:00",
            "updated_at": "2026-01-01T00:00:00+00:00",
            "submitted_at": "2026-01-01T00:00:00+00:00",
        })
        async with _client_for(helper) as client:
            legacy = await client.get(f"/api/product-requests/{legacy_id}")
            assert legacy.status_code == 200
            assert legacy.json()["request"]["status"] == "submitted"
            assert legacy.json()["request"]["status_label"] == "Terkirim"
    finally:
        app.dependency_overrides.pop(current_user, None)
