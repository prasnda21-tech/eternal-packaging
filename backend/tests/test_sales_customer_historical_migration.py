"""Focused tests for one-time historical sales_customers migration."""
from __future__ import annotations

import asyncio
import os
import uuid

import pytest
from motor.motor_asyncio import AsyncIOMotorClient

from services.sales_customer_historical_migration import (
    OWNER_APPROVAL_ENV,
    OWNER_APPROVAL_VALUE,
    RUNS_COLLECTION,
    apply_historical_customer_migration,
    assert_owner_approved,
    plan_historical_customer_migration,
    rollback_historical_customer_migration,
)
from services.sales_customers import COLLECTION, ensure_sales_customer_indexes, normalize_customer_name


@pytest.fixture
def anyio_backend():
    return "asyncio"


def _client_db():
    os.environ.setdefault("MONGO_URL", "mongodb://127.0.0.1:27017")
    os.environ.setdefault("DB_NAME", "eternal_packaging_test")
    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    return client, client[os.environ["DB_NAME"]]


async def _cleanup(database, suffix: str, backup_names=None, batch_ids=None):
    await database[COLLECTION].delete_many({
        "$or": [
            {"created_by": "historical_migration"},
            {"notes": {"$regex": "historical_migration"}},
            {"id": {"$regex": suffix}},
            {"name": {"$regex": suffix}},
            {"migration_batch_id": {"$in": list(batch_ids or [])}},
        ]
    })
    await database.orders.delete_many({"po_number": {"$regex": f"-{suffix}$"}})
    if batch_ids:
        await database[RUNS_COLLECTION].delete_many({"batch_id": {"$in": list(batch_ids)}})
    else:
        await database[RUNS_COLLECTION].delete_many({
            "$or": [
                {"batch_id": {"$regex": suffix}},
                {"report.created.display_name": {"$regex": suffix}},
            ]
        })
    for name in backup_names or []:
        try:
            await database.drop_collection(name)
        except Exception:
            pass


@pytest.mark.anyio
async def test_dry_run_is_fully_zero_write_including_audit():
    client, database = _client_db()
    suffix = uuid.uuid4().hex[:8]
    await ensure_sales_customer_indexes(database)
    batch_ids = []
    try:
        before_masters = await database[COLLECTION].count_documents({})
        before_orders = await database.orders.count_documents({})
        before_runs = await database[RUNS_COLLECTION].count_documents({})
        await database.orders.insert_one({
            "id": f"ord-dry-{suffix}",
            "po_number": f"PO-DRY-{suffix}",
            "customer": f"Toko Dry {suffix}",
            "customer_phone": "081111",
            "address": "Jl Dry 1",
            "cancelled": False,
        })
        after_insert_orders = await database.orders.count_documents({})
        # Default dry-run: zero-write including migration audit collection.
        report = await plan_historical_customer_migration(database)
        assert report.mode == "dry-run"
        assert report.applied is False
        assert report.batch_id
        batch_ids.append(report.batch_id)
        assert await database[COLLECTION].count_documents({}) == before_masters
        assert await database.orders.count_documents({}) == after_insert_orders
        assert await database[RUNS_COLLECTION].count_documents({}) == before_runs
        assert await database[RUNS_COLLECTION].count_documents({"batch_id": report.batch_id}) == 0
        assert any(
            r.normalized_name == normalize_customer_name(f"Toko Dry {suffix}")
            for r in report.created
        )
        order = await database.orders.find_one({"id": f"ord-dry-{suffix}"}, {"_id": 0})
        assert not order.get("customer_id")
        assert order["customer"] == f"Toko Dry {suffix}"
        assert after_insert_orders == before_orders + 1
    finally:
        await _cleanup(database, suffix, batch_ids=batch_ids)
        client.close()


@pytest.mark.anyio
async def test_apply_requires_owner_approval():
    with pytest.raises(RuntimeError):
        assert_owner_approved(False, env_value=OWNER_APPROVAL_VALUE)
    with pytest.raises(RuntimeError):
        assert_owner_approved(True, env_value="")
    assert_owner_approved(True, env_value=OWNER_APPROVAL_VALUE)


