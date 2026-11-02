"""Stage D: optional Master Sales display must not change financial attribution."""
from __future__ import annotations

import os
import sys
import uuid
from decimal import Decimal
from pathlib import Path

import pytest
from motor.motor_asyncio import AsyncIOMotorClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ.setdefault("MONGO_URL", "mongodb://127.0.0.1:27017")
os.environ.setdefault("DB_NAME", "eternal_packaging_sales_master_display")
os.environ.setdefault("JWT_SECRET", "test-secret-key-12345678901234567890")
os.environ.setdefault("CORS_ORIGINS", "*")

from services.sales_report_master_display import (
    MATCH_AMBIGUOUS,
    MATCH_MATCHED,
    MATCH_UNMATCHED,
    attach_master_display_to_sales_rows,
    resolve_master_display,
)
from services.sales_reports import (
    ATTR_MAPPED,
    ATTR_UNVERIFIED,
    UNVERIFIED_SALES_ID,
    UNVERIFIED_SALES_LABEL,
    _sales_rows,
)


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
async def db():
    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    database = client[os.environ["DB_NAME"]]
    await client.drop_database(os.environ["DB_NAME"])
    yield database
    await client.drop_database(os.environ["DB_NAME"])
    client.close()


def _line(label, *, penjualan="100.00", invoice_id="inv-1", qty=1):
    return {
        "sales_label": label,
        "penjualan": Decimal(penjualan),
        "quantity": Decimal(qty),
        "qty_verified": True,
        "invoice_id": invoice_id,
        "invoice_number": invoice_id,
    }


def test_resolve_master_exact_only_no_fuzzy():
    by_name = {"Vira": {"id": "sm-1", "sales_name": "Vira"}}
    got = resolve_master_display(
        labels=["vira"],  # different case → not exact
        sales_user_id=None,
        by_exact_name=by_name,
        by_user_id={},
    )
    assert got["sales_master_match_status"] == MATCH_UNMATCHED
    got2 = resolve_master_display(
        labels=["Vira"],
        sales_user_id=None,
        by_exact_name=by_name,
        by_user_id={},
    )
    assert got2["sales_master_id"] == "sm-1"
    assert got2["sales_master_match_status"] == MATCH_MATCHED


def test_resolve_ambiguous_conflicting_masters():
    by_name = {
        "Vira": {"id": "sm-vira", "sales_name": "Vira"},
        "Alfi": {"id": "sm-alfi", "sales_name": "Alfi"},
    }
    got = resolve_master_display(
        labels=["Vira", "Alfi"],
        sales_user_id="u-1",
        by_exact_name=by_name,
        by_user_id={},
    )
    assert got["sales_master_match_status"] == MATCH_AMBIGUOUS
    assert got["sales_master_id"] is None


def test_resolve_user_link_and_name_conflict():
    by_name = {"Vira": {"id": "sm-vira", "sales_name": "Vira"}}
    by_user = {"u-1": [{"id": "sm-other", "sales_name": "Other"}]}
    got = resolve_master_display(
        labels=["Vira"],
        sales_user_id="u-1",
        by_exact_name=by_name,
        by_user_id=by_user,
    )
    assert got["sales_master_match_status"] == MATCH_AMBIGUOUS


