"""Stock Opname Excel import/export for Stock Engine V2.

Template columns:
  KODE | NAMA BARANG | SATUAN | STOCK SISTEM | STOCK FISIK HASIL OPNAME

Matching: KODE/SKU only (nama/satuan/stock sistem = reference).
Does not create master items. Does not create a third stock engine —
applies via apply_stock_opname.
"""
from __future__ import annotations

import math
import re
import uuid
from datetime import datetime, timezone
from io import BytesIO
from typing import Any, Dict, Iterable, List, Optional, Tuple

from fastapi import HTTPException
from pymongo.errors import DuplicateKeyError

from services.import_parser import ImportParseError, _read_rows
from services.stock_engine_v2 import apply_stock_opname, compute_available, qty_num

BATCH_COLLECTION = "stock_opname_batches"

TEMPLATE_HEADERS = [
    "KODE",
    "NAMA BARANG",
    "SATUAN",
    "STOCK SISTEM",
    "STOCK FISIK HASIL OPNAME",
]

_HEADER_ALIASES = {
    "item_code": [
        "kode", "kode_barang", "sku", "code", "item_code", "kodebarang",
    ],
    "item_name": [
        "nama", "nama_barang", "nama_item", "item_name", "name", "namabarang",
    ],
    "unit": [
        "satuan", "unit", "uom", "satuan_barang",
    ],
    "stock_system": [
        "stock_sistem", "stok_sistem", "stock_system", "fisik_sistem",
        "stock_fisik_sistem", "qty_sistem",
    ],
    "physical_counted": [
        "stock_fisik_hasil_opname", "fisik_hasil_opname", "hasil_opname",
        "stock_fisik_hasil_hitung", "fisik_hasil_hitung", "fisik_opname",
        "stock_fisik", "fisik", "qty_fisik", "physical_qty", "qty", "stock",
        "hasil_hitung", "counted_qty", "opname_qty",
    ],
}

_WS = re.compile(r"[\s\-\./]+")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _norm_header(raw: str) -> str:
    k = (raw or "").strip().lower()
    k = _WS.sub("_", k)
    for canon, aliases in _HEADER_ALIASES.items():
        if k in aliases or k == canon:
            return canon
    return k


def is_active_master(doc: dict) -> bool:
    """Active = not explicitly inactive (True or unknown/None)."""
    if not isinstance(doc, dict):
        return False
    return doc.get("is_active") is not False


def filter_active_stock_rows(stock_rows: Iterable[dict], masters: Iterable[dict]) -> List[dict]:
    """Keep stock rows whose master is active; catalog order follows masters."""
    by_mid: Dict[str, dict] = {}
    by_code: Dict[str, dict] = {}
    for r in stock_rows or []:
        mid = str(r.get("master_item_id") or "").strip()
        code = str(r.get("item_code") or "").strip().lower()
        if mid:
            by_mid[mid] = r
        if code:
            by_code.setdefault(code, r)

    out: List[dict] = []
    seen: set[str] = set()
    for m in masters or []:
        if not is_active_master(m):
            continue
        mid = str(m.get("id") or m.get("master_item_id") or "").strip()
        code = str(m.get("item_code") or "").strip()
        if not code and not mid:
            continue
        row = (by_mid.get(mid) if mid else None) or by_code.get(code.lower())
        if row is None:
            row = {
                "master_item_id": mid or None,
                "accurate_item_id": m.get("accurate_item_id"),
                "item_code": code,
                "item_name": m.get("item_name") or "",
                "unit": m.get("unit") or "",
                "physical_qty": 0.0,
                "unprinted_qty": 0.0,
                "printed_qty": 0.0,
                "outstanding_so_qty": 0.0,
                "stock_current": 0.0,
                "available_qty": 0.0,
                "initialized": False,
                "is_active": True,
            }
        else:
            # Prefer master reference fields for export
            row = {
                **row,
                "item_code": code or row.get("item_code") or "",
                "item_name": m.get("item_name") or row.get("item_name") or "",
                "unit": m.get("unit") or row.get("unit") or "",
                "is_active": True,
            }
        key = str(row.get("master_item_id") or row.get("item_code") or "").strip().lower()
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(row)
    return out