@pytest.mark.anyio
async def test_apply_idempotent_audit_and_selective_rollback(monkeypatch):
    client, database = _client_db()
    suffix = uuid.uuid4().hex[:8]
    await ensure_sales_customer_indexes(database)
    monkeypatch.setenv(OWNER_APPROVAL_ENV, OWNER_APPROVAL_VALUE)
    backups = []
    batch_ids = []
    try:
        existing_name = f"Toko Existing {suffix}"
        existing_id = f"cust-exist-{suffix}"
        await database[COLLECTION].insert_one({
            "id": existing_id,
            "name": existing_name,
            "normalized_name": normalize_customer_name(existing_name),
            "phone": "080000",
            "address": "Alamat Master",
            "pic": "PIC Lama",
        })
        await database.orders.insert_one({
            "id": f"ord-exist-{suffix}",
            "po_number": f"PO-EXIST-{suffix}",
            "customer": existing_name,
            "customer_id": existing_id,
            "customer_phone": "089999",
            "address": "Alamat Order",
            "cancelled": False,
        })

        new_name = f"Toko Baru Mig {suffix}"
        await database.orders.insert_one({
            "id": f"ord-new-{suffix}",
            "po_number": f"PO-NEW-{suffix}",
            "customer": new_name,
            "customer_phone": "081222",
            "address": "Jl Baru",
            "customer_pic": "Ani",
            "customer_city": "Bekasi",
            "cancelled": False,
        })

        amb_name = f"Toko Ambigu {suffix}"
        await database.orders.insert_one({
            "id": f"ord-amb-a-{suffix}",
            "po_number": f"PO-AMB-A-{suffix}",
            "customer": amb_name,
            "customer_id": f"id-a-{suffix}",
            "customer_phone": "081",
            "address": "A",
            "cancelled": False,
        })
        await database.orders.insert_one({
            "id": f"ord-amb-b-{suffix}",
            "po_number": f"PO-AMB-B-{suffix}",
            "customer": amb_name,
            "customer_id": f"id-b-{suffix}",
            "customer_phone": "082",
            "address": "B",
            "cancelled": False,
        })
        await database.orders.insert_one({
            "id": f"ord-amb-h-{suffix}",
            "po_number": f"PO-AMB-H-{suffix}",
            "customer": amb_name,
            "customer_id": f"{amb_name}|Outlet",
            "outlet": "Outlet",
            "customer_phone": "083",
            "address": "H",
            "cancelled": False,
        })

        await database.orders.insert_one({
            "id": f"ord-inc-{suffix}",
            "po_number": f"PO-INC-{suffix}",
            "customer": f"Toko Incomplete {suffix}",
            "customer_phone": "",
            "address": "",
            "cancelled": False,
        })
        await database.orders.insert_one({
            "id": f"ord-can-{suffix}",
            "po_number": f"PO-CAN-{suffix}",
            "customer": f"Toko Cancel {suffix}",
            "customer_phone": "081",
            "address": "X",
            "cancelled": True,
        })
        await database.orders.insert_one({
            "id": f"ord-test-{suffix}",
            "po_number": f"PO-TEST-{suffix}",
            "customer": f"Toko Test {suffix}",
            "customer_phone": "081",
            "address": "X",
            "is_test": True,
        })

        first = await apply_historical_customer_migration(
            database, owner_approved=True, create_backup=True,
        )
        batch_ids.append(first.batch_id)
        if first.backup_collection:
            backups.append(first.backup_collection)
        assert first.applied is True
        assert first.batch_id.startswith("mig-")
        assert first.backup_collection
        assert any(r.action == "created" and r.display_name == new_name for r in first.created)
        assert any(r.existing_id == existing_id for r in first.skipped)
        assert any(r.action == "ambiguous" for r in first.ambiguous)
        assert any(r.action == "incomplete_profile" for r in first.incomplete)

        created = await database[COLLECTION].find_one(
            {"normalized_name": normalize_customer_name(new_name)}
        )
        assert created is not None
        created_id = created["id"]
        assert created["phone"] == "081222"
        assert created["pic"] == "Ani"
        assert created["city"] == "Bekasi"
        assert created["migration_batch_id"] == first.batch_id
        assert created["created_by"] == "historical_migration"

        stored_existing = await database[COLLECTION].find_one({"id": existing_id})
        assert stored_existing["phone"] == "080000"
        assert stored_existing["address"] == "Alamat Master"

        order_new = await database.orders.find_one({"id": f"ord-new-{suffix}"})
        assert not order_new.get("customer_id")

        audit = await database[RUNS_COLLECTION].find_one(
            {"batch_id": first.batch_id, "status": "applied"}, {"_id": 0},
        )
        assert audit is not None
        assert created_id in (audit.get("created_ids") or [])
        assert audit["counts"]["created"] >= 1

        # Idempotent second apply — no duplicate create
        before_count = await database[COLLECTION].count_documents({})
        second = await apply_historical_customer_migration(
            database, owner_approved=True, create_backup=False,
        )
        batch_ids.append(second.batch_id)
        after_count = await database[COLLECTION].count_documents({})
        assert after_count == before_count
        assert len(second.created) == 0
        assert await database[COLLECTION].count_documents(
            {"normalized_name": normalize_customer_name(new_name)}
        ) == 1

        # Protect operational master created after migration (no batch stamp)
        ops_id = f"cust-ops-{suffix}"
        await database[COLLECTION].insert_one({
            "id": ops_id,
            "name": f"Toko Ops {suffix}",
            "normalized_name": normalize_customer_name(f"Toko Ops {suffix}"),
            "phone": "08777",
            "address": "Ops Addr",
            "created_by": "po_verify",
            "created_at": "2026-01-01T00:00:00+00:00",
            "updated_at": "2026-01-01T00:00:00+00:00",
        })

        # Mark migrated row as edited → selective rollback must skip it
        await database[COLLECTION].update_one(
            {"id": created_id},
            {"$set": {"updated_by": "ops_user", "updated_at": "2026-11-02T00:00:00+00:00", "phone": "081333"}},
        )

        # Create another migrated row that remains unmodified for successful rollback
        untouched_name = f"Toko Untouched {suffix}"
        await database.orders.insert_one({
            "id": f"ord-unt-{suffix}",
            "po_number": f"PO-UNT-{suffix}",
            "customer": untouched_name,
            "customer_phone": "081444",
            "address": "Jl Untouched",
            "cancelled": False,
        })
        third = await apply_historical_customer_migration(
            database, owner_approved=True, create_backup=False,
        )
        batch_ids.append(third.batch_id)
        untouched = next(r for r in third.created if r.display_name == untouched_name)
        untouched_id = untouched.proposed_id

        rb = await rollback_historical_customer_migration(
            database, third.batch_id, owner_approved=True,
        )
        assert untouched_id in rb.rolled_back
        assert await database[COLLECTION].find_one({"id": untouched_id}) is None

        # Modified first-batch row + ops row must survive a rollback of first batch
        rb1 = await rollback_historical_customer_migration(
            database, first.batch_id, owner_approved=True,
        )
        assert created_id not in rb1.rolled_back
        assert any(s.get("id") == created_id for s in rb1.rollback_skipped)
        assert await database[COLLECTION].find_one({"id": created_id}) is not None
        assert await database[COLLECTION].find_one({"id": ops_id}) is not None
        assert await database[COLLECTION].find_one({"id": existing_id}) is not None
    finally:
        await _cleanup(database, suffix, backups, batch_ids=batch_ids)
        client.close()


