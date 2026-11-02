"""PR #664 — Owner Excel SSI formula + safety (null when unproven)."""
from __future__ import annotations

from services.stock_saat_ini_excel import (
    REASON_DEPENDENT_UNKNOWN,
    REASON_NO_OPERATOR_EVIDENCE,
    REASON_PRINT_DELIVERY_UNPROVEN,
    REASON_WIP_COMPOSITION_UNPROVEN,
    STATUS_READY,
    STATUS_UNVERIFIED,
    aggregate_active_so_commitment,
    aggregate_ledger_components,
    aggregate_proven_print_delivered_qty,
    apply_excel_fields_to_catalog_row,
    compute_excel_columns,
    incomplete_print_demand,
    parent_qty_tercetak_from_progress,
    resolve_qty_tercetak,
)
from services.physical_stock import aggregate_operational_orders


def test_proven_print_delivery_is_preserved_when_operator_count_is_lower():
    row = compute_excel_columns(
        stock_book=200, qty_tercetak=800,
        cetak_delivered_proven=1000, print_delivery_proven=True,
    )
    assert row["cetak_sudah_dikirim"] == 1000
    assert row["cetak_sudah_dikirim_status"] == STATUS_READY
    assert row["qty_tercetak"] == 800
    assert row["tercetak_belum_kirim"] is None
    assert row["qty_polos"] is None
    assert row["qty_polos_reason"] == "PRINT_DELIVERY_EXCEEDS_COMPLETION"
    mapped = apply_excel_fields_to_catalog_row({}, row)
    assert "melebihi" in mapped["qty_polos_gudang_note"]


def test_print_delivery_equal_to_completion_has_zero_printed_remaining():
    row = compute_excel_columns(
        stock_book=200, qty_tercetak=1000,
        cetak_delivered_proven=1000, print_delivery_proven=True,
    )
    assert row["tercetak_belum_kirim"] == 0
    assert row["qty_polos"] == 200


def test_proven_print_delivery_stays_visible_without_operator_history():
    row = compute_excel_columns(
        stock_book=200, qty_tercetak=None,
        cetak_delivered_proven=1000, print_delivery_proven=True,
    )
    assert row["cetak_sudah_dikirim"] == 1000
    assert row["qty_polos"] is None


def test_owner_excel_example_with_proven_print_delivery():
    """Stock masuk 10k, PO 32k, operator 8k, cetak kirim 4k → Owner numbers."""
    row = compute_excel_columns(
        saldo_awal=10_000,
        pembelian=0,
        delivered_total=4_000,
        po_ordered=32_000,
        so_delivered=4_000,
        qty_tercetak=8_000,
        cetak_delivered_explicit=4_000,  # proven print SJ
    )
    assert row["stock"] == 6_000
    assert row["stock_dapat_dijual"] == -22_000
    assert row["qty_polos"] == 2_000
    assert row["tercetak_belum_kirim"] == 4_000
    assert row["cetak_sudah_dikirim"] == 4_000
    assert row["qty_tercetak"] == 8_000
    assert row["cetak_sudah_dikirim_status"] == STATUS_READY
    assert row["stock_dapat_dijual"] == (10_000 - 32_000)


def test_stock_dapat_dijual_no_double_count_delivery():
    """Shipment already in Stock must not subtract full PO again."""
    wrong = 6_000 - 32_000
    right = compute_excel_columns(
        saldo_awal=10_000,
        delivered_total=4_000,
        po_ordered=32_000,
        so_delivered=4_000,
        qty_tercetak=8_000,
        cetak_delivered_explicit=4_000,
    )
    assert right["stock_dapat_dijual"] == -22_000
    assert right["stock_dapat_dijual"] != wrong


def test_unproven_print_delivery_is_null_not_estimated():
    """Mixed/unknown print vs polos → null, never min(so_delivered, qty_tercetak)."""
    row = compute_excel_columns(
        saldo_awal=10_000,
        delivered_total=5_000,  # mixed plain+print unknown split
        po_ordered=32_000,
        so_delivered=5_000,
        qty_tercetak=8_000,
        print_delivery_proven=False,
    )
    assert row["stock"] == 5_000
    assert row["stock_dapat_dijual"] == -22_000  # Stock − (PO − so_delivered)
    assert row["cetak_sudah_dikirim"] is None
    assert row["cetak_sudah_dikirim_status"] == STATUS_UNVERIFIED
    assert row["tercetak_belum_kirim"] is None
    assert row["qty_polos"] is None
    # Must NOT equal the old unsafe estimate min(5000, 8000)=5000
    assert row["cetak_sudah_dikirim"] != 5_000


