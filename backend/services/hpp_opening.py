"""HPP Awal (opening cost) Excel template / preview / confirm.

Reuses the existing HPP engine (hpp_cost_versions / hpp_cost_layers / hpp_valuation).
Does not mutate Stock V2 quantities or stock_movements.

Opening source of truth lives in hpp_opening_costs and is injected into
recalculate_hpp as source_type=OPENING_HPP acquisition events (qty > 0 only).
Zero/negative stock records cost_basis_only openings without fake or negative layers.
"""
from __future__ import annotations

import math
import re
import uuid
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from io import BytesIO
from typing import Any, Dict, Iterable, List, Optional, Tuple

from fastapi import HTTPException
from pymongo.errors import DuplicateKeyError

from services.hpp_valuation import (
    METHOD_AVG,
    RECOG_GR,
    recalculate_hpp,
)
from services.import_parser import ImportParseError, _read_rows
from services.purchase_common import actor_snapshot, item_identity, money_round, now_iso, qty_round
from services.stock_engine_v2 import qty_num

OPENING_COLLECTION = "hpp_opening_costs"
BATCH_COLLECTION = "hpp_opening_batches"
SOURCE_TYPE = "OPENING_HPP"

STATUS_OK = "OK"
STATUS_MISSING_COST = "MISSING_COST"
STATUS_INVALID_COST = "INVALID_COST"
STATUS_SKU_NOT_FOUND = "SKU_NOT_FOUND"
STATUS_DUPLICATE = "DUPLICATE"
STATUS_ALREADY_INITIALIZED = "ALREADY_INITIALIZED"
STATUS_COST_BASIS_ONLY = "COST_BASIS_ONLY"

TEMPLATE_HEADERS = [
    "KODE",
    "NAMA BARANG",
    "SATUAN",
    "STOCK FISIK",
    "HPP AWAL",
]

_HEADER_ALIASES = {
    "item_code": [
        "kode", "kode_barang", "sku", "code", "item_code", "kodebarang",
    ],
    "item_name": [
        "nama", "nama_barang", "nama_item", "item_name", "name", "namabarang",
    ],
    "unit": [
        "satuan", "unit", "uom",
    ],
    "stock_fisik": [
        "stock_fisik", "stok_fisik", "physical_qty", "physical_stock", "stock_fisik_v2",
    ],
    "hpp_awal": [
        "hpp_awal", "hpp", "opening_hpp", "unit_cost", "cost", "harga_pokok",
    ],
}

_WS = re.compile(r"[\s\-\./]+")


def _norm_header(raw: str) -> str:
    k = (raw or "").strip().lower()
    k = _WS.sub("_", k)
    for canon, aliases in _HEADER_ALIASES.items():
        if k in aliases or k == canon:
            return canon
    return k


def build_opening_template_xlsx(stock_rows: Iterable[dict]) -> bytes:
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill, Protection

    wb = Workbook()
    ws = wb.active
    ws.title = "HPP Awal"
    headers = list(TEMPLATE_HEADERS)
    ws.append(headers)
    header_font = Font(bold=True, color="FFFFFF")
    header_fill = PatternFill("solid", fgColor="0B2D22")
    for col_idx, _ in enumerate(headers, start=1):
        cell = ws.cell(row=1, column=col_idx)
        cell.font = header_font
        cell.fill = header_fill
        cell.alignment = Alignment(horizontal="left", vertical="center")

    for r in stock_rows:
        physical = r.get("physical_qty")
        if physical is None:
            physical = 0
        ws.append([
            r.get("item_code") or "",
            r.get("item_name") or "",
            r.get("unit") or "",
            qty_num(physical),
            None,  # user fills HPP AWAL only
        ])
        # Unlock HPP AWAL column for editing when sheet is protected
        ws.cell(row=ws.max_row, column=5).protection = Protection(locked=False)

    ws.column_dimensions["A"].width = 18
    ws.column_dimensions["B"].width = 36
    ws.column_dimensions["C"].width = 12
    ws.column_dimensions["D"].width = 14
    ws.column_dimensions["E"].width = 14
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = ws.dimensions
    ws.protection.sheet = True
    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _parse_hpp(raw: Any) -> Tuple[Optional[float], Optional[str]]:
    """Return (value, error_status). Blank → MISSING_COST; bad/≤0 → INVALID_COST."""
    if raw is None:
        return None, STATUS_MISSING_COST
    if isinstance(raw, str) and not raw.strip():
        return None, STATUS_MISSING_COST
    try:
        if isinstance(raw, str):
            cleaned = raw.strip().replace(",", ".")
            # Indonesian thousand separators: 1.000,50 → strip dots then comma
            if "," in cleaned and "." in cleaned:
                cleaned = cleaned.replace(".", "").replace(",", ".")
            n = float(cleaned)
        else:
            n = float(raw)
    except (TypeError, ValueError):
        return None, STATUS_INVALID_COST
    if math.isnan(n) or math.isinf(n):
        return None, STATUS_INVALID_COST
    if n <= 0:
        return None, STATUS_INVALID_COST
    return money_round(n), None


