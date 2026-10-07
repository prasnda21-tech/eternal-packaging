"""Read-only Purchasing HPP monitoring.

Canonical cost stays Goods Receipt + PO line unit_price, weighted moving
average, as rebuilt from the active cutover baseline and recognition events.
This module never writes, never recalculates, and never reads Harga Customer
or published HPP fallback.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from io import BytesIO
from typing import Any, Dict, List, Optional

from fastapi import HTTPException
from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill

from services.hpp_cutover import (
    BASELINES,
    EVENTS,
    SOURCE_FIRST_GR_UNIT_COST,
    SOURCE_GOODS_RECEIPT,
    SOURCE_GOODS_RECEIPT_REVERSAL,
    SOURCE_OPENING,
    SOURCE_PURCHASE_RETURN,
    SOURCE_PURCHASE_RETURN_REVERSAL,
    SOURCE_STOCK_OPNAME_ADJUSTMENT,
    STATUS_FAILED,
    STATUS_NEEDS_COST,
    STATUS_NEEDS_UOM,
    STATUS_PENDING_VALUATION,
    STATUS_RECOGNIZED,
    _events_from_baseline_and_recognition,
    get_active_cutover,
)
from services.hpp_valuation import InsufficientCostLayerError, moving_average_from_events
from services.inventory_accounting import ITEM_TYPE_SERVICE
from services.physical_stock import operational_physical_qty_by_item
from services.purchase_common import money_round, qty_round

STATUS_ADA_HPP = "ADA_HPP"
STATUS_BELUM_ADA_HPP = "BELUM_ADA_HPP"

STATUS_LABELS = {
    STATUS_ADA_HPP: "Ada HPP",
    STATUS_BELUM_ADA_HPP: "Belum ada HPP",
    STATUS_PENDING_VALUATION: "Menunggu valuasi",
    STATUS_NEEDS_COST: "Butuh harga PO",
    STATUS_NEEDS_UOM: "Butuh satuan",
    STATUS_FAILED: "Gagal valuasi",
}

SOURCE_LABELS = {
    SOURCE_OPENING: "HPP Awal",
    SOURCE_GOODS_RECEIPT: "Penerimaan Barang",
    SOURCE_GOODS_RECEIPT_REVERSAL: "Pembatalan Penerimaan",
    SOURCE_PURCHASE_RETURN: "Retur Pembelian",
    SOURCE_PURCHASE_RETURN_REVERSAL: "Pembatalan Retur",
    SOURCE_STOCK_OPNAME_ADJUSTMENT: "Stock Opname",
}

COST_LABELS = {
    SOURCE_OPENING: "HPP Awal",
    SOURCE_GOODS_RECEIPT: "Harga Pembelian",
    SOURCE_GOODS_RECEIPT_REVERSAL: "Harga penerimaan (reversal)",
    SOURCE_PURCHASE_RETURN: "Harga penerimaan asal",
    SOURCE_PURCHASE_RETURN_REVERSAL: "Harga penerimaan asal",
    SOURCE_STOCK_OPNAME_ADJUSTMENT: "HPP Berlaku",
}


def _dec(value: Any) -> Decimal:
    try:
        return Decimal(str(value if value is not None else 0))
    except (InvalidOperation, TypeError, ValueError):
        return Decimal("0")


def _money(value: Any) -> Optional[float]:
    if value is None or value == "":
        return None
    amount = money_round(value)
    if _dec(amount) == 0 and _dec(value) == 0:
        return 0.0
    return amount


def _positive_money(value: Any) -> Optional[float]:
    amount = _money(value)
    if amount is None or _dec(amount) <= 0:
        return None
    return amount


def _qty(value: Any) -> Optional[float]:
    if value is None or value == "":
        return None
    return qty_round(value)


def _pct(delta: Optional[float], before: Optional[float]) -> Optional[float]:
    if delta is None or before is None or _dec(before) <= 0:
        return None
    return float((_dec(delta) / _dec(before) * Decimal("100")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def _status_label(code: str) -> str:
    return STATUS_LABELS.get(code, code or "")


def _source_label(source_type: str, *, bootstrap: bool = False) -> str:
    if source_type == SOURCE_OPENING and bootstrap:
        return "HPP Awal (penerimaan pertama)"
    return SOURCE_LABELS.get(source_type, source_type or "")


def _cost_label(source_type: str, *, bootstrap: bool = False) -> str:
    if source_type == SOURCE_OPENING and bootstrap:
        return "HPP Awal (penerimaan pertama)"
    return COST_LABELS.get(source_type, "Cost Event")


def _parse_gr_id(source_key: Any, canonical_source_id: Any = None) -> str:
    if canonical_source_id:
        return str(canonical_source_id).strip()
    text = str(source_key or "")
    if text.startswith("gr:") or text.startswith("gr-reversal:"):
        parts = text.split(":")
        if len(parts) >= 2:
            return parts[1]
    return ""


def _parse_opname_session_id(source_key: Any) -> str:
    text = str(source_key or "")
    if text.startswith("stock-opname:"):
        parts = text.split(":")
        if len(parts) >= 2:
            return parts[1]
    return ""


def _parse_return_id(source_key: Any, purchase_return_id: Any = None) -> str:
    if purchase_return_id:
        return str(purchase_return_id).strip()
    text = str(source_key or "")
    for prefix in ("purchase-return-reversal:", "purchase-return:"):
        if text.startswith(prefix):
            parts = text.split(":")
            if len(parts) >= 2:
                return parts[1]
    return ""


def _row_status(current_hpp: Optional[float], event_statuses: set) -> str:
    if current_hpp is not None:
        return STATUS_ADA_HPP
    if STATUS_PENDING_VALUATION in event_statuses:
        return STATUS_PENDING_VALUATION
    if STATUS_NEEDS_COST in event_statuses:
        return STATUS_NEEDS_COST
    if STATUS_NEEDS_UOM in event_statuses:
        return STATUS_NEEDS_UOM
    if STATUS_FAILED in event_statuses:
        return STATUS_FAILED
    return STATUS_BELUM_ADA_HPP


def _empty_purchase_fields() -> dict:
    return {
        "latest_gr_id": None,
        "latest_gr_number": None,
        "latest_gr_date": None,
        "latest_po_number": None,
        "latest_supplier": None,
        "latest_gr_qty": None,
        "latest_purchase_unit_cost": None,
        "hpp_before_latest_gr": None,
        "hpp_after_latest_gr": None,
        "price_vs_hpp_delta": None,
        "price_vs_hpp_pct": None,
        "hpp_current_diverges_from_last_gr": False,
    }


async def _load_docs(db, collection: str, ids: List[str]) -> Dict[str, dict]:
    clean = [value for value in {str(item or "").strip() for item in ids} if value]
    if not clean:
        return {}
    rows = await db[collection].find({"id": {"$in": clean}}, {"_id": 0}).to_list(20000)
    return {str(row.get("id") or ""): row for row in rows if row.get("id")}


def _history_sort_key(row: dict) -> tuple:
    return (
        str(row.get("recognized_at") or row.get("document_date") or ""),
        str(row.get("source_id") or ""),
        str(row.get("source_type") or ""),
    )


def _snapshot_history_row(
    snap: dict,
    *,
    hpp_before: Optional[float],
    receipts: Dict[str, dict],
    returns: Dict[str, dict],
    opnames: Dict[str, dict],
    bootstrap: bool,
) -> dict:
    source_type = str(snap.get("source_type") or "")
    incoming = _positive_money(snap.get("incoming_unit_cost"))
    hpp_after = _positive_money(snap.get("unit_cost"))
    delta = None
    if incoming is not None and hpp_before is not None:
        delta = money_round(_dec(incoming) - _dec(hpp_before))
    receipt_id = _parse_gr_id(snap.get("source_id") or snap.get("source_key"), snap.get("canonical_source_id") or snap.get("goods_receipt_id"))
    receipt = receipts.get(receipt_id) or {}
    return_id = _parse_return_id(snap.get("source_id") or snap.get("source_key"), snap.get("purchase_return_id"))
    ret = returns.get(return_id) or {}
    if not receipt_id:
        receipt_id = str(ret.get("purchase_goods_receipt_id") or snap.get("goods_receipt_id") or "")
        receipt = receipts.get(receipt_id) or receipt
    opname = opnames.get(_parse_opname_session_id(snap.get("source_id") or snap.get("source_key"))) or {}
    document_date = (
        str(receipt.get("receipt_date") or "")[:10]
        or str(ret.get("return_date") or "")[:10]
        or str(opname.get("confirmed_at") or opname.get("created_at") or "")[:10]
        or str(snap.get("recognized_at") or "")[:10]
        or None
    )
    supplier = receipt.get("supplier") or ret.get("supplier_name") or ret.get("supplier") or None
    po_number = receipt.get("po_number") or ret.get("po_number") or None
    gr_number = receipt.get("receipt_number") or None
    return {
        "recognized_at": snap.get("recognized_at"),
        "document_date": document_date,
        "source_type": source_type,
        "source_type_label": _source_label(source_type, bootstrap=bootstrap and source_type == SOURCE_OPENING),
        "status": STATUS_RECOGNIZED,
        "supplier": supplier,
        "po_number": po_number,
        "gr_number": gr_number,
        "source_id": snap.get("source_id") or snap.get("layer_id") or snap.get("event_id"),
        "qty": _qty(snap.get("recognized_qty")),
        "incoming_unit_cost": incoming,
        "incoming_cost_label": _cost_label(source_type, bootstrap=bootstrap and source_type == SOURCE_OPENING),
        "hpp_before": hpp_before,
        "hpp_after": hpp_after,
        "price_delta": delta,
        "price_pct": _pct(delta, hpp_before),
    }


def _pending_history_row(
    rec: dict,
    *,
    receipts: Dict[str, dict],
    returns: Dict[str, dict],
    opnames: Dict[str, dict],
) -> dict:
    source_type = str(rec.get("source_type") or SOURCE_GOODS_RECEIPT)
    status = str(rec.get("status") or "")
    incoming = _positive_money(rec.get("unit_cost"))
    receipt_id = _parse_gr_id(rec.get("source_key"), rec.get("canonical_source_id") or rec.get("goods_receipt_id"))
    receipt = receipts.get(receipt_id) or {}
    return_id = str(rec.get("purchase_return_id") or "")
    ret = returns.get(return_id) or {}
    if not receipt_id:
        receipt_id = str(ret.get("purchase_goods_receipt_id") or rec.get("goods_receipt_id") or "")
        receipt = receipts.get(receipt_id) or receipt
    opname = opnames.get(_parse_opname_session_id(rec.get("source_key"))) or {}
    document_date = (
        str(rec.get("receipt_date") or receipt.get("receipt_date") or "")[:10]
        or str(ret.get("return_date") or "")[:10]
        or str(opname.get("confirmed_at") or opname.get("created_at") or "")[:10]
        or str(rec.get("recognized_at") or rec.get("created_at") or "")[:10]
        or None
    )
    return {
        "recognized_at": rec.get("recognized_at") or rec.get("created_at"),
        "document_date": document_date,
        "source_type": source_type,
        "source_type_label": _source_label(source_type),
        "status": status,
        "supplier": receipt.get("supplier") or ret.get("supplier_name") or ret.get("supplier") or None,
        "po_number": receipt.get("po_number") or ret.get("po_number") or None,
        "gr_number": receipt.get("receipt_number") or None,
        "source_id": rec.get("source_key") or rec.get("event_id"),
        "qty": _qty(rec.get("qty")),
        "incoming_unit_cost": incoming,
        "incoming_cost_label": _cost_label(source_type),
        "hpp_before": None,
        "hpp_after": None,
        "price_delta": None,
        "price_pct": None,
    }


async def _catalog(db) -> List[dict]:
    return await db.master_items.find(
        {"is_active": {"$ne": False}, "item_type": {"$ne": ITEM_TYPE_SERVICE}},
        {"_id": 0},
    ).to_list(20000)


async def _build_item_models(db) -> Dict[str, dict]:
    masters = await _catalog(db)
    states = await db.product_stock_state.find({}, {"_id": 0, "master_item_id": 1, "base_uom": 1}).to_list(20000)
    unit_by_mid = {
        str(row.get("master_item_id") or ""): str(row.get("base_uom") or "")
        for row in states
        if row.get("master_item_id")
    }
    physical = await operational_physical_qty_by_item(
        db,
        [str(master.get("id") or "") for master in masters if master.get("id")],
    )
    cutover = await get_active_cutover(db)
    baselines: List[dict] = []
    recognitions: List[dict] = []
    snapshots: List[dict] = []
    if cutover:
        baselines = await db[BASELINES].find({"cutover_id": cutover["cutover_id"]}, {"_id": 0}).to_list(5000)
        recognitions = await db[EVENTS].find({"cutover_id": cutover["cutover_id"]}, {"_id": 0}).to_list(20000)
        events = _events_from_baseline_and_recognition(cutover, baselines, recognitions)
        try:
            snapshots, _cogs, _state = moving_average_from_events(events)
        except InsufficientCostLayerError:
            snapshots = []

    rec_by_event_id = {str(row.get("event_id") or ""): row for row in recognitions if row.get("event_id")}
    snaps_by_item: Dict[str, List[dict]] = {}
    for snap in snapshots:
        mid = str(snap.get("master_item_id") or "")
        extra = rec_by_event_id.get(str(snap.get("layer_id") or snap.get("event_id") or "")) or {}
        if extra:
            snap = {
                **snap,
                "purchase_return_id": snap.get("purchase_return_id") or extra.get("purchase_return_id"),
                "goods_receipt_id": snap.get("goods_receipt_id") or extra.get("goods_receipt_id") or extra.get("canonical_source_id"),
                "source_key": extra.get("source_key") or snap.get("source_id"),
            }
        if mid:
            snaps_by_item.setdefault(mid, []).append(snap)

    recs_by_item: Dict[str, List[dict]] = {}
    receipt_ids: List[str] = []
    return_ids: List[str] = []
    opname_ids: List[str] = []
    for rec in recognitions:
        mid = str(rec.get("master_item_id") or "")
        if mid:
            recs_by_item.setdefault(mid, []).append(rec)
        receipt_ids.append(_parse_gr_id(rec.get("source_key"), rec.get("canonical_source_id") or rec.get("goods_receipt_id")))
        return_ids.append(_parse_return_id(rec.get("source_key"), rec.get("purchase_return_id")))
        opname_ids.append(_parse_opname_session_id(rec.get("source_key")))
    for item_snaps in snaps_by_item.values():
        for snap in item_snaps:
            receipt_ids.append(_parse_gr_id(snap.get("source_id") or snap.get("source_key"), snap.get("canonical_source_id") or snap.get("goods_receipt_id")))
            return_ids.append(_parse_return_id(snap.get("source_id") or snap.get("source_key"), snap.get("purchase_return_id")))
            opname_ids.append(_parse_opname_session_id(snap.get("source_id") or snap.get("source_key")))

    receipts = await _load_docs(db, "purchase_goods_receipts", receipt_ids)
    returns = await _load_docs(db, "purchase_returns", return_ids)
    opnames = await _load_docs(db, "stock_opname_sessions", opname_ids)
    baseline_by_mid = {str(row.get("master_item_id") or ""): row for row in baselines}

    models: Dict[str, dict] = {}
    for master in masters:
        mid = str(master.get("id") or "").strip()
        if not mid:
            continue
        stock = float(physical.get(mid, 0) or 0)
        if stock < 0:
            stock = 0.0
        item_snaps = snaps_by_item.get(mid) or []
        item_recs = recs_by_item.get(mid) or []
        bootstrap = str((baseline_by_mid.get(mid) or {}).get("provenance_source") or "") == SOURCE_FIRST_GR_UNIT_COST
        event_statuses = {str(row.get("status") or "") for row in item_recs}

        current_hpp = None
        if item_snaps:
            latest = item_snaps[-1]
            current_hpp = _positive_money(latest.get("unit_cost"))

        purchase = _empty_purchase_fields()
        gr_snaps = [row for row in item_snaps if str(row.get("source_type") or "") == SOURCE_GOODS_RECEIPT]
        if gr_snaps:
            latest_gr = gr_snaps[-1]
            gr_index = item_snaps.index(latest_gr)
            hpp_before = _positive_money(item_snaps[gr_index - 1].get("unit_cost")) if gr_index > 0 else None
            hpp_after = _positive_money(latest_gr.get("unit_cost"))
            purchase_cost = _positive_money(latest_gr.get("incoming_unit_cost"))
            delta = None
            if purchase_cost is not None and hpp_before is not None:
                delta = money_round(_dec(purchase_cost) - _dec(hpp_before))
            receipt_id = _parse_gr_id(latest_gr.get("source_id"), latest_gr.get("canonical_source_id"))
            receipt = receipts.get(receipt_id) or {}
            diverges = bool(
                current_hpp is not None
                and hpp_after is not None
                and _dec(current_hpp) != _dec(hpp_after)
            )
            purchase = {
                "latest_gr_id": receipt_id or None,
                "latest_gr_number": receipt.get("receipt_number") or None,
                "latest_gr_date": str(receipt.get("receipt_date") or latest_gr.get("recognized_at") or "")[:10] or None,
                "latest_po_number": receipt.get("po_number") or None,
                "latest_supplier": receipt.get("supplier") or None,
                "latest_gr_qty": _qty(latest_gr.get("recognized_qty")),
                "latest_purchase_unit_cost": purchase_cost,
                "hpp_before_latest_gr": hpp_before,
                "hpp_after_latest_gr": hpp_after,
                "price_vs_hpp_delta": delta,
                "price_vs_hpp_pct": _pct(delta, hpp_before),
                "hpp_current_diverges_from_last_gr": diverges,
            }

        status = _row_status(current_hpp, event_statuses)
        inventory_value = None
        if current_hpp is not None:
            inventory_value = money_round(_dec(stock) * _dec(current_hpp))

        recognized_ids = {str(row.get("layer_id") or row.get("event_id") or "") for row in item_snaps}
        history: List[dict] = []
        prev_hpp = None
        for snap in item_snaps:
            history.append(_snapshot_history_row(
                snap,
                hpp_before=prev_hpp,
                receipts=receipts,
                returns=returns,
                opnames=opnames,
                bootstrap=bootstrap,
            ))
            after = _positive_money(snap.get("unit_cost"))
            if after is not None:
                prev_hpp = after
        for rec in item_recs:
            event_id = str(rec.get("event_id") or "")
            if rec.get("status") == STATUS_RECOGNIZED and event_id in recognized_ids:
                continue
            history.append(_pending_history_row(rec, receipts=receipts, returns=returns, opnames=opnames))
        history.sort(key=_history_sort_key)

        models[mid] = {
            "master_item_id": mid,
            "item_code": master.get("item_code") or "",
            "item_name": master.get("item_name") or master.get("item_code") or mid,
            "unit": unit_by_mid.get(mid) or master.get("unit") or "",
            "stock_fisik": stock,
            "hpp_status": status,
            "hpp_status_label": _status_label(status),
            "current_hpp": current_hpp,
            "inventory_value": inventory_value,
            **purchase,
            "bootstrap_first_gr": bootstrap,
            "history": history,
        }
    return models


def _matches(
    row: dict,
    *,
    q: str = "",
    supplier: str = "",
    status: str = "",
    date_from: str = "",
    date_to: str = "",
) -> bool:
    needle = str(q or "").strip().lower()
    if needle and needle not in str(row.get("item_code") or "").lower() and needle not in str(row.get("item_name") or "").lower():
        return False
    want_supplier = str(supplier or "").strip().lower()
    if want_supplier and want_supplier not in str(row.get("latest_supplier") or "").lower():
        return False
    want_status = str(status or "").strip().upper()
    if want_status and want_status not in {"ALL", "*"} and str(row.get("hpp_status") or "") != want_status:
        return False
    date_from = str(date_from or "").strip()[:10]
    date_to = str(date_to or "").strip()[:10]
    latest_date = str(row.get("latest_gr_date") or "")[:10]
    if date_from or date_to:
        if not latest_date:
            return False
        if date_from and latest_date < date_from:
            return False
        if date_to and latest_date > date_to:
            return False
    return True


def _public_row(model: dict, *, include_history: bool = False) -> dict:
    row = {key: value for key, value in model.items() if key != "history"}
    if include_history:
        row["history"] = list(model.get("history") or [])
    return row


async def list_purchase_hpp(
    db,
    *,
    q: str = "",
    supplier: str = "",
    status: str = "",
    date_from: str = "",
    date_to: str = "",
) -> dict:
    models = await _build_item_models(db)
    rows = [_public_row(model) for model in models.values() if _matches(
        model, q=q, supplier=supplier, status=status, date_from=date_from, date_to=date_to,
    )]
    rows.sort(key=lambda row: (str(row.get("item_code") or ""), str(row.get("item_name") or "")))
    suppliers = sorted({
        str(model.get("latest_supplier") or "").strip()
        for model in models.values()
        if str(model.get("latest_supplier") or "").strip()
    })
    return {"rows": rows, "total": len(rows), "suppliers": suppliers}


async def get_purchase_hpp_item(db, master_item_id: str) -> dict:
    mid = str(master_item_id or "").strip()
    if not mid:
        raise HTTPException(400, {"code": "MISSING_MASTER_ITEM_ID", "message": "Item tidak punya ID master yang stabil."})
    models = await _build_item_models(db)
    model = models.get(mid)
    if not model:
        raise HTTPException(404, {"code": "NOT_FOUND", "message": "Barang tidak ditemukan pada HPP Pembelian."})
    return _public_row(model, include_history=True)


def export_xlsx(rows: List[dict]) -> bytes:
    """Read-only Excel workbook for the current HPP Pembelian filter set."""
    wb = Workbook()
    ws = wb.active
    ws.title = "HPP Pembelian"
    headers = [
        "Kode",
        "Nama",
        "Satuan",
        "Stock Fisik",
        "Status HPP",
        "HPP Sebelum Pembelian Terakhir",
        "Harga Pembelian Terbaru",
        "Selisih",
        "Persentase",
        "HPP Setelah Pembelian Terakhir",
        "HPP Saat Ini",
        "Nilai Persediaan",
        "Tgl Penerimaan Terakhir",
        "No GR",
        "Supplier",
        "No PO",
        "Qty Penerimaan Terakhir",
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
        pct = row.get("price_vs_hpp_pct")
        pct_excel = (float(pct) / 100.0) if pct is not None else None
        ws.append([
            row.get("item_code") or "",
            row.get("item_name") or "",
            row.get("unit") or "",
            row.get("stock_fisik"),
            row.get("hpp_status_label") or row.get("hpp_status") or "",
            row.get("hpp_before_latest_gr"),
            row.get("latest_purchase_unit_cost"),
            row.get("price_vs_hpp_delta"),
            pct_excel,
            row.get("hpp_after_latest_gr"),
            row.get("current_hpp"),
            row.get("inventory_value"),
            row.get("latest_gr_date") or "",
            row.get("latest_gr_number") or "",
            row.get("latest_supplier") or "",
            row.get("latest_po_number") or "",
            row.get("latest_gr_qty"),
        ])
    money_fmt = "#,##0.00"
    qty_fmt = "#,##0.######"
    pct_fmt = "0.00%"
    for row_index in range(2, ws.max_row + 1):
        ws.cell(row=row_index, column=4).number_format = qty_fmt
        for column in (6, 7, 8, 10, 11, 12):
            ws.cell(row=row_index, column=column).number_format = money_fmt
        ws.cell(row=row_index, column=9).number_format = pct_fmt
        ws.cell(row=row_index, column=17).number_format = qty_fmt
    widths = [14, 32, 10, 12, 16, 18, 18, 12, 10, 18, 14, 16, 14, 16, 22, 14, 12]
    for index, width in enumerate(widths, start=1):
        letter = ws.cell(row=1, column=index).column_letter
        ws.column_dimensions[letter].width = width
    ws.auto_filter.ref = ws.dimensions
    ws.freeze_panes = "A2"
    output = BytesIO()
    wb.save(output)
    return output.getvalue()
