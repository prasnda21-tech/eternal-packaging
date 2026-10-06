"""Gaji Operator report follows piece-payroll outputs. Local fixtures only."""
from __future__ import annotations

import asyncio
import os
import sys
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("MONGO_URL", "mongodb://127.0.0.1:27017")
os.environ.setdefault("DB_NAME", "eternal_packaging_gaji_report_test")
os.environ.setdefault("JWT_SECRET", "test-secret-key-12345678901234567890")

from services.hr_payroll import EMPLOYEES, LINES
from services.operator_payroll import SETTINGS_KEY as WAGE_KEY
from services.operator_wage_claims import (
    BONUS_STATUS_FINAL,
    BONUS_STATUS_USULAN,
    INTEGRATION_KEY,
    MISSING_WORK_DATE_REASON,
    PAYROLL_STATUS_BELUM,
    PAYROLL_STATUS_BLOCKED,
    PAYROLL_STATUS_HELD,
    PAYROLL_STATUS_POSTED,
    WAGE_CLAIMS,
    build_gaji_operator_report,
    collect_outputs,
)

START = date(2026, 8, 1)
END = date(2026, 8, 31)


class WriteDenied(AssertionError):
    pass


class _Cursor:
    def __init__(self, docs):
        self.docs = docs

    async def to_list(self, limit):
        return [dict(doc) for doc in self.docs[:limit]]


def _get_path(doc, path):
    current = doc
    for part in str(path).split("."):
        if not isinstance(current, dict):
            return None
        current = current.get(part)
    return current


def _match(doc, query):
    if not query:
        return True
    if "$or" in query:
        return any(_match(doc, item) for item in query["$or"])
    for key, expected in query.items():
        if isinstance(expected, dict) and "$in" in expected:
            if _get_path(doc, key) not in expected["$in"]:
                return False
            continue
        if isinstance(expected, dict) and "$ne" in expected:
            if doc.get(key) == expected["$ne"]:
                return False
            continue
        if isinstance(expected, dict):
            return False
        if _get_path(doc, key) != expected:
            return False
    return True


class _Coll:
    def __init__(self):
        self.docs = []

    def find(self, query=None, projection=None):
        del projection
        if query and "$or" in query:
            return _Cursor(self.docs)
        return _Cursor([doc for doc in self.docs if _match(doc, query or {})])

    async def find_one(self, query=None, projection=None):
        rows = await self.find(query, projection).to_list(1)
        return rows[0] if rows else None

    async def insert_one(self, *args, **kwargs):
        raise WriteDenied("report tried to insert")

    async def insert_many(self, *args, **kwargs):
        raise WriteDenied("report tried to insert")

    async def update_one(self, *args, **kwargs):
        raise WriteDenied("report tried to update")

    async def update_many(self, *args, **kwargs):
        raise WriteDenied("report tried to update")

    async def delete_many(self, *args, **kwargs):
        raise WriteDenied("report tried to delete")

    async def replace_one(self, *args, **kwargs):
        raise WriteDenied("report tried to replace")

    async def find_one_and_update(self, *args, **kwargs):
        raise WriteDenied("report tried to update")


class FakeDB:
    def __init__(self):
        self.cols = {}

    def __getitem__(self, name):
        self.cols.setdefault(name, _Coll())
        return self.cols[name]

    def __getattr__(self, name):
        return self[name]


def _entry(action, qty, rate, work_date, uid="user-1"):
    return {
        "action_id": action,
        "po_id": "po-1",
        "split_id": "split-1",
        "operator_user_id": uid,
        "qty": qty,
        "rate_per_pcs": rate,
        "work_date": work_date,
    }


