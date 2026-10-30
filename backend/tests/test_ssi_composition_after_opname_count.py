"""Composition invariants after physical count target (no formula change).

Stock Fisik (ledger) = counted combined total.
Tercetak Belum Kirim / Qty Polos / Sisa use existing SSI helpers — never add
blank+printed into Stock Fisik a second time; never invent completion.
"""
from __future__ import annotations

from services.stock_saat_ini_excel import (
    STATUS_READY,
    STATUS_UNVERIFIED,
    blank_need_for_physical_line,
    compute_excel_columns,
    sisa_belum_cetak_for_print_line,
)


def test_qty_polos_from_stock_minus_tercetak_belum_when_print_proven():
    # Counted physical 1500; proven printed not shipped 500 → polos 1000
    excel = compute_excel_columns(
        stock_book=1500,
        open_so_commitment=0,
        qty_tercetak=500,
        qty_tercetak_status=STATUS_READY,
        cetak_delivered_proven=0,
        print_delivery_proven=True,
        incomplete_print=False,
    )
    assert excel["stock"] == 1500
    assert excel["tercetak_belum_kirim"] == 500
    assert excel["qty_polos"] == 1000


def test_sisa_ignores_shipment_uses_ops_only():
    sisa = sisa_belum_cetak_for_print_line(
        ordered_qty=1000,
        print_completed_qty=400,
        print_completion_proven=True,
    )
    assert sisa["qty"] == 600
    assert sisa["status"] == STATUS_READY


def test_sisa_missing_ops_not_invented_zero():
    sisa = sisa_belum_cetak_for_print_line(
        ordered_qty=1000,
        print_completed_qty=None,
        print_completion_proven=False,
    )
    assert sisa["qty"] is None
    assert sisa["status"] == STATUS_UNVERIFIED


def test_blank_need_print_same_completion_evidence_as_sisa():
    blank = blank_need_for_physical_line(
        ordered_qty=1000,
        is_print=True,
        print_completed_qty=400,
        print_completion_proven=True,
        delivered_qty=200,  # ship must not change print blank need
    )
    assert blank["qty"] == 600
