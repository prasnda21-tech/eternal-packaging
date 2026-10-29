"""Sisa Belum Cetak — reuse print blank_need; never subtract shipment again."""
from __future__ import annotations

from services.physical_stock import collect_blank_open_commitment_by_master
from services.stock_saat_ini_excel import (
    STATUS_READY,
    STATUS_UNVERIFIED,
    REASON_BLANK_PRINT_COMPLETION_UNPROVEN,
    SOURCE_SISA_BELUM_CETAK,
    apply_excel_fields_to_catalog_row,
    aggregate_ledger_components,
    compute_excel_columns,
    sisa_belum_cetak_for_print_line,
)


def test_file2_partial_print_and_ship_sisa_500_unshipped_0():
    """File 2: ordered 1000, printed 500, shipped 500 → sisa 500, tercetak belum 0."""
    sisa = sisa_belum_cetak_for_print_line(
        ordered_qty=1000,
        print_completed_qty=500,
        print_completion_proven=True,
    )
    assert sisa["qty"] == 500
    assert sisa["status"] == STATUS_READY

    excel = compute_excel_columns(
        stock_book=1500,  # independent ledger: 2000 in − 500 out
        open_so_commitment=500,  # remaining print demand (not double-ship)
        qty_tercetak=500,
        qty_tercetak_status=STATUS_READY,
        cetak_delivered_proven=500,
        print_delivery_proven=True,
        incomplete_print=True,
    )
    assert excel["tercetak_belum_kirim"] == 0
    assert excel["cetak_sudah_dikirim"] == 500
    # Stock book independent of sisa — no double-count of shipment into stock.
    assert excel["stock"] == 1500


def test_file3_zero_print_proven_sisa_1000_unshipped_0():
    """File 3: ordered 1000, printed 0 (proven), shipped 0 → sisa 1000, tercetak belum 0."""
    sisa = sisa_belum_cetak_for_print_line(
        ordered_qty=1000,
        print_completed_qty=0,
        print_completion_proven=True,
    )
    assert sisa["qty"] == 1000
    assert sisa["status"] == STATUS_READY

    excel = compute_excel_columns(
        stock_book=2000,
        open_so_commitment=1000,
        qty_tercetak=0,
        qty_tercetak_status=STATUS_READY,
        cetak_delivered_proven=0,
        print_delivery_proven=True,
        incomplete_print=True,
    )
    assert excel["tercetak_belum_kirim"] == 0
    assert excel["qty_tercetak"] == 0


def test_missing_print_evidence_not_confirmed_zero():
    sisa = sisa_belum_cetak_for_print_line(
        ordered_qty=1000,
        print_completed_qty=None,
        print_completion_proven=False,
    )
    assert sisa["qty"] is None
    assert sisa["status"] == STATUS_UNVERIFIED
    assert sisa["reason"] == REASON_BLANK_PRINT_COMPLETION_UNPROVEN


def test_sisa_ignores_shipment_does_not_double_deduct():
    """Shipment must not reduce sisa — only proven print completion does."""
    with_ship_ignored = sisa_belum_cetak_for_print_line(
        ordered_qty=1000,
        print_completed_qty=500,
        print_completion_proven=True,
    )
    assert with_ship_ignored["qty"] == 500  # not 1000-500ship-500print=0


def test_ledger_receive_2000_ship_500_stock_1500():
    rows = aggregate_ledger_components(
        [
            {
                "master_item_id": "m1",
                "movement_type": "PURCHASE_RECEIPT",
                "qty_delta": 2000,
            },
            {
                "master_item_id": "m1",
                "movement_type": "SALES_DELIVERY",
                "qty_delta": -500,
            },
        ],
        master_item_ids=["m1"],
    )
    slot = rows["m1"]
    assert slot["pembelian"] == 2000
    assert slot["delivered_total"] == 500
    stock = (
        slot["saldo_awal"]
        + slot["pembelian"]
        + slot["adjustment_net"]
        - slot["delivered_total"]
    )
    assert stock == 1500


