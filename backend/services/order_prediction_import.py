"""Supplemental historical orders for Prediksi Order.

NOT production POs. Stored separately and merged into the existing prediction engine.
"""
from __future__ import annotations

import csv
import hashlib
import io
import re
import uuid
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from services.import_parser import ImportParseError
from services.order_prediction import (
    WIB,
    customer_group_key,
    normalize_name,
    order_date_of,
    parse_iso_date,
    wib_today,
)

PERM_IMPORT = "reports.order_prediction.import"
COLLECTION = "order_prediction_history"
SOURCE_LIVE = "ORDER MASUK"
SOURCE_IMPORT = "IMPORT HISTORIS"

PREFERRED_SHEET = "DATA_ORDER"
_EMPTY_STREAK_LIMIT = 40
_HEADER_SCAN_ROWS = 40

_ALIASES = {
    "customer": [
        "customer", "nama_customer", "pelanggan", "customer_name",
        "nama_pelanggan", "nama_customer",
    ],
    "outlet": [
        "outlet", "cabang", "toko", "outlet_cabang", "branch",
        "cabang_outlet", "outlet_cabang", "cabang_/_outlet",
    ],
    "order_date": [
        "order_date", "tanggal_order", "tanggal", "tgl_order", "tgl",
        "date", "tanggal_po", "order_date",
    ],
    "customer_id": ["customer_id", "id_customer", "cust_id", "id_customer_otomatis"],
    "outlet_id": ["outlet_id", "delivery_outlet_id", "id_outlet"],
    "po_number": [
        "po_number", "no_order", "no_po", "nomor_po", "nomor_order",
        "order_number", "no_order_po",
    ],
    "sales": ["sales", "salesperson", "tenaga_penjual"],
    "notes": ["notes", "catatan", "keterangan"],
    "omzet": ["omzet", "omset", "revenue", "amount", "nilai"],
}
_HEADER_LOOKUP: Dict[str, str] = {}
for canon, aliases in _ALIASES.items():
    for a in aliases:
        _HEADER_LOOKUP[a] = canon

_WS = re.compile(r"[\s\-\./\\|]+")
_BOM = "\ufeff"


def _norm_header(k: str) -> str:
    k = (k or "").replace(_BOM, "")
    k = k.replace("\r", " ").replace("\n", " ").replace("\t", " ")
    k = k.strip().lower()
    k = _WS.sub("_", k)
    k = re.sub(r"_+", "_", k).strip("_")
    # Tolerate "cabang / outlet" style after slash collapsed oddly
    if k in _HEADER_LOOKUP:
        return _HEADER_LOOKUP[k]
    if "customer" in k and "id" not in k:
        return "customer"
    if k in ("cabang", "outlet") or (k.startswith("cabang") and "outlet" in k) or (
        k.startswith("outlet") and "cabang" in k
    ):
        return "outlet"
    if "tanggal" in k and "sebelum" not in k and "jarak" not in k:
        return "order_date"
    if k in ("omzet", "omset"):
        return "omzet"
    return _HEADER_LOOKUP.get(k, k)


def _header_keys(raw_headers: Sequence[Any]) -> List[str]:
    return [_norm_header("" if h is None else str(h)) for h in raw_headers]


def _has_valid_history_headers(keys: Sequence[str]) -> bool:
    s = set(keys)
    return "customer" in s and "outlet" in s and "order_date" in s