def _index_stock_by_code(stock_rows: Iterable[dict]) -> Dict[str, dict]:
    out: Dict[str, dict] = {}
    for r in stock_rows:
        code = str(r.get("item_code") or "").strip()
        if not code:
            continue
        out.setdefault(code.lower(), r)
    return out


async def _initialized_master_ids(db) -> set:
    cursor = db[OPENING_COLLECTION].find(
        {"status": "ACTIVE"},
        {"_id": 0, "master_item_id": 1},
    )
    rows = await cursor.to_list(50000)
    return {str(r.get("master_item_id") or "").strip() for r in rows if r.get("master_item_id")}


def opening_event_from_doc(doc: dict) -> Optional[dict]:
    """Build an acquisition event for qty > 0 openings. None for cost_basis_only."""
    if not doc or doc.get("cost_basis_only"):
        return None
    qty = qty_round(doc.get("opening_qty") or 0)
    if Decimal(str(qty)) <= 0:
        return None
    unit = money_round(doc.get("opening_unit_cost") or 0)
    mid = str(doc.get("master_item_id") or "").strip()
    ident = doc.get("item_identity") or item_identity({
        "master_item_id": mid,
        "accurate_item_id": doc.get("accurate_item_id"),
    })
    ts = str(doc.get("recognized_at") or doc.get("created_at") or "")
    return {
        "event_id": f"opening:{mid}",
        "source_type": SOURCE_TYPE,
        "source_id": doc.get("id") or mid,
        "source_line_id": mid,
        "source_number": doc.get("batch_id") or "OPENING_HPP",
        "item_identity": ident,
        "item_name": doc.get("item_name"),
        "item_code": doc.get("item_code"),
        "master_item_id": mid,
        "accurate_item_id": doc.get("accurate_item_id") or "",
        "recognized_at": ts,
        "recognized_qty": qty,
        "unit_cost": unit,
        "total_cost": money_round(Decimal(str(qty)) * Decimal(str(unit))),
        "transaction_uom": doc.get("unit") or "",
        "cost_basis_only": False,
    }


def cost_basis_layer_from_doc(doc: dict, *, version_id: str, version: int, method: str, recognition_point: str) -> dict:
    """Audit-only layer: qty 0, no fake stock, no negative layer."""
    mid = str(doc.get("master_item_id") or "").strip()
    unit = money_round(doc.get("opening_unit_cost") or 0)
    ident = doc.get("item_identity") or item_identity({
        "master_item_id": mid,
        "accurate_item_id": doc.get("accurate_item_id"),
    })
    return {
        "layer_id": f"opening-basis:{mid}",
        "item_identity": ident,
        "source_type": SOURCE_TYPE,
        "source_id": doc.get("id") or mid,
        "source_line_id": mid,
        "source_number": doc.get("batch_id") or "OPENING_HPP",
        "recognized_at": str(doc.get("recognized_at") or doc.get("created_at") or ""),
        "recognized_qty": 0.0,
        "remaining_qty": 0.0,
        "unit_cost": unit,
        "incoming_unit_cost": unit,
        "total_cost": 0.0,
        "item_name": doc.get("item_name"),
        "item_code": doc.get("item_code"),
        "master_item_id": mid,
        "accurate_item_id": doc.get("accurate_item_id") or "",
        "cost_basis_only": True,
        "opening_unit_cost": unit,
        "physical_qty_at_opening": qty_round(doc.get("physical_qty_at_opening") or 0),
        "calculation_version": version_id,
        "version": version,
        "method": method,
        "recognition_point": recognition_point,
        "financial_inventory_eligible": False,
        "financial_ineligibility_reason": "OPENING_HPP_COST_BASIS_ONLY",
        "financial_ineligibility_reasons": ["OPENING_HPP_COST_BASIS_ONLY"],
        "canonical_source_type": SOURCE_TYPE,
        "canonical_source_id": doc.get("id") or mid,
    }


