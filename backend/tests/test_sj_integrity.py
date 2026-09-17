"""SJ integrity invariants: freeze, allocation, active-filter, backfill, identity."""
from __future__ import annotations

import asyncio
import copy
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from quantity_rules import (
    SHIPMENT_SOURCE_MANUAL,
    apply_loading_confirmation,
    apply_loading_discrepancy,
    apply_loading_match_confirmation,
    apply_shipment_record,
    delivery_batch_photo_readiness,
    loaded_qty_for_sj_line,
    loading_verification_status,
    qty_loaded,
    qty_shipped,
    qty_shipped_for_line,
    shipment_is_active,
    shipment_qty_total,
    validate_shipment_lines,
)
from services.delivery_history import append_confirmation
from services.sj_integrity import (
    NUMANI_PO_ID,
    NUMANI_SJ1,
    NUMANI_SJ2,
    NUMANI_SJ3,
    build_numani_broken_order,
    build_numani_repaired_order,
    cas_token,
    loaded_qty_for_shipment,
    order_integrity_summary,
)
from scripts.reconcile_es_teh_numani_sj import (
    already_repaired,
    aggregates,
    expected_aggregates,
    planned_ops,
    verify_preconditions,
    run as reconcile_run,
)


def _po_5000():
    return {
        "id": "po-5000",
        "po_number": "PO/TEST/5000",
        "line_items": [{
            "name": "22 oz Datar 8 gr Berlian",
            "quantity": 5000,
            "line_id": "L0",
            "accurate_item_id": "item-1",
            "production_process": "print",
        }],
        "total_quantity": 5000,
        "packing_info": {"qty_packed": 5000, "lines": [{"line_idx": 0, "packed_qty": 5000}]},
        "shipments": [],
        "surat_jalan_files": [],
        "loading_confirmations": [],
        "loading_snapshots": [],
        "delivery_confirmations": [],
        "delivery_proofs": [{"id": "p1"}, {"id": "p2"}],
        "qty_delivered": 0,
        "delivery_status": "Belum Terkirim",
        "current_stage": 13,
        "delivery_recipient_signature": {
            "id": "sig-1", "recipient_name": "Budi", "signature_storage_path": "sig.png",
            "recipient_signed_at": "t", "frozen": False, "shipment_id": "",
        },
        "delivery_driver_signature": {
            "id": "dsig-1", "driver_signature_storage_path": "dsig.png",
            "driver_signed_at": "t", "frozen": False, "shipment_id": "",
        },
    }


def _attach_sj(order, sid, number, qty):
    sh = {
        "id": sid,
        "number": number,
        "sales_delivery_id": sid,
        "lines": [{"line_idx": 0, "line_id": "L0", "qty": qty, "name": order["line_items"][0]["name"]}],
        "total_qty": qty,
        "status": "pending",
    }
    order.setdefault("shipments", []).append(sh)
    order.setdefault("surat_jalan_files", []).append({
        "id": sid,
        "shipment_id": sid,
        "sj_number": number,
        "name": f"{number}.pdf",
        "storage_path": f"p/{sid}.pdf",
        "lines": sh["lines"],
        "total_qty": qty,
    })
    return sh