def _db():
    db = FakeDB()
    db["orders"].docs.append({
        "id": "po-1",
        "po_number": "PO-1",
        "cancelled": False,
        "line_items": [{"name": "Cup 16", "item_code": "CUP-16"}],
        "operator_splits": [{
            "id": "split-1",
            "line_idx": 0,
            "operator_user_id": "user-1",
            "operator_name": "Budi",
            "operator_email": "budi@x.com",
            "progress_entries": [
                _entry("act-open", 10, 125, "2026-08-10"),
                _entry("act-held", 4, 125, "2026-08-11"),
                _entry("act-posted", 2, 125, "2026-08-12"),
                _entry("act-norate", 7, None, "2026-08-13"),
                _entry("act-old", 9, 125, "2026-07-01"),
            ],
        }, {
            "id": "split-2",
            "line_idx": 0,
            "operator_user_id": "user-2",
            "operator_name": "Andi",
            "operator_email": "andi@x.com",
            "progress_entries": [
                _entry("act-monthly", 3, 50, "2026-08-14", uid="user-2"),
            ],
        }, {
            "id": "split-3",
            "line_idx": 0,
            "operator_name": "Tanpa ID",
            "progress_entries": [{
                "action_id": "act-nouid",
                "po_id": "po-1",
                "split_id": "split-3",
                "qty": 6,
                "rate_per_pcs": 10,
                "work_date": "2026-08-15",
            }],
        }],
    })
    db[EMPLOYEES].docs.extend([
        {"id": "emp-1", "user_id": "user-1", "name": "Budi", "pay_basis": "piece", "active": True},
        {"id": "emp-2", "user_id": "user-2", "name": "Andi", "pay_basis": "monthly", "active": True},
    ])
    db["app_settings"].docs.extend([
        {"key": INTEGRATION_KEY, "value": {"start_date": "2026-08-01", "locked": True}},
        {"key": WAGE_KEY, "default_rate_per_pcs": 999, "bonus": {
            "mode": "once_per_period", "target_qty": 10, "amount": 5000, "active": True,
        }},
    ])
    db[WAGE_CLAIMS].docs.extend([
        {"source_key": "po-1:split-1:act-held", "status": "held", "period_id": "per-1"},
        {"source_key": "po-1:split-1:act-posted", "status": "posted", "period_id": "per-1"},
    ])
    return db


def _report(db, **kwargs):
    return asyncio.run(build_gaji_operator_report(
        db, date_from=START, date_to=END, **kwargs,
    ))


def _by_action(report, action):
    rows = [row for row in report["rows"] if row["action_id"] == action]
    assert len(rows) == 1, action
    return rows[0]


def test_status_and_source_key_follow_piece_claims():
    report = _report(_db())
    open_row = _by_action(report, "act-open")
    held = _by_action(report, "act-held")
    posted = _by_action(report, "act-posted")
    assert open_row["payroll_status"] == PAYROLL_STATUS_BELUM
    assert held["payroll_status"] == PAYROLL_STATUS_HELD
    assert posted["payroll_status"] == PAYROLL_STATUS_POSTED
    assert open_row["source_key"] == "po-1:split-1:act-open"
    assert held["source_key"] == "po-1:split-1:act-held"
    assert posted["source_key"] == "po-1:split-1:act-posted"
    assert report["read_only"] is True


def test_missing_rate_and_hr_profile_are_not_eligible():
    report = _report(_db())
    missing_rate = _by_action(report, "act-norate")
    monthly = _by_action(report, "act-monthly")
    no_uid = _by_action(report, "act-nouid")
    assert missing_rate["payroll_status"] == PAYROLL_STATUS_BLOCKED
    assert missing_rate["payroll_reason"] == "tanpa snapshot tarif"
    assert missing_rate["base_wage"] == 0
    assert monthly["payroll_status"] == PAYROLL_STATUS_BLOCKED
    assert monthly["payroll_reason"] == "profil bukan pay_basis piece"
    assert no_uid["payroll_status"] == PAYROLL_STATUS_BLOCKED
    assert "operator_user_id" in no_uid["payroll_reason"]


def test_work_date_is_canonical_and_snapshot_ignores_master_rate():
    report = _report(_db())
    row = _by_action(report, "act-open")
    assert row["work_date"] == "2026-08-10"
    assert row["tanggal"] == "2026-08-10"
    assert row["rate_per_pcs"] == 125
    assert row["base_wage"] == 1250
    assert row["product_addition"] == 0
    assert all(item["action_id"] != "act-old" for item in report["rows"])
    before = row["base_wage"]
    db = _db()
    db["app_settings"].docs = [
        doc if doc.get("key") != WAGE_KEY else {**doc, "default_rate_per_pcs": 1}
        for doc in db["app_settings"].docs
    ]
    again = _by_action(_report(db), "act-open")
    assert again["base_wage"] == before


