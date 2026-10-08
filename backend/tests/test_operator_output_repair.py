"""Guarded Gaji Operator progress_entry repairs. Local fixtures only."""
from __future__ import annotations

import asyncio
import os
import sys
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("MONGO_URL", "mongodb://127.0.0.1:27017")
os.environ.setdefault("DB_NAME", "eternal_packaging_gaji_repair_test")
os.environ.setdefault("JWT_SECRET", "test-secret-key-12345678901234567890")

from services.hr_payroll import EMPLOYEES, PERIODS, STATUS_FINAL
from services.operator_output_repair import repair_rate_snapshot, repair_work_date
from services.operator_wage_claims import (
    INTEGRATION_KEY,
    WAGE_CLAIMS,
    build_gaji_operator_report,
)
from services.operator_payroll import SETTINGS_KEY as WAGE_KEY


class _Result:
    def __init__(self, matched=1, modified=1):
        self.matched_count = matched
        self.modified_count = modified


class _Cursor:
    def __init__(self, docs):
        self.docs = docs

    async def to_list(self, limit):
        return [dict(doc) for doc in self.docs[:limit]]


def _match_simple(doc, query):
    if not query:
        return True
    if "$or" in query:
        return any(_match_simple(doc, item) for item in query["$or"])
    for key, expected in query.items():
        if key == "operator_splits":
            continue
        if isinstance(expected, dict) and "$ne" in expected:
            if doc.get(key) == expected["$ne"]:
                return False
            continue
        if isinstance(expected, dict) and "$in" in expected:
            if doc.get(key) not in expected["$in"]:
                return False
            continue
        if isinstance(expected, dict) and "$regex" in expected:
            import re
            if not re.search(expected["$regex"], str(doc.get(key) or "")):
                return False
            continue
        if doc.get(key) != expected:
            return False
    return True


class _Coll:
    def __init__(self, *, unique_fields=None):
        self.docs = []
        self.audits = []
        self.unique_fields = set(unique_fields or [])

    def find(self, query=None, projection=None):
        del projection
        # Order wage queries use nested $elemMatch / dotted paths; local fixture
        # returns the small known set when any $or/$elemMatch is present.
        raw = query or {}
        if "$or" in raw or any("$elemMatch" in str(v) for v in raw.values()):
            return _Cursor(self.docs)
        return _Cursor([doc for doc in self.docs if _match_simple(doc, raw)])

    async def find_one(self, query=None, projection=None):
        rows = await self.find(query, projection).to_list(1)
        return rows[0] if rows else None

    async def insert_one(self, doc):
        from pymongo.errors import DuplicateKeyError
        for field in self.unique_fields:
            val = doc.get(field)
            if val is None:
                continue
            if any(existing.get(field) == val for existing in self.docs):
                raise DuplicateKeyError("E11000 duplicate key")
        self.docs.append(dict(doc))
        return SimpleNamespace(inserted_id="x")

    async def delete_one(self, query=None):
        query = query or {}
        for idx, doc in enumerate(self.docs):
            if _match_simple(doc, query):
                self.docs.pop(idx)
                return SimpleNamespace(deleted_count=1)
        return SimpleNamespace(deleted_count=0)

    async def find_one_and_update(self, query, update, return_document=None):
        del return_document
        for doc in self.docs:
            if not _match_simple(doc, query or {}):
                continue
            patch = (update or {}).get("$set") or {}
            doc.update(patch)
            return dict(doc)
        return None

    async def create_index(self, *args, **kwargs):
        del args, kwargs
        return "ok"

    async def update_one(self, query, update, array_filters=None):
        del array_filters
        for doc in self.docs:
            if not _match_simple(doc, {k: v for k, v in (query or {}).items() if k != "operator_splits"}):
                continue
            # crude CAS for progress entry work_date / rate
            splits = doc.get("operator_splits") or []
            for split in splits:
                for entry in split.get("progress_entries") or []:
                    em = ((query.get("operator_splits") or {}).get("$elemMatch") or {})
                    pe = (em.get("progress_entries") or {}).get("$elemMatch") or {}
                    if pe.get("action_id") and entry.get("action_id") != pe.get("action_id"):
                        continue
                    if pe.get("legacy") == {"$ne": True} and entry.get("legacy") is True:
                        continue
                    ors = pe.get("$or") or []
                    if ors:
                        ok = False
                        for cond in ors:
                            for field, val in cond.items():
                                if val == {"$exists": False} and field not in entry:
                                    ok = True
                                elif entry.get(field) == val:
                                    ok = True
                        if not ok:
                            return _Result(0, 0)
                    patch = update.get("$set") or {}
                    for path, value in patch.items():
                        # operator_splits.$[s].progress_entries.$[e].work_date
                        field = path.split(".")[-1]
                        entry[field] = value
                    return _Result(1, 1)
            return _Result(0, 0)
        return _Result(0, 0)


class FakeDB:
    def __init__(self):
        from services.operator_output_coord import COORD_LOCKS
        self.cols = {
            COORD_LOCKS: _Coll(unique_fields={"source_key"}),
        }

    def __getitem__(self, name):
        self.cols.setdefault(name, _Coll())
        return self.cols[name]

    def __getattr__(self, name):
        return self[name]


def _order_entry(**extra):
    entry = {
        "action_id": "act-1",
        "po_id": "po-1",
        "split_id": "split-1",
        "operator_user_id": "user-1",
        "qty": 10,
    }
    entry.update(extra)
    return entry


