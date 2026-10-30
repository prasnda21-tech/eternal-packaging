"""Focused regression: demand Qty Pesanan / Belum Terkirim vs branch attribution.

Locks the UAT recovery contract:
- Print order with valid branch_id → contributes demand (e.g. qty_pesanan=120).
- Print order without branch → fail-closed exclusion (never invent onto a cabang).
- Partial operator completion moves pesanan → belum.
- Proven SJ Terkirim (partial) reduces belum deterministically.
- Unknown delivery provenance stays BELUM_TERVERIFIKASI (null belum, not fake 0).

Does NOT enable Mode B null-branch auto-assign.
Does NOT write preview/production data.
"""
from __future__ import annotations

from services.physical_stock import (
    _orders_for_branch,
    aggregate_operational_orders,
    operational_availability,
)
from services.stock_compatibility_read import (
    BELUM_STATUS_READY,
    BELUM_STATUS_UNVERIFIED,
    aggregate_legacy_delivered_qty,
    compatibility_row,
)

BRANCH_YGY = "uat-branch-ygy"
ITEM = "uat-item-karton"


def _print_order(
    order_id: str,
    *,
    quantity: float = 120,
    completed: float = 0,
    branch_id: str | None = None,
    shipments: list | None = None,
):
    doc = {
        "id": order_id,
        "cancelled": False,
        "line_items": [{
            "master_item_id": ITEM,
            "quantity": quantity,
            "production_process": "print",
            "name": "Karton UAT",
        }],
        "operator_splits": (
            [{"line_idx": 0, "qty_completed": completed}] if completed else []
        ),
    }
    if branch_id:
        doc["branch_id"] = branch_id
    if shipments is not None:
        doc["shipments"] = shipments
    return doc


def test_print_order_with_valid_branch_qty_pesanan_120():
    """UAT recovery positive path: attributed print, not completed → pesanan 120, belum 0."""
    orders = [_print_order("so-attributed", quantity=120, completed=0, branch_id=BRANCH_YGY)]
    scoped = _orders_for_branch(orders, BRANCH_YGY, legacy_settings={})
    assert len(scoped) == 1
    progress = aggregate_operational_orders(scoped, {})
    assert progress[ITEM]["ordered_qty"] == 120.0
    assert progress[ITEM]["completed_qty"] == 0.0
    buckets = operational_availability(
        physical_qty=970,
        ordered_qty=progress[ITEM]["ordered_qty"],
        completed_qty=progress[ITEM]["completed_qty"],
        r2_delivered_qty=0,
    )
    assert buckets["qty_pesanan"] == 120.0
    assert buckets["qty_belum_terkirim"] == 0.0
    # Demand is independent of physical book — stamp/attr does not invent stock.
    assert buckets["physical_qty"] == 970.0
    assert buckets["qty_tersedia"] == 850.0


def test_order_without_branch_fail_closed_exclusion():
    """Missing branch_id/cabang_id → excluded; never invent demand onto target cabang.

    Catalog zeros after exclusion are fail-closed empty demand for that branch —
    not a claim that the orphan order was counted as complete demand.
    """
    orphan = _print_order("so-orphan", quantity=120, completed=0, branch_id=None)
    assert "branch_id" not in orphan and "cabang_id" not in orphan

    scoped = _orders_for_branch([orphan], BRANCH_YGY, legacy_settings={})
    assert scoped == []

    progress = aggregate_operational_orders(scoped, {})
    assert ITEM not in progress

    buckets = operational_availability(
        physical_qty=970, ordered_qty=0, completed_qty=0, r2_delivered_qty=0,
    )
    assert buckets["qty_pesanan"] == 0.0
    assert buckets["qty_belum_terkirim"] == 0.0

    # Mode B null-branch OFF (empty settings): still excluded — no auto-assign.
    still = _orders_for_branch([orphan], BRANCH_YGY, legacy_settings={"enabled": False})
    assert still == []


