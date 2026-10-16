"""Stock import reconciliation + Loading delete + prediction history import."""
from __future__ import annotations

import sys
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from permissions import ALL_PERMISSIONS
from quantity_rules import (
    apply_loading_confirmation,
    cancel_loading_confirmation,
    delivered_qty_for_loading_confirmation,
    loading_confirmations_stored,
    qty_confirmed_loading,
    validate_cancel_loading_confirmation,
)
from services.current_stock import compute_stock_current, parse_manual_qty
from services.current_stock_import import (
    PERM_IMPORT as STOCK_IMPORT_PERM,
    build_stock_import_preview,
    manual_qty_for_target_stock,
    validate_apply_rows,
)
from services.order_prediction import build_order_prediction
from services.order_prediction_import import (
    PERM_IMPORT as PRED_IMPORT_PERM,
    SOURCE_IMPORT,
    build_history_import_preview,
    merge_prediction_orders,
    validate_history_apply_rows,
)
from services.status_po import STAGES


def _master(mid, code, name, aid=None):
    return {
        "id": mid,
        "master_item_id": mid,
        "accurate_item_id": aid or f"acc-{mid}",
        "item_code": code,
        "item_name": name,
        "unit": "pcs",
    }


def _stock_row(m, stock, *, po=0, purchase=0, op=0, manual=None):
    if manual is None:
        manual = stock - purchase + po + op
    return {
        "master_item_id": m["id"],
        "accurate_item_id": m["accurate_item_id"],
        "item_code": m["item_code"],
        "item_name": m["item_name"],
        "qty_po": po,
        "purchase_qty": purchase,
        "operator_print_qty": op,
        "manual_current_qty": manual,
        "stock_current": compute_stock_current(po, manual, purchase, op),
    }


# ---------- STOCK ----------

def test_s1_selected_only_reconciliation():
    masters = [
        _master("a", "A", "Item A"),
        _master("b", "B", "Item B"),
        _master("c", "C", "Item C"),
    ]
    rows = [
        _stock_row(masters[0], 100),
        _stock_row(masters[1], 200),
        _stock_row(masters[2], 300),
    ]
    csv = b"item_code,stock\nA,75\nB,250\nC,10\n"
    preview = build_stock_import_preview(csv, "t.csv", stock_rows=rows, masters=masters)
    assert preview["valid_count"] == 3
    selected = [r for r in preview["rows"] if r["item_code"] in ("A", "C")]
    accepted, rejected = validate_apply_rows(selected, stock_rows=rows, masters=masters)
    assert not rejected
    by_code = {a["item_code"]: a for a in accepted}
    assert by_code["A"]["stock_after"] == 75
    assert by_code["C"]["stock_after"] == 10
    assert "B" not in by_code
    # B unchanged conceptually
    assert rows[1]["stock_current"] == 200


def test_s2_checkbox_one_row():
    masters = [_master("a", "A", "A"), _master("b", "B", "B")]
    rows = [_stock_row(masters[0], 100), _stock_row(masters[1], 200)]
    csv = b"item_code,stock\nA,10\nB,20\n"
    preview = build_stock_import_preview(csv, "t.csv", stock_rows=rows, masters=masters)
    selected = [preview["rows"][0]]
    accepted, _ = validate_apply_rows(selected, stock_rows=rows, masters=masters)
    assert len(accepted) == 1
    assert accepted[0]["item_code"] == "A"
    assert accepted[0]["stock_after"] == 10


def test_s3_unknown_item():
    masters = [_master("a", "A", "A")]
    rows = [_stock_row(masters[0], 100)]
    csv = b"item_code,stock\nZZZ,50\n"
    preview = build_stock_import_preview(csv, "t.csv", stock_rows=rows, masters=masters)
    assert preview["rows"][0]["status"] == "TIDAK DITEMUKAN"
    assert preview["rows"][0]["selectable"] is False


