"""Focused read-only adapters for dashboard drill-down accuracy."""
from __future__ import annotations

import pytest

from services.cash_bank_mutasi import get_mutasi
from services.journal_core import SOURCE_PAYMENT, SOURCE_RECEIPT, SOURCE_TRANSFER
from services.purchase_invoices import list_purchase_invoices


class _Cursor:
    def __init__(self, rows):
        self._rows = list(rows)

    def sort(self, *_a, **_k):
        return self

    async def to_list(self, _n):
        return list(self._rows)


class _Coll:
    def __init__(self, rows):
        self.rows = list(rows)

    def find(self, query=None, *_a, **_k):
        query = query or {}
        out = []
        for row in self.rows:
            ok = True
            for key, expected in query.items():
                if key == "$or":
                    ok = any(
                        str(row.get(clause_key, "")).lower().find(
                            str(clause_val.get("$regex", "")).lower()
                        ) >= 0
                        for clause in expected
                        for clause_key, clause_val in clause.items()
                    )
                    continue
                actual = row.get(key)
                if isinstance(expected, dict):
                    if "$gte" in expected and str(actual or "") < expected["$gte"]:
                        ok = False
                    if "$lte" in expected and str(actual or "") > expected["$lte"]:
                        ok = False
                    if "$in" in expected and actual not in expected["$in"]:
                        ok = False
                    if "$ne" in expected and actual == expected["$ne"]:
                        ok = False
                    if "$exists" in expected:
                        exists = key in row and row[key] is not None
                        if bool(expected["$exists"]) != exists:
                            ok = False
                elif actual != expected:
                    ok = False
            if ok:
                out.append(dict(row))
        return _Cursor(out)

    async def find_one(self, query=None, *_a, **_k):
        rows = await self.find(query).to_list(1)
        return rows[0] if rows else None

    async def count_documents(self, query=None, *_a, **_k):
        rows = await self.find(query).to_list(100000)
        return len(rows)


class _DB(dict):
    def __getitem__(self, key):
        if key not in self:
            self[key] = _Coll([])
        return dict.__getitem__(self, key)


pytestmark = pytest.mark.anyio


async def test_list_purchase_invoices_filters_invoice_date_full_set(monkeypatch):
    rows = [
        {"id": "a", "invoice_number": "FP-A", "invoice_date": "2026-09-15", "supplier": "S"},
        {"id": "b", "invoice_number": "FP-B", "invoice_date": "2026-10-05", "supplier": "S"},
        {"id": "c", "invoice_number": "FP-C", "invoice_date": "2026-10-20", "supplier": "S"},
        {"id": "d", "invoice_number": "FP-D", "invoice_date": "2026-11-01", "supplier": "S"},
    ]
    db = _DB({"purchase_invoices": _Coll(rows), "purchase_payments": _Coll([])})

    async def _noop_attach(_db, items):
        return items

    monkeypatch.setattr(
        "services.purchase_invoice_posting.attach_invoice_accounting_fields",
        _noop_attach,
    )

    got = await list_purchase_invoices(db, date_from="2026-10-01", date_to="2026-10-31")
    assert [r["id"] for r in got] == ["b", "c"]


async def test_get_mutasi_exclude_internal_transfer_affects_totals(monkeypatch):
    journals = [
        {"id": "j-in", "date": "2026-10-02", "number": "IN-1", "source_type": SOURCE_RECEIPT, "source_id": "r1", "status": "posted", "created_at": "t1"},
        {"id": "j-xfer", "date": "2026-10-03", "number": "TR-1", "source_type": SOURCE_TRANSFER, "source_id": "t1", "status": "posted", "created_at": "t2"},
        {"id": "j-out", "date": "2026-10-04", "number": "OUT-1", "source_type": SOURCE_PAYMENT, "source_id": "p1", "status": "posted", "created_at": "t3"},
    ]
    lines = [
        {"id": "l1", "journal_id": "j-in", "account_id": "acc-1", "debit": 100, "credit": 0},
        {"id": "l2", "journal_id": "j-xfer", "account_id": "acc-1", "debit": 50, "credit": 0},
        {"id": "l3", "journal_id": "j-out", "account_id": "acc-1", "debit": 0, "credit": 30},
    ]
    db = _DB({
        "journal_entries": _Coll(journals),
        "journal_lines": _Coll(lines),
        "cash_bank_accounts": _Coll([{"id": "cb-1", "name": "Kas", "account_id": "acc-1"}]),
    })

    async def _cb(_db, cb_id):
        return {"id": cb_id, "name": "Kas", "account_id": "acc-1"}

    async def _bal(*_a, **_k):
        from decimal import Decimal
        return Decimal("0.00")

    async def _meta(*_a, **_k):
        return {"voucher_number": None, "source_path": None, "voucher_kind": None}

    monkeypatch.setattr("services.cash_bank_mutasi.get_cash_bank_raw", _cb)
    monkeypatch.setattr("services.cash_bank_mutasi.account_balance_as_of", _bal)
    monkeypatch.setattr("services.cash_bank_mutasi._resolve_voucher_meta", _meta)

    plain = await get_mutasi(db, cash_bank_id="cb-1", start_date="2026-10-01", end_date="2026-10-31")
    assert plain["total"] == 3
    assert plain["total_debit"] == 150.0
    assert plain["total_credit"] == 30.0

    excl = await get_mutasi(
        db,
        cash_bank_id="cb-1",
        start_date="2026-10-01",
        end_date="2026-10-31",
        exclude_internal_transfer=True,
    )
    assert excl["exclude_internal_transfer"] is True
    assert excl["total"] == 2
    assert excl["total_debit"] == 100.0
    assert excl["total_credit"] == 30.0
    assert all(row["source_type"] != SOURCE_TRANSFER for row in excl["items"])
    assert excl["ending_balance"] == 70.0