def test_operator_partial_completion_moves_pesanan_to_belum():
    orders = [
        _print_order(
            "so-partial",
            quantity=120,
            completed=40,
            branch_id=BRANCH_YGY,
        )
    ]
    scoped = _orders_for_branch(orders, BRANCH_YGY, legacy_settings={})
    progress = aggregate_operational_orders(scoped, {})[ITEM]
    buckets = operational_availability(
        physical_qty=1000,
        ordered_qty=progress["ordered_qty"],
        completed_qty=progress["completed_qty"],
        r2_delivered_qty=0,
    )
    assert progress["ordered_qty"] == 120.0
    assert progress["completed_qty"] == 40.0
    assert buckets["qty_pesanan"] == 80.0
    assert buckets["qty_belum_terkirim"] == 40.0


def test_sj_terkirim_partial_reduces_belum():
    orders = [
        _print_order(
            "so-sj-partial",
            quantity=120,
            completed=80,
            branch_id=BRANCH_YGY,
            shipments=[{
                "id": "ship-partial",
                "delivery_status": "Terkirim",
                "delivered_at": "2026-10-20T00:00:00+00:00",
                "total_qty": 30,
                "lines": [{"line_idx": 0, "qty": 30}],
            }],
        )
    ]
    scoped = _orders_for_branch(orders, BRANCH_YGY, legacy_settings={})
    delivered, prov = aggregate_legacy_delivered_qty(scoped, [])
    assert delivered[ITEM] == 30.0
    assert prov[ITEM] == "confirmed_shipment"
    progress = aggregate_operational_orders(scoped, {})[ITEM]
    buckets = operational_availability(
        physical_qty=1000,
        ordered_qty=progress["ordered_qty"],
        completed_qty=progress["completed_qty"],
        r2_delivered_qty=delivered[ITEM],
    )
    assert buckets["qty_pesanan"] == 40.0       # 120 - 80
    assert buckets["qty_belum_terkirim"] == 50.0  # 80 - 30


def test_unknown_delivery_provenance_stays_unverified():
    """Ambiguous multi-SKU shipment → BELUM_TERVERIFIKASI; never invent belum=0."""
    orders = [{
        "id": "so-ambig",
        "cancelled": False,
        "branch_id": BRANCH_YGY,
        "line_items": [
            {"master_item_id": "m1", "quantity": 100, "production_process": "print", "name": "A"},
            {"master_item_id": "m2", "quantity": 100, "production_process": "print", "name": "B"},
        ],
        "operator_splits": [
            {"line_idx": 0, "qty_completed": 80},
            {"line_idx": 1, "qty_completed": 80},
        ],
        "shipments": [{
            "id": "ship-ambig",
            "delivery_status": "Terkirim",
            "delivered_at": "2026-10-20T00:00:00+00:00",
            "total_qty": 40,
            "lines": [{"qty": 40}],  # no line_idx → unsafe
        }],
    }]
    scoped = _orders_for_branch(orders, BRANCH_YGY, legacy_settings={})
    delivered, prov = aggregate_legacy_delivered_qty(scoped, [])
    assert prov["m1"] == "unproven"
    assert prov["m2"] == "unproven"
    assert delivered.get("m1", 0) == 0

    progress = aggregate_operational_orders(scoped, {})["m1"]
    assert progress["completed_qty"] > 0
    row = compatibility_row(
        master={"id": "m1", "item_code": "M1", "item_name": "A"},
        physical_qty=500,
        ordered_qty=progress["ordered_qty"],
        completed_qty=progress["completed_qty"],
        delivered_qty=0,
        belum_status=BELUM_STATUS_UNVERIFIED,
        updated_at=None,
        base_unit="PCS",
    )
    assert row["belum_terkirim_status"] == BELUM_STATUS_UNVERIFIED
    assert row["qty_belum_terkirim"] is None
    assert row["qty_tersedia_valid"] is False
    assert row["qty_pesanan"] == 20.0  # 100 - 80
    # Fail-closed tersedia excludes only pesanan — never fabricates belum=0.
    assert row["qty_tersedia"] == 500 - row["qty_pesanan"]