def test_s4_duplicate_in_file():
    masters = [_master("a", "A", "A")]
    rows = [_stock_row(masters[0], 100)]
    csv = b"item_code,stock\nA,50\nA,60\n"
    preview = build_stock_import_preview(csv, "t.csv", stock_rows=rows, masters=masters)
    statuses = [r["status"] for r in preview["rows"]]
    assert "DUPLIKAT" in statuses
    assert statuses.count("VALID") == 1


def test_s5_stock_import_perm_in_catalog():
    assert STOCK_IMPORT_PERM in ALL_PERMISSIONS
    assert "loading.delete" in ALL_PERMISSIONS
    assert PRED_IMPORT_PERM in ALL_PERMISSIONS


def test_s6_audit_delta_fields():
    masters = [_master("a", "A", "A")]
    rows = [_stock_row(masters[0], 100)]
    accepted, _ = validate_apply_rows(
        [{"master_item_id": "a", "item_code": "A", "stock_file": 75}],
        stock_rows=rows, masters=masters,
    )
    assert accepted[0]["stock_before"] == 100
    assert accepted[0]["stock_after"] == 75
    assert accepted[0]["delta"] == -25


def test_manual_qty_invert_preserves_formula():
    target = 75
    manual = manual_qty_for_target_stock(stock_target=target, purchase_qty=10, po_qty=5, operator_print_qty=2)
    assert compute_stock_current(5, manual, 10, 2) == 75
    assert parse_manual_qty(manual, allow_negative=True) == manual


# ---------- LOADING ----------

def _po(**extra):
    o = {
        "id": "po1",
        "po_number": "PO-1",
        "total_quantity": 1000,
        "line_items": [{"name": "Box", "quantity": 1000, "accurate_item_id": "x1"}],
        "packing_info": {"qty_packed": 1000, "lines": [{"line_idx": 0, "packed_qty": 1000}]},
        "surat_jalan_files": [{"id": "f1", "name": "SJ1.pdf", "shipment_id": "f1", "sj_number": "SJ-1"}],
        "shipments": [{"id": "f1", "number": "SJ-1"}],
        "loading_confirmations": [],
        "qty_delivered": 0,
        "delivery_status": "Belum Terkirim",
        "current_stage": 13,
    }
    o.update(extra)
    return o


def _cancel_related_sj(o):
    """SJ-first correction: soft-cancel related shipment before Hapus Loading."""
    for sh in o.get("shipments") or []:
        if not isinstance(sh, dict):
            continue
        sh["is_cancelled"] = True
        sh["cancelled"] = True
        sh["status"] = "cancelled"
        sh["sj_status"] = "cancelled"


def test_l1_delete_pass_when_sj_cancelled_and_not_delivered():
    o = _po()
    apply_loading_confirmation(
        o, surat_jalan_id="f1", lines=[{"line_idx": 0, "qty": 400}],
        confirmation_id="c1", confirmed_at="t1",
    )
    assert validate_cancel_loading_confirmation(o, "c1")  # SJ still active
    _cancel_related_sj(o)
    assert validate_cancel_loading_confirmation(o, "c1") is None
    cancel_loading_confirmation(o, "c1", actor={"email": "a@x.com"}, cancelled_at="t2")
    assert len(loading_confirmations_stored(o)) == 0
    assert o["loading_confirmations"][0]["cancelled"] is True


def test_l2_reject_when_confirmation_delivered():
    o = _po(qty_delivered=400, delivery_status="Sebagian Terkirim")
    apply_loading_confirmation(
        o, surat_jalan_id="f1", lines=[{"line_idx": 0, "qty": 400}],
        confirmation_id="c1", confirmed_at="t1",
    )
    _cancel_related_sj(o)
    o["delivery_confirmations"] = [{"shipment_id": "f1", "qty": 400}]
    assert delivered_qty_for_loading_confirmation(o, "c1") == 400
    assert validate_cancel_loading_confirmation(o, "c1")


def test_l3_reject_when_fully_delivered():
    o = _po(qty_delivered=1000, delivery_status="Terkirim", current_stage=14)
    apply_loading_confirmation(
        o, surat_jalan_id="f1", lines=[{"line_idx": 0, "qty": 1000}],
        confirmation_id="c1", confirmed_at="t1",
    )
    assert validate_cancel_loading_confirmation(o, "c1")


