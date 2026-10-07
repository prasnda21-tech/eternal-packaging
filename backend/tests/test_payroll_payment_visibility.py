"""Payment visibility layer only. Does not post journals or create payments."""
from __future__ import annotations

import asyncio
import os
import sys
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("MONGO_URL", "mongodb://127.0.0.1:27017")
os.environ.setdefault("DB_NAME", "eternal_packaging_payment_visibility_test")
os.environ.setdefault("JWT_SECRET", "test-secret-key-12345678901234567890")

from services.hr_payroll import EMPLOYEES, LINES
from services.operator_payroll import SETTINGS_KEY as WAGE_KEY
from services.operator_wage_claims import (
    INTEGRATION_KEY,
    PAYROLL_STATUS_POSTED,
    WAGE_CLAIMS,
    build_gaji_operator_report,
)
from services.payroll_payment import (
    PAYMENTS,
    STATUS_APPROVED,
    STATUS_DRAFT,
    STATUS_PAID,
    VIS_BELUM_DIBAYAR,
    VIS_MENUNGGU_PERSETUJUAN,
    VIS_SIAP_DIPOSTING,
    VIS_SUDAH_DIBAYAR,
    active_payments_by_period,
    enrich_period_with_payment_visibility,
    map_payment_visibility,
    payment_visibility_fields,
    payment_visibility_label,
    summarize_payment_visibility,
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
        self.writes = 0

    def find(self, query=None, projection=None):
        del projection
        if query and "$or" in query:
            return _Cursor(self.docs)
        return _Cursor([doc for doc in self.docs if _match(doc, query or {})])

    async def find_one(self, query=None, projection=None):
        rows = await self.find(query, projection).to_list(1)
        return rows[0] if rows else None

    async def insert_one(self, *args, **kwargs):
        self.writes += 1
        raise WriteDenied("visibility read tried to insert")

    async def insert_many(self, *args, **kwargs):
        self.writes += 1
        raise WriteDenied("visibility read tried to insert")

    async def update_one(self, *args, **kwargs):
        self.writes += 1
        raise WriteDenied("visibility read tried to update")

    async def update_many(self, *args, **kwargs):
        self.writes += 1
        raise WriteDenied("visibility read tried to update")

    async def delete_many(self, *args, **kwargs):
        self.writes += 1
        raise WriteDenied("visibility read tried to delete")

    async def replace_one(self, *args, **kwargs):
        self.writes += 1
        raise WriteDenied("visibility read tried to replace")

    async def find_one_and_update(self, *args, **kwargs):
        self.writes += 1
        raise WriteDenied("visibility read tried to update")


class FakeDB:
    def __init__(self):
        self.cols = {}

    def __getitem__(self, name):
        self.cols.setdefault(name, _Coll())
        return self.cols[name]

    def __getattr__(self, name):
        return self[name]


def test_map_payment_visibility_statuses():
    assert map_payment_visibility(None) == VIS_BELUM_DIBAYAR
    assert map_payment_visibility("") == VIS_BELUM_DIBAYAR
    assert map_payment_visibility(STATUS_DRAFT) == VIS_MENUNGGU_PERSETUJUAN
    assert map_payment_visibility(STATUS_APPROVED) == VIS_SIAP_DIPOSTING
    assert map_payment_visibility(STATUS_PAID) == VIS_SUDAH_DIBAYAR
    assert payment_visibility_label(VIS_BELUM_DIBAYAR) == "Belum Dibayar"
    assert payment_visibility_label(VIS_MENUNGGU_PERSETUJUAN) == "Menunggu Persetujuan"
    assert payment_visibility_label(VIS_SIAP_DIPOSTING) == "Siap Diposting"
    assert payment_visibility_label(VIS_SUDAH_DIBAYAR) == "Sudah Dibayar"


def test_final_period_no_payment_is_belum_dibayar():
    fields = payment_visibility_fields(None, period_final=True)
    assert fields["payment_status"] == VIS_BELUM_DIBAYAR
    assert fields["payment_status_label"] == "Belum Dibayar"
    assert fields["payment_id"] is None
    period = enrich_period_with_payment_visibility(
        {"id": "p1", "status": "FINAL", "accounting_journal_id": "j1"},
        None,
    )
    assert period["payment_status"] == VIS_BELUM_DIBAYAR
    assert period["payment_status_label"] == "Belum Dibayar"


@pytest.mark.parametrize("raw,expected", [
    (STATUS_DRAFT, VIS_MENUNGGU_PERSETUJUAN),
    (STATUS_APPROVED, VIS_SIAP_DIPOSTING),
    (STATUS_PAID, VIS_SUDAH_DIBAYAR),
])
def test_period_payment_visibility_from_active_payment(raw, expected):
    payment = {
        "id": "pay-1",
        "status": raw,
        "payment_date": "2026-08-20",
        "cash_bank_account_id": "bank-1",
        "paid_at": "2026-08-21T00:00:00Z" if raw == STATUS_PAID else None,
        "accounting_journal_id": "pj-1" if raw == STATUS_PAID else None,
        "payment_number": "PYB-1",
        "total_amount": 1000,
    }
    fields = payment_visibility_fields(payment, period_final=True)
    assert fields["payment_status"] == expected
    assert fields["payment_id"] == "pay-1"
    assert fields["payment_bank_account"] == "bank-1"
    assert fields["payment_raw_status"] == raw


def test_non_final_period_has_no_payment_visibility():
    fields = payment_visibility_fields(None, period_final=False)
    assert fields["payment_status"] is None
    period = enrich_period_with_payment_visibility({"id": "p1", "status": "DRAFT"}, None)
    assert period["payment_status"] is None


def test_mixed_period_unpaid_first_precedence():
    assert summarize_payment_visibility([
        VIS_SUDAH_DIBAYAR,
        VIS_BELUM_DIBAYAR,
        VIS_SIAP_DIPOSTING,
    ]) == VIS_BELUM_DIBAYAR
    assert summarize_payment_visibility([
        VIS_SUDAH_DIBAYAR,
        VIS_MENUNGGU_PERSETUJUAN,
    ]) == VIS_MENUNGGU_PERSETUJUAN
    assert summarize_payment_visibility([VIS_SUDAH_DIBAYAR]) == VIS_SUDAH_DIBAYAR
    assert summarize_payment_visibility([None, ""]) is None


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


def _base_db(*, payment_status=None, second_period_payment=None):
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
                _entry("act-posted", 2, 125, "2026-08-12"),
                _entry("act-posted-b", 3, 100, "2026-08-13"),
            ],
        }],
    })
    db[EMPLOYEES].docs.append({
        "id": "emp-1", "user_id": "user-1", "name": "Budi", "pay_basis": "piece", "active": True,
    })
    db["app_settings"].docs.extend([
        {"key": INTEGRATION_KEY, "value": {"start_date": "2026-08-01", "locked": True}},
        {"key": WAGE_KEY, "default_rate_per_pcs": 999, "bonus": {
            "mode": "once_per_period", "target_qty": 1000, "amount": 5000, "active": True,
        }},
    ])
    db[WAGE_CLAIMS].docs.extend([
        {"source_key": "po-1:split-1:act-posted", "status": "posted", "period_id": "per-1"},
        {"source_key": "po-1:split-1:act-posted-b", "status": "posted", "period_id": "per-2"},
    ])
    if payment_status:
        db[PAYMENTS].docs.append({
            "id": "pay-1",
            "payroll_period_id": "per-1",
            "status": payment_status,
            "payment_date": "2026-08-20",
            "cash_bank_account_id": "bank-1",
            "paid_at": "2026-08-21T00:00:00Z" if payment_status == STATUS_PAID else None,
            "accounting_journal_id": "pj-1" if payment_status == STATUS_PAID else None,
            "payment_number": "PYB-1",
            "total_amount": 250,
            "created_at": "2026-08-19T00:00:00Z",
        })
    if second_period_payment:
        db[PAYMENTS].docs.append({
            "id": "pay-2",
            "payroll_period_id": "per-2",
            "status": second_period_payment,
            "payment_date": "2026-08-22",
            "cash_bank_account_id": "bank-1",
            "paid_at": "2026-08-22T00:00:00Z" if second_period_payment == STATUS_PAID else None,
            "accounting_journal_id": "pj-2" if second_period_payment == STATUS_PAID else None,
            "payment_number": "PYB-2",
            "total_amount": 300,
            "created_at": "2026-08-21T00:00:00Z",
        })
    return db