def test_sj_selesai_alone_is_not_delivery_proof():
    """sales_deliveries SELESAI without proven shipment must not reduce Belum."""
    orders = [
        _print_order(
            "so-sj-draft",
            quantity=100,
            completed=80,
            branch_id=BRANCH_YGY,
        )
    ]
    deliveries = [{
        "id": "sj-1",
        "po_id": "so-sj-draft",
        "status": "SELESAI",
        "is_cancelled": False,
        "line_items": [{"line_idx": 0, "master_item_id": ITEM, "qty": 80}],
    }]
    scoped = _orders_for_branch(orders, BRANCH_YGY, legacy_settings={})
    delivered, prov = aggregate_legacy_delivered_qty(scoped, deliveries)
    assert delivered.get(ITEM, 0) == 0
    assert prov.get(ITEM) is None
    progress = aggregate_operational_orders(scoped, {})[ITEM]
    buckets = operational_availability(
        physical_qty=1000,
        ordered_qty=progress["ordered_qty"],
        completed_qty=progress["completed_qty"],
        r2_delivered_qty=delivered.get(ITEM, 0),
    )
    assert buckets["qty_belum_terkirim"] == 80.0
    row = compatibility_row(
        master={"id": ITEM, "item_code": "K", "item_name": "Karton"},
        physical_qty=1000,
        ordered_qty=progress["ordered_qty"],
        completed_qty=progress["completed_qty"],
        delivered_qty=0,
        belum_status=BELUM_STATUS_READY,
        updated_at=None,
        base_unit="PCS",
    )
    assert row["belum_terkirim_status"] == BELUM_STATUS_READY
    assert row["qty_belum_terkirim"] == 80.0


def test_cancelled_order_excluded_from_demand():
    active = _print_order("so-live", quantity=50, completed=0, branch_id=BRANCH_YGY)
    cancelled = _print_order("so-canc", quantity=90, completed=0, branch_id=BRANCH_YGY)
    cancelled["cancelled"] = True
    scoped = _orders_for_branch([active, cancelled], BRANCH_YGY, legacy_settings={})
    progress = aggregate_operational_orders(scoped, {})
    assert progress[ITEM]["ordered_qty"] == 50.0
    buckets = operational_availability(
        physical_qty=200, ordered_qty=50, completed_qty=0, r2_delivered_qty=0,
    )
    assert buckets["qty_pesanan"] == 50.0
    assert buckets["qty_tersedia"] == 150.0


def test_buffer_stock_flag_still_counts_as_demand():
    """buffer_stock is a schedule label — still customer commitment for Qty Pesanan."""
    order = _print_order("so-buf", quantity=40, completed=10, branch_id=BRANCH_YGY)
    order["buffer_stock"] = True
    scoped = _orders_for_branch([order], BRANCH_YGY, legacy_settings={})
    progress = aggregate_operational_orders(scoped, {})[ITEM]
    buckets = operational_availability(
        physical_qty=100,
        ordered_qty=progress["ordered_qty"],
        completed_qty=progress["completed_qty"],
        r2_delivered_qty=0,
    )
    assert buckets["qty_pesanan"] == 30.0
    assert buckets["qty_belum_terkirim"] == 10.0
    assert buckets["qty_tersedia"] == 60.0


