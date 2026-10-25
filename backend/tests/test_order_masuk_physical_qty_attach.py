"""Order Masuk physical qty: packing_required_total excludes process-only SERVICE."""
from __future__ import annotations

import asyncio
from pathlib import Path
import os
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("MONGO_URL", "mongodb://localhost:27017")
os.environ.setdefault("DB_NAME", "test_database")

from quantity_rules import packing_required_total  # noqa: E402


def _ider_dual_service():
    return {
        "total_quantity": 3000,
        "line_items": [
            {"quantity": 1000, "item_type": "INVENTORY", "production_process": "print", "name": "Cup"},
            {"quantity": 1000, "item_type": "SERVICE", "production_process": "print", "name": "Sablon A"},
            {
                "quantity": 1000,
                "item_type": "SERVICE",
                "production_process": "print",
                "physical_fulfillment": False,
                "name": "Sablon B",
            },
        ],
    }


def test_packing_required_physical_only():
    order = {
        "total_quantity": 500,
        "line_items": [
            {"quantity": 500, "item_type": "INVENTORY", "production_process": "print"},
        ],
    }
    assert packing_required_total(order) == 500


def test_packing_required_ider_dual_service_is_1000():
    assert packing_required_total(_ider_dual_service()) == 1000


def test_packing_required_all_service_is_zero():
    order = {
        "total_quantity": 2000,
        "line_items": [
            {"quantity": 1000, "item_type": "SERVICE", "production_process": "print"},
            {"quantity": 1000, "item_type": "SERVICE", "production_process": "print"},
        ],
    }
    assert packing_required_total(order) == 0


def test_legacy_missing_item_type_counts_as_physical_until_stamped():
    """Without item_type, SERVICE cannot be recognized — residual risk documented."""
    order = {
        "total_quantity": 3000,
        "line_items": [
            {"quantity": 1000, "production_process": "print", "master_item_id": "m1"},
            {"quantity": 1000, "production_process": "print", "master_item_id": "m2"},
            {"quantity": 1000, "production_process": "print", "master_item_id": "m3"},
        ],
    }
    assert packing_required_total(order) == 3000


def test_attach_list_packing_required_totals_batch_no_nplus1(monkeypatch):
    import server

    calls = {"n": 0}

    async def fake_load(ids):
        calls["n"] += 1
        return (
            {"m-inv": "INVENTORY", "m-svc": "SERVICE"},
            {},
        )

    monkeypatch.setattr(server, "_load_stamp_master_maps", fake_load)

    orders = [
        {
            "total_quantity": 3000,
            "line_items": [
                {"quantity": 1000, "master_item_id": "m-inv", "production_process": "print"},
                {"quantity": 1000, "master_item_id": "m-svc", "production_process": "print"},
                {"quantity": 1000, "master_item_id": "m-svc", "production_process": "print"},
            ],
        },
        {
            "total_quantity": 200,
            "line_items": [
                {"quantity": 200, "master_item_id": "m-inv", "production_process": "print"},
            ],
        },
    ]
    asyncio.run(server._attach_list_packing_required_totals(orders))
    assert calls["n"] == 1
    assert orders[0]["packing_required_total"] == 1000
    assert orders[1]["packing_required_total"] == 200