def _db_with_entry(**extra):
    db = FakeDB()
    db["orders"].docs.append({
        "id": "po-1",
        "po_number": "PO-1",
        "cancelled": False,
        "line_items": [{"name": "Cup"}],
        "operator_splits": [{
            "id": "split-1",
            "line_idx": 0,
            "operator_user_id": "user-1",
            "operator_name": "Budi",
            "progress_entries": [_order_entry(**extra)],
        }],
    })
    db[EMPLOYEES].docs.append({
        "id": "emp-1", "user_id": "user-1", "name": "Budi", "pay_basis": "piece", "active": True,
    })
    db["app_settings"].docs.append({"key": INTEGRATION_KEY, "value": {"start_date": "2026-08-01"}})
    db["app_settings"].docs.append({"key": WAGE_KEY, "default_rate_per_pcs": 100})
    return db


async def _audit(*args, **kwargs):
    return {"ok": True}


def test_repair_work_date_fills_empty_and_validates_report():
    db = _db_with_entry()
    user = {"email": "head@x.com", "role": "head_production", "name": "Head"}

    async def run():
        out = await repair_work_date(
            db,
            order_id="po-1",
            split_id="split-1",
            action_id="act-1",
            work_date="2026-08-10",
            expected_work_date="",
            reason="Tanggal kerja aktual dari kartu absensi",
            user=user,
            write_audit=_audit,
        )
        assert out["work_date"] == "2026-08-10"
        entry = db["orders"].docs[0]["operator_splits"][0]["progress_entries"][0]
        assert entry["work_date"] == "2026-08-10"
        # still missing rate → actionable rate issue after date filled
        entry["rate_per_pcs"] = 125
        report = await build_gaji_operator_report(db, date_from=date(2026, 8, 1), date_to=date(2026, 8, 31))
        row = next(r for r in report["rows"] if r["action_id"] == "act-1")
        assert row["work_date"] == "2026-08-10"
        assert row["payroll_status"] != "NOT_ELIGIBLE" or row.get("reason_code") != "HISTORIC_UNDATED"
        return report

    report = asyncio.run(run())
    assert report["historic_summary"]["total"] == 0


def test_repair_rejects_held_claim():
    db = _db_with_entry()
    db[WAGE_CLAIMS].docs.append({"source_key": "po-1:split-1:act-1", "status": "held", "period_id": "per-1"})
    with pytest.raises(HTTPException) as exc:
        asyncio.run(repair_work_date(
            db,
            order_id="po-1",
            split_id="split-1",
            action_id="act-1",
            work_date="2026-08-10",
            expected_work_date="",
            reason="Alasan valid panjang",
            user={"email": "a@x.com"},
            write_audit=_audit,
        ))
    assert exc.value.status_code == 409


def test_repair_rejects_legacy_and_short_reason():
    db = _db_with_entry(legacy=True, action_id="legacy-operator-split-1")
    with pytest.raises(HTTPException) as exc:
        asyncio.run(repair_work_date(
            db,
            order_id="po-1",
            split_id="split-1",
            action_id="legacy-operator-split-1",
            work_date="2026-08-10",
            expected_work_date="",
            reason="Alasan valid panjang",
            user={"email": "a@x.com"},
            write_audit=_audit,
        ))
    assert exc.value.status_code == 409

    db2 = _db_with_entry()
    with pytest.raises(HTTPException) as exc2:
        asyncio.run(repair_work_date(
            db2,
            order_id="po-1",
            split_id="split-1",
            action_id="act-1",
            work_date="2026-08-10",
            expected_work_date="",
            reason="x",
            user={"email": "a@x.com"},
            write_audit=_audit,
        ))
    assert exc2.value.status_code == 400


def test_repair_rate_requires_work_date_and_fills_snapshot():
    db = _db_with_entry(work_date="2026-08-10")
    out = asyncio.run(repair_rate_snapshot(
        db,
        order_id="po-1",
        split_id="split-1",
        action_id="act-1",
        rate_per_pcs=150,
        expected_rate=None,
        reason="Tarif berlaku 10 Agustus sesuai SK",
        user={"email": "head@x.com"},
        write_audit=_audit,
    ))
    assert out["rate_per_pcs"] == 150.0
    entry = db["orders"].docs[0]["operator_splits"][0]["progress_entries"][0]
    assert entry["rate_per_pcs"] == 150.0


def test_undated_does_not_inflate_active_eligibility():
    db = _db_with_entry()  # no work_date
    db["orders"].docs[0]["operator_splits"][0]["progress_entries"].append({
        "action_id": "act-ok",
        "po_id": "po-1",
        "split_id": "split-1",
        "operator_user_id": "user-1",
        "qty": 5,
        "rate_per_pcs": 100,
        "work_date": "2026-08-12",
    })
    report = asyncio.run(build_gaji_operator_report(
        db, date_from=date(2026, 8, 1), date_to=date(2026, 8, 31),
    ))
    assert all(r.get("action_id") != "act-1" for r in report["rows"])
    assert any(r.get("action_id") == "act-1" for r in report["historic_rows"])
    assert report["eligibility_summary"]["total"] == 0
    assert report["historic_summary"]["total"] >= 1


def test_legacy_keeps_legacy_reason_not_missing_date_label():
    db = _db_with_entry(legacy=True, action_id="legacy-x", id="legacy")
    report = asyncio.run(build_gaji_operator_report(
        db, date_from=date(2026, 8, 1), date_to=date(2026, 8, 31),
    ))
    row = next(r for r in report["historic_rows"] if r["action_id"] == "legacy-x")
    assert row["reason_code"] == "LEGACY_SOURCE"
    assert row["payroll_reason"] == "sumber legacy"
