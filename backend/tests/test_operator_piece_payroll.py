"""Piece-wage payroll: one source, one bonus range, one kasbon reservation.

Uses the local test database only. Does not call production.
"""
from __future__ import annotations

import asyncio
import multiprocessing
import os
import sys
import uuid
from datetime import date
from pathlib import Path

import httpx
import pytest
from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("MONGO_URL", "mongodb://127.0.0.1:27017")
os.environ["DB_NAME"] = "eternal_packaging_piece_payroll_test"
os.environ.setdefault("JWT_SECRET", "test-secret-key-12345678901234567890")
os.environ.setdefault("CORS_ORIGINS", "*")
os.environ.setdefault("STORAGE_PROVIDER", "local")

import server
from operator_analytics import wib_today
from permissions import ROLE_DEFAULT_PERMISSIONS, with_nav_defaults
from server import app, current_user
from services.cash_bank_master import create_cash_bank, ensure_indexes as ensure_cash_bank
from services.chart_of_accounts import create_account, ensure_indexes as ensure_coa
from services.hr_payroll import (
    ATTENDANCE,
    COMPONENTS,
    EMPLOYEES,
    KASBON,
    LINES,
    PERIODS,
    STATUS_APPROVAL,
    STATUS_DRAFT,
    STATUS_FINAL,
    STATUS_REVIEW,
    ensure_indexes,
    to_money,
)
from services.journal_core import ensure_indexes as ensure_journal_indexes
from services.operator_payroll import SETTINGS_KEY as WAGE_KEY
from services.operator_wage_claims import (
    BONUS_DAYS,
    INTEGRATION_KEY,
    WAGE_CLAIMS,
    calculate_piece_period,
)
from services.payroll_accounting_settings import SETTINGS_KEY as MAP_KEY

ROOT = Path(__file__).resolve().parents[2]


def _cross_process_calculate(period_id, mongo_url, db_name):
    os.environ["MONGO_URL"] = mongo_url
    os.environ["DB_NAME"] = db_name
    os.environ.setdefault("JWT_SECRET", "test-secret-key-12345678901234567890")
    os.environ.setdefault("CORS_ORIGINS", "*")
    os.environ.setdefault("STORAGE_PROVIDER", "local")

    async def _run():
        from motor.motor_asyncio import AsyncIOMotorClient
        import server as srv
        from services.hr_payroll import get_period
        from services.operator_wage_claims import calculate_piece_period as calc

        client = AsyncIOMotorClient(mongo_url)
        db = client[db_name]
        srv.db = db
        srv.client = client
        user = {"id": "piece-proc", "name": "Proc", "role": "admin", "email": "piece-proc@example.com"}
        try:
            period = await get_period(db, period_id)
            await calc(db, period, user)
            return "ok"
        except HTTPException as exc:
            return f"http:{exc.status_code}"
        except Exception as exc:
            return f"err:{type(exc).__name__}"
        finally:
            client.close()

    return asyncio.run(_run())


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture(autouse=True)
async def bind_and_clean():
    from motor.motor_asyncio import AsyncIOMotorClient

    server.client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    server.db = server.client[os.environ["DB_NAME"]]
    for name in (
        EMPLOYEES, COMPONENTS, ATTENDANCE, KASBON, PERIODS, LINES,
        WAGE_CLAIMS, BONUS_DAYS, "orders", "journal_entries", "journal_lines",
        "files", "hr_payroll_payments", "hr_payroll_payment_lines",
        "cash_bank_accounts", "cash_bank_payments", "accounting_periods",
    ):
        await server.db[name].delete_many({})
    await server.db.app_settings.delete_many({"key": {"$in": [INTEGRATION_KEY, WAGE_KEY, MAP_KEY, "hr_payroll_settings"]}})
    await server.db.role_permissions.delete_many({"role": "holding_team"})
    await ensure_indexes(server.db)
    await ensure_coa(server.db)
    await ensure_journal_indexes(server.db)
    await ensure_cash_bank(server.db)
    yield
    app.dependency_overrides.pop(current_user, None)


def _admin():
    user = {"id": "piece-admin", "email": "piece-admin@example.com", "name": "Admin Piece", "role": "admin"}
    app.dependency_overrides[current_user] = lambda: user
    return user


def _client():
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


def _day(n: int) -> str:
    today = wib_today()
    year = today.year if today.month > 1 else today.year - 1
    month = today.month - 1 if today.month > 1 else 12
    return date(year, month, n).isoformat()


async def _wage_config():
    await server.db.app_settings.update_one(
        {"key": WAGE_KEY},
        {"$set": {
            "key": WAGE_KEY,
            "default_rate_per_pcs": 100,
            "bonus": {"mode": "once_per_period", "target_qty": 10000, "amount": 100000, "active": True},
        }},
        upsert=True,
    )


async def _person(c, *, name, basis="piece", base=5_000_000):
    uid = f"u-{uuid.uuid4().hex[:8]}"
    await server.db.users.insert_one({
        "id": uid, "email": f"{uid}@example.com", "name": name, "role": "operator", "status": "active",
    })
    created = await c.post("/api/hr-payroll/employees", json={
        "name": name,
        "employee_number": f"NIK-{uid[-6:]}",
        "user_id": uid,
        "role": "operator",
        "division": "operator",
        "pay_basis": basis,
        "salary_profile": {"base_salary": base, "allowances": [{"code": "MAKAN", "name": "Uang Makan", "amount": 300000}]},
    })
    assert created.status_code == 200, created.text
    return created.json()["employee"], uid


async def _output(db, *, user_id, qty, work_date, rate=100, action_id=None, legacy=False, name="Op", user_on_split=True):
    order_id = f"ord-{uuid.uuid4().hex[:8]}"
    split_id = f"spl-{uuid.uuid4().hex[:8]}"
    action = action_id or f"act-{uuid.uuid4().hex[:8]}"
    entry = {
        "id": str(uuid.uuid4()),
        "action_id": "legacy-operator-old" if legacy else action,
        "qty": qty,
        "stage": "operator",
        "po_id": order_id,
        "split_id": split_id,
        "legacy": True if legacy else False,
    }
    if work_date:
        entry["work_date"] = work_date
    if rate is not None:
        entry["rate_per_pcs"] = rate
    if user_id and not legacy:
        entry["operator_user_id"] = user_id
    split = {
        "id": split_id,
        "operator_name": name,
        "operator_email": f"{user_id or 'none'}@example.com",
        "quantity": max(int(qty or 0), 1),
        "line_idx": 0,
        "progress_entries": [entry],
    }
    if user_on_split and user_id:
        split["operator_user_id"] = user_id
    await db.orders.insert_one({
        "id": order_id,
        "po_number": f"PO-{order_id[-4:]}",
        "operator_splits": [split],
    })
    return {"order_id": order_id, "split_id": split_id, "action_id": entry["action_id"], "source_key": f"{order_id}:{split_id}:{entry['action_id']}"}


async def _kasbon(employee_id, outstanding=1_000_000, installment=999_999):
    doc = {
        "id": str(uuid.uuid4()),
        "request_id": str(uuid.uuid4()),
        "request_number": f"PKY-P-{uuid.uuid4().hex[:6]}",
        "employee_id": employee_id,
        "outstanding": outstanding,
        "original_amount": outstanding,
        "installment_amount": installment,
        "paid_total": 0,
        "payments": [],
        "active": True,
        "mode": "installment_fixed",
        "created_at": "2026-09-01T00:00:00+00:00",
    }
    await server.db[KASBON].insert_one(doc)
    return doc


async def _integrate(c, start):
    saved = await c.put("/api/hr-payroll/piece-integration", json={"start_date": start})
    assert saved.status_code == 200, saved.text
    return saved.json()["integration"]


async def _piece(c, start, end, bonus_start, bonus_end, kasbon_inputs=None, adjustments=None):
    created = await c.post("/api/hr-payroll/piece-periods", json={"start_date": start, "end_date": end})
    assert created.status_code == 200, created.text
    period = created.json()["period"]
    saved = await c.put(f"/api/hr-payroll/periods/{period['id']}/piece-inputs", json={
        "bonus": {"start_date": bonus_start, "end_date": bonus_end},
        "kasbon_inputs": kasbon_inputs or [],
        "bonus_adjustments": adjustments or [],
    })
    assert saved.status_code == 200, saved.text
    return saved.json()["period"]


async def _calc(c, period_id):
    res = await c.post(f"/api/hr-payroll/periods/{period_id}/calculate")
    return res


async def _accounts():
    suffix = uuid.uuid4().hex[:6]

    async def one(prefix, name, account_type):
        return await create_account(
            server.db,
            {"code": f"{prefix}{suffix}", "name": name, "account_type": account_type},
            user={"id": "t", "name": "t", "role": "admin"},
        )

    return {
        "payable": await one("21", "Utang Gaji", "OCLY"),
        "wage": await one("51", "Beban Upah Borongan", "EXPS"),
        "bonus": await one("52", "Beban Bonus Operator", "EXPS"),
        "kasbon": await one("13", "Piutang Kasbon", "AREC"),
        "bank": await one("11", "Bank Operasional", "BANK"),
    }


async def _map(c, accounts):
    saved = await c.put("/api/settings/payroll-accounting", json={
        "payroll_payable_account_id": accounts["payable"]["id"],
        "component_mappings": {
            "operator_piece:UPAH_BORONGAN": {"treatment": "expense_debit", "account_id": accounts["wage"]["id"]},
            "operator_bonus:BONUS_OPERATOR": {"treatment": "expense_debit", "account_id": accounts["bonus"]["id"]},
            "kasbon:KASBON": {"treatment": "asset_credit", "account_id": accounts["kasbon"]["id"]},
            "salary_profile:BASE": {"treatment": "expense_debit", "account_id": accounts["wage"]["id"]},
        },
    })
    assert saved.status_code == 200, saved.text