@pytest.mark.anyio
async def test_attach_does_not_change_attribution_buckets(db):
    mapping = {"Vira": "u-vira", "Ayub Alias": "u-ayub"}
    ambiguous = set()
    users_by_id = {
        "u-vira": {"id": "u-vira", "name": "Vira User", "role": "sales_eternal", "status": "active"},
        "u-ayub": {"id": "u-ayub", "name": "Ayub", "role": "sales_eternal", "status": "active"},
    }
    lines = [
        _line("Vira", penjualan="150.00", invoice_id="A"),
        _line("Ayub Alias", penjualan="50.00", invoice_id="B"),
        _line("Unknown", penjualan="25.00", invoice_id="C"),
    ]
    before = _sales_rows(lines, mapping, ambiguous, users_by_id)
    # Snapshot attribution invariants
    before_snap = [
        {
            "sales_user_id": r["sales_user_id"],
            "sales": r["sales"],
            "attribution_status": r["attribution_status"],
            "jumlah_faktur": r["jumlah_faktur"],
            "penjualan": str(r["penjualan"]),
            "quantity": str(r["quantity"]) if r.get("quantity") is not None else None,
            "label_mapping": r["label_mapping"],
        }
        for r in before
    ]
    await db.sales_master.insert_one(
        {"id": "sm-vira", "sales_name": "Vira", "user_id": "u-vira", "active": True}
    )
    await db.sales_master.insert_one(
        {"id": "sm-inactive", "sales_name": "Unknown", "active": False}
    )
    after = await attach_master_display_to_sales_rows(db, before)
    after_snap = [
        {
            "sales_user_id": r["sales_user_id"],
            "sales": r["sales"],
            "attribution_status": r["attribution_status"],
            "jumlah_faktur": r["jumlah_faktur"],
            "penjualan": str(r["penjualan"]),
            "quantity": str(r["quantity"]) if r.get("quantity") is not None else None,
            "label_mapping": r["label_mapping"],
        }
        for r in after
    ]
    assert after_snap == before_snap
    by_uid = {r["sales_user_id"]: r for r in after if r.get("sales_user_id")}
    assert by_uid["u-vira"]["sales_master_id"] == "sm-vira"
    assert by_uid["u-vira"]["sales_master_name"] == "Vira"
    assert by_uid["u-vira"]["sales_master_match_status"] == MATCH_MATCHED
    unverified = next(r for r in after if r["attribution_status"] == ATTR_UNVERIFIED)
    assert unverified["sales"] == UNVERIFIED_SALES_LABEL
    assert unverified["sales_master_id"] == "sm-inactive"
    assert unverified["sales_master_match_status"] == MATCH_MATCHED
    # inactive master still displayable when exact-linked
    assert by_uid["u-ayub"]["sales_master_match_status"] == MATCH_UNMATCHED
    assert by_uid["u-ayub"]["sales_master_id"] is None


@pytest.mark.anyio
async def test_no_authoritative_master_link(db):
    rows = [
        {
            "sales_user_id": "u-1",
            "sales": "Someone",
            "attribution_status": ATTR_MAPPED,
            "label_mapping": "NoMasterLabel",
            "jumlah_faktur": 1,
            "penjualan": Decimal("10.00"),
            "quantity": Decimal("1"),
        }
    ]
    out = await attach_master_display_to_sales_rows(db, rows)
    assert out[0]["sales_master_id"] is None
    assert out[0]["sales_master_match_status"] == MATCH_UNMATCHED
    assert out[0]["penjualan"] == Decimal("10.00")


@pytest.mark.anyio
async def test_batch_lookup_single_query_shape(db):
    class _CountCol:
        def __init__(self, col, counter):
            self._col = col
            self._counter = counter

        def find(self, *args, **kwargs):
            self._counter["n"] += 1
            return self._col.find(*args, **kwargs)

        def insert_one(self, *args, **kwargs):
            return self._col.insert_one(*args, **kwargs)

    class _CountDb:
        def __init__(self, inner):
            self._inner = inner
            self.counter = {"n": 0}

        def __getattr__(self, name):
            if name == "sales_master":
                return _CountCol(self._inner.sales_master, self.counter)
            return getattr(self._inner, name)

    await db.sales_master.insert_one({"id": "sm-1", "sales_name": "Vira", "active": True})
    wrapped = _CountDb(db)
    rows = [
        {"sales_user_id": "u-a", "label_mapping": "Vira", "penjualan": Decimal("1")},
        {"sales_user_id": "u-b", "label_mapping": "Vira", "penjualan": Decimal("2")},
        {"sales_user_id": None, "label_mapping": "Other", "penjualan": Decimal("3")},
    ]
    await attach_master_display_to_sales_rows(wrapped, rows)
    assert wrapped.counter["n"] == 1
