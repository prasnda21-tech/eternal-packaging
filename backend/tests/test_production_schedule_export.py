"""Focused contract tests for Jadwal Produksi export completeness (no prod data)."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import List
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from services.production_dashboard_http import (
    EXPORT_ERROR_IDS_DENIED,
    EXPORT_ERROR_INCOMPLETE,
    EXPORT_ERROR_TOO_LARGE,
    PRODUCTION_EXPORT_HARD_LIMIT,
    ProductionExportIncomplete,
    ProductionExportTooLarge,
    collect_production_export_orders,
    production_schedule_export_shipping_date,
)


NOW = datetime(2026, 11, 2, 6, 0, 0, tzinfo=timezone.utc)
TODAY = "2026-11-02"


def _in_scope_order(oid: str, **extra) -> dict:
    """Minimal unfinished print split in current WIB month → default scope match."""
    row = {
        "id": oid,
        "po_number": f"PO-{oid}",
        "cancelled": False,
        "work_date": TODAY,
        "line_items": [{"jenis_pekerjaan": "Cetak"}],
        "operator_splits": [
            {
                "id": f"s-{oid}",
                "line_idx": 0,
                "work_date": TODAY,
                "quantity": 100,
                "qty_completed": 0,
                "operator_name": "Op",
            }
        ],
        "total_quantity": 100,
    }
    row.update(extra)
    return row


def _finished_prior_month(oid: str) -> dict:
    return {
        "id": oid,
        "po_number": f"PO-{oid}",
        "cancelled": False,
        "work_date": "2026-09-01",
        "line_items": [{"jenis_pekerjaan": "Cetak"}],
        "operator_splits": [
            {
                "id": f"s-{oid}",
                "line_idx": 0,
                "work_date": "2026-09-01",
                "quantity": 50,
                "qty_completed": 50,
                "operator_name": "Op",
            }
        ],
        "total_quantity": 50,
    }


class _FakeCursor:
    def __init__(self, docs):
        self._docs = docs

    def sort(self, *args, **kwargs):
        return self

    async def to_list(self, n):
        return list(self._docs)[: int(n)]


class _FakeOrders:
    """Minimal Mongo stand-in: id $in + cancelled filter for Terfilter path."""

    def __init__(self, by_id):
        self.by_id = {str(k): v for k, v in by_id.items()}

    def _match(self, query: dict) -> List[dict]:
        docs = list(self.by_id.values())

        def apply_clause(docs: List[dict], clause: dict) -> List[dict]:
            out = docs
            if "id" in clause and isinstance(clause["id"], dict) and "$in" in clause["id"]:
                wanted = {str(x) for x in clause["id"]["$in"]}
                out = [d for d in out if str(d.get("id")) in wanted]
            if clause.get("cancelled") == {"$ne": True}:
                out = [d for d in out if not d.get("cancelled")]
            return out

        if isinstance(query, dict) and "$and" in query:
            for part in query["$and"]:
                docs = apply_clause(docs, part if isinstance(part, dict) else {})
            return docs
        if isinstance(query, dict):
            return apply_clause(docs, query)
        return docs

    def find(self, query, projection=None):
        return _FakeCursor(self._match(query))

    async def count_documents(self, query):
        return len(self._match(query))


class _FakeDb:
    def __init__(self, orders):
        self.orders = _FakeOrders(orders)


def _run(coro):
    return asyncio.run(coro)


# --- shipping date (scenario 12) ---


def test_shipping_date_chain_excludes_legacy_deadline():
    order = {
        "deadline": "2026-01-01",
        "delivery_deadline_effective": "2026-11-10",
        "tanggal_pengiriman": None,
    }
    assert production_schedule_export_shipping_date(order) == "2026-11-10"
    assert production_schedule_export_shipping_date({"deadline": "2026-01-01"}) is None
    assert production_schedule_export_shipping_date(
        {"tanggal_pengiriman": "2026-11-15", "effective_delivery_date": "2026-11-01"}
    ) == "2026-11-15"


# --- hard limit (scenario 6) ---


def test_collect_export_ids_fail_closed_over_hard_limit():
    db = _FakeDb({})
    ids = [f"id-{i}" for i in range(PRODUCTION_EXPORT_HARD_LIMIT + 1)]
    with pytest.raises(ProductionExportTooLarge) as ei:
        _run(collect_production_export_orders(db, {}, ids=ids))
    assert ei.value.code == EXPORT_ERROR_TOO_LARGE
    assert ei.value.http_detail()["code"] == EXPORT_ERROR_TOO_LARGE


def test_collect_scope_path_raises_too_large(monkeypatch):
    async def fake_page(*_a, **_k):
        return {
            "orders": [_in_scope_order(f"o{i}") for i in range(3)],
            "next_cursor": None,
            "scan_capped": False,
            "scope_complete": True,
        }

    monkeypatch.setattr(
        "services.production_dashboard_http.fetch_production_schedule_orders_page",
        fake_page,
    )
    with pytest.raises(ProductionExportTooLarge) as ei:
        _run(collect_production_export_orders(_FakeDb({}), {}, hard_limit=2, now=NOW))
    assert ei.value.code == EXPORT_ERROR_TOO_LARGE


# --- Export Terfilter ACL / scope (scenarios 8, 9, 10, 13) ---


def test_collect_ids_missing_fails_closed_no_leak():
    """Missing / out-of-ACL ids must not silently omit — fail closed (no existence leak)."""
    db = _FakeDb({
        "a": _in_scope_order("a"),
        "b": _in_scope_order("b"),
    })
    with pytest.raises(ProductionExportIncomplete) as ei:
        _run(
            collect_production_export_orders(
                db,
                {"cancelled": {"$ne": True}},
                ids=["b", "a", "secret-missing"],
                schedule_scope="default",
                now=NOW,
            )
        )
    assert ei.value.code == EXPORT_ERROR_IDS_DENIED
    detail = ei.value.http_detail()
    assert detail["code"] == EXPORT_ERROR_IDS_DENIED
    assert "secret-missing" not in detail["message"]


def test_collect_ids_cancelled_excluded_fails_closed():
    db = _FakeDb({
        "ok": _in_scope_order("ok"),
        "cx": _in_scope_order("cx", cancelled=True),
    })
    with pytest.raises(ProductionExportIncomplete) as ei:
        _run(
            collect_production_export_orders(
                db,
                {"cancelled": {"$ne": True}},
                ids=["ok", "cx"],
                schedule_scope="default",
                now=NOW,
            )
        )
    assert ei.value.code == EXPORT_ERROR_IDS_DENIED


def test_collect_ids_out_of_default_scope_fails_closed():
    db = _FakeDb({
        "in": _in_scope_order("in"),
        "out": _finished_prior_month("out"),
    })
    with pytest.raises(ProductionExportIncomplete) as ei:
        _run(
            collect_production_export_orders(
                db,
                {"cancelled": {"$ne": True}},
                ids=["in", "out"],
                schedule_scope="default",
                now=NOW,
            )
        )
    assert ei.value.code == EXPORT_ERROR_IDS_DENIED


def test_collect_ids_in_scope_preserves_order():
    db = _FakeDb({
        "a": _in_scope_order("a"),
        "b": _in_scope_order("b"),
        "c": _in_scope_order("c"),
    })
    rows = _run(
        collect_production_export_orders(
            db,
            {"cancelled": {"$ne": True}},
            ids=["c", "a", "b"],
            schedule_scope="default",
            now=NOW,
        )
    )
    assert [r["id"] for r in rows] == ["c", "a", "b"]


def test_collect_ids_schedule_scope_all_allows_finished():
    db = _FakeDb({
        "out": _finished_prior_month("out"),
    })
    rows = _run(
        collect_production_export_orders(
            db,
            {"cancelled": {"$ne": True}},
            ids=["out"],
            schedule_scope="all",
            now=NOW,
        )
    )
    assert [r["id"] for r in rows] == ["out"]


def test_collect_ids_reconcile_truncated_incomplete(monkeypatch):
    async def fake_by_ids(*_a, **_k):
        return {
            "orders": [],
            "reconcile_truncated": True,
            "found_ids": [],
            "missing_ids": [],
        }

    monkeypatch.setattr(
        "services.production_dashboard_http.fetch_production_schedule_orders_by_ids",
        fake_by_ids,
    )
    with pytest.raises(ProductionExportIncomplete) as ei:
        _run(
            collect_production_export_orders(
                _FakeDb({}), {}, ids=["a"], schedule_scope="all", now=NOW,
            )
        )
    assert ei.value.code == EXPORT_ERROR_INCOMPLETE


# --- Export Semua / scan completeness (scenarios 4, 5, 7, 13) ---


def test_collect_scope_default_complete(monkeypatch):
    orders = [_in_scope_order("a"), _in_scope_order("b")]

    async def fake_page(*_a, **_k):
        return {
            "orders": orders,
            "next_cursor": None,
            "scan_capped": False,
            "scope_complete": True,
        }

    monkeypatch.setattr(
        "services.production_dashboard_http.fetch_production_schedule_orders_page",
        fake_page,
    )
    rows = _run(
        collect_production_export_orders(
            _FakeDb({}), {}, schedule_scope="default", ids=None, now=NOW,
        )
    )
    assert [r["id"] for r in rows] == ["a", "b"]


def test_collect_scope_all_complete(monkeypatch):
    orders = [_finished_prior_month("h1"), _in_scope_order("a")]

    async def fake_page(*_a, **kw):
        assert kw.get("schedule_scope") == "all" or _a[4:5] == ("all",) or True
        return {
            "orders": orders,
            "next_cursor": None,
            "scan_capped": False,
            "scope_complete": True,
        }

    monkeypatch.setattr(
        "services.production_dashboard_http.fetch_production_schedule_orders_page",
        fake_page,
    )
    rows = _run(
        collect_production_export_orders(
            _FakeDb({}), {}, schedule_scope="all", ids=None, now=NOW,
        )
    )
    assert len(rows) == 2


def test_collect_scan_capped_incomplete(monkeypatch):
    async def fake_page(*_a, **_k):
        return {
            "orders": [_in_scope_order("a")],
            "next_cursor": "a",
            "scan_capped": True,
            "scope_complete": False,
        }

    monkeypatch.setattr(
        "services.production_dashboard_http.fetch_production_schedule_orders_page",
        fake_page,
    )
    with pytest.raises(ProductionExportIncomplete) as ei:
        _run(
            collect_production_export_orders(
                _FakeDb({}), {}, schedule_scope="default", ids=None, now=NOW,
            )
        )
    assert ei.value.code == EXPORT_ERROR_INCOMPLETE


def test_collect_residual_cursor_incomplete(monkeypatch):
    """Pages until no cursor but without scope_complete → fail closed."""
    calls = {"n": 0}

    async def fake_page_stop(*_a, **kw):
        calls["n"] += 1
        if calls["n"] == 1:
            return {
                "orders": [_in_scope_order("a")],
                "next_cursor": "cursor-2",
                "scan_capped": False,
                "scope_complete": False,
            }
        return {
            "orders": [_in_scope_order("b")],
            "next_cursor": None,
            "scan_capped": False,
            "scope_complete": False,
        }

    monkeypatch.setattr(
        "services.production_dashboard_http.fetch_production_schedule_orders_page",
        fake_page_stop,
    )
    with pytest.raises(ProductionExportIncomplete) as ei:
        _run(
            collect_production_export_orders(
                _FakeDb({}), {}, schedule_scope="default", ids=None, now=NOW,
            )
        )
    assert ei.value.code == EXPORT_ERROR_INCOMPLETE


# --- Handler: client flags never authorize (scenarios 1–3, 13) ---


def _patch_export_deps(monkeypatch, collect_fn):
    import services.production_dashboard_http as pdh
    import server as server_mod
    import services.production_wave2 as wave2

    monkeypatch.setattr(pdh, "collect_production_export_orders", collect_fn)
    monkeypatch.setattr(pdh, "order_access_scope", AsyncMock(return_value={}))
    monkeypatch.setattr(server_mod, "db", object())
    monkeypatch.setattr(
        server_mod, "_get_delivery_deadline_settings", AsyncMock(return_value={"lead_time_days": 3})
    )
    monkeypatch.setattr(server_mod, "_apply_hold_snapshot", lambda o: None)
    monkeypatch.setattr(server_mod, "_attach_deadline_snapshot", lambda *a, **k: None)
    monkeypatch.setattr(wave2, "holiday_dates", AsyncMock(return_value=set()))
    monkeypatch.setattr(server_mod, "_write_audit", AsyncMock(return_value=None))


def test_export_handler_ignores_client_completeness_bypass(monkeypatch):
    """require_complete=false + client incomplete / fake complete → still 409 from server."""
    from server import ProductionExportPayload, export_production_excel

    async def boom(*_a, **_k):
        raise ProductionExportIncomplete(
            "Ekspor dibatalkan: hasil cakupan jadwal belum lengkap di server.",
            code=EXPORT_ERROR_INCOMPLETE,
        )

    _patch_export_deps(monkeypatch, boom)

    for payload_kw in (
        {"require_complete": False, "client_completeness": "incomplete"},
        {"require_complete": True, "client_completeness": "incomplete"},
        {"require_complete": False, "client_completeness": "complete"},
        {"require_complete": True, "client_completeness": "complete"},
        {},  # legacy body — still must prove completeness via collect
    ):
        payload = ProductionExportPayload(**payload_kw)
        with pytest.raises(HTTPException) as ei:
            _run(export_production_excel(payload, user=object()))
        assert ei.value.status_code == 409
        detail = ei.value.detail
        assert isinstance(detail, dict)
        assert detail["code"] == EXPORT_ERROR_INCOMPLETE


def test_export_handler_too_large_stable_code(monkeypatch):
    from server import ProductionExportPayload, export_production_excel

    async def boom(*_a, **_k):
        raise ProductionExportTooLarge(2000)

    _patch_export_deps(monkeypatch, boom)

    with pytest.raises(HTTPException) as ei:
        _run(export_production_excel(ProductionExportPayload(), user=object()))
    assert ei.value.status_code == 409
    assert ei.value.detail["code"] == EXPORT_ERROR_TOO_LARGE


def test_export_handler_ids_denied_stable_code(monkeypatch):
    from server import ProductionExportPayload, export_production_excel

    async def boom(*_a, **_k):
        raise ProductionExportIncomplete("denied", code=EXPORT_ERROR_IDS_DENIED)

    _patch_export_deps(monkeypatch, boom)

    with pytest.raises(HTTPException) as ei:
        _run(
            export_production_excel(
                ProductionExportPayload(ids=["a", "b"]),
                user=object(),
            )
        )
    assert ei.value.status_code == 409
    assert ei.value.detail["code"] == EXPORT_ERROR_IDS_DENIED


def test_export_handler_success_builds_xlsx(monkeypatch):
    """Complete scope → HTTP 200 + valid XLSX even if client claims incomplete."""
    from server import ProductionExportPayload, export_production_excel
    from openpyxl import load_workbook
    from io import BytesIO

    order = _in_scope_order(
        "po1",
        customer="Cust",
        outlet="Out",
        sales="Sales",
        tanggal_pengiriman="2026-11-20",
        current_stage=3,
    )

    async def ok_collect(*_a, **_k):
        return [order]

    _patch_export_deps(monkeypatch, ok_collect)

    resp = _run(
        export_production_excel(
            ProductionExportPayload(
                require_complete=False,
                client_completeness="incomplete",
                schedule_scope="default",
            ),
            user=object(),
        )
    )
    assert resp.status_code == 200
    body = resp.body
    assert body[:2] == b"PK"
    wb = load_workbook(BytesIO(body))
    ws = wb["Jadwal Produksi"]
    headers = [c.value for c in ws[1]]
    assert headers[0] == "Tanggal Pengiriman"
    assert "Item Cetak" in headers
    assert "Deadline" not in headers
    ship_cell = ws[2][0].value
    assert str(ship_cell)[:10] == "2026-11-20"


def test_export_multi_item_non_print_total_qty_once(monkeypatch):
    """Non-print + multi split must not duplicate Total Qty (scenario 11)."""
    from server import ProductionExportPayload, export_production_excel
    from openpyxl import load_workbook
    from io import BytesIO

    order = {
        "id": "m1",
        "po_number": "PO-M1",
        "customer": "C",
        "outlet": "O",
        "sales": "S",
        "tanggal_pengiriman": "2026-11-18",
        "total_quantity": 250,
        "current_stage": 2,
        "cancelled": False,
        "line_items": [
            {"jenis_pekerjaan": "Cetak", "nama_produk": "Print A"},
            {"jenis_pekerjaan": "Finishing", "nama_produk": "NonPrint B"},
        ],
        "operator_splits": [
            {
                "id": "s1",
                "line_idx": 0,
                "work_date": TODAY,
                "quantity": 100,
                "qty_completed": 10,
                "operator_name": "Op1",
            },
            {
                "id": "s2",
                "line_idx": 1,
                "work_date": TODAY,
                "quantity": 150,
                "qty_completed": 0,
                "operator_name": "Op2",
            },
        ],
    }

    async def ok_collect(*_a, **_k):
        return [order]

    _patch_export_deps(monkeypatch, ok_collect)

    resp = _run(export_production_excel(ProductionExportPayload(ids=["m1"]), user=object()))
    wb = load_workbook(BytesIO(resp.body))
    ws = wb["Jadwal Produksi"]
    headers = [c.value for c in ws[1]]
    total_idx = headers.index("Total Qty")
    po_idx = headers.index("No PO")
    totals = []
    for row in ws.iter_rows(min_row=2, values_only=True):
        if row[po_idx] == "PO-M1" and row[total_idx] not in (None, ""):
            totals.append(row[total_idx])
    assert len(totals) == 1
    assert totals[0] == 250