@pytest.mark.anyio
async def test_concurrent_apply_safe(monkeypatch):
    client, database = _client_db()
    suffix = uuid.uuid4().hex[:8]
    await ensure_sales_customer_indexes(database)
    monkeypatch.setenv(OWNER_APPROVAL_ENV, OWNER_APPROVAL_VALUE)
    backups = []
    batch_ids = []
    try:
        name = f"Toko Concurrent {suffix}"
        await database.orders.insert_one({
            "id": f"ord-conc-{suffix}",
            "po_number": f"PO-CONC-{suffix}",
            "customer": name,
            "customer_phone": "081555",
            "address": "Jl Concurrent",
            "cancelled": False,
        })

        async def _run():
            return await apply_historical_customer_migration(
                database, owner_approved=True, create_backup=False,
            )

        results = await asyncio.gather(_run(), _run())
        for r in results:
            batch_ids.append(r.batch_id)
            if r.backup_collection:
                backups.append(r.backup_collection)

        count = await database[COLLECTION].count_documents(
            {"normalized_name": normalize_customer_name(name)}
        )
        assert count == 1
        total_created = sum(len(r.created) for r in results)
        assert total_created == 1
        # Loser skipped via existing/race path
        assert any(
            any(s.action.startswith("skipped") for s in r.skipped) or len(r.created) == 0
            for r in results
        )
    finally:
        await _cleanup(database, suffix, backups, batch_ids=batch_ids)
        client.close()