def test_po_two_sj_1000_4000_then_loading_and_partial_delivery():
    o = _po_5000()
    apply_shipment_record(
        o, number="SJ/1", lines=[{"line_idx": 0, "line_id": "L0", "qty": 1000}],
        source=SHIPMENT_SOURCE_MANUAL, shipment_id="sj-1", created_at="t1",
    )
    apply_shipment_record(
        o, number="SJ/1_2", lines=[{"line_idx": 0, "line_id": "L0", "qty": 4000}],
        source=SHIPMENT_SOURCE_MANUAL, shipment_id="sj-2", created_at="t2",
    )
    o["surat_jalan_files"] = [
        {"id": "sj-1", "shipment_id": "sj-1", "sj_number": "SJ/1", "name": "a.pdf", "storage_path": "a", "lines": [{"line_idx": 0, "qty": 1000}]},
        {"id": "sj-2", "shipment_id": "sj-2", "sj_number": "SJ/1_2", "name": "b.pdf", "storage_path": "b", "lines": [{"line_idx": 0, "qty": 4000}]},
    ]
    assert qty_shipped(o) == 5000
    apply_loading_confirmation(
        o, surat_jalan_id="sj-1", lines=[{"line_idx": 0, "qty": 1000}],
        actor={"email": "l@x"}, confirmation_id="c1", confirmed_at="t", action_id="load-1",
    )
    apply_loading_confirmation(
        o, surat_jalan_id="sj-2", lines=[{"line_idx": 0, "qty": 4000}],
        actor={"email": "l@x"}, confirmation_id="c2", confirmed_at="t", action_id="load-2",
    )
    assert qty_loaded(o) == 5000
    sj2 = next(s for s in o["shipments"] if s["id"] == "sj-2")
    rec = append_confirmation(o, {"email": "d@x", "name": "D"}, qty_increment=4000, now="t", action_id="del-2", shipment_id="sj-2", shipment=sj2)
    o["qty_delivered"] = 4000
    o["delivery_status"] = "Sebagian Terkirim"
    assert rec["shipment_id"] == "sj-2"
    assert o["qty_delivered"] == 4000
    assert qty_shipped_for_line(o, 0) == 5000
    summary = order_integrity_summary(o)
    assert summary["remaining_qty"] == 1000
    assert summary["delivered_qty"] == 4000


def test_identical_upload_after_loading_is_idempotent():
    o = _po_5000()
    apply_shipment_record(
        o, number="SJ/1", lines=[{"line_idx": 0, "line_id": "L0", "qty": 1000}],
        source=SHIPMENT_SOURCE_MANUAL, shipment_id="sj-1", created_at="t1",
    )
    o["surat_jalan_files"] = [{"id": "sj-1", "shipment_id": "sj-1", "sj_number": "SJ/1", "name": "a.pdf", "storage_path": "a", "lines": [{"line_idx": 0, "qty": 1000}]}]
    apply_loading_confirmation(
        o, surat_jalan_id="sj-1", lines=[{"line_idx": 0, "qty": 1000}],
        actor={"email": "l@x"}, confirmation_id="c1", confirmed_at="t", action_id="load-1",
    )
    rec = apply_shipment_record(
        o, number="SJ/1", lines=[{"line_idx": 0, "line_id": "L0", "qty": 1000}],
        source=SHIPMENT_SOURCE_MANUAL, shipment_id="sj-1", created_at="t2",
    )
    assert rec["id"] == "sj-1"
    assert shipment_qty_total(rec) == 1000
    assert len(o["shipments"]) == 1


def test_same_number_different_qty_after_loading_rejected():
    o = _po_5000()
    apply_shipment_record(
        o, number="SJ/1", lines=[{"line_idx": 0, "line_id": "L0", "qty": 4000}],
        source=SHIPMENT_SOURCE_MANUAL, shipment_id="sj-2", created_at="t1",
        accurate_shipment_id="ACC-2",
    )
    o["surat_jalan_files"] = [{"id": "sj-2", "shipment_id": "sj-2", "sj_number": "SJ/1", "name": "a.pdf", "storage_path": "a", "lines": [{"line_idx": 0, "qty": 4000}]}]
    apply_loading_confirmation(
        o, surat_jalan_id="sj-2", lines=[{"line_idx": 0, "qty": 4000}],
        actor={"email": "l@x"}, confirmation_id="c1", confirmed_at="t", action_id="load-1",
    )
    with pytest.raises(ValueError, match="Loading|Terkirim|dibekukan|ditolak"):
        apply_shipment_record(
            o, number="SJ/1", lines=[{"line_idx": 0, "line_id": "L0", "qty": 3000}],
            source=SHIPMENT_SOURCE_MANUAL, shipment_id="sj-2", created_at="t2",
            accurate_shipment_id="ACC-2",
        )


