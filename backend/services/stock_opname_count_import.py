"""Excel physical-count template for one frozen stock opname session.

Stock Buku, Qty Pesanan, and Qty Belum Terkirim are copied from the cutoff
snapshot. Excel formulas and imported book columns are not authoritative.
"""
from __future__ import annotations

from io import BytesIO
from typing import Any, Dict, List, Optional

from openpyxl import Workbook, load_workbook

from services.stock_opname_sessions import (
    STATUS_COUNTING,
    get_stock_opname_session,
    parse_count_qty,
    record_stock_opname_counts,
)

COUNT_SHEET = "Hitung Fisik"
SESSION_SHEET = "Sesi"
COUNT_HEADERS = [
    "Kode Barang",
    "Nama Barang",
    "Satuan Dasar",
    "Stock Buku",
    "Qty Pesanan",
    "Qty Belum Terkirim",
    "Stock Fisik",
    "Selisih",
    "Qty Tersedia Setelah Opname",
]
ID_HEADER = "ID Barang"


class CountImportError(Exception):
    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


def build_count_template_xlsx(session: dict) -> bytes:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = COUNT_SHEET
    headers = COUNT_HEADERS + [ID_HEADER]
    sheet.append(headers)
    for index, line in enumerate(session.get("lines") or [], start=2):
        sheet.append([
            line.get("item_code") or "",
            line.get("item_name") or "",
            line.get("base_uom") or "",
            line.get("book_qty"),
            line.get("qty_pesanan"),
            line.get("qty_belum_terkirim"),
            None,
            f'=IF(G{index}="","",G{index}-D{index})',
            f'=IF(G{index}="","",G{index}-E{index}-F{index})',
            line.get("master_item_id") or "",
        ])
    meta = workbook.create_sheet(SESSION_SHEET)
    meta.append(["session_id", session.get("id") or ""])
    meta.append(["warehouse_id", session.get("warehouse_id") or ""])
    meta.append(["opname_number", session.get("opname_number") or ""])
    buffer = BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def _header_map(sheet) -> Dict[str, int]:
    header = next(sheet.iter_rows(min_row=1, max_row=1, values_only=True), None)
    if not header:
        raise CountImportError("Kolom template hitung fisik tidak ditemukan.")
    found = {str(value).strip(): index for index, value in enumerate(header) if value not in (None, "")}
    missing = [name for name in COUNT_HEADERS if name not in found]
    if missing:
        raise CountImportError("Kolom template tidak lengkap: " + ", ".join(missing))
    return found


def _meta(workbook) -> Dict[str, str]:
    if SESSION_SHEET not in workbook.sheetnames:
        raise CountImportError("File tidak terikat pada sesi stock opname.")
    meta = {}
    for row in workbook[SESSION_SHEET].iter_rows(values_only=True):
        if not row or row[0] in (None, ""):
            continue
        meta[str(row[0]).strip()] = "" if len(row) < 2 or row[1] is None else str(row[1]).strip()
    return meta


def _blank_count(value: Any) -> bool:
    return value is None or (isinstance(value, str) and value.strip() == "")


def parse_count_rows(data: bytes, session: dict) -> List[dict]:
    try:
        workbook = load_workbook(BytesIO(data), data_only=False)
    except Exception as exc:
        raise CountImportError("File Excel tidak dapat dibaca.") from exc
    if COUNT_SHEET not in workbook.sheetnames:
        raise CountImportError("Lembar Hitung Fisik tidak ditemukan.")
    meta = _meta(workbook)
    if meta.get("session_id") != str(session.get("id") or ""):
        raise CountImportError("File Excel bukan untuk sesi stock opname ini.")
    if meta.get("warehouse_id") != str(session.get("warehouse_id") or ""):
        raise CountImportError("File Excel bukan untuk gudang sesi ini.")
    sheet = workbook[COUNT_SHEET]
    columns = _header_map(sheet)
    lines = list(session.get("lines") or [])
    by_id = {str(line.get("master_item_id") or ""): line for line in lines if line.get("master_item_id")}
    by_code: Dict[str, List[dict]] = {}
    for line in lines:
        code = str(line.get("item_code") or "").strip()
        if code:
            by_code.setdefault(code, []).append(line)
    seen = set()
    counts = []
    errors = []
    for offset, row in enumerate(sheet.iter_rows(min_row=2, values_only=True), start=2):
        if not row or all(value in (None, "") for value in row):
            continue
        item_id = ""
        if ID_HEADER in columns and columns[ID_HEADER] < len(row):
            item_id = str(row[columns[ID_HEADER]] or "").strip()
        code = str(row[columns["Kode Barang"]] or "").strip() if columns["Kode Barang"] < len(row) else ""
        line = by_id.get(item_id) if item_id else None
        if line is None:
            matches = by_code.get(code) or []
            if len(matches) == 1:
                line = matches[0]
            elif len(matches) > 1:
                errors.append(f"Baris {offset}: kode {code} tidak unik.")
                continue
        if line is None:
            label = code or item_id or str(offset)
            errors.append(f"Baris {offset}: barang {label} tidak ada pada sesi ini.")
            continue
        identity = str(line.get("master_item_id") or line.get("line_id"))
        if identity in seen:
            errors.append(f"Baris {offset}: barang {code or identity} duplikat.")
            continue
        seen.add(identity)
        raw_count = row[columns["Stock Fisik"]] if columns["Stock Fisik"] < len(row) else None
        if _blank_count(raw_count):
            continue
        try:
            qty = parse_count_qty(raw_count)
        except Exception as exc:
            detail = getattr(exc, "detail", None) or str(exc)
            errors.append(f"Baris {offset}: {detail}")
            continue
        counts.append({
            "line_id": line.get("line_id"),
            "physical_count_qty": qty,
        })
    if errors:
        raise CountImportError(" ".join(errors))
    return counts


async def import_physical_counts(db, session_id: str, data: bytes, user: dict) -> dict:
    session = await get_stock_opname_session(db, session_id)
    if session.get("status") != STATUS_COUNTING:
        raise CountImportError("Import hitung fisik hanya dapat dilakukan selama penghitungan.")
    counts = parse_count_rows(data, session)
    if not counts:
        return session
    # Import records qty + imported_at only. physical_count_at must be set explicitly.
    return await record_stock_opname_counts(
        db,
        session["id"],
        {"lines": counts, "source": "import"},
        user,
    )