def test_service_addon_sj_line_remaps_to_parent_physical():
    """SJ attributed to SERVICE Add On must reduce parent Belum — not double-count SERVICE."""
    parent = "inv-parent"
    svc = "svc-sablon"
    orders = [{
        "id": "so-addon-sj",
        "cancelled": False,
        "branch_id": BRANCH_YGY,
        "line_items": [
            {
                "line_id": "ln-main",
                "master_item_id": parent,
                "quantity": 100,
                "production_process": "print",
                "name": "Karton",
            },
            {
                "line_id": "ln-svc",
                "master_item_id": svc,
                "parent_line_id": "ln-main",
                "quantity": 100,
                "production_process": "print",
                "name": "Sablon",
            },
        ],
        "operator_splits": [
            {"line_idx": 0, "qty_completed": 100},
            {"line_idx": 1, "qty_completed": 100},
        ],
        "shipments": [{
            "id": "ship-svc",
            "delivery_status": "Terkirim",
            "delivered_at": "2026-10-20T00:00:00+00:00",
            "total_qty": 40,
            "lines": [{"line_idx": 1, "qty": 40}],
        }],
    }]
    scoped = _orders_for_branch(orders, BRANCH_YGY, legacy_settings={})
    progress = aggregate_operational_orders(scoped, {}, exclude_master_ids={svc})
    delivered, prov = aggregate_legacy_delivered_qty(
        scoped, [], process_master_ids={svc},
    )
    assert parent in progress
    assert svc not in progress
    assert delivered.get(parent) == 40.0
    assert svc not in delivered
    assert prov.get(parent) == "confirmed_shipment"
    buckets = operational_availability(
        physical_qty=500,
        ordered_qty=progress[parent]["ordered_qty"],
        completed_qty=progress[parent]["completed_qty"],
        r2_delivered_qty=delivered[parent],
    )
    # BEFORE bug: pesanan=0, belum=100 (SERVICE ate the SJ). AFTER: belum=60.
    assert buckets["qty_pesanan"] == 0.0
    assert buckets["qty_belum_terkirim"] == 60.0
    assert buckets["qty_tersedia"] == 440.0


def test_service_addon_qty_delivered_fallback_blocked():
    """Header qty_delivered must not credit parent when SERVICE is on the PO.

    Mixed jasa+inventory header totals (e.g. mould 100192) must not inflate
    physical delivered via fallback — require line-attributed SJ proof instead.
    """
    parent = "inv-parent"
    svc = "svc-sablon"
    orders = [{
        "id": "so-addon-qd",
        "cancelled": False,
        "branch_id": BRANCH_YGY,
        "qty_delivered": 25,
        "line_items": [
            {
                "line_id": "ln-main",
                "master_item_id": parent,
                "quantity": 100,
                "production_process": "print",
            },
            {
                "line_id": "ln-svc",
                "master_item_id": svc,
                "parent_line_id": "ln-main",
                "quantity": 100,
                "production_process": "print",
            },
        ],
        "operator_splits": [
            {"line_idx": 0, "qty_completed": 100},
            {"line_idx": 1, "qty_completed": 100},
        ],
    }]
    scoped = _orders_for_branch(orders, BRANCH_YGY, legacy_settings={})
    progress = aggregate_operational_orders(scoped, {}, exclude_master_ids={svc})[parent]
    delivered, prov = aggregate_legacy_delivered_qty(
        scoped, [], process_master_ids={svc},
    )
    assert delivered.get(parent, 0) == 0
    assert prov.get(parent) != "order_qty_delivered"
    assert progress["completed_qty"] == 100.0
    buckets = operational_availability(
        physical_qty=1000,
        ordered_qty=progress["ordered_qty"],
        completed_qty=progress["completed_qty"],
        r2_delivered_qty=delivered.get(parent, 0),
    )
    assert buckets["qty_pesanan"] == 0.0
    assert buckets["qty_belum_terkirim"] == 100.0  # siap intact; no invented delivery


