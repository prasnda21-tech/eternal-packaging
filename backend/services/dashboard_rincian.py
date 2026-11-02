"""Read-only Dashboard Rincian — spreadsheet detail for KPI cards.

Reuses Status PO / delivery plan / sales invoice / purchase invoice / mutasi
predicates. Does not write. Pagination is over the full filtered set.
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, List, Optional, Sequence, Tuple
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

from fastapi import HTTPException
from quantity_rules import packing_required_total, qty_delivered, is_process_only_service_line

from services.delivery_plan import (
    PLAN_PROJECTION,
    build_plan_rows,
    mongo_match as delivery_plan_mongo_match,
    paginate_rows as paginate_plan_rows,
)
from services.kepala_produksi_board import aggregate_kepala_kpis, filter_rows_for_kpi
from services.status_po import (
    build_status_po_page_rows,
    filter_orders_for_view,
    order_matches_search_date_filters,
    order_sort_key,
    slice_eligible_page,
)

WIB = ZoneInfo("Asia/Jakarta")

# Subfields required by delivery_remaining / process-only exclusion when the
# caller already projects dotted line_items.* (STATUS_PO_PROJECTION). Never mix
# these with parent "line_items": 1 — MongoDB rejects that as path collision 31250.
_STATUS_PO_RINCIAN_LINE_ITEM_FIELDS = (
    "line_items.quantity",
    "line_items.unit",
    "line_items.item_type",
    "line_items.physical_fulfillment",
    "line_items.production_process",
)


def merge_status_po_rincian_projection(projection: Optional[dict]) -> dict:
    """Ensure rincian fields without parent/child inclusion collisions.

    STATUS_PO_PROJECTION uses dotted ``line_items.*`` paths. Forcing parent
    ``line_items: 1`` on top of those paths raises OperationFailure 31250.
    When dotted children are present (or required), keep/add children only.
    When neither parent nor children exist, include the full ``line_items`` array.
    """
    out = dict(projection or {})
    for key in ("qty_delivered", "delivery_status", "current_stage", "total_quantity"):
        out[key] = 1

    has_parent = "line_items" in out
    has_children = any(str(k).startswith("line_items.") for k in out)
    if has_children:
        # Never leave parent + child together (MongoDB path collision 31250).
        out.pop("line_items", None)
        for path in _STATUS_PO_RINCIAN_LINE_ITEM_FIELDS:
            out[path] = 1
    elif not has_parent:
        out["line_items"] = 1
    return out


WIDGET_META: Dict[str, dict] = {
    # produksi
    "po_aktif": {
        "dashboard": "produksi",
        "title": "PO Aktif",
        "source": "status_po",
        "view": "active",
        "basis": "snapshot",
        "grain": "po",
        "kpi_any_of": ["dashboard.production.view"],
        "data_any_of": ["nav.reports.status_po"],
        "data_also_any_of": ["schedule.view"],
        "detail_kind": "po",
    },
    "po_urgent": {
        "dashboard": "produksi",
        "title": "PO Urgent",
        "source": "status_po",
        "view": "urgent",
        "basis": "snapshot",
        "grain": "po",
        "kpi_any_of": ["dashboard.production.view"],
        "data_any_of": ["nav.reports.status_po"],
        "data_also_any_of": ["schedule.view"],
        "detail_kind": "po",
    },
    "po_hold": {
        "dashboard": "produksi",
        "title": "PO HOLD",
        "source": "status_po",
        "view": "hold",
        "basis": "snapshot",
        "grain": "po",
        "kpi_any_of": ["dashboard.production.view"],
        "data_any_of": ["nav.reports.status_po"],
        "data_also_any_of": ["schedule.view"],
        "detail_kind": "po",
    },
    "deadline_6h": {
        "dashboard": "produksi",
        "title": "Deadline ≤ 6 Jam",
        "source": "status_po",
        "view": "h6",
        "basis": "snapshot",
        "grain": "po",
        "kpi_any_of": ["dashboard.production.view"],
        "data_any_of": ["nav.reports.status_po"],
        "data_also_any_of": ["schedule.view"],
        "detail_kind": "po",
    },
    "deadline_24h": {
        "dashboard": "produksi",
        "title": "Deadline ≤ 24 Jam",
        "source": "status_po",
        "view": "h24",
        "basis": "snapshot",
        "grain": "po",
        "kpi_any_of": ["dashboard.production.view"],
        "data_any_of": ["nav.reports.status_po"],
        "data_also_any_of": ["schedule.view"],
        "detail_kind": "po",
    },
    "approval_design": {
        "dashboard": "produksi",
        "title": "Menunggu Approval Design",
        "source": "status_po",
        "view": "design",
        "basis": "snapshot",
        "grain": "po",
        "kpi_any_of": ["dashboard.production.view"],
        "data_any_of": ["nav.reports.status_po"],
        "data_also_any_of": ["schedule.view"],
        "detail_kind": "po",
    },
    "approval_payment": {
        "dashboard": "produksi",
        "title": "Menunggu Approval Payment",
        "source": "status_po",
        "view": "payment",
        "basis": "snapshot",
        "grain": "po",
        "kpi_any_of": ["dashboard.production.view"],
        "data_any_of": ["nav.reports.status_po"],
        "data_also_any_of": ["schedule.view"],
        "detail_kind": "po",
    },
    "perlu_produksi": {
        "dashboard": "produksi",
        "title": "Perlu Produksi",
        "source": "kepala",
        "kpi_key": "perlu-produksi",
        "grain": "line",
        "kpi_any_of": ["dashboard.production.view"],
        "data_any_of": ["nav.tasks.kepala_produksi", "schedule.edit"],
        "detail_kind": "po",
    },
    "sisa_qty": {
        "dashboard": "produksi",
        "title": "Sisa Qty Produksi",
        "source": "kepala",
        "kpi_key": "sisa-produksi",
        "grain": "line",
        "kpi_any_of": ["dashboard.production.view"],
        "data_any_of": ["nav.tasks.kepala_produksi", "schedule.edit"],
        "detail_kind": "po",
    },
    "prioritas": {
        "dashboard": "produksi",
        "title": "Prioritas Pekerjaan",
        "source": "kepala",
        "kpi_key": "perlu-produksi",
        "grain": "line",
        "kpi_any_of": ["dashboard.production.view"],
        "data_any_of": ["nav.tasks.kepala_produksi", "schedule.edit"],
        "detail_kind": "po",
    },
    "delivery_qty_today": {
        "dashboard": "produksi",
        "title": "Qty Rencana Hari Ini",
        "source": "delivery_plan",
        "status": "hari_ini",
        "grain": "po",
        "kpi_any_of": ["dashboard.production.view"],
        "data_any_of": ["delivery.schedule.view", "schedule.view", "nav.delivery_schedule"],
        "detail_kind": "po",
    },
    "delivery_qty_overdue": {
        "dashboard": "produksi",
        "title": "Qty Rencana Lewat Rencana",
        "source": "delivery_plan",
        "status": "terlambat",
        "grain": "po",
        "kpi_any_of": ["dashboard.production.view"],
        "data_any_of": ["delivery.schedule.view", "schedule.view", "nav.delivery_schedule"],
        "detail_kind": "po",
    },
    # penjualan / keuangan shared invoice sources
    "omzet": {
        "dashboard": "penjualan",
        "title": "Omzet",
        "source": "sales_invoice",
        "financial_status": "finalized",
        "grain": "invoice",
        "kpi_any_of": ["dashboard.sales.view"],
        "data_any_of": ["sales.invoice.view", "sales_order.view", "po.view"],
        "detail_kind": "sales_invoice",
    },
    "today": {
        "dashboard": "penjualan",
        "title": "Penjualan Hari Ini",
        "source": "sales_invoice",
        "financial_status": "finalized",
        "grain": "invoice",
        "kpi_any_of": ["dashboard.sales.view"],
        "data_any_of": ["sales.invoice.view", "sales_order.view", "po.view"],
        "detail_kind": "sales_invoice",
    },
    "settlement": {
        "dashboard": "penjualan",
        "title": "Settlement",
        "source": "sales_invoice",
        "financial_status": "finalized",
        "grain": "invoice",
        "kpi_any_of": ["dashboard.sales.view"],
        "data_any_of": ["sales.invoice.view", "sales_order.view", "po.view"],
        "detail_kind": "sales_invoice",
    },
    "trend": {
        "dashboard": "penjualan",
        "title": "Tren Penjualan",
        "source": "sales_invoice",
        "financial_status": "finalized",
        "grain": "invoice",
        "kpi_any_of": ["dashboard.sales.view", "dashboard.finance.view"],
        "data_any_of": ["sales.invoice.view", "sales_order.view", "po.view"],
        "detail_kind": "sales_invoice",
    },
    "shipToday": {
        "dashboard": "penjualan",
        "title": "Pengiriman Hari Ini",
        "source": "delivery_schedule",
        "status": "hari_ini",
        "grain": "sj",
        "kpi_any_of": ["dashboard.sales.view"],
        "data_any_of": ["delivery.schedule.view", "schedule.view", "nav.delivery_schedule"],
        "detail_kind": "po",
    },
    "shipLate": {
        "dashboard": "penjualan",
        "title": "Pengiriman Terlambat",
        "source": "delivery_schedule",
        "status": "terlambat",
        "grain": "sj",
        "kpi_any_of": ["dashboard.sales.view"],
        "data_any_of": ["delivery.schedule.view", "schedule.view", "nav.delivery_schedule"],
        "detail_kind": "po",
    },
    "sales": {
        "dashboard": "keuangan",
        "title": "Penjualan",
        "source": "sales_invoice",
        "financial_status": "finalized",
        "grain": "invoice",
        "kpi_any_of": ["dashboard.finance.view", "dashboard.sales.view"],
        "data_any_of": ["sales.invoice.view", "sales_order.view", "po.view", "nav.sales.sales_invoice"],
        "detail_kind": "sales_invoice",
    },
    "purchases": {
        "dashboard": "keuangan",
        "title": "Pembelian",
        "source": "purchase_invoice",
        "grain": "invoice",
        "kpi_any_of": ["dashboard.finance.view", "dashboard.sales.view", "purchase_invoices.view"],
        "data_any_of": ["purchase_invoices.view", "purchase_invoices.create"],
        "detail_kind": "purchase_invoice",
    },
    "cash": {
        "dashboard": "keuangan",
        "title": "Arus Kas",
        "source": "cash_movement",
        "grain": "journal",
        "kpi_any_of": ["dashboard.finance.view"],
        "data_any_of": ["cash_bank.view", "nav.cash_bank.mutasi", "nav.cash_bank"],
        "detail_kind": "cash_source",
    },
    "profit": {
        "dashboard": "keuangan",
        "title": "Laba/Rugi",
        "source": "profit_loss",
        "grain": "account",
        "kpi_any_of": ["dashboard.finance.view", "profit_loss.view"],
        "data_any_of": ["profit_loss.view", "nav.ledger.profit_loss"],
        "detail_kind": "ledger",
    },
    "expense": {
        "dashboard": "keuangan",
        "title": "Beban Perusahaan",
        "source": "profit_loss",
        "grain": "account",
        "section": "expense",
        "kpi_any_of": ["dashboard.finance.view"],
        "data_any_of": ["profit_loss.view", "nav.ledger.profit_loss"],
        "detail_kind": "ledger",
    },
    "min_stock": {
        "dashboard": "keuangan",
        "title": "Barang Stok Minimum",
        "source": "min_stock",
        "grain": "item_warehouse",
        "kpi_any_of": ["current_stock.view"],
        "data_any_of": ["current_stock.view"],
        "detail_kind": "min_stock",
    },
}

COLUMNS = {
    "po": [
        {"key": "row_no", "label": "#", "type": "number"},
        {"key": "po_number", "label": "No PO", "type": "text"},
        {"key": "order_date", "label": "Tanggal PO", "type": "date"},
        {"key": "customer", "label": "Pelanggan", "type": "text"},
        {"key": "status_label", "label": "Status", "type": "text"},
        {"key": "priority", "label": "Prioritas", "type": "text"},
        {"key": "delivery_remaining", "label": "Sisa Kirim", "type": "text"},
        {"key": "deadline", "label": "Deadline", "type": "date"},
    ],
    "line": [
        {"key": "row_no", "label": "#", "type": "number"},
        {"key": "po_number", "label": "No PO", "type": "text"},
        {"key": "customer", "label": "Pelanggan", "type": "text"},
        {"key": "item_name", "label": "Item", "type": "text"},
        {"key": "unit", "label": "Satuan", "type": "text"},
        {"key": "qty_remaining", "label": "Belum Diproduksi", "type": "number"},
        {"key": "priority_label", "label": "Prioritas", "type": "text"},
        {"key": "tanggal_kirim", "label": "Tanggal Kirim", "type": "date"},
    ],
    "plan": [
        {"key": "row_no", "label": "#", "type": "number"},
        {"key": "po_number", "label": "No PO", "type": "text"},
        {"key": "customer_name", "label": "Pelanggan", "type": "text"},
        {"key": "tanggal_pengiriman", "label": "Tgl Rencana", "type": "date"},
        {"key": "qty_unit", "label": "Satuan", "type": "text"},
        {"key": "qty_remaining", "label": "Sisa Qty", "type": "number"},
        {"key": "delivery_schedule_status", "label": "Status", "type": "text"},
    ],
    "sj": [
        {"key": "row_no", "label": "#", "type": "number"},
        {"key": "po_number", "label": "No PO", "type": "text"},
        {"key": "customer_name", "label": "Pelanggan", "type": "text"},
        {"key": "surat_jalan", "label": "No SJ", "type": "text"},
        {"key": "tanggal_pengiriman", "label": "Tgl Kirim", "type": "date"},
        {"key": "status", "label": "Status", "type": "text"},
        {"key": "qty", "label": "Qty", "type": "number"},
    ],
    "sales_invoice": [
        {"key": "row_no", "label": "#", "type": "number"},
        {"key": "invoice_number", "label": "No Faktur", "type": "text"},
        {"key": "invoice_date", "label": "Tanggal", "type": "date"},
        {"key": "customer_name", "label": "Pelanggan", "type": "text"},
        {"key": "grand_total", "label": "Nominal", "type": "number"},
        {"key": "financial_status", "label": "Status Finansial", "type": "text"},
        {"key": "payment_status", "label": "Pelunasan", "type": "text"},
    ],
    "purchase_invoice": [
        {"key": "row_no", "label": "#", "type": "number"},
        {"key": "invoice_number", "label": "No Faktur", "type": "text"},
        {"key": "invoice_date", "label": "Tanggal", "type": "date"},
        {"key": "supplier_name", "label": "Supplier", "type": "text"},
        {"key": "grand_total", "label": "Nominal", "type": "number"},
        {"key": "payment_status", "label": "Status", "type": "text"},
    ],
    "cash": [
        {"key": "row_no", "label": "#", "type": "number"},
        {"key": "date", "label": "Tanggal", "type": "date"},
        {"key": "number", "label": "No. Transaksi", "type": "text"},
        {"key": "source_type", "label": "Sumber", "type": "text"},
        {"key": "debit", "label": "Debit", "type": "number"},
        {"key": "credit", "label": "Kredit", "type": "number"},
        {"key": "account_name", "label": "Rekening", "type": "text"},
    ],
    "pl": [
        {"key": "row_no", "label": "#", "type": "number"},
        {"key": "account_code", "label": "Kode", "type": "text"},
        {"key": "account_name", "label": "Akun", "type": "text"},
        {"key": "group", "label": "Kelompok", "type": "text"},
        {"key": "amount", "label": "Nominal", "type": "number"},
    ],
    "min_stock": [
        {"key": "row_no", "label": "#", "type": "number"},
        {"key": "item_name", "label": "Produk", "type": "text"},
        {"key": "branch_id", "label": "Cabang", "type": "text"},
        {"key": "warehouse_name", "label": "Gudang", "type": "text"},
        {"key": "stock", "label": "Stock", "type": "number"},
        {"key": "stock_dapat_dijual", "label": "Stock Dapat Dijual", "type": "number"},
        {"key": "qty_polos", "label": "Qty Polos", "type": "number"},
        {"key": "sisa_belum_cetak", "label": "Sisa Belum Cetak", "type": "number"},
        {"key": "minimum_label", "label": "Batas Minimum", "type": "text"},
        {"key": "status_label", "label": "Status", "type": "text"},
    ],
}


def rincian_path(widget: str, *, dashboard: str = "", **params: Any) -> str:
    meta = WIDGET_META.get(widget) or {}
    query = {"dashboard": dashboard or meta.get("dashboard") or "produksi", "widget": widget}
    for key, value in params.items():
        if value is None or value == "":
            continue
        query[key] = value
    if meta.get("basis") and "basis" not in query:
        query["basis"] = meta["basis"]
    return f"/dashboard/rincian?{urlencode(query)}"


def _has_any(perms: set, keys: Sequence[str]) -> bool:
    return any(key in perms for key in keys)


def _sum_money(values) -> Optional[float]:
    """Missing or invalid amounts make the summary incomplete, never zero."""
    total = Decimal("0")
    for raw in values:
        try:
            value = Decimal(str(raw))
        except (InvalidOperation, ValueError):
            return None
        if not value.is_finite():
            return None
        total += value
    return float(total)


def _require_access(meta: dict, perms: set) -> None:
    if not _has_any(perms, meta.get("kpi_any_of") or []):
        raise HTTPException(403, "Tidak ada izin membaca KPI dashboard")
    if meta.get("data_also_any_of") and not _has_any(perms, meta["data_also_any_of"]):
        raise HTTPException(403, "Tidak ada izin membaca dataset rincian")
    if not _has_any(perms, meta.get("data_any_of") or []):
        raise HTTPException(403, "Tidak ada izin membaca dataset rincian")


def _paginate(rows: List[dict], page: int, page_size: int) -> Tuple[List[dict], dict]:
    size = max(1, min(int(page_size or 50), 200))
    total = len(rows)
    pages = max(1, (total + size - 1) // size) if total else 1
    current = max(1, min(int(page or 1), pages))
    start = (current - 1) * size
    slice_rows = rows[start : start + size]
    return slice_rows, {
        "page": current,
        "page_size": size,
        "total": total,
        "total_pages": pages,
        "has_next": current < pages,
        "has_prev": current > 1,
    }


def _add_row_no(rows: List[dict], page: int, page_size: int) -> List[dict]:
    start = (max(1, page) - 1) * max(1, page_size)
    out = []
    for idx, row in enumerate(rows):
        item = dict(row)
        item["row_no"] = start + idx + 1
        out.append(item)
    return out


def _detail_href(kind: str, row: dict) -> Optional[str]:
    if kind == "po":
        oid = row.get("id") or row.get("order_id")
        return f"/po/{oid}" if oid else None
    if kind == "sales_invoice":
        iid = row.get("id") or row.get("invoice_id")
        return f"/penjualan/faktur-penjualan?invoice_id={iid}" if iid else None
    if kind == "purchase_invoice":
        iid = row.get("id") or row.get("invoice_id")
        return f"/pembelian/faktur-pembelian?invoice_id={iid}" if iid else None
    if kind == "cash_source":
        path = row.get("source_path")
        sid = row.get("source_id")
        if path and sid:
            return f"{path}?id={sid}"
        return None
    if kind == "ledger":
        aid = row.get("account_id")
        return f"/buku-besar/buku-besar?account_id={aid}" if aid else "/buku-besar/laba-rugi"
    return None


def delivery_remaining(order: dict) -> dict:
    """Physical remaining quantity; never label remaining print as delivery.

    The legacy delivered scalar cannot be allocated across different units.
    Keep that ambiguity explicit rather than inventing per-line delivery.
    """
    units = {}
    for line in order.get("line_items") or []:
        if not isinstance(line, dict) or is_process_only_service_line(line):
            continue
        unit = str(line.get("unit") or "tanpa satuan").strip().lower()
        try:
            raw_qty = line.get("quantity")
            if isinstance(raw_qty, bool) or raw_qty is None or not str(raw_qty).strip():
                raise ValueError("Missing quantity")
            parsed = Decimal(str(raw_qty))
            if not parsed.is_finite() or parsed != parsed.to_integral_value():
                raise ValueError("Invalid integer quantity")
            qty = max(0, int(parsed))
        except (ValueError, TypeError, InvalidOperation):
            return {"value": None, "label": "Qty tidak valid"}
        units[unit] = units.get(unit, 0) + qty
    required = packing_required_total(order)
    delivered = qty_delivered(order)
    if not units:
        units = {"tanpa satuan": required}
    if len(units) == 1:
        unit = next(iter(units))
        remaining = max(0, required - delivered)
        return {"value": remaining, "unit": unit, "by_unit": {unit: remaining},
                "label": f"{remaining:,} {unit}".replace(",", ".")}
    if delivered == 0:
        remaining = units
    elif delivered >= required:
        remaining = dict.fromkeys(units, 0)
    else:
        return {"value": None, "label": "Parsial · satuan campuran (lihat PO)"}
    return {"value": None, "by_unit": remaining,
            "label": " · ".join(f"{qty:,} {unit}".replace(",", ".") for unit, qty in remaining.items())}


async def _status_po_rincian(
    db,
    *,
    meta: dict,
    q: str,
    page: int,
    page_size: int,
    apply_hold,
    attach_deadline,
    lead_days: int,
    holidays,
    sales_scope: Optional[dict] = None,
    branch_scope: Optional[dict] = None,
    projection: Optional[dict] = None,
) -> dict:
    view = meta["view"]
    basis = meta.get("basis") or "snapshot"
    clauses: List[dict] = [{"cancelled": {"$ne": True}}]
    if sales_scope:
        clauses.append(sales_scope)
    if branch_scope:
        clauses.append(branch_scope)
    query = {"$and": clauses} if len(clauses) > 1 else clauses[0]
    proj = merge_status_po_rincian_projection(
        projection
        or {
            "id": 1, "po_number": 1, "customer": 1, "order_date": 1, "priority": 1,
            "hold": 1, "qty_delivered": 1, "current_stage": 1, "deadline": 1,
            "delivery_deadline_status": 1, "delivery_deadline_effective": 1,
            "design_status": 1, "payment_status": 1, "design_bypassed": 1,
            "payment_bypassed": 1, "line_items": 1, "cancelled": 1,
            "total_quantity": 1, "shipments": 1, "buffer_stock": 1, "delivery_status": 1,
        }
    )
    eligible_keys: List[tuple] = []
    cursor = db.orders.find(query, proj).sort([("order_date", 1), ("po_number", 1), ("id", 1)])
    async for raw in cursor:
        o = raw
        apply_hold(o)
        attach_deadline(o, lead_days, holidays=holidays)
        if not filter_orders_for_view([o], view, basis=basis):
            continue
        if q:
            filters = {"po": q, "customer": "", "status": "", "date_from": "", "date_to": ""}
            if not order_matches_search_date_filters(o, filters):
                continue
        eligible_keys.append(order_sort_key(o))
    eligible_keys.sort()
    sliced = slice_eligible_page(eligible_keys, page=page, page_size=page_size)
    page_ids = sliced["page_ids"]
    page_orders: List[dict] = []
    if page_ids:
        found = await db.orders.find({"id": {"$in": page_ids}}, proj).to_list(len(page_ids))
        by_id = {str(o.get("id")): o for o in found}
        for oid in page_ids:
            o = by_id.get(str(oid))
            if not o:
                continue
            apply_hold(o)
            attach_deadline(o, lead_days, holidays=holidays)
            page_orders.append(o)
    rows = build_status_po_page_rows(page_orders, page_ids=page_ids)
    source_orders = {str(o.get("id")): o for o in page_orders}
    normalized = []
    for row in rows:
        source_order = source_orders.get(str(row.get("id")), {})
        remaining = delivery_remaining(source_order)
        item = {
            "id": row.get("id"),
            "order_id": row.get("id"),
            "po_number": row.get("po_number"),
            "order_date": (str(row.get("order_date") or "")[:10]),
            "customer": row.get("customer") or row.get("customer_name") or "",
            "status_label": row.get("status_label") or row.get("workflow_status") or row.get("status") or "",
            "priority": row.get("priority") or "normal",
            "qty_remaining": remaining.get("value"),
            "delivery_remaining": remaining["label"],
            "delivery_remaining_by_unit": remaining.get("by_unit"),
            "deadline": source_order.get("delivery_deadline_effective") or row.get("deadline") or row.get("delivery_deadline_effective") or "",
            "detail_href": _detail_href("po", row),
        }
        normalized.append(item)
    page_rows = _add_row_no(normalized, sliced["page"], sliced["page_size"])
    return {
        "columns": COLUMNS["po"],
        "rows": page_rows,
        "total": sliced["total_count"],
        "page": sliced["page"],
        "page_size": sliced["page_size"],
        "total_pages": sliced["total_pages"],
        "has_next": sliced["has_next"],
        "has_prev": sliced["has_prev"],
        "kpi_summary": {
            "count": sliced["total_count"],
            "label": "PO unik",
            "grain": "po",
            "detail": "Snapshot PO unik. Sisa kirim = qty fisik PO dikurangi qty terkirim sah; layanan tanpa barang fisik dikecualikan. Parsial lintas satuan tidak dialokasikan secara asumsi.",
        },
    }


async def query_dashboard_rincian(
    db,
    *,
    widget: str,
    dashboard: str = "",
    permissions,
    q: str = "",
    page: int = 1,
    page_size: int = 50,
    start_date: str = "",
    end_date: str = "",
    unit: str = "",
    status: str = "",
    cash_bank_id: str = "",
    apply_hold=None,
    attach_deadline=None,
    lead_days: int = 3,
    holidays=None,
    sales_scope: Optional[dict] = None,
    branch_scope: Optional[dict] = None,
    status_po_projection: Optional[dict] = None,
    list_delivery_schedule_rows=None,
    query_sales_invoices=None,
    list_purchase_invoices=None,
    get_mutasi=None,
    get_cash_movement=None,
    get_profit_loss=None,
    list_cash_banks=None,
    build_kepala_board=None,
) -> dict:
    key = str(widget or "").strip()
    meta = WIDGET_META.get(key)
    if not meta:
        raise HTTPException(404, "Widget rincian tidak dikenali atau diblokir")
    dash = (dashboard or meta["dashboard"]).strip().lower()
    if dash != meta["dashboard"] and key not in ("trend", "purchases", "sales"):
        # allow sales/finance shared widgets across those two dashboards
        if not (meta["dashboard"] in {"penjualan", "keuangan"} and dash in {"penjualan", "keuangan"}):
            raise HTTPException(400, "Dashboard tidak cocok untuk widget ini")
    perms = {str(p) for p in (permissions or []) if p}
    _require_access(meta, perms)

    generated_at = datetime.now(WIB).isoformat(timespec="seconds")
    body: dict

    if meta["source"] == "status_po":
        if not apply_hold or not attach_deadline:
            raise HTTPException(500, "Adapter Status PO belum siap")
        body = await _status_po_rincian(
            db,
            meta=meta,
            q=q,
            page=page,
            page_size=page_size,
            apply_hold=apply_hold,
            attach_deadline=attach_deadline,
            lead_days=lead_days,
            holidays=holidays or [],
            sales_scope=sales_scope,
            branch_scope=branch_scope,
            projection=status_po_projection,
        )
    elif meta["source"] == "delivery_plan":
        plan_scope = {}
        if sales_scope:
            plan_scope.update(sales_scope)
        if branch_scope:
            plan_scope.update(branch_scope)
        match = delivery_plan_mongo_match(plan_scope or None)
        docs = await db.orders.find(match, PLAN_PROJECTION).to_list(None)
        st = status or meta.get("status") or ""
        today = datetime.now(WIB).date().isoformat()
        filters = {
            "q": q or "",
            "status": st,
            "unit": unit or "",
            "date_from": start_date if st == "hari_ini" else "",
            "date_to": end_date if st == "hari_ini" else "",
        }
        if st == "hari_ini" and not filters["date_from"]:
            filters["date_from"] = today
            filters["date_to"] = today
        rows = build_plan_rows(docs, lead_time_days=lead_days, holidays=holidays or [], filters=filters)
        page_rows, meta_page = _paginate(rows, page, page_size)
        by_unit = {}
        for row in rows:
            bucket = str(row.get("qty_unit") or "Tanpa satuan")
            by_unit[bucket] = by_unit.get(bucket, 0) + int(row.get("qty_remaining") or 0)
        out_rows = []
        for row in page_rows:
            item = {
                "id": row.get("id"),
                "order_id": row.get("id"),
                "po_number": row.get("po_number"),
                "customer_name": row.get("customer_name"),
                "tanggal_pengiriman": row.get("tanggal_pengiriman"),
                "qty_unit": row.get("qty_unit"),
                "qty_remaining": row.get("qty_remaining"),
                "delivery_schedule_status": row.get("delivery_schedule_status"),
                "detail_href": _detail_href("po", row),
            }
            out_rows.append(item)
        body = {
            "columns": COLUMNS["plan"],
            "rows": _add_row_no(out_rows, meta_page["page"], meta_page["page_size"]),
            **meta_page,
            "kpi_summary": {
                "count": meta_page["total"],
                "by_unit": by_unit,
                "unit": unit or None,
                "label": "PO dengan sisa qty",
                "grain": "po",
            },
        }
    elif meta["source"] == "kepala":
        if not build_kepala_board:
            raise HTTPException(500, "Adapter Kepala Produksi belum siap")
        board = build_kepala_board()
        if hasattr(board, "__await__"):
            board = await board  # type: ignore[misc]
        kpi_key = meta.get("kpi_key") or "perlu-produksi"
        board_rows = list(board.get("board_rows") or [])
        # Priority card previews the complete ordered board, not only print work.
        rows = board_rows if key == "prioritas" else filter_rows_for_kpi(board_rows, kpi_key)
        if unit:
            rows = [r for r in rows if str(r.get("unit") or "") == unit]
        if q:
            needle = q.lower()
            rows = [
                r for r in rows
                if needle in str(r.get("order_number") or r.get("po_number") or "").lower()
                or needle in str(r.get("customer") or r.get("customer_name") or "").lower()
                or needle in str(r.get("item") or r.get("item_name") or r.get("product") or "").lower()
            ]
        unique_po = {str(r.get("order_id") or r.get("id") or r.get("po_number")) for r in rows if r.get("order_id") or r.get("id") or r.get("po_number")}
        page_rows, meta_page = _paginate(rows, page, page_size)
        out_rows = []
        for row in page_rows:
            oid = row.get("order_id") or row.get("id")
            out_rows.append({
                "id": f"{oid}:{row['line_idx']}" if row.get("line_idx") is not None else oid,
                "order_id": oid,
                "po_number": row.get("order_number") or row.get("po_number"),
                "customer": row.get("customer") or row.get("customer_name") or "",
                "item_name": row.get("item") or row.get("item_name") or row.get("product") or row.get("line_name") or "",
                "unit": row.get("unit") or "",
                "qty_remaining": row.get("qty_production_remaining"),
                "priority_label": ", ".join(row.get("priority_display") or []) or row.get("priority_label") or row.get("priority") or "",
                "tanggal_kirim": row.get("effective_delivery_date") or row.get("tanggal_kirim") or row.get("delivery_date") or "",
                "detail_href": _detail_href("po", {"id": oid}),
            })
        body = {
            "columns": COLUMNS["line"],
            "rows": _add_row_no(out_rows, meta_page["page"], meta_page["page_size"]),
            **meta_page,
            "kpi_summary": {
                "count": len(unique_po),
                "line_count": len(rows),
                "label": f"{len(unique_po)} PO unik · {len(rows)} baris item",
                "grain": "line",
                "kpi_key": kpi_key,
                **({"by_unit": aggregate_kepala_kpis(rows)["sisa-produksi"]["by_unit"]} if key == "sisa_qty" else {}),
            },
        }
    elif meta["source"] == "sales_invoice":
        from services.sales_dashboard import (
            aggregate_omzet,
            aggregate_settlement,
            sales_invoice_population_match,
        )

        today = datetime.now(WIB).date().isoformat()
        df = start_date
        dt = end_date
        # Explicit dates stay aligned with the card. WIB today is only the
        # default when the today widget is opened without a period.
        if key == "today" and not (df and dt):
            df = dt = today
        po_ids = None
        if sales_scope:
            po_ids = set()
            cursor = db.orders.find(sales_scope, {"_id": 0, "id": 1})
            async for order in cursor:
                if order.get("id"):
                    po_ids.add(str(order["id"]))
        match = sales_invoice_population_match(
            start=df or None,
            end=dt or None,
            po_ids=po_ids,
            branch_clause=branch_scope or None,
            search=q or "",
        )
        searching = bool(str(q or "").strip())
        comparable_filters = (not searching) and bool(df) and bool(dt)

        def _customer_name(inv: dict) -> str:
            customer = inv.get("customer")
            if isinstance(customer, dict):
                return customer.get("name") or customer.get("customer_name") or ""
            return inv.get("customer_name") or customer or ""

        def _page_meta(total: int) -> dict:
            return {
                "page": page,
                "page_size": page_size,
                "total": total,
                "total_pages": max(1, (total + page_size - 1) // page_size) if total else 1,
                "has_next": page * page_size < total,
                "has_prev": page > 1,
            }

        if key == "settlement":
            # Pelunasan follows the card: grand_total, not the omzet basis.
            summary = await aggregate_settlement(db, match, page=page, page_size=page_size)
            total = int(summary["population_count"] or 0)
            out_rows = []
            for inv in summary["rows"]:
                out_rows.append({
                    **inv,
                    "detail_href": _detail_href("sales_invoice", inv),
                })
            detail = summary["detail"]
            if searching:
                search_note = (
                    "Total mengikuti pencarian. Pembandingan dengan kartu memerlukan filter yang identik."
                )
                detail = f"{detail} {search_note}".strip() if detail else search_note
            body = {
                "columns": COLUMNS["sales_invoice"],
                "rows": _add_row_no(out_rows, page, page_size),
                **_page_meta(total),
                "kpi_summary": {
                    "count": None if summary["status"] == "error" else total,
                    "amount": summary["amount"],
                    "settled_grand_total": summary["settled_grand_total"],
                    "open_grand_total": summary["open_grand_total"],
                    "settled_count": summary["settled_count"],
                    "open_count": summary["open_count"],
                    "status": summary["status"],
                    "detail": detail,
                    "label": "Faktur finalized",
                    "basis": "grand_total",
                    "grain": "invoice",
                    "comparable_to_card": comparable_filters and summary["status"] != "error",
                },
            }
        else:
            summary = await aggregate_omzet(db, match)
            total = int(summary["invoice_count"] or 0) if summary["invoice_count"] is not None else 0
            if summary["status"] == "error":
                count_doc = await db.sales_invoices.aggregate([
                    {"$match": match},
                    {"$count": "count"},
                ]).to_list(1)
                total = int(count_doc[0]["count"]) if count_doc else 0
            page_rows_raw = await db.sales_invoices.find(
                match,
                {
                    "_id": 0,
                    "id": 1,
                    "invoice_number": 1,
                    "invoice_date": 1,
                    "customer": 1,
                    "customer_name": 1,
                    "after_discount": 1,
                    "financial_status": 1,
                    "payment_status": 1,
                },
            ).sort([("invoice_date", 1), ("invoice_number", 1), ("id", 1)]).skip((page - 1) * page_size).limit(page_size).to_list(page_size)
            out_rows = []
            for inv in page_rows_raw:
                out_rows.append({
                    "id": inv.get("id"),
                    "invoice_number": inv.get("invoice_number") or inv.get("number"),
                    "invoice_date": str(inv.get("invoice_date") or "")[:10],
                    "customer_name": _customer_name(inv),
                    "grand_total": inv.get("after_discount"),
                    "financial_status": inv.get("financial_status") or "",
                    "payment_status": inv.get("payment_status") or "",
                    "detail_href": _detail_href("sales_invoice", inv),
                })
            detail = summary["detail"]
            if searching:
                search_note = "Total mengikuti pencarian. Kartu omzet sama dengan rincian hanya bila pencarian kosong dan periode serta status sama."
                detail = f"{detail} {search_note}".strip() if detail else search_note
            body = {
                "columns": COLUMNS["sales_invoice"],
                "rows": _add_row_no(out_rows, page, page_size),
                **_page_meta(total),
                "kpi_summary": {
                    "count": None if summary["status"] == "error" else total,
                    "amount": summary["amount"],
                    "status": summary["status"],
                    "detail": detail,
                    "label": "Faktur finalized",
                    "basis": "after_discount",
                    "grain": "invoice",
                    "comparable_to_card": comparable_filters and summary["status"] != "error",
                },
            }
    elif meta["source"] == "purchase_invoice":
        if not list_purchase_invoices:
            raise HTTPException(500, "Adapter Faktur Pembelian belum siap")
        payload = await list_purchase_invoices(db, date_from=start_date or None, date_to=end_date or None, q=q or None)
        if isinstance(payload, list):
            invoices = payload
        else:
            invoices = payload.get("invoices") or payload.get("items") or []
        if q and not isinstance(payload, list):
            needle = q.lower()
            invoices = [
                i for i in invoices
                if needle in str(i.get("invoice_number") or i.get("number") or "").lower()
                or needle in str(i.get("supplier_name") or i.get("party_name") or "").lower()
            ]
        page_rows, meta_page = _paginate(invoices, page, page_size)
        amount = _sum_money(i.get("grand_total") if i.get("grand_total") is not None else i.get("total") for i in invoices)
        out_rows = []
        for inv in page_rows:
            out_rows.append({
                "id": inv.get("id"),
                "invoice_number": inv.get("invoice_number") or inv.get("number"),
                "invoice_date": str(inv.get("invoice_date") or inv.get("date") or "")[:10],
                "supplier_name": inv.get("supplier_name") or inv.get("supplier") or inv.get("party_name") or "",
                "grand_total": inv.get("grand_total") if inv.get("grand_total") is not None else inv.get("total"),
                "payment_status": inv.get("payment_status") or inv.get("status") or "",
                "detail_href": _detail_href("purchase_invoice", inv),
            })
        body = {
            "columns": COLUMNS["purchase_invoice"],
            "rows": _add_row_no(out_rows, meta_page["page"], meta_page["page_size"]),
            **meta_page,
            "kpi_summary": {"count": meta_page["total"], "amount": amount, "label": "Faktur pembelian", "grain": "invoice", "status": "error" if amount is None else "complete"},
        }
    elif meta["source"] == "cash_movement":
        if not get_cash_movement:
            raise HTTPException(500, "Adapter Arus Kas belum siap")
        start = start_date or datetime.now(WIB).replace(day=1).date().isoformat()
        end = end_date or datetime.now(WIB).date().isoformat()
        try:
            payload = await get_cash_movement(db, start_date=start, end_date=end)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        rows = list(payload.get("items") or [])
        # This endpoint is all accounts, exactly like the KPI. Never silently
        # select a first account or interpret a legacy bank id as an account id.
        if cash_bank_id:
            raise HTTPException(400, "Rincian Arus Kas mencakup seluruh rekening; gunakan Mutasi untuk satu rekening.")
        if q:
            needle = q.casefold()
            rows = [r for r in rows if any(needle in str(r.get(k) or "").casefold()
                    for k in ("number", "account_name", "source_type", "description"))]
        total_debit = _sum_money(r.get("debit") for r in rows)
        total_credit = _sum_money(r.get("credit") for r in rows)
        if payload.get("cash_in") is None or payload.get("cash_out") is None:
            total_debit = total_credit = None
        page_rows, meta_page = _paginate(rows, page, page_size)
        body = {
            "columns": COLUMNS["cash"],
            "rows": _add_row_no(page_rows, meta_page["page"], meta_page["page_size"]),
            **meta_page,
            "kpi_summary": {
                "count": meta_page["total"], "total_debit": total_debit,
                "total_credit": total_credit,
                "net_change": None if total_debit is None or total_credit is None else float(Decimal(str(total_debit)) - Decimal(str(total_credit))),
                "status": payload.get("status"), "exclude_internal_transfer": True,
                "comparable_to_card": not bool(q),
                "period_cash_in": payload.get("cash_in"), "period_cash_out": payload.get("cash_out"),
                "detail": "Seluruh rekening kas/bank, jurnal sah, transfer internal dikecualikan. "
                          + ("Ringkasan mengikuti hasil pencarian." if q else "Populasi sama dengan kartu Arus Kas.")
                          + (" Ada reversal belum selesai." if payload.get("blocker_code") else ""),
                "label": "Arus kas eksternal", "grain": "journal",
            },
        }
    elif meta["source"] == "delivery_schedule":
        if not list_delivery_schedule_rows:
            raise HTTPException(500, "Adapter Jadwal Pengiriman belum siap")
        payload = await list_delivery_schedule_rows(db)
        rows = list(payload.get("rows") or [])
        want = status or meta.get("status") or ""
        if want:
            rows = [r for r in rows if str(r.get("status") or "").lower() == want]
        if q:
            needle = q.lower()
            rows = [
                r for r in rows
                if needle in str(r.get("order_number") or r.get("po_number") or "").lower()
                or needle in str(r.get("customer_name") or "").lower()
            ]
        page_rows, meta_page = _paginate(rows, page, page_size)
        out_rows = []
        for row in page_rows:
            oid = row.get("id") or row.get("order_id")
            out_rows.append({
                "id": oid,
                "order_id": oid,
                "po_number": row.get("order_number") or row.get("po_number") or "",
                "customer_name": row.get("customer_name") or "",
                "surat_jalan": row.get("surat_jalan") or row.get("sj_number") or row.get("shipment_number") or "",
                "tanggal_pengiriman": row.get("tanggal_pengiriman") or row.get("delivery_date") or "",
                "status": row.get("status") or "",
                "qty": row.get("qty") or row.get("quantity"),
                "detail_href": _detail_href("po", {"id": oid}),
            })
        body = {
            "columns": COLUMNS["sj"],
            "rows": _add_row_no(out_rows, meta_page["page"], meta_page["page_size"]),
            **meta_page,
            "kpi_summary": {"count": meta_page["total"], "label": "Baris SJ", "grain": "sj"},
        }
    elif meta["source"] == "profit_loss":
        if not get_profit_loss:
            raise HTTPException(500, "Adapter Laba/Rugi belum siap")
        start = start_date or datetime.now(WIB).replace(day=1).date().isoformat()
        end = end_date or datetime.now(WIB).date().isoformat()
        statement = await get_profit_loss(db, start_date=start, end_date=end)
        sections = statement.get("sections") or statement.get("groups") or []
        rows = []
        if isinstance(sections, list):
            for sec in sections:
                group = sec.get("report_group") or sec.get("key") or sec.get("group") or sec.get("label") or ""
                if meta.get("section") == "expense" and str(group).lower() not in {"expense", "exps", "beban", "operating_expense"}:
                    # Expense excludes revenue, COGS, and other report groups.
                    continue
                for line in sec.get("lines") or sec.get("accounts") or sec.get("items") or []:
                    rows.append({
                        "account_id": line.get("account_id") or line.get("id"),
                        "account_code": line.get("code") or line.get("account_code"),
                        "account_name": line.get("name") or line.get("account_name"),
                        "group": group,
                        "amount": line.get("amount") if line.get("amount") is not None else line.get("balance", line.get("total")),
                        "detail_href": _detail_href("ledger", line),
                    })
        if not sections and isinstance(statement.get("lines"), list):
            for line in statement["lines"]:
                if meta.get("section") == "expense" and line.get("report_group") != "expense":
                    continue
                rows.append({
                    "account_id": line.get("account_id"),
                    "account_code": line.get("code"),
                    "account_name": line.get("name"),
                    "group": line.get("report_group") or "",
                    "amount": line.get("amount"),
                    "detail_href": _detail_href("ledger", line),
                })
        if q:
            needle = q.lower()
            rows = [
                r for r in rows
                if needle in str(r.get("account_code") or "").lower()
                or needle in str(r.get("account_name") or "").lower()
            ]
        page_rows, meta_page = _paginate(rows, page, page_size)
        totals = statement.get("totals") or {}
        expense_amount = _sum_money(r.get("amount") for r in rows) if key == "expense" else None
        body = {
            "columns": COLUMNS["pl"],
            "rows": _add_row_no(page_rows, meta_page["page"], meta_page["page_size"]),
            **meta_page,
            "kpi_summary": {
                "count": meta_page["total"],
                "net_profit": totals.get("net_profit"),
                "revenue_total": totals.get("revenue_total"),
                "expense_total": totals.get("expense_total"),
                "label": "Baris laporan",
                "grain": "account",
                **({
                    "amount": expense_amount,
                    "status": "error" if expense_amount is None else "complete",
                    "detail": "Beban operasional dari seluruh akun hasil filter; nilai negatif dipertahankan. Harga pokok tidak termasuk.",
                } if key == "expense" else {
                    "detail": "Laba bersih mengikuti seluruh periode laporan; pencarian hanya menyaring daftar akun.",
                }),
            },
        }
    elif meta["source"] == "min_stock":
        from services.min_stock_widget import build_min_stock_snapshot
        snap = await build_min_stock_snapshot(
            db,
            branch_id=str((branch_scope or {}).get("branch_id") or "") or None,
            warehouse_id=None,
            page=page,
            limit=page_size,
        )
        rows = list(snap.get("items") or [])
        if q:
            needle = q.lower()
            rows = [
                r for r in rows
                if needle in str(r.get("item_name") or "").lower()
                or needle in str(r.get("item_code") or "").lower()
                or needle in str(r.get("warehouse_name") or "").lower()
            ]
        body = {
            "columns": COLUMNS["min_stock"],
            "rows": _add_row_no(rows, snap.get("page") or page, snap.get("limit") or page_size),
            "total": snap.get("total") or 0,
            "page": snap.get("page") or page,
            "page_size": snap.get("limit") or page_size,
            "page_count": snap.get("page_count") or 0,
            "kpi_summary": {
                "count": snap.get("violation_count") or 0,
                "label": "Kombinasi barang–gudang di/bawah minimum",
                "grain": "item_warehouse",
                "unevaluable_count": snap.get("unevaluable_count") or 0,
                "status": snap.get("status"),
                "detail": snap.get("detail"),
            },
        }
    else:
        raise HTTPException(404, "Sumber rincian tidak tersedia")

    return {
        "dashboard": dash,
        "widget": key,
        "title": meta["title"],
        "timezone": "Asia/Jakarta",
        "generated_at": generated_at,
        "period": {"start_date": start_date or None, "end_date": end_date or None},
        "filters": {"q": q or "", "unit": unit or "", "status": status or meta.get("status") or ""},
        "read_only": True,
        "writes": 0,
        **body,
    }