def test_l4_other_loading_identical():
    o = _po()
    apply_loading_confirmation(
        o, surat_jalan_id="f1", lines=[{"line_idx": 0, "qty": 400}],
        confirmation_id="c1", confirmed_at="t1",
    )
    apply_loading_confirmation(
        o, surat_jalan_id="f1", lines=[{"line_idx": 0, "qty": 300}],
        confirmation_id="c2", confirmed_at="t2",
    )
    _cancel_related_sj(o)
    cancel_loading_confirmation(o, "c1", actor={"email": "a@x.com"}, cancelled_at="t3")
    active = loading_confirmations_stored(o)
    assert len(active) == 1
    assert active[0]["id"] == "c2"
    assert active[0]["lines"][0]["qty"] == 300
    assert qty_confirmed_loading(o) == 300


def test_l5_sj_file_preserved():
    o = _po()
    apply_loading_confirmation(
        o, surat_jalan_id="f1", lines=[{"line_idx": 0, "qty": 100}],
        confirmation_id="c1", confirmed_at="t1",
    )
    _cancel_related_sj(o)
    cancel_loading_confirmation(o, "c1", actor={"email": "a@x.com"}, cancelled_at="t2")
    assert o["surat_jalan_files"][0]["id"] == "f1"


def test_l6_recalc_remaining():
    o = _po()
    apply_loading_confirmation(
        o, surat_jalan_id="f1", lines=[{"line_idx": 0, "qty": 400}],
        confirmation_id="c1", confirmed_at="t1",
    )
    apply_loading_confirmation(
        o, surat_jalan_id="f1", lines=[{"line_idx": 0, "qty": 200}],
        confirmation_id="c2", confirmed_at="t2",
    )
    assert qty_confirmed_loading(o) == 600
    _cancel_related_sj(o)
    cancel_loading_confirmation(o, "c2", actor={"email": "a@x.com"}, cancelled_at="t3")
    assert qty_confirmed_loading(o) == 400


# ---------- STAGE ----------

def test_t1_t2_t3_stage_label():
    assert len(STAGES) == 15
    assert STAGES[14] == "Pengiriman"
    assert STAGES[14] != "Terkirim"
    # internal delivery status string unchanged in business logic helpers
    from quantity_rules import is_fully_delivered
    o = _po(qty_delivered=1000, delivery_status="Terkirim", current_stage=14)
    assert is_fully_delivered(o)


# ---------- PREDICTION ----------

def test_p1_import_three_orders_median():
    hist = [
        {"id": "h1", "customer": "Kopi", "outlet": "Kemang", "order_date": "2026-01-01", "po_number": "H-1"},
        {"id": "h2", "customer": "Kopi", "outlet": "Kemang", "order_date": "2026-01-10", "po_number": "H-2"},
        {"id": "h3", "customer": "Kopi", "outlet": "Kemang", "order_date": "2026-01-19", "po_number": "H-3"},
    ]
    data = build_order_prediction([], now=datetime(2026, 1, 21, tzinfo=timezone.utc), supplemental=hist)
    assert data["rows"][0]["total_orders"] == 3
    assert data["rows"][0]["median_gap"] == 9
    assert data["order_total"] == 3


def test_p2_selected_only_two():
    live = []
    existing = []
    csv = (
        b"customer,outlet,tanggal_order,no_po\n"
        b"Kopi,Kemang,2026-01-01,H1\n"
        b"Kopi,Kemang,2026-01-10,H2\n"
        b"Kopi,Kemang,2026-01-19,H3\n"
    )
    preview = build_history_import_preview(csv, "h.csv", live_orders=live, existing_imports=existing,
                                          now=datetime(2026, 1, 21, tzinfo=timezone.utc))
    selected = [r for r in preview["rows"] if r["po_number"] in ("H1", "H2")]
    accepted, _ = validate_history_apply_rows(selected, live_orders=live, existing_imports=existing,
                                              now=datetime(2026, 1, 21, tzinfo=timezone.utc))
    assert len(accepted) == 2


