"""Empty Stock Saat Ini catalog must retain mode metadata (SSI-EMPTY-MODE-FLAGS).

Isolated replica-set DB only. Does not mutate production / OWNER UAT.
"""
from __future__ import annotations

import os
import uuid

import httpx
import pytest
from motor.motor_asyncio import AsyncIOMotorClient

os.environ["DB_NAME"] = "eternal_packaging_test_ssi_empty_mode"
os.environ.setdefault(
    "MONGO_URL",
    "mongodb://127.0.0.1:27017/?replicaSet=eternal_local_rs",
)

from services.physical_stock import (  # noqa: E402
    BRANCHES_COLLECTION,
    MASTER_ITEMS_COLLECTION,
    WAREHOUSES_COLLECTION,
    ensure_indexes,
    list_physical_stock_catalog,
)
from services.r2_go_live import set_r2_authority_active  # noqa: E402
from services.warehouse_stock_standalone import (  # noqa: E402
    MODE_WAREHOUSE_STANDALONE,
    set_warehouse_stock_standalone_enabled,
)
import server  # noqa: E402
from server import app, current_user  # noqa: E402

TEST_DB = "eternal_packaging_test_ssi_empty_mode"
MONGO_URL = os.environ["MONGO_URL"]
if "replicaSet=" not in MONGO_URL:
    MONGO_URL = MONGO_URL.rstrip("/") + ("&" if "?" in MONGO_URL else "?") + "replicaSet=eternal_local_rs"

pytestmark = pytest.mark.anyio

MODE_KEYS = (
    "stock_read_mode",
    "r2_authority_active",
    "warehouse_stock_standalone",
    "physical_qty_scope",
    "branch_filter_affects_physical_qty",
    "warehouse_filter_affects_physical_qty",
    "effective_warehouse_id",
    "effective_warehouse_code",
    "demand_scope",
    "physical_qty_note",
)


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
async def db():
    assert "eternal_packaging_preview" not in TEST_DB
    assert TEST_DB != "eternal_packaging"
    client = AsyncIOMotorClient(MONGO_URL)
    database = client[TEST_DB]
    await client.drop_database(TEST_DB)
    await ensure_indexes(database)
    server.db = database
    server.client = client
    try:
        yield database
    finally:
        await client.drop_database(TEST_DB)
        client.close()


async def _seed_branch_wh(db):
    branch = {
        "id": f"br-{uuid.uuid4().hex[:8]}",
        "code": "YGY",
        "name": "Yogyakarta",
        "active": True,
    }
    wh = {
        "id": f"wh-{uuid.uuid4().hex[:8]}",
        "branch_id": branch["id"],
        "code": "MAIN",
        "name": "Utama",
        "active": True,
        "is_operational": True,
    }
    await db[BRANCHES_COLLECTION].insert_one(dict(branch))
    await db[WAREHOUSES_COLLECTION].insert_one(dict(wh))
    return branch, wh


async def _seed_barang(db, code="BOX"):
    item = {
        "id": f"item-{uuid.uuid4().hex[:8]}",
        "item_code": code,
        "item_name": f"Barang {code}",
        "unit": "PCS",
        "item_type": "INVENTORY",
        "is_active": True,
    }
    await db[MASTER_ITEMS_COLLECTION].insert_one(dict(item))
    return item


async def _seed_service(db, code="SVC"):
    item = {
        "id": f"svc-{uuid.uuid4().hex[:8]}",
        "item_code": code,
        "item_name": f"Service {code}",
        "unit": "PCS",
        "item_type": "SERVICE",
        "is_active": True,
    }
    await db[MASTER_ITEMS_COLLECTION].insert_one(dict(item))
    return item


def _assert_empty_shell(catalog: dict):
    assert catalog["items"] == []
    assert catalog["total"] == 0
    assert catalog["master_item_count"] == 0
    assert catalog["page_count"] == 0


def _mode_slice(catalog: dict) -> dict:
    return {k: catalog.get(k) for k in MODE_KEYS}


async def test_mode_b_empty_search_keeps_standalone_flags(db):
    await set_r2_authority_active(db, active=False)
    await set_warehouse_stock_standalone_enabled(db, enabled=True)
    branch, wh = await _seed_branch_wh(db)
    await _seed_barang(db, "BOX-B")

    filled = await list_physical_stock_catalog(db, branch_id=branch["id"])
    empty = await list_physical_stock_catalog(
        db, branch_id=branch["id"], q="ZZZ-NOMATCH-MODE-B",
    )
    empty_wh = await list_physical_stock_catalog(
        db, branch_id=branch["id"], warehouse_id=wh["id"], q="ZZZ-NOMATCH-MODE-B",
    )

    _assert_empty_shell(empty)
    _assert_empty_shell(empty_wh)
    assert empty["stock_read_mode"] == MODE_WAREHOUSE_STANDALONE
    assert empty["r2_authority_active"] is False
    assert empty["warehouse_stock_standalone"] is True
    assert empty["physical_qty_scope"] == "BRANCH"
    assert empty["warehouse_count"] == 1
    assert empty_wh["physical_qty_scope"] == "WAREHOUSE"
    assert empty_wh["effective_warehouse_id"] == wh["id"]
    assert empty_wh["effective_warehouse_code"] == "MAIN"
    assert _mode_slice(empty) == _mode_slice(filled)
    assert filled["total"] >= 1


