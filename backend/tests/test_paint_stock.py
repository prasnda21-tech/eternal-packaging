"""Sentralisasi Cat V1 — focused stock/eligibility/RBAC tests (isolated DB)."""
from __future__ import annotations

import asyncio
import os
import uuid

import pytest
from motor.motor_asyncio import AsyncIOMotorClient

from permissions import (
    OPT_IN_ONLY_PERMISSIONS,
    ROLE_DEFAULT_PERMISSIONS,
    effective_permissions,
)

MONGO_URL = os.environ.get("MONGO_URL", "mongodb://127.0.0.1:27017")
TEST_DB = "eternal_packaging_test_paint_stock_v1"

from services.paint_stock import (  # noqa: E402
    EXCLUDED_SUBJECT_ROLES,
    PAINT_BALANCES,
    PAINT_LEDGER,
    PAINT_MASTER,
    PaintConflictError,
    PaintValidationError,
    create_paint_master,
    ensure_indexes,
    list_eligible_employees,
    list_paint_branches,
    list_transactions,
    post_transaction,
    resolve_eligible_subject,
    summary_kpis,
    usage_report,
)

pytestmark = pytest.mark.anyio

PAINT_PERMS = {
    "paint.view",
    "paint.stock.view",
    "paint.stock.opening",
    "paint.stock.add",
    "paint.transaction.create",
    "paint.transaction.correct",
    "paint.report.view",
    "paint.report.export",
    "paint.settings.manage",
    "nav.production.paint",
}


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
async def db():
    assert "eternal_packaging_preview" not in TEST_DB
    client = AsyncIOMotorClient(MONGO_URL)
    database = client[TEST_DB]
    await client.drop_database(TEST_DB)
    await ensure_indexes(database)
    await database.branches.insert_one(
        {"id": "br-ygy", "code": "YGY", "name": "Yogyakarta", "active": True}
    )
    await database.branches.insert_one(
        {"id": "br-jkt", "code": "JKT", "name": "Jakarta", "active": True}
    )
    yield database
    await client.drop_database(TEST_DB)
    client.close()


def _actor(uid="actor-1", role="admin"):
    return {"id": uid, "name": "Petugas Cat", "email": "petugas@test.local", "role": role, "status": "active"}


async def _seed_users(db):
    users = [
        {"id": "u-op", "name": "Operator Satu", "email": "op@t.local", "role": "operator", "status": "active"},
        {"id": "u-sales", "name": "Sales Satu", "email": "sales@t.local", "role": "sales", "status": "active"},
        {"id": "u-hold", "name": "Holding User", "email": "hold@t.local", "role": "holding_team", "status": "active"},
        {"id": "u-dir", "name": "Direktur User", "email": "dir@t.local", "role": "direktur_utama", "status": "active"},
        {
            "id": "u-multi",
            "name": "Multi Role",
            "email": "multi@t.local",
            "role": "operator",
            "roles": ["operator", "holding_team"],
            "status": "active",
        },
        {"id": "u-inact", "name": "Inactive", "email": "inact@t.local", "role": "helper", "status": "inactive"},
        {"id": "u-crm", "name": "CRM User", "email": "crm@t.local", "role": "crm", "status": "active"},
    ]
    await db.users.insert_many(users)
    await db.hr_employees.insert_many(
        [
            {"id": "hr-op", "user_id": "u-op", "name": "Operator Satu", "division": "Produksi", "active": True},
            {"id": "hr-sales", "user_id": "u-sales", "name": "Sales Satu", "division": "Sales", "active": True},
        ]
    )


async def _paint(db, code="MERAH", name="Merah", minimum=100):
    return await create_paint_master(
        db,
        {"code": code, "name": name, "minimum_stock_grams": minimum},
        _actor(),
    )


def _key(prefix="k"):
    return f"{prefix}-{uuid.uuid4()}"


async def test_paint_permissions_default_off():
    for perm in PAINT_PERMS:
        assert perm in OPT_IN_ONLY_PERMISSIONS
    for role, perms in ROLE_DEFAULT_PERMISSIONS.items():
        overlap = set(perms) & PAINT_PERMS
        assert not overlap, f"{role} unexpectedly grants {overlap}"
    admin_eff = effective_permissions("admin", {})
    assert not (admin_eff & PAINT_PERMS)