def test_collect_sisa_multi_so_partial_and_cancelled():
    orders = [
        {
            "id": "so-partial",
            "cancelled": False,
            "line_items": [{
                "line_id": "ln-a",
                "master_item_id": "m-cup",
                "quantity": 1000,
                "production_process": "print",
            }],
            "operator_splits": [{"line_idx": 0, "qty_completed": 500}],
        },
        {
            "id": "so-full-open",
            "cancelled": False,
            "line_items": [{
                "line_id": "ln-b",
                "master_item_id": "m-cup",
                "quantity": 1000,
                "production_process": "print",
            }],
            "operator_splits": [{"line_idx": 0, "qty_completed": 0}],
        },
        {
            "id": "so-cancelled",
            "cancelled": True,
            "line_items": [{
                "line_id": "ln-c",
                "master_item_id": "m-cup",
                "quantity": 9999,
                "production_process": "print",
            }],
            "operator_splits": [{"line_idx": 0, "qty_completed": 0}],
        },
    ]
    # Attributed delivery on partial SO — must NOT reduce sisa.
    deliveries = [{
        "order_id": "so-partial",
        "master_item_id": "m-cup",
        "line_id": "ln-a",
        "line_idx": 0,
        "qty": 500,
    }]
    pack = collect_blank_open_commitment_by_master(
        orders, {}, exclude_master_ids=set(), attributed_deliveries=deliveries,
    )
    sisa = pack["sisa_belum_cetak"]["m-cup"]
    # 500 remaining on so-partial + 1000 on so-full-open; cancelled excluded
    assert sisa["status"] == STATUS_READY
    assert sisa["qty"] == 1500
    # Blank need for print uses same completion; shipment still ignored on print.
    blank = pack["blank_open_commitment"]["m-cup"]
    assert blank["qty"] == 1500


def test_service_and_addon_excluded_from_sisa():
    orders = [{
        "id": "so-mix",
        "cancelled": False,
        "line_items": [
            {
                "line_id": "ln-p",
                "master_item_id": "m-cup",
                "quantity": 1000,
                "production_process": "print",
            },
            {
                "line_id": "ln-svc",
                "master_item_id": "m-svc",
                "quantity": 1000,
                "production_process": "print",
                "item_type": "SERVICE",
                "parent_line_id": "ln-p",  # Add On Warna linked; never counted as physical qty
            },
        ],
        "operator_splits": [
            {"line_idx": 0, "qty_completed": 0},
            {"line_idx": 1, "qty_completed": 0},
        ],
    }]
    pack = collect_blank_open_commitment_by_master(
        orders, {}, exclude_master_ids={"m-svc"}, attributed_deliveries=[],
    )
    assert "m-svc" not in pack["sisa_belum_cetak"]
    assert "m-svc" not in pack["blank_open_commitment"]
    assert pack["sisa_belum_cetak"]["m-cup"]["qty"] == 1000


def test_apply_excel_exposes_sisa_with_provenance():
    row: dict = {"master_item_id": "m1"}
    excel = {
        "saldo_awal": 0,
        "pembelian": 2000,
        "stock": 1500,
        "stock_dapat_dijual": 500,
        "qty_tercetak": 500,
        "qty_tercetak_status": STATUS_READY,
        "qty_polos": 1500,
        "qty_polos_status": STATUS_READY,
        "tercetak_belum_kirim": 0,
        "tercetak_belum_kirim_status": STATUS_READY,
        "cetak_sudah_dikirim": 500,
        "cetak_sudah_dikirim_status": STATUS_READY,
        "formula_version": "ssi-excel-v1",
        "sources": {},
        "sisa_belum_cetak": 500,
        "sisa_belum_cetak_status": STATUS_READY,
        "sisa_belum_cetak_reason": None,
        "blank_open_commitment": 500,
        "blank_open_commitment_status": STATUS_READY,
        "stock_polos_dapat_dijual": 1000,
        "stock_polos_dapat_dijual_status": STATUS_READY,
    }
    out = apply_excel_fields_to_catalog_row(row, excel, balance_present=True)
    assert out["sisa_belum_cetak"] == 500
    assert out["sisa_belum_cetak_status"] == STATUS_READY
    assert out["ssi_sources"]["sisa_belum_cetak"] == SOURCE_SISA_BELUM_CETAK
    # Existing formulas intact on row
    assert out["stock"] == 1500
    assert out["stock_dapat_dijual"] == 500
    assert out["qty_polos"] == 1500