def test_unlinked_service_sj_skips_line_not_blast_sibling():
    """SERVICE SJ without parent: skip line only; do not mark parent delivery unproven.

    completion_unproven may still force Belum UNVERIFIED until parent is linked
    (data repair) — but delivery provenance must not false-blast the master.
    """
    parent = "inv-parent"
    svc = "svc-orphan"
    orders = [{
        "id": "so-orphan-svc",
        "cancelled": False,
        "branch_id": BRANCH_YGY,
        "line_items": [
            {"master_item_id": parent, "quantity": 100, "production_process": "print"},
            {"master_item_id": svc, "quantity": 100, "production_process": "print"},
        ],
        "operator_splits": [
            {"line_idx": 0, "qty_completed": 100},
            {"line_idx": 1, "qty_completed": 100},
        ],
        "shipments": [{
            "id": "ship-orphan",
            "delivery_status": "Terkirim",
            "delivered_at": "2026-10-20T00:00:00+00:00",
            "total_qty": 40,
            "lines": [{"line_idx": 1, "qty": 40}],
        }],
    }]
    scoped = _orders_for_branch(orders, BRANCH_YGY, legacy_settings={})
    progress = aggregate_operational_orders(scoped, {}, exclude_master_ids={svc})[parent]
    delivered, prov = aggregate_legacy_delivered_qty(
        scoped, [], process_master_ids={svc},
    )
    assert delivered.get(parent, 0) == 0
    assert prov.get(parent) != "unproven"
    assert progress["completion_unproven"] is True
    row = compatibility_row(
        master={"id": parent, "item_code": "P", "item_name": "Parent"},
        physical_qty=500,
        ordered_qty=progress["ordered_qty"],
        completed_qty=progress["completed_qty"],
        delivered_qty=0,
        belum_status=BELUM_STATUS_UNVERIFIED,
        updated_at=None,
        base_unit="PCS",
    )
    assert row["qty_belum_terkirim"] is None
    assert row["qty_tersedia_valid"] is False
    assert row["qty_tersedia"] == 500 - row["qty_pesanan"]


def test_formula_tersedia_with_valid_stock_and_demand():
    """Approved ERP formula: Tersedia = Fisik − Pesanan − Belum (when valid)."""
    buckets = operational_availability(
        physical_qty=970,
        ordered_qty=120,
        completed_qty=40,
        r2_delivered_qty=10,
    )
    assert buckets["qty_pesanan"] == 80.0
    assert buckets["qty_belum_terkirim"] == 30.0
    assert buckets["qty_tersedia"] == 860.0  # 970 - 80 - 30


def test_alt3_phys_plus_service_same_sj_counts_native_only():
    """Phys 1000 + SERVICE 1000 on one SJ → delivered parent 1000 (not 2000)."""
    parent = "inv-parent-alt3"
    svc = "svc-sablon-alt3"
    orders = [{
        "id": "so-alt3-single",
        "cancelled": False,
        "branch_id": BRANCH_YGY,
        "line_items": [
            {"line_id": "ln-main", "master_item_id": parent, "quantity": 1000,
             "production_process": "print"},
            {"line_id": "ln-svc", "master_item_id": svc, "parent_line_id": "ln-main",
             "quantity": 1000, "production_process": "print", "item_type": "SERVICE"},
        ],
        "shipments": [{
            "id": "ship-alt3-1",
            "delivery_status": "Terkirim",
            "delivered_at": "2026-10-20T00:00:00+00:00",
            "total_qty": 2000,
            "lines": [
                {"line_idx": 0, "qty": 1000},
                {"line_idx": 1, "qty": 1000},
            ],
        }],
        "delivery_confirmations": [{
            "id": "conf-alt3-1",
            "shipment_id": "ship-alt3-1",
            "qty": 2000,
            "items": [
                {"line_idx": 0, "qty": 1000},
                {"line_idx": 1, "qty": 1000},
            ],
        }],
    }]
    delivered, prov = aggregate_legacy_delivered_qty(
        orders, [], process_master_ids={svc},
    )
    assert delivered.get(parent) == 1000.0
    assert svc not in delivered
    assert prov.get(parent) == "confirmed_shipment"