def test_polos_shipment_with_proven_zero_print():
    row = compute_excel_columns(
        saldo_awal=10_000,
        delivered_total=1_000,
        po_ordered=0,
        so_delivered=0,
        qty_tercetak=0,
        cetak_delivered_proven=0,
        print_delivery_proven=True,
    )
    assert row["stock"] == 9_000
    assert row["qty_polos"] == 9_000
    assert row["cetak_sudah_dikirim"] == 0
    assert row["tercetak_belum_kirim"] == 0


def test_partial_shipment_and_so_cancel_commitment():
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

    cancelled = compute_excel_columns(
        saldo_awal=5_000,
        delivered_total=0,
        po_ordered=0,
        so_delivered=0,
        qty_tercetak=0,
        cetak_delivered_proven=0,
        print_delivery_proven=True,
    )
    assert cancelled["stock_dapat_dijual"] == 5_000
    # Orphaned historical delivery after cancel must not invent negative commitment.
    orphan = compute_excel_columns(
        stock_book=10_000,
        po_ordered=0,
        so_delivered=4_000,
        delivered_total=4_000,
        qty_tercetak=0,
        cetak_delivered_proven=0,
        print_delivery_proven=True,
    )
    assert orphan["open_so_commitment"] == 0
    assert orphan["stock_dapat_dijual"] == 10_000


def test_multi_so_cancelled_a_does_not_reduce_active_b():
    """SO-A cancelled del 4k + SO-B active 10k/0 → commitment 10k (not 6k)."""
    commitment = aggregate_active_so_commitment(
        active_lines=[{
            "order_id": "SO-B",
            "master_item_id": "M1",
            "ordered_qty": 10_000,
            "line_id": "ln-b",
            "line_idx": 0,
        }],
        attributed_deliveries=[
            # Cancelled SO-A delivery must not be in the attributed set.
            # Only active-order deliveries are passed by the catalog.
        ],
    )
    assert commitment.get("M1", 0) == 10_000
    # If a bug passed SO-A delivery without active line match, ignore it.
    commitment2 = aggregate_active_so_commitment(
        active_lines=[{
            "order_id": "SO-B",
            "master_item_id": "M1",
            "ordered_qty": 10_000,
            "line_id": "ln-b",
            "line_idx": 0,
        }],
        attributed_deliveries=[{
            "order_id": "SO-A",
            "master_item_id": "M1",
            "qty": 4_000,
            "line_id": "ln-a",
            "line_idx": 0,
        }],
    )
    assert commitment2.get("M1", 0) == 10_000
    row = compute_excel_columns(
        stock_book=10_000,
        po_ordered=10_000,
        open_so_commitment=commitment2.get("M1", 0),
        qty_tercetak=0,
        cetak_delivered_proven=0,
        print_delivery_proven=True,
    )
    assert row["open_so_commitment"] == 10_000
    assert row["stock_dapat_dijual"] == 0


def test_over_delivery_so_a_does_not_reduce_so_b():
    commitment = aggregate_active_so_commitment(
        active_lines=[
            {
                "order_id": "SO-A",
                "master_item_id": "M1",
                "ordered_qty": 100,
                "line_id": "ln-a",
                "line_idx": 0,
            },
            {
                "order_id": "SO-B",
                "master_item_id": "M1",
                "ordered_qty": 100,
                "line_id": "ln-b",
                "line_idx": 0,
            },
        ],
        attributed_deliveries=[{
            "order_id": "SO-A",
            "master_item_id": "M1",
            "qty": 150,
            "line_id": "ln-a",
            "line_idx": 0,
        }],
    )
    assert commitment.get("M1", 0) == 100  # only SO-B remains


def test_two_lines_same_product_one_so():
    commitment = aggregate_active_so_commitment(
        active_lines=[
            {
                "order_id": "SO-1",
                "master_item_id": "M1",
                "ordered_qty": 500,
                "line_id": "ln-1",
                "line_idx": 0,
            },
            {
                "order_id": "SO-1",
                "master_item_id": "M1",
                "ordered_qty": 300,
                "line_id": "ln-2",
                "line_idx": 1,
            },
        ],
        attributed_deliveries=[{
            "order_id": "SO-1",
            "master_item_id": "M1",
            "qty": 200,
            "line_id": "ln-1",
            "line_idx": 0,
        }],
    )
    assert commitment.get("M1", 0) == 600  # (500-200) + 300