async def test_opening_add_take_return_adjust_and_idempotency(db):
    await _seed_users(db)
    paint = await _paint(db)
    actor = _actor()
    pid = paint["paint_id"]

    opening = await post_transaction(
        db,
        {
            "type": "OPENING",
            "paint_id": pid,
            "branch_id": "br-ygy",
            "quantity_grams": 1000,
            "idempotency_key": _key("open"),
        },
        actor,
    )
    assert opening["type"] == "OPENING"

    with pytest.raises(PaintConflictError):
        await post_transaction(
            db,
            {
                "type": "OPENING",
                "paint_id": pid,
                "branch_id": "br-ygy",
                "quantity_grams": 500,
                "idempotency_key": _key("open2"),
            },
            actor,
        )

    add_key = _key("add")
    add1 = await post_transaction(
        db,
        {
            "type": "ADD",
            "paint_id": pid,
            "branch_id": "br-ygy",
            "quantity_grams": 200,
            "idempotency_key": add_key,
        },
        actor,
    )
    add2 = await post_transaction(
        db,
        {
            "type": "ADD",
            "paint_id": pid,
            "branch_id": "br-ygy",
            "quantity_grams": 200,
            "idempotency_key": add_key,
        },
        actor,
    )
    assert add1["transaction_id"] == add2["transaction_id"]

    take = await post_transaction(
        db,
        {
            "type": "TAKE",
            "paint_id": pid,
            "branch_id": "br-ygy",
            "quantity_grams": 300,
            "employee_user_id": "u-op",
            "idempotency_key": _key("take"),
            "reference": "PO-1",
        },
        actor,
    )
    assert take["employee_user_id"] == "u-op"
    assert take["actor_user_id"] == "actor-1"
    assert take["employee_name_snapshot"] == "Operator Satu"

    ret = await post_transaction(
        db,
        {
            "type": "RETURN",
            "paint_id": pid,
            "branch_id": "br-ygy",
            "quantity_grams": 100,
            "employee_user_id": "u-op",
            "idempotency_key": _key("ret"),
        },
        actor,
    )
    assert ret["type"] == "RETURN"

    with pytest.raises(PaintValidationError):
        await post_transaction(
            db,
            {
                "type": "RETURN",
                "paint_id": pid,
                "branch_id": "br-ygy",
                "quantity_grams": 250,
                "employee_user_id": "u-op",
                "idempotency_key": _key("ret-over"),
            },
            actor,
        )

    await post_transaction(
        db,
        {
            "type": "ADJUST",
            "paint_id": pid,
            "branch_id": "br-ygy",
            "quantity_grams": -50,
            "notes": "Koreksi timbang",
            "idempotency_key": _key("adj"),
        },
        actor,
    )

    bal = await db[PAINT_BALANCES].find_one({"paint_id": pid, "branch_id": "br-ygy"}, {"_id": 0})
    # 1000 + 200 - 300 + 100 - 50 = 950
    assert int(bal["quantity_grams"]) == 950

    with pytest.raises(PaintValidationError):
        await post_transaction(
            db,
            {
                "type": "TAKE",
                "paint_id": pid,
                "branch_id": "br-ygy",
                "quantity_grams": 5000,
                "employee_user_id": "u-op",
                "idempotency_key": _key("take-big"),
            },
            actor,
        )

    report = await usage_report(db, actor, branch_id="br-ygy")
    assert report["totals"]["take_grams"] == 300
    assert report["totals"]["return_grams"] == 100
    assert report["totals"]["net_usage_grams"] == 200
    assert any(r["employee_user_id"] == "u-op" for r in report["items"])