async def _to(c, period_id, *targets):
    period = None
    for target in targets:
        moved = await c.post(f"/api/hr-payroll/periods/{period_id}/transition", json={"to_status": target})
        assert moved.status_code == 200, moved.text
        period = moved.json()["period"]
    return period


def _pdf_text(data: bytes) -> str:
    import base64
    import zlib
    start = data.find(b"stream\n")
    end = data.find(b"endstream")
    raw = data[start + 7:end].strip()
    return zlib.decompress(base64.a85decode(raw, adobe=True)).decode("latin1")


def _run_two(period_ids):
    ctx = multiprocessing.get_context("spawn")
    with ctx.Pool(len(period_ids)) as pool:
        return pool.starmap(
            _cross_process_calculate,
            [(pid, os.environ["MONGO_URL"], os.environ["DB_NAME"]) for pid in period_ids],
        )


def _hard_stop_after_partial_reservation(period_id, employee_id, source, bonus_dates, schedule, amount, mongo_url, db_name):
    """Commit a partial reservation, then die without except, finally, or rollback."""
    os.environ["MONGO_URL"] = mongo_url
    os.environ["DB_NAME"] = db_name
    os.environ.setdefault("JWT_SECRET", "test-secret-key-12345678901234567890")
    os.environ.setdefault("CORS_ORIGINS", "*")
    os.environ.setdefault("STORAGE_PROVIDER", "local")

    async def _run():
        from decimal import Decimal
        from motor.motor_asyncio import AsyncIOMotorClient
        import server as srv
        from services.hr_payroll import get_period
        from services.operator_wage_claims import (
            _claim_bonus_day,
            _claim_wage,
            ensure_piece_indexes,
            reserve_kasbon,
        )

        client = AsyncIOMotorClient(mongo_url)
        db = client[db_name]
        srv.db = db
        srv.client = client
        await ensure_piece_indexes(db)
        period = await get_period(db, period_id)
        state, _row = await _claim_wage(db, period, source, "worker-lama")
        if state != "inserted":
            await db.app_settings.insert_one({"key": "hard-stop-error", "value": f"wage:{state}"})
            return
        for day in bonus_dates:
            state, _row = await _claim_bonus_day(db, period, employee_id, day, "worker-lama")
            if state != "inserted":
                await db.app_settings.insert_one({"key": "hard-stop-error", "value": f"bonus:{state}"})
                return
        reserved = await reserve_kasbon(
            db,
            schedule["id"],
            period_id,
            Decimal(str(amount)),
            {
                "schedule_id": schedule["id"],
                "request_id": schedule.get("request_id"),
                "request_number": schedule.get("request_number"),
            },
            attempt_id="worker-lama",
        )
        if not reserved["ok"]:
            await db.app_settings.insert_one({"key": "hard-stop-error", "value": "kasbon"})
            return
        os._exit(1)

    return asyncio.run(_run())


@pytest.mark.anyio
async def test_monthly_legacy_stays_and_piece_has_no_base():
    _admin()
    await _wage_config()
    async with _client() as c:
        monthly, _ = await _person(c, name="Siti Bulanan", basis="monthly", base=5_000_000)
        piece, uid = await _person(c, name="Budi Borongan", basis="piece", base=9_000_000)
        await _output(server.db, user_id=uid, qty=12000, work_date=_day(3), rate=100)
        await _integrate(c, _day(1))
        created = await c.post("/api/hr-payroll/periods", json={
            "year": int(_day(1)[:4]), "month": int(_day(1)[5:7]),
            "start_date": _day(1), "end_date": _day(28),
        })
        assert created.status_code == 200, created.text
        monthly_period = created.json()["period"]
        calc = await _calc(c, monthly_period["id"])
        assert calc.status_code == 200, calc.text
        lines = calc.json()["lines"]
        assert {row["employee_id"] for row in lines} == {monthly["id"]}
        codes = {item["code"] for item in lines[0]["earnings"]}
        assert "BASE" in codes
        assert "UPAH_BORONGAN" not in codes
        assert to_money(lines[0]["gross"]) >= to_money(5_000_000)
        legacy = {
            "id": str(uuid.uuid4()),
            "group_id": str(uuid.uuid4()),
            "period_number": f"PRP-LEGACY-{uuid.uuid4().hex[:6]}",
            "year": 2088, "month": 1, "revision": 1,
            "start_date": "2088-01-01", "end_date": "2088-01-31",
            "status": STATUS_DRAFT,
            "calculation_version": 0,
            "config_snapshot": monthly_period["config_snapshot"],
            "audit_events": [],
            "created_at": "2088-01-01T00:00:00+00:00",
            "updated_at": "2088-01-01T00:00:00+00:00",
        }
        await server.db[PERIODS].insert_one(dict(legacy))
        legacy_calc = await _calc(c, legacy["id"])
        assert legacy_calc.status_code == 200, legacy_calc.text
        assert any(item["code"] == "BASE" for item in legacy_calc.json()["lines"][0]["earnings"])
        from scripts.migrate_piece_payroll_indexes import run as migrate_run
        await migrate_run(server.db, apply=True)
        piece_period = await _piece(c, _day(1), _day(7), _day(1), _day(20))
        piece_calc = await _calc(c, piece_period["id"])
        assert piece_calc.status_code == 200, piece_calc.text
        prow = piece_calc.json()["lines"][0]
        earned = {item["code"]: item for item in prow["earnings"]}
        assert set(earned) == {"UPAH_BORONGAN", "BONUS_OPERATOR"}
        assert "BASE" not in earned and "MAKAN" not in earned
        assert to_money(earned["UPAH_BORONGAN"]["amount"]) == to_money(1_200_000)
        assert prow["employee_id"] == piece["id"]


async def _force_legacy_monthly_index():
    import scripts.migrate_piece_payroll_indexes as mig

    info = await server.db[PERIODS].index_information()
    partial = mig.partial_monthly_index(info)
    if partial:
        await server.db[PERIODS].drop_index(partial)
    if not mig.legacy_monthly_index(await server.db[PERIODS].index_information()):
        await server.db[PERIODS].create_index(
            [("year", 1), ("month", 1), ("revision", 1)], unique=True, name="year_1_month_1_revision_1",
        )


@pytest.mark.anyio
async def test_two_piece_periods_in_one_month_after_migration():
    from scripts.migrate_piece_payroll_indexes import run as migrate_run
    from services.hr_payroll import _legacy_monthly_index_name

    _admin()
    await _wage_config()
    await _force_legacy_monthly_index()
    async with _client() as c:
        _emp, uid = await _person(c, name="Dua Periode")
        await _output(server.db, user_id=uid, qty=100, work_date=_day(3), rate=100, action_id="act-a")
        await _output(server.db, user_id=uid, qty=100, work_date=_day(10), rate=100, action_id="act-b")
        await _integrate(c, _day(1))
        first = await _piece(c, _day(1), _day(7), _day(1), _day(7))
        blocked = await c.post("/api/hr-payroll/piece-periods", json={"start_date": _day(8), "end_date": _day(14)})
        assert blocked.status_code == 409, blocked.text
        assert "migrate_piece_payroll_indexes" in blocked.text
        await migrate_run(server.db, apply=True)
        second = await c.post("/api/hr-payroll/piece-periods", json={"start_date": _day(8), "end_date": _day(14)})
        assert second.status_code == 200, second.text
        monthly = await c.post("/api/hr-payroll/periods", json={
            "year": int(_day(1)[:4]), "month": int(_day(1)[5:7]),
            "start_date": _day(1), "end_date": _day(28),
        })
        assert monthly.status_code == 200, monthly.text
        again = await c.post("/api/hr-payroll/periods", json={
            "year": int(_day(1)[:4]), "month": int(_day(1)[5:7]),
            "start_date": _day(1), "end_date": _day(28),
        })
        assert again.status_code == 409
        assert first["id"] != second.json()["period"]["id"]
        assert first["month"] == second.json()["period"]["month"]
        await ensure_indexes(server.db)
        assert await _legacy_monthly_index_name(server.db) is None
        third = await c.post("/api/hr-payroll/piece-periods", json={"start_date": _day(15), "end_date": _day(16)})
        assert third.status_code == 200, third.text
        assert third.json()["period"]["month"] == first["month"]


@pytest.mark.anyio
async def test_recalc_replaces_and_two_processes_keep_one_owner():
    from scripts.migrate_piece_payroll_indexes import run as migrate_run

    _admin()
    await _wage_config()
    await migrate_run(server.db, apply=True)
    async with _client() as c:
        _emp, uid = await _person(c, name="Satu Sumber")
        src = await _output(server.db, user_id=uid, qty=12000, work_date=_day(3), rate=100, action_id="act-race")
        await _integrate(c, _day(1))
        period = await _piece(c, _day(1), _day(7), _day(8), _day(14))
        first = await _calc(c, period["id"])
        assert first.status_code == 200, first.text
        assert len(first.json()["lines"]) == 1
        assert to_money(first.json()["lines"][0]["earnings"][0]["amount"]) == to_money(1_200_000)
        await server.db.orders.update_one(
            {"id": src["order_id"], "operator_splits.id": src["split_id"]},
            {"$set": {"operator_splits.$.progress_entries.0.qty": 1000, "operator_splits.$.progress_entries.0.rate_per_pcs": 100}},
        )
        second = await _calc(c, period["id"])
        assert second.status_code == 200, second.text
        assert len(second.json()["lines"]) == 1
        assert to_money(second.json()["lines"][0]["net"]) == to_money(100_000)
        assert await server.db[LINES].count_documents({"period_id": period["id"]}) == 1
        assert await server.db[WAGE_CLAIMS].count_documents({"source_key": src["source_key"], "status": {"$in": ["held", "posted"]}}) == 1
        await server.db.orders.update_one(
            {"id": src["order_id"], "operator_splits.id": src["split_id"]},
            {"$set": {"operator_splits.$.progress_entries.0.qty": 12000}},
        )
        other = await _piece(c, _day(1), _day(7), _day(15), _day(20))
        results = _run_two([period["id"], other["id"]])
    assert sorted(results) == ["http:409", "ok"], results
    assert await server.db[WAGE_CLAIMS].count_documents({"source_key": src["source_key"], "status": {"$in": ["held", "posted"]}}) == 1


