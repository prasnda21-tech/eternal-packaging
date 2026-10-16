"""SJ-first Loading correction: Batalkan SJ does not auto-cancel Loading;

Hapus Loading requires cancelled SJ and ignores cancelled delivery evidence.
"""
from __future__ import annotations

from quantity_rules import (
    MSG_LOADING_DELETE_REQUIRES_SJ_CANCEL,
    MSG_LOADING_DELETE_SJ_UNRESOLVED,
    available_for_loading_confirm_line,
    cancel_loading_confirmation,
    delivered_qty_for_loading_confirmation,
    loading_confirmations_stored,
    qty_loaded_for_line,
    validate_cancel_loading_confirmation,
)
from services.line_product_replacement import collect_order_embedded_downstream_hits


def _order_two_sj(*, sj_a_cancelled=False, delivery_a=None):
    ship_a = {
        "id": "sj-a",
        "number": "SJ-A",
        "sales_delivery_id": "sj-a",
        "lines": [{"line_idx": 0, "qty": 400}, {"line_idx": 1, "qty": 400}],
    }
    if sj_a_cancelled:
        ship_a.update({
            "is_cancelled": True,
            "cancelled": True,
            "status": "cancelled",
            "sj_status": "cancelled",
        })
    deliveries = []
    if delivery_a is not None:
        deliveries.append(delivery_a)
    return {
        "id": "po-1",
        "total_quantity": 2000,
        "line_items": [
            {"name": "Cup", "quantity": 1000, "production_process": "print", "line_id": "L0"},
            {"name": "Lid", "quantity": 1000, "production_process": "non_print", "line_id": "L1"},
        ],
        "packing_info": {
            "qty_packed": 1000,
            "lines": [{"line_idx": 0, "packed_qty": 1000}],
        },
        "shipments": [
            ship_a,
            {
                "id": "sj-b",
                "number": "SJ-B",
                "sales_delivery_id": "sj-b",
                "lines": [{"line_idx": 0, "qty": 600}, {"line_idx": 1, "qty": 600}],
            },
        ],
        "surat_jalan_files": [
            {"id": "sj-a", "shipment_id": "sj-a", "sj_number": "SJ-A", "name": "a.pdf"},
            {"id": "sj-b", "shipment_id": "sj-b", "sj_number": "SJ-B", "name": "b.pdf"},
        ],
        "delivery_confirmations": deliveries,
        "qty_delivered": 0,
        "delivery_status": "Belum Terkirim",
        "loading_confirmations": [
            {
                "id": "lc-a1",
                "surat_jalan_id": "sj-a",
                "shipment_id": "sj-a",
                "sj_number": "SJ-A",
                "surat_jalan_name": "SJ-A",
                "confirmed_by": "a@x",
                "confirmed_at": "2026-01-01T00:00:00+00:00",
                "lines": [{"line_idx": 0, "qty": 400, "name": "Cup"}, {"line_idx": 1, "qty": 400, "name": "Lid"}],
            },
            {
                "id": "lc-a2",
                "surat_jalan_id": "sj-a",
                "shipment_id": "sj-a",
                "sj_number": "SJ-A",
                "lines": [{"line_idx": 0, "qty": 0}],
            },
            {
                "id": "lc-b",
                "surat_jalan_id": "sj-b",
                "shipment_id": "sj-b",
                "sj_number": "SJ-B",
                "lines": [{"line_idx": 0, "qty": 600, "name": "Cup"}, {"line_idx": 1, "qty": 600, "name": "Lid"}],
            },
        ],
    }


def test_hapus_blocked_while_related_sj_active():
    order = _order_two_sj()
    err = validate_cancel_loading_confirmation(order, "lc-a1")
    assert err == MSG_LOADING_DELETE_REQUIRES_SJ_CANCEL


def test_hapus_fail_closed_when_sj_identity_missing_shipment():
    """Token present but shipment unresolved must NOT be treated as cancelled."""
    order = _order_two_sj(sj_a_cancelled=True)
    order["loading_confirmations"][0]["shipment_id"] = "sj-missing"
    order["loading_confirmations"][0]["surat_jalan_id"] = "sj-missing"
    err = validate_cancel_loading_confirmation(order, "lc-a1")
    assert err == MSG_LOADING_DELETE_SJ_UNRESOLVED


def test_hapus_fail_closed_when_sj_identity_ambiguous():
    order = _order_two_sj(sj_a_cancelled=True)
    # Two shipments share the same number alias → find_shipment returns None.
    order["shipments"][0]["number"] = "SJ-DUP"
    order["shipments"][0]["sj_number"] = "SJ-DUP"
    order["shipments"][1]["number"] = "SJ-DUP"
    order["shipments"][1]["sj_number"] = "SJ-DUP"
    order["loading_confirmations"][0]["shipment_id"] = "SJ-DUP"
    order["loading_confirmations"][0]["surat_jalan_id"] = "SJ-DUP"
    err = validate_cancel_loading_confirmation(order, "lc-a1")
    assert err == MSG_LOADING_DELETE_SJ_UNRESOLVED