async def test_employee_eligibility_filters(db):
    await _seed_users(db)
    listed = await list_eligible_employees(db, q="", page=1, page_size=100)
    ids = {x["employee_user_id"] for x in listed["items"]}
    assert "u-op" in ids
    assert "u-sales" in ids
    assert "u-crm" in ids
    assert "u-hold" not in ids
    assert "u-dir" not in ids
    assert "u-multi" not in ids
    assert "u-inact" not in ids

    await resolve_eligible_subject(db, "u-op")
    with pytest.raises(PaintValidationError):
        await resolve_eligible_subject(db, "u-hold")
    with pytest.raises(PaintValidationError):
        await resolve_eligible_subject(db, "u-dir")
    with pytest.raises(PaintValidationError):
        await resolve_eligible_subject(db, "u-multi")
    with pytest.raises(PaintValidationError):
        await resolve_eligible_subject(db, "u-inact")
    with pytest.raises(PaintValidationError):
        await resolve_eligible_subject(db, "u-forged-missing")


async def test_forged_employee_rejected_on_take(db):
    await _seed_users(db)
    paint = await _paint(db)
    await post_transaction(
        db,
        {
            "type": "OPENING",
            "paint_id": paint["paint_id"],
            "branch_id": "br-ygy",
            "quantity_grams": 500,
            "idempotency_key": _key("o"),
        },
        _actor(),
    )
    with pytest.raises(PaintValidationError):
        await post_transaction(
            db,
            {
                "type": "TAKE",
                "paint_id": paint["paint_id"],
                "branch_id": "br-ygy",
                "quantity_grams": 10,
                "employee_user_id": "forged-id",
                "idempotency_key": _key("t"),
            },
            _actor(),
        )
    with pytest.raises(PaintValidationError):
        await post_transaction(
            db,
            {
                "type": "TAKE",
                "paint_id": paint["paint_id"],
                "branch_id": "br-ygy",
                "quantity_grams": 10,
                "employee_user_id": "u-hold",
                "idempotency_key": _key("t2"),
            },
            _actor(),
        )


async def test_cross_branch_isolation_and_get_readonly(db):
    await _seed_users(db)
    paint = await _paint(db)
    actor_ygy = {
        **_actor("actor-ygy"),
        "branch_ids": ["br-ygy"],
        "role": "operator",
    }
    # Force non-global scope in local runtime by assigning branch_ids
    os.environ.setdefault("APP_ENV", "test")

    await post_transaction(
        db,
        {
            "type": "OPENING",
            "paint_id": paint["paint_id"],
            "branch_id": "br-ygy",
            "quantity_grams": 400,
            "idempotency_key": _key("oy"),
        },
        _actor(),  # global actor seeds YGY
    )
    await post_transaction(
        db,
        {
            "type": "OPENING",
            "paint_id": paint["paint_id"],
            "branch_id": "br-jkt",
            "quantity_grams": 800,
            "idempotency_key": _key("oj"),
        },
        _actor(),
    )

    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc:
        await post_transaction(
            db,
            {
                "type": "ADD",
                "paint_id": paint["paint_id"],
                "branch_id": "br-jkt",
                "quantity_grams": 10,
                "idempotency_key": _key("xj"),
            },
            actor_ygy,
        )
    assert exc.value.status_code == 403

    before = await db[PAINT_LEDGER].count_documents({})
    await list_transactions(db, actor_ygy, branch_id="br-ygy")
    await summary_kpis(db, actor_ygy, branch_id="br-ygy")
    after = await db[PAINT_LEDGER].count_documents({})
    assert before == after


async def test_concurrent_take_cannot_go_negative(db):
    await _seed_users(db)
    paint = await _paint(db)
    pid = paint["paint_id"]
    await post_transaction(
        db,
        {
            "type": "OPENING",
            "paint_id": pid,
            "branch_id": "br-ygy",
            "quantity_grams": 100,
            "idempotency_key": _key("oc"),
        },
        _actor(),
    )

    async def take_once(i: int):
        try:
            await post_transaction(
                db,
                {
                    "type": "TAKE",
                    "paint_id": pid,
                    "branch_id": "br-ygy",
                    "quantity_grams": 60,
                    "employee_user_id": "u-op",
                    "idempotency_key": _key(f"ct{i}"),
                },
                _actor(),
            )
            return "ok"
        except PaintValidationError:
            return "reject"
        except Exception:
            return "err"

    results = await asyncio.gather(*[take_once(i) for i in range(4)])
    assert results.count("ok") == 1
    bal = await db[PAINT_BALANCES].find_one({"paint_id": pid, "branch_id": "br-ygy"}, {"_id": 0})
    assert int(bal["quantity_grams"]) == 40
    assert int(bal["quantity_grams"]) >= 0