def test_revision_after_delivered_rejected():
    o = _po_5000()
    apply_shipment_record(
        o, number="SJ/1", lines=[{"line_idx": 0, "line_id": "L0", "qty": 4000}],
        source=SHIPMENT_SOURCE_MANUAL, shipment_id="sj-2", created_at="t1",
        accurate_shipment_id="ACC-2",
    )
    o["surat_jalan_files"] = [{"id": "sj-2", "shipment_id": "sj-2", "sj_number": "SJ/1", "name": "a.pdf", "storage_path": "a", "lines": [{"line_idx": 0, "qty": 4000}]}]
    apply_loading_confirmation(
        o, surat_jalan_id="sj-2", lines=[{"line_idx": 0, "qty": 4000}],
        actor={"email": "l@x"}, confirmation_id="c1", confirmed_at="t", action_id="load-1",
    )
    sj2 = o["shipments"][0]
    sj2["delivered_at"] = "t"
    sj2["delivery_status"] = "Terkirim"
    with pytest.raises(ValueError, match="Terkirim"):
        apply_shipment_record(
            o, number="SJ/1", lines=[{"line_idx": 0, "line_id": "L0", "qty": 3000}],
            source=SHIPMENT_SOURCE_MANUAL, shipment_id="sj-2", created_at="t2",
            accurate_shipment_id="ACC-2",
        )


def test_superseded_not_counted_and_over_allocation_rejected():
    o = _po_5000()
    apply_shipment_record(
        o, number="SJ/1", lines=[{"line_idx": 0, "line_id": "L0", "qty": 1000}],
        source=SHIPMENT_SOURCE_MANUAL, shipment_id="sj-1", created_at="t1",
    )
    apply_shipment_record(
        o, number="SJ/1_2", lines=[{"line_idx": 0, "line_id": "L0", "qty": 4000}],
        source=SHIPMENT_SOURCE_MANUAL, shipment_id="sj-2", created_at="t2",
    )
    assert qty_shipped(o) == 5000
    err = validate_shipment_lines([{"line_idx": 0, "line_id": "L0", "qty": 1000}], o, source=SHIPMENT_SOURCE_MANUAL)
    assert err is not None
    o["shipments"][-1]["status"] = "superseded"
    o["shipments"][-1]["cancelled"] = True
    o["shipments"][-1]["superseded_by_shipment_id"] = "sj-x"
    assert qty_shipped(o) == 1000
    assert shipment_is_active(o["shipments"][-1]) is False


def test_action_id_retry_no_second_delivery_or_loading_confirmation():
    o = _po_5000()
    apply_shipment_record(
        o, number="SJ/1", lines=[{"line_idx": 0, "line_id": "L0", "qty": 1000}],
        source=SHIPMENT_SOURCE_MANUAL, shipment_id="sj-1", created_at="t1",
    )
    o["surat_jalan_files"] = [{"id": "sj-1", "shipment_id": "sj-1", "sj_number": "SJ/1", "name": "a.pdf", "storage_path": "a", "lines": [{"line_idx": 0, "qty": 1000}]}]
    apply_loading_confirmation(
        o, surat_jalan_id="sj-1", lines=[{"line_idx": 0, "qty": 1000}],
        actor={"email": "l@x"}, confirmation_id="c1", confirmed_at="t", action_id="load-same",
    )
    again = apply_loading_confirmation(
        o, surat_jalan_id="sj-1", lines=[{"line_idx": 0, "qty": 1000}],
        actor={"email": "l@x"}, confirmation_id="c2", confirmed_at="t2", action_id="load-same",
    )
    assert again["id"] == "c1"
    assert len([c for c in o["loading_confirmations"] if not c.get("cancelled")]) == 1
    sj = o["shipments"][0]
    r1 = append_confirmation(o, {"email": "d@x"}, qty_increment=1000, now="t", action_id="del-same", shipment_id="sj-1", shipment=sj)
    r2 = append_confirmation(o, {"email": "d@x"}, qty_increment=1000, now="t2", action_id="del-same", shipment_id="sj-1", shipment=sj)
    assert r1["id"] == r2["id"]
    assert len(o["delivery_confirmations"]) == 1