@pytest.mark.anyio
async def test_bonus_adjustment_overlap_and_unfinished_range():
    from scripts.migrate_piece_payroll_indexes import run as migrate_run

    _admin()
    await _wage_config()
    await migrate_run(server.db, apply=True)
    async with _client() as c:
        emp, uid = await _person(c, name="Bonus")
        await _output(server.db, user_id=uid, qty=12000, work_date=_day(3), rate=100, action_id="act-bonus-wage")
        await _output(server.db, user_id=uid, qty=500, work_date=_day(12), rate=100, action_id="act-bonus-only")
        await _integrate(c, _day(1))
        period = await _piece(c, _day(1), _day(7), _day(1), _day(14))
        calc = await _calc(c, period["id"])
        assert calc.status_code == 200, calc.text
        snap = calc.json()["lines"][0]["bonus_snapshot"]
        assert snap["base_qty"] == 12500
        assert to_money(snap["proposed_amount"]) == to_money(100_000)
        assert snap["start_date"] == _day(1) and snap["end_date"] == _day(14)
        bare = await c.put(f"/api/hr-payroll/periods/{period['id']}/piece-inputs", json={
            "bonus_adjustments": [{"employee_id": emp["id"], "final_amount": 150000, "reason": ""}],
        })
        assert bare.status_code == 200, bare.text
        rejected = await _calc(c, period["id"])
        assert rejected.status_code == 409
        assert "Alasan" in rejected.text
        assert await server.db[WAGE_CLAIMS].count_documents({"period_id": period["id"], "status": "held"}) == 1
        reasoned = await c.put(f"/api/hr-payroll/periods/{period['id']}/piece-inputs", json={
            "bonus_adjustments": [{"employee_id": emp["id"], "final_amount": 150000, "reason": "penyesuaian holding"}],
        })
        assert reasoned.status_code == 200, reasoned.text
        accepted = await _calc(c, period["id"])
        assert accepted.status_code == 200, accepted.text
        stored = accepted.json()["lines"][0]["bonus_snapshot"]
        assert to_money(stored["final_amount"]) == to_money(150_000)
        assert stored["reason"] == "penyesuaian holding"
        assert stored["actor"]["id"] == "piece-admin"
        left = await _piece(c, _day(8), _day(14), _day(10), _day(16))
        clash = await _calc(c, left["id"])
        assert clash.status_code == 409
        assert "bertabrakan" in clash.text
        open_bonus = await _piece(c, _day(15), _day(20), _day(15), wib_today().isoformat())
        await _output(server.db, user_id=uid, qty=100, work_date=_day(16), rate=100, action_id="act-open")
        ready = await _calc(c, open_bonus["id"])
        assert ready.status_code == 200, ready.text
        accounts = await _accounts()
        await _map(c, accounts)
        await _to(c, open_bonus["id"], STATUS_REVIEW, STATUS_APPROVAL)
        final = await c.post(f"/api/hr-payroll/periods/{open_bonus['id']}/transition", json={"to_status": STATUS_FINAL})
        assert final.status_code == 409
        assert "selesai" in final.text
        emp2, uid2 = await _person(c, name="Race Bonus")
        await _output(server.db, user_id=uid2, qty=10, work_date=_day(2), rate=100, action_id="act-race-a")
        await _output(server.db, user_id=uid2, qty=10, work_date=_day(18), rate=100, action_id="act-race-b")
        race_a = await _piece(c, _day(1), _day(2), _day(1), _day(5))
        race_b = await _piece(c, _day(18), _day(19), _day(4), _day(8))
        raced = _run_two([race_a["id"], race_b["id"]])
    assert sorted(raced) == ["http:409", "ok"], raced
    owners = await server.db[BONUS_DAYS].distinct("period_id", {"employee_id": emp2["id"], "bonus_date": _day(4), "status": {"$in": ["held", "posted"]}})
    assert len(owners) == 1
    held_counts = []
    for period_id in (race_a["id"], race_b["id"]):
        held_counts.append(await server.db[BONUS_DAYS].count_documents({"period_id": period_id, "status": {"$in": ["held", "posted"]}}))
    assert sorted(held_counts)[0] == 0
    assert max(held_counts) >= 1


@pytest.mark.anyio
async def test_kasbon_zero_valid_invalid_and_two_period_reservation():
    from scripts.migrate_piece_payroll_indexes import run as migrate_run

    _admin()
    await _wage_config()
    await migrate_run(server.db, apply=True)
    async with _client() as c:
        emp, uid = await _person(c, name="Kasbon")
        await _output(server.db, user_id=uid, qty=12000, work_date=_day(3), rate=100)
        schedule = await _kasbon(emp["id"], outstanding=1_000_000, installment=999_999)
        await _integrate(c, _day(1))
        zero = await _piece(c, _day(1), _day(7), _day(1), _day(7), kasbon_inputs=[{
            "employee_id": emp["id"], "schedule_id": schedule["id"], "amount": 0,
        }])
        zero_calc = await _calc(c, zero["id"])
        assert zero_calc.status_code == 200, zero_calc.text
        assert zero_calc.json()["lines"][0]["deductions"] == []
        assert to_money(zero_calc.json()["lines"][0]["net"]) == to_money(1_300_000)
        stored = await server.db[KASBON].find_one({"id": schedule["id"]}, {"_id": 0})
        assert stored["payments"] == []
        assert to_money(stored["outstanding"]) == to_money(1_000_000)
        negative = await c.put(f"/api/hr-payroll/periods/{zero['id']}/piece-inputs", json={
            "kasbon_inputs": [{"employee_id": emp["id"], "schedule_id": schedule["id"], "amount": -1}],
        })
        assert negative.status_code == 400
        valid = await _piece(c, _day(8), _day(8), _day(8), _day(8), kasbon_inputs=[{
            "employee_id": emp["id"], "schedule_id": schedule["id"], "amount": 250000,
        }])
        empty = await _calc(c, valid["id"])
        assert empty.status_code == 200, empty.text
        assert empty.json()["lines"] == []
        await _output(server.db, user_id=uid, qty=12000, work_date=_day(8), rate=100, action_id="act-kasbon")
        good = await _calc(c, valid["id"])
        assert good.status_code == 200, good.text
        assert to_money(good.json()["lines"][0]["deductions"][0]["amount"]) == to_money(250_000)
        assert to_money(good.json()["lines"][0]["net"]) == to_money(1_050_000)
        kept_input = await server.db[PERIODS].find_one({"id": valid["id"]}, {"_id": 0, "piece_kasbon_inputs": 1})
        assert kept_input["piece_kasbon_inputs"][0]["amount"] == 250000
        pending = await server.db[KASBON].find_one({"id": schedule["id"]}, {"_id": 0})
        assert to_money(pending["outstanding"]) == to_money(1_000_000)
        assert [row["amount"] for row in pending["payments"] if row["status"] == "pending"] == [250000]
        too_big = await c.put(f"/api/hr-payroll/periods/{valid['id']}/piece-inputs", json={
            "kasbon_inputs": [{"employee_id": emp["id"], "schedule_id": schedule["id"], "amount": 1000001}],
        })
        assert too_big.status_code == 200, too_big.text
        over = await _calc(c, valid["id"])
        assert over.status_code == 409
        assert "tidak diperkecil" in over.text or "melebihi" in over.text
        after = await server.db[KASBON].find_one({"id": schedule["id"]}, {"_id": 0})
        assert [row["amount"] for row in after["payments"] if row["status"] == "pending"] == [250000]
        other = await _piece(c, _day(9), _day(9), _day(9), _day(9), kasbon_inputs=[{
            "employee_id": emp["id"], "schedule_id": schedule["id"], "amount": 800000,
        }])
        await _output(server.db, user_id=uid, qty=12000, work_date=_day(9), rate=100, action_id="act-kasbon-b")
        blocked = await _calc(c, other["id"])
        assert blocked.status_code == 409
        assert "tidak diperkecil" in blocked.text
        still = await server.db[KASBON].find_one({"id": schedule["id"]}, {"_id": 0})
        own = [row for row in still["payments"] if row["status"] == "pending"]
        assert len(own) == 1 and own[0]["period_id"] == valid["id"] and to_money(own[0]["amount"]) == to_money(250_000)