def test_alt3_phys_plus_two_service_colors_no_multiply():
    """Phys 1000 + two SERVICE 1000 → delivered 1000."""
    parent = "inv-parent-alt3b"
    svc = "svc-sablon-alt3b"
    orders = [{
        "id": "so-alt3-dual",
        "cancelled": False,
        "line_items": [
            {"line_id": "ln-main", "master_item_id": parent, "quantity": 1000,
             "production_process": "print"},
            {"line_id": "ln-s1", "master_item_id": svc, "parent_line_id": "ln-main",
             "quantity": 1000, "production_process": "print", "item_type": "SERVICE"},
            {"line_id": "ln-s2", "master_item_id": svc, "parent_line_id": "ln-main",
             "quantity": 1000, "production_process": "print", "item_type": "SERVICE"},
        ],
        "shipments": [{
            "id": "ship-alt3-dual",
            "delivery_status": "Terkirim",
            "delivered_at": "2026-10-20T00:00:00+00:00",
            "total_qty": 3000,
            "lines": [
                {"line_idx": 0, "qty": 1000},
                {"line_idx": 1, "qty": 1000},
                {"line_idx": 2, "qty": 1000},
            ],
        }],
        "delivery_confirmations": [{
            "id": "conf-alt3-dual",
            "shipment_id": "ship-alt3-dual",
            "items": [
                {"line_idx": 0, "qty": 1000},
                {"line_idx": 1, "qty": 1000},
                {"line_idx": 2, "qty": 1000},
            ],
        }],
    }]
    delivered, _ = aggregate_legacy_delivered_qty(
        orders, [], process_master_ids={svc},
    )
    assert delivered.get(parent) == 1000.0


def test_alt3_pf_true_service_no_multiplication():
    """PF=true printing SERVICE on same SJ as phys must not multiply delivery."""
    parent = "inv-parent-pf"
    svc = "svc-pf"
    orders = [{
        "id": "so-alt3-pf",
        "cancelled": False,
        "line_items": [
            {"line_id": "ln-main", "master_item_id": parent, "quantity": 1000,
             "production_process": "print"},
            {"line_id": "ln-s1", "master_item_id": svc, "parent_line_id": "ln-main",
             "quantity": 1000, "production_process": "print", "item_type": "SERVICE",
             "physical_fulfillment": True},
            {"line_id": "ln-s2", "master_item_id": svc, "parent_line_id": "ln-main",
             "quantity": 1000, "production_process": "print", "item_type": "SERVICE",
             "physical_fulfillment": True},
        ],
        "shipments": [{
            "id": "ship-alt3-pf",
            "delivery_status": "Terkirim",
            "delivered_at": "2026-10-20T00:00:00+00:00",
            "total_qty": 3000,
            "lines": [
                {"line_idx": 0, "qty": 1000},
                {"line_idx": 1, "qty": 1000},
                {"line_idx": 2, "qty": 1000},
            ],
        }],
        "delivery_confirmations": [{
            "id": "conf-alt3-pf",
            "shipment_id": "ship-alt3-pf",
            "items": [
                {"line_idx": 0, "qty": 1000},
                {"line_idx": 1, "qty": 1000},
                {"line_idx": 2, "qty": 1000},
            ],
        }],
    }]
    delivered, _ = aggregate_legacy_delivered_qty(
        orders, [], process_master_ids={svc},
    )
    assert delivered.get(parent) == 1000.0
    assert orders[0]["line_items"][1]["physical_fulfillment"] is True
    assert orders[0]["line_items"][2]["physical_fulfillment"] is True


def test_alt3_partial_shipments_sum_across_identities():
    """Distinct partial SJ identities sum; colors on each SJ do not multiply."""
    parent = "inv-parent-partial"
    svc = "svc-partial"
    orders = [{
        "id": "so-alt3-partial",
        "cancelled": False,
        "line_items": [
            {"line_id": "ln-main", "master_item_id": parent, "quantity": 1000,
             "production_process": "print"},
            {"line_id": "ln-svc", "master_item_id": svc, "parent_line_id": "ln-main",
             "quantity": 1000, "production_process": "print", "item_type": "SERVICE"},
        ],
        "shipments": [
            {
                "id": "ship-p1",
                "delivery_status": "Terkirim",
                "delivered_at": "2026-10-20T00:00:00+00:00",
                "total_qty": 800,
                "lines": [
                    {"line_idx": 0, "qty": 400},
                    {"line_idx": 1, "qty": 400},
                ],
            },
            {
                "id": "ship-p2",
                "delivery_status": "Terkirim",
                "delivered_at": "2026-10-21T00:00:00+00:00",
                "total_qty": 600,
                "lines": [
                    {"line_idx": 0, "qty": 300},
                    {"line_idx": 1, "qty": 300},
                ],
            },
        ],
        "delivery_confirmations": [
            {
                "id": "conf-p1",
                "shipment_id": "ship-p1",
                "items": [
                    {"line_idx": 0, "qty": 400},
                    {"line_idx": 1, "qty": 400},
                ],
            },
            {
                "id": "conf-p2",
                "shipment_id": "ship-p2",
                "items": [
                    {"line_idx": 0, "qty": 300},
                    {"line_idx": 1, "qty": 300},
                ],
            },
        ],
    }]
    delivered, prov = aggregate_legacy_delivered_qty(
        orders, [], process_master_ids={svc},
    )
    assert delivered.get(parent) == 700.0  # 400 + 300 native across SJs
    assert prov.get(parent) == "confirmed_shipment"