async def load_active_opening_docs(db) -> List[dict]:
    return await db[OPENING_COLLECTION].find(
        {"status": "ACTIVE"},
        {"_id": 0},
    ).to_list(50000)


async def load_opening_acquisition_events(db) -> List[dict]:
    """Qty > 0 openings only — safe to feed into FIFO / moving-average."""
    docs = await load_active_opening_docs(db)
    events: List[dict] = []
    for doc in docs:
        ev = opening_event_from_doc(doc)
        if ev:
            events.append(ev)
    return events


async def load_cost_basis_only_docs(db) -> List[dict]:
    docs = await load_active_opening_docs(db)
    return [d for d in docs if d.get("cost_basis_only")]


def build_opening_import_preview(
    file_bytes: bytes,
    filename: str,
    *,
    stock_rows: List[dict],
    initialized_master_ids: Optional[Iterable[str]] = None,
) -> dict:
    """Parse XLSX and build preview — does not persist.

    Matching is KODE/SKU only. STOCK FISIK is informational (not imported).
    """
    try:
        headers, data_rows = _read_rows(file_bytes, filename or "upload.xlsx")
    except ImportParseError as e:
        raise HTTPException(400, str(e)) from e

    mapped = [_norm_header(h) for h in headers]
    if "item_code" not in mapped:
        raise HTTPException(400, "Kolom KODE wajib ada di file")
    if "hpp_awal" not in mapped:
        raise HTTPException(400, "Kolom HPP AWAL wajib ada di file")

    code_idx = mapped.index("item_code")
    name_idx = mapped.index("item_name") if "item_name" in mapped else None
    hpp_idx = mapped.index("hpp_awal")

    by_code = _index_stock_by_code(stock_rows)
    already = {str(x).strip() for x in (initialized_master_ids or []) if str(x).strip()}
    seen_codes: Dict[str, Tuple[int, int]] = {}
    preview_rows: List[dict] = []
    batch_request_id = str(uuid.uuid4())

    for row_no, cells in enumerate(data_rows, start=2):
        code = str(cells[code_idx] if code_idx < len(cells) else "").strip()
        name_file = ""
        if name_idx is not None and name_idx < len(cells):
            name_file = str(cells[name_idx] or "").strip()
        raw_hpp = cells[hpp_idx] if hpp_idx < len(cells) else None

        if not code and (raw_hpp is None or str(raw_hpp).strip() == "") and not name_file:
            continue

        base = {
            "row_number": row_no,
            "item_code": code,
            "item_name_file": name_file,
            "item_name": name_file or "",
            "unit": None,
            "master_item_id": None,
            "accurate_item_id": None,
            "item_identity": None,
            "physical_qty": None,
            "hpp_awal": None,
            "nilai_awal": None,
            "cost_basis_only": False,
            "status": STATUS_OK,
            "message": "",
            "can_apply": False,
        }

        if not code:
            base["status"] = STATUS_SKU_NOT_FOUND
            base["message"] = "KODE kosong"
            preview_rows.append(base)
            continue

        key = code.lower()
        if key in seen_codes:
            first_idx, first_row = seen_codes[key]
            first = preview_rows[first_idx]
            first["status"] = STATUS_DUPLICATE
            first["message"] = f"Baris duplikat (juga di baris {row_no})"
            first["can_apply"] = False
            first["nilai_awal"] = None
            base["status"] = STATUS_DUPLICATE
            base["message"] = f"Baris duplikat (juga di baris {first_row})"
            preview_rows.append(base)
            continue
        seen_codes[key] = (len(preview_rows), row_no)

        stock = by_code.get(key)
        if not stock:
            base["status"] = STATUS_SKU_NOT_FOUND
            base["message"] = "SKU tidak ditemukan di Master Barang/Jasa"
            preview_rows.append(base)
            continue

        mid = str(stock.get("master_item_id") or "").strip()
        physical = qty_num(stock.get("physical_qty") if stock.get("physical_qty") is not None else 0)
        ident = item_identity({
            "master_item_id": mid,
            "accurate_item_id": stock.get("accurate_item_id"),
        })
        base.update({
            "master_item_id": mid or None,
            "accurate_item_id": stock.get("accurate_item_id") or "",
            "item_identity": ident,
            "item_name": stock.get("item_name") or name_file or "",
            "item_code": stock.get("item_code") or code,
            "unit": stock.get("unit") or "",
            "physical_qty": physical,
        })

        if not mid:
            base["status"] = STATUS_SKU_NOT_FOUND
            base["message"] = "master_item_id tidak tersedia untuk SKU ini"
            preview_rows.append(base)
            continue

        if mid in already:
            base["status"] = STATUS_ALREADY_INITIALIZED
            base["message"] = "HPP Awal sudah diinisialisasi — tidak di-overwrite"
            preview_rows.append(base)
            continue

        hpp, hpp_err = _parse_hpp(raw_hpp)
        if hpp_err:
            base["status"] = hpp_err
            base["message"] = (
                "HPP AWAL kosong" if hpp_err == STATUS_MISSING_COST
                else "HPP AWAL harus angka > 0"
            )
            preview_rows.append(base)
            continue

        assert hpp is not None
        base["hpp_awal"] = hpp

        if physical > 0:
            nilai = money_round(Decimal(str(physical)) * Decimal(str(hpp)))
            base["nilai_awal"] = nilai
            base["cost_basis_only"] = False
            base["status"] = STATUS_OK
            base["message"] = "Siap diposting sebagai opening layer"
            base["can_apply"] = True
        else:
            # Zero or negative stock: cost basis only — never invent qty / negative layer
            base["nilai_awal"] = 0.0
            base["cost_basis_only"] = True
            base["status"] = STATUS_COST_BASIS_ONLY
            base["message"] = (
                "Stock Fisik ≤ 0 — HPP dicatat sebagai cost basis saja "
                "(tanpa layer qty / tanpa ubah stock)"
            )
            base["can_apply"] = True

        base["client_request_id"] = f"{batch_request_id}:{key}"
        preview_rows.append(base)

    ok_count = sum(1 for r in preview_rows if r["can_apply"] and r["status"] == STATUS_OK)
    basis_count = sum(1 for r in preview_rows if r["can_apply"] and r["status"] == STATUS_COST_BASIS_ONLY)
    block_count = sum(1 for r in preview_rows if not r["can_apply"])

    return {
        "filename": filename or "upload.xlsx",
        "batch_request_id": batch_request_id,
        "rows": preview_rows,
        "total_rows": len(preview_rows),
        "ok_count": ok_count,
        "cost_basis_only_count": basis_count,
        "block_count": block_count,
        "can_process": (ok_count + basis_count) > 0,
        "method_after_confirm": METHOD_AVG,
        "recognition_after_confirm": RECOG_GR,
        "note": (
            "Preview tidak mengubah data. Mutation hanya setelah konfirmasi. "
            "Stock Fisik / stock_movements tidak diubah."
        ),
    }


