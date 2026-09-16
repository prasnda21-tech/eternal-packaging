"""Write-time exact-SJ canonical identity for Loading confirmations.

Qty math is asserted unchanged. Identity must fail closed when not unique.
"""
from __future__ import annotations

import copy
import os
import uuid

import pytest

os.environ.setdefault("MONGO_URL", "mongodb://localhost:27017")
os.environ.setdefault("DB_NAME", "eternal_packaging_test")

import httpx

import server
from server import app, current_user
from quantity_rules import (
    LOADING_WRITE_SJ_UNRESOLVED,
    apply_loading_confirmation,
    apply_loading_discrepancy,
    apply_loading_match_confirmation,
    loaded_qty_for_sj_line,
    qty_loaded_for_line,
    resolve_loading_write_sj_identity,
    validate_loading_confirmation,
    validate_loading_discrepancy,
    validate_loading_match_confirmation,
)


SJ_A = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
SJ_B = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"


def _order(*, extra_files=None, extra_ships=None, confs=None, snapshots=None):
    files = [
        {
            "id": SJ_A,
            "shipment_id": SJ_A,
            "sj_number": "SJ-A",
            "name": "SJ-A.pdf",
            "lines": [{"line_idx": 0, "qty": 1000}],
            "total_qty": 1000,
        },
    ]
    ships = [
        {
            "id": SJ_A,
            "number": "SJ-A",
            "lines": [{"line_idx": 0, "qty": 1000}],
            "total_qty": 1000,
        },
    ]
    if extra_files:
        files.extend(extra_files)
    if extra_ships:
        ships.extend(extra_ships)
    return {
        "id": "po-canonical-load",
        "po_number": "PO/CANON",
        "line_items": [
            {
                "name": "Cup",
                "quantity": 4000,
                "production_process": "print",
                "accurate_item_id": "cup-1",
            }
        ],
        "total_quantity": 4000,
        "packing_info": {"qty_packed": 4000, "lines": [{"line_idx": 0, "packed_qty": 4000}]},
        "surat_jalan_files": files,
        "shipments": ships,
        "loading_confirmations": list(confs or []),
        "loading_snapshots": list(snapshots or []),
        "loading_discrepancies": [],
        "loading_info": {},
    }


def _sj_b_file():
    return {
        "id": SJ_B,
        "shipment_id": SJ_B,
        "sj_number": "SJ-B",
        "name": "SJ-B.pdf",
        "lines": [{"line_idx": 0, "qty": 1500}],
        "total_qty": 1500,
    }


def _sj_b_ship():
    return {
        "id": SJ_B,
        "number": "SJ-B",
        "lines": [{"line_idx": 0, "qty": 1500}],
        "total_qty": 1500,
    }


def _assert_canonical(rec, canonical, number):
    assert rec["surat_jalan_id"] == canonical
    assert rec["shipment_id"] == canonical
    assert rec["sj_number"] == number
    assert rec["surat_jalan_id"] == rec["shipment_id"]


def test_match_writer_persists_canonical_identity():
    o = _order()
    rec = apply_loading_match_confirmation(
        o, surat_jalan_id=SJ_A, actor={"email": "a@x"}, confirmation_id="c1",
        confirmed_at="t", action_id="a1",
    )
    _assert_canonical(rec, SJ_A, "SJ-A")
    assert rec["source"] == "sj_match"
    assert rec["lines"][0]["qty"] == 1000
    stored = o["loading_confirmations"][-1]
    _assert_canonical(stored, SJ_A, "SJ-A")
    assert stored["lines"][0]["qty"] == 1000


def test_direct_writer_persists_canonical_identity_and_exact_qty():
    o = _order()
    rec = apply_loading_confirmation(
        o, surat_jalan_id=SJ_A, lines=[{"line_idx": 0, "qty": 400}],
        actor={"email": "a@x"}, confirmation_id="c1", confirmed_at="t", action_id="a1",
    )
    _assert_canonical(rec, SJ_A, "SJ-A")
    assert rec["lines"] == [{"line_idx": 0, "qty": 400, "name": "Cup", "accurate_item_id": "cup-1"}]
    assert o["loading_confirmations"][-1]["lines"][0]["qty"] == 400