@pytest.mark.anyio
async def test_reopen_retry_and_partial_failure_do_not_duplicate():
    from scripts.migrate_piece_payroll_indexes import run as migrate_run
    import services.hr_payroll as payroll
    import services.operator_wage_claims as claims

    _admin()
    await _wage_config()
    await migrate_run(server.db, apply=True)
    async with _client() as c:
        emp, uid = await _person(c, name="Pulih")
        src = await _output(server.db, user_id=uid, qty=12000, work_date=_day(3), rate=100, action_id="act-recover")
        schedule = await _kasbon(emp["id"], outstanding=1_000_000)
        await _integrate(c, _day(1))
        period = await _piece(c, _day(1), _day(7), _day(1), _day(7), kasbon_inputs=[{
            "employee_id": emp["id"], "schedule_id": schedule["id"], "amount": 250000,
        }])
        original = claims._bonus_snapshot

        def boom(*args, **kwargs):
            raise RuntimeError("hitung terputus")

        claims._bonus_snapshot = boom
        try:
            with pytest.raises(RuntimeError, match="hitung terputus"):
                await _calc(c, period["id"])
        finally:
            claims._bonus_snapshot = original
        assert await server.db[WAGE_CLAIMS].count_documents({"source_key": src["source_key"], "status": {"$in": ["held", "posted"]}}) == 0
        assert await server.db[BONUS_DAYS].count_documents({"period_id": period["id"], "status": {"$in": ["held", "posted"]}}) == 0
        assert await server.db[LINES].count_documents({"period_id": period["id"]}) == 0
        failed_schedule = await server.db[KASBON].find_one({"id": schedule["id"]}, {"_id": 0, "payments": 1})
        assert failed_schedule["payments"] == []
        ok = await _calc(c, period["id"])
        assert ok.status_code == 200, ok.text
        assert await server.db[WAGE_CLAIMS].count_documents({"source_key": src["source_key"], "status": "held"}) == 1
        held_id = (await server.db[WAGE_CLAIMS].find_one({"source_key": src["source_key"]}, {"_id": 0, "id": 1}))["id"]

        def boom_again(*args, **kwargs):
            raise RuntimeError("hitung terputus lagi")

        claims._bonus_snapshot = boom_again
        try:
            with pytest.raises(RuntimeError, match="hitung terputus"):
                await _calc(c, period["id"])
        finally:
            claims._bonus_snapshot = original
        kept = await server.db[WAGE_CLAIMS].find_one({"source_key": src["source_key"]}, {"_id": 0})
        assert kept["status"] == "held" and kept["id"] == held_id
        restored = await _calc(c, period["id"])
        assert restored.status_code == 200, restored.text
        accounts = await _accounts()
        await _map(c, accounts)
        calls = {"n": 0}
        real_slip = payroll.generate_payslip_for_line

        async def slip_once(*args, **kwargs):
            calls["n"] += 1
            if calls["n"] == 1:
                raise HTTPException(500, "slip gagal sekali")
            return await real_slip(*args, **kwargs)

        payroll.generate_payslip_for_line = slip_once
        try:
            await _to(c, period["id"], STATUS_REVIEW, STATUS_APPROVAL)
            first_final = await c.post(f"/api/hr-payroll/periods/{period['id']}/transition", json={"to_status": STATUS_FINAL})
            assert first_final.status_code == 500, first_final.text
            await server.db.orders.update_one(
                {"id": src["order_id"]},
                {"$set": {"operator_splits.0.progress_entries.0.qty": 1}},
            )
            assert await server.db.journal_entries.count_documents({"source_type": "payroll_period", "source_id": period["id"], "reversal_of": None}) == 1
            journal_amount = (await server.db.journal_entries.find_one(
                {"source_type": "payroll_period", "source_id": period["id"]}, {"_id": 0, "payroll_payable.amount": 1},
            ))["payroll_payable"]["amount"]
            retry = await c.post(f"/api/hr-payroll/periods/{period['id']}/transition", json={"to_status": STATUS_FINAL})
            assert retry.status_code == 200, retry.text
        finally:
            payroll.generate_payslip_for_line = real_slip
        assert retry.json()["period"]["status"] == STATUS_FINAL
        assert await server.db.journal_entries.count_documents({"source_type": "payroll_period", "source_id": period["id"], "reversal_of": None}) == 1
        same = await server.db.journal_entries.find_one({"source_type": "payroll_period", "source_id": period["id"]}, {"_id": 0, "payroll_payable.amount": 1})
        assert same["payroll_payable"]["amount"] == journal_amount == 1_050_000
        line = await server.db[LINES].find_one({"period_id": period["id"]}, {"_id": 0})
        assert to_money(line["net"]) == to_money(1_050_000)
        assert line["payslip"]["generated"] is True
        assert await server.db.files.count_documents({"line_id": line["id"], "kind": "hr_payroll"}) == 1
        posted = await server.db[KASBON].find_one({"id": schedule["id"]}, {"_id": 0})
        assert to_money(posted["outstanding"]) == to_money(750_000)
        assert sum(1 for row in posted["payments"] if row["status"] == "posted") == 1
        assert await server.db[WAGE_CLAIMS].count_documents({"source_key": src["source_key"], "status": "posted"}) == 1
        repeat = await c.post(f"/api/hr-payroll/periods/{period['id']}/transition", json={"to_status": STATUS_FINAL})
        assert repeat.status_code == 200, repeat.text
        assert await server.db.journal_entries.count_documents({"source_type": "payroll_period", "source_id": period["id"], "reversal_of": None}) == 1
        blocked = await c.post(f"/api/hr-payroll/periods/{period['id']}/reopen", json={"reason": "sudah ada jurnal"})
        assert blocked.status_code == 409

        emp2, uid2 = await _person(c, name="Revisi")
        await _output(server.db, user_id=uid2, qty=2000, work_date=_day(11), rate=100, action_id="act-reopen")
        schedule2 = await _kasbon(emp2["id"], outstanding=400_000)
        draft = await _piece(c, _day(11), _day(12), _day(11), _day(12), kasbon_inputs=[{
            "employee_id": emp2["id"], "schedule_id": schedule2["id"], "amount": 100000,
        }])
        opened_calc = await _calc(c, draft["id"])
        assert opened_calc.status_code == 200, opened_calc.text
        await payroll._post_kasbon_payments(server.db, draft["id"])
        await server.db[PERIODS].update_one({"id": draft["id"]}, {"$set": {"status": STATUS_FINAL, "frozen": True}})
        before = await server.db[KASBON].find_one({"id": schedule2["id"]}, {"_id": 0})
        assert to_money(before["outstanding"]) == to_money(300_000)
        reopened = await c.post(f"/api/hr-payroll/periods/{draft['id']}/reopen", json={"reason": "perbaiki input"})
        assert reopened.status_code == 200, reopened.text
        revision = reopened.json()["period"]
        assert revision["status"] == STATUS_DRAFT and revision["revision"] == 2
        claim = await server.db[WAGE_CLAIMS].find_one({"source_key": {"$regex": "act-reopen"}}, {"_id": 0})
        assert claim["status"] == "held" and claim["period_id"] == revision["id"]
        day = await server.db[BONUS_DAYS].find_one({"employee_id": emp2["id"], "bonus_date": _day(11)}, {"_id": 0})
        assert day["status"] == "held" and day["period_id"] == revision["id"]
        restored_kasbon = await server.db[KASBON].find_one({"id": schedule2["id"]}, {"_id": 0})
        assert to_money(restored_kasbon["outstanding"]) == to_money(400_000)
        assert sum(1 for row in restored_kasbon["payments"] if row["status"] == "restored") == 1
        second_open = await c.post(f"/api/hr-payroll/periods/{draft['id']}/reopen", json={"reason": "ulang"})
        assert second_open.status_code == 200, second_open.text
        assert second_open.json()["period"]["id"] == revision["id"]
        assert await server.db[PERIODS].count_documents({"group_id": draft["group_id"], "revision": 2}) == 1
        untouched = await server.db[KASBON].find_one({"id": schedule2["id"]}, {"_id": 0})
        assert to_money(untouched["outstanding"]) == to_money(400_000)