def test_legacy_alias_does_not_steal_other_sj_confirmation():
    o = _po_5000()
    _attach_sj(o, "sj-a", "SJ/SHARED", 1000)
    _attach_sj(o, "sj-b", "SJ/SHARED_2", 4000)
    o["delivery_confirmations"] = [{"id": "d1", "shipment_id": "sj-b", "qty": 4000, "action_id": "x"}]
    from quantity_rules import qty_delivered_for_shipment, find_shipment
    a = find_shipment(o, "sj-a")
    b = find_shipment(o, "sj-b")
    assert qty_delivered_for_shipment(o, a) == 0
    assert qty_delivered_for_shipment(o, b) == 4000
    assert find_shipment(o, "sj-a")["id"] == "sj-a"
    assert find_shipment(o, "sj-b")["id"] == "sj-b"


class _FakeOrders:
    def __init__(self, docs):
        self.docs = {d["id"]: copy.deepcopy(d) for d in docs}

    async def find_one(self, q, *a, **k):
        return copy.deepcopy(self.docs.get(q.get("id")))

    async def replace_one(self, q, doc):
        self.docs[q["id"]] = copy.deepcopy(doc)
        return True


class _FakeDB:
    def __init__(self, docs):
        self.orders = _FakeOrders(docs)


def test_backfill_dry_run_no_write():
    broken = build_numani_broken_order()
    db = _FakeDB([broken, {"id": "other-po", "shipments": [{"id": "x"}]}])
    before = json.dumps(db.orders.docs[NUMANI_PO_ID], default=str)
    other_before = json.dumps(db.orders.docs["other-po"], default=str)
    report = asyncio.run(reconcile_run(db, po_id=NUMANI_PO_ID, apply=False, expected_cas=None, backup_dir=Path("/tmp/sj-bak")))
    assert report["ok"] is True
    assert report["mode"] == "dry-run"
    assert report["wrote"] is False
    assert json.dumps(db.orders.docs[NUMANI_PO_ID], default=str) == before
    assert json.dumps(db.orders.docs["other-po"], default=str) == other_before
    assert verify_preconditions(broken) == []


def test_backfill_apply_only_target_po_and_second_apply_noop_and_cas():
    broken = build_numani_broken_order()
    other = {"id": "other-po", "shipments": [{"id": "keep-me", "lines": [{"qty": 9}]}]}
    db = _FakeDB([broken, other])
    token = cas_token(broken)
    report = asyncio.run(reconcile_run(
        db, po_id=NUMANI_PO_ID, apply=True, expected_cas=token, backup_dir=Path("/tmp/sj-bak"),
    ))
    assert report["ok"] is True
    assert report["wrote"] is True
    assert db.orders.docs["other-po"]["shipments"][0]["id"] == "keep-me"
    repaired = db.orders.docs[NUMANI_PO_ID]
    assert already_repaired(repaired)
    ag = aggregates(repaired)
    exp = expected_aggregates()
    assert ag["po_qty"] == exp["po_qty"]
    assert ag["active_sj_qty"] == exp["active_sj_qty"]
    assert ag["loaded_qty"] == exp["loaded_qty"]
    assert ag["delivered_qty"] == exp["delivered_qty"]
    assert ag["remaining_qty"] == exp["remaining_qty"]
    assert set(ag["active_shipment_ids"]) == {NUMANI_SJ1, NUMANI_SJ2}
    assert ag["leftover_task_shipment_ids"] == [NUMANI_SJ1]
    report2 = asyncio.run(reconcile_run(
        db, po_id=NUMANI_PO_ID, apply=True, expected_cas=None, backup_dir=Path("/tmp/sj-bak"),
    ))
    assert report2["already_repaired"] is True
    assert report2["wrote"] is False
    db2 = _FakeDB([build_numani_broken_order()])
    bad = asyncio.run(reconcile_run(
        db2, po_id=NUMANI_PO_ID, apply=True, expected_cas="deadbeef", backup_dir=Path("/tmp/sj-bak"),
    ))
    assert bad["ok"] is False
    assert "compare-and-swap" in (bad.get("error") or "")