def test_ambiguous_delivery_without_line_id_fail_closed():
    """Two lines same master — delivery lacking line identity must not reduce."""
    commitment = aggregate_active_so_commitment(
        active_lines=[
            {
                "order_id": "SO-1",
                "master_item_id": "M1",
                "ordered_qty": 500,
                "line_id": "ln-1",
                "line_idx": 0,
            },
            {
                "order_id": "SO-1",
                "master_item_id": "M1",
                "ordered_qty": 500,
                "line_id": "ln-2",
                "line_idx": 1,
            },
        ],
        attributed_deliveries=[{
            "order_id": "SO-1",
            "master_item_id": "M1",
            "qty": 400,
            # no line_id / line_idx
        }],
    )
    assert commitment.get("M1", 0) == 1_000


def test_qty_polos_incomplete_print_unverified():
    assert incomplete_print_demand({
        "has_print_line": True,
        "print_ordered_qty": 5_000,
        "print_completed_qty": 3_000,
    }) is True
    row = compute_excel_columns(
        stock_book=10_000,
        qty_tercetak=3_000,
        cetak_delivered_proven=0,
        print_delivery_proven=True,
        incomplete_print=True,
    )
    assert row["tercetak_belum_kirim"] == 3_000
    assert row["qty_polos"] is None
    assert row["qty_polos_status"] == STATUS_UNVERIFIED
    assert row["qty_polos_reason"] == REASON_WIP_COMPOSITION_UNPROVEN


def test_qty_polos_complete_print_ready_simple_case():
    assert incomplete_print_demand({
        "has_print_line": True,
        "print_ordered_qty": 3_000,
        "print_completed_qty": 3_000,
    }) is False
    row = compute_excel_columns(
        stock_book=10_000,
        qty_tercetak=3_000,
        cetak_delivered_proven=0,
        print_delivery_proven=True,
        incomplete_print=False,
    )
    assert row["qty_polos"] == 7_000
    assert row["qty_polos_status"] == STATUS_READY


def test_qty_tercetak_uses_print_completed_not_non_print_ready():
    """Non-print siap-kirim must not inflate Qty Tercetak (Owner Excel)."""
    resolved = resolve_qty_tercetak(
        {
            "completed_qty": 18_000,  # includes non_print ready
            "print_completed_qty": 8_000,
            "completion_unproven": False,
            "operator_evidence_present": True,
            "has_print_line": True,
        }
    )
    assert resolved["qty"] == 8_000
    # Legacy completed_qty fallback only with explicit operator evidence.
    assert resolve_qty_tercetak(
        {
            "completed_qty": 8_000,
            "completion_unproven": False,
            "operator_evidence_present": True,
        }
    )["qty"] == 8_000


def test_completion_unproven_is_null_not_zero():
    resolved = resolve_qty_tercetak(
        {"completed_qty": 24_000, "completion_unproven": True}
    )
    assert resolved["qty"] is None
    assert resolved["status"] == STATUS_UNVERIFIED
    assert parent_qty_tercetak_from_progress(
        {"completed_qty": 24_000, "completion_unproven": True}
    ) is None
    assert parent_qty_tercetak_from_progress(
        {
            "completed_qty": 8_000,
            "completion_unproven": False,
            "operator_evidence_present": True,
        }
    ) == 8_000

    row = compute_excel_columns(
        saldo_awal=10_000,
        delivered_total=0,
        po_ordered=32_000,
        so_delivered=0,
        qty_tercetak=None,
        qty_tercetak_status=STATUS_UNVERIFIED,
        qty_tercetak_reason=REASON_NO_OPERATOR_EVIDENCE,
        cetak_delivered_proven=0,
        print_delivery_proven=True,
    )
    assert row["qty_tercetak"] is None
    assert row["qty_tercetak_status"] == STATUS_UNVERIFIED
    assert row["qty_tercetak_reason"] == REASON_NO_OPERATOR_EVIDENCE
    assert row["tercetak_belum_kirim"] is None
    assert row["qty_polos"] is None
    # Stock / Stock Dapat Dijual stay numeric
    assert row["stock"] == 10_000
    assert row["stock_dapat_dijual"] == 10_000 - 32_000