@pytest.mark.anyio
async def test_boundary_legacy_identity_and_progress_retry():
    _admin()
    await _wage_config()
    async with _client() as c:
        emp, uid = await _person(c, name="Batas")
        await _integrate(c, _day(5))
        await _output(server.db, user_id=uid, qty=12000, work_date=_day(3), rate=100, action_id="act-before")
        await _output(server.db, user_id=uid, qty=100, work_date=_day(6), rate=100, action_id="act-legacy", legacy=True)
        await _output(server.db, user_id="", qty=80, work_date=_day(6), rate=100, action_id="act-noid", name="Batas")
        ok_src = await _output(server.db, user_id=uid, qty=12000, work_date=_day(6), rate=100, action_id="act-ok")
        await server.db.orders.update_one(
            {"id": ok_src["order_id"]},
            {"$push": {"operator_splits.0.progress_entries": {"$each": [
                {
                    "id": "nodate", "action_id": "act-nodate", "qty": 50,
                    "po_id": ok_src["order_id"], "split_id": ok_src["split_id"], "operator_user_id": uid,
                },
                {
                    "id": "outside", "action_id": "act-outside-same", "qty": 40, "work_date": _day(21),
                    "po_id": ok_src["order_id"], "split_id": ok_src["split_id"], "operator_user_id": uid,
                    "rate_per_pcs": 100,
                },
            ]}}},
        )
        undated_only = await _output(server.db, user_id=uid, qty=70, work_date="", rate=100, action_id="act-undated-only")
        outside_only = await _output(server.db, user_id=uid, qty=30, work_date=_day(21), rate=100, action_id="act-outside-only")
        period = await _piece(c, _day(1), _day(10), _day(1), _day(10))
        blocked = await _calc(c, period["id"])
        assert blocked.status_code == 409
        assert "belum terhubung" in blocked.text
        fresh = await server.db[PERIODS].find_one({"id": period["id"]}, {"_id": 0})
        reasons = {row["reason"] for row in fresh["piece_exclusions"]}
        assert any("legacy" in reason for reason in reasons)
        assert any("batas" in reason for reason in reasons)
        assert any("tanggal" in reason for reason in reasons)
        assert any("di luar rentang" in reason for reason in reasons)
        excluded_keys = {row["source_key"] for row in fresh["piece_exclusions"]}
        assert undated_only["source_key"] in excluded_keys
        assert outside_only["source_key"] not in excluded_keys
        assert any(row["source_key"].endswith("act-outside-same") and row["reason"] == "di luar rentang upah dan bonus" for row in fresh["piece_exclusions"])
        assert fresh["piece_blockers"]
        filler_ids = []
        for n in range(205):
            filler = await _output(server.db, user_id=uid, qty=3, work_date="", rate=100, action_id=f"act-fill-{n}")
            filler_ids.append(filler["order_id"])
        omitted = await _calc(c, period["id"])
        assert omitted.status_code == 409
        listed = await server.db[PERIODS].find_one({"id": period["id"]}, {"_id": 0})
        assert len(listed["piece_exclusions"]) == 200
        assert listed["piece_omitted_exclusions"] > 0
        assert listed["piece_omitted_exclusion_qty"] > 0
        assert await server.db[WAGE_CLAIMS].count_documents({"status": {"$in": ["held", "posted"]}}) == 0
        await server.db.orders.delete_many({"id": {"$in": filler_ids}})
        assert await server.db[LINES].count_documents({"period_id": period["id"]}) == 0
        await server.db.orders.delete_many({"operator_splits.progress_entries.action_id": "act-noid"})
        clean = await _calc(c, period["id"])
        assert clean.status_code == 200, clean.text
        assert clean.json()["lines"][0]["piece_sources"][0]["qty"] == 12000
        from scripts.migrate_piece_payroll_indexes import run as migrate_run
        await migrate_run(server.db, apply=True)
        other = await _piece(c, _day(6), _day(6), _day(16), _day(16))
        taken = await _calc(c, other["id"])
        assert taken.status_code == 409
        await server.db[EMPLOYEES].update_one({"id": emp["id"]}, {"$set": {"pay_basis": "monthly"}})
        still = await server.db[WAGE_CLAIMS].count_documents({"status": "held", "employee_id": emp["id"]})
        assert still == 1
        await server.db[EMPLOYEES].update_one({"id": emp["id"]}, {"$set": {"pay_basis": "piece"}})
        moved = await c.put("/api/hr-payroll/piece-integration", json={"start_date": _day(1)})
        assert moved.status_code == 200, moved.text
        oid = f"ord-live-{uuid.uuid4().hex[:6]}"
        sid = f"spl-live-{uuid.uuid4().hex[:6]}"
        await server.db.orders.insert_one({
            "id": oid,
            "po_number": "PO-LIVE",
            "updated_at": "2026-09-01T00:00:00+00:00",
            "design_status": "Approved",
            "payment_status": "Approved",
            "sample_approved": True,
            "sample_files": [{"id": "sample"}],
            "helper_prep": {"qty_prepared": 12000},
            "screener_info": {"screen_qty": 1},
            "line_items": [{"quantity": 12000, "production_process": "print", "name": "Cup"}],
            "current_stage": 8,
            "operator_splits": [{
                "id": sid, "quantity": 12000, "line_idx": 0, "qty_completed": 0,
                "operator_user_id": uid, "operator_email": "piece-admin@example.com", "operator_name": "Batas",
            }],
        })
        action = "act-retry-progress"
        first = await c.post(f"/api/orders/{oid}/operator-progress", json={
            "operator_id": sid, "qty": 100, "action_id": action, "work_date": _day(6),
        })
        assert first.status_code == 200, first.text
        retry = await c.post(f"/api/orders/{oid}/operator-progress", json={
            "operator_id": sid, "qty": 900, "action_id": action, "work_date": _day(9),
        })
        assert retry.status_code == 200, retry.text
        order = await server.db.orders.find_one({"id": oid}, {"_id": 0})
        entries = [row for row in order["operator_splits"][0]["progress_entries"] if not row.get("legacy")]
        assert len(entries) == 1
        assert entries[0]["qty"] == 100
        assert entries[0]["work_date"] == _day(6)
        assert entries[0]["operator_user_id"] == uid
        assert entries[0]["rate_per_pcs"] == 100
        omitted = await c.post(f"/api/orders/{oid}/operator-progress", json={"operator_id": sid, "qty": 10, "action_id": "act-no-date"})
        assert omitted.status_code == 200, omitted.text
    for rel in (
        "frontend/src/components/RoleWorkPanel.jsx",
        "frontend/src/components/OperatorSplitPanel.jsx",
        "frontend/src/mobile/workspaces/MobilePrintWorkspace.jsx",
    ):
        text = (ROOT / rel).read_text()
        assert "work_date: workDate" in text or "work_date: confirm.workDate" in text
        assert "work_date: new Date" not in text


@pytest.mark.anyio
async def test_finalize_one_journal_payment_and_zero_net():
    from scripts.migrate_piece_payroll_indexes import run as migrate_run
    from services.hr_payroll import generate_payslip_pdf_bytes

    _admin()
    await _wage_config()
    await migrate_run(server.db, apply=True)
    async with _client() as c:
        emp, uid = await _person(c, name="Jurnal")
        await _output(server.db, user_id=uid, qty=12000, work_date=_day(3), rate=100)
        schedule = await _kasbon(emp["id"], outstanding=1_000_000, installment=999_999)
        await _integrate(c, _day(1))
        period = await _piece(c, _day(1), _day(7), _day(1), _day(20), kasbon_inputs=[{
            "employee_id": emp["id"], "schedule_id": schedule["id"], "amount": 250000,
        }])
        calc = await _calc(c, period["id"])
        assert calc.status_code == 200, calc.text
        line = calc.json()["lines"][0]
        wage = next(row["amount"] for row in line["earnings"] if row["code"] == "UPAH_BORONGAN")
        bonus = next(row["amount"] for row in line["earnings"] if row["code"] == "BONUS_OPERATOR")
        cut = line["deductions"][0]["amount"]
        assert (wage, bonus, cut, line["net"]) == (1_200_000, 100_000, 250_000, 1_050_000)
        accounts = await _accounts()
        await _map(c, accounts)
        final = await _to(c, period["id"], STATUS_REVIEW, STATUS_APPROVAL, STATUS_FINAL)
        assert final["status"] == STATUS_FINAL
        journals = await server.db.journal_entries.find(
            {"source_type": "payroll_period", "source_id": period["id"], "reversal_of": None}, {"_id": 0},
        ).to_list(5)
        assert len(journals) == 1
        lines = await server.db.journal_lines.find({"journal_id": journals[0]["id"]}, {"_id": 0}).to_list(10)
        assert sum(row["debit"] for row in lines) == sum(row["credit"] for row in lines) == 1_300_000
        assert journals[0]["payroll_payable"]["amount"] == 1_050_000
        assert journals[0]["date"] == _day(7)
        assert await server.db.journal_entries.count_documents({"source_type": "payroll_payment"}) == 0
        assert await server.db.cash_bank_payments.count_documents({}) == 0
        slip = await server.db[LINES].find_one({"period_id": period["id"]}, {"_id": 0})
        pdf = generate_payslip_pdf_bytes(slip, final)
        slip_text = _pdf_text(pdf)
        assert f"Rentang: {_day(1)}" in slip_text and _day(7) in slip_text
        assert to_money(slip["payslip"]["net"]) == to_money(1_050_000)
        kasbon = await server.db[KASBON].find_one({"id": schedule["id"]}, {"_id": 0})
        assert to_money(kasbon["outstanding"]) == to_money(750_000)
        locked = await c.put("/api/hr-payroll/piece-integration", json={"start_date": _day(2)})
        assert locked.status_code == 409
        bank = await create_cash_bank(
            server.db,
            {"name": "Bank Dummy", "account_id": accounts["bank"]["id"], "active": True},
            user={"id": "t", "name": "t", "role": "admin"},
        )
        payment = await c.post(f"/api/hr-payroll/periods/{period['id']}/payments", json={
            "cash_bank_account_id": bank["id"], "payment_date": _day(20),
        })
        assert payment.status_code == 200, payment.text
        body = payment.json()["payment"]
        assert body["total_amount"] == 1_050_000
        assert body["cash_bank_account_id"] == bank["id"]
        assert body["status"] == "DRAFT"
        assert await server.db.cash_bank_payments.count_documents({}) == 0
        assert await server.db.journal_entries.count_documents({"source_type": "payroll_payment"}) == 0

        emp0, uid0 = await _person(c, name="Nol")
        await _output(server.db, user_id=uid0, qty=1, work_date=_day(4), rate=100, action_id="act-zero")
        schedule0 = await _kasbon(emp0["id"], outstanding=100, installment=50)
        zero = await _piece(c, _day(4), _day(4), _day(4), _day(4), kasbon_inputs=[{
            "employee_id": emp0["id"], "schedule_id": schedule0["id"], "amount": 100,
        }])
        zero_calc = await _calc(c, zero["id"])
        assert zero_calc.status_code == 200, zero_calc.text
        assert to_money(zero_calc.json()["lines"][0]["net"]) == to_money(0)
        zero_final = await _to(c, zero["id"], STATUS_REVIEW, STATUS_APPROVAL, STATUS_FINAL)
        assert zero_final["status"] == STATUS_FINAL
        zero_journal = await server.db.journal_entries.find_one(
            {"source_type": "payroll_period", "source_id": zero["id"], "reversal_of": None}, {"_id": 0},
        )
        assert zero_journal["payroll_payable"]["amount"] == 0
        assert zero_journal["payroll_payable"]["zero_net"] is True
        zero_lines = await server.db.journal_lines.find({"journal_id": zero_journal["id"]}, {"_id": 0}).to_list(10)
        assert sum(row["debit"] for row in zero_lines) == sum(row["credit"] for row in zero_lines) == 100
        refused = await c.post(f"/api/hr-payroll/periods/{zero['id']}/payments", json={
            "cash_bank_account_id": bank["id"], "payment_date": _day(20),
        })
        assert refused.status_code == 409
        assert await server.db.hr_payroll_payments.count_documents({"payroll_period_id": zero["id"]}) == 0


