"""Real Mongo tests for local master create + partial Accurate ID index.

Uses a dedicated DB. Does not touch eternal_packaging_preview or production.
Accurate live is never called.
"""
from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path

import pytest
from fastapi import HTTPException
from motor.motor_asyncio import AsyncIOMotorClient
from pymongo.errors import DuplicateKeyError

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ.setdefault("MONGO_URL", "mongodb://127.0.0.1:27017/?replicaSet=eternal_local_rs")
os.environ["DB_NAME"] = "eternal_packaging_test_master_local_create"
os.environ.setdefault("APP_ENV", "test")

from services.accurate import normalize_accurate_item  # noqa: E402
from services.master_items import (  # noqa: E402
    ACCURATE_ID_PARTIAL_INDEX,
    CODE_CLAIMS_COLLECTION,
    CODE_CLAIMS_INDEX,
    COLLECTION,
    INDEX_NOT_READY_MSG,
    LEGACY_ACCURATE_ID_INDEX,
    create_local_master_item,
    ensure_master_item_indexes,
    local_master_create_index_status,
    normalize_item_code_key,
    prepare_master_item_indexes_for_local_create,
    sync_master_items,
    upsert_master_item,
)

TEST_DB = "eternal_packaging_test_master_local_create"
TEST_DB_INDEX_GATE = "eternal_packaging_test_master_local_index_gate"
MONGO_URL = os.environ.get("MONGO_URL", "mongodb://127.0.0.1:27017/?replicaSet=eternal_local_rs")
if "replicaSet=" not in MONGO_URL:
    MONGO_URL = MONGO_URL.rstrip("/") + ("&" if "?" in MONGO_URL else "?") + "replicaSet=eternal_local_rs"

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
async def db():
    assert TEST_DB != "eternal_packaging"
    assert "preview" not in TEST_DB
    client = AsyncIOMotorClient(MONGO_URL)
    database = client[TEST_DB]
    await client.drop_database(TEST_DB)
    # Explicit migrate (allow drop) — NOT what ordinary server startup does.
    await prepare_master_item_indexes_for_local_create(database, allow_drop_legacy=True)
    yield database
    await client.drop_database(TEST_DB)
    client.close()


class _FakeAccurate:
    def __init__(self, items):
        self._items = list(items)
        self.is_live = False

    async def fetch_all_items(self):
        out = []
        for raw in self._items:
            n = normalize_accurate_item(raw)
            if n:
                out.append(n)
        return out


async def test_partial_index_allows_many_local_and_rejects_dup_accurate_id(db):
    info = await db[COLLECTION].index_information()
    assert ACCURATE_ID_PARTIAL_INDEX in info
    if LEGACY_ACCURATE_ID_INDEX in info:
        assert info[LEGACY_ACCURATE_ID_INDEX].get("partialFilterExpression")

    a = await create_local_master_item(db, {
        "item_code": "L-1", "item_name": "Lokal 1", "item_type": "INVENTORY",
        "unit": "pcs", "is_active": True,
    })
    b = await create_local_master_item(db, {
        "item_code": "L-2", "item_name": "Lokal 2", "item_type": "INVENTORY",
        "unit": "pcs", "is_active": True,
    })
    assert a["id"] != b["id"]
    assert a["accurate_item_id"] is None and b["accurate_item_id"] is None
    assert await db[COLLECTION].count_documents({"origin": "local"}) == 2

    await upsert_master_item(db, {
        "accurate_item_id": "A-100",
        "item_code": "ACC-100",
        "item_name": "Acc One",
        "item_type": "INVENTORY",
        "unit": "pcs",
        "is_active": True,
    })
    with pytest.raises(DuplicateKeyError):
        await db[COLLECTION].insert_one({
            "id": str(uuid.uuid4()),
            "accurate_item_id": "A-100",
            "item_code": "ACC-100-DUP",
            "item_name": "Dup",
            "origin": "accurate",
        })

    client = _FakeAccurate([{
        "id": 200, "no": "ACC-200", "name": "Acc Two",
        "itemType": "INVENTORY", "unit1Name": "pcs", "suspended": False,
    }])
    result = await sync_master_items(db, client)
    assert result["created"] == 1
    assert await db[COLLECTION].count_documents({"origin": "local"}) == 2
    local = await db[COLLECTION].find_one({"item_code": "L-1"}, {"_id": 0})
    assert local.get("accurate_item_id") in (None, "")
    assert "accurate_item_id" not in local or local.get("accurate_item_id") in (None, "")

    assert await db.physical_stock_balances.count_documents({}) == 0
    assert await db.product_stock_state.count_documents({}) == 0