async def test_mode_c_empty_search_keeps_r2_flags(db):
    await set_warehouse_stock_standalone_enabled(db, enabled=False)
    await set_r2_authority_active(db, active=True)
    branch, wh = await _seed_branch_wh(db)
    await _seed_barang(db, "BOX-C")

    filled = await list_physical_stock_catalog(db, branch_id=branch["id"])
    empty = await list_physical_stock_catalog(
        db, branch_id=branch["id"], q="ZZZ-NOMATCH-MODE-C",
    )

    _assert_empty_shell(empty)
    assert empty["stock_read_mode"] == "r2"
    assert empty["r2_authority_active"] is True
    assert empty["warehouse_stock_standalone"] is False
    assert _mode_slice(empty) == _mode_slice(filled)
    empty_wh_scope = await list_physical_stock_catalog(
        db, branch_id=branch["id"], warehouse_id=wh["id"], q="NOPE",
    )
    assert empty_wh_scope["physical_qty_scope"] == "WAREHOUSE"
    assert empty_wh_scope["effective_warehouse_id"] == wh["id"]


async def test_mode_a_empty_search_keeps_compatibility_flags(db):
    await set_r2_authority_active(db, active=False)
    await set_warehouse_stock_standalone_enabled(db, enabled=False)
    await _seed_barang(db, "BOX-A")

    filled = await list_physical_stock_catalog(db)
    empty = await list_physical_stock_catalog(db, q="ZZZ-NOMATCH-MODE-A")

    _assert_empty_shell(empty)
    assert empty["stock_read_mode"] == "compatibility"
    assert empty["r2_authority_active"] is False
    assert empty.get("warehouse_stock_standalone") in (None, False)
    assert empty["physical_qty_scope"] == "COMPANY"
    assert empty["branch_filter_affects_physical_qty"] is False
    assert filled["stock_read_mode"] == empty["stock_read_mode"]
    assert filled["physical_qty_scope"] == empty["physical_qty_scope"]
    assert filled["r2_authority_active"] is False
    assert "perusahaan" in (empty.get("physical_qty_note") or "").lower()


async def test_service_only_masters_empty_catalog_keeps_mode_b_flags(db):
    await set_r2_authority_active(db, active=False)
    await set_warehouse_stock_standalone_enabled(db, enabled=True)
    branch, _wh = await _seed_branch_wh(db)
    await _seed_service(db, "SVC-ONLY")

    catalog = await list_physical_stock_catalog(db, branch_id=branch["id"])
    _assert_empty_shell(catalog)
    assert catalog["stock_read_mode"] == MODE_WAREHOUSE_STANDALONE
    assert catalog["warehouse_stock_standalone"] is True
    assert catalog["r2_authority_active"] is False
    assert catalog["warehouse_count"] == 1


async def test_http_empty_search_mode_b_preserves_adjust_and_label(db):
    """Endpoint must not stamp standalone-OFF blocked reason on empty Mode B."""
    await set_r2_authority_active(db, active=False)
    await set_warehouse_stock_standalone_enabled(db, enabled=True)
    branch, _wh = await _seed_branch_wh(db)
    await _seed_barang(db, "BOX-HTTP")

    async def _admin():
        return {
            "id": "admin-empty-mode",
            "email": "admin@test.local",
            "name": "Admin",
            "role": "admin",
            "permissions": ["*"],
            "is_super_admin": True,
        }

    app.dependency_overrides[current_user] = _admin
    transport = httpx.ASGITransport(app=app)
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            filled = await client.get(
                "/api/physical-stock",
                params={"branch_id": branch["id"]},
            )
            empty = await client.get(
                "/api/physical-stock",
                params={"branch_id": branch["id"], "q": "ZZZ-NOMATCH-HTTP"},
            )
    finally:
        app.dependency_overrides.pop(current_user, None)

    assert filled.status_code == 200
    assert empty.status_code == 200
    filled_body = filled.json()
    empty_body = empty.json()
    _assert_empty_shell(empty_body)
    assert empty_body["stock_read_mode"] == MODE_WAREHOUSE_STANDALONE
    assert empty_body["warehouse_stock_standalone"] is True
    assert empty_body["r2_authority_active"] is False
    assert empty_body.get("can_adjust_physical_stock") is True
    assert "adjust_blocked_reason" not in empty_body or not empty_body.get("adjust_blocked_reason")
    assert "standalone" in (empty_body.get("stock_read_mode_label") or "").lower()
    assert filled_body["stock_read_mode"] == empty_body["stock_read_mode"]
    assert filled_body["warehouse_stock_standalone"] == empty_body["warehouse_stock_standalone"]