@pytest.mark.anyio
async def test_holding_can_edit_inputs_and_other_roles_cannot():
    holding_defaults = set(with_nav_defaults(set(ROLE_DEFAULT_PERMISSIONS["holding_team"])))
    assert "payroll.piece_holding.edit" in holding_defaults
    assert "nav.hr_payroll.payroll" in holding_defaults
    for banned in ("payroll.calculate", "payroll.finalize", "payroll_accounting.post", "payroll_payment.create", "payroll_payment.post"):
        assert banned not in holding_defaults
    holding = {"id": "hold-1", "email": "holding-piece@example.com", "name": "Holding", "role": "holding_team", "permissions_override": {}}
    stranger = {"id": "op-1", "email": "op-piece@example.com", "name": "Operator", "role": "operator", "permissions_override": {}}
    app.dependency_overrides[current_user] = lambda: holding
    async with _client() as c:
        listed = await c.get("/api/hr-payroll/periods")
        assert listed.status_code == 200, listed.text
        created = await c.post("/api/hr-payroll/piece-periods", json={"start_date": _day(1), "end_date": _day(7)})
        assert created.status_code == 200, created.text
        period_id = created.json()["period"]["id"]
        saved = await c.put(f"/api/hr-payroll/periods/{period_id}/piece-inputs", json={
            "bonus": {"start_date": _day(1), "end_date": _day(7)},
            "kasbon_inputs": [],
        })
        assert saved.status_code == 200, saved.text
        assert (await c.get("/api/hr-payroll/employees")).status_code == 200
        assert (await c.get(f"/api/hr-payroll/periods/{period_id}/attendance")).status_code == 200
        assert (await c.get("/api/hr-payroll/kasbon-schedules")).status_code == 200
        assert (await c.post(f"/api/hr-payroll/periods/{period_id}/calculate")).status_code == 403
        assert (await c.post(f"/api/hr-payroll/periods/{period_id}/transition", json={"to_status": "FINAL"})).status_code == 403
        assert (await c.post(f"/api/hr-payroll/periods/{period_id}/payments", json={
            "cash_bank_account_id": "bank-x", "payment_date": _day(7),
        })).status_code == 403
        assert (await c.put("/api/hr-payroll/settings", json={"kasbon": {"max_deduction_ratio": 0.1}})).status_code == 403
    app.dependency_overrides[current_user] = lambda: stranger
    async with _client() as c:
        assert (await c.post("/api/hr-payroll/piece-periods", json={"start_date": _day(8), "end_date": _day(9)})).status_code == 403
        assert (await c.put(f"/api/hr-payroll/periods/{period_id}/piece-inputs", json={"bonus": {"start_date": _day(1), "end_date": _day(2)}})).status_code == 403
    await server.db.role_permissions.insert_one({"role": "holding_team", "permissions": ["product_request.read_all"]})
    app.dependency_overrides[current_user] = lambda: holding
    async with _client() as c:
        custom = await c.post("/api/hr-payroll/piece-periods", json={"start_date": _day(10), "end_date": _day(11)})
        assert custom.status_code == 403
    app.dependency_overrides[current_user] = lambda: _admin()
    async with _client() as c:
        assert (await c.get("/api/hr-payroll/piece-integration")).status_code == 200


@pytest.mark.anyio
async def test_migration_dry_run_repeat_and_mid_stamp_failure():
    import scripts.migrate_piece_payroll_indexes as mig

    old = {
        "id": "legacy-period-1",
        "group_id": "legacy-group-1",
        "period_number": "PRP-OLD-1",
        "year": 2077, "month": 3, "revision": 1,
        "status": "FINAL",
        "created_at": "2077-03-01T00:00:00+00:00",
    }
    other = {
        "id": "legacy-period-2",
        "group_id": "legacy-group-2",
        "period_number": "PRP-OLD-2",
        "year": 2077, "month": 4, "revision": 1,
        "status": "FINAL",
        "created_at": "2077-04-01T00:00:00+00:00",
    }
    info = await server.db[PERIODS].index_information()
    partial = mig.partial_monthly_index(info)
    if partial:
        await server.db[PERIODS].drop_index(partial)
    if not mig.legacy_monthly_index(await server.db[PERIODS].index_information()):
        await server.db[PERIODS].create_index(
            [("year", 1), ("month", 1), ("revision", 1)], unique=True,
        )
    await server.db[PERIODS].insert_many([dict(old), dict(other)])
    dry = await mig.run(server.db, apply=False)
    assert dry["dry_run"] is True
    assert dry["before"]["unstamped"] == 2
    assert dry["before"]["legacy_index"]
    assert await server.db[PERIODS].count_documents({"period_scope": {"$exists": False}}) == 2
    info = await server.db[PERIODS].index_information()
    assert mig.partial_monthly_index(info) is None
    original = mig._stamp_one
    calls = {"n": 0}

    async def flaky(db):
        calls["n"] += 1
        if calls["n"] > 1:
            raise RuntimeError("stempel berhenti")
        return await original(db)

    mig._stamp_one = flaky
    try:
        with pytest.raises(RuntimeError, match="stempel"):
            await mig.run(server.db, apply=True)
    finally:
        mig._stamp_one = original
    mid = await mig.inspect(server.db)
    assert mid["legacy_index"]
    assert mid["partial_index"]
    assert mid["unstamped"] == 1
    assert await server.db[PERIODS].count_documents({"period_scope": "monthly"}) == 1
    done = await mig.run(server.db, apply=True)
    assert done["stopped"] is None
    assert done["after"]["unstamped"] == 0
    assert done["after"]["legacy_index"] is None
    assert done["after"]["partial_index"]
    assert done["dropped"]
    again = await mig.run(server.db, apply=True)
    assert again["stamped"] == 0
    assert again["dropped"] is None
    assert again["after"]["legacy_index"] is None
    stamped = await server.db[PERIODS].find({"id": {"$in": ["legacy-period-1", "legacy-period-2"]}}, {"_id": 0}).to_list(5)
    assert {row["period_scope"] for row in stamped} == {"monthly"}
    piece = {
        "id": "piece-kept",
        "group_id": "piece-kept-group",
        "period_number": "PRP-PIECE-KEPT",
        "year": 2077, "month": 3, "revision": 1,
        "period_scope": "piece",
        "status": "DRAFT",
        "created_at": "2077-03-02T00:00:00+00:00",
    }
    await server.db[PERIODS].insert_one(piece)
    repeat = await mig.run(server.db, apply=True)
    assert repeat["stamped"] == 0
    kept = await server.db[PERIODS].find_one({"id": "piece-kept"}, {"_id": 0, "period_scope": 1})
    assert kept["period_scope"] == "piece"
    with pytest.raises(Exception):
        await server.db[PERIODS].insert_one({
            "id": "monthly-dup",
            "group_id": "monthly-dup-group",
            "period_number": "PRP-OLD-DUP",
            "year": 2077, "month": 4, "revision": 1,
            "period_scope": "monthly",
            "status": "DRAFT",
        })
    await ensure_indexes(server.db)
    restarted = await mig.inspect(server.db)
    assert restarted["legacy_index"] is None
    assert restarted["partial_index"]


@pytest.mark.anyio
async def test_new_assignment_stores_selected_user_id():
    _admin()
    chosen = "user-chosen-assignment"
    decoy = "user-decoy-same-email"
    email = "budi.assign@example.com"
    await server.db.users.insert_one({
        "id": decoy, "email": email, "name": "Decoy", "role": "operator", "status": "active",
    })
    order_id = f"ord-assign-{uuid.uuid4().hex[:8]}"
    await server.db.orders.insert_one({
        "id": order_id,
        "po_number": "PO-ASSIGN",
        "updated_at": "2026-09-01T00:00:00+00:00",
        "current_stage": 5,
        "design_status": "Approved",
        "payment_status": "Approved",
        "line_items": [{"name": "Cup", "quantity": 1000, "production_process": "print"}],
        "operator_splits": [],
        "timeline": [],
    })
    async with _client() as c:
        saved = await c.post(f"/api/orders/{order_id}/assign-operators", json={"splits": [{
            "operator_email": email,
            "operator_name": "Budi",
            "operator_user_id": chosen,
            "quantity": 1000,
            "line_idx": 0,
        }]})
        assert saved.status_code == 200, saved.text
        split = saved.json()["order"]["operator_splits"][0]
        assert split["operator_user_id"] == chosen
        assert split["operator_user_id"] != decoy
        kept = await c.post(f"/api/orders/{order_id}/assign-operators", json={"splits": [{
            "id": split["id"],
            "operator_email": email,
            "operator_name": "Budi",
            "quantity": 1000,
            "line_idx": 0,
        }]})
        assert kept.status_code == 200, kept.text
        assert kept.json()["order"]["operator_splits"][0]["operator_user_id"] == chosen
        changed = await c.post(f"/api/orders/{order_id}/assign-operators", json={"splits": [{
            "id": split["id"],
            "operator_email": "other.assign@example.com",
            "operator_name": "Lain",
            "quantity": 1000,
            "line_idx": 0,
        }]})
        assert changed.status_code == 200, changed.text
        dropped = changed.json()["order"]["operator_splits"][0]
        assert dropped.get("operator_user_id") in (None, "")


