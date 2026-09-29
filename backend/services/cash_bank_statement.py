"""Generic CSV/XLSX bank-statement import.

One header layout is recognized from its columns, not from the file name or
account number. Every other CSV/XLSX file stays on the generic mapping.
Preview stores the file and expires. Commit re-parses those bytes and
recomputes totals. Statement rows are not journals.
"""
from __future__ import annotations

import asyncio
import csv
import hashlib
import io
import re
import time
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, List, Optional, Sequence, Tuple

from bson.binary import Binary
from openpyxl import load_workbook
from pymongo.errors import DuplicateKeyError

from services.cash_bank_master import (
    COLLECTION as CASH_BANK_ACCOUNTS,
    CURRENCY_WRITER,
    CURRENCY_WRITER_UNTIL,
    CashBankValidationError,
    IMPORT_READERS,
    _writer_takeover_filter,
    coordination_deadline,
    get_cash_bank_raw,
    normalize_currency_code,
)
from services.cash_bank_statement_layout import (
    LayoutRejected,
    classify_matrix,
    interpret_recognized,
    line_number,
    suggested_mapping,
)
from services.purchase_common import actor_snapshot, now_iso

IMPORTS = "cash_bank_statement_imports"
LINES = "cash_bank_statement_lines"
PREVIEWS = "cash_bank_statement_previews"
MANUAL_BALANCES = "cash_bank_manual_statement_balances"
IMPORT_FENCES = "cash_bank_import_fences"

MAX_BYTES = 2_000_000
MAX_ROWS = 2000
MAX_COLS = 40
PREVIEW_TTL = timedelta(hours=2)
MONEY = Decimal("0.01")

DATE_FORMATS = {
    "YYYY-MM-DD": "%Y-%m-%d",
    "DD/MM/YYYY": "%d/%m/%Y",
    "MM/DD/YYYY": "%m/%d/%Y",
    "DD-MM-YYYY": "%d-%m-%Y",
}

STATUS_IMPORTING = "importing"
STATUS_COMPLETE = "complete"
STATUS_FAILED = "failed"

DUP_CLEAR = "clear"
DUP_PENDING = "pending_review"
DUP_KEPT = "kept"
DUP_EXCLUDED = "excluded"
DUP_DEFINITE = "definite_duplicate"
AUTHORITATIVE_TXN_INDEX = "uniq_authoritative_bank_txn"


async def _before_currency_pin() -> None:
    """Test seam before this commit pins the account currency."""
    return None


async def _after_currency_pin() -> None:
    """Test seam after the currency pin is stored and before statement rows are written."""
    return None


async def _on_import_fence_busy(cash_bank_id: str) -> None:
    """Test seam when another import already holds this account's classification fence."""
    return None


async def _before_statement_lines_insert() -> None:
    """Test seam. Production leaves this empty. Placed after the duplicate scan."""
    return None


async def _after_import_fence_acquired() -> None:
    """Test seam after this attempt owns the fence and before classification."""
    return None


async def _before_duplicate_resolution() -> None:
    """Test seam immediately before the duplicate-resolution compare-and-swap."""
    return None


async def _before_fenced_write(_kind: str) -> None:
    """Test seam after this attempt's last ownership check and before the business write."""
    return None


DISPLACED_WRITER = "displaced"


class StatementValidationError(ValueError):
    def __init__(self, message: str, errors: Optional[List[dict]] = None):
        super().__init__(message)
        self.errors = errors or []


class StatementConflictError(Exception):
    pass