def test_matrix_a_no_so_no_operator_keeps_stock_column_level_unverified():
    """A: no SO + no operator → stock proven; activity cols Belum terverifikasi."""
    resolved = resolve_qty_tercetak(None)
    assert resolved["qty"] is None
    assert resolved["reason"] == REASON_NO_OPERATOR_EVIDENCE
    row = compute_excel_columns(
        saldo_awal=10_000,
        pembelian=500,
        delivered_total=0,
        po_ordered=0,
        so_delivered=0,
        qty_tercetak=None,
        qty_tercetak_status=STATUS_UNVERIFIED,
        qty_tercetak_reason=REASON_NO_OPERATOR_EVIDENCE,
        print_delivery_proven=True,
        cetak_delivered_proven=0,
        stock_book=10_500,
    )
    assert row["saldo_awal"] == 10_000
    assert row["pembelian"] == 500
    assert row["stock"] == 10_500
    assert row["stock_dapat_dijual"] == 10_500
    assert row["qty_tercetak"] is None
    assert row["qty_tercetak_reason"] == REASON_NO_OPERATOR_EVIDENCE
    assert row["tercetak_belum_kirim"] is None
    assert row["qty_polos"] is None
    apply_excel_fields_to_catalog_row(
        {"master_item_id": "A", "physical_qty": 10_500},
        row,
        balance_present=True,
    )


def test_matrix_b_so_only_activates_dapat_dijual_not_fake_tercetak():
    """B: SO only → Stock Dapat Dijual READY; Qty Tercetak not fake 0."""
    progress = aggregate_operational_orders(
        [
            {
                "id": "so-b",
                "branch_id": "B1",
                "line_items": [
                    {
                        "master_item_id": "M1",
                        "quantity": 32_000,
                        "production_process": "print",
                    }
                ],
                "operator_splits": [],
            }
        ]
    )
    slot = progress["M1"]
    assert slot["ordered_qty"] == 32_000
    assert slot.get("has_print_line") is True
    assert slot.get("operator_evidence_present") is False
    resolved = resolve_qty_tercetak(slot)
    assert resolved["qty"] is None
    assert resolved["reason"] == REASON_NO_OPERATOR_EVIDENCE
    row = compute_excel_columns(
        saldo_awal=10_000,
        delivered_total=0,
        po_ordered=slot["ordered_qty"],
        so_delivered=0,
        qty_tercetak=resolved["qty"],
        qty_tercetak_status=resolved["status"],
        qty_tercetak_reason=resolved.get("reason"),
        print_delivery_proven=True,
        cetak_delivered_proven=0,
        stock_book=10_000,
    )
    assert row["stock"] == 10_000
    assert row["stock_dapat_dijual"] == 10_000 - 32_000
    assert row["qty_tercetak"] is None
    assert row["qty_polos"] is None


def test_matrix_c_operator_only_activates_tercetak():
    """C: operator evidence alone is enough for Qty Tercetak (SO not required)."""
    resolved = resolve_qty_tercetak(
        {
            "ordered_qty": 0,
            "print_completed_qty": 8_000,
            "operator_evidence_present": True,
            "has_print_line": True,
            "completion_unproven": False,
        }
    )
    assert resolved["qty"] == 8_000
    row = compute_excel_columns(
        saldo_awal=10_000,
        delivered_total=0,
        po_ordered=0,
        so_delivered=0,
        qty_tercetak=resolved["qty"],
        qty_tercetak_status=resolved["status"],
        print_delivery_proven=True,
        cetak_delivered_proven=0,
        stock_book=10_000,
    )
    assert row["qty_tercetak"] == 8_000
    assert row["tercetak_belum_kirim"] == 8_000
    assert row["qty_polos"] == 2_000
    assert row["stock_dapat_dijual"] == 10_000


def test_matrix_d_so_and_operator_owner_excel_numbers():
    """D: SO + operator + proven print SJ → Owner Excel example."""
    row = compute_excel_columns(
        saldo_awal=10_000,
        pembelian=0,
        delivered_total=4_000,
        po_ordered=32_000,
        so_delivered=4_000,
        qty_tercetak=8_000,
        cetak_delivered_explicit=4_000,
        stock_book=6_000,
    )
    assert row["stock"] == 6_000
    assert row["stock_dapat_dijual"] == -22_000
    assert row["qty_tercetak"] == 8_000
    assert row["qty_polos"] == 2_000
    assert row["tercetak_belum_kirim"] == 4_000
    assert row["cetak_sudah_dikirim"] == 4_000