def test_p3_different_outlet_separate():
    hist = [
        {"id": "1", "customer": "Kopi", "outlet": "Kemang", "order_date": "2026-01-01", "po_number": "A1"},
        {"id": "2", "customer": "Kopi", "outlet": "Kemang", "order_date": "2026-01-10", "po_number": "A2"},
        {"id": "3", "customer": "Kopi", "outlet": "Kemang", "order_date": "2026-01-19", "po_number": "A3"},
        {"id": "4", "customer": "Kopi", "outlet": "Tebet", "order_date": "2026-01-02", "po_number": "B1"},
        {"id": "5", "customer": "Kopi", "outlet": "Tebet", "order_date": "2026-01-12", "po_number": "B2"},
        {"id": "6", "customer": "Kopi", "outlet": "Tebet", "order_date": "2026-01-22", "po_number": "B3"},
    ]
    data = build_order_prediction([], now=datetime(2026, 1, 25, tzinfo=timezone.utc), supplemental=hist)
    assert data["total"] == 1
    assert data["rows"][0]["total_orders"] == 6
    assert data["rows"][0]["outlet"] == "Kemang, Tebet"


def test_p4_duplicate_file_upload():
    existing = [{"id": "h1", "customer": "Kopi", "outlet": "K", "order_date": "2026-01-01", "po_number": "H1"}]
    csv = b"customer,outlet,tanggal_order,no_po\nKopi,K,2026-01-01,H1\n"
    preview = build_history_import_preview(csv, "h.csv", live_orders=[], existing_imports=existing,
                                          now=datetime(2026, 1, 21, tzinfo=timezone.utc))
    assert preview["rows"][0]["status"] == "DUPLIKAT"
    assert preview["rows"][0]["selectable"] is False


def test_p5_live_order_wins():
    live = [{
        "id": "o1", "po_number": "PO-LIVE", "customer": "Kopi", "outlet": "K",
        "order_date": "2026-01-01", "customer_id": "c1",
    }]
    imported = [{
        "id": "h1", "po_number": "PO-LIVE", "customer": "Kopi", "outlet": "K",
        "order_date": "2026-01-01",
    }]
    merged = merge_prediction_orders(live, imported)
    assert len(merged) == 1
    assert merged[0]["history_source"] == "ORDER MASUK"


def test_p6_future_date_invalid():
    csv = b"customer,outlet,tanggal_order\nKopi,K,2099-01-01\n"
    preview = build_history_import_preview(csv, "h.csv", live_orders=[], existing_imports=[],
                                          now=datetime(2026, 1, 21, tzinfo=timezone.utc))
    assert preview["rows"][0]["status"] == "INVALID"


def test_p7_omzet_blank_null():
    csv = b"customer,outlet,tanggal_order,omzet\nKopi,K,2026-01-01,\n"
    preview = build_history_import_preview(csv, "h.csv", live_orders=[], existing_imports=[],
                                          now=datetime(2026, 1, 21, tzinfo=timezone.utc))
    assert preview["rows"][0]["omzet"] is None
    accepted, _ = validate_history_apply_rows(
        [preview["rows"][0]], live_orders=[], existing_imports=[],
        now=datetime(2026, 1, 21, tzinfo=timezone.utc),
    )
    assert accepted[0]["omzet"] is None


def test_p8_source_import_in_history():
    hist = [
        {"id": "h1", "customer": "Kopi", "outlet": "K", "order_date": "2026-01-01", "po_number": "H1"},
        {"id": "h2", "customer": "Kopi", "outlet": "K", "order_date": "2026-01-10", "po_number": "H2"},
        {"id": "h3", "customer": "Kopi", "outlet": "K", "order_date": "2026-01-19", "po_number": "H3"},
    ]
    data = build_order_prediction([], now=datetime(2026, 1, 21, tzinfo=timezone.utc), supplemental=hist)
    assert all(r.get("source") == SOURCE_IMPORT for r in data["order_rows"])