def test_match_qty_is_server_side_remaining_sj():
    o = _order()
    rec = apply_loading_match_confirmation(
        o, surat_jalan_id=SJ_A, actor={"email": "a@x"}, confirmation_id="c1",
        confirmed_at="t", action_id="a1",
    )
    assert [ln["qty"] for ln in rec["lines"]] == [1000]
    o2 = _order()
    apply_loading_confirmation(
        o2, surat_jalan_id=SJ_A, lines=[{"line_idx": 0, "qty": 250}], confirmation_id="prior",
    )
    rec2 = apply_loading_match_confirmation(
        o2, surat_jalan_id=SJ_A, actor={"email": "a@x"}, confirmation_id="c2",
        confirmed_at="t2", action_id="a2",
    )
    assert rec2["lines"][0]["qty"] == 750


def test_multi_sj_selected_b_does_not_touch_a():
    o = _order(extra_files=[_sj_b_file()], extra_ships=[_sj_b_ship()])
    rec = apply_loading_match_confirmation(
        o, surat_jalan_id=SJ_B, actor={"email": "a@x"}, confirmation_id="c-b",
        confirmed_at="t", action_id="ab",
    )
    _assert_canonical(rec, SJ_B, "SJ-B")
    assert rec["lines"][0]["qty"] == 1500
    assert all(c["surat_jalan_id"] != SJ_A for c in o["loading_confirmations"])
    assert loaded_qty_for_sj_line(o, SJ_A, 0) == 0
    assert loaded_qty_for_sj_line(o, SJ_B, 0) == 1500
    assert qty_loaded_for_line(o, 0) == 1500


def test_unknown_identity_fail_closed():
    o = _order()
    before = copy.deepcopy(o["loading_confirmations"])
    ident, err = resolve_loading_write_sj_identity(o, "missing-sj")
    assert ident is None
    assert "tidak ditemukan" in err.lower()
    assert validate_loading_confirmation(o, "missing-sj", [{"line_idx": 0, "qty": 10}])
    assert validate_loading_match_confirmation(o, "missing-sj")
    with pytest.raises(ValueError):
        apply_loading_confirmation(o, surat_jalan_id="missing-sj", lines=[{"line_idx": 0, "qty": 10}])
    with pytest.raises(ValueError):
        apply_loading_match_confirmation(o, surat_jalan_id="missing-sj")
    assert o["loading_confirmations"] == before


def test_stale_identity_no_first_sj_fallback():
    o = _order(extra_files=[_sj_b_file()], extra_ships=[_sj_b_ship()])
    stale = "stale-deleted-sj"
    before = copy.deepcopy(o["loading_confirmations"])
    ident, err = resolve_loading_write_sj_identity(o, stale)
    assert ident is None
    assert "tidak ditemukan" in err.lower()
    with pytest.raises(ValueError):
        apply_loading_match_confirmation(o, surat_jalan_id=stale)
    assert o["loading_confirmations"] == before
    assert loaded_qty_for_sj_line(o, SJ_A, 0) == 0


def test_file_without_unique_shipment_fail_closed():
    o = _order()
    o["shipments"] = []
    before = copy.deepcopy(o["loading_confirmations"])
    ident, err = resolve_loading_write_sj_identity(o, SJ_A)
    assert ident is None
    assert err == LOADING_WRITE_SJ_UNRESOLVED
    with pytest.raises(ValueError, match="Identitas Surat Jalan"):
        apply_loading_confirmation(o, surat_jalan_id=SJ_A, lines=[{"line_idx": 0, "qty": 10}])
    assert o["loading_confirmations"] == before


def test_ambiguous_two_shipments_fail_closed():
    o = _order()
    o["shipments"].append({"id": "other", "number": "SJ-A", "file_id": SJ_A})
    ident, err = resolve_loading_write_sj_identity(o, SJ_A)
    assert ident is None
    assert err == LOADING_WRITE_SJ_UNRESOLVED
    with pytest.raises(ValueError):
        apply_loading_match_confirmation(o, surat_jalan_id=SJ_A)


