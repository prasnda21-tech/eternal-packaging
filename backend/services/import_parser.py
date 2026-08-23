"""File-based PO import parser (XLSX + CSV).

Contract: caller passes raw bytes + filename; parser returns a normalized
preview dict identical in shape to the Accurate preview (`_build_preview`
in server.py). Uploaded files never touch MongoDB before user confirmation
— server.py's confirm endpoint is what persists.

Expected columns (case-insensitive, flexible aliases):
  PO-level, repeated on each row:
    po_number*, order_date, customer*, outlet, recipient, address,
    payment_terms, sales, created_by_user, expedition, delivery_zone,
    estimated_delivery, notes
  Line-item columns (one row per line item):
    item_code*, item_name*, quantity*, unit, color, item_notes

Only ONE distinct po_number per file (single-PO import mode).
"""
from __future__ import annotations

import csv
import io
import re
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Tuple

# Column alias table — accept common Indonesian & English header names.
_ALIASES = {
    "po_number": ["po_number", "po", "no_po", "nomor_po", "sales_order", "so_number", "so"],
    "order_date": ["order_date", "tanggal_po", "tanggal_order", "tgl_po", "tgl"],
    "customer": ["customer", "pelanggan", "customer_name"],
    "outlet": ["outlet", "cabang", "toko"],
    "recipient": ["recipient", "penerima", "contact_person", "pic"],
    "address": ["address", "alamat"],
    "payment_terms": ["payment_terms", "termin", "term_bayar", "termin_bayar"],
    "sales": ["sales", "salesperson", "tenaga_penjual"],
    "created_by_user": ["created_by_user", "pembuat_po", "user_po", "sales_admin", "input_by"],
    "expedition": ["expedition", "ekspedisi", "kurir"],
    "delivery_zone": ["delivery_zone", "zona_kirim", "wilayah_kirim", "zona"],
    "estimated_delivery": ["estimated_delivery", "estimasi_kirim", "tgl_kirim", "delivery_date"],
    "notes": ["notes", "catatan", "keterangan", "printing_description"],
    "item_code": ["item_code", "kode_barang", "sku"],
    "item_name": ["item_name", "nama_barang", "product_name", "produk"],
    "quantity": ["quantity", "qty", "jumlah", "kuantitas"],
    "unit": ["unit", "satuan"],
    "color": ["color", "warna"],
    "item_notes": ["item_notes", "catatan_item", "item_note", "notes_item"],
}
_HEADER_LOOKUP: Dict[str, str] = {}
for canon, aliases in _ALIASES.items():
    for a in aliases:
        _HEADER_LOOKUP[a] = canon


class ImportParseError(ValueError):
    """Raised when the file cannot be parsed or is missing required data."""


def _norm(k: str) -> str:
    k = (k or "").strip().lower()
    # normalize whitespace / dashes to underscores
    k = re.sub(r"[\s\-\.]+", "_", k)
    return _HEADER_LOOKUP.get(k, k)


def _read_rows(data: bytes, filename: str) -> Tuple[List[str], List[List[str]]]:
    name = (filename or "").lower()
    if name.endswith(".csv") or name.endswith(".txt"):
        # Auto-detect encoding
        text = None
        for enc in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
            try:
                text = data.decode(enc)
                break
            except UnicodeDecodeError:
                continue
        if text is None:
            raise ImportParseError("File CSV tidak bisa dibaca (encoding tidak dikenal).")
        # Sniff delimiter
        sample = text[:2048]
        try:
            dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
        except csv.Error:
            dialect = csv.excel
        reader = csv.reader(io.StringIO(text), dialect=dialect)
        all_rows = [r for r in reader if any((c or "").strip() for c in r)]
        if not all_rows:
            raise ImportParseError("File CSV kosong.")
        header, *rows = all_rows
        return [c.strip() for c in header], rows
    if name.endswith(".xlsx"):
        try:
            from openpyxl import load_workbook  # type: ignore
        except ImportError as e:
            raise ImportParseError("openpyxl belum terpasang di server.") from e
        try:
            wb = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
        except Exception as e:
            raise ImportParseError(f"Gagal membuka XLSX: {e}")
        ws = wb.active
        rows_iter = ws.iter_rows(values_only=True)
        try:
            header = next(rows_iter)
        except StopIteration:
            raise ImportParseError("Sheet XLSX kosong.")
        header = [str(c).strip() if c is not None else "" for c in header]
        data_rows: List[List[str]] = []
        for r in rows_iter:
            if not any(c not in (None, "") for c in r):
                continue
            data_rows.append(["" if c is None else str(c) for c in r])
        return header, data_rows
    raise ImportParseError("Format tidak didukung. Gunakan .xlsx atau .csv.")


