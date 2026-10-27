"""Focused tests for Dashboard Rincian spreadsheet endpoint adapters."""
from __future__ import annotations

import pytest
from fastapi import HTTPException

from services.dashboard_rincian import (
    WIDGET_META,
    query_dashboard_rincian,
    rincian_path,
)


def test_rincian_path_seeds_widget_and_dashboard():
    path = rincian_path("po_aktif", dashboard="produksi", basis="snapshot")
    assert path.startswith("/dashboard/rincian?")
    assert "widget=po_aktif" in path
    assert "dashboard=produksi" in path
    assert "basis=snapshot" in path


def test_widget_catalog_covers_priority_cards():
    for key in (
        "po_aktif", "po_urgent", "po_hold", "deadline_6h", "deadline_24h",
        "approval_design", "approval_payment", "perlu_produksi", "sisa_qty",
        "prioritas", "delivery_qty_today", "delivery_qty_overdue",
        "omzet", "today", "settlement", "trend", "shipToday", "shipLate",
        "sales", "purchases", "cash", "profit", "expense",
    ):
        assert key in WIDGET_META
    assert "po_terlambat" not in WIDGET_META


class _Cursor:
    def __init__(self, rows):
        self._rows = list(rows)

    def sort(self, *_a, **_k):
        return self

    def __aiter__(self):
        self._i = 0
        return self

    async def __anext__(self):
        if self._i >= len(self._rows):
            raise StopAsyncIteration
        row = self._rows[self._i]
        self._i += 1
        return row

    async def to_list(self, n=None):
        if n is None:
            return list(self._rows)
        return list(self._rows[:n])


class _Coll:
    def __init__(self, rows):
        self.rows = list(rows)

    def find(self, query=None, *_a, **_k):
        query = query or {}
        out = []
        for row in self.rows:
            if "id" in query and "$in" in query["id"]:
                if row.get("id") not in query["id"]["$in"]:
                    continue
            if query.get("cancelled", {}).get("$ne") is True and row.get("cancelled") is True:
                continue
            out.append(dict(row))
        return _Cursor(out)


class _DB(dict):
    def __getitem__(self, key):
        if key not in self:
            self[key] = _Coll([])
        return dict.__getitem__(self, key)

    def __getattr__(self, key):
        return self[key]


pytestmark = pytest.mark.anyio


async def test_po_aktif_kpi_count_is_unique_po_full_set(monkeypatch):
    from services import dashboard_rincian as mod

    orders = [
        {
            "id": "o1", "po_number": "PO-1", "customer": "A", "order_date": "2026-10-01",
            "priority": "normal", "cancelled": False, "hold": False,
            "qty_delivered": 0, "total_quantity": 10, "line_items": [{"quantity": 10, "unit": "PCS"}],
            "design_status": "Approved", "payment_status": "Approved",
            "delivery_deadline_status": "ok",
        },
        {
            "id": "o2", "po_number": "PO-2", "customer": "B", "order_date": "2026-10-02",
            "priority": "urgent", "cancelled": False, "hold": False,
            "qty_delivered": 0, "total_quantity": 5, "line_items": [{"quantity": 5, "unit": "PCS"}],
            "design_status": "Approved", "payment_status": "Approved",
            "delivery_deadline_status": "ok",
        },
        {
            "id": "o3", "po_number": "PO-3", "customer": "C", "order_date": "2026-10-03",
            "priority": "normal", "cancelled": True, "hold": False,
            "qty_delivered": 0, "total_quantity": 5, "line_items": [{"quantity": 5, "unit": "PCS"}],
            "design_status": "Approved", "payment_status": "Approved",
        },
    ]
    db = _DB({"orders": _Coll(orders)})

    def apply_hold(o):
        o["hold"] = bool(o.get("hold"))

    def attach_deadline(o, *_a, **_k):
        o.setdefault("delivery_deadline_status", "ok")

    monkeypatch.setattr(mod, "filter_orders_for_view", lambda rows, view, basis="snapshot": [
        r for r in rows if not r.get("cancelled") and view == "active"
    ])
    monkeypatch.setattr(mod, "build_status_po_page_rows", lambda page_orders, page_ids=None: [
        {
            "id": o["id"],
            "po_number": o["po_number"],
            "customer": o["customer"],
            "order_date": o["order_date"],
            "status_label": "Aktif",
            "priority": o.get("priority"),
            "qty_remaining": (o.get("total_quantity") or 0) - (o.get("qty_delivered") or 0),
            "deadline": "",
        }
        for o in page_orders
    ])
    monkeypatch.setattr(mod, "order_sort_key", lambda o: (o.get("order_date") or "", o.get("po_number") or "", o.get("id") or ""))
    monkeypatch.setattr(mod, "slice_eligible_page", lambda keys, page=1, page_size=50: {
        "page_ids": [k[2] for k in keys[(page - 1) * page_size: page * page_size]],
        "page": page,
        "page_size": page_size,
        "total_count": len(keys),
        "total_pages": max(1, (len(keys) + page_size - 1) // page_size) if keys else 1,
        "has_next": page * page_size < len(keys),
        "has_prev": page > 1,
    })
    monkeypatch.setattr(mod, "order_matches_search_date_filters", lambda *_a, **_k: True)

    with pytest.raises(HTTPException) as denied:
        await query_dashboard_rincian(
            db,
            widget="po_aktif",
            dashboard="produksi",
            permissions={"dashboard.production.view"},
            page=1,
            page_size=50,
            apply_hold=apply_hold,
            attach_deadline=attach_deadline,
        )
    assert denied.value.status_code == 403

    body = await query_dashboard_rincian(
        db,
        widget="po_aktif",
        dashboard="produksi",
        permissions={"dashboard.production.view", "nav.reports.status_po", "schedule.view"},
        page=1,
        page_size=50,
        apply_hold=apply_hold,
        attach_deadline=attach_deadline,
    )
    assert body["kpi_summary"]["count"] == 2
    assert body["total"] == 2
    assert {r["po_number"] for r in body["rows"]} == {"PO-1", "PO-2"}
    assert body["read_only"] is True
    assert body["writes"] == 0


async def test_unknown_widget_blocked():
    with pytest.raises(HTTPException) as exc:
        await query_dashboard_rincian(
            _DB(),
            widget="po_terlambat",
            dashboard="produksi",
            permissions={"dashboard.production.view", "nav.reports.status_po"},
        )
    assert exc.value.status_code == 404