def test_file_exists_but_sj_number_unproven_fail_closed():
    o = _order()
    o["shipments"][0]["number"] = ""
    o["shipments"][0].pop("sj_number", None)
    ident, err = resolve_loading_write_sj_identity(o, SJ_A)
    assert ident is None
    assert err == LOADING_WRITE_SJ_UNRESOLVED
    with pytest.raises(ValueError):
        apply_loading_confirmation(o, surat_jalan_id=SJ_A, lines=[{"line_idx": 0, "qty": 10}])
    assert o["loading_confirmations"] == []


def test_conflicting_file_and_shipment_number_fail_closed():
    o = _order()
    o["surat_jalan_files"][0]["sj_number"] = "SJ-OTHER"
    ident, err = resolve_loading_write_sj_identity(o, SJ_A)
    assert ident is None
    assert err == LOADING_WRITE_SJ_UNRESOLVED


def test_snapshot_only_persists_canonical_identity():
    o = _order()
    apply_loading_match_confirmation(
        o, surat_jalan_id=SJ_A, actor={"email": "a@x"}, confirmation_id="c1",
        confirmed_at="t", action_id="a1",
    )
    o["surat_jalan_files"][0]["lines"][0]["qty"] = 800
    o["shipments"][0]["lines"][0]["qty"] = 800
    rec = apply_loading_match_confirmation(
        o, surat_jalan_id=SJ_A, actor={"email": "a@x"}, confirmation_id="c2",
        confirmed_at="t2", action_id="a2",
    )
    assert rec.get("snapshot_only") is True
    _assert_canonical(rec, SJ_A, "SJ-A")
    snap = o["loading_snapshots"][-1]
    _assert_canonical(snap, SJ_A, "SJ-A")
    qty_rows = [c for c in o["loading_confirmations"] if not c.get("snapshot_only")]
    assert len(qty_rows) == 1
    assert qty_rows[0]["lines"][0]["qty"] == 1000


def test_physical_and_selected_sj_math_unchanged_with_canonical_fields():
    o = _order(extra_files=[_sj_b_file()], extra_ships=[_sj_b_ship()])
    apply_loading_confirmation(
        o, surat_jalan_id=SJ_A, lines=[{"line_idx": 0, "qty": 400}], confirmation_id="ca",
    )
    apply_loading_confirmation(
        o, surat_jalan_id=SJ_B, lines=[{"line_idx": 0, "qty": 250}], confirmation_id="cb",
    )
    assert qty_loaded_for_line(o, 0) == 650
    assert loaded_qty_for_sj_line(o, SJ_A, 0) == 400
    assert loaded_qty_for_sj_line(o, SJ_B, 0) == 250
    sibling = copy.deepcopy(o)
    sibling["loading_confirmations"][0].pop("shipment_id", None)
    sibling["loading_confirmations"][0].pop("sj_number", None)
    sibling["loading_confirmations"][1].pop("shipment_id", None)
    sibling["loading_confirmations"][1].pop("sj_number", None)
    assert qty_loaded_for_line(o, 0) == qty_loaded_for_line(sibling, 0)
    assert loaded_qty_for_sj_line(o, SJ_A, 0) == loaded_qty_for_sj_line(sibling, SJ_A, 0)
    assert loaded_qty_for_sj_line(o, SJ_B, 0) == loaded_qty_for_sj_line(sibling, SJ_B, 0)


def test_discrepancy_does_not_create_loading_confirmation():
    o = _order()
    before = copy.deepcopy(o["loading_confirmations"])
    assert validate_loading_discrepancy(o, SJ_A, [{"line_idx": 0, "actual_qty": 10}]) is None
    apply_loading_discrepancy(
        o, surat_jalan_id=SJ_A, mismatches=[{"line_idx": 0, "actual_qty": 10}],
        actor={"email": "r@x"}, discrepancy_id="d1", reported_at="t", action_id="d",
    )
    assert o["loading_confirmations"] == before
    assert o["loading_discrepancies"]


