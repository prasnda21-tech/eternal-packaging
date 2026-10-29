"""Option A — blank commitment + Stock Polos Dapat Dijual (local pure UAT)."""
from __future__ import annotations

from services.physical_stock import collect_blank_open_commitment_by_master
from services.stock_saat_ini_excel import (
    STATUS_READY,
    STATUS_UNVERIFIED,
    REASON_BLANK_PRINT_COMPLETION_UNPROVEN,
    REASON_BRANCH_DEMAND_WAREHOUSE_SCOPE,
    blank_need_for_physical_line,
    compute_excel_columns,
    resolve_stock_polos_dapat_dijual,
)


def test_owner_scenario_20000_5000_3000():
    excel = compute_excel_columns(
        stock_book=20_000,
        open_so_commitment=8_000,  # Jalur A unchanged (print+blank open)
        qty_tercetak=5_000,
        qty_tercetak_status=STATUS_READY,
        cetak_delivered_proven=0,
        print_delivery_proven=True,
        incomplete_print=False,
    )
    assert excel["stock"] == 20_000
    assert excel["qty_polos"] == 15_000
    assert excel["stock_dapat_dijual"] == 12_000  # Jalur A: stock − 8k

    blank = blank_need_for_physical_line(
        ordered_qty=3_000, is_print=False, delivered_qty=0,
    )
    print_done = blank_need_for_physical_line(
        ordered_qty=5_000,
        is_print=True,
        print_completed_qty=5_000,
        print_completion_proven=True,
    )
    assert blank["qty"] == 3_000
    assert print_done["qty"] == 0
    polos_dapat = resolve_stock_polos_dapat_dijual(
        qty_polos=excel["qty_polos"],
        qty_polos_status=excel["qty_polos_status"],
        blank_open_commitment=3_000,
        blank_open_commitment_status=STATUS_READY,
    )
    assert polos_dapat["qty"] == 12_000
    assert polos_dapat["status"] == STATUS_READY


def test_jalur_a_stock_dapat_dijual_unchanged_when_blank_differs():
    excel = compute_excel_columns(
        stock_book=20_000,
        open_so_commitment=3_000,
        qty_tercetak=5_000,
        qty_tercetak_status=STATUS_READY,
        cetak_delivered_proven=0,
        print_delivery_proven=True,
    )
    assert excel["stock_dapat_dijual"] == 17_000
    polos_dapat = resolve_stock_polos_dapat_dijual(
        qty_polos=15_000,
        qty_polos_status=STATUS_READY,
        blank_open_commitment=3_000,
        blank_open_commitment_status=STATUS_READY,
    )
    assert polos_dapat["qty"] == 12_000
    assert polos_dapat["qty"] != excel["stock_dapat_dijual"]


def test_unproven_print_completion_fail_closed():
    need = blank_need_for_physical_line(
        ordered_qty=5_000,
        is_print=True,
        print_completed_qty=None,
        print_completion_proven=False,
    )
    assert need["qty"] is None
    assert need["status"] == STATUS_UNVERIFIED
    assert need["reason"] == REASON_BLANK_PRINT_COMPLETION_UNPROVEN
    polos_dapat = resolve_stock_polos_dapat_dijual(
        qty_polos=15_000,
        qty_polos_status=STATUS_READY,
        blank_open_commitment=None,
        blank_open_commitment_status=STATUS_UNVERIFIED,
        blank_open_commitment_reason=REASON_BLANK_PRINT_COMPLETION_UNPROVEN,
    )
    assert polos_dapat["qty"] is None
    assert polos_dapat["status"] == STATUS_UNVERIFIED


def test_warehouse_scope_fail_closed():
    slot = resolve_stock_polos_dapat_dijual(
        qty_polos=15_000,
        qty_polos_status=STATUS_READY,
        blank_open_commitment=3_000,
        blank_open_commitment_status=STATUS_READY,
        warehouse_scoped=True,
    )
    assert slot["qty"] is None
    assert slot["reason"] == REASON_BRANCH_DEMAND_WAREHOUSE_SCOPE


def test_partial_print_blank_still_needed():
    need = blank_need_for_physical_line(
        ordered_qty=8_000,
        is_print=True,
        print_completed_qty=5_000,
        print_completion_proven=True,
    )
    assert need["qty"] == 3_000


def test_collect_blank_multi_so_print_done_plus_blank_so():
    orders = [
        {
            "id": "so-print",
            "cancelled": False,
            "line_items": [
                {
                    "line_id": "ln-p",
                    "master_item_id": "m-cup",
                    "quantity": 5_000,
                    "production_process": "print",
                },
            ],
            "operator_splits": [
                {"line_idx": 0, "qty_completed": 5_000},
            ],
        },
        {
            "id": "so-blank",
            "cancelled": False,
            "line_items": [
                {
                    "line_id": "ln-b",
                    "master_item_id": "m-cup",
                    "quantity": 3_000,
                    "production_process": "non_print",
                },
            ],
            "operator_splits": [],
        },
    ]
    out = collect_blank_open_commitment_by_master(
        orders,
        {},
        exclude_master_ids={"m-svc"},
        attributed_deliveries=[],
    )
    slot = out["m-cup"]
    assert slot["status"] == STATUS_READY
    assert slot["qty"] == 3_000  # print fully done → 0 blank need; blank SO 3k


def test_service_master_excluded_from_blank_commitment():
    orders = [{
        "id": "so-svc",
        "cancelled": False,
        "line_items": [{
            "line_id": "ln-s",
            "master_item_id": "m-svc",
            "quantity": 9_999,
            "production_process": "print",
            "item_type": "SERVICE",
        }],
        "operator_splits": [{"line_idx": 0, "qty_completed": 0}],
    }]
    out = collect_blank_open_commitment_by_master(
        orders, {}, exclude_master_ids={"m-svc"}, attributed_deliveries=[],
    )
    assert "m-svc" not in out



def test_negative_stock_and_no_history_zeros():
    neg = compute_excel_columns(stock_book=-500, open_so_commitment=0)
    assert neg["stock"] == -500
    assert neg["stock_dapat_dijual"] == -500
    zero = resolve_stock_polos_dapat_dijual(
        qty_polos=0,
        qty_polos_status=STATUS_READY,
        blank_open_commitment=0,
        blank_open_commitment_status=STATUS_READY,
    )
    assert zero["qty"] == 0
