"""Kartu Stok / Mutasi Persediaan — Report Center Wave 3.

Sumber: ``physical_stock_ledger`` (R2) saja.
- Qty masuk/keluar dari ``qty_delta`` tersimpan.
- Saldo sebelum/sesudah = nilai ledger tercatat (bukan rekalkulasi historis).
- Completeness: COMPLETE / INCOMPLETE / UNRECONCILED dari baseline + bukti opening.
- Tidak menulis ledger, balance, mapping, atau dokumen bisnis.
- Tidak mencampur stock_movements legacy, paint_ledger, atau Accurate non-R2.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from io import BytesIO
from typing import Any, Dict, List, Optional, Sequence, Tuple

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font

from services.branch_access import (
    authorize_stock_location,
    can_switch_branches,
    filter_warehouses_for_user,
    resolve_branch_access,
)
from services.physical_stock import (
    BALANCE_COLLECTION,
    BASELINE_RECONCILED,
    BASELINE_UNRECONCILED,
    LEDGER_COLLECTION,
    MOVEMENT_OPENING_BALANCE,
    MOVEMENT_TYPES,
    _MOVEMENT_LABELS,
    _date_bound,
    _location_maps,
)
from services.sales_reports import (
    _company_name,
    _page,
    _text,
    format_period_date,
    parse_report_date,
)
from services.sales_user_mapping import is_sales_pwa_role

PERM_VIEW = "current_stock.view"
PERM_EXPORT = "current_stock.export"
PERM_ITEM = "master_items.view"
MEMORIZE_COLLECTION = "stock_report_memorized"

REPORT_KARTU = "kartu-stok"

HIST_COMPLETE = "complete"
HIST_INCOMPLETE = "incomplete"
HIST_UNRECONCILED = "unreconciled"

HIST_LABELS = {
    HIST_COMPLETE: "Histori Lengkap",
    HIST_INCOMPLETE: "Histori Tidak Lengkap",
    HIST_UNRECONCILED: "Belum Rekonsiliasi",
}

COLUMNS = (
    {"key": "waktu", "label": "Tanggal/Waktu", "align": "left", "default": True},
    {"key": "cabang", "label": "Cabang", "align": "left", "default": True},
    {"key": "gudang", "label": "Gudang", "align": "left", "default": True},
    {"key": "kode", "label": "Kode Barang", "align": "left", "default": True},
    {"key": "nama_barang", "label": "Nama Barang", "align": "left", "default": True},
    {"key": "jenis_mutasi", "label": "Jenis Mutasi", "align": "left", "default": True},
    {"key": "referensi", "label": "Referensi", "align": "left", "default": True},
    {"key": "qty_masuk", "label": "Qty Masuk", "align": "right", "default": True, "numeric": True},
    {"key": "qty_keluar", "label": "Qty Keluar", "align": "right", "default": True, "numeric": True},
    {"key": "saldo_sebelum", "label": "Saldo Sebelum", "align": "right", "default": True, "numeric": True},
    {"key": "saldo_sesudah", "label": "Saldo Sesudah", "align": "right", "default": True, "numeric": True},
    {"key": "status_histori", "label": "Status Histori", "align": "left", "default": True},
)

_CATALOG = (
    {
        "id": REPORT_KARTU,
        "name": "Kartu Stok / Mutasi Barang",
        "description": "Mutasi fisik R2 per lokasi dari physical_stock_ledger",
        "implemented": True,
        "reference": "",
    },
)

_MAX_ROWS = 5000


class StockReportError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


def catalog() -> List[dict]:
    rows = []
    for row in _CATALOG:
        item = dict(row)
        item["parameters"] = [
            "master_item_id", "tanggal_dari", "tanggal_sd", "cabang", "gudang",
            "jenis_mutasi", "referensi",
        ]
        item["columns"] = [
            {"key": c["key"], "label": c["label"], "align": c["align"], "default": c["default"]}
            for c in COLUMNS
        ]
        item["movement_types"] = sorted(MOVEMENT_TYPES)
        item["movement_labels"] = dict(_MOVEMENT_LABELS)
        rows.append(item)
    return rows


def _selected_columns(layout: Optional[dict]) -> List[dict]:
    requested = None
    if isinstance(layout, dict) and isinstance(layout.get("columns"), list) and layout.get("columns"):
        requested = list(dict.fromkeys(_text(k) for k in layout["columns"]))
    by_key = {c["key"]: c for c in COLUMNS}
    if requested is None:
        chosen = [dict(c) for c in COLUMNS if c["default"]]
    else:
        chosen = [dict(by_key[k]) for k in requested if k in by_key]
    if not chosen:
        chosen = [dict(c) for c in COLUMNS if c["default"]]
    return chosen


def _qty_int(value: Any) -> Optional[int]:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        try:
            return int(float(value))
        except (TypeError, ValueError):
            return None


def _fmt_qty(value: Optional[int]) -> Optional[str]:
    if value is None:
        return None
    return str(value)


def _loc_key(item_id: str, branch_id: str, warehouse_id: str) -> Tuple[str, str, str]:
    return (_text(item_id), _text(branch_id), _text(warehouse_id))


async def _filter_options(db, user: Optional[dict]) -> dict:
    access = await resolve_branch_access(db, user)
    branches = await db.branches.find({"active": {"$ne": False}}, {"_id": 0}).to_list(500)
    warehouses = await db.warehouses.find({"active": {"$ne": False}}, {"_id": 0}).to_list(2000)
    warehouses = await filter_warehouses_for_user(db, user, warehouses)
    if access.get("scope") != "ALL" and access.get("branch_ids") is not None:
        allowed = set(access.get("branch_ids") or [])
        branches = [b for b in branches if _text(b.get("id")) in allowed]
    return {
        "branches": [
            {"id": _text(b.get("id")), "code": _text(b.get("code")), "label": _text(b.get("name") or b.get("code"))}
            for b in branches if _text(b.get("id"))
        ],
        "warehouses": [
            {
                "id": _text(w.get("id")),
                "branch_id": _text(w.get("branch_id")),
                "code": _text(w.get("code")),
                "label": _text(w.get("name") or w.get("code")),
            }
            for w in warehouses if _text(w.get("id"))
        ],
        "can_switch_branches": can_switch_branches(access),
        "branch_scope": access.get("scope"),
    }


async def list_memorized(db, user: Optional[dict]) -> List[dict]:
    uid = _text((user or {}).get("id") or (user or {}).get("user_id"))
    if not uid:
        return []
    rows = await db[MEMORIZE_COLLECTION].find({"user_id": uid}, {"_id": 0}).sort("created_at", -1).to_list(50)
    return rows


async def save_memorized(db, user: Optional[dict], payload: dict) -> dict:
    uid = _text((user or {}).get("id") or (user or {}).get("user_id"))
    if not uid:
        raise StockReportError(400, "User tidak valid.")
    name = _text(payload.get("name"))
    if not name:
        raise StockReportError(400, "Nama tampilan wajib diisi.")
    doc = {
        "id": str(uuid.uuid4()),
        "user_id": uid,
        "report_id": REPORT_KARTU,
        "name": name,
        "description": _text(payload.get("description")),
        "layout": payload.get("layout") if isinstance(payload.get("layout"), dict) else {},
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    await db[MEMORIZE_COLLECTION].insert_one(doc)
    out = dict(doc)
    out.pop("_id", None)
    return out


async def delete_memorized(db, user: Optional[dict], memorize_id: str) -> dict:
    uid = _text((user or {}).get("id") or (user or {}).get("user_id"))
    mid = _text(memorize_id)
    result = await db[MEMORIZE_COLLECTION].delete_one({"id": mid, "user_id": uid})
    if not result.deleted_count:
        raise StockReportError(404, "Tampilan tersimpan tidak ditemukan.")
    return {"ok": True, "id": mid}


async def _history_status(
    db,
    *,
    master_item_id: str,
    branch_id: str,
    warehouse_id: str,
    date_from_bound: str,
) -> dict:
    """COMPLETE / INCOMPLETE / UNRECONCILED for one location — no invented opening."""
    bal = await db[BALANCE_COLLECTION].find_one(
        {
            "master_item_id": master_item_id,
            "branch_id": branch_id,
            "warehouse_id": warehouse_id,
        },
        {"_id": 0, "baseline_status": 1, "physical_qty": 1},
    )
    baseline = _text((bal or {}).get("baseline_status")) or BASELINE_RECONCILED
    if baseline == BASELINE_UNRECONCILED:
        return {
            "status": HIST_UNRECONCILED,
            "label": HIST_LABELS[HIST_UNRECONCILED],
            "opening_qty": None,
            "opening_proven": False,
            "opening_source": None,
        }

    opening_row = await db[LEDGER_COLLECTION].find_one(
        {
            "master_item_id": master_item_id,
            "branch_id": branch_id,
            "warehouse_id": warehouse_id,
            "movement_type": MOVEMENT_OPENING_BALANCE,
        },
        {"_id": 0, "id": 1, "occurred_at": 1, "balance_after": 1, "qty_delta": 1},
    )
    prior = await db[LEDGER_COLLECTION].find(
        {
            "master_item_id": master_item_id,
            "branch_id": branch_id,
            "warehouse_id": warehouse_id,
            "occurred_at": {"$lt": date_from_bound},
        },
        {"_id": 0, "id": 1, "occurred_at": 1, "balance_after": 1, "created_at": 1},
    ).sort([("occurred_at", -1), ("created_at", -1), ("id", -1)]).limit(1).to_list(1)

    if prior:
        opening_qty = _qty_int(prior[0].get("balance_after"))
        return {
            "status": HIST_COMPLETE,
            "label": HIST_LABELS[HIST_COMPLETE],
            "opening_qty": opening_qty,
            "opening_proven": opening_qty is not None,
            "opening_source": "ledger_prior_balance_after",
        }
    if opening_row:
        # Opening exists at or after period start, or is the only baseline — period
        # open is proven only when opening occurred at/before period start.
        occ = _text(opening_row.get("occurred_at"))
        if occ and occ < date_from_bound:
            opening_qty = _qty_int(opening_row.get("balance_after"))
            return {
                "status": HIST_COMPLETE,
                "label": HIST_LABELS[HIST_COMPLETE],
                "opening_qty": opening_qty,
                "opening_proven": opening_qty is not None,
                "opening_source": "opening_balance_before_period",
            }
        # Opening inside period: first movement may be the opening itself — complete
        # for that location once opening exists, but no separate period-open claim
        # before that row.
        return {
            "status": HIST_COMPLETE,
            "label": HIST_LABELS[HIST_COMPLETE],
            "opening_qty": None,
            "opening_proven": False,
            "opening_source": "opening_inside_period",
        }

    # Movements may exist in period without opening/prior — incomplete.
    any_in_scope = await db[LEDGER_COLLECTION].find_one(
        {
            "master_item_id": master_item_id,
            "branch_id": branch_id,
            "warehouse_id": warehouse_id,
        },
        {"_id": 0, "id": 1},
    )
    if any_in_scope:
        return {
            "status": HIST_INCOMPLETE,
            "label": HIST_LABELS[HIST_INCOMPLETE],
            "opening_qty": None,
            "opening_proven": False,
            "opening_source": None,
        }
    return {
        "status": HIST_INCOMPLETE,
        "label": HIST_LABELS[HIST_INCOMPLETE],
        "opening_qty": None,
        "opening_proven": False,
        "opening_source": None,
    }


def _detect_backdated_chain(docs: Sequence[dict]) -> bool:
    """True when chronological order disagrees with recorded balance chain."""
    prev_after = None
    for doc in docs:
        before = _qty_int(doc.get("balance_before"))
        after = _qty_int(doc.get("balance_after"))
        if prev_after is not None and before is not None and before != prev_after:
            return True
        if after is not None:
            prev_after = after
    return False


def _project_row(row: dict, columns: Sequence[dict]) -> dict:
    values = {}
    for col in columns:
        key = col["key"]
        raw = row.get(key)
        if col.get("numeric"):
            values[key] = _fmt_qty(raw) if raw is not None else None
        else:
            values[key] = raw if raw is not None else ""
    return {"kind": row.get("kind") or "line", "values": values, "drill": row.get("drill")}


async def run_report(
    db,
    *,
    user: Optional[dict],
    report_id: str,
    date_from: Any,
    date_to: Any,
    master_item_id: Any = "",
    branch_id: Any = "",
    warehouse_id: Any = "",
    movement_type: Any = "",
    reference_q: Any = "",
    layout: Optional[dict] = None,
    page: int = 1,
    page_size: int = 25,
    include_all: bool = False,
) -> dict:
    if is_sales_pwa_role(user):
        raise StockReportError(403, "Laporan ERP tidak tersedia untuk peran ini.")
    if _text(report_id) != REPORT_KARTU:
        raise StockReportError(404, "Laporan Kartu Stok tidak ditemukan.")

    item_id = _text(master_item_id)
    if not item_id:
        raise StockReportError(400, "Filter barang (master_item_id) wajib diisi.")

    start = parse_report_date(date_from, "Dari")
    end = parse_report_date(date_to, "s/d")
    if end < start:
        raise StockReportError(400, "Tanggal s/d tidak boleh lebih kecil dari tanggal Dari.")

    bid = _text(branch_id)
    wid = _text(warehouse_id)
    # Location authorization — never leak unauthorized branch/warehouse via query.
    try:
        loc = await authorize_stock_location(
            db, user, branch_id=bid or None, warehouse_id=wid or None,
            require_warehouse=False,
        )
    except Exception as exc:
        # FastAPI HTTPException from branch_access
        status = getattr(exc, "status_code", None) or 403
        detail = getattr(exc, "detail", None) or str(exc)
        raise StockReportError(int(status), str(detail)) from exc

    if not bid and loc.get("branch_id"):
        bid = _text(loc.get("branch_id"))

    master = await db.master_items.find_one({"id": item_id}, {"_id": 0})
    if not master:
        raise StockReportError(404, "Barang tidak ditemukan.")

    mtype = _text(movement_type).upper()
    if mtype and mtype not in MOVEMENT_TYPES:
        raise StockReportError(400, "Jenis mutasi tidak dikenal.")

    from_bound = _date_bound(start, end=False)
    to_bound = _date_bound(end, end=True)

    query: Dict[str, Any] = {
        "master_item_id": item_id,
        "occurred_at": {"$gte": from_bound, "$lte": to_bound},
    }
    if bid:
        query["branch_id"] = bid
    if wid:
        query["warehouse_id"] = wid
    if mtype:
        query["movement_type"] = mtype
    ref_q = _text(reference_q)
    if ref_q:
        query["$or"] = [
            {"reference_id": {"$regex": ref_q, "$options": "i"}},
            {"reference_type": {"$regex": ref_q, "$options": "i"}},
        ]

    # Scope warehouses to authorized set when branch-only (no warehouse filter).
    if bid and not wid:
        opts = await _filter_options(db, user)
        allowed_wh = {
            w["id"] for w in opts["warehouses"]
            if (not bid or w.get("branch_id") == bid)
        }
        if allowed_wh:
            query["warehouse_id"] = {"$in": sorted(allowed_wh)}

    docs = await db[LEDGER_COLLECTION].find(query, {"_id": 0}).sort(
        [("occurred_at", 1), ("created_at", 1), ("id", 1)]
    ).to_list(_MAX_ROWS)

    # Harden: drop any row outside authorized warehouses (defense in depth).
    opts = await _filter_options(db, user)
    allowed_wh = {w["id"] for w in opts["warehouses"]}
    allowed_br = {b["id"] for b in opts["branches"]}
    if opts.get("branch_scope") != "ALL":
        docs = [
            d for d in docs
            if _text(d.get("warehouse_id")) in allowed_wh
            and (not allowed_br or _text(d.get("branch_id")) in allowed_br)
        ]

    branches, warehouses = await _location_maps(
        db,
        {d.get("branch_id") for d in docs if d.get("branch_id")},
        {d.get("warehouse_id") for d in docs if d.get("warehouse_id")},
    )

    # Completeness per location appearing in result (and requested location even if empty).
    location_keys: List[Tuple[str, str, str]] = []
    seen_loc = set()
    for d in docs:
        key = _loc_key(item_id, d.get("branch_id"), d.get("warehouse_id"))
        if key not in seen_loc and key[1] and key[2]:
            seen_loc.add(key)
            location_keys.append(key)
    if wid and bid and _loc_key(item_id, bid, wid) not in seen_loc:
        location_keys.append(_loc_key(item_id, bid, wid))

    hist_by_loc: Dict[Tuple[str, str, str], dict] = {}
    for key in location_keys:
        hist_by_loc[key] = await _history_status(
            db,
            master_item_id=key[0],
            branch_id=key[1],
            warehouse_id=key[2],
            date_from_bound=from_bound,
        )

    # Group docs by location — never one running balance across warehouses.
    by_loc: Dict[Tuple[str, str, str], List[dict]] = {}
    for d in docs:
        key = _loc_key(item_id, d.get("branch_id"), d.get("warehouse_id"))
        by_loc.setdefault(key, []).append(d)

    item_code = _text(master.get("item_code") or master.get("code"))
    item_name = _text(master.get("item_name") or master.get("name")) or "—"
    columns = _selected_columns(layout)
    display_rows: List[dict] = []
    qty_in_total = 0
    qty_out_total = 0
    backdated_any = False
    statuses_seen = set()
    multi_location = len(by_loc) > 1 or (not wid and len(location_keys) > 1)

    for key in sorted(by_loc.keys(), key=lambda k: (k[1], k[2])):
        loc_docs = by_loc[key]
        hist = hist_by_loc.get(key) or {
            "status": HIST_INCOMPLETE,
            "label": HIST_LABELS[HIST_INCOMPLETE],
            "opening_proven": False,
            "opening_qty": None,
        }
        statuses_seen.add(hist["status"])
        branch = branches.get(key[1]) or {}
        warehouse = warehouses.get(key[2]) or {}
        branch_label = _text(branch.get("name") or branch.get("code") or key[1])
        wh_label = _text(warehouse.get("name") or warehouse.get("code") or key[2])

        if multi_location or len(by_loc) > 1:
            display_rows.append({
                "kind": "group",
                "label": f"{item_code or item_id} · {branch_label} / {wh_label} — {hist['label']}",
                "level": 0,
            })

        if hist.get("opening_proven") and hist.get("opening_qty") is not None:
            display_rows.append(_project_row({
                "kind": "line",
                "waktu": f"{start} 00:00:00",
                "cabang": branch_label,
                "gudang": wh_label,
                "kode": item_code,
                "nama_barang": item_name,
                "jenis_mutasi": "Saldo Awal Periode",
                "referensi": "ledger:" + str(hist.get("opening_source") or "prior"),
                "qty_masuk": None,
                "qty_keluar": None,
                "saldo_sebelum": None,
                "saldo_sesudah": hist["opening_qty"],
                "status_histori": hist["label"],
            }, columns))

        if _detect_backdated_chain(loc_docs):
            backdated_any = True

        for doc in loc_docs:
            delta = _qty_int(doc.get("qty_delta")) or 0
            qty_in = delta if delta > 0 else 0
            qty_out = -delta if delta < 0 else 0
            qty_in_total += qty_in
            qty_out_total += qty_out
            ref_parts = [p for p in (_text(doc.get("reference_type")), _text(doc.get("reference_id"))) if p]
            display_rows.append(_project_row({
                "kind": "line",
                "waktu": _text(doc.get("occurred_at")),
                "cabang": branch_label,
                "gudang": wh_label,
                "kode": item_code,
                "nama_barang": item_name,
                "jenis_mutasi": _MOVEMENT_LABELS.get(doc.get("movement_type")) or doc.get("movement_type") or "",
                "referensi": " / ".join(ref_parts) if ref_parts else (_text(doc.get("reason")) or "—"),
                "qty_masuk": qty_in if qty_in else None,
                "qty_keluar": qty_out if qty_out else None,
                "saldo_sebelum": _qty_int(doc.get("balance_before")),
                "saldo_sesudah": _qty_int(doc.get("balance_after")),
                "status_histori": hist["label"],
                "drill": None,
            }, columns))

    data_count = sum(1 for r in display_rows if r.get("kind") != "group")
    if include_all:
        paged = display_rows
        page_meta = {
            "page": 1,
            "page_size": max(data_count, 1),
            "filtered_count": data_count,
            "page_count": 1,
        }
    else:
        # Page by display rows including groups — filter applied before pagination.
        paged, page_meta = _page(display_rows, page, page_size)
        page_meta["filtered_count"] = data_count

    gap_parts = []
    if HIST_INCOMPLETE in statuses_seen:
        gap_parts.append(
            "Histori Tidak Lengkap: saldo awal periode tidak dapat dibuktikan dari ledger "
            "(tidak ada OPENING_BALANCE/prior balance_after). Qty mutasi tetap ditampilkan."
        )
    if HIST_UNRECONCILED in statuses_seen:
        gap_parts.append(
            "Belum Rekonsiliasi: baseline lokasi masih unreconciled — jangan anggap saldo buku final."
        )
    if backdated_any:
        gap_parts.append(
            "Terdeteksi rantai balance_before/after yang tidak kronologis (kemungkinan backdated). "
            "Saldo yang ditampilkan adalah nilai ledger tercatat, bukan saldo historis rekalkulasi."
        )
    if multi_location:
        gap_parts.append(
            "Beberapa lokasi: saldo tidak digabung. Saldo sebelum/sesudah berlaku per baris lokasi."
        )
    if not gap_parts:
        gap_parts.append(
            "Saldo sebelum/sesudah = nilai physical_stock_ledger tercatat. Tidak ada Rupiah pada laporan ini."
        )

    gap_detail = (
        "Sumber: physical_stock_ledger R2 (master_item_id, branch_id, warehouse_id, occurred_at, "
        "qty_delta, balance_before, balance_after, movement_type, reference_*). "
        "Opening periode hanya dari prior balance_after atau OPENING_BALANCE sebelum periode. "
        "Tidak memakai current stock − histori. Tidak mencampur legacy stock_movements / paint_ledger. "
        "Reversal dan mutasi produksi ditampilkan bila ada di ledger."
    )

    company = await _company_name(db)
    branch_label = "Semua lokasi diizinkan"
    if wid:
        wh = next((w for w in opts["warehouses"] if w["id"] == wid), None)
        branch_label = (wh or {}).get("label") or wid
    elif bid:
        br = next((b for b in opts["branches"] if b["id"] == bid), None)
        branch_label = (br or {}).get("label") or bid

    overall_status = HIST_COMPLETE
    if HIST_UNRECONCILED in statuses_seen:
        overall_status = HIST_UNRECONCILED
    elif HIST_INCOMPLETE in statuses_seen or not statuses_seen:
        overall_status = HIST_INCOMPLETE if docs or not hist_by_loc else HIST_INCOMPLETE
    if location_keys and not docs:
        # Empty period — still report location completeness.
        first_hist = hist_by_loc.get(location_keys[0]) or {}
        overall_status = first_hist.get("status") or HIST_INCOMPLETE

    totals = {
        "qty_masuk": _fmt_qty(qty_in_total),
        "qty_keluar": _fmt_qty(qty_out_total),
        "row_count": data_count,
    }

    return {
        "report_id": REPORT_KARTU,
        "name": "Kartu Stok / Mutasi Barang",
        "description": "Mutasi fisik R2 per lokasi dari physical_stock_ledger",
        "company_name": company,
        "period_label": f"Dari {format_period_date(start)} s/d {format_period_date(end)}",
        "branch_label": f"Lokasi : {branch_label}",
        "date_from": start,
        "date_to": end,
        "master_item_id": item_id,
        "item_code": item_code,
        "item_name": item_name,
        "branch_id": bid or None,
        "warehouse_id": wid or None,
        "columns": [{"key": c["key"], "label": c["label"], "align": c["align"]} for c in columns],
        "rows": paged,
        "totals": totals,
        "totals_scope": "qty masuk/keluar seluruh hasil filter; saldo tidak dijumlah lintas lokasi",
        "grouped": multi_location or len(by_loc) > 1,
        "gap": " ".join(gap_parts),
        "gap_detail": gap_detail,
        "history_status": overall_status,
        "history_status_label": HIST_LABELS.get(overall_status, overall_status),
        "balance_semantics": "ledger_recorded",
        "methodology": {
            "source": "physical_stock_ledger",
            "opening": "prior balance_after atau OPENING_BALANCE sebelum periode; tidak dari current−histori",
            "balance": "balance_before/after ledger tercatat; bukan rekalkulasi sort user",
            "currency": "qty only — no Rupiah",
            "locations": "satu running-balance chain per (item, cabang, gudang)",
            "backdated": "ditandai bila rantai balance tidak kronologis; nilai tetap dari ledger",
        },
        "source_meta": {
            "row_cap": _MAX_ROWS,
            "locations": len(location_keys),
            "movement_types_present": sorted({_text(d.get("movement_type")) for d in docs}),
            "backdated_chain": backdated_any,
        },
        **page_meta,
    }


def export_workbook(report: dict) -> bytes:
    book = Workbook()
    sheet = book.active
    raw_title = (report.get("name") or "Kartu Stok").replace("/", "-")
    sheet.title = raw_title[:31]
    sheet["A1"] = report.get("company_name") or ""
    sheet["A2"] = report.get("name") or ""
    sheet["A3"] = report.get("period_label") or ""
    sheet["A4"] = report.get("branch_label") or ""
    sheet["A5"] = report.get("history_status_label") or ""
    headers = [col["label"] for col in report.get("columns") or []]
    keys = [col["key"] for col in report.get("columns") or []]
    header_row = 7
    for index, label in enumerate(headers, start=1):
        cell = sheet.cell(header_row, index, label)
        cell.font = Font(bold=True)
    row_index = header_row + 1
    moneyish = {"qty_masuk", "qty_keluar", "saldo_sebelum", "saldo_sesudah"}
    for row in report.get("rows") or []:
        if row.get("kind") == "group":
            sheet.cell(row_index, 1, row.get("label") or "")
            sheet.cell(row_index, 1).font = Font(bold=True)
            row_index += 1
            continue
        values = row.get("values") or {}
        for index, key in enumerate(keys, start=1):
            raw = values.get(key)
            cell = sheet.cell(row_index, index, "" if raw is None else raw)
            if key in moneyish and raw not in (None, ""):
                cell.alignment = Alignment(horizontal="right")
        row_index += 1
    totals = report.get("totals") or {}
    if any(k in totals for k in keys):
        sheet.cell(row_index, 1, "Total")
        sheet.cell(row_index, 1).font = Font(bold=True)
        for index, key in enumerate(keys, start=1):
            if key in totals and totals.get(key) is not None:
                cell = sheet.cell(row_index, index, totals.get(key))
                cell.font = Font(bold=True)
                cell.alignment = Alignment(horizontal="right")
    buffer = BytesIO()
    book.save(buffer)
    return buffer.getvalue()
