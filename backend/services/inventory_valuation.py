"""Read-only inventory valuation view over R2 physical stock and canonical HPP.

The view never recalculates HPP and never writes stock, purchasing, pricing,
or accounting data. Stock Fisik is the R2 physical balance sum for ONE
selected/effective branch (default YGY). product_stock_state is not the
official quantity. HPP unit cost remains company-wide — never branch-specific.

Inventory HPP comes only from acquisition cost (PO / Goods Receipt / WMA /
approved cutover / first-GR bootstrap). Customer Pricing must never become
inventory HPP fallback.

Scope note: branch qty awareness here is Purchasing/Inventory recovery only;
it does not claim full Sales/Production/Delivery enterprise isolation.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
from io import BytesIO
from math import ceil
from typing import Any, Dict, List, Optional

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill

from services.hpp_cutover import (
    STATUS_PENDING_VALUATION,
    get_active_cutover,
    list_reconciliation,
)
from services.hpp_valuation import LAYERS, SETTINGS_KEY, VERSIONS
from services.inventory_accounting import ITEM_TYPE_SERVICE
from services.purchase_common import money_round

STATUS_OK = "OK"
STATUS_MISSING_COST = "MISSING_COST"
STATUS_NEEDS_COST = "NEEDS_COST"
STATUS_NEEDS_UOM = "NEEDS_UOM"
STATUS_QTY_MISMATCH = "QTY_MISMATCH"
STATUS_FAILED = "FAILED"

SUPPORTED_STATUSES = {
    STATUS_OK,
    STATUS_MISSING_COST,
    STATUS_NEEDS_COST,
    STATUS_NEEDS_UOM,
    STATUS_QTY_MISMATCH,
    STATUS_FAILED,
    STATUS_PENDING_VALUATION,
}
MISSING_STATUSES = {STATUS_MISSING_COST, STATUS_NEEDS_COST, STATUS_PENDING_VALUATION}
PROBLEM_STATUSES = {STATUS_NEEDS_UOM, STATUS_QTY_MISMATCH, STATUS_FAILED}
SORT_KEYS = {
    "code",
    "name",
    "unit",
    "stock_fisik",
    "current_hpp",
    "inventory_value",
    "hpp_status",
    "updated_at",
}


def _dec(value: Any) -> Decimal:
    try:
        return Decimal(str(value if value is not None else 0))
    except (InvalidOperation, TypeError, ValueError):
        return Decimal("0")


def _layer_sort_key(row: dict) -> tuple:
    return (
        str(row.get("recognized_at") or ""),
        str(row.get("source_id") or ""),
        str(row.get("source_line_id") or ""),
        str(row.get("layer_id") or ""),
    )


def _latest_text(*values: Any) -> Optional[str]:
    texts = [str(value) for value in values if value]
    return max(texts) if texts else None


async def _canonical_positions(db) -> tuple[Dict[str, dict], Optional[dict], Optional[dict]]:
    settings = await db.app_settings.find_one({"key": SETTINGS_KEY}, {"_id": 0})
    active_version = (settings or {}).get("active_version")
    version = None
    positions: Dict[str, dict] = {}
    if active_version:
        version = await db[VERSIONS].find_one({"id": active_version}, {"_id": 0})
        layers = await db[LAYERS].find(
            {"calculation_version": active_version},
            {"_id": 0},
        ).to_list(20000)
        latest: Dict[str, dict] = {}
        for layer in layers:
            keys = [
                str(layer.get("master_item_id") or "").strip(),
                str(layer.get("item_identity") or "").strip(),
            ]
            for key in [value for value in keys if value]:
                if key not in latest or _layer_sort_key(layer) > _layer_sort_key(latest[key]):
                    latest[key] = layer
        for key, layer in latest.items():
            qty = float(layer.get("remaining_qty") or 0)
            hpp_raw = money_round(layer.get("unit_cost") or 0)
            hpp = hpp_raw if hpp_raw > 0 else None
            positions[key] = {
                "valuation_qty": qty,
                "current_hpp": hpp if hpp and hpp > 0 else None,
                "valuation_value": (
                    money_round(layer.get("total_cost") or 0)
                    if qty >= 0
                    else None
                ),
                "hpp_status": STATUS_OK if hpp and hpp > 0 else STATUS_MISSING_COST,
                "reconciliation_reason": None,
                "updated_at": layer.get("recognized_at"),
                "hpp_source": "PURCHASING_MOVING_AVERAGE",
            }
    return positions, settings, version


async def build_inventory_valuation_rows(db, *, branch_id: Optional[str] = None) -> dict:
    """Build all rows before search/filter/pagination.

    Physical qty is for ONE branch (default operational YGY when omitted).
    HPP / unit cost remains company-wide — never invent branch-specific HPP.
    """
    masters = await db.master_items.find(
        {
            "is_active": {"$ne": False},
            "item_type": {"$ne": ITEM_TYPE_SERVICE},
        },
        {"_id": 0},
    ).to_list(20000)
    states = await db.product_stock_state.find({}, {"_id": 0}).to_list(20000)
    state_by_mid = {
        str(row.get("master_item_id") or ""): row
        for row in states
        if row.get("master_item_id")
    }
    positions, settings, version = await _canonical_positions(db)
    cutover = await get_active_cutover(db)
    reconciliation_rows = await list_reconciliation(db, cutover.get("cutover_id")) if cutover else []
    reconciliation_by_mid = {
        str(row.get("master_item_id") or ""): row
        for row in reconciliation_rows
        if row.get("master_item_id")
    }
    from services.financial_inventory_opening import financial_display_by_item
    from services.physical_stock import operational_physical_qty_by_item

    financial_by_item = await financial_display_by_item(db)
    physical_by_item = await operational_physical_qty_by_item(
        db,
        [str(master.get("id") or "") for master in masters if master.get("id")],
        branch_id=branch_id,
    )

    rows: List[dict] = []
    for master in masters:
        mid = str(master.get("id") or "").strip()
        aid = str(master.get("accurate_item_id") or "").strip()
        state = state_by_mid.get(mid) or {}
        stock = float(physical_by_item.get(mid, 0) or 0)
        if stock < 0:
            stock = 0.0
        position = reconciliation_by_mid.get(mid)
        if not position:
            position = positions.get(mid) or positions.get(f"a:{aid}") or {}

        valuation_qty = float(position.get("valuation_qty") or 0)
        difference = float(_dec(stock) - _dec(valuation_qty))
        current_hpp_raw = position.get("current_hpp")
        current_hpp = (
            money_round(current_hpp_raw)
            if current_hpp_raw is not None and _dec(current_hpp_raw) > 0
            else None
        )
        original_status = str(position.get("hpp_status") or STATUS_MISSING_COST)
        if original_status not in SUPPORTED_STATUSES:
            original_status = STATUS_FAILED

        # Never invent cost from Customer Pricing / published HPP.
        canonical_exists = current_hpp is not None

        if current_hpp is None:
            if original_status in {
                STATUS_NEEDS_COST,
                STATUS_NEEDS_UOM,
                STATUS_FAILED,
                STATUS_PENDING_VALUATION,
            }:
                status = original_status
            else:
                status = STATUS_MISSING_COST
        else:
            status = STATUS_OK if original_status == STATUS_QTY_MISMATCH else original_status

        inventory_value = None
        if stock == 0 and status == STATUS_OK and current_hpp is not None:
            inventory_value = 0.0
        elif stock > 0 and status == STATUS_OK and current_hpp is not None:
            exact_average = position.get("exact_average")
            if exact_average is not None and _dec(exact_average) > 0:
                inventory_value = money_round(_dec(stock) * _dec(exact_average))
            elif position.get("valuation_value") is not None and abs(difference) <= 0.0000001:
                inventory_value = money_round(position.get("valuation_value"))
            else:
                inventory_value = money_round(_dec(stock) * _dec(current_hpp))

        rows.append({
            "master_item_id": mid,
            "accurate_item_id": aid,
            "item_code": master.get("item_code") or "",
            "item_name": master.get("item_name") or master.get("item_code") or aid or mid,
            "unit": state.get("base_uom") or master.get("unit") or "",
            "stock_fisik": stock,
            "valuation_qty": valuation_qty,
            "difference": difference,
            "current_hpp": current_hpp,
            "inventory_value": inventory_value,
            "hpp_status": status,
            "hpp_reason": (
                position.get("reconciliation_reason")
                or (
                    original_status
                    if original_status != status
                    and original_status in {
                        STATUS_MISSING_COST,
                        STATUS_NEEDS_COST,
                        STATUS_NEEDS_UOM,
                        STATUS_FAILED,
                        STATUS_PENDING_VALUATION,
                    }
                    else None
                )
            ),
            "hpp_source": (
                position.get("hpp_source")
                or ("PURCHASING_MOVING_AVERAGE" if canonical_exists else None)
            ),
            "published_pricing_version_id": None,
            "valuation_status": status,
            "financial_posting_status": (financial_by_item.get(mid) or {}).get("financial_posting_status") or "UNPOSTED",
            "financial_opening_qty": (financial_by_item.get(mid) or {}).get("financial_opening_qty"),
            "financial_opening_unit_cost": (financial_by_item.get(mid) or {}).get("financial_opening_unit_cost"),
            "financial_opening_total": (financial_by_item.get(mid) or {}).get("financial_opening_total"),
            "financial_opening_source": (financial_by_item.get(mid) or {}).get("financial_opening_source"),
            "financial_effective_date": (financial_by_item.get(mid) or {}).get("financial_effective_date"),
            "financial_journal_id": (financial_by_item.get(mid) or {}).get("financial_journal_id"),
            "financial_journal_origin": (financial_by_item.get(mid) or {}).get("financial_journal_origin"),
            "financial_blocker": (financial_by_item.get(mid) or {}).get("financial_blocker"),
            "financial_blocker_message": (financial_by_item.get(mid) or {}).get("financial_blocker_message"),
            "published_is_book_value": False,
            "reconciliation_difference": difference,
            "reconciliation_explanation": (
                "Belum ada HPP akuisisi kanonik. Nilai estimasi belum tersedia."
                if not canonical_exists
                else "Selisih stok fisik terhadap qty valuasi rata-rata pembelian."
            ),
            "updated_at": _latest_text(
                position.get("updated_at"),
                state.get("updated_at"),
                (version or {}).get("recalculated_at"),
            ),
        })

    summary = {
        "total_inventory_value": money_round(sum(
            _dec(row["inventory_value"])
            for row in rows
            if row["hpp_status"] == STATUS_OK and row["inventory_value"] is not None
        )),
        "valued_items": sum(1 for row in rows if row["hpp_status"] == STATUS_OK),
        "items_without_hpp": sum(1 for row in rows if row["current_hpp"] is None),
        "problem_items": sum(1 for row in rows if row["hpp_status"] in PROBLEM_STATUSES),
    }
    return {
        "rows": rows,
        "summary": summary,
        "method": (settings or {}).get("method"),
        "active_version": (settings or {}).get("active_version"),
        "cutover_id": (cutover or {}).get("cutover_id"),
    }


def _lookup_payload(
    master_item_id: str,
    *,
    item: Optional[dict] = None,
    reason_code: Optional[str] = None,
    reason: Optional[str] = None,
    item_type: Optional[str] = None,
) -> dict:
    """Explain a miss without inventing a zero valuation."""
    covered = item is not None and not reason_code
    return {
        "found": item is not None,
        "covered": covered,
        "master_item_id": master_item_id,
        "matched_by": "master_item_id" if covered else None,
        "item_type": item_type,
        "reason_code": reason_code,
        "reason": reason,
        "item": item,
        "inventory_value": None if item is None else item.get("inventory_value"),
        "book_value_per_item": None,
        "writes": 0,
    }


def _uncovered_master(master: Optional[dict]) -> tuple:
    if not master:
        return "NOT_FOUND", "Item tidak ditemukan.", None
    item_type = master.get("item_type")
    if master.get("is_active") is False:
        return (
            "INACTIVE",
            "Item tidak aktif dan tidak masuk daftar Nilai Persediaan.",
            item_type,
        )
    normalized = str(item_type or "").strip().upper()
    if normalized == ITEM_TYPE_SERVICE:
        return (
            "NOT_COVERED",
            "Item berjenis Jasa tidak tercakup dalam daftar Nilai Persediaan.",
            item_type,
        )
    if not str(master.get("id") or "").strip():
        return "MISSING_MASTER_ITEM_ID", "Item tidak punya ID master yang stabil.", item_type
    return "NOT_COVERED", "Item tidak tercakup dalam daftar Nilai Persediaan.", item_type


async def lookup_inventory_valuation_item(
    db, master_item_id: str, *, branch_id: Optional[str] = None,
) -> dict:
    """Read one builder row by exact master_item_id.

    The row is the object produced by build_inventory_valuation_rows. This
    helper does not reprice stock, HPP, or book value, and it does not match
    name, code, or accurate_item_id. The catalog filter stays unchanged:
    active non-SERVICE rows, including NON_INVENTORY, are still returned.
    """
    mid = str(master_item_id or "").strip()
    if not mid:
        return _lookup_payload(
            "",
            reason_code="MISSING_MASTER_ITEM_ID",
            reason="Item tidak punya ID master yang stabil.",
        )
    view = await build_inventory_valuation_rows(db, branch_id=branch_id)
    matches = [
        row for row in view["rows"]
        if str(row.get("master_item_id") or "") == mid
    ]
    if len(matches) > 1:
        return _lookup_payload(
            mid,
            reason_code="NOT_FOUND",
            reason="Lebih dari satu baris memakai ID master yang sama.",
        )
    if len(matches) == 1:
        return _lookup_payload(mid, item=matches[0])
    master = await db.master_items.find_one({"id": mid}, {"_id": 0, "id": 1, "item_type": 1, "is_active": 1})
    code, reason, item_type = _uncovered_master(master)
    return _lookup_payload(mid, reason_code=code, reason=reason, item_type=item_type)


def _matches_filter(row: dict, status_filter: str) -> bool:
    selected = str(status_filter or "all").strip().lower()
    if selected == "ok":
        return row.get("hpp_status") == STATUS_OK
    if selected == "missing":
        return row.get("current_hpp") is None or row.get("hpp_status") in MISSING_STATUSES
    if selected == "problem":
        return row.get("hpp_status") in PROBLEM_STATUSES
    return True


def filter_sort_all(
    rows: List[dict],
    *,
    q: str = "",
    status_filter: str = "all",
    sort_by: str = "name",
    sort_dir: str = "asc",
) -> List[dict]:
    """Return every matching row in canonical UI/export order.

    Export deliberately uses this helper directly so UI pagination limits
    never truncate the workbook.
    """
    needle = str(q or "").strip().lower()
    filtered = [
        row for row in rows
        if (
            not needle
            or needle in str(row.get("item_code") or "").lower()
            or needle in str(row.get("item_name") or "").lower()
        )
        and _matches_filter(row, status_filter)
    ]
    key_name = sort_by if sort_by in SORT_KEYS else "name"

    def key(row: dict):
        mapping = {
            "code": row.get("item_code") or "",
            "name": row.get("item_name") or "",
            "unit": row.get("unit") or "",
            "stock_fisik": row.get("stock_fisik"),
            "current_hpp": row.get("current_hpp"),
            "inventory_value": row.get("inventory_value"),
            "hpp_status": row.get("hpp_status") or "",
            "updated_at": row.get("updated_at") or "",
        }
        value = mapping[key_name]
        if isinstance(value, str):
            return (value.lower(), row.get("item_code") or "")
        return (value is None, value if value is not None else 0, row.get("item_code") or "")

    filtered.sort(key=key, reverse=str(sort_dir).lower() == "desc")
    return filtered


def filter_sort_page(
    rows: List[dict],
    *,
    q: str = "",
    status_filter: str = "all",
    sort_by: str = "name",
    sort_dir: str = "asc",
    page: int = 1,
    page_size: int = 50,
) -> dict:
    filtered = filter_sort_all(
        rows,
        q=q,
        status_filter=status_filter,
        sort_by=sort_by,
        sort_dir=sort_dir,
    )
    size = min(max(int(page_size or 50), 1), 200)
    count = len(filtered)
    page_count = max(1, ceil(count / size))
    current_page = min(max(int(page or 1), 1), page_count)
    start = (current_page - 1) * size
    return {
        "items": filtered[start:start + size],
        "total": count,
        "page": current_page,
        "page_size": size,
        "page_count": page_count,
    }


def export_xlsx(rows: List[dict]) -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.title = "Nilai Persediaan"
    headers = [
        "Kode",
        "Nama",
        "Satuan",
        "Stock Fisik",
        "HPP Rata-Rata",
        "Nilai Persediaan",
        "Status HPP",
        "Sumber HPP",
        "Posting Finansial",
        "Opening Disetujui",
        "Selisih",
        "Penjelasan Selisih",
        "Hambatan",
        "Terakhir Update",
    ]
    ws.append(headers)
    header_font = Font(bold=True, color="FFFFFF")
    header_fill = PatternFill("solid", fgColor="17313E")
    for index in range(1, len(headers) + 1):
        cell = ws.cell(row=1, column=index)
        cell.font = header_font
        cell.fill = header_fill
        cell.alignment = Alignment(horizontal="left", vertical="center")
    for row in rows:
        ws.append([
            row.get("item_code") or "",
            row.get("item_name") or "",
            row.get("unit") or "",
            row.get("stock_fisik"),
            row.get("current_hpp"),
            row.get("inventory_value"),
            row.get("hpp_status") or "",
            row.get("hpp_source") or "",
            row.get("financial_posting_status") or "",
            row.get("financial_opening_total"),
            row.get("reconciliation_difference"),
            row.get("reconciliation_explanation") or "",
            row.get("financial_blocker_message") or row.get("hpp_reason") or "",
            row.get("updated_at") or "",
        ])
    for row_index in range(2, ws.max_row + 1):
        ws.cell(row=row_index, column=4).number_format = "#,##0.######"
        ws.cell(row=row_index, column=5).number_format = '"Rp " #,##0.00'
        ws.cell(row=row_index, column=6).number_format = '"Rp " #,##0'
    widths = [16, 34, 12, 16, 18, 22, 18, 28, 18, 18, 14, 42, 28, 24]
    for index, width in enumerate(widths, start=1):
        ws.column_dimensions[chr(64 + index)].width = width
    ws.auto_filter.ref = ws.dimensions
    ws.freeze_panes = "A2"
    output = BytesIO()
    wb.save(output)
    return output.getvalue()