def build_opname_template_xlsx(stock_rows: Iterable[dict]) -> bytes:
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill, Protection

    wb = Workbook()
    ws = wb.active
    ws.title = "Stock Opname"
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
        phys = r.get("physical_qty")
        if phys is None:
            phys = r.get("stock_system")
        ws.append([
            r.get("item_code") or "",
            r.get("item_name") or "",
            r.get("unit") or "",
            qty_num(phys) if phys is not None else 0,
            None,  # user fills counted physical
        ])
        ws.cell(row=ws.max_row, column=5).protection = Protection(locked=False)

    ws.column_dimensions["A"].width = 18
    ws.column_dimensions["B"].width = 36
    ws.column_dimensions["C"].width = 12
    ws.column_dimensions["D"].width = 14
    ws.column_dimensions["E"].width = 28
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = ws.dimensions
    ws.protection.sheet = True
    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _parse_qty(raw: Any) -> Tuple[Optional[float], Optional[str]]:
    if raw is None:
        return None, "QTY KOSONG"
    if isinstance(raw, str) and not raw.strip():
        return None, "QTY KOSONG"
    try:
        if isinstance(raw, str):
            cleaned = raw.strip().replace(",", ".")
            n = float(cleaned)
        else:
            n = float(raw)
    except (TypeError, ValueError):
        return None, "QTY BUKAN ANGKA"
    if math.isnan(n) or math.isinf(n):
        return None, "QTY BUKAN ANGKA"
    if n < 0:
        return None, "QTY NEGATIF"
    if n == int(n):
        n = float(int(n))
    return n, None


def _index_stock_by_code(stock_rows: Iterable[dict]) -> Dict[str, dict]:
    out: Dict[str, dict] = {}
    for r in stock_rows:
        code = str(r.get("item_code") or "").strip()
        if not code:
            continue
        key = code.lower()
        out.setdefault(key, r)
    return out