async def _after_statement_line_insert(_index: int) -> None:
    """Test seam after one statement line is durable."""
    return None


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _mapping_hash(mapping: dict) -> str:
    import json
    raw = json.dumps(mapping, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _money(raw: Decimal) -> Decimal:
    return raw.quantize(MONEY)


def _money_float(raw: Decimal) -> float:
    return float(_money(raw))


async def ensure_indexes(db) -> None:
    await db[IMPORTS].create_index("id", unique=True)
    await db[IMPORTS].create_index(
        [("cash_bank_id", 1), ("file_sha256", 1)],
        unique=True,
        name="uniq_statement_file_account",
    )
    await db[IMPORTS].create_index([("cash_bank_id", 1), ("created_at", -1)])
    await db[LINES].create_index("id", unique=True)
    await db[LINES].create_index("import_id")
    await db[LINES].create_index([("cash_bank_id", 1), ("bank_txn_id", 1)])
    await db[LINES].create_index([("cash_bank_id", 1), ("date", 1)])
    await db[LINES].create_index(
        [("cash_bank_id", 1), ("authoritative_txn_id", 1)],
        unique=True,
        partialFilterExpression={"authoritative_txn_id": {"$type": "string"}},
        name=AUTHORITATIVE_TXN_INDEX,
    )
    await db[PREVIEWS].create_index("id", unique=True)
    await db[PREVIEWS].create_index("expires_at")
    await db[MANUAL_BALANCES].create_index(
        [("cash_bank_id", 1), ("as_of", 1)],
        unique=True,
        name="uniq_manual_statement_balance",
    )
    await db[IMPORT_FENCES].create_index("cash_bank_id", unique=True, name="uniq_import_fence_account")


class ParsedMatrix(list):
    """Rows plus the 1-based file line where each record starts."""

    def __init__(self, rows: Sequence[Sequence[Any]], line_numbers: Sequence[int]):
        super().__init__(rows)
        self.line_numbers = list(line_numbers)


def _decode_csv_text(data: bytes) -> str:
    if data.startswith(b"\xff\xfe") or data.startswith(b"\xfe\xff"):
        raise StatementValidationError("Encoding UTF-16 tidak dibaca. Encoding tidak ditebak.")
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise StatementValidationError("Berkas CSV bukan UTF-8. Encoding tidak ditebak.") from exc


def _matrix_from_csv(data: bytes) -> ParsedMatrix:
    text = _decode_csv_text(data)
    sample = text[:4096]
    delimiter = ","
    if sample:
        counts = {d: sample.count(d) for d in (",", ";", "\t")}
        delimiter = max(counts, key=counts.get)
        if counts[delimiter] == 0:
            delimiter = ","
    physical = text.splitlines(keepends=True)
    consumed = {"n": 0}

    def _lines():
        while consumed["n"] < len(physical):
            item = physical[consumed["n"]]
            consumed["n"] += 1
            yield item

    reader = csv.reader(_lines(), delimiter=delimiter)
    rows: List[List[Any]] = []
    line_numbers: List[int] = []
    while True:
        start = consumed["n"] + 1
        try:
            row = next(reader)
        except StopIteration:
            break
        if len(rows) >= MAX_ROWS:
            raise StatementValidationError(f"Berkas melebihi {MAX_ROWS} baris")
        rows.append(list(row[:MAX_COLS]))
        line_numbers.append(start)
    return ParsedMatrix(rows, line_numbers)


def _matrix_from_xlsx(data: bytes) -> List[List[Any]]:
    try:
        wb = load_workbook(
            io.BytesIO(data),
            read_only=True,
            data_only=False,
            keep_links=False,
        )
    except Exception as exc:
        raise StatementValidationError("Berkas XLSX tidak dapat dibaca") from exc
    try:
        ws = wb.worksheets[0] if wb.worksheets else None
        if ws is None:
            raise StatementValidationError("Berkas XLSX tidak memiliki lembar")
        rows: List[List[Any]] = []
        # read_only yields stored rows only. data_only stays false so formulas
        # are rejected instead of evaluated. keep_links stays false.
        for cells in ws.iter_rows(max_col=MAX_COLS):
            if len(rows) >= MAX_ROWS:
                raise StatementValidationError(f"Berkas melebihi {MAX_ROWS} baris")
            values = []
            for cell in cells:
                if getattr(cell, "data_type", None) == "f":
                    raise StatementValidationError(
                        "Sel formula tidak dievaluasi dan tidak diimpor"
                    )
                values.append(cell.value)
            rows.append(values)
        return ParsedMatrix(rows, list(range(1, len(rows) + 1)))
    finally:
        wb.close()


def read_matrix(filename: str, data: bytes) -> List[List[Any]]:
    name = str(filename or "").lower()
    if name.endswith(".csv") or name.endswith(".txt"):
        return _matrix_from_csv(data)
    if name.endswith(".xlsx"):
        return _matrix_from_xlsx(data)
    raise StatementValidationError("Format tidak didukung. Gunakan .csv atau .xlsx")


def _cell(row: Sequence[Any], index: Optional[int]) -> Any:
    if index is None:
        return None
    if index < 0 or index >= len(row):
        return None
    return row[index]


def _as_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()


def parse_date(value: Any, date_format: str) -> str:
    if isinstance(value, datetime):
        return value.date().isoformat()
    if hasattr(value, "isoformat") and not isinstance(value, str):
        try:
            return value.isoformat()[:10]
        except Exception as exc:
            raise StatementValidationError("Tanggal tidak valid") from exc
    text = _as_text(value)
    if not text:
        raise StatementValidationError("Tanggal kosong")
    if isinstance(value, (int, float)) or text.replace(".", "", 1).isdigit():
        raise StatementValidationError("Tanggal numerik ambigu dan tidak ditebak")
    fmt = DATE_FORMATS.get(date_format)
    if not fmt:
        raise StatementValidationError("Format tanggal tidak didukung")
    try:
        parsed = datetime.strptime(text, fmt)
    except ValueError as exc:
        raise StatementValidationError(
            f"Tanggal '{text}' tidak sesuai format {date_format}"
        ) from exc
    return parsed.date().isoformat()


def parse_amount(value: Any, decimal_mode: str) -> Optional[Decimal]:
    if value is None or value == "":
        return None
    if isinstance(value, Decimal):
        return _money(value)
    if isinstance(value, bool):
        raise StatementValidationError("Nominal tidak valid")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return _money(Decimal(str(value)))
    text = _as_text(value).replace(" ", "").replace("Rp", "").replace("IDR", "")
    if not text:
        return None
    negative = False
    if text.startswith("(") and text.endswith(")"):
        negative = True
        text = text[1:-1]
    if text.startswith("-"):
        negative = True
        text = text[1:]
    if text.startswith("+"):
        text = text[1:]
    last_comma = text.rfind(",")
    last_dot = text.rfind(".")
    if decimal_mode == "comma_decimal":
        if last_dot != -1 and last_comma != -1 and last_dot > last_comma:
            raise StatementValidationError("Pemisah desimal tidak sesuai mode koma")
        if text.count(",") > 1:
            raise StatementValidationError("Koma desimal ganda")
        if last_dot != -1 and last_comma == -1:
            if not re.fullmatch(r"\d{1,3}(\.\d{3})+", text):
                raise StatementValidationError(
                    "Titik pada nominal ambigu untuk mode koma dan tidak diubah menjadi pemisah ribuan"
                )
            body = text.replace(".", "")
        else:
            body = text.replace(".", "").replace(",", ".")
    elif decimal_mode == "dot_decimal":
        if last_comma != -1 and last_dot != -1 and last_comma > last_dot:
            raise StatementValidationError("Pemisah desimal tidak sesuai mode titik")
        if text.count(".") > 1:
            raise StatementValidationError("Titik desimal ganda atau ribuan ambigu")
        if last_comma != -1 and last_dot == -1:
            if not re.fullmatch(r"\d{1,3}(,\d{3})+", text):
                raise StatementValidationError(
                    "Koma pada nominal ambigu untuk mode titik dan tidak dihapus"
                )
            body = text.replace(",", "")
        else:
            body = text.replace(",", "")
    else:
        raise StatementValidationError("Mode desimal tidak didukung")
    try:
        amount = _money(Decimal(body))
    except (InvalidOperation, ValueError) as exc:
        raise StatementValidationError("Nominal tidak valid") from exc
    if negative:
        amount = -amount
    return amount


def normalize_mapping(raw: dict) -> dict:
    data = dict(raw or {})
    allowed = {
        "header_row", "date_column", "date_format", "description_column",
        "reference_column", "bank_txn_id_column", "bank_txn_id_authoritative",
        "amount_mode", "amount_column", "indicator_column",
        "sign_convention", "in_column", "out_column", "balance_column",
        "currency_column", "file_currency", "decimal_mode",
        "operator_coverage_confirmed",
    }
    unknown = set(data) - allowed
    if unknown:
        raise StatementValidationError("Kolom mapping tidak dikenal: " + ", ".join(sorted(unknown)))
    try:
        header_row = int(data.get("header_row", 0))
        date_column = int(data["date_column"])
        description_column = int(data["description_column"])
    except (KeyError, TypeError, ValueError) as exc:
        raise StatementValidationError("Mapping tanggal dan uraian wajib") from exc
    date_format = str(data.get("date_format") or "")
    if date_format not in DATE_FORMATS:
        raise StatementValidationError("Format tanggal wajib dipilih")
    decimal_mode = str(data.get("decimal_mode") or "")
    if decimal_mode not in ("comma_decimal", "dot_decimal"):
        raise StatementValidationError("Mode desimal wajib dipilih")
    amount_mode = str(data.get("amount_mode") or "")
    if amount_mode not in ("signed", "in_out", "indicator"):
        raise StatementValidationError("Mode nominal wajib: signed, in_out, atau indicator")

    def opt_int(key: str) -> Optional[int]:
        if key not in data or data.get(key) in (None, ""):
            return None
        return int(data[key])

    mapping = {
        "header_row": header_row,
        "date_column": date_column,
        "date_format": date_format,
        "description_column": description_column,
        "reference_column": opt_int("reference_column"),
        "bank_txn_id_column": opt_int("bank_txn_id_column"),
        "bank_txn_id_authoritative": bool(data.get("bank_txn_id_authoritative")),
        "amount_mode": amount_mode,
        "amount_column": opt_int("amount_column"),
        "sign_convention": str(data.get("sign_convention") or ""),
        "in_column": opt_int("in_column"),
        "out_column": opt_int("out_column"),
        "balance_column": opt_int("balance_column"),
        "currency_column": opt_int("currency_column"),
        "file_currency": None,
        "decimal_mode": decimal_mode,
        "operator_coverage_confirmed": bool(data.get("operator_coverage_confirmed")),
    }
    if data.get("file_currency") not in (None, ""):
        mapping["file_currency"] = normalize_currency_code(data.get("file_currency"))
    if mapping["bank_txn_id_authoritative"] and mapping["bank_txn_id_column"] is None:
        raise StatementValidationError(
            "ID transaksi bank yang otoritatif membutuhkan kolom ID. Kolom referensi tidak dianggap unik."
        )
    if amount_mode == "signed":
        if mapping["amount_column"] is None:
            raise StatementValidationError("Kolom nominal wajib")
        if mapping["sign_convention"] not in ("negative_out", "negative_in"):
            raise StatementValidationError(
                "Konvensi tanda wajib dipilih. Label debit/kredit tidak menentukan arah."
            )
    elif amount_mode == "indicator":
        mapping["indicator_column"] = opt_int("indicator_column")
        if mapping["amount_column"] is None or mapping["indicator_column"] is None:
            raise StatementValidationError("Kolom nominal dan penanda arah wajib")
        if mapping["decimal_mode"] != "dot_decimal":
            raise StatementValidationError(
                "Layout ini memakai nominal titik dua digit. Mode koma tidak dipakai agar nominal tidak berubah."
            )
        if mapping["in_column"] is not None or mapping["out_column"] is not None:
            raise StatementValidationError("Mode penanda arah tidak memakai kolom masuk dan keluar")
        if mapping["sign_convention"]:
            raise StatementValidationError("Arah tidak diambil dari konvensi tanda")
    else:
        if mapping["in_column"] is None or mapping["out_column"] is None:
            raise StatementValidationError("Kolom masuk dan keluar wajib")
        if mapping["in_column"] == mapping["out_column"]:
            raise StatementValidationError("Kolom masuk dan keluar harus berbeda")
    used = [
        mapping["date_column"], mapping["description_column"],
        mapping["reference_column"], mapping["bank_txn_id_column"],
        mapping["amount_column"], mapping.get("indicator_column"), mapping["in_column"], mapping["out_column"],
        mapping["balance_column"], mapping["currency_column"],
    ]
    concrete = [n for n in used if n is not None]
    if len(concrete) != len(set(concrete)):
        raise StatementValidationError("Satu kolom tidak boleh dipakai dua kali")
    return mapping


def _direction_from_signed(amount: Decimal, convention: str) -> Tuple[str, Decimal]:
    if amount == 0:
        raise StatementValidationError("Nominal nol tidak punya arah")
    if convention == "negative_out":
        direction = "out" if amount < 0 else "in"
    elif convention == "negative_in":
        direction = "in" if amount < 0 else "out"
    else:
        raise StatementValidationError("Konvensi tanda tidak valid")
    return direction, abs(amount)


def interpret_rows(matrix: List[List[Any]], mapping: dict) -> dict:
    """Return accepted rows plus per-row errors. Does not write."""
    if mapping.get("amount_mode") == "indicator":
        try:
            return interpret_recognized(matrix, mapping)
        except LayoutRejected as exc:
            raise StatementValidationError(str(exc), errors=exc.errors) from exc
    header_row = mapping["header_row"]
    if header_row < 0 or header_row >= len(matrix):
        raise StatementValidationError("Baris header di luar berkas")
    errors: List[dict] = []
    accepted: List[dict] = []
    data_rows = matrix[header_row + 1:]
    if not data_rows:
        raise StatementValidationError("Tidak ada baris data di bawah header")
    total_in = Decimal("0.00")
    total_out = Decimal("0.00")
    closing = None
    closing_date = None
    for offset, row in enumerate(data_rows, start=1):
        source_row = line_number(matrix, header_row + offset)
        if not any(_as_text(c) for c in row):
            continue
        try:
            if any(isinstance(c, str) and c.strip().startswith("=") for c in row):
                raise StatementValidationError("Teks formula tidak diimpor")
            date = parse_date(_cell(row, mapping["date_column"]), mapping["date_format"])
            description = _as_text(_cell(row, mapping["description_column"]))
            if not description:
                raise StatementValidationError("Uraian kosong")
            reference = _as_text(_cell(row, mapping["reference_column"])) or None
            bank_txn_id = _as_text(_cell(row, mapping["bank_txn_id_column"])) or None
            if mapping["amount_mode"] == "signed":
                amount = parse_amount(_cell(row, mapping["amount_column"]), mapping["decimal_mode"])
                if amount is None:
                    raise StatementValidationError("Nominal kosong")
                direction, absolute = _direction_from_signed(amount, mapping["sign_convention"])
            else:
                money_in = parse_amount(_cell(row, mapping["in_column"]), mapping["decimal_mode"])
                money_out = parse_amount(_cell(row, mapping["out_column"]), mapping["decimal_mode"])
                money_in = money_in or Decimal("0.00")
                money_out = money_out or Decimal("0.00")
                if money_in < 0 or money_out < 0:
                    raise StatementValidationError("Kolom masuk/keluar tidak boleh negatif")
                if money_in > 0 and money_out > 0:
                    raise StatementValidationError("Baris tidak boleh berisi masuk dan keluar sekaligus")
                if money_in == 0 and money_out == 0:
                    raise StatementValidationError("Nominal masuk/keluar kosong")
                if money_in > 0:
                    direction, absolute = "in", money_in
                else:
                    direction, absolute = "out", money_out
            balance = None
            if mapping["balance_column"] is not None:
                balance = parse_amount(_cell(row, mapping["balance_column"]), mapping["decimal_mode"])
            currency = mapping["file_currency"]
            if mapping["currency_column"] is not None:
                raw_ccy = _as_text(_cell(row, mapping["currency_column"]))
                cell_ccy = normalize_currency_code(raw_ccy) if raw_ccy else None
                if currency and cell_ccy and currency != cell_ccy:
                    raise StatementValidationError("Mata uang sel berbeda dari mata uang berkas")
                currency = cell_ccy or currency
            if direction == "in":
                total_in += absolute
            else:
                total_out += absolute
            if balance is not None:
                closing = balance
                closing_date = date
            accepted.append({
                "source_row": source_row,
                "row_number": offset,
                "date": date,
                "description": description,
                "reference": reference,
                "bank_txn_id": bank_txn_id,
                "direction": direction,
                "amount": _money_float(absolute),
                "running_balance": None if balance is None else _money_float(balance),
                "currency_code": currency,
            })
        except (StatementValidationError, CashBankValidationError) as exc:
            errors.append({"row": source_row, "message": str(exc)})
    return {
        "errors": errors,
        "accepted": accepted,
        "accepted_count": len(accepted),
        "total_in": _money_float(total_in),
        "total_out": _money_float(total_out),
        "closing_balance": None if closing is None else _money_float(closing),
        "closing_date": closing_date,
        "headers": [_as_text(c) for c in matrix[header_row]],
    }


def _sample(matrix: List[List[Any]], limit: int = 8, include_through: Optional[int] = None) -> List[List[str]]:
    end = limit
    if include_through is not None:
        end = max(end, include_through + 1)
    end = min(end, len(matrix), 30)
    out = []
    for row in matrix[:end]:
        out.append([_as_text(c) for c in row])
    return out


async def create_preview(db, *, cash_bank_id: str, filename: str, data: bytes, user: Optional[dict]) -> dict:
    cb_id = str(cash_bank_id or "").strip()
    if not cb_id:
        raise StatementValidationError("Kas/Bank wajib dipilih")
    cb = await get_cash_bank_raw(db, cb_id)
    if not cb:
        raise StatementValidationError("Kas/Bank tidak ditemukan")
    if not data:
        raise StatementValidationError("Berkas kosong")
    if len(data) > MAX_BYTES:
        raise StatementValidationError("Berkas melebihi batas 2 MB")
    matrix = read_matrix(filename, data)
    if not matrix:
        raise StatementValidationError("Berkas tidak berisi baris")
    classified = classify_matrix(matrix)
    if classified.get("status") == "ambiguous":
        raise StatementValidationError(classified["message"], errors=classified.get("errors") or [])
    suggestion = suggested_mapping(classified)
    now = datetime.now(timezone.utc)
    doc = {
        "id": str(uuid.uuid4()),
        "kind": "preview",
        "cash_bank_id": cb_id,
        "filename": str(filename or "mutasi"),
        "file_sha256": _sha256(data),
        "file_bytes": Binary(data),
        "row_count": len(matrix),
        "sample_rows": _sample(
            matrix,
            include_through=None if suggestion is None else suggestion["header_row"] + 1,
        ),
        "suggested_mapping": suggestion,
        "created_at": now.isoformat(),
        "expires_at": (now + PREVIEW_TTL).isoformat(),
        "created_by": actor_snapshot(user),
        "account_currency_code": cb.get("currency_code") or None,
        "currency_context_note": (
            "Konteks mata uang rekening saat pratinjau. Penetapan operator, bukan bukti independen bank."
        ),
    }
    await db[PREVIEWS].insert_one(dict(doc))
    doc.pop("_id", None)
    doc.pop("file_bytes", None)
    doc["currency_code"] = cb.get("currency_code") or None
    doc["account_currency_code"] = cb.get("currency_code") or None
    doc["currency_context_note"] = (
        "Konteks mata uang rekening saat pratinjau. Penetapan operator, bukan bukti independen bank."
    )
    doc["persisted_statement_lines"] = False
    return doc


async def _load_preview(db, preview_id: str) -> dict:
    doc = await db[PREVIEWS].find_one({"id": str(preview_id)}, {"_id": 0})
    if not doc:
        raise StatementValidationError("Pratinjau tidak ditemukan atau sudah tidak berlaku")
    if str(doc.get("expires_at") or "") < datetime.now(timezone.utc).isoformat():
        raise StatementValidationError("Pratinjau kedaluwarsa. Unggah berkas kembali.")
    return doc


def _currency_changed_since_preview(preview: dict, account: Optional[dict]) -> bool:
    current = (account or {}).get("currency_code") or None
    recorded = preview.get("account_currency_code") or None
    return recorded != current


async def validate_preview(db, preview_id: str, mapping_raw: dict) -> dict:
    preview = await _load_preview(db, preview_id)
    account = await get_cash_bank_raw(db, preview.get("cash_bank_id") or "")
    if _currency_changed_since_preview(preview, account):
        raise StatementConflictError(
            "Mata uang rekening berubah setelah pratinjau. Validasi ulang setelah pratinjau baru. "
            "Commit tidak dilanjutkan."
        )
    mapping = normalize_mapping(mapping_raw)
    data = bytes(preview.get("file_bytes") or b"")
    matrix = read_matrix(preview.get("filename") or "", data)
    parsed = interpret_rows(matrix, mapping)
    return {
        "preview_id": preview["id"],
        "expires_at": preview.get("expires_at"),
        "ok": not parsed["errors"],
        "errors": parsed["errors"],
        "accepted_count": parsed["accepted_count"],
        "total_in": parsed["total_in"],
        "total_out": parsed["total_out"],
        "closing_balance": parsed["closing_balance"],
        "closing_date": parsed["closing_date"],
        "headers": parsed["headers"],
        "sample_accepted": parsed["accepted"][:8],
        "opening_balance": parsed.get("opening_balance"),
        "footer_ending_balance": parsed.get("footer_ending_balance"),
        "balance_equation_ok": parsed.get("balance_equation_ok"),
        "balance_checks": parsed.get("balance_checks") or [],
        "inconsistencies": parsed.get("inconsistencies") or [],
        "persisted_statement_lines": False,
        "mapping_hash": _mapping_hash(mapping),
    }


def _line_identity(row: dict) -> str:
    return "|".join([
        str(row.get("date") or ""),
        str(row.get("direction") or ""),
        str(row.get("amount") or ""),
        str(row.get("description") or ""),
        str(row.get("bank_txn_id") or ""),
        str(row.get("reference") or ""),
        str(row.get("currency_code") or ""),
        str(row.get("duplicate_status") or ""),
        str(row.get("authoritative_txn_id") or ""),
    ])


async def _account_import_epoch(db, cash_bank_id: str) -> int:
    account = await db[CASH_BANK_ACCOUNTS].find_one(
        {"id": str(cash_bank_id)}, {"_id": 0, "import_epoch": 1},
    )
    return int((account or {}).get("import_epoch") or 0)


def _epoch_clause(epoch: int) -> Dict[str, Any]:
    """Lines written before this field existed stay on generation 0."""
    if int(epoch) == 0:
        return {"$or": [{"import_epoch": 0}, {"import_epoch": {"$exists": False}}]}
    return {"import_epoch": int(epoch)}


async def _bump_import_epoch(db, cash_bank_id: str) -> None:
    """Advance the classification generation. Does not change amounts or decisions."""
    cash_bank_id = str(cash_bank_id)
    await db[CASH_BANK_ACCOUNTS].update_one({"id": cash_bank_id}, {"$inc": {"import_epoch": 1}})
    await db[LINES].update_many({"cash_bank_id": cash_bank_id}, {"$inc": {"import_epoch": 1}})


async def _reassign_stale_import_writers(
    db, cash_bank_id: str, new_attempt: Optional[str], keep_batch_id: Optional[str],
) -> None:
    """The fence holder adopts every importing writer except itself.

    A previous attempt can die after the fence compare-and-swap and before its
    own writer id was stored. Matching only that dead holder would leave the
    older writer in place.
    """
    cash_bank_id = str(cash_bank_id)
    owner = str(new_attempt or "")
    if keep_batch_id and owner:
        await db[IMPORTS].update_one(
            {
                "id": keep_batch_id,
                "status": STATUS_IMPORTING,
                "writer_attempt": {"$ne": owner},
            },
            {"$set": {"writer_attempt": owner}},
        )
        await db[LINES].update_many(
            {
                "import_id": keep_batch_id,
                "sealed": False,
                "writer_attempt": {"$ne": owner},
            },
            {"$set": {"writer_attempt": owner}},
        )
    batch_filter: Dict[str, Any] = {
        "cash_bank_id": cash_bank_id,
        "status": STATUS_IMPORTING,
        "writer_attempt": {"$ne": owner},
    }
    line_filter: Dict[str, Any] = {
        "cash_bank_id": cash_bank_id,
        "sealed": False,
        "writer_attempt": {"$ne": owner},
    }
    if keep_batch_id:
        batch_filter["id"] = {"$ne": keep_batch_id}
        line_filter["import_id"] = {"$ne": keep_batch_id}
    await db[IMPORTS].update_many(batch_filter, {"$set": {"writer_attempt": DISPLACED_WRITER}})
    await db[LINES].update_many(line_filter, {"$set": {"writer_attempt": DISPLACED_WRITER}})


async def _displace_attempt_writes(
    db, cash_bank_id: str, old_holder: Optional[str], new_attempt: Optional[str], keep_batch_id: Optional[str],
) -> None:
    """Used when a fence is cleared without a new holder. Generation moves first."""
    await _bump_import_epoch(db, cash_bank_id)
    if not old_holder and not new_attempt:
        await _reassign_stale_import_writers(db, cash_bank_id, None, keep_batch_id)
        return
    if new_attempt:
        await _reassign_stale_import_writers(db, cash_bank_id, new_attempt, keep_batch_id)
        return
    old_holder = str(old_holder)
    cash_bank_id = str(cash_bank_id)
    await db[IMPORTS].update_many(
        {
            "cash_bank_id": cash_bank_id,
            "status": STATUS_IMPORTING,
            "writer_attempt": old_holder,
        },
        {"$set": {"writer_attempt": DISPLACED_WRITER}},
    )
    await db[LINES].update_many(
        {
            "cash_bank_id": cash_bank_id,
            "sealed": False,
            "writer_attempt": old_holder,
        },
        {"$set": {"writer_attempt": DISPLACED_WRITER}},
    )


async def _flag_duplicates(
    db,
    cash_bank_id: str,
    rows: List[dict],
    *,
    authoritative: bool,
    ignore_import_id: Optional[str] = None,
    attempt: Optional[dict] = None,
) -> None:
    """Classify duplicates. Never delete a row for date, amount, and text.

    An ID is authoritative only when the mapping says that column is the
    bank transaction id. A reference column is not unique. Weak similarity
    is an indication, not a guaranteed detection.
    """
    seen_txn: Dict[str, int] = {}
    seen_weak: Dict[str, int] = {}
    for idx, row in enumerate(rows):
        row["duplicate_status"] = DUP_CLEAR
        row["duplicate_reason"] = None
        row["duplicate_of_line_id"] = None
        row["authoritative_txn_id"] = None
        txn = str(row.get("bank_txn_id") or "").strip()
        if authoritative and txn:
            row["identity_guarantee"] = "authoritative"
            if txn in seen_txn:
                row["duplicate_status"] = DUP_DEFINITE
                row["duplicate_reason"] = "id_transaksi_bank_sama_dalam_berkas"
                row["duplicate_of_bank_txn_id"] = txn
            else:
                seen_txn[txn] = idx
                row["authoritative_txn_id"] = txn
        else:
            row["identity_guarantee"] = "tidak_dijamin"
            row["duplicate_of_bank_txn_id"] = None
            weak = "|".join([
                row["date"], row["direction"], str(row["amount"]),
                " ".join(str(row["description"]).lower().split()),
            ])
            if weak in seen_weak:
                row["duplicate_status"] = DUP_PENDING
                row["duplicate_reason"] = "tanggal_nominal_uraian_sama_tanpa_id"
                rows[seen_weak[weak]]["duplicate_status"] = DUP_PENDING
                rows[seen_weak[weak]]["duplicate_reason"] = "tanggal_nominal_uraian_sama_tanpa_id"
                rows[seen_weak[weak]]["identity_guarantee"] = "tidak_dijamin"
            else:
                seen_weak[weak] = idx

    if authoritative and seen_txn:
        query: Dict[str, Any] = {
            "cash_bank_id": cash_bank_id,
            "authoritative_txn_id": {"$in": list(seen_txn)},
        }
        if ignore_import_id:
            query["import_id"] = {"$ne": ignore_import_id}
        existing = await db[LINES].find(
            query, {"_id": 0, "authoritative_txn_id": 1, "id": 1}
        ).to_list(len(seen_txn) + 5)
        known = {d.get("authoritative_txn_id"): d.get("id") for d in existing}
        for row in rows:
            txn = row.get("authoritative_txn_id")
            if txn and txn in known:
                row["duplicate_status"] = DUP_DEFINITE
                row["duplicate_reason"] = "id_transaksi_bank_sama_dengan_impor_lain"
                row["duplicate_of_line_id"] = known[txn]
                row["authoritative_txn_id"] = None
    for row in rows:
        if row.get("identity_guarantee") == "authoritative" or row["duplicate_status"] != DUP_CLEAR:
            continue
        weak_query: Dict[str, Any] = {
            "cash_bank_id": cash_bank_id,
            "date": row["date"],
            "direction": row["direction"],
            "amount": row["amount"],
            "description": row["description"],
            "identity_guarantee": "tidak_dijamin",
            "duplicate_status": {"$ne": DUP_EXCLUDED},
        }
        if ignore_import_id:
            weak_query["import_id"] = {"$ne": ignore_import_id}
        hit = await db[LINES].find_one(weak_query, {"_id": 0, "id": 1})
        if hit:
            row["duplicate_status"] = DUP_PENDING
            row["duplicate_reason"] = "tumpang_tindih_tanpa_id_bank"
            row["duplicate_of_line_id"] = hit.get("id")
    await _persist_clear_weak_twins(db, cash_bank_id, rows, attempt)


async def _persist_clear_weak_twins(db, cash_bank_id: str, rows: List[dict], attempt: Optional[dict]) -> None:
    """Move an already stored clear twin to pending_review. Do not delete it.

    The epoch is read before the write. A takeover increments it, so this
    update matches nothing after the holder has changed.
    """
    epoch = await _account_import_epoch(db, cash_bank_id)
    for row in rows:
        if row.get("identity_guarantee") == "authoritative":
            continue
        if row.get("duplicate_status") != DUP_PENDING:
            continue
        if attempt:
            await _assert_import_attempt(db, attempt)
        await _before_fenced_write("twin")
        await db[LINES].update_many(
            {
                "cash_bank_id": cash_bank_id,
                "date": row.get("date"),
                "direction": row.get("direction"),
                "amount": row.get("amount"),
                "description": row.get("description"),
                "identity_guarantee": "tidak_dijamin",
                "duplicate_status": DUP_CLEAR,
                "active_claim": None,
                **_epoch_clause(epoch),
            },
            {"$set": {
                "duplicate_status": DUP_PENDING,
                "duplicate_reason": row.get("duplicate_reason") or "tumpang_tindih_tanpa_id_bank",
            }},
        )


def public_import(doc: Optional[dict]) -> Optional[dict]:
    if not doc:
        return None
    return {
        "id": doc.get("id"),
        "cash_bank_id": doc.get("cash_bank_id"),
        "filename": doc.get("filename"),
        "file_sha256": doc.get("file_sha256"),
        "mapping_hash": doc.get("mapping_hash"),
        "mapping": doc.get("mapping"),
        "status": doc.get("status"),
        "accepted_count": doc.get("accepted_count"),
        "stored_count": doc.get("stored_count"),
        "total_in": doc.get("total_in"),
        "total_out": doc.get("total_out"),
        "closing_balance": doc.get("closing_balance"),
        "closing_date": doc.get("closing_date"),
        "operator_coverage_confirmed": bool(doc.get("operator_coverage_confirmed")),
        "coverage_note": (
            "Cakupan dikonfirmasi operator, bukan verifikasi bank"
            if doc.get("operator_coverage_confirmed")
            else "Cakupan impor tidak diketahui"
        ),
        "preview_id": doc.get("preview_id"),
        "created_at": doc.get("created_at"),
        "created_by": doc.get("created_by"),
        "completed_at": doc.get("completed_at"),
    }


async def _stored_totals(db, import_id: str) -> Tuple[int, Decimal, Decimal]:
    rows = await db[LINES].find(
        {"import_id": import_id, "sealed": {"$ne": False}},
        {"_id": 0, "direction": 1, "amount": 1},
    ).to_list(MAX_ROWS + 5)
    total_in = Decimal("0.00")
    total_out = Decimal("0.00")
    for row in rows:
        amount = Decimal(str(row.get("amount") or 0))
        if row.get("direction") == "in":
            total_in += amount
        elif row.get("direction") == "out":
            total_out += amount
    return len(rows), _money(total_in), _money(total_out)


def _dup_fields(exc: DuplicateKeyError) -> set:
    details = getattr(exc, "details", None) or {}
    pattern = details.get("keyPattern") or {}
    if isinstance(pattern, dict) and pattern:
        return set(pattern)
    text = str(exc)
    return {part for part in ("authoritative_txn_id", "id") if part in text}


def _seal_filter(line_id: str, attempt_id: str, epoch: int) -> Dict[str, Any]:
    return {
        "id": line_id,
        "writer_attempt": str(attempt_id),
        "sealed": False,
        **_epoch_clause(epoch),
    }


async def _ensure_unsealed_slot(db, batch: dict, row: dict, attempt_id: str, epoch: int) -> str:
    """Reservation only. Business amount and classification are written by the seal."""
    line_id = f"{batch['id']}:{row['row_number']}"
    existing = await db[LINES].find_one({"id": line_id}, {"_id": 0, "id": 1})
    if existing:
        return line_id
    try:
        await db[LINES].insert_one({
            "id": line_id,
            "import_id": batch["id"],
            "cash_bank_id": batch["cash_bank_id"],
            "row_number": row["row_number"],
            "writer_attempt": str(attempt_id),
            "sealed": False,
            "duplicate_status": "unsealed",
            "import_epoch": int(epoch),
        })
    except DuplicateKeyError:
        pass
    return line_id


async def _insert_prepared_lines(db, batch: dict, rows: List[dict], actor: dict, attempt: dict) -> None:
    await _assert_import_attempt(db, attempt)
    await _before_statement_lines_insert()
    await _assert_import_attempt(db, attempt)
    now = now_iso()
    for index, row in enumerate(rows):
        await _assert_import_attempt(db, attempt)
        epoch = await _account_import_epoch(db, batch["cash_bank_id"])
        line_id = await _ensure_unsealed_slot(db, batch, row, attempt["attempt_id"], epoch)
        await _assert_import_attempt(db, attempt)
        await _before_fenced_write("line")
        doc = _line_doc(batch, row, line_id, actor, now)
        doc["sealed"] = True
        doc["writer_attempt"] = str(attempt["attempt_id"])
        doc["import_epoch"] = epoch
        try:
            result = await db[LINES].update_one(_seal_filter(line_id, attempt["attempt_id"], epoch), {"$set": doc})
        except DuplicateKeyError as exc:
            fields = _dup_fields(exc)
            if "authoritative_txn_id" in fields:
                await _store_definite_loser(db, batch, row, line_id, actor, now, attempt["attempt_id"], epoch)
            else:
                raise StatementConflictError(
                    "Baris impor bentrok dengan data yang sudah tersimpan"
                ) from exc
        else:
            if result.matched_count != 1:
                current = await db[LINES].find_one(
                    {"id": line_id}, {"_id": 0, "identity": 1, "sealed": 1},
                )
                if current and current.get("sealed") is not False and current.get("identity") == doc["identity"]:
                    pass
                elif current and current.get("sealed") is not False:
                    raise StatementConflictError(
                        "Baris impor bentrok dengan data yang sudah tersimpan"
                    )
                else:
                    raise StatementConflictError(
                        "Pagar impor tidak lagi dimiliki permintaan ini. Penulisan dihentikan."
                    )
        await _after_statement_line_insert(index)


def _line_doc(batch: dict, row: dict, line_id: str, actor: dict, now: str) -> dict:
    doc = {
        "id": line_id,
        "import_id": batch["id"],
        "cash_bank_id": batch["cash_bank_id"],
        "source_row": row["source_row"],
        "row_number": row["row_number"],
        "date": row["date"],
        "description": row["description"],
        "reference": row.get("reference"),
        "bank_txn_id": row.get("bank_txn_id"),
        "direction": row["direction"],
        "amount": row["amount"],
        "running_balance": row.get("running_balance"),
        "currency_code": row.get("currency_code"),
        "account_currency_code": batch.get("account_currency_code"),
        "identity_guarantee": row.get("identity_guarantee") or "tidak_dijamin",
        "duplicate_status": row.get("duplicate_status") or DUP_CLEAR,
        "duplicate_reason": row.get("duplicate_reason"),
        "duplicate_of_line_id": row.get("duplicate_of_line_id"),
        "duplicate_of_bank_txn_id": row.get("duplicate_of_bank_txn_id"),
        "identity": _line_identity(row),
        "resolutions": [],
        "created_at": now,
        "created_by": actor,
    }
    if row.get("authoritative_txn_id"):
        doc["authoritative_txn_id"] = row["authoritative_txn_id"]
    return doc


async def _store_definite_loser(
    db, batch: dict, row: dict, line_id: str, actor: dict, now: str, attempt_id: str, epoch: int,
) -> None:
    """The unique index already kept one eligible identity. Store the other as ineligible."""
    txn = row.get("authoritative_txn_id") or row.get("bank_txn_id")
    winner = await db[LINES].find_one(
        {"cash_bank_id": batch["cash_bank_id"], "authoritative_txn_id": txn},
        {"_id": 0, "id": 1},
    )
    loser = dict(row)
    loser["duplicate_status"] = DUP_DEFINITE
    loser["duplicate_reason"] = "id_transaksi_bank_sama_dengan_impor_lain"
    loser["duplicate_of_line_id"] = (winner or {}).get("id")
    loser["duplicate_of_bank_txn_id"] = txn
    loser["authoritative_txn_id"] = None
    loser["identity_guarantee"] = "authoritative"
    doc = _line_doc(batch, loser, line_id, actor, now)
    doc.pop("authoritative_txn_id", None)
    doc["sealed"] = True
    doc["writer_attempt"] = str(attempt_id)
    doc["import_epoch"] = epoch
    try:
        result = await db[LINES].update_one(
            {"id": line_id, "writer_attempt": str(attempt_id), "sealed": False},
            {"$set": doc, "$unset": {"authoritative_txn_id": ""}},
        )
    except DuplicateKeyError as exc:
        current = await db[LINES].find_one({"id": line_id}, {"_id": 0, "duplicate_status": 1})
        if not current or current.get("duplicate_status") != DUP_DEFINITE:
            raise StatementConflictError(
                "Baris impor bentrok dengan data yang sudah tersimpan"
            ) from exc
        return
    if result.matched_count == 1:
        return
    current = await db[LINES].find_one({"id": line_id}, {"_id": 0, "duplicate_status": 1})
    if not current or current.get("duplicate_status") != DUP_DEFINITE:
        raise StatementConflictError(
            "Pagar impor tidak lagi dimiliki permintaan ini. Penulisan dihentikan."
        )


async def _finish_if_complete(db, batch: dict, attempt: dict) -> dict:
    count, total_in, total_out = await _stored_totals(db, batch["id"])
    expected = int(batch.get("accepted_count") or 0)
    same = (
        count == expected
        and total_in == _money(Decimal(str(batch.get("total_in") or 0)))
        and total_out == _money(Decimal(str(batch.get("total_out") or 0)))
    )
    status = STATUS_COMPLETE if same else STATUS_FAILED
    write: Dict[str, Any] = {"$set": {
        "status": status,
        "stored_count": count,
        "completed_at": now_iso() if same else None,
    }}
    if same:
        write["$unset"] = {"file_bytes": ""}
    await _assert_import_attempt(db, attempt)
    await _before_fenced_write("finish")
    result = await db[IMPORTS].update_one(
        {
            "id": batch["id"],
            "status": STATUS_IMPORTING,
            "writer_attempt": str(attempt["attempt_id"]),
        },
        write,
    )
    if result.matched_count != 1:
        raise StatementConflictError(
            "Pagar impor tidak lagi dimiliki permintaan ini. Penulisan dihentikan."
        )
    batch["status"] = status
    batch["stored_count"] = count
    return batch


async def _import_fence_reusable(db, current: dict) -> bool:
    batch_id = current.get("batch_id")
    if not batch_id:
        return False
    batch = await db[IMPORTS].find_one({"id": batch_id}, {"_id": 0, "status": 1})
    if not batch:
        return True
    return batch.get("status") in (STATUS_COMPLETE, STATUS_FAILED)


def _lease_expired(value: Optional[str], now: Optional[str] = None) -> bool:
    if not value:
        return True
    return str(value) <= (now or now_iso())


def _same_operation_live(current: dict, operation_id: str, now: str) -> bool:
    if str(current.get("operation_id") or "") != str(operation_id):
        return False
    return not _lease_expired(current.get("lease_until"), now)


async def _pull_dead_reader(db, cash_bank_id: str, attempt_id: Optional[str], operation_id: Optional[str] = None) -> None:
    """Drop the reader of an attempt whose fence was taken or cleared. Not an age sweep."""
    cash_bank_id = str(cash_bank_id)
    if attempt_id:
        await db[CASH_BANK_ACCOUNTS].update_one(
            {"id": cash_bank_id},
            {"$pull": {IMPORT_READERS: {"attempt_id": str(attempt_id)}}},
        )
        await db[CASH_BANK_ACCOUNTS].update_one(
            {"id": cash_bank_id},
            {"$pull": {IMPORT_READERS: str(attempt_id)}},
        )
    if operation_id and str(operation_id) != str(attempt_id or ""):
        await db[CASH_BANK_ACCOUNTS].update_one(
            {"id": cash_bank_id},
            {"$pull": {IMPORT_READERS: str(operation_id)}},
        )


async def _refresh_own_fence(db, cash_bank_id: str, attempt_id: str) -> bool:
    """Extend the lease only while this attempt is still the holder."""
    now = now_iso()
    refreshed = await db[IMPORT_FENCES].update_one(
        {"cash_bank_id": str(cash_bank_id), "holder": str(attempt_id)},
        {"$set": {"lease_until": coordination_deadline(), "at": now}},
    )
    return refreshed.matched_count == 1


async def _takeover_expired_fence(
    db, current: dict, attempt_id: str, operation_id: str, file_hash: str,
) -> bool:
    """Lease expiry only makes this compare-and-swap eligible. Age does not delete the fence."""
    now = now_iso()
    if not _lease_expired(current.get("lease_until"), now):
        return False
    keep_batch = None
    batch_id = current.get("batch_id")
    if batch_id:
        batch = await db[IMPORTS].find_one(
            {"id": batch_id}, {"_id": 0, "status": 1, "file_sha256": 1},
        )
        if (
            batch
            and batch.get("status") == STATUS_IMPORTING
            and batch.get("file_sha256") == file_hash
        ):
            keep_batch = batch_id
    await _bump_import_epoch(db, current.get("cash_bank_id"))
    replaced = await db[IMPORT_FENCES].update_one(
        {
            "cash_bank_id": current.get("cash_bank_id"),
            "holder": current.get("holder"),
            "$or": [
                {"lease_until": {"$lte": now}},
                {"lease_until": {"$exists": False}},
            ],
        },
        {"$set": {
            "holder": str(attempt_id),
            "operation_id": str(operation_id),
            "batch_id": keep_batch,
            "file_hash": file_hash,
            "lease_until": coordination_deadline(),
            "at": now,
        }},
    )
    if replaced.matched_count != 1:
        return False
    await _pull_dead_reader(
        db, current.get("cash_bank_id"), current.get("holder"), current.get("operation_id"),
    )
    await _reassign_stale_import_writers(
        db, current.get("cash_bank_id"), attempt_id, keep_batch,
    )
    return True


async def _replace_finished_fence(
    db, current: dict, attempt_id: str, operation_id: str, file_hash: str,
) -> bool:
    if not await _import_fence_reusable(db, current):
        return False
    await _bump_import_epoch(db, current.get("cash_bank_id"))
    now = now_iso()
    replaced = await db[IMPORT_FENCES].update_one(
        {"cash_bank_id": current.get("cash_bank_id"), "holder": current.get("holder")},
        {"$set": {
            "holder": str(attempt_id),
            "operation_id": str(operation_id),
            "batch_id": None,
            "file_hash": file_hash,
            "lease_until": coordination_deadline(),
            "at": now,
        }},
    )
    if replaced.matched_count != 1:
        return False
    await _pull_dead_reader(
        db, current.get("cash_bank_id"), current.get("holder"), current.get("operation_id"),
    )
    await _reassign_stale_import_writers(db, current.get("cash_bank_id"), attempt_id, None)
    return True


async def _acquire_import_fence(
    db, cash_bank_id: str, attempt_id: str, *, operation_id: str, file_hash: str,
    wait: bool = True, timeout: float = 20.0,
) -> None:
    """Per-account fence. The holder is one commit attempt, not the preview id."""
    deadline = time.monotonic() + timeout
    cash_bank_id = str(cash_bank_id)
    while True:
        current = await db[IMPORT_FENCES].find_one({"cash_bank_id": cash_bank_id})
        if current is None:
            try:
                await db[IMPORT_FENCES].insert_one({
                    "cash_bank_id": cash_bank_id,
                    "holder": str(attempt_id),
                    "operation_id": str(operation_id),
                    "batch_id": None,
                    "file_hash": file_hash,
                    "lease_until": coordination_deadline(),
                    "at": now_iso(),
                })
                return
            except DuplicateKeyError:
                continue
        if current.get("holder") == str(attempt_id):
            if await _refresh_own_fence(db, cash_bank_id, attempt_id):
                return
            continue
        if await _takeover_expired_fence(db, current, attempt_id, operation_id, file_hash):
            return
        if await _replace_finished_fence(db, current, attempt_id, operation_id, file_hash):
            return
        now = now_iso()
        if _same_operation_live(current, operation_id, now):
            raise StatementConflictError(
                "Impor pratinjau ini sedang berjalan pada permintaan lain. "
                "Ulangi setelah permintaan itu selesai. Permintaan ini tidak mengambil alih pagar."
            )
        await _on_import_fence_busy(cash_bank_id)
        if (not wait) or time.monotonic() >= deadline:
            raise StatementConflictError(
                "Impor lain sedang berjalan pada rekening ini. "
                "Ulangi setelah impor itu selesai atau gagal."
            )
        await asyncio.sleep(0.01)


async def _release_import_fence(db, cash_bank_id: str, attempt_id: str) -> None:
    """Drop only this attempt's fence. An importing batch it still owns becomes claimable."""
    released = await db[IMPORT_FENCES].delete_one({
        "cash_bank_id": str(cash_bank_id),
        "holder": str(attempt_id),
    })
    if released.deleted_count != 1:
        return
    await db[IMPORTS].update_many(
        {
            "cash_bank_id": str(cash_bank_id),
            "status": STATUS_IMPORTING,
            "writer_attempt": str(attempt_id),
        },
        {"$set": {"writer_attempt": DISPLACED_WRITER}},
    )
    await db[LINES].update_many(
        {
            "cash_bank_id": str(cash_bank_id),
            "sealed": False,
            "writer_attempt": str(attempt_id),
        },
        {"$set": {"writer_attempt": DISPLACED_WRITER}},
    )


async def _bind_import_fence(db, cash_bank_id: str, attempt_id: str, batch_id: str) -> None:
    bound = await db[IMPORT_FENCES].update_one(
        {"cash_bank_id": str(cash_bank_id), "holder": str(attempt_id)},
        {"$set": {
            "batch_id": batch_id,
            "lease_until": coordination_deadline(),
            "at": now_iso(),
        }},
    )
    if bound.matched_count != 1:
        raise StatementConflictError("Pagar impor tidak lagi dimiliki permintaan ini. Penulisan dihentikan.")


def _reader_doc(attempt_id: str, operation_id: str) -> dict:
    return {
        "attempt_id": str(attempt_id),
        "operation_id": str(operation_id),
        "until": coordination_deadline(),
    }


async def _pin_import_currency(
    db, cash_bank_id: str, attempt_id: str, operation_id: str, expected: Optional[str],
) -> None:
    """Pin this attempt. Another attempt of the same preview is a different reader."""
    now = now_iso()
    result = await db[CASH_BANK_ACCOUNTS].update_one(
        {
            "id": str(cash_bank_id),
            "currency_code": expected,
            **{"$and": [_writer_takeover_filter(now)]},
        },
        {
            "$addToSet": {IMPORT_READERS: _reader_doc(attempt_id, operation_id)},
            "$unset": {CURRENCY_WRITER: "", CURRENCY_WRITER_UNTIL: ""},
        },
    )
    if result.matched_count == 1:
        return
    account = await get_cash_bank_raw(db, cash_bank_id)
    if _attempt_reader_current(account, attempt_id, expected):
        return
    raise StatementConflictError(
        "Mata uang rekening berubah setelah pratinjau. Validasi ulang setelah pratinjau baru. "
        "Commit tidak dilanjutkan."
    )


def _attempt_reader_current(account: Optional[dict], attempt_id: str, expected: Optional[str]) -> bool:
    if not account or (account.get("currency_code") or None) != expected:
        return False
    until = account.get(CURRENCY_WRITER_UNTIL)
    writer = account.get(CURRENCY_WRITER)
    if writer and until and str(until) > now_iso():
        return False
    if writer and not until:
        return False
    return any(
        isinstance(item, dict) and item.get("attempt_id") == str(attempt_id)
        for item in (account.get(IMPORT_READERS) or [])
    )


async def _unpin_import_currency(db, cash_bank_id: str, attempt_id: str) -> None:
    await db[CASH_BANK_ACCOUNTS].update_one(
        {"id": str(cash_bank_id)},
        {"$pull": {IMPORT_READERS: {"attempt_id": str(attempt_id)}}},
    )


async def _refresh_import_reader(db, attempt: dict) -> bool:
    now = now_iso()
    refreshed = await db[CASH_BANK_ACCOUNTS].update_one(
        {
            "id": str(attempt["cash_bank_id"]),
            "currency_code": attempt.get("expected_currency"),
            f"{IMPORT_READERS}.attempt_id": str(attempt["attempt_id"]),
            "$and": [_writer_takeover_filter(now)],
        },
        {
            "$set": {f"{IMPORT_READERS}.$[r].until": coordination_deadline()},
            "$unset": {CURRENCY_WRITER: "", CURRENCY_WRITER_UNTIL: ""},
        },
        array_filters=[{"r.attempt_id": str(attempt["attempt_id"])}],
    )
    return refreshed.matched_count == 1


async def _assert_import_attempt(db, attempt: dict) -> None:
    """Fencing check at each write. A lost holder cannot finish a conflicting import."""
    if attempt.get("fenced"):
        if not await _refresh_own_fence(db, attempt["cash_bank_id"], attempt["attempt_id"]):
            raise StatementConflictError(
                "Pagar impor tidak lagi dimiliki permintaan ini. Penulisan dihentikan."
            )
    if not await _refresh_import_reader(db, attempt):
        raise StatementConflictError(
            "Mata uang rekening berubah atau pembaca impor tidak lagi dimiliki permintaan ini."
        )


async def _operation_batch(db, preview_id: str, preview: Optional[dict]) -> Optional[dict]:
    found = await db[IMPORTS].find_one({"preview_id": str(preview_id)})
    if found:
        return found
    if preview and preview.get("cash_bank_id") and preview.get("file_sha256"):
        return await db[IMPORTS].find_one({
            "cash_bank_id": preview["cash_bank_id"],
            "file_sha256": preview["file_sha256"],
        })
    return None


def _preview_is_expired(preview: Optional[dict]) -> bool:
    if not preview:
        return True
    return str(preview.get("expires_at") or "") < now_iso()


async def _clear_expired_uncommitted(db, cash_bank_id: str, fence: Optional[dict]) -> None:
    if not fence or not cash_bank_id or fence.get("batch_id"):
        return
    if not _lease_expired(fence.get("lease_until")):
        return
    await _bump_import_epoch(db, cash_bank_id)
    now = now_iso()
    deleted = await db[IMPORT_FENCES].delete_one({
        "cash_bank_id": str(cash_bank_id),
        "holder": fence.get("holder"),
        "$and": [
            {"$or": [{"batch_id": None}, {"batch_id": {"$exists": False}}]},
            {"$or": [
                {"lease_until": {"$lte": now}},
                {"lease_until": {"$exists": False}},
            ]},
        ],
    })
    if deleted.deleted_count == 1:
        await _pull_dead_reader(
            db, cash_bank_id, fence.get("holder"), fence.get("operation_id"),
        )
        await _displace_attempt_writes(
            db, cash_bank_id, fence.get("holder"), None, None,
        )


def _fence_is_this_operation(fence: Optional[dict], preview_id: str) -> bool:
    if not fence:
        return False
    token = str(preview_id)
    return str(fence.get("operation_id") or "") == token or str(fence.get("holder") or "") == token


async def _reject_expired_without_resume(db, preview: Optional[dict], batch: Optional[dict], preview_id: str) -> None:
    cash_bank_id = (preview or {}).get("cash_bank_id") or (batch or {}).get("cash_bank_id")
    fence = None
    if cash_bank_id:
        fence = await db[IMPORT_FENCES].find_one({"cash_bank_id": str(cash_bank_id)})
    else:
        fence = await db[IMPORT_FENCES].find_one({
            "$or": [{"operation_id": str(preview_id)}, {"holder": str(preview_id)}],
        })
        cash_bank_id = (fence or {}).get("cash_bank_id")
    if _fence_is_this_operation(fence, preview_id):
        if not fence.get("batch_id") and not _lease_expired(fence.get("lease_until")):
            raise StatementConflictError(
                "Impor pratinjau ini masih berjalan pada permintaan lain. "
                "Ulangi setelah permintaan itu selesai."
            )
        await _clear_expired_uncommitted(db, cash_bank_id, fence)
    if batch and batch.get("status") == STATUS_IMPORTING:
        raise StatementConflictError(
            "Impor ini belum selesai dan berkasnya tidak tersimpan pada batch. "
            "Unggah berkas yang sama untuk melanjutkan. Batch tidak ditandai selesai."
        )
    if preview is None:
        raise StatementValidationError("Pratinjau tidak ditemukan atau sudah tidak berlaku")
    raise StatementValidationError("Pratinjau kedaluwarsa. Unggah berkas kembali.")


def _source_from_batch(batch: dict, preview_id: str) -> dict:
    data = bytes(batch.get("file_bytes") or b"")
    if _sha256(data) != batch.get("file_sha256"):
        raise StatementConflictError("Hash berkas impor tersimpan tidak sesuai")
    return {
        "id": str(preview_id),
        "cash_bank_id": batch.get("cash_bank_id"),
        "filename": batch.get("filename"),
        "file_bytes": data,
        "file_sha256": batch.get("file_sha256"),
        "account_currency_code": batch.get("account_currency_code") or None,
        "currency_context_note": batch.get("currency_context_note"),
    }


async def commit_import(db, *, preview_id: str, mapping_raw: dict, user: Optional[dict]) -> dict:
    from services.cash_bank_reconciliation import ClaimFeatureNotReady, require_claim_feature
    try:
        await require_claim_feature(db)
    except ClaimFeatureNotReady as exc:
        raise StatementConflictError(str(exc)) from exc
    preview_id = str(preview_id)
    preview = await db[PREVIEWS].find_one({"id": preview_id}, {"_id": 0})
    batch = await _operation_batch(db, preview_id, preview)
    if _preview_is_expired(preview):
        if batch and batch.get("status") == STATUS_COMPLETE:
            if batch.get("mapping_hash") != _mapping_hash(normalize_mapping(mapping_raw)):
                raise StatementConflictError(
                    "Berkas yang sama sudah diimpor dengan mapping berbeda. "
                    "Batch lama tidak diubah."
                )
            return public_import(batch)
        if batch and batch.get("status") == STATUS_IMPORTING and batch.get("file_bytes"):
            source = _source_from_batch(batch, preview_id)
        else:
            await _reject_expired_without_resume(db, preview, batch, preview_id)
            raise StatementValidationError("Pratinjau kedaluwarsa. Unggah berkas kembali.")
    else:
        source = preview
    account = await get_cash_bank_raw(db, source.get("cash_bank_id") or "")
    if not account:
        raise StatementValidationError("Kas/Bank tidak ditemukan")
    await _before_currency_pin()
    attempt_id = str(uuid.uuid4())
    expected_currency = source.get("account_currency_code") or None
    pinned = False
    try:
        await _pin_import_currency(db, account["id"], attempt_id, preview_id, expected_currency)
        pinned = True
        await _after_currency_pin()
        account = await get_cash_bank_raw(db, account["id"]) or account
        return await _commit_import_pinned(
            db, preview=source, account=account, mapping_raw=mapping_raw, user=user,
            attempt_id=attempt_id,
        )
    finally:
        if pinned:
            await _unpin_import_currency(db, account["id"], attempt_id)


async def _commit_import_pinned(
    db, *, preview: dict, account: dict, mapping_raw: dict, user: Optional[dict], attempt_id: str,
) -> dict:
    mapping = normalize_mapping(mapping_raw)
    data = bytes(preview.get("file_bytes") or b"")
    file_hash = _sha256(data)
    if file_hash != preview.get("file_sha256"):
        raise StatementConflictError("Hash berkas pratinjau tidak sesuai")
    matrix = read_matrix(preview.get("filename") or "", data)
    parsed = interpret_rows(matrix, mapping)
    if parsed["errors"]:
        raise StatementValidationError(
            "Impor ditolak karena ada baris tidak valid",
            errors=parsed["errors"],
        )
    if not parsed["accepted"]:
        raise StatementValidationError("Tidak ada baris yang dapat diimpor")
    mapping_hash = _mapping_hash(mapping)
    cash_bank_id = preview["cash_bank_id"]
    authoritative = bool(mapping.get("bank_txn_id_authoritative"))
    attempt = {
        "attempt_id": str(attempt_id),
        "operation_id": str(preview["id"]),
        "cash_bank_id": cash_bank_id,
        "expected_currency": preview.get("account_currency_code") or None,
        "fenced": not authoritative,
        "source_bytes": data,
    }
    if attempt["fenced"]:
        await _acquire_import_fence(
            db, cash_bank_id, attempt_id, operation_id=attempt["operation_id"], file_hash=file_hash,
        )
    try:
        return await _store_pinned_import(
            db, preview=preview, account=account, mapping=mapping, parsed=parsed,
            user=user, file_hash=file_hash, mapping_hash=mapping_hash,
            cash_bank_id=cash_bank_id, authoritative=authoritative, attempt=attempt,
        )
    finally:
        if attempt["fenced"]:
            await _release_import_fence(db, cash_bank_id, attempt_id)


async def _claim_batch_writer(db, batch_id: str, attempt_id: str) -> None:
    claimed = await db[IMPORTS].update_one(
        {
            "id": batch_id,
            "status": STATUS_IMPORTING,
            "$or": [
                {"writer_attempt": str(attempt_id)},
                {"writer_attempt": DISPLACED_WRITER},
                {"writer_attempt": {"$exists": False}},
            ],
        },
        {"$set": {"writer_attempt": str(attempt_id)}},
    )
    if claimed.matched_count != 1:
        raise StatementConflictError(
            "Pagar impor tidak lagi dimiliki permintaan ini. Penulisan dihentikan."
        )
    await db[LINES].update_many(
        {
            "import_id": batch_id,
            "sealed": False,
            "writer_attempt": DISPLACED_WRITER,
        },
        {"$set": {"writer_attempt": str(attempt_id)}},
    )


async def _continue_importing_batch(db, existing: dict, parsed: dict, actor: dict, attempt: dict, cash_bank_id: str) -> dict:
    await _assert_import_attempt(db, attempt)
    await _claim_batch_writer(db, existing["id"], attempt["attempt_id"])
    if attempt.get("fenced"):
        await _bind_import_fence(db, cash_bank_id, attempt["attempt_id"], existing["id"])
    if attempt.get("source_bytes") and not existing.get("file_bytes"):
        await db[IMPORTS].update_one(
            {"id": existing["id"], "status": STATUS_IMPORTING, "file_bytes": {"$exists": False}},
            {"$set": {"file_bytes": Binary(attempt["source_bytes"])}},
        )
    if not existing.get("preview_id"):
        await db[IMPORTS].update_one(
            {"id": existing["id"], "preview_id": {"$exists": False}},
            {"$set": {"preview_id": attempt["operation_id"]}},
        )
    await _insert_prepared_lines(db, existing, parsed["accepted"], actor, attempt)
    await _assert_import_attempt(db, attempt)
    finished = await _finish_if_complete(db, existing, attempt)
    if finished.get("status") != STATUS_COMPLETE:
        raise StatementConflictError("Impor belum lengkap dan total tersimpan tidak sesuai")
    return public_import(finished)


async def _store_pinned_import(
    db, *, preview, account, mapping, parsed, user, file_hash, mapping_hash,
    cash_bank_id, authoritative, attempt,
) -> dict:
    await _assert_import_attempt(db, attempt)
    await _after_import_fence_acquired()
    await _assert_import_attempt(db, attempt)
    existing_for_flag = await db[IMPORTS].find_one(
        {"cash_bank_id": cash_bank_id, "file_sha256": file_hash},
        {"_id": 0, "id": 1, "mapping_hash": 1},
    )
    ignore_import_id = None
    if existing_for_flag and existing_for_flag.get("mapping_hash") == mapping_hash:
        ignore_import_id = existing_for_flag.get("id")
    await _flag_duplicates(
        db,
        cash_bank_id,
        parsed["accepted"],
        authoritative=authoritative,
        ignore_import_id=ignore_import_id,
        attempt=attempt,
    )
    await _assert_import_attempt(db, attempt)
    actor = actor_snapshot(user)
    existing = await db[IMPORTS].find_one(
        {"cash_bank_id": cash_bank_id, "file_sha256": file_hash},
    )
    if existing:
        existing.pop("_id", None)
        if existing.get("mapping_hash") != mapping_hash:
            raise StatementConflictError(
                "Berkas yang sama sudah diimpor dengan mapping berbeda. "
                "Batch lama tidak diubah."
            )
        if existing.get("status") == STATUS_COMPLETE:
            return public_import(existing)
        if existing.get("status") == STATUS_IMPORTING:
            return await _continue_importing_batch(db, existing, parsed, actor, attempt, cash_bank_id)
        raise StatementConflictError("Batch impor sebelumnya gagal dan tidak diubah diam-diam")

    await _assert_import_attempt(db, attempt)
    batch = {
        "id": str(uuid.uuid4()),
        "preview_id": attempt["operation_id"],
        "cash_bank_id": cash_bank_id,
        "filename": preview.get("filename"),
        "file_sha256": file_hash,
        "file_bytes": Binary(attempt.get("source_bytes") or b""),
        "mapping_hash": mapping_hash,
        "mapping": mapping,
        "status": STATUS_IMPORTING,
        "accepted_count": parsed["accepted_count"],
        "stored_count": 0,
        "total_in": parsed["total_in"],
        "total_out": parsed["total_out"],
        "closing_balance": parsed["closing_balance"],
        "closing_date": parsed["closing_date"],
        "operator_coverage_confirmed": bool(mapping.get("operator_coverage_confirmed")),
        "account_currency_code": account.get("currency_code") or None,
        "currency_context_note": preview.get("currency_context_note"),
        "created_at": now_iso(),
        "created_by": actor,
        "completed_at": None,
        "writer_attempt": str(attempt["attempt_id"]),
    }
    try:
        await db[IMPORTS].insert_one(dict(batch))
    except DuplicateKeyError:
        existing = await db[IMPORTS].find_one(
            {"cash_bank_id": cash_bank_id, "file_sha256": file_hash},
        )
        if existing and existing.get("mapping_hash") == mapping_hash:
            existing.pop("_id", None)
            if existing.get("status") == STATUS_COMPLETE:
                return public_import(existing)
            if existing.get("status") == STATUS_IMPORTING:
                return await _continue_importing_batch(db, existing, parsed, actor, attempt, cash_bank_id)
        raise StatementConflictError(
            "Berkas yang sama sudah diimpor dengan mapping berbeda. Batch lama tidak diubah."
        )
    batch.pop("_id", None)
    if attempt.get("fenced"):
        await _bind_import_fence(db, cash_bank_id, attempt["attempt_id"], batch["id"])
    await _insert_prepared_lines(db, batch, parsed["accepted"], actor, attempt)
    await _assert_import_attempt(db, attempt)
    finished = await _finish_if_complete(db, batch, attempt)
    if finished.get("status") != STATUS_COMPLETE:
        raise StatementConflictError("Impor belum lengkap dan total tersimpan tidak sesuai")
    return public_import(finished)


async def list_imports(db, *, cash_bank_id: str) -> List[dict]:
    docs = await db[IMPORTS].find(
        {"cash_bank_id": str(cash_bank_id)},
        {"_id": 0, "file_bytes": 0},
    ).sort("created_at", -1).to_list(200)
    return [public_import(d) for d in docs]


async def list_ready_lines(db, *, cash_bank_id: str, start: str, end: str) -> List[dict]:
    imports = await db[IMPORTS].find(
        {"cash_bank_id": str(cash_bank_id), "status": STATUS_COMPLETE},
        {"_id": 0, "id": 1},
    ).to_list(500)
    ids = [d["id"] for d in imports if d.get("id")]
    if not ids:
        return []
    rows = await db[LINES].find(
        {
            "import_id": {"$in": ids},
            "date": {"$gte": start, "$lte": end},
            "duplicate_status": {"$ne": DUP_EXCLUDED},
            "sealed": {"$ne": False},
        },
        {"_id": 0},
    ).to_list(MAX_ROWS * 5)
    rows.sort(key=lambda r: (str(r.get("date") or ""), str(r.get("id") or "")))
    return rows


def line_matchable(row: dict, account_currency: Optional[str]) -> Tuple[bool, str]:
    if row.get("duplicate_status") == DUP_DEFINITE:
        return False, "Duplikat pasti transaksi bank yang sama tidak dapat dicocokkan"
    if row.get("duplicate_status") == DUP_PENDING:
        return False, "Dugaan duplikat belum diputuskan"
    if row.get("duplicate_status") == DUP_EXCLUDED:
        return False, "Baris dikecualikan"
    if row.get("duplicate_status") not in (DUP_CLEAR, DUP_KEPT):
        return False, "Status baris tidak siap"
    if row.get("active_claim"):
        return False, "Baris sedang diklaim pencocokan"
    currency = row.get("currency_code") or None
    if not account_currency:
        return False, "Mata uang rekening belum ditetapkan"
    if not currency:
        return False, "Mata uang baris bank tidak diketahui"
    if currency != account_currency:
        return False, "Mata uang baris bank berbeda dari rekening"
    return True, ""


async def resolve_duplicate(
    db, line_id: str, *, action: str, reason: str, idempotency_key: str, user: Optional[dict]
) -> dict:
    from services.cash_bank_reconciliation import ClaimFeatureNotReady, require_claim_feature
    try:
        await require_claim_feature(db)
    except ClaimFeatureNotReady as exc:
        raise StatementConflictError(str(exc)) from exc
    act = str(action or "").strip()
    if act not in ("keep", "exclude"):
        raise StatementValidationError("Keputusan duplikat harus keep atau exclude")
    why = str(reason or "").strip()
    if len(why) < 3:
        raise StatementValidationError("Alasan keputusan wajib diisi")
    key = str(idempotency_key or "").strip()
    if not key:
        raise StatementValidationError("Idempotency key wajib")
    line = await db[LINES].find_one({"id": str(line_id)}, {"_id": 0})
    if not line:
        raise KeyError("missing")
    prior = await db[LINES].find_one({"resolutions.idempotency_key": key}, {"_id": 0})
    if prior:
        if prior.get("id") != line.get("id"):
            raise StatementConflictError("Kunci keputusan sudah dipakai pada baris lain")
        same = next((r for r in prior.get("resolutions") or [] if r.get("idempotency_key") == key), None)
        if same and (same.get("action") != act or same.get("reason") != why):
            raise StatementConflictError("Kunci yang sama tidak dapat mengubah keputusan")
        return prior
    if line.get("duplicate_status") == DUP_DEFINITE and act == "keep":
        raise StatementConflictError(
            "Duplikat pasti tidak dapat dipertahankan sebagai transaksi aktif kedua."
        )
    if line.get("duplicate_status") not in (DUP_PENDING, DUP_KEPT, DUP_CLEAR, DUP_DEFINITE):
        raise StatementConflictError("Baris yang dikecualikan tidak diubah lewat kunci baru tanpa alur terpisah")
    if line.get("active_claim") and act == "exclude":
        raise StatementConflictError(
            "Baris sudah dicocokkan. Batalkan pencocokan melalui alur berizin sebelum mengecualikan."
        )
    from services.cash_bank_reconciliation import RECON_MATCHES
    claimed = await db[RECON_MATCHES].find_one(
        {"statement_line_id": line["id"], "status": "active"},
        {"_id": 0, "id": 1},
    )
    if claimed and act == "exclude":
        raise StatementConflictError(
            "Baris sudah dicocokkan. Batalkan pencocokan melalui alur berizin sebelum mengecualikan."
        )
    await _before_duplicate_resolution()
    event = {
        "action": act,
        "reason": why,
        "idempotency_key": key,
        "at": now_iso(),
        "actor": actor_snapshot(user),
        "duplicate_of_line_id": line.get("duplicate_of_line_id"),
        "identity_guarantee": line.get("identity_guarantee") or "tidak_dijamin",
    }
    new_status = DUP_KEPT if act == "keep" else DUP_EXCLUDED
    result = await db[LINES].update_one(
        {
            "id": line["id"],
            "duplicate_status": line.get("duplicate_status"),
            "active_claim": None,
        },
        {
            "$set": {"duplicate_status": new_status, "updated_at": event["at"]},
            "$push": {"resolutions": event},
        },
    )
    if result.matched_count != 1:
        current = await db[LINES].find_one({"id": line["id"]}, {"_id": 0})
        if current and current.get("active_claim"):
            raise StatementConflictError(
                "Baris sudah dicocokkan. Batalkan pencocokan melalui alur berizin sebelum mengecualikan."
            )
        raise StatementConflictError("Keputusan duplikat bentrok. Muat ulang baris.")
    return await db[LINES].find_one({"id": line["id"]}, {"_id": 0})


async def set_manual_balance(
    db, *, cash_bank_id: str, as_of: str, amount: Any, user: Optional[dict]
) -> dict:
    cb = await get_cash_bank_raw(db, cash_bank_id)
    if not cb:
        raise StatementValidationError("Kas/Bank tidak ditemukan")
    date = str(as_of or "").strip()
    if len(date) != 10:
        raise StatementValidationError("Tanggal saldo manual tidak valid")
    parsed = parse_amount(amount, "dot_decimal")
    if parsed is None:
        raise StatementValidationError("Nominal saldo manual wajib")
    doc = {
        "cash_bank_id": str(cash_bank_id),
        "as_of": date,
        "amount": _money_float(parsed),
        "source": "manual",
        "label": "Input manual",
        "updated_at": now_iso(),
        "updated_by": actor_snapshot(user),
    }
    await db[MANUAL_BALANCES].update_one(
        {"cash_bank_id": doc["cash_bank_id"], "as_of": date},
        {"$set": doc, "$setOnInsert": {"id": str(uuid.uuid4()), "created_at": doc["updated_at"]}},
        upsert=True,
    )
    stored = await db[MANUAL_BALANCES].find_one(
        {"cash_bank_id": doc["cash_bank_id"], "as_of": date},
        {"_id": 0},
    )
    return {
        "cash_bank_id": stored.get("cash_bank_id"),
        "as_of": stored.get("as_of"),
        "amount": stored.get("amount"),
        "source": "manual",
        "label": "Input manual",
    }


async def statement_closing(db, *, cash_bank_id: str, end: str) -> dict:
    """File closing only when one complete import states a balance on end."""
    batches = await db[IMPORTS].find(
        {
            "cash_bank_id": str(cash_bank_id),
            "status": STATUS_COMPLETE,
            "closing_date": end,
            "closing_balance": {"$ne": None},
        },
        {"_id": 0, "id": 1, "closing_balance": 1, "closing_date": 1, "filename": 1},
    ).to_list(50)
    manual = await db[MANUAL_BALANCES].find_one(
        {"cash_bank_id": str(cash_bank_id), "as_of": end},
        {"_id": 0, "amount": 1, "as_of": 1, "source": 1},
    )
    manual_view = None
    if manual:
        manual_view = {
            "amount": manual.get("amount"),
            "as_of": manual.get("as_of"),
            "source": "manual",
            "label": "Input manual",
        }
    if len(batches) == 1:
        batch = batches[0]
        return {
            "status": "dari_kolom_saldo_impor",
            "amount": batch.get("closing_balance"),
            "as_of": end,
            "source": "kolom_saldo_impor",
            "import_id": batch.get("id"),
            "filename": batch.get("filename"),
            "label": "Dari kolom saldo impor",
            "manual": manual_view,
        }
    if len(batches) > 1:
        return {
            "status": "belum_tersedia",
            "amount": None,
            "as_of": end,
            "source": None,
            "label": "Belum tersedia",
            "reason": "Lebih dari satu saldo impor pada tanggal akhir",
            "manual": manual_view,
        }
    return {
        "status": "belum_tersedia",
        "amount": None,
        "as_of": end,
        "source": None,
        "label": "Belum tersedia",
        "manual": manual_view,
    }
