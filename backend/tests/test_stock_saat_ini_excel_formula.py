"""Gate S1 — Owner Excel Stock Saat Ini pure formula tests."""
from __future__ import annotations

from services.stock_saat_ini_excel import (
    aggregate_ledger_components,
    apply_excel_fields_to_catalog_row,
    compute_excel_columns,
    parent_qty_tercetak_from_progress,
)


def test_owner_excel_example_core():
    """Stock masuk 10k, PO 32k, operator 8k, cetak kirim 4k → Owner numbers."""
    row = compute_excel_columns(
        saldo_awal=10_000,
        pembelian=0,
        delivered_total=4_000,
        po_ordered=32_000,
        so_delivered=4_000,
        qty_tercetak=8_000,
        cetak_delivered_explicit=4_000,
    )
    assert row["stock"] == 6_000
    assert row["stock_dapat_dijual"] == -22_000
    assert row["qty_polos"] == 2_000
    assert row["tercetak_belum_kirim"] == 4_000
    assert row["cetak_sudah_dikirim"] == 4_000
    assert row["qty_tercetak"] == 8_000
    # Equivalent without double-counting shipped in commitment:
    assert row["stock_dapat_dijual"] == (10_000 - 32_000)


def test_stock_dapat_dijual_no_double_count_delivery():
    """Shipment already in Stock must not subtract full PO again."""
    # Wrong (double): (stock_in - shipped) - po = 6000 - 32000 = -26000
    wrong = 6_000 - 32_000
    right = compute_excel_columns(
        saldo_awal=10_000,
        delivered_total=4_000,
        po_ordered=32_000,
        so_delivered=4_000,
        qty_tercetak=8_000,
    )
    assert right["stock_dapat_dijual"] == -22_000
    assert right["stock_dapat_dijual"] != wrong


def test_polos_shipment_reduces_polos_not_cetak():
    row = compute_excel_columns(
        saldo_awal=10_000,
        delivered_total=1_000,  # polos out
        po_ordered=0,
        so_delivered=0,
        qty_tercetak=0,
        cetak_delivered_explicit=0,
    )
    assert row["stock"] == 9_000
    assert row["qty_polos"] == 9_000
    assert row["cetak_sudah_dikirim"] == 0
    assert row["tercetak_belum_kirim"] == 0


def test_partial_shipment_and_so_cancel_commitment():
    # Book stock already 5k after partial ship; PO 10k shipped 3k → open 7k → -2k
    active = compute_excel_columns(
        saldo_awal=8_000,
        delivered_total=3_000,
        po_ordered=10_000,
        so_delivered=3_000,
        qty_tercetak=3_000,
        cetak_delivered_explicit=3_000,
        stock_book=5_000,
    )
    assert active["stock"] == 5_000
    assert active["open_so_commitment"] == 7_000
    assert active["stock_dapat_dijual"] == -2_000
    assert active["tercetak_belum_kirim"] == 0

    # Cancelled SO → po_ordered treated as 0 by caller
    cancelled = compute_excel_columns(
        saldo_awal=5_000,
        delivered_total=0,
        po_ordered=0,
        so_delivered=0,
        qty_tercetak=0,
    )
    assert cancelled["stock_dapat_dijual"] == 5_000


def test_multiwarna_parent_not_sum_of_colors():
    # Progress slot already parent-attributed; unproven → 0 tercetak
    assert parent_qty_tercetak_from_progress(
        {"completed_qty": 8_000, "completion_unproven": False}
    ) == 8_000
    assert parent_qty_tercetak_from_progress(
        {"completed_qty": 24_000, "completion_unproven": True}  # 3× warna mistake
    ) == 0.0


def test_reversal_excluded_from_ledger_components():
    movements = [
        {"id": "o1", "master_item_id": "M1", "movement_type": "OPENING_BALANCE", "qty_delta": 10_000, "branch_id": "B1"},
        {"id": "p1", "master_item_id": "M1", "movement_type": "PURCHASE_RECEIPT", "qty_delta": 500, "branch_id": "B1"},
        {"id": "d1", "master_item_id": "M1", "movement_type": "SALES_DELIVERY", "qty_delta": -400, "branch_id": "B1"},
        {"id": "r1", "master_item_id": "M1", "movement_type": "REVERSAL", "qty_delta": 400, "reference_id": "d1", "branch_id": "B1"},
    ]
    agg = aggregate_ledger_components(movements, master_item_ids=["M1"], branch_id="B1")
    assert agg["M1"]["saldo_awal"] == 10_000
    assert agg["M1"]["pembelian"] == 500
    assert agg["M1"]["delivered_total"] == 0  # delivery reversed


def test_branch_isolation_ygy_smg():
    movements = [
        {"id": "a", "master_item_id": "M1", "movement_type": "OPENING_BALANCE", "qty_delta": 100, "branch_id": "YGY"},
        {"id": "b", "master_item_id": "M1", "movement_type": "OPENING_BALANCE", "qty_delta": 200, "branch_id": "SMG"},
    ]
    ygy = aggregate_ledger_components(movements, master_item_ids=["M1"], branch_id="YGY")
    smg = aggregate_ledger_components(movements, master_item_ids=["M1"], branch_id="SMG")
    assert ygy["M1"]["saldo_awal"] == 100
    assert smg["M1"]["saldo_awal"] == 200


def test_apply_excel_overrides_condition_nulls():
    base = {"master_item_id": "M1", "physical_qty": 6000}
    excel = compute_excel_columns(
        saldo_awal=10_000,
        delivered_total=4_000,
        po_ordered=32_000,
        so_delivered=4_000,
        qty_tercetak=8_000,
        stock_book=6_000,
    )
    apply_excel_fields_to_catalog_row(base, excel, balance_present=True)
    assert base["stock_dapat_dijual"] == -22_000
    assert base["qty_siap_jual"] == -22_000
    assert base["qty_siap_jual_status"] == "READY"
    assert base["qty_polos_gudang"] == 2_000
    assert base["qty_sudah_cetak_gudang"] == 8_000