def revalidate_opening_apply_rows(
    preview_rows: List[dict],
    *,
    stock_rows: List[dict],
    initialized_master_ids: Optional[Iterable[str]],
    batch_request_id: str,
) -> List[dict]:
    """Rebuild validation from client HPP values against the live catalog."""
    request_id = str(batch_request_id or "").strip()
    if not request_id:
        raise HTTPException(400, "batch_request_id wajib diisi")

    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.append(TEMPLATE_HEADERS)
    for row in preview_rows or []:
        ws.append([
            row.get("item_code") or "",
            row.get("item_name") or "",
            row.get("unit") or "",
            row.get("physical_qty"),
            row.get("hpp_awal"),
        ])
    buf = BytesIO()
    wb.save(buf)
    validated = build_opening_import_preview(
        buf.getvalue(),
        "apply-validation.xlsx",
        stock_rows=stock_rows,
        initialized_master_ids=initialized_master_ids,
    )
    for row in validated["rows"]:
        code_key = str(row.get("item_code") or "").strip().lower()
        row["client_request_id"] = f"{request_id}:{code_key}"
    return validated["rows"]


async def confirm_opening_import(
    db,
    *,
    preview_rows: List[dict],
    filename: str,
    user: Optional[dict] = None,
    note: str = "",
    batch_request_id: str = "",
) -> dict:
    """Persist openings then recalculate HPP (RATA-RATA / goods_receipt).

    Never mutates product_stock_state or stock_movements.
    """
    request_id = str(batch_request_id or "").strip()
    if request_id:
        existing_batch = await db[BATCH_COLLECTION].find_one(
            {"client_request_id": request_id},
            {"_id": 0},
        )
        if existing_batch:
            return {
                "batch": existing_batch,
                "success_count": existing_batch.get("success_count", 0),
                "failure_count": existing_batch.get("failure_count", 0),
                "skipped_count": existing_batch.get("skipped_count", 0),
                "message": "Batch HPP Awal sudah diproses",
                "idempotent": True,
                "settings": None,
            }

    batch_id = str(uuid.uuid4())
    stamp = now_iso()
    actor = actor_snapshot(user)

    to_apply = [
        r for r in (preview_rows or [])
        if r.get("can_apply") and r.get("master_item_id") and r.get("hpp_awal") is not None
    ]
    successes: List[dict] = []
    failures: List[dict] = []
    skipped = [
        {
            "item_code": r.get("item_code"),
            "status": r.get("status"),
            "message": r.get("message"),
        }
        for r in (preview_rows or [])
        if not r.get("can_apply")
    ]

    for row in to_apply:
        mid = str(row.get("master_item_id") or "").strip()
        physical = qty_num(row.get("physical_qty") if row.get("physical_qty") is not None else 0)
        hpp = money_round(row.get("hpp_awal"))
        cost_basis_only = physical <= 0
        opening_qty = qty_round(physical) if physical > 0 else 0.0
        opening_total = money_round(Decimal(str(opening_qty)) * Decimal(str(hpp))) if opening_qty > 0 else 0.0
        ident = row.get("item_identity") or item_identity({
            "master_item_id": mid,
            "accurate_item_id": row.get("accurate_item_id"),
        })
        doc = {
            "id": str(uuid.uuid4()),
            "master_item_id": mid,
            "accurate_item_id": row.get("accurate_item_id") or "",
            "item_identity": ident,
            "item_code": row.get("item_code") or "",
            "item_name": row.get("item_name") or "",
            "unit": row.get("unit") or "",
            "physical_qty_at_opening": qty_round(physical),
            "opening_qty": opening_qty,
            "opening_unit_cost": hpp,
            "opening_total_cost": opening_total,
            "cost_basis_only": cost_basis_only,
            "source_type": SOURCE_TYPE,
            "status": "ACTIVE",
            "batch_id": batch_id,
            "client_request_id": row.get("client_request_id"),
            "recognized_at": stamp,
            "created_at": stamp,
            "created_by": actor,
            "note": note or "",
        }
        try:
            await db[OPENING_COLLECTION].insert_one(doc)
            successes.append({
                "item_code": doc["item_code"],
                "master_item_id": mid,
                "opening_qty": opening_qty,
                "opening_unit_cost": hpp,
                "opening_total_cost": opening_total,
                "cost_basis_only": cost_basis_only,
                "status": STATUS_COST_BASIS_ONLY if cost_basis_only else STATUS_OK,
            })
        except DuplicateKeyError:
            failures.append({
                "item_code": row.get("item_code"),
                "master_item_id": mid,
                "status": STATUS_ALREADY_INITIALIZED,
                "message": "HPP Awal sudah diinisialisasi",
            })
        except Exception as e:  # noqa: BLE001
            failures.append({
                "item_code": row.get("item_code"),
                "master_item_id": mid,
                "message": str(e),
            })

    settings = None
    recalc_error = None
    if successes:
        try:
            # Local-only path: set method/recognition via existing engine recalc.
            settings = await recalculate_hpp(
                db,
                method=METHOD_AVG,
                recognition_point=RECOG_GR,
                user=user,
            )
            active_version = (settings or {}).get("active_version")
            if active_version:
                await db[OPENING_COLLECTION].update_many(
                    {"batch_id": batch_id, "status": "ACTIVE"},
                    {"$set": {"version_id": active_version, "versioned_at": now_iso()}},
                )
        except Exception as e:  # noqa: BLE001
            recalc_error = str(e)

    item_count = len(preview_rows or [])
    batch_doc = {
        **({"_id": f"hpp_opening_batch:{request_id}"} if request_id else {}),
        "id": batch_id,
        "filename": filename or "upload.xlsx",
        "uploaded_by": actor,
        "uploaded_at": stamp,
        "item_count": item_count,
        "success_count": len(successes),
        "failure_count": len(failures),
        "skipped_count": len(skipped),
        "successes": successes,
        "failures": failures,
        "skipped": skipped,
        "note": note or "",
        "method": METHOD_AVG,
        "recognition_point": RECOG_GR,
        "active_version": (settings or {}).get("active_version"),
        "recalc_error": recalc_error,
        "client_request_id": request_id or None,
        "source_type": SOURCE_TYPE,
    }
    try:
        await db[BATCH_COLLECTION].insert_one(batch_doc)
    except DuplicateKeyError:
        existing_batch = await db[BATCH_COLLECTION].find_one(
            {"client_request_id": request_id},
            {"_id": 0},
        )
        if existing_batch:
            return {
                "batch": existing_batch,
                "success_count": existing_batch.get("success_count", 0),
                "failure_count": existing_batch.get("failure_count", 0),
                "skipped_count": existing_batch.get("skipped_count", 0),
                "message": "Batch HPP Awal sudah diproses",
                "idempotent": True,
                "settings": None,
            }
        raise

    public_batch = {k: v for k, v in batch_doc.items() if k != "_id"}
    msg = (
        f"HPP Awal selesai: {len(successes)} sukses, "
        f"{len(failures)} gagal, {len(skipped)} dilewati"
    )
    if recalc_error:
        msg += f". Peringatan perhitungan ulang: {recalc_error}"
    return {
        "batch": public_batch,
        "success_count": len(successes),
        "failure_count": len(failures),
        "skipped_count": len(skipped),
        "message": msg,
        "settings": settings,
        "stock_mutated": False,
        "stock_movements_mutated": False,
    }