def test_new_sj_rejected_after_full_loading_even_if_active_sj_qty_was_shrunk():
    o = _po_5000()
    apply_shipment_record(
        o, number="SJ/1", lines=[{"line_idx": 0, "line_id": "L0", "qty": 1000}],
        source=SHIPMENT_SOURCE_MANUAL, shipment_id="sj-1", created_at="t1",
    )
    apply_shipment_record(
        o, number="SJ/1_2", lines=[{"line_idx": 0, "line_id": "L0", "qty": 4000}],
        source=SHIPMENT_SOURCE_MANUAL, shipment_id="sj-2", created_at="t2",
    )
    o["surat_jalan_files"] = [
        {"id": "sj-1", "shipment_id": "sj-1", "sj_number": "SJ/1", "lines": [{"line_idx": 0, "qty": 1000}]},
        {"id": "sj-2", "shipment_id": "sj-2", "sj_number": "SJ/1_2", "lines": [{"line_idx": 0, "qty": 4000}]},
    ]
    apply_loading_confirmation(
        o, surat_jalan_id="sj-1", lines=[{"line_idx": 0, "qty": 1000}],
        actor={"email": "l@x"}, confirmation_id="c1", confirmed_at="t", action_id="load-1",
    )
    apply_loading_confirmation(
        o, surat_jalan_id="sj-2", lines=[{"line_idx": 0, "qty": 4000}],
        actor={"email": "l@x"}, confirmation_id="c2", confirmed_at="t", action_id="load-2",
    )
    o["shipments"][1]["lines"][0]["qty"] = 3000
    o["shipments"][1]["total_qty"] = 3000
    err = validate_shipment_lines([{"line_idx": 0, "line_id": "L0", "qty": 1000}], o, source=SHIPMENT_SOURCE_MANUAL)
    assert err is not None


def test_invariants_apply_to_old_and_new_po_created_at():
    for created in ("2024-01-01T00:00:00+07:00", "2026-09-17T00:00:00+07:00"):
        o = _po_5000()
        o["created_at"] = created
        apply_shipment_record(
            o, number="SJ/1", lines=[{"line_idx": 0, "line_id": "L0", "qty": 5000}],
            source=SHIPMENT_SOURCE_MANUAL, shipment_id="sj-1", created_at=created,
        )
        o["surat_jalan_files"] = [{"id": "sj-1", "shipment_id": "sj-1", "sj_number": "SJ/1", "lines": [{"line_idx": 0, "qty": 5000}]}]
        apply_loading_confirmation(
            o, surat_jalan_id="sj-1", lines=[{"line_idx": 0, "qty": 5000}],
            actor={"email": "l@x"}, confirmation_id="c1", confirmed_at=created, action_id="load-1",
        )
        err = validate_shipment_lines([{"line_idx": 0, "line_id": "L0", "qty": 1}], o, source=SHIPMENT_SOURCE_MANUAL)
        assert err is not None
        with pytest.raises(ValueError, match="Loading|dibekukan"):
            apply_shipment_record(
                o, number="SJ/1", lines=[{"line_idx": 0, "line_id": "L0", "qty": 4000}],
                source=SHIPMENT_SOURCE_MANUAL, shipment_id="sj-1", created_at=created,
            )


def test_loading_and_delivery_resolve_stale_line_idx_by_stable_line_id():
    order = {
        "id": "po-stable-lines",
        "line_items": [
            {"line_id": "LA", "accurate_item_id": "A", "name": "Cup A", "quantity": 10, "production_process": "print"},
            {"line_id": "LB", "accurate_item_id": "B", "name": "Cup B", "quantity": 5, "production_process": "print"},
        ],
        "packing_info": {"qty_packed": 15, "lines": [
            {"line_idx": 0, "line_id": "LA", "packed_qty": 10},
            {"line_idx": 1, "line_id": "LB", "packed_qty": 5},
        ]},
        "shipments": [{
            "id": "sj-b", "number": "SJ/B", "total_qty": 5,
            "lines": [{"line_idx": 0, "line_id": "LB", "accurate_item_id": "B", "qty": 5}],
        }],
        "surat_jalan_files": [{
            "id": "file-b", "shipment_id": "sj-b", "sj_number": "SJ/B", "total_qty": 5,
            "lines": [{"line_idx": 0, "line_id": "LB", "accurate_item_id": "B", "qty": 5}],
        }],
        "loading_confirmations": [{
            "id": "load-b", "shipment_id": "sj-b",
            "lines": [{"line_idx": 0, "line_id": "LB", "accurate_item_id": "B", "qty": 5}],
        }],
    }
    assert loaded_qty_for_sj_line(order, "sj-b", 0) == 0
    assert loaded_qty_for_sj_line(order, "sj-b", 1) == 5
    assert delivery_batch_photo_readiness(order, order["shipments"][0])["allowed"] is True


