"""Sisa Belum Cetak — reuse print blank_need; never subtract shipment again."""
from __future__ import annotations

from services.mode_b_legacy_demand import (
    filter_orders_for_branch,
    mongo_order_branch_clauses,
    order_attributed_to_branch,
    sanitize_settings,
)
from services.physical_stock import collect_blank_open_commitment_by_master
from services.stock_saat_ini_excel import (
    ORDERS_FETCH_LIMIT,
    STATUS_READY,
    STATUS_UNVERIFIED,
    REASON_BLANK_PRINT_COMPLETION_UNPROVEN,
    REASON_ORDERS_FETCH_CAP_REACHED,
    SOURCE_SISA_BELUM_CETAK,
    apply_excel_fields_to_catalog_row,
    aggregate_ledger_components,
    compute_excel_columns,
    resolve_sisa_belum_cetak_catalog_slot,
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


# ---------------------------------------------------------------------------
# Owner PO fixtures (PR #683 recon samples) + reconstruction cases 1–8
# ---------------------------------------------------------------------------

SVC_EXCLUDE = {"m-svc-warna-a", "m-svc-warna-b", "m-svc-orphan"}


def _owner_po_100089_full():
    """100089 · PO/08/10/2026/000001 — ordered 500 / completed 500."""
    return {
        "id": "PO/08/10/2026/000001",
        "cancelled": False,
        "line_items": [
            {
                "line_id": "ln-100089",
                "master_item_id": "100089",
                "quantity": 500,
                "production_process": "print",
            },
            {
                "line_id": "ln-svc-a",
                "master_item_id": "m-svc-warna-a",
                "quantity": 500,
                "production_process": "print",
                "item_type": "SERVICE",
                "parent_line_id": "ln-100089",
            },
        ],
        "operator_splits": [
            {"line_idx": 0, "qty_completed": 500},
            {"line_idx": 1, "qty_completed": 500},
        ],
    }


def _owner_po_100032_full():
    """100032 · PO/27/10/2026/000015 — ordered 10000 / completed 10000."""
    return {
        "id": "PO/27/10/2026/000015",
        "cancelled": False,
        "line_items": [{
            "line_id": "ln-100032-a",
            "master_item_id": "100032",
            "quantity": 10000,
            "production_process": "print",
        }],
        "operator_splits": [{"line_idx": 0, "qty_completed": 10000}],
    }


def _owner_po_100032_two_lines():
    """100032 · PO/13/10/2026/000019 — 2×2000 fully completed."""
    return {
        "id": "PO/13/10/2026/000019",
        "cancelled": False,
        "line_items": [
            {
                "line_id": "ln-100032-b1",
                "master_item_id": "100032",
                "quantity": 2000,
                "production_process": "print",
            },
            {
                "line_id": "ln-100032-b2",
                "master_item_id": "100032",
                "quantity": 2000,
                "production_process": "print",
            },
        ],
        "operator_splits": [
            {"line_idx": 0, "qty_completed": 2000},
            {"line_idx": 1, "qty_completed": 2000},
        ],
    }


def _owner_po_100397_full():
    """100397 · PO/19/10/2026/000008 — ordered 1000 / completed 1000."""
    return {
        "id": "PO/19/10/2026/000008",
        "cancelled": False,
        "line_items": [{
            "line_id": "ln-100397",
            "master_item_id": "100397",
            "quantity": 1000,
            "production_process": "print",
        }],
        "operator_splits": [{"line_idx": 0, "qty_completed": 1000}],
    }


def test_owner_four_po_samples_sisa_zero_ready():
    """Owner verified samples: all print progress complete → sisa 0 READY."""
    orders = [
        _owner_po_100089_full(),
        _owner_po_100032_full(),
        _owner_po_100032_two_lines(),
        _owner_po_100397_full(),
    ]
    pack = collect_blank_open_commitment_by_master(
        orders, {}, exclude_master_ids=SVC_EXCLUDE, attributed_deliveries=[],
    )
    assert pack["sisa_belum_cetak"]["100089"] == {
        "qty": 0.0, "status": STATUS_READY, "reason": None,
    }
    assert pack["sisa_belum_cetak"]["100032"] == {
        "qty": 0.0, "status": STATUS_READY, "reason": None,
    }
    assert pack["sisa_belum_cetak"]["100397"] == {
        "qty": 0.0, "status": STATUS_READY, "reason": None,
    }


def test_case1_one_line_fully_completed():
    orders = [_owner_po_100397_full()]
    pack = collect_blank_open_commitment_by_master(
        orders, {}, exclude_master_ids=set(), attributed_deliveries=[],
    )
    assert pack["sisa_belum_cetak"]["100397"]["qty"] == 0.0
    assert pack["sisa_belum_cetak"]["100397"]["status"] == STATUS_READY


def test_case2_one_line_incomplete():
    orders = [{
        "id": "so-incomplete",
        "cancelled": False,
        "line_items": [{
            "line_id": "ln-p",
            "master_item_id": "100397",
            "quantity": 1000,
            "production_process": "print",
        }],
        "operator_splits": [{"line_idx": 0, "qty_completed": 400}],
    }]
    pack = collect_blank_open_commitment_by_master(
        orders, {}, exclude_master_ids=set(), attributed_deliveries=[],
    )
    assert pack["sisa_belum_cetak"]["100397"]["qty"] == 600.0
    assert pack["sisa_belum_cetak"]["100397"]["status"] == STATUS_READY


def test_case3_one_line_multi_operator():
    orders = [{
        "id": "so-multi-op",
        "cancelled": False,
        "line_items": [{
            "line_id": "ln-p",
            "master_item_id": "100089",
            "quantity": 500,
            "production_process": "print",
        }],
        "operator_splits": [
            {"line_idx": 0, "operator_id": "op-a", "qty_completed": 200},
            {"line_idx": 0, "operator_id": "op-b", "qty_completed": 250},
        ],
    }]
    pack = collect_blank_open_commitment_by_master(
        orders, {}, exclude_master_ids=set(), attributed_deliveries=[],
    )
    # 500 − (200+250) = 50
    assert pack["sisa_belum_cetak"]["100089"]["qty"] == 50.0
    assert pack["sisa_belum_cetak"]["100089"]["status"] == STATUS_READY


def test_case4_two_same_sku_lines_in_one_po():
    orders = [_owner_po_100032_two_lines()]
    # mutate second line incomplete
    orders[0]["operator_splits"][1]["qty_completed"] = 500
    pack = collect_blank_open_commitment_by_master(
        orders, {}, exclude_master_ids=set(), attributed_deliveries=[],
    )
    # line0: 2000−2000=0; line1: 2000−500=1500 → aggregate 1500
    assert pack["sisa_belum_cetak"]["100032"]["qty"] == 1500.0
    assert pack["sisa_belum_cetak"]["100032"]["status"] == STATUS_READY


def test_case5_addon_service_multi_warna_excluded_parent_ops_drive_sisa():
    """Multi-warna SERVICE excluded; parent ops drive sisa even if child incomplete."""
    orders = [{
        "id": "so-multi-warna",
        "cancelled": False,
        "line_items": [
            {
                "line_id": "ln-p",
                "master_item_id": "100089",
                "quantity": 500,
                "production_process": "print",
            },
            {
                "line_id": "ln-warna-a",
                "master_item_id": "m-svc-warna-a",
                "quantity": 500,
                "production_process": "print",
                "item_type": "SERVICE",
                "parent_line_id": "ln-p",
            },
            {
                "line_id": "ln-warna-b",
                "master_item_id": "m-svc-warna-b",
                "quantity": 500,
                "production_process": "print",
                "item_type": "SERVICE",
                "parent_line_id": "ln-p",
            },
        ],
        "operator_splits": [
            {"line_idx": 0, "qty_completed": 500},
            # warna incomplete — must not invent parent unfinished print
        ],
    }]
    pack = collect_blank_open_commitment_by_master(
        orders, {}, exclude_master_ids=SVC_EXCLUDE, attributed_deliveries=[],
    )
    assert "m-svc-warna-a" not in pack["sisa_belum_cetak"]
    assert "m-svc-warna-b" not in pack["sisa_belum_cetak"]
    assert pack["sisa_belum_cetak"]["100089"]["qty"] == 0.0
    assert pack["sisa_belum_cetak"]["100089"]["status"] == STATUS_READY


def test_case6_active_so_partial_progress():
    orders = [{
        "id": "so-partial-active",
        "cancelled": False,
        "line_items": [{
            "line_id": "ln-p",
            "master_item_id": "100032",
            "quantity": 10000,
            "production_process": "print",
        }],
        "operator_splits": [{"line_idx": 0, "qty_completed": 3500}],
    }]
    pack = collect_blank_open_commitment_by_master(
        orders, {}, exclude_master_ids=set(), attributed_deliveries=[],
    )
    assert pack["sisa_belum_cetak"]["100032"]["qty"] == 6500.0
    assert pack["sisa_belum_cetak"]["100032"]["status"] == STATUS_READY


def test_case7_so_without_operator_results_fail_closed():
    """No operator_splits → do not invent 0 completed; UNVERIFIED."""
    orders = [{
        "id": "so-no-ops",
        "cancelled": False,
        "line_items": [{
            "line_id": "ln-p",
            "master_item_id": "100397",
            "quantity": 1000,
            "production_process": "print",
        }],
        "operator_splits": [],
    }]
    pack = collect_blank_open_commitment_by_master(
        orders, {}, exclude_master_ids=set(), attributed_deliveries=[],
    )
    sisa = pack["sisa_belum_cetak"]["100397"]
    assert sisa["qty"] is None
    assert sisa["status"] == STATUS_UNVERIFIED
    assert sisa["reason"] == REASON_BLANK_PRINT_COMPLETION_UNPROVEN


def test_case8_unlinked_service_or_orphan_must_not_discard_parent_ops():
    """Root-cause regression: order-wide process_gate must not nullify line ops."""
    # 8a — unlinked SERVICE Add On + full parent ops (was UNVERIFIED before fix)
    orders_unlinked = [{
        "id": "PO/08/10/2026/000001-unlinked",
        "cancelled": False,
        "line_items": [
            {
                "line_id": "ln-100089",
                "master_item_id": "100089",
                "quantity": 500,
                "production_process": "print",
            },
            {
                "line_id": "ln-svc-orphan",
                "master_item_id": "m-svc-orphan",
                "quantity": 500,
                "production_process": "print",
                "item_type": "SERVICE",
                # no parent_line_id → order_unlinked_process
            },
        ],
        "operator_splits": [{"line_idx": 0, "qty_completed": 500}],
    }]
    pack_u = collect_blank_open_commitment_by_master(
        orders_unlinked, {}, exclude_master_ids=SVC_EXCLUDE, attributed_deliveries=[],
    )
    assert pack_u["sisa_belum_cetak"]["100089"]["qty"] == 0.0
    assert pack_u["sisa_belum_cetak"]["100089"]["status"] == STATUS_READY

    # 8b — orphan print sibling (missing master) + full ops on real parent
    orders_orphan = [{
        "id": "PO/27/10/2026/000015-orphan",
        "cancelled": False,
        "line_items": [
            {
                "line_id": "ln-ok",
                "master_item_id": "100032",
                "quantity": 10000,
                "production_process": "print",
            },
            {
                "line_id": "ln-orphan-print",
                "quantity": 1,
                "production_process": "print",
                # missing master_item_id → has_orphan_print
            },
        ],
        "operator_splits": [{"line_idx": 0, "qty_completed": 10000}],
    }]
    pack_o = collect_blank_open_commitment_by_master(
        orders_orphan, {}, exclude_master_ids=set(), attributed_deliveries=[],
    )
    assert pack_o["sisa_belum_cetak"]["100032"]["qty"] == 0.0
    assert pack_o["sisa_belum_cetak"]["100032"]["status"] == STATUS_READY

    # 8c — pagination/branch filter: SO not in the orders iterable → not counted
    # (collector only sees what catalog query hands it; missing SO ≠ invent 0)
    pack_missing = collect_blank_open_commitment_by_master(
        [], {}, exclude_master_ids=set(), attributed_deliveries=[],
    )
    assert "100089" not in pack_missing["sisa_belum_cetak"]
    # Catalog default for absent slot remains READY 0 at list_physical_stock_catalog
    # — documented separately; collector itself does not invent a master row.


def test_root_cause_gate_before_ops_before_after_contract():
    """Before: process_gate + ops → UNVERIFIED. After: numeric from ops."""
    orders = [{
        "id": "so-gate",
        "cancelled": False,
        "line_items": [
            {
                "line_id": "ln-p",
                "master_item_id": "100089",
                "quantity": 500,
                "production_process": "print",
            },
            {
                "line_id": "ln-svc",
                "master_item_id": "m-svc-orphan",
                "quantity": 500,
                "production_process": "print",
                "item_type": "SERVICE",
            },
        ],
        "operator_splits": [{"line_idx": 0, "qty_completed": 200}],
    }]
    pack = collect_blank_open_commitment_by_master(
        orders, {}, exclude_master_ids=SVC_EXCLUDE, attributed_deliveries=[],
    )
    sisa = pack["sisa_belum_cetak"]["100089"]
    assert sisa["status"] == STATUS_READY
    assert sisa["qty"] == 300.0  # 500 − 200; not null UNVERIFIED
    # Blank may still fail-closed under order-wide gate — sisa is independent.
    blank = pack["blank_open_commitment"]["100089"]
    assert blank["status"] == STATUS_UNVERIFIED
    assert blank["qty"] is None


# ---------------------------------------------------------------------------
# Completeness gate — Mode B branch coverage + fetch-cap READY 0 defect
# ---------------------------------------------------------------------------

_YGY_UUID = "branch-yogyakarta-uuid"
_HIST = "2024-01-15T00:00:00+00:00"
_NEW = "2026-10-01T00:00:00+00:00"


def _mode_b_ygy_settings(*, include_null=True):
    return sanitize_settings({
        "enabled": True,
        "org_branch_id": _YGY_UUID,
        "historical_cutoff_at": "2025-01-01T00:00:00+00:00",
        "accurate_branch_ids": ["50"],
        "include_null_branch": include_null,
    })


def _print_so(oid, mid, qty, completed, *, branch_id=None, created_at=_HIST, **extra):
    order = {
        "id": oid,
        "cancelled": False,
        "created_at": created_at,
        "line_items": [{
            "line_id": f"ln-{oid}",
            "master_item_id": mid,
            "quantity": qty,
            "production_process": "print",
        }],
        "operator_splits": [{"line_idx": 0, "qty_completed": completed}],
    }
    if branch_id is not None:
        order["branch_id"] = branch_id
    order.update(extra)
    return order


def test_completeness_mode_b_includes_legacy_50_and_null_branch():
    """Yogyakarta Mode B: historical Accurate '50' + missing branch_id attribute."""
    settings = _mode_b_ygy_settings(include_null=True)
    clauses = mongo_order_branch_clauses(_YGY_UUID, settings)
    # Must fetch UUID, digit 50, and null-branch docs (Python filter authoritative).
    assert {"branch_id": _YGY_UUID} in clauses
    assert {"branch_id": "50"} in clauses
    assert any(
        isinstance(c, dict) and "$and" in c for c in clauses
    ), "null-branch clause required when include_null_branch"

    hist_50 = _print_so("h50", "100089", 500, 200, branch_id="50", created_at=_HIST)
    hist_null = _print_so("hnull", "100089", 500, 100, created_at=_HIST)
    hist_null.pop("branch_id", None)
    new_50 = _print_so("n50", "100089", 999, 0, branch_id="50", created_at=_NEW)
    uuid_so = _print_so("uuid", "100089", 500, 500, branch_id=_YGY_UUID, created_at=_NEW)

    assert order_attributed_to_branch(hist_50, _YGY_UUID, settings) is True
    assert order_attributed_to_branch(hist_null, _YGY_UUID, settings) is True
    assert order_attributed_to_branch(new_50, _YGY_UUID, settings) is False
    assert order_attributed_to_branch(uuid_so, _YGY_UUID, settings) is True

    kept = filter_orders_for_branch(
        [hist_50, hist_null, new_50, uuid_so], _YGY_UUID, settings,
    )
    pack = collect_blank_open_commitment_by_master(
        kept, {}, exclude_master_ids=set(), attributed_deliveries=[],
    )
    # 500-200 + 500-100 + 500-500 = 300+400+0 = 700
    assert pack["sisa_belum_cetak"]["100089"]["status"] == STATUS_READY
    assert pack["sisa_belum_cetak"]["100089"]["qty"] == 700.0


def test_completeness_without_mode_b_legacy_50_excluded():
    """Without Mode B, Accurate '50' / null are not inventably Yogyakarta."""
    settings = sanitize_settings({"enabled": False})
    hist_50 = _print_so("h50", "100032", 10000, 0, branch_id="50", created_at=_HIST)
    assert order_attributed_to_branch(hist_50, _YGY_UUID, settings) is False
    kept = filter_orders_for_branch([hist_50], _YGY_UUID, settings)
    assert kept == []
    pack = collect_blank_open_commitment_by_master(
        kept, {}, exclude_master_ids=set(), attributed_deliveries=[],
    )
    assert "100032" not in pack["sisa_belum_cetak"]
    # Catalog default for absent master after complete walk = READY 0
    slot = resolve_sisa_belum_cetak_catalog_slot(
        pack["sisa_belum_cetak"], "100032", orders_fetch_capped=False,
    )
    assert slot == {"qty": 0.0, "status": STATUS_READY, "reason": None}


def test_completeness_fetch_cap_must_not_invent_ready_zero():
    """Hitting ORDERS_FETCH_LIMIT → UNVERIFIED, not false READY 0."""
    assert ORDERS_FETCH_LIMIT == 20000
    # Simulate: walk only saw completed SO (partial scan); real open SO omitted.
    seen = [_print_so("seen-done", "100397", 1000, 1000, branch_id=_YGY_UUID)]
    pack = collect_blank_open_commitment_by_master(
        seen, {}, exclude_master_ids=set(), attributed_deliveries=[],
    )
    assert pack["sisa_belum_cetak"]["100397"]["qty"] == 0.0  # from visible SO only

    # Incomplete scan: catalog must NOT publish that 0 as READY.
    capped = resolve_sisa_belum_cetak_catalog_slot(
        pack["sisa_belum_cetak"],
        "100397",
        orders_fetch_capped=True,
    )
    assert capped["qty"] is None
    assert capped["status"] == STATUS_UNVERIFIED
    assert capped["reason"] == REASON_ORDERS_FETCH_CAP_REACHED

    # Absent master under cap also fail-closed (not invent READY 0).
    absent = resolve_sisa_belum_cetak_catalog_slot(
        {}, "100089", orders_fetch_capped=True,
    )
    assert absent["status"] == STATUS_UNVERIFIED
    assert absent["qty"] is None

    # Complete scan + absent master → READY 0 is allowed (no print demand seen).
    complete_absent = resolve_sisa_belum_cetak_catalog_slot(
        {}, "100089", orders_fetch_capped=False,
    )
    assert complete_absent == {"qty": 0.0, "status": STATUS_READY, "reason": None}


def test_completeness_omitted_so_ready_zero_vs_cap():
    """SKU aggregation READY 0 only when walk is complete; omitted SO under cap → block."""
    included = [_print_so("a", "100089", 500, 500, branch_id=_YGY_UUID)]
    omitted = _print_so("b", "100089", 500, 0, branch_id=_YGY_UUID)  # not passed to collect
    pack = collect_blank_open_commitment_by_master(
        included, {}, exclude_master_ids=set(), attributed_deliveries=[],
    )
    # Partial visible demand looks like 0 remaining — false if omitted exists.
    assert pack["sisa_belum_cetak"]["100089"]["qty"] == 0.0
    # Cap flag is how catalog refuses to publish that false zero.
    assert resolve_sisa_belum_cetak_catalog_slot(
        pack["sisa_belum_cetak"], "100089", orders_fetch_capped=True,
    )["status"] == STATUS_UNVERIFIED
    # When scan complete and omitted truly not in branch query, READY 0 stands.
    assert resolve_sisa_belum_cetak_catalog_slot(
        pack["sisa_belum_cetak"], "100089", orders_fetch_capped=False,
    )["qty"] == 0.0
    del omitted  # fixture documents the omitted demand; not loaded into walk


def test_completeness_no_shipment_or_stage_inference():
    """Sisa ignores shipment qty and never reads current_stage."""
    orders = [{
        "id": "so-ship-stage",
        "cancelled": False,
        "current_stage": "selesai",  # must not invent completion
        "line_items": [{
            "line_id": "ln-p",
            "master_item_id": "100089",
            "quantity": 500,
            "production_process": "print",
        }],
        "operator_splits": [{"line_idx": 0, "qty_completed": 100}],
        "shipments": [{"qty": 400}],
    }]
    deliveries = [{
        "order_id": "so-ship-stage",
        "master_item_id": "100089",
        "line_id": "ln-p",
        "line_idx": 0,
        "qty": 400,
    }]
    pack = collect_blank_open_commitment_by_master(
        orders, {}, exclude_master_ids=set(), attributed_deliveries=deliveries,
    )
    assert pack["sisa_belum_cetak"]["100089"]["qty"] == 400.0  # 500−100, not −ship
    assert pack["sisa_belum_cetak"]["100089"]["status"] == STATUS_READY


def test_completeness_blank_gate_unchanged_when_sisa_uses_ops():
    """Blank commitment still fail-closed on order-wide gate; sisa uses ops."""
    orders = [{
        "id": "so-blank-gate",
        "cancelled": False,
        "line_items": [
            {
                "line_id": "ln-p",
                "master_item_id": "100397",
                "quantity": 1000,
                "production_process": "print",
            },
            {
                "line_id": "ln-svc",
                "master_item_id": "m-svc-orphan",
                "quantity": 1000,
                "production_process": "print",
                "item_type": "SERVICE",
            },
        ],
        "operator_splits": [{"line_idx": 0, "qty_completed": 1000}],
    }]
    pack = collect_blank_open_commitment_by_master(
        orders, {}, exclude_master_ids=SVC_EXCLUDE, attributed_deliveries=[],
    )
    assert pack["sisa_belum_cetak"]["100397"]["qty"] == 0.0
    assert pack["sisa_belum_cetak"]["100397"]["status"] == STATUS_READY
    assert pack["blank_open_commitment"]["100397"]["status"] == STATUS_UNVERIFIED
    assert pack["blank_open_commitment"]["100397"]["qty"] is None
