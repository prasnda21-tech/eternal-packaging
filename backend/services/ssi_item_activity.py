"""Stock Saat Ini — item activity timeline (read-only compose).

Lanes:
  FISIK  — physical_stock_ledger only (stock mutations)
  SO     — sales-order commitment events (not physical stock)
  CETAK  — operator print confirmation / proven print-delivery refs
           (production status; not a physical stock deduction)

Never invents events. Never writes. Never a second ledger.
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional

from quantity_rules import is_print_line
from services.physical_stock import (
    LEDGER_COLLECTION,
    MASTER_ITEMS_COLLECTION,
    MOVEMENT_TYPES,
    PhysicalStockNotFoundError,
    PhysicalStockValidationError,
    _MOVEMENT_LABELS,
    _date_bound,
    _location_maps,
    _mutation_row,
    _qty_num,
    _require_branch_any,
    _require_master_item,
    _require_warehouse_any,
)
from services.stock_saat_ini_excel import aggregate_proven_print_delivered_qty

LANE_FISIK = "FISIK"
LANE_SO = "SO"
LANE_CETAK = "CETAK"
LANES = frozenset({LANE_FISIK, LANE_SO, LANE_CETAK})

METRIC_STOCK = "Stock"
METRIC_SALDO_AWAL = "Saldo Awal"
METRIC_PEMBELIAN = "Pembelian"
METRIC_SO_COMMITMENT = "Komitmen SO"
METRIC_QTY_TERCETAK = "Qty Tercetak"
METRIC_CETAK_DIKIRIM = "Cetak Sudah Dikirim"

_FISIK_METRIC = {
    "OPENING_BALANCE": METRIC_SALDO_AWAL,
    "PURCHASE_RECEIPT": METRIC_PEMBELIAN,
    "PURCHASE_RETURN": METRIC_PEMBELIAN,
    "SALES_DELIVERY": METRIC_STOCK,
    "MANUAL_ADJUSTMENT": METRIC_STOCK,
    "STOCK_OPNAME_ADJUSTMENT": METRIC_STOCK,
    "REVERSAL": METRIC_STOCK,
    "WAREHOUSE_TRANSFER_OUT": METRIC_STOCK,
    "WAREHOUSE_TRANSFER_IN": METRIC_STOCK,
    "PRODUCTION_CONSUMPTION": METRIC_STOCK,
    "PRODUCTION_OUTPUT": METRIC_STOCK,
}


def _as_text(value: Any) -> str:
    return str(value or "").strip()


def _event(
    *,
    lane: str,
    activity: str,
    activity_code: str,
    occurred_at: Optional[str],
    qty_pcs: Optional[float],
    metric_affected: str,
    source_type: Optional[str] = None,
    source_id: Optional[str] = None,
    source_label: Optional[str] = None,
    source_path: Optional[str] = None,
    actor_name: Optional[str] = None,
    balance_before: Any = None,
    balance_after: Any = None,
    notes: Optional[str] = None,
    affects_physical_stock: bool = False,
) -> Dict[str, Any]:
    before = balance_before if balance_before is not None else None
    after = balance_after if balance_after is not None else None
    return {
        "lane": lane,
        "activity": activity,
        "activity_code": activity_code,
        "occurred_at": occurred_at,
        "qty_pcs": None if qty_pcs is None else _qty_num(qty_pcs),
        "uom": "PCS",
        "metric_affected": metric_affected,
        "source_type": source_type,
        "source_id": source_id,
        "source_label": source_label,
        "source_path": source_path,
        "actor_name": actor_name or "Tidak tercatat",
        "balance_before": before,
        "balance_after": after,
        "before_after_supported": before is not None or after is not None,
        "affects_physical_stock": bool(affects_physical_stock),
        "notes": notes,
    }


async def _fisik_events(
    db,
    master_id: str,
    *,
    branch_id: Optional[str],
    warehouse_id: Optional[str],
    from_date: Optional[str],
    to_date: Optional[str],
    limit: int,
) -> List[Dict[str, Any]]:
    query: Dict[str, Any] = {"master_item_id": master_id}
    if branch_id:
        query["branch_id"] = branch_id
    if warehouse_id:
        query["warehouse_id"] = warehouse_id
    occurred: Dict[str, str] = {}
    if from_date:
        occurred["$gte"] = _date_bound(from_date, end=False)
    if to_date:
        occurred["$lte"] = _date_bound(to_date, end=True)
    if occurred:
        query["occurred_at"] = occurred
    docs = await db[LEDGER_COLLECTION].find(query, {"_id": 0}).sort(
        [("occurred_at", -1), ("created_at", -1), ("id", -1)]
    ).limit(limit).to_list(limit)
    branches, warehouses = await _location_maps(
        db,
        {doc.get("branch_id") for doc in docs if doc.get("branch_id")},
        {doc.get("warehouse_id") for doc in docs if doc.get("warehouse_id")},
    )
    out: List[Dict[str, Any]] = []
    for doc in docs:
        mt = _as_text(doc.get("movement_type")).upper()
        if mt and mt not in MOVEMENT_TYPES:
            continue
        row = _mutation_row(
            doc,
            branches.get(doc.get("branch_id")),
            warehouses.get(doc.get("warehouse_id")),
        )
        meta = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
        actor = (
            _as_text(meta.get("actor_name"))
            or _as_text(doc.get("actor_name"))
            or _as_text(doc.get("created_by_name"))
            or None
        )
        ref_id = _as_text(row.get("reference_id"))
        ref_type = _as_text(row.get("reference_type"))
        path = None
        if ref_id and ref_type.upper() in {"ORDER", "SALES_ORDER", "PO"}:
            path = f"/penjualan/order-masuk/{ref_id}"
        elif ref_id and "SJ" in ref_type.upper():
            path = f"/pengiriman?q={ref_id}"
        out.append(
            _event(
                lane=LANE_FISIK,
                activity=row.get("movement_label") or mt or "Mutasi fisik",
                activity_code=mt or "PHYSICAL",
                occurred_at=_as_text(row.get("occurred_at") or doc.get("created_at")) or None,
                qty_pcs=row.get("qty_delta"),
                metric_affected=_FISIK_METRIC.get(mt, METRIC_STOCK),
                source_type=ref_type or "physical_stock_ledger",
                source_id=ref_id or _as_text(row.get("id")) or None,
                source_label=ref_id or _as_text(row.get("id")) or None,
                source_path=path,
                actor_name=actor,
                balance_before=row.get("balance_before"),
                balance_after=row.get("balance_after"),
                notes=_as_text(row.get("reason")) or None,
                affects_physical_stock=True,
            )
        )
    return out


def _line_touches_master(line: dict, master_id: str, owners: Dict[str, str]) -> bool:
    mid = _as_text(line.get("master_item_id"))
    if mid and mid == master_id:
        return True
    aid = _as_text(line.get("accurate_item_id") or line.get("item_id"))
    if aid and owners.get(aid) == master_id:
        return True
    return False


def _order_cancelled(order: dict) -> bool:
    return bool(order.get("cancelled") or order.get("is_cancelled"))


def _line_cancelled(line: dict) -> bool:
    return bool(line.get("cancelled") or line.get("is_cancelled"))


def _shipment_cancelled(sh: dict) -> bool:
    if bool(sh.get("is_cancelled") or sh.get("cancelled")):
        return True
    st = _as_text(sh.get("delivery_status")).lower()
    return st in {"dibatalkan", "cancelled", "canceled"}


def _shipment_confirmed(sh: dict) -> bool:
    if _shipment_cancelled(sh):
        return False
    st = _as_text(sh.get("delivery_status")).lower()
    if st in {"terkirim", "delivered", "confirmed", "selesai"}:
        return True
    return bool(sh.get("confirmed") or sh.get("is_confirmed"))


async def _orders_for_master(
    db,
    master_id: str,
    *,
    branch_id: Optional[str],
    limit_orders: int = 200,
) -> List[dict]:
    query: Dict[str, Any] = {
        "$or": [
            {"line_items.master_item_id": master_id},
            {"line_items.item_id": master_id},
        ]
    }
    if branch_id:
        query["$and"] = [
            {"$or": [{"branch_id": branch_id}, {"cabang_id": branch_id}]},
        ]
    return await db.orders.find(query, {"_id": 0}).sort("created_at", -1).to_list(limit_orders)


def _so_cetak_events_from_orders(
    orders: Iterable[dict],
    master_id: str,
    *,
    owners: Optional[Dict[str, str]] = None,
) -> tuple[List[Dict[str, Any]], List[Dict[str, Any]], Dict[str, Any]]:
    """Build SO + CETAK events from order documents only (no aggregate invention)."""
    owners = owners or {}
    so_events: List[Dict[str, Any]] = []
    cetak_events: List[Dict[str, Any]] = []
    coverage = {
        "so_orders_scanned": 0,
        "so_events": 0,
        "cetak_events": 0,
        "orders_missing_timestamps": 0,
        "limitation": None,
    }

    # Proven print delivery per order (reference only — does not mutate stock here).
    print_qty_map, print_prov = aggregate_proven_print_delivered_qty(list(orders), owners)

    for order in orders:
        if not isinstance(order, dict):
            continue
        coverage["so_orders_scanned"] += 1
        oid = _as_text(order.get("id") or order.get("order_id"))
        po_no = _as_text(order.get("po_number") or order.get("nomor_po") or oid) or oid
        created = _as_text(order.get("created_at") or order.get("order_date")) or None
        if not created:
            coverage["orders_missing_timestamps"] += 1
        actor = (
            _as_text(order.get("created_by_name"))
            or _as_text(order.get("sales_name"))
            or None
        )
        lines = list(order.get("line_items") or [])
        touched = [
            (idx, line)
            for idx, line in enumerate(lines)
            if isinstance(line, dict) and _line_touches_master(line, master_id, owners)
        ]
        if not touched:
            continue

        ordered_total = 0.0
        for idx, line in touched:
            if _line_cancelled(line):
                continue
            ordered_total += max(_qty_num(line.get("quantity")), 0.0)

        path = f"/penjualan/order-masuk/{oid}" if oid else None

        if ordered_total > 0 and not _order_cancelled(order):
            so_events.append(
                _event(
                    lane=LANE_SO,
                    activity="Komitmen SO aktif",
                    activity_code="SO_COMMITMENT_ACTIVE",
                    occurred_at=created,
                    qty_pcs=ordered_total,
                    metric_affected=METRIC_SO_COMMITMENT,
                    source_type="ORDER",
                    source_id=oid or None,
                    source_label=po_no or oid,
                    source_path=path,
                    actor_name=actor,
                    notes="Komitmen penjualan — bukan pengurangan stok fisik",
                    affects_physical_stock=False,
                )
            )

        if _order_cancelled(order):
            cancel_at = (
                _as_text(order.get("cancelled_at"))
                or _as_text(order.get("updated_at"))
                or created
            )
            cancel_qty = sum(max(_qty_num(line.get("quantity")), 0.0) for _, line in touched)
            so_events.append(
                _event(
                    lane=LANE_SO,
                    activity="SO dibatalkan",
                    activity_code="SO_CANCELLED",
                    occurred_at=cancel_at,
                    qty_pcs=cancel_qty,
                    metric_affected=METRIC_SO_COMMITMENT,
                    source_type="ORDER",
                    source_id=oid or None,
                    source_label=po_no or oid,
                    source_path=path,
                    actor_name=_as_text(order.get("cancelled_by_name")) or actor,
                    notes="Komitmen dibatalkan — bukan mutasi stok fisik",
                    affects_physical_stock=False,
                )
            )

        # Line-level cancel (partial)
        for idx, line in touched:
            if not _line_cancelled(line) or _order_cancelled(order):
                continue
            so_events.append(
                _event(
                    lane=LANE_SO,
                    activity="Baris SO dibatalkan",
                    activity_code="SO_LINE_CANCELLED",
                    occurred_at=_as_text(line.get("cancelled_at")) or _as_text(order.get("updated_at")) or created,
                    qty_pcs=max(_qty_num(line.get("quantity")), 0.0),
                    metric_affected=METRIC_SO_COMMITMENT,
                    source_type="ORDER_LINE",
                    source_id=oid or None,
                    source_label=f"{po_no} · line {idx}",
                    source_path=path,
                    actor_name=actor,
                    notes="Komitmen baris dibatalkan",
                    affects_physical_stock=False,
                )
            )

        # Fulfilled commitment qty from confirmed shipments (commitment metric only).
        for sh in order.get("shipments") or []:
            if not isinstance(sh, dict) or not _shipment_confirmed(sh):
                continue
            sh_id = _as_text(sh.get("id") or sh.get("sj_number"))
            fulfilled = 0.0
            lines_meta = sh.get("lines") or []
            if lines_meta:
                for ln in lines_meta:
                    if not isinstance(ln, dict):
                        continue
                    try:
                        lidx = int(ln.get("line_idx"))
                    except (TypeError, ValueError):
                        continue
                    if lidx < 0 or lidx >= len(lines):
                        continue
                    if not _line_touches_master(lines[lidx], master_id, owners):
                        continue
                    fulfilled += max(_qty_num(ln.get("qty") or ln.get("quantity")), 0.0)
            else:
                # Header-only shipment: attribute only when single touched line exists.
                if len(touched) == 1:
                    fulfilled = max(_qty_num(sh.get("qty_delivered") or sh.get("quantity")), 0.0)
            if fulfilled <= 0:
                continue
            so_events.append(
                _event(
                    lane=LANE_SO,
                    activity="Komitmen SO terpenuhi (SJ)",
                    activity_code="SO_FULFILLED",
                    occurred_at=_as_text(sh.get("delivered_at") or sh.get("confirmed_at") or sh.get("created_at")) or created,
                    qty_pcs=fulfilled,
                    metric_affected=METRIC_SO_COMMITMENT,
                    source_type="SHIPMENT",
                    source_id=sh_id or oid or None,
                    source_label=sh_id or po_no,
                    source_path=path,
                    actor_name=_as_text(sh.get("confirmed_by_name")) or actor,
                    notes="Mengurangi komitmen SO — pengurangan stok fisik tercatat di lane Fisik (SALES_DELIVERY) bila ada",
                    affects_physical_stock=False,
                )
            )

        # CETAK: operator confirmation on print parent lines only (not SERVICE warna sum).
        print_parent_idxs = [
            idx
            for idx, line in touched
            if is_print_line(line) and not _line_cancelled(line)
            and _as_text(line.get("item_type")).upper() != "SERVICE"
        ]
        # Also allow inventory print without item_type
        if not print_parent_idxs:
            print_parent_idxs = [
                idx for idx, line in touched
                if is_print_line(line) and not _line_cancelled(line)
            ]

        for split in order.get("operator_splits") or []:
            if not isinstance(split, dict):
                continue
            raw_idx = split.get("line_idx")
            try:
                idx = int(raw_idx)
            except (TypeError, ValueError):
                continue
            if idx not in print_parent_idxs:
                continue
            qty = max(_qty_num(split.get("qty_completed")), 0.0)
            if qty <= 0:
                continue
            cetak_events.append(
                _event(
                    lane=LANE_CETAK,
                    activity="Konfirmasi cetak selesai operator",
                    activity_code="OPERATOR_PRINT_COMPLETED",
                    occurred_at=_as_text(
                        split.get("completed_at")
                        or split.get("updated_at")
                        or split.get("created_at")
                    ) or created,
                    qty_pcs=qty,
                    metric_affected=METRIC_QTY_TERCETAK,
                    source_type="OPERATOR_SPLIT",
                    source_id=oid or None,
                    source_label=f"{po_no} · parent line {idx}",
                    source_path=path,
                    actor_name=_as_text(split.get("operator_name") or split.get("completed_by_name")) or actor,
                    notes="Status produksi cetak — bukan pengurangan stok fisik",
                    affects_physical_stock=False,
                )
            )

        # Proven printed delivery reference (metric Cetak Sudah Dikirim only).
        mid_print = print_qty_map.get(master_id)
        if mid_print and print_prov.get(master_id) == "confirmed_print_shipment":
            # Emit once per order that contributed — scan confirmed print shipments
            for sh in order.get("shipments") or []:
                if not isinstance(sh, dict) or not _shipment_confirmed(sh):
                    continue
                print_qty = 0.0
                for ln in sh.get("lines") or []:
                    if not isinstance(ln, dict):
                        continue
                    try:
                        lidx = int(ln.get("line_idx"))
                    except (TypeError, ValueError):
                        continue
                    if lidx < 0 or lidx >= len(lines):
                        continue
                    line = lines[lidx]
                    if not _line_touches_master(line, master_id, owners):
                        continue
                    if not is_print_line(line):
                        continue
                    print_qty += max(_qty_num(ln.get("qty") or ln.get("quantity")), 0.0)
                if print_qty <= 0:
                    continue
                sh_id = _as_text(sh.get("id") or sh.get("sj_number"))
                cetak_events.append(
                    _event(
                        lane=LANE_CETAK,
                        activity="SJ cetak terbukti",
                        activity_code="PRINT_DELIVERY_PROVEN",
                        occurred_at=_as_text(sh.get("delivered_at") or sh.get("confirmed_at") or sh.get("created_at")) or created,
                        qty_pcs=print_qty,
                        metric_affected=METRIC_CETAK_DIKIRIM,
                        source_type="SHIPMENT_PRINT",
                        source_id=sh_id or oid or None,
                        source_label=sh_id or po_no,
                        source_path=path,
                        actor_name=_as_text(sh.get("confirmed_by_name")) or actor,
                        notes="Referensi cetak terkirim — pengurangan stok fisik ada di lane Fisik bila ledger SALES_DELIVERY ada",
                        affects_physical_stock=False,
                    )
                )

    coverage["so_events"] = len(so_events)
    coverage["cetak_events"] = len(cetak_events)
    if coverage["orders_missing_timestamps"]:
        coverage["limitation"] = (
            "Sebagian order tidak punya timestamp lengkap; "
            "hanya event dengan bukti dokumen yang ditampilkan."
        )
    return so_events, cetak_events, coverage


async def list_ssi_item_activity(
    db,
    item_id: str,
    *,
    branch_id: Optional[str] = None,
    warehouse_id: Optional[str] = None,
    lane: Optional[str] = None,
    from_date: Optional[str] = None,
    to_date: Optional[str] = None,
    limit: int = 100,
) -> Dict[str, Any]:
    """Read-only SSI activity for one master item."""
    master = await _require_master_item(db, item_id)
    master_id = master["id"]
    branch = await _require_branch_any(db, branch_id) if branch_id else None
    warehouse = await _require_warehouse_any(db, warehouse_id) if warehouse_id else None
    if branch and warehouse and warehouse.get("branch_id") != branch.get("id"):
        raise PhysicalStockValidationError("Gudang tidak berada pada cabang ini")

    wanted = _as_text(lane).upper() or None
    if wanted and wanted not in LANES and wanted != "SEMUA":
        raise PhysicalStockValidationError("Filter lane tidak dikenal")
    if wanted == "SEMUA":
        wanted = None

    safe_limit = max(1, min(int(limit or 100), 200))
    bid = branch["id"] if branch else None
    wid = warehouse["id"] if warehouse else None

    fisik: List[Dict[str, Any]] = []
    so: List[Dict[str, Any]] = []
    cetak: List[Dict[str, Any]] = []
    coverage: Dict[str, Any] = {
        "fisik_source": "physical_stock_ledger",
        "so_source": "orders (document events)",
        "cetak_source": "orders.operator_splits + proven print shipment lines",
        "limitation": None,
    }

    if wanted in (None, LANE_FISIK):
        fisik = await _fisik_events(
            db, master_id,
            branch_id=bid, warehouse_id=wid,
            from_date=from_date, to_date=to_date,
            limit=safe_limit,
        )

    if wanted in (None, LANE_SO, LANE_CETAK):
        orders = await _orders_for_master(db, master_id, branch_id=bid)
        so_ev, cetak_ev, cov = _so_cetak_events_from_orders(orders, master_id)
        coverage.update({k: v for k, v in cov.items() if k != "limitation" or v})
        if wanted in (None, LANE_SO):
            so = so_ev
        if wanted in (None, LANE_CETAK):
            cetak = cetak_ev
        if cov.get("limitation"):
            coverage["limitation"] = cov["limitation"]

    merged = [*fisik, *so, *cetak]

    def sort_key(ev: dict):
        ts = _as_text(ev.get("occurred_at")) or ""
        return ts

    merged.sort(key=sort_key, reverse=True)
    truncated = len(merged) > safe_limit
    items = merged[:safe_limit]

    return {
        "master_item_id": master_id,
        "item_code": master.get("item_code") or "",
        "item_name": master.get("item_name") or "",
        "branch_id": bid,
        "warehouse_id": wid,
        "lane_filter": wanted or "SEMUA",
        "items": items,
        "counts": {
            "fisik": len(fisik),
            "so": len(so),
            "cetak": len(cetak),
            "returned": len(items),
        },
        "coverage": coverage,
        "truncated": truncated,
        "history_incomplete": bool(coverage.get("limitation")) or truncated,
        "read_only": True,
        "notes": [
            "Lane SO = komitmen penjualan, bukan mutasi stok fisik.",
            "Lane Cetak = status produksi / referensi SJ cetak, bukan pengurangan stok fisik.",
            "Lane Fisik = physical_stock_ledger saja.",
        ],
    }