def _report(db):
    return asyncio.run(build_gaji_operator_report(db, date_from=START, date_to=END))


def test_gaji_posted_without_payment_is_belum_dibayar():
    report = _report(_base_db())
    rows = [r for r in report["rows"] if r["payroll_status"] == PAYROLL_STATUS_POSTED]
    assert rows
    assert all(r["payment_status"] == VIS_BELUM_DIBAYAR for r in rows)
    assert all(r["payment_status_label"] == "Belum Dibayar" for r in rows)
    person = report["people"][0]
    assert person["payroll_status"] == PAYROLL_STATUS_POSTED
    assert person["payment_status"] == VIS_BELUM_DIBAYAR
    assert person["payment_status_label"] == "Belum Dibayar"
    assert report["kpis"]["belum_dibayar"] >= 1
    assert report["kpis"]["sudah_dibayar"] == 0


def test_gaji_paid_period_shows_sudah_dibayar():
    report = _report(_base_db(payment_status=STATUS_PAID, second_period_payment=STATUS_PAID))
    person = report["people"][0]
    assert person["payroll_status"] == PAYROLL_STATUS_POSTED
    assert person["payment_status"] == VIS_SUDAH_DIBAYAR
    assert person["payment_status_label"] == "Sudah Dibayar"
    assert report["kpis"]["sudah_dibayar"] == 1
    assert report["kpis"]["belum_dibayar"] == 0


def test_gaji_mixed_periods_unpaid_first():
    report = _report(_base_db(payment_status=STATUS_PAID, second_period_payment=STATUS_DRAFT))
    person = report["people"][0]
    assert person["payment_status"] == VIS_MENUNGGU_PERSETUJUAN
    by_action = {r["action_id"]: r for r in report["rows"]}
    assert by_action["act-posted"]["payment_status"] == VIS_SUDAH_DIBAYAR
    assert by_action["act-posted-b"]["payment_status"] == VIS_MENUNGGU_PERSETUJUAN


def test_gaji_report_read_does_not_write():
    db = _base_db(payment_status=STATUS_APPROVED)
    report = _report(db)
    assert report["read_only"] is True
    assert db[PAYMENTS].writes == 0
    assert db[WAGE_CLAIMS].writes == 0
    assert db[LINES].writes == 0


def test_active_payments_by_period_uses_one_active():
    db = FakeDB()
    db[PAYMENTS].docs.extend([
        {
            "id": "old",
            "payroll_period_id": "per-1",
            "status": STATUS_DRAFT,
            "created_at": "2026-01-01T00:00:00Z",
        },
        {
            "id": "new",
            "payroll_period_id": "per-1",
            "status": STATUS_APPROVED,
            "created_at": "2026-02-01T00:00:00Z",
        },
    ])
    found = asyncio.run(active_payments_by_period(db, ["per-1"]))
    assert found["per-1"]["id"] == "new"
    assert found["per-1"]["status"] == STATUS_APPROVED