def test_report_wage_matches_collect_outputs_and_does_not_write():
    db = _db()

    async def _both():
        found = await collect_outputs(
            db,
            wage_start="2026-08-01",
            wage_end="2026-08-31",
            bonus_start="2026-08-01",
            bonus_end="2026-08-31",
            integration_start="2026-08-01",
        )
        report = await build_gaji_operator_report(db, date_from=START, date_to=END)
        return found, report

    found, report = asyncio.run(_both())
    payroll_amount = sum(row["amount"] for row in found["wage"])
    visible = [
        row for row in report["rows"]
        if row["payroll_status"] != PAYROLL_STATUS_BLOCKED
    ]
    assert sum(row["base_wage"] for row in visible) == payroll_amount
    assert {row["source_key"] for row in visible} == {row["source_key"] for row in found["wage"]}
    person = next(row for row in report["people"] if row["operator_user_id"] == "user-1")
    assert person["wage_basis"] == payroll_amount
    assert person["bonus"] == 5000
    assert person["bonus_source"] == "proposed"
    assert person["bonus_status"] == BONUS_STATUS_USULAN
    assert person["bonus_proposed_amount"] == 5000
    assert person["bonus_final_amount"] == 0
    assert person["total_payroll"] == payroll_amount
    assert person["total_upah"] == payroll_amount
    assert person["estimate_total"] == payroll_amount + 5000
    assert report["kpis"]["total_payroll"] == payroll_amount
    assert report["kpis"]["bonus"] == 0
    assert report["kpis"]["bonus_proposed"] == 5000
    assert len(db[WAGE_CLAIMS].docs) == 2
    assert db[LINES].docs == []


def test_before_integration_is_not_eligible():
    db = _db()
    for doc in db["app_settings"].docs:
        if doc.get("key") == INTEGRATION_KEY:
            doc["value"] = {"start_date": "2026-08-16", "locked": True}
    report = _report(db)
    early = _by_action(report, "act-open")
    assert early["work_date"] == "2026-08-10"
    assert early["payroll_status"] == PAYROLL_STATUS_BLOCKED
    assert early["payroll_reason"] == "sebelum batas integrasi"


def _bonus_line(qty, final, *, earning=True, start="2026-08-01", end="2026-08-31"):
    doc = {
        "employee_id": "emp-1",
        "updated_at": "2026-08-31T00:00:00",
        "bonus_snapshot": {
            "start_date": start,
            "end_date": end,
            "base_qty": qty,
            "final_amount": final,
            "proposed_amount": 5000,
        },
        "earnings": [],
    }
    if earning:
        doc["earnings"].append({"code": "BONUS_OPERATOR", "amount": final})
    return doc


def test_proposed_bonus_without_payroll_line_is_usulan():
    person = next(row for row in _report(_db())["people"] if row["employee_id"] == "emp-1")
    assert person["bonus_status"] == BONUS_STATUS_USULAN
    assert person["bonus_source"] == "proposed"
    assert person["bonus_proposed_amount"] == 5000
    assert person["total_payroll"] == person["wage_basis"]
    assert person["estimate_total"] == person["wage_basis"] + 5000


def test_snapshot_without_bonus_operator_earning_stays_usulan():
    db = _db()
    db[LINES].docs.append(_bonus_line(23, 7000, earning=False))
    person = next(row for row in _report(db)["people"] if row["employee_id"] == "emp-1")
    assert person["bonus_status"] == BONUS_STATUS_USULAN
    assert person["bonus_source"] == "proposed"
    assert person["total_payroll"] == person["wage_basis"]
    assert person["bonus_final_amount"] == 0


