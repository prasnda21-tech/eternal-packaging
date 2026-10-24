"""Wave 3 composition, category snapshot, product lock, and production location.

Local fixtures only. Does not read production data.
"""
from __future__ import annotations

import asyncio
import os
import sys
from datetime import date
from pathlib import Path

import pytest
from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("MONGO_URL", "mongodb://127.0.0.1:27017")

from services.line_product_replacement import (  # noqa: E402
    REASON_PRINT_CONFIRMED,
    confirmed_print_locks_line,
    evaluate_line_product_replacement,
)
from services.master_items import ERP_OWNED_FIELDS, upsert_master_item  # noqa: E402
from services.operator_schedule import apply_operator_assignments  # noqa: E402
from services.product_categories import create_category, set_master_product_category, update_category  # noqa: E402
from services.production_analytics import (  # noqa: E402
    build_branch_output,
    build_composition,
    build_plant_analytics,
    confirmation_snapshot,
)

TODAY = date(2026, 10, 23)
pytestmark = pytest.mark.anyio


def _entry(qty, action, **extra):
    body = {
        "qty": qty,
        "confirmed_at": "2026-10-23T02:00:00+00:00",
        "action_id": action,
        "legacy": False,
    }
    body.update(extra)
    return body


def _order(lines, splits, branch_id=None):
    doc = {"id": "po-1", "cancelled": False, "line_items": lines, "operator_splits": splits}
    if branch_id:
        doc["branch_id"] = branch_id
    return doc


def _line(name, unit="pcs", master=None, **extra):
    row = {"name": name, "quantity": 100, "unit": unit, "production_process": "print", "master_item_id": master}
    row.update(extra)
    return row


def _split(line_idx, entries, **extra):
    row = {
        "id": extra.pop("id", "s1"),
        "operator_email": extra.pop("operator_email", "a@x"),
        "operator_name": "Ani",
        "quantity": 100,
        "qty_completed": sum(int(e.get("qty") or 0) for e in entries),
        "line_idx": line_idx,
        "progress_entries": entries,
    }
    row.update(extra)
    return row


class _Col:
    def __init__(self):
        self.docs = []

    def _match(self, doc, query):
        for key, expected in (query or {}).items():
            if key == "$or":
                if not any(self._match(doc, part) for part in expected):
                    return False
                continue
            if isinstance(expected, dict) and "$ne" in expected:
                if doc.get(key) == expected["$ne"]:
                    return False
                continue
            if isinstance(expected, dict) and "$in" in expected:
                if doc.get(key) not in expected["$in"]:
                    return False
                continue
            if doc.get(key) != expected:
                return False
        return True

    async def find_one(self, query, projection=None):
        for doc in self.docs:
            if self._match(doc, query):
                return dict(doc)
        return None

    async def insert_one(self, doc):
        self.docs.append(dict(doc))

    async def update_one(self, query, update, upsert=False):
        for doc in self.docs:
            if self._match(doc, query):
                doc.update(update.get("$set") or {})
                return
        if upsert:
            self.docs.append(dict(update.get("$setOnInsert") or {}))

    async def create_index(self, *args, **kwargs):
        return "ok"

    def find(self, query, projection=None):
        rows = [dict(doc) for doc in self.docs if self._match(doc, query)]

        class _Cursor:
            def sort(self, *args, **kwargs):
                return self

            async def to_list(self, limit):
                return rows[:limit]

        return _Cursor()


class _DB:
    def __init__(self):
        self.cols = {}

    def __getitem__(self, name):
        self.cols.setdefault(name, _Col())
        return self.cols[name]


def test_confirmation_snapshot_freezes_category_name_and_branch():
    snap = confirmation_snapshot(
        {"master_item_id": "m1", "unit": "pack", "conversion_factor": 50, "base_uom": "PCS", "transaction_uom": "PACK", "name": "Cup"},
        {"id": "m1", "item_name": "Cup", "item_code": "C1", "product_category_id": "cat-1", "accurate_item_id": "9"},
        {"id": "cat-1", "name": "Paper Cup"},
        {"production_branch_id": "br-ygy"},
    )
    assert snap["category_snapshotted"] is True
    assert snap["category_name"] == "Paper Cup"
    assert snap["production_branch_id"] == "br-ygy"
    assert snap["conversion_factor"] == 50
    assert "branch_id" not in snap