def test_matrix_e_verified_zero():
    """E: evidence proves empty → display 0, not Belum terverifikasi."""
    resolved = resolve_qty_tercetak(
        {
            "ordered_qty": 5_000,
            "print_completed_qty": 0,
            "operator_evidence_present": True,
            "has_print_line": True,
        }
    )
    assert resolved["qty"] == 0
    assert resolved["status"] == STATUS_READY
    # Non-print-only SO → verified tercetak 0.
    assert resolve_qty_tercetak(
        {"ordered_qty": 5_000, "has_print_line": False, "operator_evidence_present": False}
    )["qty"] == 0
    row = compute_excel_columns(
        saldo_awal=9_000,
        delivered_total=1_000,
        po_ordered=0,
        so_delivered=0,
        qty_tercetak=0,
        cetak_delivered_proven=0,
        print_delivery_proven=True,
        stock_book=8_000,
    )
    assert row["qty_tercetak"] == 0
    assert row["cetak_sudah_dikirim"] == 0
    assert row["tercetak_belum_kirim"] == 0
    assert row["qty_polos"] == 8_000


def test_matrix_f_missing_historical_data_column_level():
    """F: missing history → only affected columns UNVERIFIED."""
    row = compute_excel_columns(
        saldo_awal=10_000,
        delivered_total=5_000,
        po_ordered=12_000,
        so_delivered=5_000,
        qty_tercetak=8_000,
        print_delivery_proven=False,
        stock_book=5_000,
    )
    assert row["stock"] == 5_000
    assert row["stock_dapat_dijual"] == 5_000 - (12_000 - 5_000)
    assert row["qty_tercetak"] == 8_000
    assert row["cetak_sudah_dikirim"] is None
    assert row["cetak_sudah_dikirim_reason"] == REASON_PRINT_DELIVERY_UNPROVEN
    assert row["tercetak_belum_kirim"] is None
    assert row["qty_polos_reason"] == REASON_DEPENDENT_UNKNOWN


def test_matrix_g_multiwarna_counted_once():
    """G: multiwarna / process group FULLY_COMPLETED → parent qty once."""
    progress = aggregate_operational_orders(
        [
            {
                "id": "so-mw",
                "cancelled": False,
                "line_items": [
                    {
                        "line_id": "ln-main",
                        "master_item_id": "M1",
                        "quantity": 8_000,
                        "production_process": "print",
                        "item_type": "INVENTORY",
                    },
                    {
                        "line_id": "ln-w2",
                        "master_item_id": "SVC1",
                        "parent_line_id": "ln-main",
                        "quantity": 8_000,
                        "production_process": "print",
                        "item_type": "SERVICE",
                    },
                ],
                "operator_splits": [
                    {"line_idx": 0, "qty_completed": 8_000},
                    {"line_idx": 1, "qty_completed": 8_000},
                ],
            }
        ],
        exclude_master_ids=["SVC1"],
    )
    resolved = resolve_qty_tercetak(progress["M1"])
    assert resolved["qty"] == 8_000
    assert progress["M1"].get("operator_evidence_present") is True
    assert "SVC1" not in progress


def test_matrix_h_partial_cancelled_shipment():
    """H: cancelled SJ excluded; partial print SJ credits only proven qty."""
    orders = [
        {
            "id": "po-h",
            "line_items": [
                {
                    "master_item_id": "M1",
                    "quantity": 10_000,
                    "production_process": "print",
                },
            ],
            "shipments": [
                {
                    "id": "sh-ok",
                    "delivery_status": "Terkirim",
                    "qty_delivered": 4_000,
                    "lines": [{"line_idx": 0, "qty": 4_000}],
                },
                {
                    "id": "sh-cancel",
                    "delivery_status": "Dibatalkan",
                    "is_cancelled": True,
                    "qty_delivered": 2_000,
                    "lines": [{"line_idx": 0, "qty": 2_000}],
                },
            ],
        }
    ]
    qty, prov = aggregate_proven_print_delivered_qty(orders)
    assert qty.get("M1") == 4_000
    assert prov.get("M1") == "confirmed_print_shipment"
    row = compute_excel_columns(
        saldo_awal=10_000,
        delivered_total=4_000,
        po_ordered=10_000,
        so_delivered=4_000,
        qty_tercetak=8_000,
        cetak_delivered_proven=qty["M1"],
        print_delivery_proven=True,
        stock_book=6_000,
    )
    assert row["cetak_sudah_dikirim"] == 4_000
    assert row["tercetak_belum_kirim"] == 4_000
    assert row["stock_dapat_dijual"] == 6_000 - (10_000 - 4_000)