def test_alt3_duplicate_confirmation_identity_not_double_counted():
    """Repeated confirmation for the same shipment identity must not double-count."""
    parent = "inv-parent-dedupe"
    svc = "svc-dedupe"
    orders = [{
        "id": "so-alt3-dedupe",
        "cancelled": False,
        "line_items": [
            {"line_id": "ln-main", "master_item_id": parent, "quantity": 1000,
             "production_process": "print"},
            {"line_id": "ln-svc", "master_item_id": svc, "parent_line_id": "ln-main",
             "quantity": 1000, "production_process": "print", "item_type": "SERVICE"},
        ],
        "shipments": [
            {
                "id": "ship-dedupe",
                "delivery_status": "Terkirim",
                "delivered_at": "2026-10-20T00:00:00+00:00",
                "total_qty": 2000,
                "lines": [
                    {"line_idx": 0, "qty": 1000},
                    {"line_idx": 1, "qty": 1000},
                ],
            },
            {
                # Same identity tokens as ship-dedupe → skipped by seen_identities.
                "id": "ship-dedupe",
                "delivery_status": "Terkirim",
                "delivered_at": "2026-10-20T00:00:00+00:00",
                "total_qty": 2000,
                "lines": [
                    {"line_idx": 0, "qty": 1000},
                    {"line_idx": 1, "qty": 1000},
                ],
            },
        ],
        "delivery_confirmations": [{
            "id": "conf-dedupe",
            "shipment_id": "ship-dedupe",
            "items": [
                {"line_idx": 0, "qty": 1000},
                {"line_idx": 1, "qty": 1000},
            ],
        }],
    }]
    delivered, _ = aggregate_legacy_delivered_qty(
        orders, [], process_master_ids={svc},
    )
    assert delivered.get(parent) == 1000.0


def test_alt3_service_only_colors_use_max_not_sum():
    """SERVICE-only SJ with two colors → max(process qtys), not sum."""
    parent = "inv-parent-svc-only"
    svc = "svc-only-colors"
    orders = [{
        "id": "so-alt3-svc-only",
        "cancelled": False,
        "line_items": [
            {"line_id": "ln-main", "master_item_id": parent, "quantity": 1000,
             "production_process": "print"},
            {"line_id": "ln-s1", "master_item_id": svc, "parent_line_id": "ln-main",
             "quantity": 1000, "production_process": "print", "item_type": "SERVICE"},
            {"line_id": "ln-s2", "master_item_id": svc, "parent_line_id": "ln-main",
             "quantity": 1000, "production_process": "print", "item_type": "SERVICE"},
        ],
        "shipments": [{
            "id": "ship-svc-only",
            "delivery_status": "Terkirim",
            "delivered_at": "2026-10-20T00:00:00+00:00",
            "total_qty": 2000,
            "lines": [
                {"line_idx": 1, "qty": 1000},
                {"line_idx": 2, "qty": 800},
            ],
        }],
        "delivery_confirmations": [{
            "id": "conf-svc-only",
            "shipment_id": "ship-svc-only",
            "items": [
                {"line_idx": 1, "qty": 1000},
                {"line_idx": 2, "qty": 800},
            ],
        }],
    }]
    delivered, _ = aggregate_legacy_delivered_qty(
        orders, [], process_master_ids={svc},
    )
    assert delivered.get(parent) == 1000.0  # max(1000, 800), not 1800