def test_rename_does_not_change_historical_slice_and_old_events_stay_out():
    new = _entry(10, "new", category_snapshotted=True, master_item_id="m1", product_name="Cup", item_code="C1", category_id="cat", category_name="Paper Cup", unit="pcs")
    old = _entry(4, "old")
    body = build_composition([
        {"day": TODAY, "unit": "pcs", "qty": 10, "identity": "snapshot", "product_id": "m1", "product_name": "Cup", "item_code": "C1", "category_snapshotted": True, "category_id": "cat", "category_name": "Paper Cup"},
        {"day": TODAY, "unit": "pcs", "qty": 4, "identity": "unverified", "product_id": None, "category_snapshotted": False, "category_id": None, "category_name": None},
    ], TODAY, 7)
    assert body["categories"]["pcs"]["slices"][0]["name"] == "Paper Cup"
    assert body["categories"]["pcs"]["excluded_historical_qty"] == 4
    assert body["categories"]["pcs"]["status"] == "partial"
    plant = build_plant_analytics([
        _order([_line("Cup", master="m-now")], [_split(0, [new, old])]),
    ], today=TODAY)
    ranked = plant["composition"]["products"]["pcs"]
    assert [row["key"] for row in ranked] == ["m1"]
    assert plant["composition"]["unverified_by_unit"]["pcs"] == 4
    assert plant["today"]["by_unit"]["pcs"] == 14


def test_same_name_stays_two_products_and_units_do_not_mix():
    orders = [_order(
        [_line("Cup", master="m1"), _line("Cup", unit="pack", master="m2")],
        [
            _split(0, [_entry(8, "a", category_snapshotted=True, master_item_id="m1", product_name="Cup", item_code="A", unit="pcs", category_id="c", category_name="PET Cup")], id="s1"),
            _split(1, [_entry(2, "b", category_snapshotted=True, master_item_id="m2", product_name="Cup", item_code="B", unit="pack")], id="s2", operator_email="b@x"),
        ],
    )]
    body = build_plant_analytics(orders, today=TODAY)
    assert body["today"]["by_unit"] == {"pcs": 8, "pack": 2}
    names = [row["name"] for row in body["composition"]["products"]["pcs"]]
    assert names == ["Cup · A"]
    assert body["composition"]["products"]["pack"][0]["key"] == "m2"
    assert "pcs" not in body["composition"]["categories"]["pack"]["slices"][0] if False else body["composition"]["products"]["pack"][0]["qty"] == 2


def test_legacy_cancelled_duplicate_and_missing_master():
    entries = [
        {"qty": 9, "confirmed_at": "2026-10-23T02:00:00+00:00", "action_id": "leg", "legacy": True},
        {"qty": 3, "confirmed_at": "2026-10-23T02:00:00+00:00", "action_id": "can", "cancelled": True},
        _entry(5, "dup", category_snapshotted=True, master_item_id="m1", product_name="Cup", unit="pcs"),
        _entry(5, "dup", category_snapshotted=True, master_item_id="m1", product_name="Cup", unit="pcs"),
        _entry(1, "bare", category_snapshotted=True, unit="pcs"),
    ]
    body = build_plant_analytics([
        _order([_line("Cup", master="m1")], [_split(0, entries), _split(0, [_entry(2, "other", category_snapshotted=True, master_item_id="m1", product_name="Cup", unit="pcs")], id="s2", operator_email="b@x")]),
    ], today=TODAY)
    assert body["coverage"]["legacy"] == 1
    assert body["coverage"]["cancelled"] == 1
    assert body["coverage"]["duplicate"] == 1
    assert body["today"]["by_unit"]["pcs"] == 8
    assert body["composition"]["unmapped_by_unit"]["pcs"] == 1
    assert body["composition"]["products"]["pcs"][0]["qty"] == 7


def test_pack_without_factor_stays_pack_even_if_line_later_has_a_factor():
    entry = _entry(3, "pack", category_snapshotted=True, master_item_id="m1", product_name="Cup", unit="pack")
    line = _line("Cup", unit="pack", master="m1", conversion_factor=100, base_uom="PCS", transaction_uom="PACK")
    body = build_plant_analytics([_order([line], [_split(0, [entry])])], today=TODAY)
    assert body["today"]["by_unit"] == {"pack": 3}
    snap_line = _line("Cup", unit="pack", master="m1", conversion_factor=10, base_uom="PCS", transaction_uom="PACK")
    frozen = _entry(2, "ok", category_snapshotted=True, master_item_id="m1", product_name="Cup", unit="pack", conversion_factor=10, base_uom="PCS", transaction_uom="PACK")
    converted = build_plant_analytics([_order([snap_line], [_split(0, [frozen])])], today=TODAY)
    assert converted["today"]["by_unit"] == {"pcs": 20}


