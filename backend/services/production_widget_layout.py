"""Production dashboard layout. Same store and CAS as Keuangan, separate key."""
from __future__ import annotations

from typing import Any, Iterable, Optional

from services.finance_widget_layout import get_finance_layout, reset_finance_layout, save_finance_layout

PERM = "dashboard.production.view"
OPERATOR_PERM = "dashboard.production.operator_analytics"
BRANCH_PERM = "dashboard.production.branch_output"
DELIVERY_QTY_PERM = (
    "delivery.schedule.view",
    "schedule.view",
    "nav.delivery_schedule",
)

READY_IDS = (
    "perlu_produksi",
    "po_urgent",
    "po_hold",
    "po_aktif",
    "output_hari_ini",
    "tren_output",
    "sisa_qty",
    "deadline",
    "distribusi_tahap",
    "approval",
    "output_operator",
    "beban_operator",
    "prioritas",
)

READY = {widget_id: PERM for widget_id in READY_IDS}
READY.update({
    "output_hari_ini": PERM,
    "tren_output": PERM,
    "distribusi_tahap": PERM,
    "output_operator": OPERATOR_PERM,
    "beban_operator": OPERATOR_PERM,
    # Placeable, but not part of the default order.
    "kesiapan": PERM,
    "target_produksi": PERM,
    "po_terlambat": PERM,
    "target_realisasi": PERM,
    "aging": PERM,
    "ketepatan": PERM,
    "bottleneck": PERM,
    "komposisi": PERM,
    "output_cabang": BRANCH_PERM,
    "delivery_qty": DELIVERY_QTY_PERM,
})

# min_w, max_w, min_h, max_h, default_w, default_h
SIZES = {
    "perlu_produksi": (3, 6, 2, 4, 3, 2),
    "po_urgent": (3, 6, 2, 4, 3, 2),
    "po_hold": (3, 6, 2, 4, 3, 2),
    "po_aktif": (3, 6, 2, 4, 3, 2),
    "sisa_qty": (4, 8, 2, 6, 4, 3),
    "deadline": (4, 8, 2, 6, 4, 3),
    "approval": (3, 6, 2, 5, 4, 3),
    "prioritas": (6, 12, 3, 8, 12, 4),
    "output_hari_ini": (3, 6, 2, 5, 4, 3),
    "tren_output": (4, 12, 3, 8, 4, 3),
    "distribusi_tahap": (4, 12, 3, 8, 4, 3),
    "output_operator": (4, 12, 3, 8, 6, 3),
    "beban_operator": (4, 12, 3, 8, 6, 3),
    "kesiapan": (6, 12, 3, 6, 8, 4),
    "target_produksi": (4, 12, 3, 8, 6, 4),
    "po_terlambat": (4, 8, 3, 6, 4, 3),
    "target_realisasi": (4, 12, 3, 8, 6, 4),
    "aging": (4, 12, 3, 6, 6, 3),
    "ketepatan": (4, 12, 3, 6, 6, 3),
    "bottleneck": (6, 12, 3, 8, 8, 4),
    "komposisi": (6, 12, 4, 8, 6, 5),
    "output_cabang": (6, 12, 4, 8, 6, 5),
    "delivery_qty": (6, 12, 5, 9, 7, 7),
}

PRODUCTION_SPEC = {
    "key": "produksi",
    "ready": READY,
    "sizes": SIZES,
    "order": READY_IDS,
    "period": frozenset(),
    "visual": frozenset(),
    "max_ready": len(READY),
    "max_preserved": len(READY),
}


async def get_production_layout(db, user: Optional[dict], permissions: Iterable[str]) -> dict:
    return await get_finance_layout(db, user, permissions, PRODUCTION_SPEC)


async def save_production_layout(db, user: Optional[dict], permissions: Iterable[str], version: Any, widgets: Any) -> dict:
    return await save_finance_layout(db, user, permissions, version, widgets, PRODUCTION_SPEC)


async def reset_production_layout(db, user: Optional[dict], permissions: Iterable[str]) -> dict:
    return await reset_finance_layout(db, user, permissions, PRODUCTION_SPEC)