async def test_adjust_requires_reason_and_append_only(db):
    paint = await _paint(db)
    await post_transaction(
        db,
        {
            "type": "OPENING",
            "paint_id": paint["paint_id"],
            "branch_id": "br-ygy",
            "quantity_grams": 100,
            "idempotency_key": _key("oa"),
        },
        _actor(),
    )
    with pytest.raises(PaintValidationError):
        await post_transaction(
            db,
            {
                "type": "ADJUST",
                "paint_id": paint["paint_id"],
                "branch_id": "br-ygy",
                "quantity_grams": -10,
                "idempotency_key": _key("adj0"),
            },
            _actor(),
        )
    adj = await post_transaction(
        db,
        {
            "type": "ADJUST",
            "paint_id": paint["paint_id"],
            "branch_id": "br-ygy",
            "quantity_grams": -10,
            "notes": "Alasan wajib",
            "idempotency_key": _key("adj1"),
        },
        _actor(),
    )
    # Append-only: no update/delete API — ledger row remains
    stored = await db[PAINT_LEDGER].find_one({"transaction_id": adj["transaction_id"]}, {"_id": 0})
    assert stored["notes"] == "Alasan wajib"
    count = await db[PAINT_LEDGER].count_documents({"paint_id": paint["paint_id"]})
    assert count == 2


async def test_no_r2_finance_collections_touched(db):
    await _seed_users(db)
    paint = await _paint(db)
    await post_transaction(
        db,
        {
            "type": "OPENING",
            "paint_id": paint["paint_id"],
            "branch_id": "br-ygy",
            "quantity_grams": 50,
            "idempotency_key": _key("nr"),
        },
        _actor(),
    )
    for coll in (
        "physical_stock_ledger",
        "physical_stock_balances",
        "journals",
        "accurate_sync_queue",
        "purchase_orders",
    ):
        assert await db[coll].count_documents({}) == 0


async def test_excluded_roles_constant():
    assert EXCLUDED_SUBJECT_ROLES == frozenset({"holding_team", "direktur_utama"})


async def test_list_paint_branches_scoped(db):
    all_scope = await list_paint_branches(db, _actor())
    assert {b["id"] for b in all_scope["items"]} == {"br-ygy", "br-jkt"}
    assert all(b.get("code") and b.get("name") for b in all_scope["items"])

    limited = await list_paint_branches(
        db, {"id": "u-lim", "role": "operator", "status": "active", "branch_ids": ["br-ygy"]}
    )
    assert [b["id"] for b in limited["items"]] == ["br-ygy"]


async def test_list_transactions_filter_sort_before_pagination(db):
    await _seed_users(db)
    paint = await _paint(db)
    actor = _actor()
    for i, notes in enumerate(["alpha-one", "beta-two", "alpha-three", "gamma"]):
        await post_transaction(
            db,
            {
                "type": "ADD",
                "paint_id": paint["paint_id"],
                "branch_id": "br-ygy",
                "quantity_grams": 10 + i,
                "notes": notes,
                "idempotency_key": _key(f"s{i}"),
            },
            actor,
        )
    # seed opening first may be required - ADD needs stock? ADD can add without opening in this ledger
    filtered = await list_transactions(
        db,
        actor,
        branch_id="br-ygy",
        hf=["notes:contains:alpha"],
        sort_by="quantity_grams",
        sort_dir="asc",
        page=1,
        page_size=1,
    )
    assert filtered["total"] == 2
    assert len(filtered["items"]) == 1
    assert "alpha" in (filtered["items"][0].get("notes") or "")
    assert filtered["items"][0]["quantity_grams"] in (10, 12)