def test_branch_actual_and_origin_do_not_double_count():
    names = {"br-ygy": "Yogyakarta", "br-smg": "Semarang"}
    first = _entry(6, "a", category_snapshotted=True, master_item_id="m1", product_name="Cup", unit="pcs", production_branch_id="br-ygy")
    second = _entry(4, "b", category_snapshotted=True, master_item_id="m1", product_name="Cup", unit="pcs", production_branch_id="br-smg")
    old = _entry(1, "old")
    orders = [_order([_line("Cup", master="m1")], [
        _split(0, [first], id="s1", production_branch_id="br-ygy"),
        _split(0, [second, old], id="s2", operator_email="b@x", production_branch_id="br-smg"),
    ], branch_id="br-smg")]
    body = build_branch_output(orders, branch_names=names, today=TODAY)
    assert body["company_total"]["pcs"] == 11
    actual = {row["name"]: row["qty"] for row in body["actual"]["pcs"]}
    assert actual == {"Yogyakarta": 6, "Semarang": 4, "Belum Teratribusi": 1}
    origin = {row["name"]: row["qty"] for row in body["origin"]["pcs"]}
    assert origin == {"Semarang": 11}
    assert sum(row["qty"] for row in body["actual"]["pcs"]) == body["company_total"]["pcs"]
    missing = build_branch_output([_order([_line("Cup")], [_split(0, [_entry(2, "x")])])], branch_names=names, today=TODAY)
    assert missing["origin"]["pcs"][0]["name"] == "Belum Teratribusi"
    invented = build_branch_output([
        _order([_line("Cup")], [_split(0, [_entry(2, "y", category_snapshotted=True, unit="pcs", production_branch_id="nope")])])
    ], branch_names=names, today=TODAY)
    assert invented["actual"]["pcs"][0]["name"] == "Belum Teratribusi"


def test_product_lock_partial_duplicate_and_two_operators():
    order = _order(
        [_line("Cup", master="m1")],
        [_split(0, [_entry(1, "one")], id="s1"), _split(0, [], id="s2", operator_email="b@x")],
    )
    assert confirmed_print_locks_line(order, 0) is True
    assert confirmed_print_locks_line(order, 1) is False
    legacy = _order([_line("Cup")], [_split(0, [{"qty": 5, "legacy": True, "action_id": "leg", "confirmed_at": "2026-10-23T02:00:00+00:00"}])])
    assert confirmed_print_locks_line(legacy, 0) is False
    duplicate = _order([_line("Cup")], [_split(0, [
        {"qty": 0, "action_id": "same", "confirmed_at": "2026-10-23T02:00:00+00:00"},
        {"qty": 4, "action_id": "same", "confirmed_at": "2026-10-23T02:00:00+00:00"},
    ])])
    assert confirmed_print_locks_line(duplicate, 0) is False


async def test_evaluate_rejects_product_change_after_confirmed_print():
    order = _order([_line("Cup", master="m1")], [_split(0, [_entry(2, "done")])])

    class _Empty:
        async def find_one(self, *args, **kwargs):
            return None

    class _DBView:
        def __getattr__(self, name):
            return _Empty()

    guard = await evaluate_line_product_replacement(_DBView(), order, 0, commitment_ctx={})
    assert guard["allowed"] is False
    assert guard["reason_code"] == REASON_PRINT_CONFIRMED
    open_line = _order([_line("Cup", master="m1")], [_split(0, [])])
    allowed = await evaluate_line_product_replacement(_DBView(), open_line, 0, commitment_ctx={})
    assert allowed["allowed"] is True


