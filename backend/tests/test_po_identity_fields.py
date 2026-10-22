"""Focused locks for batch PO identity attach (orders.id only, one query)."""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from services.po_identity_fields import (
    apply_identity_fields,
    attach_po_identity_fields,
    order_identity_by_ids,
)


class _AsyncCursor:
    def __init__(self, docs):
        self._docs = list(docs)

    def __aiter__(self):
        self._i = 0
        return self

    async def __anext__(self):
        if self._i >= len(self._docs):
            raise StopAsyncIteration
        doc = self._docs[self._i]
        self._i += 1
        return doc


def _fake_db(docs):
    cursor = _AsyncCursor(docs)
    orders = MagicMock()
    orders.find = MagicMock(return_value=cursor)
    return SimpleNamespace(orders=orders), orders


def test_apply_identity_fields_defaults_when_missing():
    row = {"po_id": "missing"}
    apply_identity_fields(row, None)
    assert row["priority"] == "normal"
    assert row["urgent"] is False
    assert row["buffer_stock"] is False


def test_apply_identity_fields_copies_canonical():
    row = {"po_id": "o1"}
    apply_identity_fields(
        row,
        {"priority": "urgent", "urgent": True, "buffer_stock": True},
    )
    assert row["priority"] == "urgent"
    assert row["urgent"] is True
    assert row["buffer_stock"] is True


def test_order_identity_by_ids_keys_canonical_id_only():
    db, orders = _fake_db(
        [
            {"id": "po-a", "priority": "urgent", "buffer_stock": True},
            {"id": "po-b", "priority": "normal", "buffer_stock": False},
        ]
    )
    out = asyncio.run(order_identity_by_ids(db, ["po-a", "po-b", "", None]))
    assert set(out.keys()) == {"po-a", "po-b"}
    assert out["po-a"]["urgent"] is True
    assert out["po-a"]["buffer_stock"] is True
    assert out["po-b"]["urgent"] is False
    # Query uses $in on id — never po_number
    args, _kwargs = orders.find.call_args
    assert args[0] == {"id": {"$in": ["po-a", "po-b"]}}
    assert args[1] == {"_id": 0, "id": 1, "priority": 1, "buffer_stock": 1}


def test_attach_po_identity_fields_one_query_no_n_plus_one():
    db, orders = _fake_db(
        [
            {"id": "po-1", "priority": "urgent", "buffer_stock": False},
            {"id": "po-2", "priority": "normal", "buffer_stock": True},
        ]
    )
    rows = [
        {"po_id": "po-1", "label": "A"},
        {"po_id": "po-2", "label": "B"},
        {"po_id": "po-unrelated", "label": "C"},
        {"label": "no-po"},
    ]
    out = asyncio.run(attach_po_identity_fields(db, rows))
    assert orders.find.call_count == 1
    assert out[0]["urgent"] is True
    assert out[0]["buffer_stock"] is False
    assert out[1]["urgent"] is False
    assert out[1]["buffer_stock"] is True
    # Missing PO → safe defaults; unrelated id not invented from other rows
    assert out[2]["priority"] == "normal"
    assert out[2]["urgent"] is False
    assert out[2]["buffer_stock"] is False
    assert out[3]["priority"] == "normal"
    assert out[3]["urgent"] is False
    # No cross-association: po-1 identity not applied to po-2
    assert out[1]["urgent"] is False


def test_attach_empty_rows_skips_query():
    db, orders = _fake_db([])
    assert asyncio.run(attach_po_identity_fields(db, [])) == []
    orders.find.assert_not_called()