async def ensure_opening_indexes(db) -> None:
    await db[OPENING_COLLECTION].create_index("id", unique=True)
    await db[OPENING_COLLECTION].create_index(
        [("master_item_id", 1), ("status", 1)],
        unique=True,
        partialFilterExpression={"status": "ACTIVE"},
        name="uniq_active_opening_per_master",
    )
    await db[OPENING_COLLECTION].create_index("item_code")
    await db[OPENING_COLLECTION].create_index("source_type")
    await db[BATCH_COLLECTION].create_index("id", unique=True)
    await db[BATCH_COLLECTION].create_index("client_request_id", unique=True, sparse=True)


# Re-export helpers used by HTTP layer
__all__ = [
    "BATCH_COLLECTION",
    "OPENING_COLLECTION",
    "SOURCE_TYPE",
    "STATUS_ALREADY_INITIALIZED",
    "STATUS_COST_BASIS_ONLY",
    "STATUS_DUPLICATE",
    "STATUS_INVALID_COST",
    "STATUS_MISSING_COST",
    "STATUS_OK",
    "STATUS_SKU_NOT_FOUND",
    "TEMPLATE_HEADERS",
    "build_opening_import_preview",
    "build_opening_template_xlsx",
    "confirm_opening_import",
    "cost_basis_layer_from_doc",
    "ensure_opening_indexes",
    "load_active_opening_docs",
    "load_cost_basis_only_docs",
    "load_opening_acquisition_events",
    "opening_event_from_doc",
    "revalidate_opening_apply_rows",
]