def test_branch_move_keeps_confirmed_qty_on_the_original_split():
    order = _order([_line("Cup", master="m1")], [])
    order["operator_splits"] = [_split(0, [_entry(4, "done")], id="s1", production_branch_id="br-ygy", quantity=10, qty_completed=4)]
    with pytest.raises(ValueError):
        apply_operator_assignments(order, [{
            "id": "s1", "operator_email": "a@x", "operator_name": "Ani", "quantity": 10,
            "line_idx": 0, "production_branch_id": "br-smg",
        }], {"email": "lead@x"}, "2026-10-23T00:00:00+00:00", new_id_fn=lambda: "s-new")
    moved, _notes = apply_operator_assignments(order, [
        {"id": "s1", "operator_email": "a@x", "operator_name": "Ani", "quantity": 4, "line_idx": 0, "production_branch_id": "br-ygy"},
        {"operator_email": "a@x", "operator_name": "Ani", "quantity": 6, "line_idx": 0, "production_branch_id": "br-smg"},
    ], {"email": "lead@x"}, "2026-10-23T00:00:00+00:00", new_id_fn=lambda: "s-new")
    old = next(row for row in moved if row["id"] == "s1")
    new = next(row for row in moved if row["id"] == "s-new")
    assert old["production_branch_id"] == "br-ygy"
    assert old["progress_entries"][0]["action_id"] == "done"
    assert new["production_branch_id"] == "br-smg"
    assert new["progress_entries"] == []
    assert sum(row["quantity"] for row in moved) == 10


def test_branch_change_before_confirmation_is_recorded():
    order = _order([_line("Cup")], [_split(0, [], id="s1", production_branch_id="br-ygy", quantity=10, qty_completed=0)])
    apply_operator_assignments(order, [{
        "id": "s1", "operator_email": "a@x", "operator_name": "Ani", "quantity": 10,
        "line_idx": 0, "production_branch_id": "br-smg",
    }], {"email": "lead@x"}, "2026-10-23T00:00:00+00:00", new_id_fn=lambda: "x")
    assert order["operator_splits"][0]["production_branch_id"] == "br-smg"
    assert order["production_location_revisions"][0]["from_branch_id"] == "br-ygy"
    assert order["production_location_revisions"][0]["to_branch_id"] == "br-smg"
    apply_operator_assignments(order, [{
        "id": "s1", "operator_email": "a@x", "operator_name": "Ani", "quantity": 10, "line_idx": 0,
    }], {"email": "other@x"}, "2026-10-23T01:00:00+00:00", new_id_fn=lambda: "x")
    assert order["operator_splits"][0]["production_branch_id"] == "br-smg"


async def test_category_master_survives_accurate_upsert_and_allows_blank():
    db = _DB()
    created = await create_category(db, "Paper Cup", user={"email": "lead@x"})
    renamed = await update_category(db, created["id"], name="Paper Cup Baru", user={"email": "lead@x"})
    assert renamed["name"] == "Paper Cup Baru"
    assert renamed["id"] == created["id"]
    db["master_items"].docs.append({
        "id": "m1",
        "accurate_item_id": "55",
        "item_name": "Cup",
        "item_code": "C",
        "item_type": "INVENTORY",
        "unit": "pcs",
        "is_active": True,
        "origin": "accurate",
        "product_category_id": created["id"],
    })
    fresh, action = await upsert_master_item(db, {
        "accurate_item_id": "55",
        "item_name": "Cup Renamed",
        "item_code": "C",
        "item_type": "INVENTORY",
        "unit": "pcs",
        "is_active": True,
        "product_category_id": None,
    })
    assert action == "updated"
    assert fresh["product_category_id"] == created["id"]
    assert "product_category_id" in ERP_OWNED_FIELDS
    cleared = await set_master_product_category(db, "m1", None, user={"email": "lead@x"})
    assert cleared["product_category_id"] is None
    with pytest.raises(HTTPException):
        await set_master_product_category(db, "m1", "missing", user={"email": "lead@x"})
    await update_category(db, created["id"], active=False, user={"email": "lead@x"})
    with pytest.raises(HTTPException) as inactive:
        await set_master_product_category(db, "m1", created["id"], user={"email": "lead@x"})
    assert inactive.value.status_code == 400


def test_get_builders_do_not_mutate_orders():
    order = _order([_line("Cup", master="m1")], [_split(0, [_entry(1, "a", category_snapshotted=True, master_item_id="m1", product_name="Cup", unit="pcs")])])
    before = repr(order)
    build_plant_analytics([order], today=TODAY)
    build_branch_output([order], branch_names={}, today=TODAY)
    assert repr(order) == before