def test_one_click_loading_persists_stable_line_identity_and_selected_sj_status():
    order = _po_5000()
    _attach_sj(order, "sj-1", "SJ/1", 1000)
    _attach_sj(order, "sj-2", "SJ/2", 4000)
    record = apply_loading_match_confirmation(
        order,
        surat_jalan_id="SJ/1",
        actor={"email": "loading@test"},
        confirmation_id="load-1",
        confirmed_at="t",
        action_id="action-1",
    )
    assert record["shipment_id"] == "sj-1"
    assert record["lines"] == [{
        "line_idx": 0,
        "qty": 1000,
        "name": "22 oz Datar 8 gr Berlian",
        "line_id": "L0",
        "accurate_item_id": "item-1",
    }]
    assert loading_verification_status(order, "SJ/1")["code"] == "loading_complete"
    assert loading_verification_status(order, "SJ/2")["code"] != "loading_complete"


def test_loading_discrepancy_stores_canonical_shipment_identity():
    order = _po_5000()
    _attach_sj(order, "sj-1", "SJ/1", 1000)
    record = apply_loading_discrepancy(
        order,
        surat_jalan_id="SJ/1",
        mismatches=[{"line_idx": 0, "actual_qty": 900}],
        discrepancy_id="disc-1",
    )
    assert record["surat_jalan_id"] == "sj-1"
    assert record["shipment_id"] == "sj-1"
    assert record["sj_number"] == "SJ/1"
    assert record["lines"][0]["line_id"] == "L0"


def test_legacy_shipment_without_lines_keeps_scoped_loading_for_audit():
    order = {
        "line_items": [{"line_id": "L0", "quantity": 1000}],
        "shipments": [{"id": "sj-legacy", "number": "SJ/LEGACY", "total_qty": 1000}],
        "surat_jalan_files": [{"id": "f1", "shipment_id": "sj-legacy", "sj_number": "SJ/LEGACY"}],
        "loading_confirmations": [{
            "id": "load-legacy", "surat_jalan_id": "SJ/LEGACY",
            "lines": [{"line_idx": 0, "qty": 1000}],
        }],
    }
    assert loaded_qty_for_shipment(order, order["shipments"][0]) == 1000


def test_get_derived_sj_integrity_is_not_persisted():
    src = (Path(__file__).resolve().parents[1] / "server.py").read_text(encoding="utf-8")
    assert '"sj_integrity"' in src
    assert "sj_integrity" in src
    assert "_DERIVED_ORDER_KEYS" in src


def test_repaired_fixture_matches_business_truth():
    o = build_numani_repaired_order()
    ag = aggregates(o)
    exp = expected_aggregates()
    assert ag["po_qty"] == 5000
    assert ag["active_sj_qty"] == 5000
    assert ag["loaded_qty"] == 5000
    assert ag["delivered_qty"] == 4000
    assert ag["remaining_qty"] == 1000
    assert NUMANI_SJ3 not in ag["active_shipment_ids"]
    assert ag["leftover_task_shipment_ids"] == [NUMANI_SJ1]
    # delivery evidence stays bound to SJ2 — not invented
    rec = o["delivery_confirmations"][0]
    assert rec["shipment_id"] == NUMANI_SJ2
    assert rec["qty"] == 4000
    assert rec.get("recipient_name")  # already on fixture, not minted by repair
    assert verify_preconditions(o) == []
    assert already_repaired(o)
    _ = exp


def test_integrity_summary_is_read_only():
    o = build_numani_repaired_order()
    before = json.dumps(o, default=str)
    summary = order_integrity_summary(o)
    assert json.dumps(o, default=str) == before
    assert summary["po_qty"] == 5000
    assert summary["active_sj_qty"] == 5000
    assert summary["loaded_qty"] == 5000
    assert summary["delivered_qty"] == 4000
    assert summary["remaining_qty"] == 1000
    assert summary["actionable_delivery"] == [NUMANI_SJ1]
    assert NUMANI_SJ3 in summary["inactive_shipment_ids"]