def build_opname_import_preview(
    file_bytes: bytes,
    filename: str,
    *,
    stock_rows: List[dict],
) -> dict:
    """Parse XLSX and build preview rows — does not persist.

    Matching is KODE/SKU only. Nama/satuan/stock sistem are reference.
    Does not create master items outside catalog.
    """
    try:
        headers, data_rows = _read_rows(file_bytes, filename or "upload.xlsx")
    except ImportParseError as e:
        raise HTTPException(400, str(e)) from e

    mapped = [_norm_header(h) for h in headers]
    if "item_code" not in mapped:
        raise HTTPException(400, "Kolom KODE wajib ada di file")
    if "physical_counted" not in mapped:
        raise HTTPException(400, "Kolom STOCK FISIK HASIL OPNAME wajib ada di file")

    code_idx = mapped.index("item_code")
    name_idx = mapped.index("item_name") if "item_name" in mapped else None
    qty_idx = mapped.index("physical_counted")

    by_code = _index_stock_by_code(stock_rows)
    seen_codes: Dict[str, Tuple[int, int]] = {}
    preview_rows: List[dict] = []
    batch_request_id = str(uuid.uuid4())

    for row_no, cells in enumerate(data_rows, start=2):
        code = str(cells[code_idx] if code_idx < len(cells) else "").strip()
        name_file = ""
        if name_idx is not None and name_idx < len(cells):
            name_file = str(cells[name_idx] or "").strip()
        raw_qty = cells[qty_idx] if qty_idx < len(cells) else None

        # skip fully empty lines
        if not code and (raw_qty is None or str(raw_qty).strip() == "") and not name_file:
            continue

        base = {
            "row_number": row_no,
            "item_code": code,
            "item_name_file": name_file,
            "item_name": name_file or "",
            "unit": None,
            "master_item_id": None,
            "physical_system": None,
            "physical_opname": None,
            "selisih": None,
            "printed_qty": None,
            "unprinted_qty_system": None,
            "unprinted_qty_new": None,
            "outstanding_so_qty": None,
            "available_new": None,
            "initialized": False,
            "opname_action": None,
            "status": "OK",
            "message": "",
            "can_apply": False,
        }

        if not code:
            base["status"] = "BLOCK"
            base["message"] = "KODE kosong"
            preview_rows.append(base)
            continue

        key = code.lower()
        if key in seen_codes:
            first_idx, first_row = seen_codes[key]
            first = preview_rows[first_idx]
            first["status"] = "BLOCK"
            first["message"] = f"KODE DUPLIKAT (juga di baris {row_no})"
            first["can_apply"] = False
            base["status"] = "BLOCK"
            base["message"] = f"KODE DUPLIKAT (juga di baris {first_row})"
            preview_rows.append(base)
            continue
        seen_codes[key] = (len(preview_rows), row_no)

        stock = by_code.get(key)
        if not stock:
            base["status"] = "BLOCK"
            base["message"] = "KODE TIDAK DITEMUKAN di Master Barang/Jasa"
            preview_rows.append(base)
            continue

        qty, qty_err = _parse_qty(raw_qty)
        mid = stock.get("master_item_id")
        phys_sys = qty_num(stock.get("physical_qty"))
        printed = qty_num(stock.get("printed_qty"))
        unprinted_sys = qty_num(stock.get("unprinted_qty"))
        outstanding = qty_num(stock.get("outstanding_so_qty"))
        initialized = bool(stock.get("initialized"))
        master_name = stock.get("item_name") or ""

        base.update({
            "master_item_id": mid,
            # Always prefer master name (ignore Excel name drift)
            "item_name": master_name or name_file or "",
            "item_code": stock.get("item_code") or code,
            "unit": stock.get("unit") or "",
            "physical_system": phys_sys,
            "printed_qty": printed,
            "unprinted_qty_system": unprinted_sys,
            "outstanding_so_qty": outstanding,
            "initialized": initialized,
            "opname_action": "KOREKSI" if initialized else "SALDO_AWAL",
        })
        if name_file and master_name and name_file.strip().lower() != master_name.strip().lower():
            base["message"] = "Nama file diabaikan — memakai nama master sistem"

        if qty_err:
            base["status"] = "BLOCK"
            base["message"] = qty_err
            preview_rows.append(base)
            continue

        assert qty is not None
        new_unprinted = qty - printed
        available_new = compute_available(qty, outstanding, printed)
        belum_new = max(outstanding - printed, 0.0) if printed is not None else None
        selisih = qty - phys_sys

        base.update({
            "physical_opname": qty,
            "selisih": selisih,
            "unprinted_qty_new": new_unprinted,
            "available_new": available_new,
            "client_request_id": f"{batch_request_id}:{key}",
        })

        if new_unprinted < -1e-9:
            base["status"] = "BLOCK"
            base["message"] = (
                "Data tidak konsisten: STOCK FISIK HASIL OPNAME "
                f"({qty}) < SUDAH DICETAK sistem ({printed})"
            )
            base["can_apply"] = False
            preview_rows.append(base)
            continue

        if initialized and abs(selisih) < 1e-9 and abs(new_unprinted - unprinted_sys) < 1e-9:
            base["status"] = "UNCHANGED"
            base["message"] = "Tidak ada selisih"
            base["can_apply"] = False
            preview_rows.append(base)
            continue

        base["status"] = "OK"
        if not base["message"]:
            base["message"] = "Siap diproses"
        base["can_apply"] = True
        preview_rows.append(base)

    ok_count = sum(1 for r in preview_rows if r["can_apply"])
    block_count = sum(1 for r in preview_rows if r["status"] == "BLOCK")
    error_count = 0
    unchanged_count = sum(1 for r in preview_rows if r["status"] == "UNCHANGED")

    return {
        "filename": filename or "upload.xlsx",
        "batch_request_id": batch_request_id,
        "rows": preview_rows,
        "total_rows": len(preview_rows),
        "ok_count": ok_count,
        "block_count": block_count,
        "error_count": error_count,
        "unchanged_count": unchanged_count,
        "can_process": ok_count > 0,
        "stock_engine": "v2",
        "formula": (
            "available_new = max(physical_new - outstanding_so, 0); "
            "unprinted_new = physical_new - printed_qty"
        ),
    }