def _to_int(v: Any) -> int:
    if v is None or v == "":
        return 0
    if isinstance(v, (int, float)):
        return int(v)
    s = str(v).strip().replace(".", "").replace(",", "")
    try:
        return int(float(s))
    except ValueError:
        return 0


def _parse_date(v: Any) -> str | None:
    if not v:
        return None
    if isinstance(v, datetime):
        return v.replace(tzinfo=v.tzinfo or timezone.utc).isoformat()
    s = str(v).strip()
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%Y/%m/%d", "%d %b %Y", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(s, fmt).replace(tzinfo=timezone.utc).isoformat()
        except ValueError:
            continue
    return None


def parse_import_file(data: bytes, filename: str) -> Dict[str, Any]:
    """Parse an uploaded XLSX/CSV and return a preview dict.

    Shape matches the Accurate preview shape used by /accurate/import/preview,
    plus an `import_source` field ("file") and the detected `filename`.
    """
    header, rows = _read_rows(data, filename)
    if not rows:
        raise ImportParseError("File tidak berisi data baris.")
    norm_header = [_norm(h) for h in header]

    def col(row: List[str], key: str) -> str:
        if key not in norm_header:
            return ""
        idx = norm_header.index(key)
        return (row[idx] if idx < len(row) else "").strip()

    # Required at PO level: po_number + customer. At line level: item_name + quantity.
    if "po_number" not in norm_header:
        raise ImportParseError("Kolom 'po_number' (atau 'nomor_po') wajib ada di header.")
    if "item_name" not in norm_header and "item_code" not in norm_header:
        raise ImportParseError("Kolom line item ('item_name' atau 'item_code') wajib ada di header.")
    if "quantity" not in norm_header:
        raise ImportParseError("Kolom 'quantity' (atau 'qty') wajib ada di header.")

    po_numbers = {col(r, "po_number") for r in rows if col(r, "po_number")}
    po_numbers.discard("")
    if not po_numbers:
        raise ImportParseError("Tidak ada 'po_number' pada baris data.")
    if len(po_numbers) > 1:
        raise ImportParseError(f"File berisi {len(po_numbers)} PO ({', '.join(sorted(po_numbers))[:120]}). Import file hanya mendukung 1 PO per file.")
    po_number = po_numbers.pop()

    po_rows = [r for r in rows if col(r, "po_number") == po_number]
    first = po_rows[0]

    def pick(key: str) -> str | None:
        # take first non-empty value across rows so PO-level fields can be blank on later rows
        for r in po_rows:
            v = col(r, key)
            if v:
                return v
        return None

    line_items: List[Dict[str, Any]] = []
    for r in po_rows:
        name = col(r, "item_name") or col(r, "item_code")
        qty = _to_int(col(r, "quantity"))
        if not name or qty <= 0:
            continue
        line_items.append({
            "item_code": col(r, "item_code") or None,
            "name": name,
            "quantity": qty,
            "unit": col(r, "unit") or "pcs",
            "color": col(r, "color") or None,
            "notes": col(r, "item_notes") or None,
        })

    if not line_items:
        raise ImportParseError("Tidak ada line item valid (butuh nama barang + kuantitas > 0).")

    now = datetime.now(timezone.utc)
    order_date = _parse_date(pick("order_date")) or now.isoformat()
    est_delivery = _parse_date(pick("estimated_delivery")) or (now + timedelta(days=3)).isoformat()
    total_qty = sum(li["quantity"] for li in line_items)

    return {
        "import_source": "file",
        "filename": filename,
        "accurate_id": None,          # file imports have no external Accurate id
        "po_number": po_number,
        "customer": pick("customer"),
        "outlet": pick("outlet"),
        "recipient": pick("recipient"),
        "address": pick("address"),
        "payment_terms": pick("payment_terms"),
        "sales": pick("sales"),
        "created_by_user": pick("created_by_user"),
        "expedition": pick("expedition"),
        "delivery_zone": pick("delivery_zone"),
        "seller": pick("sales"),
        "order_date": order_date,
        "estimated_delivery": est_delivery,
        "deadline": est_delivery,
        "notes": pick("notes") or "",
        "line_items": line_items,
        "total_quantity": total_qty,
        "item_count": len(line_items),
    }