def test_matching_bonus_operator_line_is_final():
    db = _db()
    db[LINES].docs.append(_bonus_line(23, 7000))
    person = next(row for row in _report(db)["people"] if row["employee_id"] == "emp-1")
    assert person["bonus_source"] == "payroll_line"
    assert person["bonus_status"] == BONUS_STATUS_FINAL
    assert person["bonus"] == 7000
    assert person["bonus_final_amount"] == 7000
    assert person["total_payroll"] == person["wage_basis"] + 7000
    assert person["total_upah"] == person["total_payroll"]
    assert person["estimate_total"] == person["total_payroll"]


def test_product_filter_that_changes_qty_keeps_bonus_usulan():
    db = _db()
    order = db["orders"].docs[0]
    order["line_items"].append({"name": "Cup 22", "item_code": "CUP-22"})
    order["operator_splits"].append({
        "id": "split-extra",
        "line_idx": 1,
        "operator_user_id": "user-1",
        "operator_name": "Budi",
        "operator_email": "budi@x.com",
        "progress_entries": [{
            **_entry("act-extra", 5, 125, "2026-08-16"),
            "split_id": "split-extra",
        }],
    })
    db[LINES].docs.append(_bonus_line(28, 9000))
    full = next(row for row in _report(db)["people"] if row["employee_id"] == "emp-1")
    assert full["bonus_status"] == BONUS_STATUS_FINAL
    assert full["bonus_final_amount"] == 9000
    narrowed = _report(db, item="CUP-16")
    person = next(row for row in narrowed["people"] if row["employee_id"] == "emp-1")
    assert person["bonus_status"] == BONUS_STATUS_USULAN
    assert person["bonus_source"] == "proposed"
    assert person["bonus_final_amount"] == 0
    assert person["total_payroll"] == person["wage_basis"]
    assert "act-extra" not in {row["action_id"] for row in narrowed["rows"]}
    other = _report(db, operator_ids=["user-1"], item="CUP-22")
    cup22 = next(row for row in other["people"] if row["employee_id"] == "emp-1")
    assert cup22["bonus_status"] == BONUS_STATUS_USULAN
    assert cup22["qty"] == 5
    assert cup22["total_payroll"] == 625
    assert cup22["bonus_proposed_amount"] == 0


def test_missing_work_date_is_not_eligible_and_does_not_infer_a_date():
    db = _db()
    order = db["orders"].docs[0]
    order["order_date"] = "2026-08-20"
    order["created_at"] = "2026-08-21T00:00:00"
    order["timeline"] = [{"date": "2026-08-22", "qty": 8}]
    order["operator_splits"][0]["progress_entries"].append({
        "action_id": "act-nodate",
        "po_id": "po-1",
        "split_id": "split-1",
        "operator_user_id": "user-1",
        "qty": 8,
        "rate_per_pcs": 125,
    })
    db[WAGE_CLAIMS].docs.append({
        "source_key": "po-1:split-1:act-nodate",
        "status": "posted",
        "period_id": "per-1",
    })
    before = _report(_db())
    report = _report(db)
    dated = _by_action(report, "act-open")
    missing = _by_action(report, "act-nodate")
    assert dated["work_date"] == "2026-08-10"
    assert dated["tanggal"] == "2026-08-10"
    assert dated["payroll_status"] == PAYROLL_STATUS_BELUM
    assert dated["base_wage"] == 1250
    assert missing["payroll_status"] == PAYROLL_STATUS_BLOCKED
    assert missing["payroll_reason"] == MISSING_WORK_DATE_REASON
    assert missing["work_date"] == ""
    assert missing["tanggal"] == ""
    assert missing["date"] == ""
    assert missing["base_wage"] == 0
    assert missing["tanggal"] != order["order_date"]
    assert "2026-08-21" not in missing["tanggal"]
    assert "2026-08-22" not in missing["tanggal"]
    person = next(row for row in report["people"] if row["operator_user_id"] == "user-1")
    before_person = next(row for row in before["people"] if row["operator_user_id"] == "user-1")
    assert person["wage_basis"] == before_person["wage_basis"]
    assert person["total_payroll"] == before_person["total_payroll"]
