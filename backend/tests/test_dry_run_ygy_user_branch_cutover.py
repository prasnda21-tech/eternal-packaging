"""Dry-run YGY user branch cutover tool safety."""
from __future__ import annotations

import importlib.util
import os
from pathlib import Path

import pytest
from motor.motor_asyncio import AsyncIOMotorClient

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "dry_run_ygy_user_branch_cutover.py"
TEST_DB = "eternal_packaging_test_ygy_cutover_dryrun"
pytestmark = pytest.mark.anyio


def _load():
    spec = importlib.util.spec_from_file_location("dry_run_ygy_user_branch_cutover", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
async def db():
    client = AsyncIOMotorClient(os.environ.get("MONGO_URL", "mongodb://127.0.0.1:27017/?replicaSet=eternal_local_rs"))
    database = client[TEST_DB]
    await client.drop_database(TEST_DB)
    await database.branches.insert_one({
        "id": "br-ygy", "code": "YGY", "name": "Yogyakarta", "active": True,
    })
    await database.warehouses.insert_one({
        "id": "wh-ygy", "branch_id": "br-ygy", "code": "YGY-UTAMA",
        "name": "Gudang Utama Yogyakarta", "active": True, "is_operational": True,
    })
    await database.users.insert_many([
        {"id": "1", "email": "a@test", "role": "admin", "status": "active"},
        {"id": "2", "email": "h@test", "role": "holding_team", "status": "active"},
        {"id": "3", "email": "y@test", "role": "sales", "status": "active", "branch_ids": ["br-ygy"]},
        {"id": "4", "email": "x@test", "role": "operator", "status": "active"},
    ])
    yield database
    await client.drop_database(TEST_DB)
    client.close()


def test_refuses_apply(monkeypatch):
    mod = _load()
    monkeypatch.setenv("APPLY", "true")
    with pytest.raises(SystemExit):
        mod._refuse_apply()


async def test_dry_run_counts(db):
    mod = _load()
    report = await mod.dry_run(db)
    assert report["mode"] == "DRY_RUN"
    assert report["USERS_TOTAL"] == 4
    assert report["HOLDING_TEAM_GLOBAL"] == 1
    assert report["USERS_ALREADY_ASSIGNED"] == 1
    assert report["USERS_WOULD_ASSIGN_YGY"] == 2  # admin + operator unassigned
    assert report["ygy_branch"]["exists"] is True
    assert report["ygy_warehouse"]["exists"] is True