def test_mixed_print_and_polos_shipment_attribution():
    """Print SJ lines credit Cetak; non_print does not."""
    orders = [
        {
            "id": "po1",
            "line_items": [
                {
                    "master_item_id": "M1",
                    "quantity": 10_000,
                    "production_process": "print",
                },
                {
                    "master_item_id": "M1",
                    "quantity": 2_000,
                    "production_process": "non_print",
                },
            ],
            "shipments": [
                {
                    "id": "sh-print",
                    "delivery_status": "Terkirim",
                    "qty_delivered": 4_000,
                    "lines": [{"line_idx": 0, "qty": 4_000}],
                },
                {
                    "id": "sh-polos",
                    "delivery_status": "Terkirim",
                    "qty_delivered": 1_000,
                    "lines": [{"line_idx": 1, "qty": 1_000}],
                },
            ],
        }
    ]
    qty, prov = aggregate_proven_print_delivered_qty(orders)
    assert prov.get("M1") == "confirmed_print_shipment"
    assert qty.get("M1") == 4_000  # polos 1k excluded

    row = compute_excel_columns(
        saldo_awal=10_000,
        delivered_total=5_000,
        po_ordered=12_000,
        so_delivered=5_000,
        qty_tercetak=8_000,
        cetak_delivered_proven=qty["M1"],
        print_delivery_proven=True,
        stock_book=5_000,
    )
    assert row["cetak_sudah_dikirim"] == 4_000
    assert row["tercetak_belum_kirim"] == 4_000
    assert row["qty_polos"] == 1_000  # 5000 − 4000
    assert row["stock_dapat_dijual"] == 5_000 - (12_000 - 5_000)


def test_cancelled_shipment_and_retry_no_double_print_credit():
    orders = [
        {
            "id": "po-retry",
            "line_items": [
                {"master_item_id": "M1", "quantity": 8_000, "production_process": "print"},
            ],
            "shipments": [
                {
                    "id": "sh-dup",
                    "delivery_status": "Terkirim",
                    "qty_delivered": 4_000,
                    "lines": [{"line_idx": 0, "qty": 4_000}],
                },
                {
                    "id": "sh-dup",
                    "delivery_status": "Terkirim",
                    "qty_delivered": 4_000,
                    "lines": [{"line_idx": 0, "qty": 4_000}],
                },
                {
                    "id": "sh-cancel",
                    "delivery_status": "Dibatalkan",
                    "is_cancelled": True,
                    "qty_delivered": 9_999,
                    "lines": [{"line_idx": 0, "qty": 9_999}],
                },
            ],
        }
    ]
    qty, prov = aggregate_proven_print_delivered_qty(orders)
    assert prov.get("M1") == "confirmed_print_shipment"
    assert qty.get("M1") == 4_000


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
    assert agg["M1"]["delivered_total"] == 0


def test_optional_opening_balance_not_prerequisite():
    """Saldo Awal optional — SSI columns compute without OPENING_BALANCE."""
    empty = aggregate_ledger_components([], master_item_ids=["M1"], branch_id="B1")
    assert empty == {}
    # Purchase-only path (no opening) still yields Stock via book or components.
    movements = [
        {"id": "p1", "master_item_id": "M1", "movement_type": "PURCHASE_RECEIPT", "qty_delta": 3_000, "branch_id": "B1"},
    ]
    agg = aggregate_ledger_components(movements, master_item_ids=["M1"], branch_id="B1")
    assert agg["M1"]["saldo_awal"] == 0
    assert agg["M1"]["pembelian"] == 3_000
    row = compute_excel_columns(
        saldo_awal=0,
        pembelian=3_000,
        delivered_total=0,
        po_ordered=0,
        qty_tercetak=None,
        qty_tercetak_status=STATUS_UNVERIFIED,
        print_delivery_proven=True,
        cetak_delivered_proven=0,
        stock_book=3_000,
    )
    assert row["saldo_awal"] == 0
    assert row["pembelian"] == 3_000
    assert row["stock"] == 3_000
    assert row["stock_dapat_dijual"] == 3_000
    assert row["qty_tercetak"] is None