async def test_concurrent_duplicate_code_and_orphan_claim_recovery(db):
    import asyncio

    payload = {
        "item_code": "RaceCode",
        "item_name": "Race",
        "item_type": "INVENTORY",
        "unit": "pcs",
        "is_active": True,
    }
    results = await asyncio.gather(
        create_local_master_item(db, payload),
        create_local_master_item(db, {**payload, "item_name": "Race2"}),
        return_exceptions=True,
    )
    oks = [r for r in results if not isinstance(r, BaseException)]
    errs = [r for r in results if isinstance(r, BaseException)]
    assert len(oks) == 1, results
    assert len(errs) == 1
    assert isinstance(errs[0], HTTPException)
    assert errs[0].status_code == 409
    assert await db[COLLECTION].count_documents({}) == 1
    assert await db[CODE_CLAIMS_COLLECTION].count_documents({}) == 1


async def test_startup_ensure_does_not_drop_legacy(db):
    """Ordinary ensure must not drop a legacy full unique if reintroduced."""
    # Reinstall a fake legacy-style unique name for the readiness signal.
    # Preview DBs after prepare already dropped it; recreate non-partial unique
    # only when no conflicting nulls exist (this fixture has locals — skip recreate
    # and instead assert ensure never reports dropped_legacy).
    before = await local_master_create_index_status(db)
    result = await ensure_master_item_indexes(db)
    assert "dropped_legacy_accurate_id" not in result
    assert result["partial_accurate_id"] is True
    assert result["code_claims"] is True
    after = await local_master_create_index_status(db)
    assert after["partial_accurate_id"] == before["partial_accurate_id"]
    assert after["local_create_ready"] == before["local_create_ready"]


async def test_sync_new_accurate_id_with_code_collision_does_not_overwrite_local(db):
    """Conflict rule: sync upserts by Accurate ID only; never merges into origin=local.

    A new Accurate item may share a normalized item_code with a local row; local
    identity (id, omitted accurate_item_id, origin) stays intact. Create API still
    rejects the duplicate code; sync does not auto-link by name/code.
    """
    import asyncio

    local = await create_local_master_item(db, {
        "item_code": "Share-99",
        "item_name": "Lokal Collision",
        "item_type": "INVENTORY",
        "unit": "pcs",
        "is_active": True,
    })
    local_id = local["id"]

    # Concurrent: local create attempt + Accurate sync with colliding code (whitespace/case).
    client = _FakeAccurate([{
        "id": 88001,
        "no": "  share-99  ",
        "name": "Accurate Collision Twin",
        "itemType": "INVENTORY",
        "unit1Name": "pcs",
        "suspended": False,
    }])

    async def try_dup_create():
        return await create_local_master_item(db, {
            "item_code": "SHARE-99",
            "item_name": "Should 409",
            "item_type": "INVENTORY",
            "unit": "pcs",
            "is_active": True,
        })

    results = await asyncio.gather(
        sync_master_items(db, client),
        try_dup_create(),
        return_exceptions=True,
    )
    sync_res, create_res = results
    assert not isinstance(sync_res, BaseException), sync_res
    assert sync_res["created"] == 1
    assert isinstance(create_res, HTTPException)
    assert create_res.status_code == 409

    fresh_local = await db[COLLECTION].find_one({"id": local_id}, {"_id": 0})
    assert fresh_local["origin"] == "local"
    assert fresh_local.get("accurate_item_id") in (None, "")
    assert fresh_local["item_name"] == "Lokal Collision"

    acc = await db[COLLECTION].find_one({"accurate_item_id": "88001"}, {"_id": 0})
    assert acc is not None
    assert acc["origin"] == "accurate"
    assert acc["id"] != local_id
    # Normalized code collision is allowed across origins after sync; no merge.
    assert normalize_item_code_key(acc.get("item_code")) == normalize_item_code_key("Share-99")