def test_legacy_loading_without_sj_identity_keeps_delivery_guards_only():
    order = {
        "id": "po-legacy",
        "total_quantity": 1000,
        "line_items": [{"name": "Box", "quantity": 1000, "line_id": "L0"}],
        "shipments": [{"id": "sj-x", "number": "SJ-X"}],
        "qty_delivered": 0,
        "delivery_status": "Belum Terkirim",
        "loading_confirmations": [{
            "id": "lc-legacy",
            "lines": [{"line_idx": 0, "qty": 100}],
        }],
    }
    assert validate_cancel_loading_confirmation(order, "lc-legacy") is None
    cancel_loading_confirmation(order, "lc-legacy", actor={"email": "a@x"}, cancelled_at="t")
    assert order["loading_confirmations"][0]["cancelled"] is True


def test_sj_cancel_does_not_auto_cancel_loading():
    """Simulated post-Batalkan-SJ state: shipment cancelled, Loading still active."""
    order = _order_two_sj(sj_a_cancelled=True)
    active = loading_confirmations_stored(order)
    assert [c["id"] for c in active] == ["lc-a1", "lc-a2", "lc-b"]
    assert qty_loaded_for_line(order, 0) == 1000


def test_hapus_after_sj_cancel_releases_qty_and_isolates_multi_sj():
    order = _order_two_sj(sj_a_cancelled=True)
    assert validate_cancel_loading_confirmation(order, "lc-a1") is None
    cancel_loading_confirmation(order, "lc-a1", actor={"email": "admin@x"}, cancelled_at="t1")
    assert validate_cancel_loading_confirmation(order, "lc-a2") is None
    cancel_loading_confirmation(order, "lc-a2", actor={"email": "admin@x"}, cancelled_at="t2")
    active = loading_confirmations_stored(order)
    assert [c["id"] for c in active] == ["lc-b"]
    assert order["loading_confirmations"][0]["cancelled"] is True
    assert order["loading_confirmations"][0]["confirmed_by"] == "a@x"
    assert order["loading_confirmations"][2].get("cancelled") is not True
    assert qty_loaded_for_line(order, 0) == 600
    assert available_for_loading_confirm_line(order, 0) == 400
    # Sibling Loading still protected by active SJ B
    assert validate_cancel_loading_confirmation(order, "lc-b") == MSG_LOADING_DELETE_REQUIRES_SJ_CANCEL


def test_cancelled_delivery_evidence_does_not_block_hapus():
    order = _order_two_sj(
        sj_a_cancelled=True,
        delivery_a={
            "shipment_id": "sj-a",
            "qty": 400,
            "cancelled": True,
            "is_cancelled": True,
            "cancelled_at": "t0",
        },
    )
    assert delivered_qty_for_loading_confirmation(order, "lc-a1") == 0
    assert validate_cancel_loading_confirmation(order, "lc-a1") is None


def test_active_delivery_still_blocks_hapus():
    order = _order_two_sj(
        sj_a_cancelled=True,
        delivery_a={"shipment_id": "sj-a", "qty": 400},
    )
    # Active delivery on cancelled SJ should not happen after official cancel,
    # but if evidence remains active the guard must still protect.
    assert delivered_qty_for_loading_confirmation(order, "lc-a1") == 400
    assert validate_cancel_loading_confirmation(order, "lc-a1")


def test_hapus_idempotent_missing_after_cancel():
    order = _order_two_sj(sj_a_cancelled=True)
    cancel_loading_confirmation(order, "lc-a1", actor={"email": "a@x"}, cancelled_at="t1")
    err = validate_cancel_loading_confirmation(order, "lc-a1")
    assert err == "Konfirmasi Loading tidak ditemukan."


def test_cancelled_loading_does_not_block_product_replace_hits():
    order = _order_two_sj(sj_a_cancelled=True)
    assert "loading" in collect_order_embedded_downstream_hits(order, 1)
    cancel_loading_confirmation(order, "lc-a1", actor={"email": "a@x"}, cancelled_at="t")
    cancel_loading_confirmation(order, "lc-a2", actor={"email": "a@x"}, cancelled_at="t")
    # Sibling Loading B still active → still blocks
    assert "loading" in collect_order_embedded_downstream_hits(order, 1)
    # Cancel B after its SJ cancelled
    order["shipments"][1]["is_cancelled"] = True
    order["shipments"][1]["cancelled"] = True
    order["shipments"][1]["status"] = "cancelled"
    cancel_loading_confirmation(order, "lc-b", actor={"email": "a@x"}, cancelled_at="t")
    assert "loading" not in collect_order_embedded_downstream_hits(order, 1)


def test_active_loading_still_blocks_replace():
    order = _order_two_sj()
    assert "loading" in collect_order_embedded_downstream_hits(order, 0)