def revalidate_opname_apply_rows(
    preview_rows: List[dict],
    *,
    stock_rows: List[dict],
    batch_request_id: str,
) -> List[dict]:
    """Rebuild validation from client quantities against the live catalog."""
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
            row.get("physical_system"),
            row.get("physical_opname"),
        ])
    buf = BytesIO()
    wb.save(buf)
    validated = build_opname_import_preview(
        buf.getvalue(),
        "apply-validation.xlsx",
        stock_rows=stock_rows,
    )
    for row in validated["rows"]:
        code_key = str(row.get("item_code") or "").strip().lower()
        row["client_request_id"] = f"{request_id}:{code_key}"
    return validated["rows"]


async def apply_opname_import_batch(
    db,
    *,
    preview_rows: List[dict],
    filename: str,
    user: Optional[dict] = None,
    note: str = "",
    batch_request_id: str = "",
) -> dict:
    """Apply OK preview rows via existing apply_stock_opname. Append-only ledger."""
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
                "message": "Batch Stock Opname sudah diproses",
                "stock_engine": "v2",
                "idempotent": True,
            }

    batch_id = str(uuid.uuid4())
    stamp = _now_iso()
    actor = {
        "id": str((user or {}).get("id") or (user or {}).get("_id") or ""),
        "name": (user or {}).get("name") or (user or {}).get("email") or "User",
        "email": (user or {}).get("email") or "",
        "role": (user or {}).get("role") or "",
    }

    to_apply = [r for r in (preview_rows or []) if r.get("can_apply") and r.get("master_item_id")]
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
        physical_new = qty_num(row.get("physical_opname"))
        printed = qty_num(row.get("printed_qty"))
        new_unprinted = physical_new - printed
        if new_unprinted < -1e-9:
            failures.append({
                "item_code": row.get("item_code"),
                "master_item_id": mid,
                "message": "Blocked: physical < printed",
            })
            continue
        try:
            result = await apply_stock_opname(
                db,
                master_item_id=mid,
                counted_unprinted_qty=new_unprinted,
                counted_printed_qty=printed,
                note=note or f"Import Opname batch {batch_id[:8]} · {filename}",
                user=user,
                client_request_id=row.get("client_request_id"),
            )
            successes.append({
                "item_code": row.get("item_code"),
                "master_item_id": mid,
                "physical_opname": physical_new,
                "available_new": row.get("available_new"),
                "unchanged": bool(result.get("unchanged")),
                "opname_action": result.get("opname_action"),
                "message": result.get("message"),
            })
        except HTTPException as e:
            failures.append({
                "item_code": row.get("item_code"),
                "master_item_id": mid,
                "message": e.detail if isinstance(e.detail, str) else str(e.detail),
            })
        except Exception as e:  # noqa: BLE001 — surface per-row failure
            failures.append({
                "item_code": row.get("item_code"),
                "master_item_id": mid,
                "message": str(e),
            })

    item_count = len(preview_rows or [])
    batch_doc = {
        **({"_id": f"stock_opname_batch:{request_id}"} if request_id else {}),
        "id": batch_id,
        "filename": filename or "upload.xlsx",
        "uploaded_by": actor,
        "uploaded_at": stamp,
        "item_count": item_count,
        "jumlah_item": item_count,
        "success_count": len(successes),
        "failure_count": len(failures),
        "failed_count": len(failures),
        "skipped_count": len(skipped),
        "successes": successes,
        "failures": failures,
        "skipped": skipped,
        "note": note or "",
        "stock_engine": "v2",
        "client_request_id": request_id or None,
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
                "message": "Batch Stock Opname sudah diproses",
                "stock_engine": "v2",
                "idempotent": True,
            }
        raise

    public_batch = {k: v for k, v in batch_doc.items() if k != "_id"}
    return {
        "batch": public_batch,
        "success_count": len(successes),
        "failure_count": len(failures),
        "skipped_count": len(skipped),
        "message": (
            f"Opname batch selesai: {len(successes)} sukses, "
            f"{len(failures)} gagal, {len(skipped)} dilewati"
        ),
        "stock_engine": "v2",
    }