@pytest.mark.anyio
async def test_partial_bonus_day_failure_leaves_no_reservation():
    from scripts.migrate_piece_payroll_indexes import run as migrate_run
    import services.operator_wage_claims as claims

    _admin()
    await _wage_config()
    await migrate_run(server.db, apply=True)
    async with _client() as c:
        _emp, uid = await _person(c, name="Bonus Putus")
        src = await _output(server.db, user_id=uid, qty=12000, work_date=_day(3), rate=100, action_id="act-bonus-cut")
        await _integrate(c, _day(1))
        period = await _piece(c, _day(1), _day(7), _day(1), _day(7))
        real = claims._claim_bonus_day
        calls = {"n": 0}

        async def flaky(*args, **kwargs):
            calls["n"] += 1
            if calls["n"] > 2:
                raise RuntimeError("bonus terputus")
            return await real(*args, **kwargs)

        claims._claim_bonus_day = flaky
        try:
            with pytest.raises(RuntimeError, match="bonus terputus"):
                await _calc(c, period["id"])
        finally:
            claims._claim_bonus_day = real
        assert calls["n"] >= 3
        assert await server.db[BONUS_DAYS].count_documents({"period_id": period["id"], "status": {"$in": ["held", "posted"]}}) == 0
        assert await server.db[WAGE_CLAIMS].count_documents({"source_key": src["source_key"], "status": {"$in": ["held", "posted"]}}) == 0
        assert await server.db[LINES].count_documents({"period_id": period["id"]}) == 0


@pytest.mark.anyio
async def test_bonus_includes_qty_already_owned_by_another_payroll():
    from scripts.migrate_piece_payroll_indexes import run as migrate_run

    _admin()
    await _wage_config()
    await migrate_run(server.db, apply=True)
    async with _client() as c:
        _emp, uid = await _person(c, name="Bonus Lama")
        src = await _output(server.db, user_id=uid, qty=12000, work_date=_day(3), rate=100, action_id="act-owned-wage")
        await _integrate(c, _day(1))
        owner = await _piece(c, _day(1), _day(7), _day(15), _day(16))
        owned = await _calc(c, owner["id"])
        assert owned.status_code == 200, owned.text
        claim = await server.db[WAGE_CLAIMS].find_one({"source_key": src["source_key"]}, {"_id": 0})
        assert claim["status"] == "held" and claim["period_id"] == owner["id"]
        later = await _piece(c, _day(8), _day(9), _day(1), _day(7))
        bonus = await _calc(c, later["id"])
        assert bonus.status_code == 200, bonus.text
        snap = bonus.json()["lines"][0]["bonus_snapshot"]
        assert snap["base_qty"] == 12000
        assert to_money(snap["proposed_amount"]) == to_money(100_000)
        still = await server.db[WAGE_CLAIMS].find_one({"source_key": src["source_key"]}, {"_id": 0})
        assert still["id"] == claim["id"] and still["period_id"] == owner["id"] and still["status"] == "held"
        assert await server.db[WAGE_CLAIMS].count_documents({"source_key": src["source_key"], "status": {"$in": ["held", "posted"]}}) == 1


@pytest.mark.anyio
async def test_two_processes_cannot_reserve_more_kasbon_than_outstanding():
    from scripts.migrate_piece_payroll_indexes import run as migrate_run

    _admin()
    await _wage_config()
    await migrate_run(server.db, apply=True)
    async with _client() as c:
        emp, uid = await _person(c, name="Kasbon Race")
        await _output(server.db, user_id=uid, qty=12000, work_date=_day(1), rate=100, action_id="act-kas-a")
        await _output(server.db, user_id=uid, qty=12000, work_date=_day(3), rate=100, action_id="act-kas-b")
        schedule = await _kasbon(emp["id"], outstanding=1_000_000, installment=999_999)
        await _integrate(c, _day(1))
        inputs = [{"employee_id": emp["id"], "schedule_id": schedule["id"], "amount": 600000}]
        left = await _piece(c, _day(1), _day(2), _day(1), _day(2), kasbon_inputs=inputs)
        right = await _piece(c, _day(3), _day(4), _day(3), _day(4), kasbon_inputs=inputs)
        raced = _run_two([left["id"], right["id"]])
    assert sorted(raced) == ["http:409", "ok"], raced
    stored = await server.db[KASBON].find_one({"id": schedule["id"]}, {"_id": 0})
    pending = [row for row in stored["payments"] if row.get("status") == "pending"]
    assert len(pending) == 1
    assert to_money(pending[0]["amount"]) == to_money(600_000)
    assert sum(to_money(row["amount"]) for row in pending) <= to_money(1_000_000)
    assert to_money(stored["outstanding"]) == to_money(1_000_000)


@pytest.mark.anyio
async def test_revalidate_before_journal_rejects_over_outstanding_and_negative_net():
    from scripts.migrate_piece_payroll_indexes import run as migrate_run

    _admin()
    await _wage_config()
    await migrate_run(server.db, apply=True)
    async with _client() as c:
        emp, uid = await _person(c, name="Cek Ulang")
        await _output(server.db, user_id=uid, qty=12000, work_date=_day(3), rate=100, action_id="act-recheck")
        schedule = await _kasbon(emp["id"], outstanding=1_000_000)
        await _integrate(c, _day(1))
        period = await _piece(c, _day(1), _day(7), _day(1), _day(7), kasbon_inputs=[{
            "employee_id": emp["id"], "schedule_id": schedule["id"], "amount": 250000,
        }])
        calc = await _calc(c, period["id"])
        assert calc.status_code == 200, calc.text
        accounts = await _accounts()
        await _map(c, accounts)
        await _to(c, period["id"], STATUS_REVIEW, STATUS_APPROVAL)
        await server.db[KASBON].update_one({"id": schedule["id"]}, {"$set": {"outstanding": 100}})
        over = await c.post(f"/api/hr-payroll/periods/{period['id']}/transition", json={"to_status": STATUS_FINAL})
        assert over.status_code == 409, over.text
        assert "melebihi" in over.text
        assert await server.db.journal_entries.count_documents({"source_type": "payroll_period", "source_id": period["id"]}) == 0
        await server.db[KASBON].update_one({"id": schedule["id"]}, {"$set": {"outstanding": 1_000_000}})
        await server.db[LINES].update_one({"period_id": period["id"]}, {"$set": {"net": -1, "total_deductions": 1_300_001}})
        negative = await c.post(f"/api/hr-payroll/periods/{period['id']}/transition", json={"to_status": STATUS_FINAL})
        assert negative.status_code == 409, negative.text
        assert "negatif" in negative.text
        assert await server.db.journal_entries.count_documents({"source_type": "payroll_period", "source_id": period["id"]}) == 0
        fresh = await server.db[PERIODS].find_one({"id": period["id"]}, {"_id": 0, "status": 1})
        assert fresh["status"] == STATUS_APPROVAL


@pytest.mark.anyio
async def test_reopen_moves_claim_and_other_period_cannot_take_it():
    from scripts.migrate_piece_payroll_indexes import run as migrate_run
    import services.hr_payroll as payroll

    _admin()
    await _wage_config()
    await migrate_run(server.db, apply=True)
    async with _client() as c:
        emp, uid = await _person(c, name="Revisi Terkunci")
        src = await _output(server.db, user_id=uid, qty=2000, work_date=_day(11), rate=100, action_id="act-thief")
        schedule = await _kasbon(emp["id"], outstanding=400_000)
        draft = await _piece(c, _day(11), _day(12), _day(11), _day(12), kasbon_inputs=[{
            "employee_id": emp["id"], "schedule_id": schedule["id"], "amount": 100000,
        }])
        await _integrate(c, _day(1))
        opened = await _calc(c, draft["id"])
        assert opened.status_code == 200, opened.text
        await payroll._post_kasbon_payments(server.db, draft["id"])
        await server.db[PERIODS].update_one({"id": draft["id"]}, {"$set": {"status": STATUS_FINAL, "frozen": True}})
        reopened = await c.post(f"/api/hr-payroll/periods/{draft['id']}/reopen", json={"reason": "pindah klaim"})
        assert reopened.status_code == 200, reopened.text
        revision = reopened.json()["period"]
        thief = await _piece(c, _day(11), _day(12), _day(13), _day(14))
        taken = await _calc(c, thief["id"])
        assert taken.status_code == 409, taken.text
        claim = await server.db[WAGE_CLAIMS].find_one({"source_key": src["source_key"]}, {"_id": 0})
        assert claim["status"] == "held" and claim["period_id"] == revision["id"]
        assert await server.db[WAGE_CLAIMS].count_documents({"source_key": src["source_key"], "status": {"$in": ["held", "posted"]}}) == 1
        released = await server.db[WAGE_CLAIMS].count_documents({"source_key": src["source_key"], "status": "released"})
        assert released == 0


