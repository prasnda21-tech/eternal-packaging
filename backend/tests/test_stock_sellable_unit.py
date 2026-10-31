"""ATP / Stock Dapat Dijual helpers — warehouse vs branch commitment."""
from services.stock_sellable import (
    collect_allocation_commitment,
    stock_dapat_dijual_branch,
    stock_dapat_dijual_warehouse,
)


def test_warehouse_atp_uses_remaining_allocation_only():
    slot = stock_dapat_dijual_warehouse(stock_book=100, remaining_allocation=30)
    assert slot["qty"] == 70
    assert slot["status"] == "READY"
    assert slot["provisional"] is False


def test_incomplete_warehouse_not_safe():
    slot = stock_dapat_dijual_warehouse(
        stock_book=100, remaining_allocation=10, incomplete=True,
    )
    assert slot["qty"] == 90
    assert slot["status"] == "INCOMPLETE"
    assert slot["provisional"] is True


def test_branch_includes_full_open_commitment():
    slot = stock_dapat_dijual_branch(
        stock_book=200,
        open_so_commitment=80,
    )
    assert slot["qty"] == 120


def test_collect_commitment_separates_allocated_and_unallocated():
    orders = [
        {
            "id": "o1",
            "cancelled": False,
            "operational_branch_id": "b1",
            "line_items": [
                {
                    "line_id": "l1",
                    "master_item_id": "m1",
                    "quantity": 10,
                    "item_type": "INVENTORY",
                    "stock_allocations": [
                        {"warehouse_id": "w1", "qty": 4},
                        {"warehouse_id": "w2", "qty": 3},
                    ],
                }
            ],
        }
    ]
    deliveries = [
        {"order_id": "o1", "line_id": "l1", "warehouse_id": "w1", "qty": 2},
    ]
    snap = collect_allocation_commitment(orders, deliveries)
    # ordered 10, delivered 2 → open commit 8
    assert snap["open_commit_by_master"]["m1"] == 8
    # remaining alloc: w1 4-2=2, w2 3 → sum 5; unalloc open = 8-5 = 3
    rem = snap["remaining_by_master_warehouse"]["m1"]
    assert rem["w1"] == 2
    assert rem["w2"] == 3
    assert snap["unallocated_open_by_master"]["m1"] == 3
    assert "m1" in snap["incomplete_masters"]


def test_empty_allocation_keeps_full_branch_commitment_unallocated():
    orders = [{
        "id": "o2",
        "line_items": [{
            "line_id": "l2",
            "master_item_id": "m9",
            "quantity": 12,
            "item_type": "INVENTORY",
            "stock_allocations": [],
        }],
    }]
    snap = collect_allocation_commitment(orders, [])
    assert snap["open_commit_by_master"]["m9"] == 12
    assert snap["unallocated_open_by_master"]["m9"] == 12
    assert "m9" in snap["incomplete_masters"]