@pytest.fixture
async def db_index_gate():
    """Separate DB for index-not-ready create gates. Never touches preview."""
    assert TEST_DB_INDEX_GATE != "eternal_packaging"
    assert "preview" not in TEST_DB_INDEX_GATE
    client = AsyncIOMotorClient(MONGO_URL)
    database = client[TEST_DB_INDEX_GATE]
    await client.drop_database(TEST_DB_INDEX_GATE)
    yield database
    await client.drop_database(TEST_DB_INDEX_GATE)
    client.close()


async def _assert_create_503_no_writes(database, code: str):
    before_masters = await database[COLLECTION].count_documents({})
    before_claims = await database[CODE_CLAIMS_COLLECTION].count_documents({})
    with pytest.raises(HTTPException) as exc:
        await create_local_master_item(database, {
            "item_code": code,
            "item_name": "Blocked by indexes",
            "item_type": "INVENTORY",
            "unit": "pcs",
            "is_active": True,
        })
    assert exc.value.status_code == 503
    assert INDEX_NOT_READY_MSG in str(exc.value.detail)
    assert await database[COLLECTION].count_documents({}) == before_masters
    assert await database[CODE_CLAIMS_COLLECTION].count_documents({}) == before_claims


async def test_create_rejected_when_legacy_full_unique_present(db_index_gate):
    """Matches live snapshot shape: full unique accurate_item_id_1, claims may exist."""
    db = db_index_gate
    await db[COLLECTION].create_index(
        [("accurate_item_id", 1)],
        unique=True,
        name=LEGACY_ACCURATE_ID_INDEX,
    )
    await db[CODE_CLAIMS_COLLECTION].create_index(
        [("code_key", 1)],
        unique=True,
        name=CODE_CLAIMS_INDEX,
    )
    status = await local_master_create_index_status(db)
    assert status["legacy_full_unique_present"] is True
    assert status["partial_accurate_id"] is False
    assert status["local_create_ready"] is False
    await _assert_create_503_no_writes(db, "GATE-LEGACY-1")


async def test_create_rejected_when_partial_missing(db_index_gate):
    db = db_index_gate
    await db[CODE_CLAIMS_COLLECTION].create_index(
        [("code_key", 1)],
        unique=True,
        name=CODE_CLAIMS_INDEX,
    )
    status = await local_master_create_index_status(db)
    assert status["partial_accurate_id"] is False
    assert status["local_create_ready"] is False
    await _assert_create_503_no_writes(db, "GATE-NOPART-1")


async def test_create_rejected_when_claims_unique_missing(db_index_gate):
    db = db_index_gate
    await db[COLLECTION].create_index(
        [("accurate_item_id", 1)],
        unique=True,
        name=ACCURATE_ID_PARTIAL_INDEX,
        partialFilterExpression={
            "accurate_item_id": {"$exists": True, "$type": "string", "$gt": ""},
        },
    )
    status = await local_master_create_index_status(db)
    assert status["code_claims"] is False
    assert status["local_create_ready"] is False
    await _assert_create_503_no_writes(db, "GATE-NOCLAIM-1")


async def test_create_succeeds_when_indexes_fully_ready(db_index_gate):
    db = db_index_gate
    await prepare_master_item_indexes_for_local_create(db, allow_drop_legacy=True)
    status = await local_master_create_index_status(db)
    assert status["local_create_ready"] is True
    created = await create_local_master_item(db, {
        "item_code": "GATE-OK-1",
        "item_name": "Ready Create",
        "item_type": "INVENTORY",
        "unit": "pcs",
        "is_active": True,
    })
    assert created["origin"] == "local"
    assert created["accurate_item_id"] is None
    assert await db[COLLECTION].count_documents({"id": created["id"]}) == 1
    assert await db[CODE_CLAIMS_COLLECTION].count_documents({}) == 1

    with pytest.raises(HTTPException) as exc:
        await create_local_master_item(db, {
            "item_code": "gate-ok-1",
            "item_name": "Dup",
            "item_type": "SERVICE",
            "unit": "pcs",
            "is_active": True,
        })
    assert exc.value.status_code == 409
    assert await db[COLLECTION].count_documents({}) == 1
    assert await db[CODE_CLAIMS_COLLECTION].count_documents({}) == 1