@pytest.mark.anyio
async def test_monthly_operator_progress_still_saves_work_date():
    from scripts.migrate_piece_payroll_indexes import run as migrate_run

    _admin()
    await _wage_config()
    await migrate_run(server.db, apply=True)
    async with _client() as c:
        monthly, uid = await _person(c, name="Sari Tetap", basis="monthly", base=5_000_000)
        await _integrate(c, _day(1))
        oid = f"ord-monthly-{uuid.uuid4().hex[:6]}"
        sid = f"spl-monthly-{uuid.uuid4().hex[:6]}"
        await server.db.orders.insert_one({
            "id": oid,
            "po_number": "PO-TETAP",
            "updated_at": "2026-09-01T00:00:00+00:00",
            "design_status": "Approved",
            "payment_status": "Approved",
            "sample_approved": True,
            "sample_files": [{"id": "sample"}],
            "helper_prep": {"qty_prepared": 500},
            "screener_info": {"screen_qty": 1},
            "line_items": [{"quantity": 500, "production_process": "print", "name": "Cup"}],
            "current_stage": 8,
            "operator_splits": [{
                "id": sid, "quantity": 500, "line_idx": 0, "qty_completed": 0,
                "operator_user_id": uid, "operator_email": "piece-admin@example.com", "operator_name": "Sari Tetap",
            }],
        })
        saved = await c.post(f"/api/orders/{oid}/operator-progress", json={
            "operator_id": sid, "qty": 200, "action_id": "act-monthly", "work_date": _day(6),
        })
        assert saved.status_code == 200, saved.text
        order = await server.db.orders.find_one({"id": oid}, {"_id": 0})
        entry = order["operator_splits"][0]["progress_entries"][0]
        assert entry["qty"] == 200 and entry["work_date"] == _day(6) and entry["operator_user_id"] == uid
        piece = await _piece(c, _day(1), _day(10), _day(1), _day(10))
        calc = await _calc(c, piece["id"])
        assert calc.status_code == 200, calc.text
        assert calc.json()["lines"] == []
        fresh = await server.db[PERIODS].find_one({"id": piece["id"]}, {"_id": 0, "piece_exclusions": 1})
        assert any("piece" in row["reason"] for row in fresh["piece_exclusions"])
        assert await server.db[WAGE_CLAIMS].count_documents({"employee_id": monthly["id"], "status": {"$in": ["held", "posted"]}}) == 0
        monthly_period = await c.post("/api/hr-payroll/periods", json={
            "year": int(_day(1)[:4]), "month": int(_day(1)[5:7]),
            "start_date": _day(1), "end_date": _day(28),
        })
        assert monthly_period.status_code == 200, monthly_period.text
        monthly_calc = await _calc(c, monthly_period.json()["period"]["id"])
        assert monthly_calc.status_code == 200, monthly_calc.text
        codes = {item["code"] for item in monthly_calc.json()["lines"][0]["earnings"]}
        assert "BASE" in codes
        assert "UPAH_BORONGAN" not in codes


@pytest.mark.anyio
async def test_hard_stop_partial_reservation_recovers_and_stale_worker_cannot_delete():
    """A killed worker leaves held rows. Retry finishes them; its late cleanup cannot.

    The child calls os._exit after the writes. No exception handler, finally, or
    rollback runs for that attempt.
    """
    from decimal import Decimal
    from services.operator_wage_claims import _rollback_days, _rollback_wage, reserve_kasbon

    _admin()
    await _wage_config()
    async with _client() as c:
        emp, uid = await _person(c, name="Proses Mati")
        src = await _output(server.db, user_id=uid, qty=12000, work_date=_day(3), rate=100, action_id="act-hard-stop")
        schedule = await _kasbon(emp["id"], outstanding=1_000_000)
        await _integrate(c, _day(1))
        period = await _piece(c, _day(1), _day(7), _day(1), _day(7), kasbon_inputs=[{
            "employee_id": emp["id"], "schedule_id": schedule["id"], "amount": 250000,
        }])
        source = {
            "source_key": src["source_key"],
            "order_id": src["order_id"],
            "split_id": src["split_id"],
            "action_id": src["action_id"],
            "employee_id": emp["id"],
            "work_date": _day(3),
            "qty": 12000,
            "rate_per_pcs": 100,
            "amount": 1_200_000,
            "operator_user_id": uid,
        }
        sched = {
            "id": schedule["id"],
            "request_id": schedule["request_id"],
            "request_number": schedule["request_number"],
        }
        ctx = multiprocessing.get_context("spawn")
        proc = ctx.Process(
            target=_hard_stop_after_partial_reservation,
            args=(
                period["id"], emp["id"], source, [_day(1), _day(2)], sched, 250000,
                os.environ["MONGO_URL"], os.environ["DB_NAME"],
            ),
        )
        proc.start()
        proc.join(60)
        assert proc.exitcode == 1, await server.db.app_settings.find_one({"key": "hard-stop-error"})
        stopped_days = await server.db[BONUS_DAYS].find(
            {"period_id": period["id"], "status": "held"}, {"_id": 0},
        ).to_list(20)
        assert sorted(row["bonus_date"] for row in stopped_days) == [_day(1), _day(2)]
        assert {row["calc_attempt"] for row in stopped_days} == {"worker-lama"}
        stopped_claim = await server.db[WAGE_CLAIMS].find_one({"source_key": src["source_key"]}, {"_id": 0})
        assert stopped_claim["status"] == "held" and stopped_claim["calc_attempt"] == "worker-lama"
        assert await server.db[LINES].count_documents({"period_id": period["id"]}) == 0
        stopped_kasbon = await server.db[KASBON].find_one({"id": schedule["id"]}, {"_id": 0})
        assert to_money(stopped_kasbon["outstanding"]) == to_money(1_000_000)
        stopped_pending = [row for row in stopped_kasbon["payments"] if row.get("status") == "pending"]
        assert len(stopped_pending) == 1
        assert to_money(stopped_pending[0]["amount"]) == to_money(250_000)
        assert stopped_pending[0]["calc_attempt"] == "worker-lama"

        recovered = await _calc(c, period["id"])
        assert recovered.status_code == 200, recovered.text
        days = await server.db[BONUS_DAYS].find(
            {"period_id": period["id"], "status": "held"}, {"_id": 0},
        ).to_list(20)
        assert len(days) == 7
        assert {_day(1), _day(2)} <= {row["bonus_date"] for row in days}
        adopted = [row for row in days if row["bonus_date"] in {_day(1), _day(2)}]
        assert {row["id"] for row in adopted} == {row["id"] for row in stopped_days}
        assert all(row["calc_attempt"] != "worker-lama" for row in days)
        claim = await server.db[WAGE_CLAIMS].find_one({"source_key": src["source_key"]}, {"_id": 0})
        assert claim["id"] == stopped_claim["id"] and claim["status"] == "held"
        assert claim["calc_attempt"] != "worker-lama"
        assert await server.db[WAGE_CLAIMS].count_documents({
            "source_key": src["source_key"], "status": {"$in": ["held", "posted"]},
        }) == 1
        line = recovered.json()["lines"][0]
        wage = next(item for item in line["earnings"] if item["code"] == "UPAH_BORONGAN")
        assert to_money(wage["amount"]) == to_money(1_200_000)
        assert wage["claim_ids"] == [src["source_key"]]
        kasbon_lines = [item for item in line["deductions"] if item["code"] == "KASBON"]
        assert len(kasbon_lines) == 1 and to_money(kasbon_lines[0]["amount"]) == to_money(250_000)
        restored = await server.db[KASBON].find_one({"id": schedule["id"]}, {"_id": 0})
        pending = [row for row in restored["payments"] if row.get("status") == "pending"]
        assert len(pending) == 1
        assert to_money(pending[0]["amount"]) == to_money(250_000)
        assert pending[0]["calc_attempt"] != "worker-lama"
        assert to_money(restored["outstanding"]) == to_money(1_000_000)

        await _rollback_days(server.db, [row["id"] for row in stopped_days], "worker-lama", period["id"])
        await _rollback_wage(server.db, [stopped_claim["id"]], "worker-lama", period["id"])
        late = await reserve_kasbon(
            server.db, schedule["id"], period["id"], Decimal("0.00"),
            {"schedule_id": schedule["id"], "request_number": schedule["request_number"]},
            only_if_attempt="worker-lama",
        )
        assert late["skipped"] is True
        assert await server.db[BONUS_DAYS].count_documents({"period_id": period["id"], "status": "held"}) == 7
        assert await server.db[WAGE_CLAIMS].count_documents({"id": claim["id"], "status": "held"}) == 1
        after_late = await server.db[KASBON].find_one({"id": schedule["id"]}, {"_id": 0})
        late_pending = [row for row in after_late["payments"] if row.get("status") == "pending"]
        assert len(late_pending) == 1 and to_money(late_pending[0]["amount"]) == to_money(250_000)
        assert to_money(after_late["outstanding"]) == to_money(1_000_000)

        other = await _piece(c, _day(3), _day(3), _day(20), _day(20))
        taken = await _calc(c, other["id"])
        assert taken.status_code == 409, taken.text
        assert "dimiliki" in taken.text
        assert await server.db[WAGE_CLAIMS].count_documents({
            "source_key": src["source_key"], "status": {"$in": ["held", "posted"]},
        }) == 1

        await server.db[BONUS_DAYS].delete_one({"id": days[0]["id"]})
        await _to(c, period["id"], STATUS_REVIEW, STATUS_APPROVAL)
        final = await c.post(f"/api/hr-payroll/periods/{period['id']}/transition", json={"to_status": STATUS_FINAL})
        assert final.status_code == 409, final.text
        assert "Reservasi bonus belum lengkap" in final.text
        assert await server.db.journal_entries.count_documents({"source_type": "payroll_period", "source_id": period["id"]}) == 0
        fresh = await server.db[PERIODS].find_one({"id": period["id"]}, {"_id": 0, "status": 1})
        assert fresh["status"] == STATUS_APPROVAL
        assert await server.db[BONUS_DAYS].count_documents({"period_id": period["id"], "status": "held"}) == 6