def test_repeated_manual_adjustment_no_double_post_in_components():
    """Subsequent corrections are MANUAL_ADJUSTMENT deltas — summed once each."""
    movements = [
        {"id": "o1", "master_item_id": "M1", "movement_type": "OPENING_BALANCE", "qty_delta": 10_000, "branch_id": "B1"},
        {"id": "a1", "master_item_id": "M1", "movement_type": "MANUAL_ADJUSTMENT", "qty_delta": -500, "branch_id": "B1"},
        {"id": "a2", "master_item_id": "M1", "movement_type": "MANUAL_ADJUSTMENT", "qty_delta": 200, "branch_id": "B1"},
        # Duplicate id must not appear in real ledger; components sum each movement once.
        {"id": "a3", "master_item_id": "M1", "movement_type": "MANUAL_ADJUSTMENT", "qty_delta": 0, "branch_id": "B1"},
    ]
    agg = aggregate_ledger_components(movements, master_item_ids=["M1"], branch_id="B1")
    assert agg["M1"]["saldo_awal"] == 10_000
    assert agg["M1"]["adjustment_net"] == -300
    row = compute_excel_columns(
        saldo_awal=10_000,
        pembelian=0,
        adjustment_net=-300,
        delivered_total=0,
        po_ordered=0,
        qty_tercetak=0,
        print_delivery_proven=True,
        cetak_delivered_proven=0,
        stock_book=9_700,
    )
    assert row["stock"] == 9_700
    assert row["adjustment_net"] == -300
    # Without stock_book, component math matches book after adjustments.
    computed = compute_excel_columns(
        saldo_awal=10_000,
        adjustment_net=-300,
        delivered_total=0,
        po_ordered=0,
        qty_tercetak=0,
        print_delivery_proven=True,
        cetak_delivered_proven=0,
    )
    assert computed["stock"] == 9_700


def test_branch_isolation_ygy_smg():
    movements = [
        {"id": "a", "master_item_id": "M1", "movement_type": "OPENING_BALANCE", "qty_delta": 100, "branch_id": "YGY"},
        {"id": "b", "master_item_id": "M1", "movement_type": "OPENING_BALANCE", "qty_delta": 200, "branch_id": "SMG"},
    ]
    ygy = aggregate_ledger_components(movements, master_item_ids=["M1"], branch_id="YGY")
    smg = aggregate_ledger_components(movements, master_item_ids=["M1"], branch_id="SMG")
    assert ygy["M1"]["saldo_awal"] == 100
    assert smg["M1"]["saldo_awal"] == 200


def test_unverified_notes_are_reason_specific():
    base = {
        "master_item_id": "m1",
        "item_code": "X",
        "physical_qty": 1000,
        "balance_present": True,
    }
    excel = compute_excel_columns(
        saldo_awal=1000,
        delivered_total=0,
        po_ordered=0,
        so_delivered=0,
        qty_tercetak=None,
        qty_tercetak_status=STATUS_UNVERIFIED,
        cetak_delivered_proven=None,
        print_delivery_proven=False,
        stock_book=1000,
    )
    apply_excel_fields_to_catalog_row(base, excel, balance_present=True)
    assert base["qty_polos_gudang"] is None
    assert "Belum terverifikasi" in (base.get("qty_polos_gudang_note") or "")
    assert base["qty_sudah_cetak_gudang"] is None
    note = base.get("qty_sudah_cetak_gudang_note") or ""
    assert "Belum terverifikasi" in note or "multiwarna" in note.lower()


def test_apply_excel_maps_unverified_to_belum_tersedia():
    base = {"master_item_id": "M1", "physical_qty": 6000}
    excel = compute_excel_columns(
        saldo_awal=10_000,
        delivered_total=4_000,
        po_ordered=32_000,
        so_delivered=4_000,
        qty_tercetak=8_000,
        print_delivery_proven=False,
        stock_book=6_000,
    )
    apply_excel_fields_to_catalog_row(base, excel, balance_present=True)
    assert base["stock_dapat_dijual"] == -22_000
    assert base["qty_siap_jual"] == -22_000
    assert base["cetak_sudah_dikirim"] is None
    assert base["qty_polos_gudang"] is None
    assert base["qty_polos_gudang_status"] == "BELUM_TERSEDIA"