def _row_dict(headers: List[str], cells: Sequence[Any]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for i, h in enumerate(headers):
        key = _norm_header(h)
        val = cells[i] if i < len(cells) else None
        if val is None:
            out[key] = ""
        elif isinstance(val, (datetime, date, int, float)) and not isinstance(val, bool):
            out[key] = val
        else:
            out[key] = str(val).strip()
    return out


def _is_blank_row(cells: Sequence[Any]) -> bool:
    for c in cells:
        if c is None:
            continue
        if isinstance(c, str) and not c.strip():
            continue
        return False
    return True


def _cell_str(val: Any) -> str:
    if val is None:
        return ""
    if isinstance(val, float) and val != val:
        return ""
    return str(val).strip()


def _history_identity_empty(headers: Sequence[str], cells: Sequence[Any]) -> bool:
    """True when Nama Customer is blank (trailing Excel rows often keep Cabang filled)."""
    keys = _header_keys(headers)
    parsed = _row_dict(list(headers), cells)
    customer = _cell_str(parsed.get("customer"))
    if "customer" not in keys:
        return not _cell_str(cells[0] if cells else None)
    return not customer


def _parse_omzet(raw: Any) -> Tuple[Optional[float], Optional[str]]:
    if raw is None:
        return None, None
    if isinstance(raw, bool):
        return None, "Omzet harus angka atau kosong"
    if isinstance(raw, (int, float)):
        n = float(raw)
        if n != n or n == float("inf") or n == float("-inf"):
            return None, "Omzet tidak valid"
        if n < 0:
            return None, "Omzet tidak boleh negatif"
        if n == int(n):
            return int(n), None
        return n, None
    s = str(raw).strip()
    if not s or s in ("-", "–", "—", "null", "None", "n/a", "N/A"):
        return None, None
    s = s.replace("Rp", "").replace("rp", "").replace(" ", "")
    # id-ID: 1.234.567,89 or plain 1234567.89
    if re.match(r"^-?\d{1,3}(\.\d{3})+(,\d+)?$", s):
        s = s.replace(".", "").replace(",", ".")
    else:
        s = s.replace(",", "")
    try:
        n = float(s)
    except ValueError:
        return None, "Omzet harus angka atau kosong"
    if n != n or n == float("inf") or n == float("-inf"):
        return None, "Omzet tidak valid"
    if n < 0:
        return None, "Omzet tidak boleh negatif"
    if n == int(n):
        return int(n), None
    return n, None


def _excel_serial_to_date(n: float) -> Optional[date]:
    """Excel 1900-date system serial → date (openpyxl / Excel epoch)."""
    try:
        serial = float(n)
    except (TypeError, ValueError):
        return None
    if serial != serial or serial < 1 or serial > 1000000:
        return None
    # Excel incorrectly treats 1900 as leap year; openpyxl uses 1899-12-30 origin.
    return date(1899, 12, 30) + timedelta(days=int(serial))


def _parse_order_date(raw: Any, today: date) -> Tuple[Optional[date], Optional[str]]:
    if raw is None or (isinstance(raw, str) and not str(raw).strip()):
        return None, "Tanggal Order wajib"
    if isinstance(raw, datetime):
        d = raw.astimezone(WIB).date() if raw.tzinfo else raw.replace(tzinfo=WIB).astimezone(WIB).date()
    elif isinstance(raw, date) and not isinstance(raw, datetime):
        d = raw
    elif isinstance(raw, bool):
        return None, "Tanggal Order tidak valid"
    elif isinstance(raw, (int, float)):
        # Plain year like 2026 is not a serial; Excel serials for modern dates are ~40000–60000.
        if 20000 <= float(raw) <= 80000:
            d = _excel_serial_to_date(float(raw))
            if d is None:
                return None, "Tanggal Order tidak valid"
        else:
            return None, "Tanggal Order tidak valid"
    else:
        s = str(raw).strip()
        if not s or s in ("-", "–", "—"):
            return None, "Tanggal Order wajib"
        # Excel serial encoded as string
        if re.fullmatch(r"\d{4,6}(\.0+)?", s):
            d = _excel_serial_to_date(float(s))
            if d is None:
                return None, "Tanggal Order tidak valid"
        else:
            d = parse_iso_date(s)
            if d is None:
                # "2026-02-16 00:00:00" from str(datetime)
                for fmt in (
                    "%Y-%m-%d %H:%M:%S",
                    "%Y-%m-%d %H:%M:%S.%f",
                    "%d/%m/%Y",
                    "%d-%m-%Y",
                    "%Y/%m/%d",
                    "%d/%m/%y",
                    "%d-%m-%y",
                    "%d.%m.%Y",
                ):
                    try:
                        d = datetime.strptime(s, fmt).date()
                        break
                    except ValueError:
                        continue
            if d is None:
                return None, "Tanggal Order tidak valid"
    if d > today:
        return None, "Tanggal Order tidak boleh di masa depan"
    return d, None


def history_fingerprint(doc: dict) -> str:
    """Deterministic dedup key for imported (and live) history rows."""
    po = str(doc.get("po_number") or "").strip().lower()
    if po:
        return f"po:{po}"
    cid = str(doc.get("customer_id") or "").strip()
    oid = str(doc.get("outlet_id") or doc.get("delivery_outlet_id") or "").strip()
    d = order_date_of(doc)
    date_s = d.isoformat() if d else ""
    if cid or oid:
        return f"id:{cid}|oid:{oid}|d:{date_s}|po:{po}"
    cname = normalize_name(doc.get("customer") or doc.get("customer_name"))
    oname = normalize_name(doc.get("outlet"))
    notes = normalize_name(doc.get("notes"))
    blob = f"name:{cname}|out:{oname}|d:{date_s}|po:{po}|n:{notes}"
    return "fp:" + hashlib.sha256(blob.encode("utf-8")).hexdigest()[:24]


def as_prediction_order(doc: dict) -> dict:
    """Shape compatible with order_prediction.is_valid_business_order + analyze_group."""
    omzet = doc.get("omzet")
    if omzet is not None and str(omzet).strip() == "":
        omzet = None
    return {
        "id": doc.get("id"),
        "po_number": doc.get("po_number") or "",
        "order_date": doc.get("order_date"),
        "customer": doc.get("customer") or doc.get("customer_name") or "",
        "customer_name": doc.get("customer") or doc.get("customer_name") or "",
        "customer_id": doc.get("customer_id"),
        "outlet": doc.get("outlet") or "",
        "delivery_outlet_id": doc.get("outlet_id") or doc.get("delivery_outlet_id"),
        "outlet_id": doc.get("outlet_id") or doc.get("delivery_outlet_id"),
        "sales": doc.get("sales") or "",
        "notes": doc.get("notes") or "",
        "omzet": omzet,
        "cancelled": False,
        "source": SOURCE_IMPORT,
        "history_source": SOURCE_IMPORT,
        "_fingerprint": history_fingerprint(doc),
    }


def live_as_prediction_order(order: dict) -> dict:
    out = dict(order)
    out["source"] = SOURCE_LIVE
    out["history_source"] = SOURCE_LIVE
    out["omzet"] = order.get("omzet") if order.get("omzet") not in ("",) else None
    if out.get("omzet") is None:
        out["omzet"] = None
    out["_fingerprint"] = history_fingerprint(out)
    return out


def merge_prediction_orders(
    live_orders: Sequence[dict],
    imported: Sequence[dict],
) -> List[dict]:
    """Live Order Masuk wins on fingerprint collision."""
    merged: List[dict] = []
    seen: set = set()
    for o in live_orders:
        row = live_as_prediction_order(o)
        fp = row["_fingerprint"]
        if fp in seen:
            continue
        seen.add(fp)
        merged.append(row)
    for o in imported:
        row = as_prediction_order(o)
        fp = row["_fingerprint"]
        if fp in seen:
            continue
        seen.add(fp)
        merged.append(row)
    return merged


def _extract_table_from_matrix(
    matrix: List[List[Any]],
    *,
    sheet_name: Optional[str] = None,
) -> Tuple[List[str], List[List[Any]], str, int]:
    """Find history header row; return headers, data rows, sheet label, skipped empty count."""
    limit = min(len(matrix), _HEADER_SCAN_ROWS)
    for i in range(limit):
        raw = matrix[i]
        if _is_blank_row(raw):
            continue
        keys = _header_keys(raw)
        if not _has_valid_history_headers(keys):
            continue
        headers = [("" if c is None else str(c).strip()) for c in raw]
        data_rows: List[List[Any]] = []
        skipped = 0
        empty_streak = 0
        for r in matrix[i + 1 :]:
            if _is_blank_row(r) or _history_identity_empty(headers, r):
                skipped += 1
                empty_streak += 1
                if empty_streak >= _EMPTY_STREAK_LIMIT:
                    break
                continue
            empty_streak = 0
            # Skip title-like rows that reappear
            if _has_valid_history_headers(_header_keys(r)):
                continue
            data_rows.append(list(r))
        return headers, data_rows, sheet_name or "", skipped
    raise ImportParseError(
        "Header histori tidak ditemukan. Butuh kolom Customer, Outlet/Cabang, dan Tanggal Order"
        + (f" (sheet {sheet_name})." if sheet_name else ".")
    )


def _read_csv_matrix(data: bytes) -> List[List[Any]]:
    text = None
    for enc in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
        try:
            text = data.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    if text is None:
        raise ImportParseError("File CSV tidak bisa dibaca (encoding tidak dikenal).")
    sample = text[:2048]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel
    reader = csv.reader(io.StringIO(text), dialect=dialect)
    return [list(r) for r in reader]


def _sheet_rows(ws) -> List[List[Any]]:
    """Read worksheet rows with early stop after identity-empty streak past header."""
    rows: List[List[Any]] = []
    empty_streak = 0
    header_found = False
    headers: List[str] = []
    for r in ws.iter_rows(values_only=True):
        cells = list(r) if r is not None else []
        if not header_found:
            rows.append(cells)
            if len(rows) > _HEADER_SCAN_ROWS and not any(
                _has_valid_history_headers(_header_keys(x)) for x in rows
            ):
                break
            if not _is_blank_row(cells) and _has_valid_history_headers(_header_keys(cells)):
                header_found = True
                headers = [("" if c is None else str(c).strip()) for c in cells]
                empty_streak = 0
            continue
        # Past header: stop when many consecutive rows lack customer/outlet/date.
        if _is_blank_row(cells) or _history_identity_empty(headers, cells):
            empty_streak += 1
            rows.append(cells)
            if empty_streak >= _EMPTY_STREAK_LIMIT:
                break
            continue
        empty_streak = 0
        rows.append(cells)
    return rows


def _read_xlsx_history(data: bytes) -> Tuple[List[str], List[List[Any]], str, int]:
    try:
        from openpyxl import load_workbook  # type: ignore
    except ImportError as e:
        raise ImportParseError("openpyxl belum terpasang di server.") from e
    try:
        wb = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    except Exception as e:
        raise ImportParseError(f"Gagal membuka XLSX: file rusak atau bukan workbook Excel.") from e

    names = list(wb.sheetnames or [])
    if not names:
        raise ImportParseError("Workbook XLSX tidak berisi sheet.")

    preferred = None
    for n in names:
        if str(n).strip().upper() == PREFERRED_SHEET:
            preferred = n
            break

    errors: List[str] = []

    def try_sheet(name: str) -> Optional[Tuple[List[str], List[List[Any]], str, int]]:
        try:
            matrix = _sheet_rows(wb[name])
            return _extract_table_from_matrix(matrix, sheet_name=name)
        except ImportParseError as e:
            errors.append(f"{name}: {e}")
            return None

    if preferred is not None:
        got = try_sheet(preferred)
        if got is not None:
            return got

    for name in names:
        if preferred is not None and name == preferred:
            continue
        got = try_sheet(name)
        if got is not None:
            return got

    raise ImportParseError(
        "Tidak ada sheet dengan header histori valid "
        "(Customer + Outlet/Cabang + Tanggal Order). "
        "Preferensi: sheet DATA_ORDER."
    )


def read_history_rows(data: bytes, filename: str) -> Tuple[List[str], List[List[Any]], str, int]:
    """Parse CSV/XLSX into raw headers + cell rows (native types preserved for XLSX)."""
    name = (filename or "").lower().strip()
    if name.endswith(".csv") or name.endswith(".txt"):
        matrix = _read_csv_matrix(data)
        if not matrix:
            raise ImportParseError("File CSV kosong.")
        headers, rows, _, skipped = _extract_table_from_matrix(matrix, sheet_name="CSV")
        return headers, rows, "CSV", skipped
    if name.endswith(".xlsx") or name.endswith(".xlsm"):
        return _read_xlsx_history(data)
    if name.endswith(".xls"):
        raise ImportParseError("Format .xls tidak didukung. Simpan sebagai .xlsx atau .csv.")
    raise ImportParseError("Format tidak didukung. Gunakan .xlsx atau .csv.")


def build_history_import_preview(
    data: bytes,
    filename: str,
    *,
    live_orders: Sequence[dict],
    existing_imports: Sequence[dict],
    now: Optional[datetime] = None,
) -> dict:
    try:
        headers, raw_rows, sheet_name, skipped_empty = read_history_rows(data, filename)
    except ImportParseError:
        raise
    except Exception as e:
        raise ImportParseError(
            "Gagal membaca file histori. Pastikan file .xlsx/.csv valid "
            "dengan sheet DATA_ORDER atau header Customer, Outlet, Tanggal Order."
        ) from e

    today = wib_today(now)
    live_fps = {history_fingerprint(o) for o in live_orders}
    import_fps = {history_fingerprint(o) for o in existing_imports}
    file_seen: Dict[str, int] = {}

    rows: List[dict] = []
    for idx, cells in enumerate(raw_rows):
        if _is_blank_row(cells) or _history_identity_empty(headers, cells):
            skipped_empty += 1
            continue
        parsed = _row_dict(headers, cells)
        customer = parsed.get("customer")
        outlet = parsed.get("outlet")
        customer_s = "" if customer is None else str(customer).strip()
        outlet_s = "" if outlet is None else str(outlet).strip()

        status = "VALID"
        reason = None
        selectable = True
        order_date, date_err = _parse_order_date(parsed.get("order_date"), today)
        omzet, omzet_err = _parse_omzet(parsed.get("omzet"))
        if not customer_s and not str(parsed.get("customer_id") or "").strip():
            status = "INVALID"
            reason = "Nama Customer wajib"
            selectable = False
        elif not outlet_s and not str(parsed.get("outlet_id") or "").strip():
            status = "INVALID"
            reason = "Outlet/Cabang wajib"
            selectable = False
        elif date_err:
            status = "INVALID"
            reason = date_err
            selectable = False
        elif omzet_err:
            status = "INVALID"
            reason = omzet_err
            selectable = False

        draft = {
            "customer": customer_s,
            "customer_name": customer_s,
            "customer_id": (str(parsed.get("customer_id")).strip() if parsed.get("customer_id") not in (None, "") else None),
            "outlet": outlet_s,
            "outlet_id": (str(parsed.get("outlet_id")).strip() if parsed.get("outlet_id") not in (None, "") else None),
            "delivery_outlet_id": (str(parsed.get("outlet_id")).strip() if parsed.get("outlet_id") not in (None, "") else None),
            "order_date": order_date.isoformat() if order_date else None,
            "po_number": str(parsed.get("po_number") or "").strip(),
            "sales": str(parsed.get("sales") or "").strip(),
            "notes": str(parsed.get("notes") or "").strip(),
            "omzet": omzet,
        }
        fp = history_fingerprint(draft) if order_date or draft["po_number"] else f"row:{idx}"

        if status == "VALID":
            if fp in live_fps:
                status = "DUPLIKAT"
                reason = "Duplikat dengan Order Masuk (Order Masuk menang)"
                selectable = False
            elif fp in import_fps:
                status = "DUPLIKAT"
                reason = "Sudah pernah diimpor"
                selectable = False
            elif fp in file_seen:
                status = "DUPLIKAT"
                reason = f"Duplikat dalam file (baris {file_seen[fp] + 1})"
                selectable = False
            else:
                file_seen[fp] = idx

        rows.append({
            "row_id": f"r{idx}",
            "row_index": idx,
            "customer": customer_s,
            "outlet": outlet_s,
            "customer_id": draft["customer_id"],
            "outlet_id": draft["outlet_id"],
            "order_date": draft["order_date"],
            "po_number": draft["po_number"],
            "sales": draft["sales"] or None,
            "notes": draft["notes"] or None,
            "omzet": omzet,
            "fingerprint": fp,
            "status": status,
            "reason": reason,
            "selectable": selectable,
            "selected": selectable,
        })

    if not rows:
        raise ImportParseError(
            "File tidak berisi baris data histori."
            + (f" Sheet terdeteksi: {sheet_name}." if sheet_name else "")
        )

    file_fp = hashlib.sha256(data).hexdigest()[:16]
    return {
        "filename": filename,
        "sheet_name": sheet_name,
        "file_fingerprint": file_fp,
        "rows": rows,
        "total": len(rows),
        "valid_count": sum(1 for r in rows if r["status"] == "VALID"),
        "invalid_count": sum(1 for r in rows if r["status"] == "INVALID"),
        "duplicate_count": sum(1 for r in rows if r["status"] == "DUPLIKAT"),
        "skipped_empty": skipped_empty,
    }


def validate_history_apply_rows(
    selected: List[dict],
    *,
    live_orders: Sequence[dict],
    existing_imports: Sequence[dict],
    now: Optional[datetime] = None,
) -> Tuple[List[dict], List[dict]]:
    today = wib_today(now)
    live_fps = {history_fingerprint(o) for o in live_orders}
    import_fps = {history_fingerprint(o) for o in existing_imports}
    accepted: List[dict] = []
    rejected: List[dict] = []
    seen: set = set()

    for raw in selected or []:
        if not isinstance(raw, dict):
            rejected.append({"reason": "Baris tidak valid"})
            continue
        customer = str(raw.get("customer") or raw.get("customer_name") or "").strip()
        outlet = str(raw.get("outlet") or "").strip()
        if not customer and not str(raw.get("customer_id") or "").strip():
            rejected.append({**raw, "status": "INVALID", "reason": "Nama Customer wajib"})
            continue
        if not outlet and not str(raw.get("outlet_id") or "").strip():
            rejected.append({**raw, "status": "INVALID", "reason": "Outlet/Cabang wajib"})
            continue
        order_date, date_err = _parse_order_date(raw.get("order_date"), today)
        if date_err:
            rejected.append({**raw, "status": "INVALID", "reason": date_err})
            continue
        omzet, omzet_err = _parse_omzet(raw.get("omzet"))
        if omzet_err:
            rejected.append({**raw, "status": "INVALID", "reason": omzet_err})
            continue
        draft = {
            "id": str(uuid.uuid4()),
            "customer": customer,
            "customer_name": customer,
            "customer_id": str(raw.get("customer_id") or "").strip() or None,
            "outlet": outlet,
            "outlet_id": str(raw.get("outlet_id") or "").strip() or None,
            "delivery_outlet_id": str(raw.get("outlet_id") or raw.get("delivery_outlet_id") or "").strip() or None,
            "order_date": order_date.isoformat(),
            "po_number": str(raw.get("po_number") or "").strip(),
            "sales": str(raw.get("sales") or "").strip(),
            "notes": str(raw.get("notes") or "").strip(),
            "omzet": omzet,
            "source": SOURCE_IMPORT,
            "fingerprint": "",
        }
        draft["fingerprint"] = history_fingerprint(draft)
        fp = draft["fingerprint"]
        if fp in live_fps or fp in import_fps or fp in seen:
            rejected.append({**raw, "status": "DUPLIKAT", "reason": "Duplikat — dilewati"})
            continue
        seen.add(fp)
        accepted.append(draft)
    return accepted, rejected