@pytest.fixture
def anyio_backend():
    return "asyncio"


def _bind_motor():
    from motor.motor_asyncio import AsyncIOMotorClient
    server.client = AsyncIOMotorClient(os.environ.get("MONGO_URL", "mongodb://localhost:27017"))
    server.db = server.client[os.environ.get("DB_NAME", "eternal_packaging")]


def _admin():
    return {"email": "admin@eternalpackaging.com", "name": "Admin Tester", "role": "admin"}


async def _insert_po(*, extra_files=None, extra_ships=None):
    _bind_motor()
    po = _order(extra_files=extra_files, extra_ships=extra_ships)
    po["id"] = str(uuid.uuid4())
    po["current_stage"] = 13
    po["cancelled"] = False
    po["created_at"] = "2026-09-16T00:00:00+00:00"
    po["updated_at"] = "2026-09-16T00:00:00+00:00"
    await server.db.orders.insert_one(copy.deepcopy(po))
    return po


@pytest.mark.anyio
async def test_http_match_and_direct_writers_persist_canonical_identity():
    po = await _insert_po()
    app.dependency_overrides[current_user] = _admin
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            match = await client.post(
                f"/api/orders/{po['id']}/loading-confirm-match",
                json={"surat_jalan_id": SJ_A, "action_id": "act-match-1"},
            )
            assert match.status_code == 200, match.text
            rec = match.json()["confirmation"]
            _assert_canonical(rec, SJ_A, "SJ-A")
            assert rec["lines"][0]["qty"] == 1000

            po2 = await _insert_po()
            direct = await client.post(
                f"/api/orders/{po2['id']}/loading-confirm",
                json={
                    "surat_jalan_id": SJ_A,
                    "action_id": "act-direct-1",
                    "lines": [{"line_idx": 0, "qty": 400}],
                },
            )
            assert direct.status_code == 200, direct.text
            stored = await server.db.orders.find_one({"id": po2["id"]}, {"_id": 0, "loading_confirmations": 1})
            conf = stored["loading_confirmations"][-1]
            _assert_canonical(conf, SJ_A, "SJ-A")
            assert conf["lines"][0]["qty"] == 400
    finally:
        app.dependency_overrides.pop(current_user, None)


@pytest.mark.anyio
async def test_http_multi_sj_unknown_stale_and_idempotent():
    po = await _insert_po(extra_files=[_sj_b_file()], extra_ships=[_sj_b_ship()])
    app.dependency_overrides[current_user] = _admin
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            ok_b = await client.post(
                f"/api/orders/{po['id']}/loading-confirm-match",
                json={"surat_jalan_id": SJ_B, "action_id": "act-b"},
            )
            assert ok_b.status_code == 200, ok_b.text
            rec = ok_b.json()["confirmation"]
            _assert_canonical(rec, SJ_B, "SJ-B")

            again = await client.post(
                f"/api/orders/{po['id']}/loading-confirm-match",
                json={"surat_jalan_id": SJ_B, "action_id": "act-b"},
            )
            assert again.status_code == 200, again.text
            assert again.json().get("idempotent") is True

            missing = await client.post(
                f"/api/orders/{po['id']}/loading-confirm-match",
                json={"surat_jalan_id": "unknown-sj", "action_id": "act-miss"},
            )
            assert missing.status_code == 400

            stale = await client.post(
                f"/api/orders/{po['id']}/loading-confirm",
                json={
                    "surat_jalan_id": "stale-sj-old",
                    "action_id": "act-stale",
                    "lines": [{"line_idx": 0, "qty": 10}],
                },
            )
            assert stale.status_code == 400

            stored = await server.db.orders.find_one({"id": po["id"]}, {"_id": 0, "loading_confirmations": 1})
            active = [c for c in stored["loading_confirmations"] if not c.get("cancelled")]
            assert len(active) == 1
            _assert_canonical(active[0], SJ_B, "SJ-B")
            assert all(c.get("surat_jalan_id") != SJ_A for c in active)
    finally:
        app.dependency_overrides.pop(current_user, None)
